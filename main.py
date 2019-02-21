#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import gzip
import html
import pickle
import sqlite3
import operator
import textwrap
import itertools
import functools
import subprocess
import collections

import jinja2
import bottle

import bottle_sqlite
from utils import cmp, version_compare, version_compare_key, strftime, \
                  sizeof_fmt, parse_fail_arch, iter_read1, Pager

__version__ = '2.0'

SQL_GET_PACKAGES = 'SELECT name, description, full_version FROM v_packages'

SQL_GET_PACKAGE_INFO = '''
SELECT
  name, tree, tree_category, branch, category, section, pkg_section, directory,
  description, version, full_version, commit_time, committer,
  dep.dependency dependency,
  (spabhost.value IS 'noarch') noarch, spfailarch.value fail_arch,
  spsrc.key srctype, spsrc.value srcurl,
  (revdep.dependency IS NOT null) hasrevdep
FROM v_packages
LEFT JOIN (
    SELECT
      package,
      group_concat(dependency || '|' || version || '|' || relationship) dependency
    FROM package_dependencies
    GROUP BY package
  ) dep
  ON dep.package = v_packages.name
LEFT JOIN package_spec spabhost
  ON spabhost.package = v_packages.name AND spabhost.key = 'ABHOST'
LEFT JOIN package_spec spfailarch
  ON spfailarch.package = v_packages.name AND spfailarch.key = 'FAIL_ARCH'
LEFT JOIN package_spec spsrc
  ON spsrc.package = v_packages.name
  AND spsrc.key IN ('SRCTBL', 'GITSRC', 'SVNSRC', 'BZRSRC')
LEFT JOIN (
    SELECT dependency FROM package_dependencies
    WHERE relationship == 'PKGDEP' OR relationship == 'BUILDDEP'
      OR relationship == 'PKGRECOM'
    GROUP BY dependency
  ) revdep
  ON revdep.dependency = v_packages.name
WHERE name = ?
'''

SQL_GET_PACKAGE_INFO_GHOST = '''
SELECT DISTINCT
  package name, '' tree, '' tree_category, '' branch,
  '' category, '' section, '' pkg_section, '' directory,
  '' description, '' version, '' full_version, NULL commit_time, '' committer,
  '' dependency, 0 noarch, NULL fail_arch, NULL srctype, NULL srcurl,
  0 hasrevdep
FROM dpkg_packages WHERE package = ?
'''

SQL_ATTACH_PISS = "ATTACH 'file:data/piss.db?mode=ro' AS piss"

SQL_GET_PISS_VERSION = '''
SELECT version, updated, url FROM piss.v_package_upstream WHERE package=?
'''

SQL_GET_PACKAGE_CHANGELOG = '''
SELECT
  ((CASE WHEN ifnull(epoch, '') = '' THEN '' ELSE epoch || ':' END) ||
   version || (CASE WHEN ifnull(release, '') IN ('', '0') THEN '' ELSE '-' ||
   release END)) fullver, pr.rid rid, m.githash githash,
  round((ev.mtime-2440587.5)*86400) time,
  ev.user email, cm.name fullname, pr.message message
FROM marks.package_rel pr
LEFT JOIN marks.marks m ON m.rid=pr.rid
LEFT JOIN fossil.event ev ON ev.objid=pr.rid
LEFT JOIN marks.committers cm ON cm.email=ev.user
WHERE package = ?
ORDER BY mtime DESC, rid DESC
'''

SQL_GET_PACKAGE_DPKG = '''
SELECT
  version, dp.architecture, repo, dr.realname reponame,
  dr.testing testing, filename, size
FROM dpkg_packages dp
LEFT JOIN dpkg_repos dr ON dr.name=dp.repo
WHERE package = ?
ORDER BY dr.realname ASC, version COLLATE vercomp DESC, testing DESC
'''

SQL_GET_PACKAGE_REPO = '''
SELECT
  p.name name, p.full_version full_version, dpkg.dpkg_version dpkg_version,
  p.description description
FROM v_packages p
LEFT JOIN package_spec spabhost
  ON spabhost.package = p.name AND spabhost.key = 'ABHOST'
LEFT JOIN v_dpkg_packages_new dpkg
  ON dpkg.package = p.name
WHERE dpkg.repo = ?
  AND ((spabhost.value IS 'noarch') = (dpkg.architecture IS 'noarch'))
ORDER BY p.name
'''

SQL_GET_PACKAGE_NEW = '''
SELECT name, description, full_version, commit_time FROM v_packages
ORDER BY commit_time DESC, name ASC LIMIT 10
'''

SQL_GET_PACKAGE_NEW_LIST = '''
SELECT
  name, dpkg.dpkg_version dpkg_version,
  description, full_version, commit_time,
  ifnull(CASE WHEN dpkg_version IS NOT null
   THEN (dpkg_version > full_version COLLATE vercomp) -
   (dpkg_version < full_version COLLATE vercomp)
   ELSE -1 END, -2) ver_compare
FROM v_packages
LEFT JOIN v_dpkg_packages_new dpkg ON dpkg.package = v_packages.name
WHERE full_version IS NOT null
GROUP BY name
ORDER BY commit_time DESC, name ASC
LIMIT ?
'''

SQL_GET_PACKAGE_LAGGING = '''
SELECT
  p.name name, dpkg.dpkg_version dpkg_version, description,
  ((CASE WHEN ifnull(pv.epoch, '') = '' THEN ''
    ELSE pv.epoch || ':' END) || pv.version ||
   (CASE WHEN ifnull(pv.release, '') IN ('', '0') THEN ''
    ELSE '-' || pv.release END)) full_version
FROM packages p
LEFT JOIN package_spec spabhost
  ON spabhost.package = p.name AND spabhost.key = 'ABHOST'
LEFT JOIN v_dpkg_packages_new dpkg
  ON dpkg.package = p.name
LEFT JOIN package_versions pv
  ON pv.package = p.name AND pv.branch = dpkg.branch
WHERE dpkg.repo = ? AND
  dpkg_version IS NOT null AND
  (dpkg.architecture IS 'noarch' OR ? != 'noarch') AND
  ((spabhost.value IS 'noarch') = (dpkg.architecture IS 'noarch'))
GROUP BY name
HAVING (max(dpkg_version COLLATE vercomp) < full_version COLLATE vercomp)
ORDER BY name
'''

SQL_GET_PACKAGE_GHOST = '''
SELECT package name, dpkg_version
FROM v_dpkg_packages_new
WHERE repo = ? AND name NOT IN (SELECT name FROM packages)
GROUP BY name
'''

SQL_GET_PACKAGE_MISSING = '''
SELECT
  v_packages.name name, description, full_version, dpkg_version, v_packages.tree_category
FROM v_packages
LEFT JOIN package_spec spabhost
  ON spabhost.package = v_packages.name AND spabhost.key = 'ABHOST'
LEFT JOIN v_dpkg_packages_new dpkg
  ON dpkg.package = v_packages.name AND dpkg.reponame = ?
WHERE full_version IS NOT null AND dpkg_version IS null
  AND ((spabhost.value IS 'noarch') = (? IS 'noarch'))
  AND (EXISTS(SELECT 1 FROM dpkg_repos WHERE realname=? AND category='bsp') =
       (v_packages.tree_category='bsp'))
ORDER BY name
'''

SQL_GET_PACKAGE_TREE = '''
SELECT
  name, dpkg.dpkg_version dpkg_version,
  group_concat(DISTINCT dpkg.reponame) dpkg_availrepos,
  description, full_version,
  ifnull(CASE WHEN dpkg_version IS NOT null
   THEN (dpkg_version > full_version COLLATE vercomp) -
   (dpkg_version < full_version COLLATE vercomp)
   ELSE -1 END, -2) ver_compare
FROM v_packages
LEFT JOIN v_dpkg_packages_new dpkg ON dpkg.package = v_packages.name
WHERE tree = ?
GROUP BY name
ORDER BY name
'''

SQL_GET_PACKAGE_SRCUPD = '''
SELECT
  vp.name, vp.version, vpu.version upstream_version,
  vpu.updated, vpu.url upstream_url, vpu.tarball upstream_tarball
FROM v_packages vp
INNER JOIN piss.v_package_upstream vpu ON vpu.package=vp.name
WHERE vp.tree=? AND (NOT vpu.version LIKE (vp.version || '%'))
  AND (vp.version < vpu.version COLLATE vercomp)
  AND (? IS NULL OR vp.section = ?)
ORDER BY vp.name
'''

SQL_GET_PACKAGE_LIST = '''
SELECT
  p.name, p.tree, p.tree_category, p.branch, p.category, p.section,
  p.pkg_section, p.directory, p.description, p.version, p.full_version,
  p.commit_time, p.committer, dpkg.dpkg_version dpkg_version,
  group_concat(DISTINCT dpkg.reponame) dpkg_availrepos,
  ifnull(CASE WHEN dpkg.dpkg_version IS NOT null
   THEN (dpkg.dpkg_version > p.full_version COLLATE vercomp) -
   (dpkg.dpkg_version < p.full_version COLLATE vercomp)
   ELSE -1 END, -2) ver_compare
FROM v_packages p
LEFT JOIN v_dpkg_packages_new dpkg ON dpkg.package = p.name
GROUP BY name
ORDER BY name
'''

SQL_GET_REPO_COUNT = '''
SELECT
  drs.repo name, dr.realname realname, dr.architecture, dr.suite branch,
  dr.path path, dr.date date, dr.testing testing, dr.category category,
  coalesce(drs.packagecnt, 0) pkgcount,
  coalesce(drs.ghostcnt, 0) ghost,
  coalesce(drs.laggingcnt, 0) lagging,
  coalesce(drs.missingcnt, 0) missing
FROM dpkg_repo_stats drs
LEFT JOIN dpkg_repos dr ON dr.name=drs.repo
LEFT JOIN (
  SELECT drs2.repo repo, drs2.packagecnt packagecnt
  FROM dpkg_repo_stats drs2
  LEFT JOIN dpkg_repos dr2 ON dr2.name=drs2.repo
  WHERE dr2.testing=0
) drs_m ON drs_m.repo=dr.realname
ORDER BY drs_m.packagecnt DESC, dr.realname ASC, dr.testing ASC
'''

SQL_GET_TREES = '''
SELECT
  tree name, category, url, max(date) date, count(name) pkgcount,
  sum(ver_compare) srcupd
FROM (
  SELECT
    p.name, p.tree, t.category, t.url, p.commit_time date,
    (CASE WHEN vpu.version LIKE (p.version || '%') THEN 0 ELSE
     p.version < vpu.version COLLATE vercomp END) ver_compare
  FROM v_packages p
  INNER JOIN trees t ON t.name=p.tree
  LEFT JOIN piss.v_package_upstream vpu ON vpu.package=p.name
) q1
GROUP BY tree
ORDER BY pkgcount DESC
'''

SQL_GET_DEB_LIST_HASARCH = '''
SELECT
  dp.package package, dp.version version, repo, filename,
  (spabhost.value IS 'noarch') noarch,
  (packages.name IS NULL) outoftree,
  dpnoarch.versions noarchver
FROM (
  SELECT package, version, repo, filename FROM dpkg_packages
  UNION
  SELECT package, version, repo, filename FROM dpkg_package_duplicate
) dp
LEFT JOIN packages ON packages.name = dp.package
LEFT JOIN package_spec spabhost
  ON spabhost.package = dp.package AND spabhost.key = 'ABHOST'
LEFT JOIN (
    SELECT
      dp.package,
      group_concat(dp.version) versions
    FROM dpkg_packages dp
    INNER JOIN dpkg_repos dr ON dr.name=dp.repo
    WHERE dr.architecture = 'noarch'
    GROUP BY dp.package
  ) dpnoarch
  ON dpnoarch.package = dp.package
WHERE repo = ?
ORDER BY dp.package
'''

SQL_GET_DEB_LIST_NOARCH = '''
SELECT
  dp.package package, dp.version version, repo, filename,
  (spabhost.value IS 'noarch') noarch,
  (packages.name IS NULL) outoftree,
  dparch.versions hasarchver
FROM (
  SELECT package, version, repo, filename FROM dpkg_packages
  UNION
  SELECT package, version, repo, filename FROM dpkg_package_duplicate
) dp
LEFT JOIN packages ON packages.name = dp.package
LEFT JOIN package_spec spabhost
  ON spabhost.package = dp.package AND spabhost.key = 'ABHOST'
LEFT JOIN (
    SELECT
      dp.package,
      group_concat(dp.version) versions
    FROM dpkg_packages dp
    INNER JOIN dpkg_repos dr ON dr.name=dp.repo
    WHERE dr.architecture != 'noarch'
    GROUP BY dp.package
  ) dparch
  ON dparch.package = dp.package
WHERE repo = ?
ORDER BY dp.package
'''

SQL_GET_PACKAGE_REV_REL = '''
SELECT package, version, relationship
FROM package_dependencies
WHERE
  dependency = ? AND (relationship == 'PKGDEP' OR
  relationship == 'BUILDDEP' OR relationship == 'PKGRECOM')
ORDER BY relationship, package
'''

SQL_SEARCH_PACKAGES_DESC = '''
SELECT q.name, q.description, q.desc_highlight, vp.full_version
FROM (
  SELECT
    vp.name, vp.description,
    highlight(fts_packages, 1, '<b>', '</b>') desc_highlight,
    (CASE WHEN vp.name=? THEN 1
     WHEN instr(vp.name, ?)=0 THEN 3 ELSE 2 END) matchcls,
    bm25(fts_packages, 5, 1) ftrank
  FROM packages vp
  INNER JOIN fts_packages fp ON fp.name=vp.name
  WHERE fts_packages MATCH ?
  UNION ALL
  SELECT
    vp.name, vp.description, vp.description desc_highlight,
    2 matchcls, 1.0 ftrank
  FROM v_packages vp
  LEFT JOIN fts_packages fp ON fp.name=vp.name AND fts_packages MATCH ?
  WHERE vp.name LIKE ('%' || ? || '%') AND vp.name!=? AND fp.name IS NULL
) q
INNER JOIN v_packages vp ON vp.name=q.name
ORDER BY q.matchcls, q.ftrank, vp.commit_time DESC, q.name
'''

DEP_REL = collections.OrderedDict((
    ('PKGDEP', 'Depends'),
    ('BUILDDEP', 'Depends (build)'),
    ('PKGREP', 'Replaces'),
    ('PKGRECOM', 'Recommends'),
    ('PKGCONFL', 'Conflicts'),
    ('PKGBREAK', 'Breaks')
))
DEP_REL_REV = collections.OrderedDict((
    ('PKGDEP', 'Depended by'),
    ('BUILDDEP', 'Depended by (build)'),
    ('PKGRECOM', 'Recommended by')
))
VER_REL = {
    -2: 'deprecated',
    -1: 'old',
    0: 'same',
    1: 'new'
}
SRC_TYPE = {
    'SRCTBL': 'tarball',
    'GITSRC': 'Git',
    'SVNSRC': 'Subversion',
    'BZRSRC': 'Bazaar'
}
REPO_CAT = (('base', None), ('bsp', 'BSP'), ('overlay', 'Overlay'))
PAGESIZE = 60

RE_QUOTES = re.compile(r'"([a-z]+|\$)"')
RE_FTS5_COLSPEC = re.compile(r'(?<!")(\w*-[\w-]*)(?!")')
RE_SRCHOST = re.compile(r'^https://(github\.com|bitbucket\.org|gitlab\.com)')
RE_PYPI = re.compile(r'^https?://pypi\.(python\.org|io)')
RE_PYPISRC = re.compile(r'^https?://pypi\.(python\.org|io)/packages/source/')

application = app = bottle.Bottle()
plugin = bottle_sqlite.Plugin(
    dbfile='data/abbs.db',
    readonly=True,
    extensions=('./mod_vercomp.so',)
    # collations={'vercomp': version_compare}
)
app.install(plugin)


def response_lm(f_body=None, status=None, headers=None, modified=None, etag=None):
    ''' Makes an HTTPResponse according to supplied modified time or ETag.
    '''

    headers = headers or dict()
    lm = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(modified))
    headers['Last-Modified'] = lm
    headers['Date'] = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

    getenv = bottle.request.environ.get

    if etag:
        headers['ETag'] = etag
        check = getenv('HTTP_IF_NONE_MATCH')
        if check and check == etag:
            return bottle.HTTPResponse(status=304, **headers)

    ims = getenv('HTTP_IF_MODIFIED_SINCE')
    if ims:
        ims = bottle.parse_date(ims.split(";")[0].strip())
    if ims is not None and ims >= int(modified):
        return bottle.HTTPResponse(status=304, **headers)

    body = b'' if bottle.request.method == 'HEAD' else f_body()

    return bottle.HTTPResponse(body, status, **headers)


jinja2_settings = {
    'filters': {
        'strftime': strftime,
        'sizeof_fmt': sizeof_fmt,
        'fill': textwrap.fill
    },
    'tests': {
        'blob': lambda x: type(x) == bytes
    },
    'autoescape': jinja2.select_autoescape(('html', 'htm', 'xml'))
}
jinja2_template = functools.partial(bottle.jinja2_template,
    template_settings=jinja2_settings)
isjson = lambda: (
    bottle.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    or bottle.request.query.get('type') == 'json')
render = lambda *args, **kwargs: (
    kwargs if isjson() else jinja2_template(*args, **kwargs)
)


def gen_trie(wordlist):
    trie = {}
    for word in wordlist:
        p = trie
        for c in word:
            if c not in p:
                p[c] = {}
            p = p[c]
        p['$'] = 0
    return trie


def get_page():
    page_q = bottle.request.query.get('page')
    if not page_q:
        return 1, PAGESIZE
    elif page_q == 'all':
        return 1, 1000000000
    try:
        return int(page_q), PAGESIZE
    except ValueError:
        return 1, PAGESIZE


def pagination(pager):
    if pager is None:
        return {'cur': 1, 'max': 1, 'count': 0}
    return {'cur': pager.page, 'max': pager.pagecount(), 'count': pager.count()}


def render_html(**kwargs):
    jinjaenv = jinja2.Environment(loader=jinja2.FileSystemLoader(
        os.path.normpath(os.path.join(os.path.dirname(__file__), 'templates'))))
    jinjaenv.filters['strftime'] = (
        lambda t, f='%Y-%m-%dT%H:%M:%SZ': time.strftime(f, t))
    template = jinjaenv.get_template(kwargs.get('template', 'template.html'))
    kvars = kwargs.copy()
    kvars['updatetime'] = time.gmtime()
    trie = json.dumps(gen_trie(p['name'] for p in kwargs['packages']), separators=',:')
    kvars['packagetrie'] = RE_QUOTES.sub('\\1', trie).replace('{$:0}', '0')
    kvars['dep_rel'] = DEP_REL
    return template.render(**kvars)


def db_last_modified(db, ttl=3600):
    now = time.monotonic()
    if (not db_last_modified.last_updated or
        now - db_last_modified.last_updated > ttl):
        row = db.execute(
            'SELECT commit_time FROM package_versions ORDER BY commit_time DESC LIMIT 1'
            ).fetchone()
        if row:
            db_last_modified.last_updated = now
            db_last_modified.value = row[0]
    return db_last_modified.value
db_last_modified.last_updated = float('-inf')
db_last_modified.value = 0


def db_repos(db, ttl=1800):
    now = time.monotonic()
    if (not db_last_modified.last_updated or
        now - db_repos.last_updated > ttl):
        d = collections.OrderedDict((row['name'], dict(row))
            for row in db.execute(SQL_GET_REPO_COUNT))
        db_repos.last_updated = now
        db_repos.value = d
    return db_repos.value
db_repos.last_updated = float('-inf')
db_repos.value = {}


def db_trees(db, ttl=1800):
    db.execute(SQL_ATTACH_PISS)
    now = time.monotonic()
    if now - db_trees.last_updated > ttl:
        d = collections.OrderedDict((row['name'], dict(row))
            for row in db.execute(SQL_GET_TREES))
        db_trees.last_updated = now
        db_trees.value = d
    return db_trees.value
db_trees.last_updated = float('-inf')
db_trees.value = {}


def makefullver(epoch, version, release):
    v = version
    if epoch:
        v = '%s:%s' % (epoch, v)
    if release:
        v = '%s-%s' % (v, release)
    return v


@app.route('/static/<filename>')
def server_static(filename):
    return bottle.static_file(filename, root='static')

@app.route('/pkgtrie.js')
def pkgtrie(db):
    return response_lm(lambda: jinja2_template(
        'pkgtrie.js', packagetrie=RE_QUOTES.sub('\\1', json.dumps(gen_trie(
        row[0] for row in db.execute('SELECT name FROM packages')),
        separators=',:')).replace('{$:0}', '0')),
        modified=db_last_modified(db),
        headers={'Content-Type': 'application/javascript; charset=UTF-8'})

@app.route('/search/')
def search(db):
    q = bottle.request.query.get('q')
    noredir = bottle.request.query.get('noredir') or isjson()
    page, pagesize = get_page()
    if not q:
        return render('search.html', q=q, packages=[], page=pagination(None))
    if not noredir:
        qn = q.strip().lower().replace(' ', '-').replace('_', '-')
        row = db.execute("SELECT 1 FROM packages WHERE name=?", (qn,)).fetchone()
        if not row:
            row = db.execute(SQL_GET_PACKAGE_INFO_GHOST, (qn,)).fetchone()
        if row:
            bottle.redirect("/packages/" + qn, 303)
    packages = []
    qesc = RE_FTS5_COLSPEC.sub(r'"\1"', q)
    try:
        rows = db.execute(SQL_SEARCH_PACKAGES_DESC, (qesc,)*6).fetchall()
    except sqlite3.OperationalError:
        # fts5 syntax error
        rows = []
    for row in rows:
        d = dict(row)
        d['desc_highlight'] = html.escape(d['desc_highlight']).replace(
            '&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')
        d['name_highlight'] = html.escape(d['name']).replace(q, '<b>%s</b>' % q)
        packages.append(d)
    res = Pager(packages, pagesize, page)
    return render('search.html', q=q, packages=list(res), page=pagination(res))

@app.route('/query/', method=('GET', 'POST'))
def query(db):
    q = bottle.request.forms.get('q')
    if not q:
        return render('query.html', q='', headers=[], rows=[], error=None)
    try:
        proc = subprocess.run(
            ('python3', 'rawquery.py', 'data/abbs.db'),
            input=q.encode('utf-8'), stdout=subprocess.PIPE, check=True)
        result = pickle.loads(proc.stdout)
    except Exception as ex:
        result = {'error': 'failed to execute query.'}
    return render('query.html', q=q, headers=result.get('header', ()),
                  rows=result.get('rows', ()), error=result.get('error'))

@app.route('/packages/<name>')
def package(name, db):
    name = name.strip().lower()
    res = db.execute(SQL_GET_PACKAGE_INFO, (name,)).fetchone()
    pkgintree = True
    if res is None:
        res = db.execute(SQL_GET_PACKAGE_INFO_GHOST, (name,)).fetchone()
        pkgintree = False
    if res is None:
        return bottle.HTTPResponse(render('error.html',
                error='Package "%s" not found.' % name), 404)
    pkg = dict(res)
    # Process depenencies
    dep_dict = {}
    if pkg['dependency']:
        for dep in pkg['dependency'].split(','):
            dep_pkg, dep_ver, dep_rel = dep.split('|')
            if dep_rel in dep_dict:
                dep_dict[dep_rel].append((dep_pkg, dep_ver))
            else:
                dep_dict[dep_rel] = [(dep_pkg, dep_ver)]
    pkg['dependency'] = dep_dict
    # Generate version matrix
    fullver = pkg['full_version']
    repos = db_repos(db)
    dpkg_dict = {}
    ver_list = []
    for repo, group in itertools.groupby(db.execute(
        SQL_GET_PACKAGE_DPKG, (name,)), key=operator.itemgetter('reponame')):
        table_row = collections.OrderedDict()
        if pkgintree and fullver:
            table_row[fullver] = None
        for row in group:
            d = dict(row)
            ver = d['version']
            table_row[ver] = d
            if ver not in ver_list:
                ver_list.append(ver)
        dpkg_dict[repo] = table_row
    ver_list.sort(key=version_compare_key, reverse=True)
    if pkgintree and fullver and fullver not in ver_list:
        ver_list.insert(0, fullver)
    fail_arch = parse_fail_arch(pkg['fail_arch'])
    if pkg['noarch']:
        reponames = ['noarch']
    elif pkg['tree_category']:
        reponames = sorted(set(
            r['realname'] for r in repos.values()
            if (r['category'] == pkg['tree_category'] and
                r['realname'] != 'noarch' and (not fail_arch.op or
                (fail_arch.op == '@' and r['realname'] not in fail_arch.plist) or
                (fail_arch.op == '!' and r['realname'] in fail_arch.plist)))
        ))
    else:
        reponames = sorted(dpkg_dict.keys())
    pkg['versions'] = ver_list
    pkg['dpkg_matrix'] = [
        (repo, [dpkg_dict[repo].get(ver) for ver in ver_list]
         if repo in dpkg_dict else [None]*len(ver_list)) for repo in reponames]
    # Guess upstream url
    if pkg['srctype']:
        pkg['srctype'] = SRC_TYPE[pkg['srctype']]
        if RE_SRCHOST.match(pkg['srcurl']):
            pkg['srcurl_base'] = '/'.join(pkg['srcurl'].split('/')[:5])
        elif RE_PYPI.match(pkg['srcurl']):
            if RE_PYPISRC.match(pkg['srcurl']):
                pypiname = pkg['srcurl'].split('/')[-2]
            else:
                pypiname = pkg['srcurl'].split('/')[-1].rsplit('-', 1)[0]
            pkg['srcurl_base'] = 'https://pypi.python.org/pypi/%s/' % pypiname
        elif pkg['srctype'] == 'tarball':
            pkg['srcurl_base'] = pkg['srcurl'].rsplit('/', 1)[0]
        elif pkg['srctype'] == 'Git':
            if pkg['srcurl'].startswith('git://'):
                pkg['srcurl_base'] = 'http://' + pkg['srcurl'][6:]
            else:
                pkg['srcurl_base'] = pkg['srcurl']
        if 'srcurl_base' in pkg and pkg['srcurl_base'].endswith('.git'):
            pkg['srcurl_base'] = pkg['srcurl_base'][:-4]
    db.execute(SQL_ATTACH_PISS)
    res_upstream = db.execute(SQL_GET_PISS_VERSION, (name,)).fetchone()
    if pkg['version'] and res_upstream and res_upstream['version']:
        pkg['upstream'] = dict(res_upstream)
        if res_upstream['version'].startswith(pkg['version']):
            pkg['upstream']['ver_compare'] = 'same'
        else:
            pkg['upstream']['ver_compare'] = VER_REL[
                version_compare(pkg['version'], res_upstream['version'])]
    return render('package.html', pkg=pkg, dep_rel=DEP_REL, repos=repos)

@app.route('/changelog/<name>')
def changelog(name, db):
    res = db.execute(SQL_GET_PACKAGE_INFO, (name,)).fetchone()
    if res is None:
        return bottle.HTTPResponse(render('error.txt',
                error='Package "%s" not found.' % name), 404,
                content_type='text/plain; charset=UTF-8')
    pkg = dict(res)
    db.execute('ATTACH ? AS marks', ('file:data/%s-marks.db?mode=ro' % pkg['tree'],))
    db.execute('ATTACH ? AS fossil', ('file:data/%s.fossil?mode=ro' % pkg['tree'],))
    changelog = []
    for row in db.execute(SQL_GET_PACKAGE_CHANGELOG, (name,)):
        changelog.append(dict(row))
    bottle.response.content_type = 'text/plain; charset=UTF-8'
    return render('changelog.txt', name=name, changes=changelog)

@app.route('/revdep/<name>')
def revdep(name, db):
    res = db.execute('SELECT 1 FROM packages WHERE name = ?', (name,)).fetchone()
    if res is None:
        return bottle.HTTPResponse(render('error.html',
                error='Package "%s" not found.' % name), 404)
    revdeps = collections.defaultdict(list)
    for relationship, group in itertools.groupby(
        db.execute(SQL_GET_PACKAGE_REV_REL, (name,)),
        key=operator.itemgetter('relationship')):
        for row in group:
            revdeps[relationship].append(dict(row))
    return render('revdep.html', name=name, revdeps=revdeps, dep_rel_rev=DEP_REL_REV)

@app.route('/lagging/<repo:path>')
def lagging(repo, db):
    page, pagesize = get_page()
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    arch = repos[repo]['architecture']
    res = Pager(db.execute(SQL_GET_PACKAGE_LAGGING,
                (repo, arch)), pagesize, page)
    for row in res:
        packages.append(dict(row))
    if packages:
        return render('lagging.html',
            repo=repo, packages=packages, page=pagination(res))
    else:
        return render('error.html',
            error="There's no lagging packages.")

@app.route('/srcupd/<tree>')
def srcupd(tree, db):
    page, pagesize = get_page()
    trees = db_trees(db)
    if tree not in trees:
        return bottle.HTTPResponse(render('error.html',
                error='Source tree "%s" not found.' % tree), 404)
    section = bottle.request.query.get('section') or None
    packages = []
    res = Pager(db.execute(SQL_GET_PACKAGE_SRCUPD, (tree, section, section)), pagesize, page)
    for row in res:
        packages.append(dict(row))
    if packages:
        return render('srcupd.html',
            tree=tree, packages=packages, section=section, page=pagination(res))
    else:
        return render('error.html', error="There's no lagging packages.")

@app.route('/ghost/<repo:path>')
def ghost(repo, db):
    page, pagesize = get_page()
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    res = Pager(db.execute(SQL_GET_PACKAGE_GHOST, (repo,)), pagesize, page)
    for row in res:
        packages.append(dict(row))
    if packages:
        return render('ghost.html', repo=repo, packages=packages, page=pagination(res))
    else:
        return render('error.html',
            error="There's no ghost packages.")

@app.route('/missing/<repo:path>')
def missing(repo, db):
    page, pagesize = get_page()
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    reponame = repos[repo]['realname']
    arch = repos[repo]['architecture']
    res = Pager(db.execute(SQL_GET_PACKAGE_MISSING,
                (reponame, arch, reponame)), pagesize, page)
    for row in res:
        packages.append(dict(row))
    if packages:
        return render('missing.html',
            repo=repo, packages=packages, page=pagination(res))
    else:
        return render('error.html', error="There's no missing packages.")

@app.route('/tree/<tree>')
def tree(tree, db):
    page, pagesize = get_page()
    trees = db_trees(db)
    if tree not in trees:
        return bottle.HTTPResponse(render('error.html',
                error='Source tree "%s" not found.' % tree), 404)
    packages = []
    res = Pager(db.execute(SQL_GET_PACKAGE_TREE, (tree,)), pagesize, page)
    for row in res:
        d = dict(row)
        d['dpkg_repos'] = ', '.join(sorted((d.pop('dpkg_availrepos') or '').split(',')))
        d['ver_compare'] = VER_REL[d['ver_compare']]
        packages.append(d)
    if packages:
        return render('tree.html', tree=tree, packages=packages, page=pagination(res))
    else:
        return render('error.html', error="There's no ghost packages.")

@app.route('/list.json')
def pkg_list(db):
    modified = db_last_modified(db)

    def _query():
        packages = []
        for row in db.execute(SQL_GET_PACKAGE_LIST):
            d = dict(row)
            packages.append(d)
        return json.dumps({'packages': packages, 'last_modified': modified},
                          sort_keys=True)

    return response_lm(_query, modified=modified,
        headers={'Content-Type': 'application/json'})

@app.route('/updates')
def updates(db):
    packages = []
    for row in db.execute(SQL_GET_PACKAGE_NEW_LIST, (100,)):
        d = dict(row)
        d['ver_compare'] = VER_REL[d['ver_compare']]
        packages.append(d)
    if packages:
        return render('updates.html', packages=packages)
    else:
        return render('error.html', error="There's no updates.")

@app.route('/repo/<repo:path>')
def repo(repo, db):
    page, pagesize = get_page()
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    res = Pager(db.execute(SQL_GET_PACKAGE_REPO, (repo,)), pagesize, page)
    for row in res:
        d = dict(row)
        latest, fullver = d['dpkg_version'], d['full_version']
        d['ver_compare'] = VER_REL[
            version_compare(latest, fullver) if latest else -1]
        packages.append(d)
    return render('repo.html', repo=repo, packages=packages, page=pagination(res))

_debcompare_key = functools.cmp_to_key(lambda a, b:
    (version_compare(a['version'], b['version'])
     or cmp(a['filename'], b['filename'])))

@app.route('/cleanmirror/<repo:path>')
def cleanmirror(repo, db):
    try:
        retain = int(bottle.request.query.get('retain', 0))
    except ValueError:
        retain = 0
    reason = bottle.request.query.get('reason')
    reason = frozenset(reason.split(',')) if reason else None
    getall = bool(bottle.request.query.get('all'))

    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.txt',
                error='Repo "%s" not found.' % repo), 404,
                content_type='text/plain; charset=UTF-8')
    debs = []
    for package, group in itertools.groupby(
            db.execute(SQL_GET_DEB_LIST_HASARCH
                if repo != 'noarch' else SQL_GET_DEB_LIST_NOARCH, (repo,)),
            key=operator.itemgetter('package')):
        debgroup = sorted(map(dict, group), key=_debcompare_key)
        latest = debgroup[-1]
        latestver = latest['version']
        if retain:
            debgroup = debgroup[:-retain]
            if not debgroup:
                continue
        for deb in debgroup:
            removereason = []
            if deb['version'] != latestver:
                removereason.append('old')
            elif deb['filename'] != latest['filename']:
                removereason.append('dup')
            if deb['outoftree']:
                removereason.append('outoftree')
            elif repo != 'noarch':
                if (deb['noarch'] and deb['noarchver']
                    and version_compare(deb['version'],
                        max(deb['noarchver'].split(','),
                        key=version_compare_key)) < 0):
                    if 'old' not in removereason:
                        removereason.append('old')
                    removereason.append('noarch')
            elif (not deb['noarch'] and deb['hasarchver']
                  and version_compare(deb['version'],
                      max(deb['hasarchver'].split(','),
                      key=version_compare_key)) < 0):
                removereason.append('hasarch')
            deb['removereason'] = removereason
            if reason:
                if reason.intersection(frozenset(removereason)):
                    debs.append(deb)
            elif getall or removereason:
                debs.append(deb)
    bottle.response.content_type = 'text/plain; charset=UTF-8'
    return render('cleanmirror.txt', repo=repo, packages=debs)

@app.route('/data/<filename>')
def data_dl(db, filename):
    if not (filename.endswith('.db')):
        bottle.abort(404, "Not found: '/data/%s'" % filename)
    cachedir = 'data/cache'
    fsize = fhash = None
    with open(os.path.join(cachedir, 'dbhashs'), 'r', encoding='utf-8') as f:
        for ln in f:
            fsize, fhash, fname = ln.strip().split(' ', 2)
            if fname == filename:
                break
    if fsize is None:
        bottle.abort(404, "Not found: '/data/%s'" % filename)
    gzfile = os.path.join(cachedir, filename + '.gz')
    accept_encoding = bottle.request.headers.get('Accept-Encoding', '')
    headers = {
        'Vary': 'Accept-Encoding',
        'Content-Type': 'application/x-sqlite3; charset=binary',
        'Content-Length': fsize,
        'Content-Disposition': 'attachment; filename="%s"' % filename
    }
    attachfn = filename
    mtime = os.stat(gzfile).st_mtime
    etag = '"%s"' % fhash
    if 'gzip' in accept_encoding.lower():
        headers['Content-Encoding'] = 'gzip'
        content = lambda: open(gzfile, 'rb')
    else:
        # prevent it from using sendfile
        content = lambda: iter_read1(gzip.open(gzfile, 'rb'))
    return response_lm(content, headers=headers, modified=mtime, etag=etag)

@app.route('/api_version')
def api_version(db):
    return {"version": __version__}

@app.route('/')
def index(db):
    source_trees = list(db_trees(db).values())
    repo_categories = [(c[1], [r for r in db_repos(db).values()
                       if r['category'] == c[0]]) for c in REPO_CAT]
    updates = []
    total = sum(r['pkgcount'] for r in source_trees)
    for row in db.execute(SQL_GET_PACKAGE_NEW):
        d = dict(row)
        updates.append(d)
    return render('index.html',
           total=total, repo_categories=repo_categories,
           source_trees=source_trees, updates=updates)


if __name__ == '__main__':
    import sys
    host = '0.0.0.0'
    port = 8082
    if len(sys.argv) > 1:
        srvhost = sys.argv[1]
        spl = srvhost.rsplit(":", 1)
        if spl[1].isnumeric():
            host = spl[0].lstrip('[').rstrip(']')
            port = int(spl[1])
        else:
            host = srvhost.lstrip('[').rstrip(']')
    app.run(host=host, port=port)
