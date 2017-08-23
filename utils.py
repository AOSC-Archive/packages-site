#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import functools
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
