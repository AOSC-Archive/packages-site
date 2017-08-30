#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import operator
import itertools
import functools
import collections

import jinja2
import bottle

import bottle_sqlite
from utils import cmp, version_compare, version_compare_key, strftime, sizeof_fmt

SQL_GET_PACKAGES = 'SELECT name, description, full_version FROM v_packages'

SQL_GET_PACKAGE_INFO = '''
SELECT
  name, tree, category, section, pkg_section, directory,
  description, full_version, commit_time, dep.dependency dependency
FROM v_packages
LEFT JOIN (
    SELECT
      package,
      group_concat(dependency || '|' || version || '|' || relationship) dependency
    FROM package_dependencies
    GROUP BY package
  ) dep
  ON dep.package = v_packages.name
WHERE name = ?
ORDER BY category, section, name
'''

SQL_GET_PACKAGE_INFO_GHOST = '''
SELECT DISTINCT
  package name, '' tree, '' category, '' section, '' pkg_section,
  '' directory, '' version, '' description, NULL commit_time,
  '' dependency, '' full_version
FROM dpkg_packages WHERE package = ?
'''

SQL_GET_PACKAGE_DPKG = '''
SELECT version, architecture, repo, filename, size
FROM dpkg_packages WHERE package = ?
ORDER BY version ASC, architecture ASC
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

SQL_GET_REPO_COUNT = '''
SELECT
  drs.repo name, dr.realname realname, dr.path path,
  dr.date date, dr.testing testing,
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

DEP_REL = collections.OrderedDict((
    ('PKGDEP', 'Depends'),
    ('BUILDDEP', 'Depends (build)'),
    ('PKGREP', 'Replaces'),
    ('PKGRECOM', 'Recommends'),
    ('PKGCONFL', 'Conflicts'),
    ('PKGBREAK', 'Breaks')
))
VER_REL = {
    -2: 'deprecated',
    -1: 'old',
    0: 'same',
    1: 'new'
}

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
        'sizeof_fmt': sizeof_fmt
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
        modified=db_last_modified(db))

@app.route('/search/')
def search(db):
    q = bottle.request.query.get('q')
    packages = []
    packages_set = set()
    if q:
        for row in db.execute(
            SQL_GET_PACKAGES + " WHERE name LIKE ? ORDER BY name",
            ('%%%s%%' % q,)):
            if row['name'] == q:
                bottle.redirect("/packages/" + q)
            packages.append(dict(row))
            packages_set.add(row['name'])
        row = db.execute(SQL_GET_PACKAGE_INFO_GHOST, (q,)).fetchone()
        if row:
            bottle.redirect("/packages/" + q)
        for row in db.execute(
            SQL_GET_PACKAGES + " WHERE description LIKE ? ORDER BY name",
            ('%%%s%%' % q,)):
            if row['name'] not in packages_set:
                packages.append(dict(row))
    if packages:
        return render('search.html', q=q, packages=packages)
    else:
        return render('error.html',
            error='No packages matching "%s" found.' % q)

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
    dpkg_dict = {}
    repo_list = set()
    for ver, group in itertools.groupby(db.execute(
        SQL_GET_PACKAGE_DPKG, (name,)), key=operator.itemgetter('version')):
        table_row = {}
        for row in group:
            d = dict(row)
            repo_list.add(d['repo'])
            table_row[d['repo']] = d
        dpkg_dict[ver] = table_row
    fullver = pkg['full_version']
    if pkgintree and fullver and fullver not in dpkg_dict:
        dpkg_dict[fullver] = {}
    pkg['repo'] = repo_list = sorted(repo_list)
    pkg['dpkg_matrix'] = [
        (ver, [dpkg_dict[ver].get(reponame) for reponame in repo_list])
        for ver in sorted(dpkg_dict.keys(),
        key=version_compare_key, reverse=True)]
    repos = db_repos(db)
    return render(
        'package.html', pkg=pkg, dep_rel=DEP_REL, repos=repos)

@app.route('/lagging/<repo:path>')
def lagging(repo, db):
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    reponame = repos[repo]['realname']
    for row in db.execute(SQL_GET_PACKAGE_LAGGING, (reponame, reponame)):
        packages.append(dict(row))
    if packages:
        return render('lagging.html', repo=repo, packages=packages)
    else:
        return render('error.html',
            error="There's no lagging packages.")

@app.route('/ghost/<repo:path>')
def ghost(repo, db):
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    for row in db.execute(SQL_GET_PACKAGE_GHOST, (repo,)):
        packages.append(dict(row))
    if packages:
        return render('ghost.html', repo=repo, packages=packages)
    else:
        return render('error.html',
            error="There's no ghost packages.")

@app.route('/missing/<repo:path>')
def missing(repo, db):
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    reponame = repos[repo]['realname']
    for row in db.execute(SQL_GET_PACKAGE_MISSING, (reponame, reponame, reponame)):
        packages.append(dict(row))
    if packages:
        return render('missing.html', repo=repo, packages=packages)
    else:
        return render('error.html', error="There's no missing packages.")

@app.route('/tree/<tree>')
def tree(tree, db):
    trees = db_trees(db)
    if tree not in trees:
        return bottle.HTTPResponse(render('error.html',
                error='Source tree "%s" not found.' % tree), 404)
    packages = []
    for row in db.execute(SQL_GET_PACKAGE_TREE, (tree,)):
        d = dict(row)
        d['dpkg_repos'] = ', '.join(sorted((d.pop('dpkg_availrepos') or '').split(',')))
        d['ver_compare'] = VER_REL[d['ver_compare']]
        packages.append(d)
    if packages:
        return render('tree.html', tree=tree, packages=packages)
    else:
        return render('error.html', error="There's no ghost packages.")

@app.route('/repo/<repo:path>')
def repo(repo, db):
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
    packages = []
    for row in db.execute(SQL_GET_PACKAGE_REPO, (repo,)):
        d = dict(row)
        latest, fullver = d['dpkg_version'], d['full_version']
        d['ver_compare'] = VER_REL[
            version_compare(latest, fullver) if latest else -1]
        packages.append(d)
    return render('repo.html', repo=repo, packages=packages)

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
        return bottle.HTTPResponse(render('error.html',
                error='Repo "%s" not found.' % repo), 404)
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
    bottle.response.content_type = 'text/plain; charset=UTF8'
    return render('cleanmirror.txt', repo=repo, packages=debs)

@app.route('/')
def index(db):
    repos = list(db_repos(db).values())
    source_trees = list(db_trees(db).values())
    updates = []
    total = sum(r['pkgcount'] for r in source_trees)
    for row in db.execute(SQL_GET_PACKAGE_NEW):
        d = dict(row)
        updates.append(d)
    return render('index.html',
           total=total, repos=repos, source_trees=source_trees,
           updates=updates)


if __name__ == '__main__':
    app.run(host='0.0.0.0')
