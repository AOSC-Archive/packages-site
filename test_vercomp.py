#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import unittest

class TestVercomp(unittest.TestCase):

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.enable_load_extension(True)
        self.db.execute("SELECT load_extension(?)", ('./mod_vercomp.so',))
        self.db.enable_load_extension(False)

    def _test_comparison(self, v1, cmp_oper, v2):
        res = self.db.execute(
            'SELECT (? %s ? COLLATE vercomp)' % cmp_oper, (v1, v2)).fetchone()[0]
        self.assertTrue(res == 1, "(%r %s %r) == %s" % (v1, cmp_oper, v2, res))

    def test_comparisons(self):
        """Test comparison against all combinations of Version classes"""

        self._test_comparison('0', '<', 'a')
        self._test_comparison('1.0', '<', '1.1')
        self._test_comparison('1.2', '<', '1.11')
        self._test_comparison('1.0-0.1', '<', '1.1')
        self._test_comparison('1.0-0.1', '<', '1.0-1')
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


if __name__ == '__main__':
    unittest.main()
