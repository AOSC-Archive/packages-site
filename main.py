#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import pickle
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
                  sizeof_fmt, parse_fail_arch, Pager

__version__ = '1.5'

SQL_GET_PACKAGES = 'SELECT name, description, full_version FROM v_packages'

SQL_GET_PACKAGE_INFO = '''
SELECT
  name, tree, tree_category, branch, category, section, pkg_section, directory,
  description, version, full_version, commit_time, dep.dependency dependency,
  (spabhost.value IS 'noarch') noarch, spfailarch.value fail_arch,
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
  '' description, '' version, '' full_version, NULL commit_time,
  '' dependency, 0 noarch, NULL fail_arch, 0 hasrevdep
FROM dpkg_packages WHERE package = ?
'''

SQL_GET_PACKAGE_CHANGELOG = '''
SELECT
  ((CASE WHEN ifnull(epoch, '') = '' THEN '' ELSE epoch || ':' END) ||
   version || (CASE WHEN ifnull(release, '') = '' THEN '' ELSE '-' ||
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
  version, architecture, repo, dr.realname reponame,
  dr.testing testing, filename, size
FROM dpkg_packages dp
LEFT JOIN dpkg_repos dr ON dr.name=dp.repo
WHERE package = ?
ORDER BY dr.realname ASC, version COLLATE vercomp DESC
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
  AND ((spabhost.value IS 'noarch') = (dpkg.reponame IS 'noarch'))
ORDER BY p.name
'''

SQL_GET_PACKAGE_NEW = '''
SELECT name, description, full_version, commit_time FROM v_packages
ORDER BY commit_time DESC LIMIT 10
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
ORDER BY commit_time DESC
LIMIT ?
'''

SQL_GET_PACKAGE_LAGGING = '''
SELECT
  v_packages.name name, dpkg.dpkg_version dpkg_version, description, full_version
FROM v_packages
LEFT JOIN package_spec spabhost
  ON spabhost.package = v_packages.name AND spabhost.key = 'ABHOST'
LEFT JOIN v_dpkg_packages_new dpkg
  ON dpkg.package = v_packages.name
WHERE dpkg.reponame = ? AND
  dpkg_version IS NOT null AND
  (dpkg.reponame IS 'noarch' OR ? != 'noarch') AND
  ((spabhost.value IS 'noarch') = (dpkg.reponame IS 'noarch'))
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

SQL_GET_PACKAGE_LIST = '''
SELECT
  name, tree, tree_category, branch, category, section, pkg_section, directory,
  description, version, full_version, commit_time,
  dpkg.dpkg_version dpkg_version,
  group_concat(DISTINCT dpkg.reponame) dpkg_availrepos,
  ifnull(CASE WHEN dpkg_version IS NOT null
   THEN (dpkg_version > full_version COLLATE vercomp) -
   (dpkg_version < full_version COLLATE vercomp)
   ELSE -1 END, -2) ver_compare
FROM v_packages
LEFT JOIN v_dpkg_packages_new dpkg ON dpkg.package = v_packages.name
GROUP BY name
ORDER BY name
'''

SQL_GET_REPO_COUNT = '''
SELECT
  drs.repo name, dr.realname realname, dr.path path,
  dr.date date, dr.testing testing, dr.category category,
  drs.packagecnt pkgcount, drs.ghostcnt ghost,
  drs.laggingcnt lagging, drs.missingcnt missing
FROM dpkg_repo_stats drs
LEFT JOIN dpkg_repos dr ON dr.name=drs.repo
LEFT JOIN (
  SELECT drs2.repo repo, drs2.packagecnt packagecnt
  FROM dpkg_repo_stats drs2
  LEFT JOIN dpkg_repos dr2 ON dr2.name=drs2.repo
  WHERE dr2.testing=0
) drs_m ON drs_m.repo=dr.realname
ORDER BY drs_m.packagecnt DESC, dr.testing ASC
'''

SQL_GET_TREES = '''
SELECT
  p.tree name, t.category, t.url, max(pv.commit_time) date,
  count(DISTINCT p.name) pkgcount
FROM packages p
INNER JOIN package_versions pv ON pv.package=p.name
LEFT JOIN trees t ON t.name=p.tree
GROUP BY p.tree
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
      package,
      group_concat(version) versions
    FROM dpkg_packages
    WHERE repo = 'noarch'
    GROUP BY package
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
      package,
      group_concat(version) versions
    FROM dpkg_packages
    WHERE repo != 'noarch'
    GROUP BY package
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
REPO_CAT = (('base', None), ('bsp', 'BSP'), ('overlay', 'Overlay'))
PAGESIZE = 60

RE_QUOTES = re.compile(r'"([a-z]+|\$)"')

application = app = bottle.Bottle()
plugin = bottle_sqlite.Plugin(
    dbfile='data/abbs.db',
    collations={'vercomp': version_compare}
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

    body = '' if bottle.request.method == 'HEAD' else f_body()

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
render = lambda *args, **kwargs: (
    kwargs
    if (bottle.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or bottle.request.query.get('type') == 'json')
    else jinja2_template(*args, **kwargs)
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
db_last_modified.last_updated = 0
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
db_repos.last_updated = 0
db_repos.value = {}


def db_trees(db, ttl=1800):
    now = time.monotonic()
    if now - db_trees.last_updated > ttl:
        d = collections.OrderedDict((row['name'], dict(row))
            for row in db.execute(SQL_GET_TREES))
        db_trees.last_updated = now
        db_trees.value = d
    return db_trees.value
db_trees.last_updated = 0
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
    noredir = bottle.request.query.get('noredir')
    packages = []
    packages_set = set()
    page, pagesize = get_page()
    if q:
        for row in db.execute(
            SQL_GET_PACKAGES + " WHERE name LIKE ? ORDER BY name",
            ('%%%s%%' % q,)):
            if row['name'] == q and not noredir:
                bottle.redirect("/packages/" + q)
            packages.append(dict(row))
            packages_set.add(row['name'])
        row = db.execute(SQL_GET_PACKAGE_INFO_GHOST, (q,)).fetchone()
        if row and not noredir:
            bottle.redirect("/packages/" + q)
        for row in db.execute(
            SQL_GET_PACKAGES + " WHERE description LIKE ? ORDER BY name",
            ('%%%s%%' % q,)):
            if row['name'] not in packages_set:
                packages.append(dict(row))
    res = Pager(packages, pagesize, page)
    return render('search.html', q=q, packages=list(res), page=pagination(res))

@app.route('/query/', method=('GET', 'POST'))
def query(db):
    q = bottle.request.forms.get('q')
    if not q:
        return render('query.html', q='', headers=[], rows=[], error=None)
    proc = subprocess.run(
        ('python3', 'rawquery.py', 'data/abbs.db'),
        input=q.encode('utf-8'), stdout=subprocess.PIPE, check=True)
    result = pickle.loads(proc.stdout)
    return render('query.html', q=q, headers=result.get('header', ()),
                  rows=result.get('rows', ()), error=result.get('error'))

@app.route('/packages/<name>')
def package(name, db):
    res = db.execute(SQL_GET_PACKAGE_INFO, (name,)).fetchone()
    pkgintree = True
    if res is None:
        res = db.execute(SQL_GET_PACKAGE_INFO_GHOST, (name,)).fetchone()
        pkgintree = False
    if res is None:
        return bottle.HTTPResponse(render('error.html',
                error='Package "%s" not found.' % name), 404)
    pkg = dict(res)
    dep_dict = {}
    if pkg['dependency']:
        for dep in pkg['dependency'].split(','):
            dep_pkg, dep_ver, dep_rel = dep.split('|')
            if dep_rel in dep_dict:
                dep_dict[dep_rel].append((dep_pkg, dep_ver))
            else:
                dep_dict[dep_rel] = [(dep_pkg, dep_ver)]
    pkg['dependency'] = dep_dict
    fullver = pkg['full_version']
    repos = db_repos(db)
    dpkg_dict = {}
    ver_list = []
    if pkgintree and fullver:
        ver_list.append(fullver)
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
    return render('package.html', pkg=pkg, dep_rel=DEP_REL, repos=repos)

@app.route('/changelog/<name>')
def changelog(name, db):
    res = db.execute(SQL_GET_PACKAGE_INFO, (name,)).fetchone()
    if res is None:
        return bottle.HTTPResponse(render('error.txt',
                error='Package "%s" not found.' % name), 404,
                content_type='text/plain; charset=UTF-8')
    pkg = dict(res)
    db.execute('ATTACH ? AS marks', ('data/%s-marks.db' % pkg['tree'],))
    db.execute('ATTACH ? AS fossil', ('data/%s.fossil' % pkg['tree'],))
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
    reponame = repos[repo]['realname']
    res = Pager(db.execute(
                SQL_GET_PACKAGE_LAGGING, (reponame, reponame)), pagesize, page)
    for row in res:
        packages.append(dict(row))
    if packages:
        return render('lagging.html',
            repo=repo, packages=packages, page=pagination(res))
    else:
        return render('error.html',
            error="There's no lagging packages.")

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
    res = Pager(db.execute(SQL_GET_PACKAGE_MISSING,
                (reponame, reponame, reponame)), pagesize, page)
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

@app.route('/list')
def pkg_list(db):
    packages = []
    for row in db.execute(SQL_GET_PACKAGE_LIST):
        d = dict(row)
        packages.append(d)
    return {'packages': packages}

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
        return render('error.html', error="There's no ghost packages.")

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
    if not (filename.endswith('.db') or filename.endswith('.fossil')):
        bottle.abort(404, "Not found: '/data/%s'" % filename)
    mime = 'application/x-sqlite3; charset=binary'
    return bottle.static_file(filename, root='data', mimetype=mime, download=filename)

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
    app.run(host='0.0.0.0')
