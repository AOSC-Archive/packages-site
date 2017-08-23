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

Repo = collections.namedtuple('Repo', 'name path source_tree category testing')

# we don't have gpg, must be https
MIRROR = os.environ.get('REPO_MIRROR', 'https://repo.aosc.io/')
REPOS = (
    Repo('amd64', 'os-amd64/os3-dpkg', None, 'base', 0),
    Repo('amd64', 'os-amd64/testing/os-amd64/os3-dpkg', None, 'base', 1),
    Repo('arm64', 'os-arm64/os3-dpkg', None, 'base', 0),
    Repo('arm64', 'os-arm64/testing/os-arm64/os3-dpkg', None, 'base', 1),
    Repo('arm64-sunxi', 'os-arm64/sunxi/os3-dpkg', 'aosc-os-arm-bsps', 'bsp', 0),
    Repo('arm64-sunxi', 'os-arm64/sunxi/testing/os-arm64/os3-dpkg', 'aosc-os-arm-bsps', 'bsp', 1),
    Repo('armel', 'os-armel/os3-dpkg', None, 'base', 0),
    Repo('armel', 'os-armel/testing/os-armel/os3-dpkg', None, 'base', 1),
    Repo('armel-sunxi', 'os-armel/sunxi/os3-dpkg', 'aosc-os-arm-bsps', 'bsp', 0),
    Repo('armel-sunxi', 'os-armel/sunxi/testing/os-armel/os3-dpkg', 'aosc-os-arm-bsps', 'bsp', 1),
    Repo('mips64el', 'os-mips64el/os3-dpkg', None, 'base', 0),
    Repo('mips64el', 'os-mips64el/testing/os-mips64el/os3-dpkg', None, 'base', 1),
    Repo('mipsel', 'os-mipsel/os3-dpkg', None, 'base', 0),
    Repo('mipsel', 'os-mipsel/testing/os-mipsel/os3-dpkg', None, 'base', 1),
    Repo('noarch', 'os-noarch/os3-dpkg', None, 'base', 0),
    Repo('noarch', 'os-noarch/testing/os-noarch/os3-dpkg', None, 'base', 1),
    Repo('powerpc', 'os-powerpc/os3-dpkg', None, 'base', 0),
    Repo('powerpc', 'os-powerpc/testing/os-powerpc/os3-dpkg', None, 'base', 1),
    Repo('ppc64', 'os-ppc64/os3-dpkg', None, 'base', 0),
    Repo('ppc64', 'os-ppc64/testing/os-ppc64/os3-dpkg', None, 'base', 1)
)

def init_db(cur):
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_repos ('
                'name TEXT PRIMARY KEY,'
                'path TEXT,'
                'source_tree TEXT,' # abbs tree
                'category TEXT,' # base, bsp, overlay
                'testing INTEGER,' # 0, 1
                'origin TEXT,'
                'label TEXT,'
                'suite TEXT,'
                'codename TEXT,'
                'date INTEGER,'
                'valid_until INTEGER,'
                'architectures TEXT,'
                'components TEXT,'
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
                'PRIMARY KEY (package, version, architecture, repo),'
                'FOREIGN KEY(package) REFERENCES packages(name)'
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
                'name TEXT PRIMARY KEY,'
                'packagecnt INTEGER,'
                'missingcnt INTEGER,'
                'laggingcnt INTEGER,'
                'ghostcnt INTEGER,'
                'FOREIGN KEY(name) REFERENCES packages(name)'
                ')')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dpkg_packages'
                ' ON dpkg_packages (package, repo)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dpkg_package_dependencies'
                ' ON dpkg_package_dependencies (package)')

def release_update(cur, repo):
    url = urllib.parse.urljoin(MIRROR, repo.path.rstrip('/') + '/Release')
    req = requests.get(url, timeout=120)
    if req.status_code == 404:
        # testing not available
        logging.error('dpkg source %s not found' % repo.path)
        cur.execute('REPLACE INTO dpkg_repos VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?)',
            (repo.name, repo.path, repo.source_tree, repo.category, repo.testing,
            None, None, None, None, None, None, None, None, None))
        return 0, None
    else:
        req.raise_for_status()
    rel = deb822.Release(req.text)
    cur.execute('REPLACE INTO dpkg_repos VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?)', (
        repo.name, repo.path, repo.source_tree, repo.category, repo.testing,
        rel.get('Origin'), rel.get('Label'), rel.get('Suite'), rel.get('Codename'),
        calendar.timegm(parsedate(rel['Date'])) if 'Date' in rel else None,
        calendar.timegm(parsedate(rel['Valid-Until'])) if 'Valid-Until' in rel else None,
        rel.get('Architectures'), rel.get('Components'), rel.get('Description')
    ))
    for item in rel['SHA256']:
        if item['name'] == 'Packages.xz':
            return int(item['size']), item['sha256']

_relationship_fields = ('depends', 'pre-depends', 'recommends',
        'suggests', 'breaks', 'conflicts', 'provides', 'replaces',
        'enhances')

def package_update(cur, repo, size, sha256):
    url = urllib.parse.urljoin(MIRROR, repo.path.rstrip('/') + '/Packages.xz')
    req = requests.get(url, timeout=120)
    req.raise_for_status()
    assert len(req.content) == size
    assert hashlib.sha256(req.content).hexdigest() == sha256
    pkgs = lzma.decompress(req.content).decode('utf-8')
    del req
    packages = {}
    cur.execute('DELETE FROM dpkg_package_duplicate WHERE repo = ?', (repo.name,))
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
    packages_old = set(cur.execute(
        'SELECT package, version, architecture, repo FROM dpkg_packages'
        ' WHERE repo = ?', (repo.name,)
    ))
    for pkg in packages_old.difference(packages.keys()):
        cur.execute('DELETE FROM dpkg_packages WHERE package = ? AND version = ?'
                    ' AND architecture = ? AND repo = ?', pkg)
        cur.execute('DELETE FROM dpkg_package_dependencies WHERE package = ?'
                    ' AND version = ? AND architecture = ? AND repo = ?', pkg)

def update(cur):
    for repo in REPOS:
        logging.info(repo)
        size, sha256 = release_update(cur, repo)
        if sha256:
            package_update(cur, repo, size, sha256)

def main(dbfile):
    db = sqlite3.connect(dbfile)
    db.create_collation("vercomp", version_compare)
    cur = db.cursor()
    init_db(cur)
    update(cur)
    db.commit()

if __name__ == '__main__':
    import sys
    sys.exit(main(*sys.argv[1:]))
