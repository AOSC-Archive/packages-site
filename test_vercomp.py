#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import unittest

class TestVercomp(unittest.TestCase):

    op_list = frozenset(('<<', '<=', '=', '>=', '>>'))

    op_same = {
        '>': ('>>', '>='),
        '<': ('<<', '<='),
        '==': ('=', '>=', '<='),
    }

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.enable_load_extension(True)
        self.db.execute("SELECT load_extension(?)", ('./mod_vercomp.so',))
        self.db.enable_load_extension(False)

    def _test_comparison_coll(self, v1, cmp_oper, v2):
        res = self.db.execute(
            'SELECT (? %s ? COLLATE vercomp)' % cmp_oper, (v1, v2)).fetchone()[0]
        self.assertTrue(res == 1, "(%r %s %r) == %s" % (v1, cmp_oper, v2, res))

    def _test_comparison_func(self, v1, cmp_oper, v2, result=1):
        res = self.db.execute(
            'SELECT compare_dpkgrel(?, ?, ?)', (v1, cmp_oper, v2)).fetchone()[0]
        self.assertTrue(
            res == result, "(%r %s %r) == %s" % (v1, cmp_oper, v2, res))

    def _test_comparison(self, v1, cmp_oper, v2):
        self._test_comparison_coll(v1, cmp_oper, v2)
        func_opers = self.op_same.get(cmp_oper, (cmp_oper,))
        for oper in func_opers:
            self._test_comparison_func(v1, oper, v2)
        for oper in self.op_list.difference(frozenset(func_opers)):
            self._test_comparison_func(v1, oper, v2, 0)

    def test_comparisons(self):
        """Test comparison against all combinations of Version classes"""

        self._test_comparison('0', '<', 'a')
        self._test_comparison('1.0', '<', '1.1')
        self._test_comparison('1.2', '<', '1.11')
        self._test_comparison('1.0-0.1', '<', '1.1')
        self._test_comparison('1.0-0.1', '<', '1.0-1')
        # make them different for sorting
        self._test_comparison('1:1.0-0', '>', '1:1.0')
        self._test_comparison('1.0', '==', '1.0')
        self._test_comparison('1.0-0.1', '==', '1.0-0.1')
        self._test_comparison('1:1.0-0.1', '==', '1:1.0-0.1')
        self._test_comparison('1:1.0', '==', '1:1.0')
        self._test_comparison('1.0-0.1', '<', '1.0-1')
        self._test_comparison('1.0final-5sarge1', '>', '1.0final-5')
        self._test_comparison('1.0final-5', '>', '1.0a7-2')
        self._test_comparison('0.9.2-5', '<',
                              '0.9.2+cvs.1.0.dev.2004.07.28-1.5')
        self._test_comparison('1:500', '<', '1:5000')
        self._test_comparison('100:500', '>', '11:5000')
        self._test_comparison('1.0.4-2', '>', '1.0pre7-2')
        self._test_comparison('1.5~rc1', '<', '1.5')
        self._test_comparison('1.5~rc1', '<', '1.5+b1')
        self._test_comparison('1.5~rc1', '<', '1.5~rc2')
        self._test_comparison('1.5~rc1', '>', '1.5~dev0')

        self._test_comparison_func('1.5~rc1', '>', None, None)
        self._test_comparison_func('1.5~rc1', None, None, 1)
        self._test_comparison_func(None, None, None, None)
        self._test_comparison_func(None, '=', None, None)
        self._test_comparison_func(None, '=', '1', None)
        self._test_comparison_func(1, '<', 2, 1)

    def _test_dpkg_version(self, version, release, epoch, result):
        res = self.db.execute(
            'SELECT dpkg_version(?, ?, ?)',
            (version, release, epoch)).fetchone()[0]
        self.assertEqual(res, result)

    def test_dpkg_version(self):
        self._test_dpkg_version(None, None, None, None);
        self._test_dpkg_version('1.2.3', None, None, '1.2.3')
        self._test_dpkg_version('1.2.3', '', None, '1.2.3')
        self._test_dpkg_version('1.2.3', '0', None, '1.2.3')
        self._test_dpkg_version('1.2.3', '1', None, '1.2.3-1')
        self._test_dpkg_version('1.2.3', None, '', '1.2.3')
        self._test_dpkg_version('1.2.3', None, '2', '2:1.2.3');
        self._test_dpkg_version('1.2.3', '0', '2', '2:1.2.3');
        self._test_dpkg_version('1.2.3', '3', None, '1.2.3-3')
        self._test_dpkg_version('1.2.3', '3', '', '1.2.3-3');
        self._test_dpkg_version('1.2.3', '3', '1', '1:1.2.3-3');
        self._test_dpkg_version('1.2.3', '3', '222', '222:1.2.3-3');
        self._test_dpkg_version('1.2.3', '3', 222, '222:1.2.3-3');
        self._test_dpkg_version(1, 2, 3, '3:1-2');

if __name__ == '__main__':
    unittest.main()
