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
from debian_support import version_compare

import bottle
import bottle.ext.sqlite

SQL_GET_PACKAGES = '''
SELECT name, version, spec.epoch, release, description FROM packages
LEFT JOIN (
  SELECT
    package,
    value epoch
  FROM package_spec
  WHERE key = 'PKGEPOCH'
) spec
ON spec.package = packages.name
'''

SQL_GET_PACKAGE_INFO = '''
SELECT
  name, category, section, pkg_section, spec.epoch, version, release,
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

SQL_GET_PACKAGE_DPKG = '''
SELECT version, architecture, repo, filename, size
FROM dpkg_packages WHERE package = ?
ORDER BY version ASC, architecture ASC
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

app = bottle.Bottle()
plugin = bottle.ext.sqlite.Plugin(dbfile='data/abbs.db')
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


def sizeof_fmt(num, suffix='B'):
    for unit in ['','Ki','Mi','Gi','Ti','Pi','Ei','Zi']:
        if abs(num) < 1024:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


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


@app.route('/static/<filename>')
def server_static(filename):
    return bottle.static_file(filename, root='static')

@app.route('/pkgtrie.js')
def pkgtrie(db):
    return response_lm(lambda: bottle.jinja2_template(
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
        for row in db.execute(
            SQL_GET_PACKAGES + " WHERE description LIKE ? ORDER BY name",
            ('%%%s%%' % q,)):
            if row['name'] not in packages_set:
                packages.append(dict(row))
    if packages:
        return bottle.jinja2_template('search.html', q=q, packages=packages)
    else:
        return bottle.jinja2_template('error.html',
            error='No packages matching "%s" found.' % q)

@app.route('/packages/<name>')
def package(name, db):
    res = db.execute(SQL_GET_PACKAGE_INFO, (name,)).fetchone()
    if res is None:
        return bottle.HTTPResponse(bottle.jinja2_template('error.html',
                error='Package "%s" not found.' % name), 404)
    pkg = dict(res)
    dep_dict = {}
    fullver = pkg['version']
    if pkg['epoch']:
        fullver = '%s:%s' % (pkg['epoch'], fullver)
    if pkg['release']:
        fullver = '%s-%s' % (fullver, pkg['release'])
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
            reponame = d['repo'][3:]
            repo_list.add(reponame)
            table_row[reponame] = d
        dpkg_dict[ver] = table_row
    if fullver not in dpkg_dict:
        dpkg_dict[fullver] = {}
    pkg['repo'] = repo_list = sorted(repo_list)
    pkg['dpkg_matrix'] = [
        (ver, [dpkg_dict[ver].get(reponame) for reponame in repo_list])
        for ver in sorted(dpkg_dict.keys(),
        key=functools.cmp_to_key(version_compare), reverse=True)]
    return bottle.jinja2_template(
        'package.html', pkg=pkg, dep_rel=DEP_REL,
        template_settings={'filters': {'sizeof_fmt': sizeof_fmt}}
    )

@app.route('/lagging')
def lagging(db):
    res = db.execute(SQL_GET_PACKAGE_INFO, (name,)).fetchone()
    if res is None:
        return bottle.HTTPResponse(bottle.jinja2_template('error.html',
                error='Package "%s" not found.' % name), 404)

@app.route('/')
def index():
    return bottle.jinja2_template('index.html')



def main(args):
    return 0

if __name__ == '__main__':
    app.run(host='0.0.0.0')
