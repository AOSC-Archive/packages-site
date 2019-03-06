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
import collections
import urllib.parse
from email.utils import parsedate

import requests

import deb822
from utils import version_compare

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

# we don't have gpg, must be https
MIRROR = os.environ.get('REPO_MIRROR', 'https://repo.aosc.io/')
REPOPATH = 'debs'

ARCHS = ('amd64', 'arm64', 'armel', 'powerpc', 'ppc64', 'riscv64', 'noarch')
BRANCHES = ('stable', 'testing', 'explosive')
OVERLAYS = (
    # dpkg_repos.category, component, source, arch
    ('base', 'main', None, ARCHS),
    ('bsp', 'bsp-sunxi', 'aosc-os-arm-bsps', ('armel', 'arm64', 'noarch')),
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


def init_db(db):
    cur = db.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_repos ('
                'name TEXT PRIMARY KEY,' # key: bsp-sunxi-armel/testing
                'realname TEXT,'    # group key: amd64, bsp-sunxi-armel
                'path TEXT,'
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
                'PRIMARY KEY (package, version, architecture, repo, relationship),'
                'FOREIGN KEY(package) REFERENCES dpkg_packages(package)'
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

def suite_update(db, suite, repos):
    cur = db.cursor()
    url = urllib.parse.urljoin(MIRROR, '/'.join((
        REPOPATH, 'dists', suite, 'InRelease')))
    req = requests.get(url, timeout=120)
    if req.status_code == 404:
        logging.error('dpkg suite %s not found' % suite)
        for repo in repos:
            cur.execute('UPDATE dpkg_repos SET origin=null, label=null, '
                'codename=null, date=null, valid_until=null, description=null '
                'WHERE name=?', (repo.name,))
            cur.execute('DELETE FROM dpkg_package_dependencies WHERE repo=?',
                (repo.name,))
            cur.execute('DELETE FROM dpkg_package_duplicate WHERE repo=?',
                (repo.name,))
            cur.execute('DELETE FROM dpkg_packages WHERE repo=?', (repo.name,))
        return {}
    else:
        req.raise_for_status()
    releasetxt = remove_clearsign(req.content).decode('utf-8')
    rel = deb822.Release(releasetxt)
    pkgrepos = {}
    for item in rel['SHA256']:
        path = item['name'].split('/')
        if path[-1] == 'Packages.xz':
            arch = path[1].split('-')[-1]
            if arch == 'all':
                arch = 'noarch'
            pkgrepos[path[0], arch] = (
                item['name'], int(item['size']), item['sha256'])
    rel_date = calendar.timegm(parsedate(rel['Date'])) if 'Date' in rel else None
    rel_valid = calendar.timegm(parsedate(rel['Valid-Until'])) if 'Valid-Until' in rel else None
    pkgrepos2 = {}
    for repo in repos:
        pkgrepo = pkgrepos.get((repo.component, repo.architecture))
        if pkgrepo:
            res = cur.execute('SELECT date FROM dpkg_repos WHERE name=?',
                              (repo.name,)).fetchone()
            if res and rel_date:
                if res[0] >= rel_date:
                    continue
            pkgpath = '/'.join((REPOPATH, 'dists', suite, pkgrepo[0]))
            pkgrepos2[repo.component, repo.architecture] = (repo, pkgpath) + pkgrepo[1:]
            cur.execute('REPLACE INTO dpkg_repos VALUES '
                '(?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?)', (
                repo.name, repo.realname, REPOPATH,
                repo.source_tree, repo.category, repo.testing, repo.suite,
                repo.component, repo.architecture, rel.get('Origin'),
                rel.get('Label'), rel.get('Codename'), rel_date, rel_valid,
                rel.get('Description')
            ))
        else:
            cur.execute('UPDATE dpkg_repos SET origin=null, label=null, '
                'codename=null, date=null, valid_until=null, description=null '
                'WHERE name=?', (repo.name,))
            cur.execute('DELETE FROM dpkg_package_dependencies WHERE repo=?',
                (repo.name,))
            cur.execute('DELETE FROM dpkg_package_duplicate WHERE repo=?',
                (repo.name,))
            cur.execute('DELETE FROM dpkg_packages WHERE repo=?', (repo.name,))
    db.commit()
    cur.close()
    return pkgrepos2

_relationship_fields = ('depends', 'pre-depends', 'recommends',
        'suggests', 'breaks', 'conflicts', 'provides', 'replaces',
        'enhances')

def package_update(db, repo, path, size, sha256):
    logging.info(repo.name)
    cur = db.cursor()
    url = urllib.parse.urljoin(MIRROR, path)
    req = requests.get(url, timeout=120)
    req.raise_for_status()
    assert len(req.content) == size
    assert hashlib.sha256(req.content).hexdigest() == sha256
    pkgs = lzma.decompress(req.content).decode('utf-8')
    del req
    packages = {}
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
    db.commit()
    cur.close()

SQL_COUNT_REPO = '''
REPLACE INTO dpkg_repo_stats
SELECT c1.repo repo, pkgcount, ghost, lagging, missing, olddebcnt
FROM (
SELECT
  dpkg_repos.name repo, dpkg_repos.realname reponame,
  dpkg_repos.testing testing, dpkg_repos.category category,
  count(CASE WHEN ((spabhost.value IS 'noarch') = (dpkg.architecture IS 'noarch'))
    THEN packages.name ELSE NULL END) pkgcount,
  (CASE WHEN sum((
    (spabhost.value IS 'noarch') = (dpkg.architecture IS 'noarch'))
    AND packages.name IS NOT NULL)
   THEN sum(((spabhost.value IS 'noarch') = (dpkg.architecture IS 'noarch'))
    AND packages.name IS NULL)
   ELSE 0 END) ghost,
  sum(dpnew.package IS NULL OR packages.name IS NULL
    OR ((dpkg_repos.architecture = 'noarch') = (spabhost.value IS NOT 'noarch')
    AND dparch.package IS NULL)) + dpkg_dup.cnt olddebcnt
FROM dpkg_repos
LEFT JOIN dpkg_packages dp ON dpkg_repos.name=dp.repo
LEFT JOIN (
  SELECT package, max(version COLLATE vercomp) version, architecture, repo
  FROM dpkg_packages
  GROUP BY package, architecture, repo
) dpnew USING (package, version, architecture, repo)
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
LEFT JOIN (
  SELECT dp.package, (dr.architecture = 'noarch') noarch,
    max(dp.version COLLATE vercomp) version
  FROM dpkg_packages dp
  INNER JOIN dpkg_repos dr ON dr.name=dp.repo
  GROUP BY dp.package, (dr.architecture = 'noarch')
) dparch ON dparch.package=dp.package
AND (dpkg_repos.architecture != 'noarch') = dparch.noarch
AND dparch.version=dpnew.version
LEFT JOIN (
  SELECT repo, count(*) cnt FROM dpkg_package_duplicate GROUP BY repo
) dpkg_dup
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
ORDER BY c1.category, c1.reponame, c1.testing
'''

def stats_update(db):
    db.execute(SQL_COUNT_REPO)

def update(db):
    for suite, repos in REPOS.items():
        pkgrepos = suite_update(db, suite, repos)
        for repo, path, size, sha256 in pkgrepos.values():
            package_update(db, repo, path, size, sha256)
    stats_update(db)

def main(dbfile):
    db = sqlite3.connect(dbfile)
    db.create_collation("vercomp", version_compare)
    init_db(db)
    update(db)
    cur = db.cursor()
    cur.execute('PRAGMA optimize')
    db.commit()

if __name__ == '__main__':
    import sys
    sys.exit(main(*sys.argv[1:]))
