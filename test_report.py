#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import sqlite3
import tempfile
import unittest
import subprocess

import requests

URLBASE = 'http://127.0.0.1:8082'

def download_file(url, localpath, filename=None, gzip=True):
    local_filename = filename or url.split('/')[-1]
    if gzip:
        r = requests.get(url)
    else:
        r = requests.get(url, headers={'Accept-Encoding': None}, stream=True)
    r.raise_for_status()
    filepath = os.path.join(localpath, local_filename)
    with open(filepath, 'wb') as f:
        if gzip:
            f.write(r.content)
        else:
            shutil.copyfileobj(r.raw, f)
    etag = r.headers.get('ETag')
    assert os.stat(filepath).st_size == int(r.headers['Content-Length'])
    r.close()
    return local_filename, etag

class TestWebsite(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None
        self.dbhash = shutil.which('dbhash') or shutil.which('./dbhash')

    def test_listnumber(self):
        req = requests.get(URLBASE + '/?type=json')
        req.raise_for_status()
        d = req.json()
        repo_nums = {}
        tree_nums = {}
        for _, cat in d['repo_categories']:
            for row in cat:
                repo_nums[row['name']] = (
                    row['pkgcount'], row['ghost'], row['lagging'],
                    (None if row['testing'] or row['category'] == 'overlay'
                     else row['missing'])
                )
        for row in d['source_trees']:
            tree_nums[row['name']] = (row['pkgcount'], row['srcupd'])
        fails = []
        for rn, row in repo_nums.items():
            for (name, num) in zip(('repo', 'ghost', 'lagging', 'missing'), row):
                if num is None:
                    continue
                req = requests.get('%s/%s/%s?type=json' % (URLBASE, name, rn))
                req.raise_for_status()
                d = req.json()
                if 'error' in d:
                    realnum = 0
                else:
                    realnum = d['page']['count']
                if num != realnum:
                    fails.append((rn, name, num, realnum))
        for rn, row in tree_nums.items():
            for (name, num) in zip(('tree', 'srcupd'), row):
                req = requests.get('%s/%s/%s?type=json' % (URLBASE, name, rn))
                req.raise_for_status()
                d = req.json()
                if 'error' in d:
                    realnum = 0
                else:
                    realnum = d['page']['count']
                if num != realnum:
                    fails.append((rn, name, num, realnum))
        self.assertListEqual([], fails, msg='rn, name, stat, list')
        req = requests.get(URLBASE + '/updates?type=json')
        req.raise_for_status()
        d = req.json()
        self.assertEqual(len(d['packages']), 100)

    def test_listjson(self):
        url = URLBASE + '/list.json'
        req = requests.get(url)
        req.raise_for_status()
        lm = req.headers['Last-Modified']
        d_json = req.json()
        req.close()
        req = requests.get(url, headers={"If-Modified-Since": lm})
        self.assertEqual(req.status_code, 304)
        self.assertEqual(req.content, b'')
        req.close()
        req = requests.get(URLBASE + '/?type=json')
        req.raise_for_status()
        d_index = req.json()
        req.close()
        self.assertEqual(len(d_json['packages']), d_index['total'])

    def test_pkgtrie(self):
        url = URLBASE + '/pkgtrie.js'
        req = requests.get(url)
        req.raise_for_status()
        lm = req.headers['Last-Modified']
        self.assertTrue(req.text.startswith('var pkgTrie = {'))
        req.close()
        req = requests.get(url, headers={"If-Modified-Since": lm})
        self.assertEqual(req.status_code, 304)
        self.assertEqual(req.content, b'')
        req.close()

    def test_static(self):
        for filename in ('aosc.png', 'style.css', 'autocomplete.js'):
            req = requests.get(URLBASE + '/static/' + filename)
            req.raise_for_status()
            self.assertEqual(req.status_code, 200)

    def test_search(self):
        req = requests.get(URLBASE + '/search/?q=GLIBC%20')
        req.raise_for_status()
        self.assertTrue(req.history)
        self.assertTrue(req.url.endswith('/packages/glibc'))
        req = requests.get(URLBASE + '/search/?q=glibc&noredir=1')
        req.raise_for_status()
        self.assertFalse(req.history)
        req = requests.get(URLBASE + '/search/?q=glibc&type=json')
        req.raise_for_status()
        self.assertFalse(req.history)

    def test_query(self):
        req = requests.get(URLBASE + '/query/')
        req.raise_for_status()
        for query, result in (
            ("select * from sqlite_master", True),
            ("select ('1.10' < '1.2' COLLATE vercomp)", True),
            ("aaa", False),
            ("drop table trees", False),
            ("delete from trees", False),
            ("update trees set name='a'", False),
            ("select * from package_versions", False),
            ("select count(*) from package_versions", True),
            ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<5) SELECT x FROM c;", False),
            ("select 1;select 2;", False),
        ):
            req = requests.post(URLBASE + '/query/?type=json',
                                data={'q': query})
            req.raise_for_status()
            self.assertEqual(req.status_code, 200)
            d = req.json()
            self.assertEqual(not d.get('error'), result, (query, d.get('error')))
            self.assertLessEqual(len(d['rows']), 10000)

    def test_changelog(self):
        req = requests.get(URLBASE + '/changelog/glibc')
        self.assertEqual(req.status_code, 200)
        self.assertEqual(req.headers['content-type'].lower(), 'text/plain; charset=utf-8')
        req = requests.get(URLBASE + '/changelog/a')
        self.assertEqual(req.status_code, 404)
        self.assertEqual(req.headers['content-type'].lower(), 'text/plain; charset=utf-8')

    def test_cleanmirror(self):
        req = requests.get(URLBASE + '/?type=json')
        req.raise_for_status()
        dindex = req.json()
        repos = []
        for _, cat in dindex['repo_categories']:
            for row in cat:
                repos.append(row['name'])
        for repo in repos:
            req = requests.get(URLBASE + '/cleanmirror/' + repo)
            self.assertEqual(req.status_code, 200)
            self.assertEqual(req.headers['content-type'].lower(), 'text/plain; charset=utf-8')

    def test_dbdownload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in (
                'abbs.db', 'piss.db',
                'aosc-os-abbs-marks.db',
                'aosc-os-core-marks.db',
                'aosc-os-arm-bsps-marks.db',
            ):
                url = URLBASE + '/data/' + name
                for usegz in (True, False):
                    dbfn, etag = download_file(url, tmpdir, gzip=usegz)
                    dbpath = os.path.join(tmpdir, dbfn)
                    self.assertTrue(os.path.isfile(dbpath))
                    ret = subprocess.check_output((self.dbhash, dbpath))
                    dbhash = ret.decode('utf-8').split(' ', 1)[0]
                    self.assertEqual(dbhash, etag.strip('"'))
                    db = sqlite3.connect(dbpath)
                    result = db.execute('PRAGMA integrity_check;').fetchall()
                    self.assertListEqual(result, [('ok',)])
                    db.close()
                    os.unlink(dbpath)
                req = requests.get(url, headers={'If-None-Match': etag})
                self.assertEqual(req.status_code, 304)
                self.assertEqual(req.content, b'')
                req.close()
            for name in (
                'aosc-os-abbs.fossil',
                'aosc-os-core.fossil',
                'aosc-os-arm-bsps.fossil',
            ):
                req = requests.get(URLBASE + '/data/' + name)
                self.assertEqual(req.status_code, 404)
                req.close()

    def test_api_version(self):
        req = requests.get(URLBASE + '/api_version')
        req.raise_for_status()
        self.assertTrue('version' in req.json())

if __name__ == '__main__':
    unittest.main()
