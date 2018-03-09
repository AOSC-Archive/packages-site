#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import sys
import pickle
import sqlite3
from utils import version_compare

SQLITE_FUNCTION = 31
MAX_ROW = 10000

def sql_auth(sqltype, arg1, arg2, dbname, source):
    if sqltype in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    else:
        return sqlite3.SQLITE_DENY

result = {'rows': []}

try:
    conn = sqlite3.connect(sys.argv[1])
    conn.create_collation("vercomp", version_compare)
    conn.set_authorizer(sql_auth)
    cur = conn.cursor()
    cur.execute(sys.stdin.read())
    if cur.description:
        result['header'] = tuple(x[0] for x in cur.description)
    for i, row in enumerate(cur):
        result['rows'].append(tuple(row))
        if i > MAX_ROW:
            result['error'] = 'only showing the first %d rows' % MAX_ROW
            break
    conn.close()
except sqlite3.DatabaseError as ex:
    result['error'] = str(ex)

pickle.dump(result, sys.stdout.buffer, pickle.HIGHEST_PROTOCOL)
