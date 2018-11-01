#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
import requests

URLBASE = 'http://127.0.0.1:8082'

class TestReportNum(unittest.TestCase):

    def setUp(self):
        self.session = requests.Session()

    def test_listnumber(self):
        req = self.session.get(URLBASE + '/?type=json')
        req.raise_for_status()
        d = req.json()
        repo_nums = {}
        for _, cat in d['repo_categories']:
            for row in cat:
                testing = row['testing']
                repo_nums[row['name']] = (
                    row['pkgcount'], row['ghost'],
                    row['lagging'], None if testing else row['missing']
                )
        for rn, row in repo_nums.items():
            for (name, num) in zip(('repo', 'ghost', 'lagging', 'missing'), row):
                if num is None:
                    continue
                req = self.session.get('%s/%s/%s?type=json' % (URLBASE, name, rn))
                req.raise_for_status()
                d = req.json()
                if 'error' in d:
                    continue
                self.assertEqual(num, d['page']['count'],
                    "%s %s: stat %d, list %d" % (rn, name, num, d['page']['count']))

if __name__ == '__main__':
    unittest.main()
