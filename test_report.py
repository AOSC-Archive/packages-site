#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import sqlite3
import tempfile
import unittest
import requests

URLBASE = 'http://127.0.0.1:8082'

def download_file(url, localpath, filename=None):
    local_filename = filename or url.split('/')[-1]
    r = requests.get(url, stream=True)
    filepath = os.path.join(localpath, local_filename)
    with open(filepath, 'wb') as f:
        shutil.copyfileobj(r.raw, f)
    return local_filename

class TestWebsite(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None

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
        req = requests.get(URLBASE + '/list.json')
        req.raise_for_status()
        d_json = req.json()
        req = requests.get(URLBASE + '/?type=json')
        req.raise_for_status()
        d_index = req.json()
        self.assertEqual(len(d_json['packages']), d_index['total'])

    def test_pkgtrie(self):
        req = requests.get(URLBASE + '/pkgtrie.js')
        req.raise_for_status()
        self.assertTrue(req.text.startswith('var pkgTrie = {'))

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
                'aosc-os-abbs.fossil',
                'aosc-os-core.fossil',
                'aosc-os-arm-bsps.fossil',
            ):
                dbpath = download_file(URLBASE + '/data/' + name, tmpdir)
                db = sqlite3.connect(dbpath)
                result = db.execute('PRAGMA integrity_check;').fetchall()
                self.assertListEqual(result, [('ok',)])
                db.close()
                os.unlink(dbpath)

    def test_api_version(self):
        req = requests.get(URLBASE + '/api_version')
        req.raise_for_status()
        self.assertTrue('version' in req.json())

if __name__ == '__main__':
    unittest.main()
