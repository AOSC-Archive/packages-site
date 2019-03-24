#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import math
import time
import weakref
import itertools
import functools
import collections
import collections.abc
from debian_support import version_compare as _version_compare

cmp = lambda a, b: ((a > b) - (a < b))

@functools.lru_cache(maxsize=1024)
def version_compare(a, b):
    try:
        return _version_compare(a, b) or cmp(a, b)
    except ValueError:
        return cmp(a, b)

version_compare_key = functools.cmp_to_key(version_compare)

def sizeof_fmt(num, suffix='B'):
    for unit in ('','Ki','Mi','Gi','Ti','Pi','Ei','Zi'):
        if abs(num) < 1024:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)

def sizeof_fmt_ls(num):
    if abs(num) < 1024:
        return str(num)
    num /= 1024.0
    for unit in 'KMGTPEZ':
        if abs(num) < 10:
            return "%3.1f%s" % (num, unit)
        elif abs(num) < 1024:
            return "%3.0f%s" % (num, unit)
        num /= 1024.0
    return "%.1fY" % num

def strftime(t=None, fmt='%Y-%m-%d %H:%M:%S'):
    return time.strftime(fmt, time.gmtime(t))

def ls_perm(x, ftype='reg'):
    return {'reg': '-', 'lnk': 'l', 'sock': 's', 'chr': 'c', 'blk': 'b', 
        'dir': 'd', 'fifo': 'p'}.get(ftype, '-') + ''.join(
        (b if a=='1' else '-')
        for a, b in zip(bin(x)[2:].zfill(9), 'rwxrwxrwx'))

re_test = re.compile(r'^([@!])\((.+)\)$')
TestList = collections.namedtuple('TestList', 'op plist')

def parse_fail_arch(s):
    if not s:
        return TestList(None, ())
    match = re_test.match(s)
    if match:
        return TestList(match.group(1), match.group(2).split('|'))
    else:
        return TestList('@', [s])

def iter_read1(fd):
    while True:
        res = fd.read1()
        if res:
            yield res
        else:
            return

def remember(ttl):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            if now - wrapper.last_updated > ttl:
                wrapper.last_updated = now
                wrapper.cached_value = fn(*args, **kwargs)
            return wrapper.cached_value
        wrapper.last_updated = float('-inf')
        wrapper.cached_value = None
        return wrapper
    return deco

def groupby_val(iterable, key=None, resultkey=None, resultcmpkey=None):
    keys = []
    values = []
    uniqdict = {}
    resid = 0
    for groupkey, rgroup in itertools.groupby(iterable, key=key):
        result = tuple(map(resultkey, rgroup))
        result_hash = tuple(map(resultcmpkey, result))
        result_id = uniqdict.get(result_hash)
        if result_id is None:
            keys.append([groupkey])
            values.append(result)
            uniqdict[result_hash] = resid
            resid += 1
        else:
            keys[result_id].append(groupkey)
    del uniqdict
    return keys, values


class CircularDependencyError(ValueError):
    def __init__(self, data):
        s = 'Circular dependencies exist among these items: {%s}' % (
            ', '.join('{!r}:{!r}'.format(key, value)
            for key, value in sorted(data.items())))
        super(CircularDependencyError, self).__init__(s)
        self.data = data

def toposort(data):
    if not data:
        return
    # normalize data
    #data = {k: data.get(k, set()).difference(set((k,))) for k in
        #functools.reduce(set.union, data.values(), set(data.keys()))}
    # we don't need this
    data = data.copy()
    while True:
        ordered = set(item for item, dep in data.items() if not dep)
        if not ordered:
            break
        yield sorted(ordered)
        data = {item: (dep - ordered) for item, dep in data.items()
                if item not in ordered}
    if data:
        raise CircularDependencyError(data)


class FileRemover(object):
    def __init__(self):
        self.weak_references = dict()  # weak_ref -> filepath to remove

    def cleanup_once_done(self, response, filepath):
        wr = weakref.ref(response, self._do_cleanup)
        self.weak_references[wr] = filepath

    def _do_cleanup(self, wr):
        filepath = self.weak_references[wr]
        # shutil.rmtree(filepath, ignore_errors=True)
        os.unlink(filepath)

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
        self._pagecount = math.ceil((self.index+1)/self.pagesize)
        return self._pagecount
