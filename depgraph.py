#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import sqlite3
import argparse
import itertools
import collections

import toposort
import graphviz

SQL_GET_PACKAGE_REL = '''
SELECT dependency, version, relationship
FROM package_dependencies
WHERE
  package = ? AND (relationship == 'PKGDEP' OR
  relationship == 'BUILDDEP' OR relationship == 'PKGRECOM')
ORDER BY relationship, dependency
'''

SQL_GET_PACKAGE_REV_REL = '''
SELECT package, version, relationship
FROM package_dependencies
WHERE
  dependency = ? AND (relationship == 'PKGDEP' OR
  relationship == 'BUILDDEP' OR relationship == 'PKGRECOM')
ORDER BY relationship, package
'''

class DependencyProperties:
    __slots__ = ('PKGDEP', 'PKGRECOM', 'BUILDDEP', 'depth', 'version')
    def __init__(self):
        self.PKGDEP = False
        self.PKGRECOM = False
        self.BUILDDEP = False
        self.depth = None
        self.version = None

def render_graph(name, tree, deps, rels, rev=False, engine='dot'):
    g = graphviz.Digraph(
        name='%s%s dependencies' % (name, ' reverse' if rev else ''),
        format='svg', engine=engine,
        node_attr={'shape': 'box', 'fontcolor': 'black', 'margin': '0.2,0.1'},
        edge_attr={'color': 'grey', 'dir': 'back', 'arrowtail': 'none'}
    )
    g.graph_attr['overlap'] = 'prism'
    g.graph_attr['rankdir'] = 'LR'
    for k, v in rels.items():
        attr = {}
        if v.version:
            attr['xlabel'] = v.version
        elif k == name:
            attr['fontcolor'] = 'blue'
        elif v.BUILDDEP and not v.PKGDEP:
            attr['fontcolor'] = 'red'
            attr['style'] = 'rounded'
        elif v.PKGRECOM and not v.PKGDEP:
            attr['fontcolor'] = 'grey'
            attr['style'] = 'rounded'
        if attr:
            g.node(k, **attr)
    for k, row in deps.items():
        for v in row:
            g.edge(k, v)
    g.body.append('{"%s"}' % name)
    for k, layer in enumerate(tree):
        if len(layer) < 2:
            continue
        g.body.append('{%s}' % '; '.join('"%s"' % x for x in layer))
    return g.pipe()

def find_pkgdep(db, name, deps, rels, rev=False, depth=0):
    sql = SQL_GET_PACKAGE_REV_REL if rev else SQL_GET_PACKAGE_REL
    if name in deps:
        return
    else:
        deps[name] = set()
        if rels[name].depth is None:
            rels[name].depth = depth
        else:
            rels[name].depth = min(rels[name].depth, depth)
    for dep_pkg, dep_ver, dep_rel in db.execute(sql, (name,)):
        deps[name].add(dep_pkg)
        setattr(rels[dep_pkg], dep_rel, True)
        rels[dep_pkg].version = rels[dep_pkg].version or dep_ver
        find_pkgdep(db, dep_pkg, deps, rels, depth + 1)

def pkgdep_graph(name, db, rev=False, engine='dot'):
    res = db.execute('SELECT 1 FROM packages WHERE name = ?', (name,)).fetchone()
    if res is None:
        raise KeyError(name)
    deps = {}
    rels = collections.defaultdict(DependencyProperties)
    find_pkgdep(db, name, deps, rels, rev)
    try:
        tree = [sorted(layer) for layer in toposort.toposort(deps)]
    except toposort.CircularDependencyError:
        tree = [sorted(x[0] for x in group) for k, group in
                itertools.groupby(rels.items(), key=lambda x: x[1].depth)]
    return render_graph(name, tree, deps, rels, rev, engine)

def main():
    parser = argparse.ArgumentParser(description="Generate (reverse) dependency graph from abbs database.")
    parser.add_argument("-r", "--reverse", help="Show reverse dependency", action='store_true')
    parser.add_argument("-e", "--engine", help="Graphviz render engine", choices=('dot', 'neato', 'twopi', 'circo', 'fdp', 'sfdp', 'patchwork'), default='sfdp')
    parser.add_argument("-o", "--output", help="Save SVG as")
    parser.add_argument("dbfile", help="Abbs-meta database file")
    parser.add_argument("package", help="Package name")
    args = parser.parse_args()

    db = sqlite3.connect(args.dbfile)
    graph = pkgdep_graph(args.package, db, args.reverse, args.engine)
    if args.output:
        with open(args.output, 'wb') as f:
            f.write(graph)
    else:
        sys.stdout.buffer.write(graph)

if __name__ == '__main__':
    main()

