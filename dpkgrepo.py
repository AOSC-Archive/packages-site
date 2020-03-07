#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2017 AOSC-Dev <aosc@members.fsf.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301, USA.

import os
import lzma
import sqlite3
import hashlib
import logging
import calendar
import argparse
import collections
import urllib.parse
from email.utils import parsedate

try:
    import httpx as requests
except ImportError:
    import requests

import deb822

logging.basicConfig(
    format='%(asctime)s %(levelname).1s %(message)s', level=logging.INFO)

# base (list all arch)
#  - amd64
#    - stable
#    - testing
#    - explosive
#  - arm64
#  - ...
#  - noarch
# overlay (list avail arch)
#  - bsp-sunxi
#    - arm64
#      - stable
#      - testing
#      - explosive
#    - armel
#  - opt-avx2
#    - amd64

Repo = collections.namedtuple('Repo', (
    'name',     # primary key
    'realname', # overlay-arch, group key
    'source_tree',  # git source
    'category', # base, bsp, overlay
    'testing',  # 0-2, testing level
    'suite',        # deb source suite/distribution, git branch
    'component',    # deb source component
    'architecture', # deb source architecture
))

ARCHS = ('amd64', 'arm64', 'armel', 'i586', 'powerpc', 'ppc64', 'riscv64', 'noarch')
BRANCHES = ('stable', 'stable-proposed', 'testing', 'testing-proposed', 'explosive')
OVERLAYS = (
    # dpkg_repos.category, component, source, arch
    ('base', 'main', None, ARCHS),
    ('bsp', 'bsp-sunxi', 'aosc-os-arm-bsps', ('armel', 'arm64', 'noarch')),
    ('bsp', 'bsp-rk', 'aosc-os-arm-bsps', ('armel', 'arm64', 'noarch')),
    ('overlay', 'opt-avx2', None, ('amd64',)),
    ('overlay', 'opt-g4', None, ('powerpc',)),
)
REPOS = collections.OrderedDict((k, []) for k in BRANCHES)
for category, component, source, archs in OVERLAYS:
    for arch in archs:
        for testlvl, branch in enumerate(BRANCHES):
            if category == 'base':
                realname = arch
            else:
                realname = '%s-%s' % (component, arch)
            REPOS[branch].append(Repo(
                realname + '/' + branch, realname,
                source, category, testlvl, branch, component, arch
            ))


def _url_slash(url):
    if url[-1] == '/':
        return url
    return url + '/'

def init_db(db):
    cur = db.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_repos ('
                'name TEXT PRIMARY KEY,' # key: bsp-sunxi-armel/testing
                'realname TEXT,'    # group key: amd64, bsp-sunxi-armel
                'source_tree TEXT,' # abbs tree
                'category TEXT,' # base, bsp, overlay
                'testing INTEGER,'    # 0, 1, 2
                'suite TEXT,'         # stable, testing, explosive
                'component TEXT,'     # main, bsp-sunxi, opt-avx2
                'architecture TEXT,'  # amd64, all
                'origin TEXT,'
                'label TEXT,'
                'codename TEXT,'
                'date INTEGER,'
                'valid_until INTEGER,'
                'description TEXT'
                ')')
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_packages ('
                'package TEXT,'
                'version TEXT,'
                'architecture TEXT,'
                'repo TEXT,'
                'maintainer TEXT,'
                'installed_size INTEGER,'
                'filename TEXT,'
                'size INTEGER,'
                'sha256 TEXT,'
                # we have Section and Description in packages table
                'PRIMARY KEY (package, version, architecture, repo)'
                # 'FOREIGN KEY(package) REFERENCES packages(name)'
                ')')
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_package_dependencies ('
                'package TEXT,'
                'version TEXT,'
                'architecture TEXT,'
                'repo TEXT,'
                'relationship TEXT,'
                'value TEXT,'
                'PRIMARY KEY (package, version, architecture, repo, relationship)'
                # 'FOREIGN KEY(package) REFERENCES dpkg_packages(package)'
                ')')
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_package_duplicate ('
                'package TEXT,'
                'version TEXT,'
                'architecture TEXT,'
                'repo TEXT,'
                'maintainer TEXT,'
                'installed_size INTEGER,'
                'filename TEXT,'
                'size INTEGER,'
                'sha256 TEXT,'
                'PRIMARY KEY (repo, filename)'
                ')')
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_repo_stats ('
                'repo TEXT PRIMARY KEY,'
                'packagecnt INTEGER,'
                'ghostcnt INTEGER,'
                'laggingcnt INTEGER,'
                'missingcnt INTEGER,'
                'oldcnt INTEGER,'
                'FOREIGN KEY(repo) REFERENCES dpkg_repos(name)'
                ')')
    cur.execute("DROP VIEW IF EXISTS v_dpkg_packages_new")
    cur.execute("CREATE VIEW IF NOT EXISTS v_dpkg_packages_new AS "
                "SELECT dp.package package, "
                "  max(version COLLATE vercomp) dpkg_version, "
                "  dp.repo repo, dr.realname reponame, "
                "  dr.architecture architecture, "
                "  dr.suite branch "
                "FROM dpkg_packages dp "
                "LEFT JOIN dpkg_repos dr ON dr.name=dp.repo "
                "GROUP BY package, repo")
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dpkg_repos'
                ' ON dpkg_repos (realname)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dpkg_packages'
                ' ON dpkg_packages (package, repo)')
    db.commit()
    cur.close()

def remove_clearsign(blob):
    clearsign_header = b'-----BEGIN PGP SIGNED MESSAGE-----'
    pgpsign_header = b'-----BEGIN PGP SIGNATURE-----'
    if not blob.startswith(clearsign_header):
        return blob
    lines = []
    content = False
    for k, ln in enumerate(blob.splitlines(True)):
        if not ln.rstrip():
            content = True
        elif content:
            if ln.rstrip() == pgpsign_header:
                break
            elif ln.startswith(b'- '):
                lines.append(ln[2:])
            else:
                lines.append(ln)
    return b''.join(lines)

def download_catalog(url, local=False, timeout=120, ignore404=False):
    if local:
        urlsp = urllib.parse.urlsplit(url)
        filename = (urlsp.netloc + urlsp.path).replace('/', '_')
        try:
            with open('/var/lib/apt/lists/' + filename, 'rb') as f:
                content = f.read()
            print(url, local)
            return content
        except FileNotFoundError:
            pass
    req = requests.get(url, timeout=120)
    if ignore404 and req.status_code == 404:
        return None
    req.raise_for_status()
    return req.content

def suite_update(db, mirror, suite, repos=None, local=False, force=False):
    """
    Fetch and parse InRelease file. Update relavant metadata.
    suite: branch
    repos: list of Repos
    """
    url = urllib.parse.urljoin(mirror, '/'.join(('dists', suite, 'InRelease')))
    content = download_catalog(url, local)
    cur = db.cursor()
    if content is None:
        logging.error('dpkg suite %s not found' % suite)
        if not repos:
            return {}
        for repo in repos:
            cur.execute('UPDATE dpkg_repos SET origin=null, label=null, '
                'codename=null, date=null, valid_until=null, description=null '
                'WHERE name=?', (repo.name,))
            cur.execute('DELETE FROM dpkg_package_dependencies WHERE repo=?',
                (repo.name,))
            cur.execute('DELETE FROM dpkg_package_duplicate WHERE repo=?',
                (repo.name,))
            cur.execute('DELETE FROM dpkg_packages WHERE repo=?', (repo.name,))
        db.commit()
        return {}
    releasetxt = remove_clearsign(content).decode('utf-8')
    rel = deb822.Release(releasetxt)
    pkgrepos = []
    for item in rel['SHA256']:
        path = item['name'].split('/')
        if path[-1] == 'Packages.xz':
            arch = path[1].split('-')[-1]
            if arch == 'all':
                arch = 'noarch'
            component = path[0]
            if component == 'main':
                realname = arch
            else:
                realname = '%s-%s' % (component, arch)
            name = '%s/%s' % (realname, suite)
            repo = Repo(name, realname, None, 'base', 0, suite, component, arch)
            pkgrepos.append(
                (repo, item['name'], int(item['size']), item['sha256']))
    rel_date = calendar.timegm(parsedate(rel['Date'])) if 'Date' in rel else None
    rel_valid = None
    if 'Valid-Until' in rel:
        rel_valid = calendar.timegm(parsedate(rel['Valid-Until']))
    result_repos = {}
    repo_dict = {}
    if repos:
        repo_dict = {(r.component, r.architecture): r for r in repos}
    for pkgrepo, filename, size, sha256 in pkgrepos:
        try:
            repo = repo_dict.pop((pkgrepo.component, pkgrepo.architecture))
        except KeyError:
            repo = pkgrepo
        res = cur.execute('SELECT date FROM dpkg_repos WHERE name=?',
                          (repo.name,)).fetchone()
        if res and rel_date and not force:
            if res[0] and res[0] >= rel_date:
                continue
        pkgpath = '/'.join(('dists', suite, filename))
        result_repos[repo.component, repo.architecture] = (repo, pkgpath, size, sha256)
        cur.execute('REPLACE INTO dpkg_repos VALUES '
            '(?,?,?,?,?, ?,?,?,?,?, ?,?,?,?)', (
            repo.name, repo.realname,
            repo.source_tree, repo.category, repo.testing, repo.suite,
            repo.component, repo.architecture, rel.get('Origin'),
            rel.get('Label'), rel.get('Codename'), rel_date, rel_valid,
            rel.get('Description')
        ))
    for repo in repo_dict.values():
        cur.execute('UPDATE dpkg_repos SET origin=null, label=null, '
            'codename=null, date=null, valid_until=null, description=null '
            'WHERE name=?', (repo.name,))
        cur.execute('DELETE FROM dpkg_package_dependencies WHERE repo=?',
            (repo.name,))
        cur.execute('DELETE FROM dpkg_package_duplicate WHERE repo=?',
            (repo.name,))
        cur.execute('DELETE FROM dpkg_packages WHERE repo=?', (repo.name,))
    cur.close()
    db.commit()
    return result_repos

_relationship_fields = ('depends', 'pre-depends', 'recommends',
        'suggests', 'breaks', 'conflicts', 'provides', 'replaces',
        'enhances')

def package_update(db, mirror, repo, path, size, sha256, local=False):
    logging.info(repo.name)
    url = urllib.parse.urljoin(mirror, path)
    content = download_catalog(url, local)
    if len(content) != size:
        logging.warning('%s size %d != %d', url, len(content), size)
    elif hashlib.sha256(content).hexdigest() != sha256:
        logging.warning('%s sha256 mismatch', url)
    pkgs = lzma.decompress(content).decode('utf-8')
    packages = {}
    cur = db.cursor()
    cur.execute('DELETE FROM dpkg_package_duplicate WHERE repo = ?', (repo.name,))
    packages_old = set(cur.execute(
        'SELECT package, version, architecture, repo FROM dpkg_packages'
        ' WHERE repo = ?', (repo.name,)
    ))
    for pkg in deb822.Packages.iter_paragraphs(pkgs):
        name = pkg['Package']
        arch = pkg['Architecture']
        ver = pkg['Version']
        pkgtuple = (name, ver, arch, repo.name)
        pkginfo = (
            name, ver, arch, repo.name, pkg.get('Maintainer'),
            int(pkg['Installed-Size']) if 'Installed-Size' in pkg else None,
            pkg['Filename'], int(pkg['Size']), pkg.get('SHA256')
        )
        if pkgtuple in packages:
            logging.warning('duplicate package: %r', pkgtuple)
            cur.execute(
                'REPLACE INTO dpkg_package_duplicate VALUES (?,?,?,?,?,?,?,?,?)',
                packages[pkgtuple])
            cur.execute(
                'REPLACE INTO dpkg_package_duplicate VALUES (?,?,?,?,?,?,?,?,?)',
                pkginfo)
            if pkg['Filename'] < packages[pkgtuple][6]:
                continue
        packages[pkgtuple] = pkginfo
        cur.execute('REPLACE INTO dpkg_packages VALUES (?,?,?,?,?,?,?,?,?)',
                    pkginfo)
        oldrels = frozenset(row[0] for row in cur.execute(
            'SELECT relationship FROM dpkg_package_dependencies'
            ' WHERE package = ? AND version = ? AND architecture = ? AND repo = ?',
            (name, ver, arch, repo.name)
        ))
        newrels = set()
        for rel in _relationship_fields:
            if rel in pkg:
                cur.execute(
                    'REPLACE INTO dpkg_package_dependencies VALUES (?,?,?,?,?,?)',
                    (name, ver, arch, repo.name, rel, pkg[rel])
                )
                newrels.add(rel)
        for rel in oldrels.difference(newrels):
            cur.execute(
                'DELETE FROM dpkg_package_dependencies'
                ' WHERE package = ? AND version = ? AND architecture = ?'
                ' AND repo = ? AND relationship = ?',
                (name, ver, arch, repo.name, rel)
            )
    for pkg in packages_old.difference(packages.keys()):
        cur.execute('DELETE FROM dpkg_packages WHERE package = ? AND version = ?'
                    ' AND architecture = ? AND repo = ?', pkg)
        cur.execute('DELETE FROM dpkg_package_dependencies WHERE package = ?'
                    ' AND version = ? AND architecture = ? AND repo = ?', pkg)
    cur.close()
    db.commit()

SQL_COUNT_REPO = '''
REPLACE INTO dpkg_repo_stats
SELECT c1.repo repo, pkgcount, ghost, lagging, missing, coalesce(olddebcnt, 0)
FROM (
SELECT
  dpkg_repos.name repo, dpkg_repos.realname reponame,
  dpkg_repos.testing testing, dpkg_repos.category category,
  count(packages.name) pkgcount,
  (CASE WHEN count(packages.name)
   THEN sum(CASE WHEN packages.name IS NULL THEN 1 ELSE 0 END)
   ELSE 0 END) ghost
FROM dpkg_repos
LEFT JOIN (
    SELECT DISTINCT dp.package, dp.repo, dr.realname reponame, dr.architecture
    FROM dpkg_packages dp
    LEFT JOIN dpkg_repos dr ON dr.name=dp.repo
  ) dpkg
  ON dpkg.repo = dpkg_repos.name
LEFT JOIN packages
  ON packages.name = dpkg.package
LEFT JOIN package_spec spabhost
  ON spabhost.package = packages.name AND spabhost.key = 'ABHOST'
WHERE packages.name IS NULL
OR ((spabhost.value IS 'noarch') = (dpkg.architecture IS 'noarch'))
GROUP BY dpkg_repos.name
) c1
LEFT JOIN (
SELECT
  dpkg.repo repo, dpkg.reponame reponame,
  sum(pkgver.fullver > dpkg.version COLLATE vercomp) lagging
FROM packages
INNER JOIN (
    SELECT
      package, branch,
      ((CASE WHEN ifnull(epoch, '') = '' THEN '' ELSE epoch || ':' END) ||
       version || (CASE WHEN ifnull(release, '') IN ('', '0') THEN '' ELSE '-'
       || release END)) fullver
    FROM package_versions
  ) pkgver
  ON pkgver.package = packages.name
INNER JOIN trees ON trees.name = packages.tree
LEFT JOIN package_spec spabhost
  ON spabhost.package = packages.name AND spabhost.key = 'ABHOST'
LEFT JOIN (
    SELECT
      dp_d.name package, dr.name repo, dr.realname reponame,
      max(dp.version COLLATE vercomp) version, dr.category category,
      dr.architecture architecture, dr.suite branch
    FROM packages dp_d
    INNER JOIN dpkg_repos dr
    LEFT JOIN dpkg_packages dp ON dp.package=dp_d.name AND dp.repo=dr.name
    GROUP BY dp_d.name, dr.name
  ) dpkg ON dpkg.package = packages.name
WHERE pkgver.branch = dpkg.branch
  AND ((spabhost.value IS 'noarch') = (dpkg.architecture IS 'noarch'))
  AND dpkg.repo IS NOT null
  AND (dpkg.version IS NOT null OR (dpkg.category='bsp') = (trees.category='bsp'))
GROUP BY dpkg.repo
) c2 ON c2.repo=c1.repo
LEFT JOIN (
SELECT reponame, sum(CASE WHEN dpp IS NULL THEN 1 ELSE 0 END) missing
FROM (
  SELECT
    packages.name package, dr.realname reponame, dr.category category,
    max(dp.package) dpp
  FROM packages
  INNER JOIN dpkg_repos dr
  INNER JOIN trees ON trees.name = packages.tree
  INNER JOIN package_versions pv
    ON pv.package=packages.name AND pv.branch=trees.mainbranch
    AND pv.version IS NOT NULL
  LEFT JOIN package_spec spabhost
    ON spabhost.package = packages.name AND spabhost.key = 'ABHOST'
  LEFT JOIN dpkg_packages dp ON dp.package=packages.name AND dp.repo=dr.name
  WHERE ((spabhost.value IS 'noarch') = (dr.architecture IS 'noarch'))
  AND dr.category != 'overlay'
  AND (dp.package IS NOT null OR (dr.category='bsp') = (trees.category='bsp'))
  GROUP BY packages.name, dr.realname
)
GROUP BY reponame
) c3 ON c3.reponame=c1.reponame AND c1.testing=0
LEFT JOIN (
  SELECT repo, count(repo) olddebcnt
  FROM (
    SELECT dp.repo
    FROM dpkg_packages dp
    INNER JOIN dpkg_repos dr ON dr.name=dp.repo
    LEFT JOIN (
      SELECT package, max(version COLLATE vercomp) version, architecture, repo
      FROM dpkg_packages
      GROUP BY package, architecture, repo
    ) dpnew USING (package, version, architecture, repo)
    LEFT JOIN packages ON packages.name = dp.package
    LEFT JOIN package_spec spabhost
      ON spabhost.package = dp.package AND spabhost.key = 'ABHOST'
    LEFT JOIN (
      SELECT dp.package, (dr.architecture = 'noarch') noarch,
        max(dp.version COLLATE vercomp) version
      FROM dpkg_packages dp
      INNER JOIN dpkg_repos dr ON dr.name=dp.repo
      GROUP BY dp.package, (dr.architecture = 'noarch')
    ) dparch ON dparch.package=dp.package
    AND (dr.architecture != 'noarch') = dparch.noarch
    AND dparch.version=dpnew.version
    WHERE (dpnew.package IS NULL OR packages.name IS NULL
    OR ((dr.architecture = 'noarch') = (spabhost.value IS NOT 'noarch')
      AND dparch.package IS NULL))
    UNION ALL
    SELECT repo FROM dpkg_package_duplicate
  ) q1
  GROUP BY repo
) c4 ON c4.repo=c1.repo
ORDER BY c1.category, c1.reponame, c1.testing
'''

def stats_update(db):
    db.execute(SQL_COUNT_REPO)
    db.commit()

def update(db, mirror, branches=None, arch=None, local=False, force=False):
    branches = frozenset(branches if branches else ())
    for suite, repos in REPOS.items():
        if branches and suite not in branches:
            continue
        pkgrepos = suite_update(db, mirror, suite, repos, local, force)
        for repo, path, size, sha256 in pkgrepos.values():
            if arch and repo.architecture != arch:
                continue
            package_update(db, mirror, repo, path, size, sha256, local)
    stats_update(db)

def update_sources_list(db, filename, branches=None, arch=None, local=False, force=False):
    with open(filename, 'r', encoding='utf-8') as f:
        for ln in f:
            if ln[0] == '#':
                continue
            hashpos = ln.find('#')
            if hashpos != -1:
                ln = ln[:hashpos]
            fields = ln.strip().split()
            if not fields:
                continue
            elif fields[0] != 'deb':
                continue
            elif branches and fields[2] not in branches:
                continue
            mirror = _url_slash(fields[1])
            pkgrepos = suite_update(db, mirror, fields[2], None, local, force)
            for repo, path, size, sha256 in pkgrepos.values():
                if arch and repo.architecture != arch:
                    continue
                package_update(db, mirror, repo, path, size, sha256, local)
    stats_update(db)

def main(argv):
    parser = argparse.ArgumentParser(description="Get package info from DPKG sources.")
    parser.add_argument("-l", "--local", help="Try local apt cache", action='store_true')
    parser.add_argument("-f", "--force", help="Force update", action='store_true')
    parser.add_argument("-b", "--branch", help="Only get this branch, can be specified multiple times", action='append')
    parser.add_argument("-a", "--arch", help="Only get this architecture")
    parser.add_argument("-m", "--mirror",
        help="Set mirror location, https is recommended. "
             "This overrides environment variable REPO_MIRROR. ",
        default=os.environ.get('REPO_MIRROR', 'https://repo.aosc.io/debs')
    )
    parser.add_argument("-s", "--sources-list",
        help="Use specified sources.list file as repo list."
    )
    parser.add_argument("dbfile", help="abbs database file")
    args = parser.parse_args(argv)

    db = sqlite3.connect(args.dbfile)
    try:
        db.enable_load_extension(True)
        extpath = os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'mod_vercomp.so'))
        db.load_extension(extpath)
    except sqlite3.OperationalError:
        from utils import version_compare
        db.create_collation("vercomp", version_compare)
    db.enable_load_extension(False)
    init_db(db)
    if args.sources_list:
        update_sources_list(
            db, args.sources_list, args.branch, args.arch, args.local, args.force)
    else:
        update(
            db, _url_slash(args.mirror),
            args.branch, args.arch, args.local, args.force
        )
    db.execute('PRAGMA optimize')
    db.commit()

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv[1:]))
