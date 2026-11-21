"""Baseline operand coverage for simple comparisons. No patches or labels used."""

import ast
import builtins
import collections
import enum
import functools
import gzip
import importlib.abc
import importlib.util
import importlib.machinery
import json
import operator
import os
from pathlib import Path
import sys
import pytest

root = Path(os.environ["BR_REPO"]).resolve()
ids = {
    t["id"]: t["number"]
    for t in json.loads(Path(os.environ["BR_TESTS"]).read_text())["tests"]
}
observed = collections.defaultdict(set)
sites = []
ops = {
    k: getattr(operator, v)
    for k, v in {
        "Eq": "eq",
        "NotEq": "ne",
        "Lt": "lt",
        "Gt": "gt",
        "LtE": "le",
        "GtE": "ge",
        "Is": "is_",
        "IsNot": "is_not",
    }.items()
}
current = None


@functools.lru_cache(None)
def simple_type(t):
    if t in {int, str, bool, bytes, type(None)}:
        return True
    return issubclass(t, enum.Enum) and t.__eq__ in {
        object.__eq__,
        str.__eq__,
        int.__eq__,
    }


def relation(a, b):
    ta, tb = type(a), type(b)
    if a is None or b is None:
        other = tb if a is None else ta
        if simple_type(other) or other.__eq__ is object.__eq__:
            return "I" if a is b else "D"
        return "?"
    if ta is tuple and tb is tuple:
        if not all(type(x) is int for x in a + b):
            return "?"
    elif not (simple_type(ta) and ta is tb):
        return "?"
    if a is b:
        return "I"
    if a == b:
        return "E"
    if ta is tuple or ta in (int, str, bool, bytes):
        return "L" if a < b else "G"
    return "D"


def capture(i, op, a, b):
    if current is not None:
        observed[(i, relation(a, b))].add(current)
    return ops[op](a, b)


builtins._br_capture_operands = capture


class Modify(ast.NodeTransformer):
    def __init__(self, path):
        self.path = path

    def visit_Compare(self, node):
        self.generic_visit(node)
        if len(node.ops) != 1:
            return node
        op = type(node.ops[0]).__name__
        rhs = node.comparators[0]
        if op not in ops or not (
            isinstance(rhs, ast.Tuple)
            or isinstance(rhs, ast.Attribute)
            and rhs.attr.isupper()
            or isinstance(rhs, ast.Constant)
            and rhs.value is None
        ):
            return node
        index = len(sites)
        sites.append((self.path, node.lineno, node.col_offset))
        call = ast.Call(
            ast.Name("_br_capture_operands", ast.Load()),
            [ast.Constant(index), ast.Constant(op), node.left, rhs],
            [],
        )
        return ast.copy_location(call, node)


class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        path = Path(self.path)
        tree = Modify(str(path.relative_to(root))).visit(ast.parse(path.read_bytes()))
        return compile(
            ast.fix_missing_locations(tree), self.path, "exec", dont_inherit=True
        )


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if not (name == "sqlglot" or name.startswith("sqlglot.")):
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if (
            spec
            and spec.origin
            and spec.origin.startswith(str(root) + "/sqlglot/")
            and spec.origin.endswith(".py")
        ):
            spec.loader = Loader(name, spec.origin)
        return spec


class Plugin:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        global current
        current = ids.get(item.nodeid)
        yield
        current = None


if __name__ == "__main__":
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.meta_path.insert(0, Finder())
    if os.environ.get("BR_POOL"):
        from pool_trace import install

        install()
    pytest.main(
        ["-q", "--disable-warnings", "--tb=no", *(sys.argv[1:] or ["tests"])],
        plugins=[Plugin()],
    )
    groups = {}
    rows = []
    for (i, r), v in sorted(observed.items()):
        group = groups.setdefault(tuple(sorted(v)), len(groups))
        rows.append([*sites[i], r, group])
    with gzip.open(os.environ["BR_OUT"], "wt") as f:
        json.dump([list(groups), rows], f, separators=(",", ":"))
