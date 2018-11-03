#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
import requests

URLBASE = 'http://127.0.0.1:8082'

class TestReportNum(unittest.TestCase):

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

if __name__ == '__main__':
    unittest.main()
