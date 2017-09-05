#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import functools
import collections
from debian_support import version_compare as _version_compare

cmp = lambda a, b: ((a > b) - (a < b))
version_compare = functools.lru_cache(maxsize=1024)(
    lambda a, b: _version_compare(a, b) or cmp(a, b)
)
version_compare_key = functools.cmp_to_key(version_compare)

def sizeof_fmt(num, suffix='B'):
    for unit in ['','Ki','Mi','Gi','Ti','Pi','Ei','Zi']:
        if abs(num) < 1024:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)

def strftime(t=None, fmt='%Y-%m-%d %H:%M:%S'):
    return time.strftime(fmt, time.gmtime(t))

class Pager(collections.abc.Iterable):
    def __init__(self, iterable, pagesize, page=1):
        '''Page number starts from 1.'''
        self.iterator = iter(iterable)
        self.pagesize = pagesize
        self.page = page
        self.index = -1
        self._pagecount = None

    def __iter__(self):
        start = (self.page-1) * self.pagesize
        stop = self.page * self.pagesize
        it = iter(range(start, stop))
        try:
            nexti = next(it)
        except StopIteration:
            return
        for self.index, element in enumerate(self.iterator, self.index+1):
            if self.index == nexti:
                yield element
                try:
                    nexti = next(it)
                except StopIteration:
                    return

    def count(self):
        return self.index + 1

    def pagecount(self):
        if self._pagecount:
            return self._pagecount
        for self.index, element in enumerate(self.iterator, self.index+1):
            pass
        self._pagecount = (self.index+1)//self.pagesize + 1
        return self._pagecount
