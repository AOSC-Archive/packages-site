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
from debian_support import version_compare as _version_compare

import jinja2
import bottle
import bottle.ext.sqlite

SQL_GET_PACKAGES = '''
SELECT name, version, spepoch.value epoch, release, description FROM packages
LEFT JOIN package_spec spepoch
  ON spepoch.package = packages.name AND spepoch.key = 'PKGEPOCH'
'''

SQL_GET_PACKAGE_INFO = '''
SELECT
  name, tree, category, section, pkg_section, directory,
  spec.epoch, version, release,
  description, commit_time, dep.dependency
FROM packages
LEFT JOIN (
    SELECT
      package,
      value epoch
    FROM package_spec
    WHERE key = 'PKGEPOCH'
  ) spec
ON spec.package = packages.name
LEFT JOIN (
    SELECT
      package,
      group_concat(dependency || '|' || version || '|' || relationship) dependency
    FROM package_dependencies
    GROUP BY package
  ) dep
  ON dep.package = packages.name
WHERE name = ?
ORDER BY category, section, name
'''

SQL_GET_PACKAGE_INFO_GHOST = '''
SELECT DISTINCT
  package name, '' tree, '' category, '' section, '' pkg_section,
  '' directory, '' epoch, '' version, '' release,
  '' description, NULL commit_time, '' dependency
FROM dpkg_packages WHERE package = ?
'''

SQL_GET_PACKAGE_DPKG = '''
SELECT version, architecture, repo, filename, size
FROM dpkg_packages WHERE package = ?
ORDER BY version ASC, architecture ASC
'''

SQL_GET_PACKAGE_REPO = '''
SELECT
  packages.name, packages.version, spepoch.value epoch, packages.release,
  dpkg.versions dpkg_versions, packages.description
FROM packages
LEFT JOIN package_spec spepoch
  ON spepoch.package = packages.name AND spepoch.key = 'PKGEPOCH'
LEFT JOIN package_spec spabhost
  ON spabhost.package = packages.name AND spabhost.key = 'ABHOST'
LEFT JOIN (
    SELECT
      package,
      repo,
      group_concat(version) versions
    FROM dpkg_packages
    WHERE repo = ?
    GROUP BY package
  ) dpkg
  ON dpkg.package = packages.name
WHERE dpkg.repo = ?
  AND ((spabhost.value IS 'noarch') = (dpkg.repo IS 'noarch'))
ORDER BY packages.name
'''

SQL_GET_PACKAGE_NEW = '''
SELECT
  name, packages.version, spec.epoch, release, 
  dpkg.versions dpkg_versions, description
FROM packages
LEFT JOIN (
    SELECT
      package,
      value epoch
    FROM package_spec
    WHERE key = 'PKGEPOCH'
  ) spec
  ON spec.package = packages.name
LEFT JOIN (
    SELECT
      package,
      group_concat(version) versions
    FROM dpkg_packages
    GROUP BY package
  ) dpkg
  ON dpkg.package = packages.name
ORDER BY packages.commit_time DESC
LIMIT 10
'''

SQL_GET_PACKAGE_LAGGING = '''
SELECT
  name, packages.version, spepoch.value epoch, release, 
  dpkg.versions dpkg_versions, description
FROM packages
LEFT JOIN package_spec spepoch
  ON spepoch.package = packages.name AND spepoch.key = 'PKGEPOCH'
LEFT JOIN package_spec spabhost
  ON spabhost.package = packages.name AND spabhost.key = 'ABHOST'
LEFT JOIN (
    SELECT
      package,
      repo,
      group_concat(version) versions
    FROM dpkg_packages
	WHERE repo = ?
    GROUP BY package
  ) dpkg
  ON dpkg.package = packages.name
WHERE (dpkg.repo IS 'noarch' OR ? != 'noarch') AND
  ((spabhost.value IS 'noarch') = (dpkg.repo IS 'noarch'))
'''

SQL_GET_REPO_COUNT = '''
SELECT
  dpkg_repos.name, path, date, count(packages.name) pkgcount,
  sum(CASE WHEN packages.name IS NULL THEN 1 END) ghost
FROM dpkg_repos
LEFT JOIN (
    SELECT DISTINCT
      package,
      repo
    FROM dpkg_packages
  ) dpkg
  ON dpkg.repo = dpkg_repos.name
LEFT JOIN packages
  ON packages.name = dpkg.package
LEFT JOIN package_spec spabhost
  ON spabhost.package = packages.name AND spabhost.key = 'ABHOST'
WHERE ((spabhost.value IS 'noarch') = (dpkg.repo IS 'noarch'))
GROUP BY dpkg_repos.name ORDER BY pkgcount + ifnull(ghost, 0) DESC
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
    -1: 'old',
    0: 'same',
    1: 'new'
}

RE_QUOTES = re.compile(r'"([a-z]+|\$)"')

application = app = bottle.Bottle()
plugin = bottle.ext.sqlite.Plugin(dbfile='data/abbs.db')
app.install(plugin)

cmp = lambda a, b: ((a > b) - (a < b))
version_compare = functools.lru_cache(maxsize=1024)(
    lambda a, b: _version_compare(a, b) or cmp(a, b)
)
version_compare_key = functools.cmp_to_key(version_compare)

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


def sizeof_fmt(num, suffix='B'):
    for unit in ['','Ki','Mi','Gi','Ti','Pi','Ei','Zi']:
        if abs(num) < 1024:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


def strftime(t=None, fmt='%Y-%m-%d %H:%M:%S'):
    return time.strftime(fmt, time.gmtime(t))


jinja2_settings = {
    'filters': {
        'strftime': strftime,
        'sizeof_fmt': sizeof_fmt
    },
    'autoescape': jinja2.select_autoescape(('html', 'htm', 'xml'))
}
jinja2_template = functools.partial(bottle.jinja2_template,
    template_settings=jinja2_settings)


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


def read_db(filename):
    db = sqlite3.connect(filename)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    packages = []
    for row in cur.execute(SQL_GET_PACKAGES):
        pkg = dict(row)
        dep_dict = {}
        if row[-1]:
            for dep in row[-1].split(','):
                dep_pkg, dep_ver, dep_rel = dep.split('|')
                if dep_rel in dep_dict:
                    dep_dict[dep_rel].append((dep_pkg, dep_ver))
                else:
                    dep_dict[dep_rel] = [(dep_pkg, dep_ver)]
        pkg['dependency'] = dep_dict
        packages.append(pkg)
    return packages

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


def db_last_modified(db, ttl=300):
    now = time.monotonic()
    if now - db_last_modified.last_updated > ttl:
        row = db.execute(
            'SELECT commit_time FROM packages ORDER BY commit_time DESC LIMIT 1'
            ).fetchone()
        if row:
            db_last_modified.last_updated = now
            db_last_modified.value = row[0]
    return db_last_modified.value
db_last_modified.last_updated = 0
db_last_modified.value = 0


def db_repos(db, ttl=300):
    now = time.monotonic()
    if now - db_repos.last_updated > ttl:
        d = collections.OrderedDict((row['name'], dict(row))
            for row in db.execute(SQL_GET_REPO_COUNT))
        db_repos.last_updated = now
        db_repos.value = d
    return db_repos.value
db_repos.last_updated = 0
db_repos.value = {}


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
        return jinja2_template('search.html', q=q, packages=packages)
    else:
        return jinja2_template('error.html',
            error='No packages matching "%s" found.' % q)

@app.route('/packages/<name>')
def package(name, db):
    res = db.execute(SQL_GET_PACKAGE_INFO, (name,)).fetchone()
    pkgintree = True
    if res is None:
        res = db.execute(SQL_GET_PACKAGE_INFO_GHOST, (name,)).fetchone()
        pkgintree = False
    if res is None:
        return bottle.HTTPResponse(jinja2_template('error.html',
                error='Package "%s" not found.' % name), 404)
    pkg = dict(res)
    dep_dict = {}
    if pkgintree:
        fullver = makefullver(pkg['epoch'], pkg['version'], pkg['release'])
        pkg['full_version'] = fullver
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
    if pkgintree and fullver not in dpkg_dict:
        dpkg_dict[fullver] = {}
    pkg['repo'] = repo_list = sorted(repo_list)
    pkg['dpkg_matrix'] = [
        (ver, [dpkg_dict[ver].get(reponame) for reponame in repo_list])
        for ver in sorted(dpkg_dict.keys(),
        key=version_compare_key, reverse=True)]
    repos = db_repos(db)
    return jinja2_template(
        'package.html', pkg=pkg, dep_rel=DEP_REL, repos=repos)

@app.route('/lagging/<repo>')
def lagging(repo, db):
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(jinja2_template('error.html',
                error='Repo "%s" not found.' % name), 404)
    packages = []
    for row in db.execute(SQL_GET_PACKAGE_LAGGING, (repo, repo)):
        d = dict(row)
        dpkg_versions = (d.pop('dpkg_versions') or '').split(',')
        latest = max(dpkg_versions, key=version_compare_key)
        fullver = makefullver(d['epoch'], d['version'], d['release'])
        if not latest or version_compare(latest, fullver) < 0:
            d['dpkg_version'] = latest
            d['full_version'] = fullver
            packages.append(d)
    if packages:
        return jinja2_template('lagging.html', repo=repo, packages=packages)
    else:
        return jinja2_template('error.html',
            error="There's no lagging packages.")

@app.route('/repo/<repo>')
def repo(repo, db):
    repos = db_repos(db)
    if repo not in repos:
        return bottle.HTTPResponse(jinja2_template('error.html',
                error='Repo "%s" not found.' % name), 404)
    packages = []
    for row in db.execute(SQL_GET_PACKAGE_REPO, (repo, repo)):
        d = dict(row)
        dpkg_versions = (d.pop('dpkg_versions') or '').split(',')
        latest = max(dpkg_versions, key=version_compare_key)
        fullver = makefullver(d['epoch'], d['version'], d['release'])
        d['dpkg_version'] = latest
        d['full_version'] = fullver
        d['ver_compare'] = VER_REL[
            version_compare(latest, fullver)]
        packages.append(d)
    return jinja2_template('repo.html', repo=repo, packages=packages)

@app.route('/')
def index(db):
    repos = list(db_repos(db).values())
    source_trees = list(db.execute(
        'SELECT tree name, max(commit_time) date, count(name) pkgcount'
        ' FROM packages GROUP BY tree ORDER BY pkgcount DESC'))
    updates = []
    total = sum(r[-1] for r in source_trees)
    for row in db.execute(SQL_GET_PACKAGE_NEW):
        d = dict(row)
        dpkg_versions = (d.pop('dpkg_versions') or '').split(',')
        latest = max(dpkg_versions, key=version_compare_key)
        fullver = makefullver(d['epoch'], d['version'], d['release'])
        if not latest or version_compare(latest, fullver) < 0:
            d['full_version'] = fullver
            d['ver_compare'] = VER_REL[
                version_compare(latest, fullver)]
            updates.append(d)
    return jinja2_template('index.html',
           total=total, repos=repos, source_trees=source_trees,
           updates=updates)


if __name__ == '__main__':
    app.run(host='0.0.0.0')
