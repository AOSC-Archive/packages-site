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

import lzma
import sqlite3
import hashlib
import logging
import calendar
import urllib.parse
from email.utils import parsedate

import requests

import deb822

logging.basicConfig(
    format='%(asctime)s %(levelname).1s %(message)s', level=logging.INFO)

# we don't have gpg, must be https
# MIRROR = 'https://repo.aosc.io/'
MIRROR = 'http://127.0.0.1/tuna/anthon/'
REPOS = (
    ('os-amd64', 'os-amd64/os3-dpkg'),
    ('os-arm64', 'os-arm64/os3-dpkg'),
    ('os-arm64-sunxi', 'os-arm64/sunxi/os3-dpkg'),
    ('os-armel', 'os-armel/os3-dpkg'),
    ('os-rpi2', 'os-armel/rpi2/os3-dpkg'),
    ('os-standard-bsp', 'os-armel/standard-bsp/os3-dpkg'),
    ('os-armel-sunxi', 'os-armel/sunxi/os3-dpkg'),
    ('os-mips64el', 'os-mips64el/os3-dpkg'),
    ('os-mipsel', 'os-mipsel/os3-dpkg'),
    ('os-noarch', 'os-noarch/os3-dpkg'),
    ('os-powerpc', 'os-powerpc/os3-dpkg'),
    ('os-ppc64', 'os-ppc64/os3-dpkg')
)

def init_db(cur):
    cur.execute('CREATE TABLE IF NOT EXISTS dpkg_repos ('
                'name TEXT PRIMARY KEY,'
                'path TEXT,'
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
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dpkg_packages'
                ' ON dpkg_packages (package)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dpkg_package_dependencies'
                ' ON dpkg_package_dependencies (package)')

def release_update(cur, repo, path):
    url = urllib.parse.urljoin(MIRROR, path.rstrip('/') + '/Release')
    req = requests.get(url, timeout=120)
    req.raise_for_status()
    rel = deb822.Release(req.text)
    cur.execute('REPLACE INTO dpkg_repos VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
        repo, path, rel.get('Origin'), rel.get('Label'),
        rel.get('Suite'), rel.get('Codename'),
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

def package_update(cur, repo, path, size, sha256):
    url = urllib.parse.urljoin(MIRROR, path.rstrip('/') + '/Packages.xz')
    req = requests.get(url, timeout=120)
    req.raise_for_status()
    assert len(req.content) == size
    assert hashlib.sha256(req.content).hexdigest() == sha256
    pkgs = lzma.decompress(req.content).decode('utf-8')
    del req
    packages = set()
    for pkg in deb822.Packages.iter_paragraphs(pkgs):
        name = pkg['Package']
        arch = pkg['Architecture']
        ver = pkg['Version']
        pkgtuple = (name, ver, arch, repo)
        if pkgtuple in packages:
            logging.warning('duplicate package: %r', pkgtuple)
            continue
        else:
            packages.add(pkgtuple)
        cur.execute('REPLACE INTO dpkg_packages VALUES (?,?,?,?,?,?,?,?,?)', (
            name, ver, arch, repo, pkg.get('Maintainer'),
            int(pkg['Installed-Size']) if 'Installed-Size' in pkg else None, 
            pkg['Filename'], int(pkg['Size']), pkg.get('SHA256')
        ))
        oldrels = frozenset(row[0] for row in cur.execute(
            'SELECT relationship FROM dpkg_package_dependencies'
            ' WHERE package = ? AND version = ? AND architecture = ? AND repo = ?',
            (name, ver, arch, repo)
        ))
        newrels = set()
        for rel in _relationship_fields:
            if rel in pkg:
                cur.execute(
                    'REPLACE INTO dpkg_package_dependencies VALUES (?,?,?,?,?,?)',
                    (name, ver, arch, repo, rel, pkg[rel])
                )
                newrels.add(rel)
        for rel in oldrels.difference(newrels):
            cur.execute(
                'DELETE FROM dpkg_package_dependencies'
                ' WHERE package = ? AND version = ? AND architecture = ?'
                ' AND repo = ? AND relationship = ?',
                (name, ver, arch, repo, rel)
            )
    packages_old = set(cur.execute(
        'SELECT package, version, architecture, repo FROM dpkg_packages'
        ' WHERE repo = ?', (repo,)
    ))
    for pkg in packages_old.difference(packages):
        cur.execute('DELETE FROM dpkg_packages WHERE package = ? AND version = ?'
                    ' AND architecture = ? AND repo = ?', pkg)
        cur.execute('DELETE FROM dpkg_package_dependencies WHERE package = ?'
                    ' AND version = ? AND architecture = ? AND repo = ?', pkg)

def update(cur):
    for repo, path in REPOS:
        logging.info(repo)
        size, sha256 = release_update(cur, repo, path)
        package_update(cur, repo, path, size, sha256)

def main(dbfile):
    db = sqlite3.connect(dbfile)
    cur = db.cursor()
    init_db(cur)
    update(cur)
    db.commit()

if __name__ == '__main__':
    import sys
    sys.exit(main(*sys.argv[1:]))
