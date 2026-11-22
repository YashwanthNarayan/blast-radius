"""Select from observed dependencies of the pinned base checkout."""

import ast
import collections
import functools
import gzip
import json
import os
import types
import hashlib
import re
from pathlib import Path


ROOT = Path(os.getenv("BR_REPO", "/opt/repo"))
DATA = Path(os.getenv("BR_DATA", "/data"))
HERE = Path(__file__).resolve().parent
BYFILE = collections.defaultdict(set)
MAP = Path(os.getenv("BR_MEASURED", HERE / "measured.json.gz"))
GROUPS, ENTRIES, COMPLETE = json.loads(gzip.decompress(MAP.read_bytes()))
DEPS = {tuple(row[:-1]): GROUPS[row[-1]] for row in ENTRIES}


NATIVE = json.loads(gzip.decompress((HERE / "native.json.gz").read_bytes()))
CHILD_LINES = {
    (path, line): NATIVE["groups"][group] for path, line, group in NATIVE["rows"]
}


def hits(*key):
    return set(DEPS.get(key, ()))


HEADER = re.compile(r"diff --git a/((?:sqlglot|tests)/[\w./-]+) b/\1\Z")
HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?\Z")


def require(condition):
    if not condition:
        raise ValueError("unsupported patch or map")


def changes(raw):
    # Only same-path regular UTF-8 text modifications with LF endings.
    lines = raw.decode().split("\n")
    require(lines.pop() == "")
    i = 0
    seen = set()
    while i < len(lines):
        m = HEADER.fullmatch(lines[i])
        require(m)
        path = m[1]
        require(".." not in Path(path).parts and path not in seen)
        seen.add(path)
        i += 1
        if lines[i].startswith("index "):
            require(re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?", lines[i]))
            i += 1
        require(lines[i : i + 2] == ["--- a/" + path, "+++ b/" + path])
        i += 2
        base = (ROOT / path).read_bytes()
        old = base.decode().split("\n")
        require(old.pop() == "" and b"\r" not in base)
        new = []
        cursor = 0
        require(i < len(lines) and HUNK.fullmatch(lines[i]))
        while i < len(lines) and not lines[i].startswith("diff --git "):
            h = HUNK.fullmatch(lines[i])
            require(h)
            start, count, dest, size = [
                int(x if x is not None else 1) for x in h.groups()
            ]
            before = start - int(count > 0)
            require(cursor <= before <= len(old))
            new.extend(old[cursor:before])
            require(len(new) == dest - int(size > 0))
            cursor = before
            begin = len(new)
            i += 1
            while i < len(lines) and lines[i][:1] in {" ", "-", "+"}:
                tag, body = lines[i][0], lines[i][1:]
                if tag != "+":
                    require(cursor < len(old) and old[cursor] == body)
                    cursor += 1
                if tag != "-":
                    new.append(body)
                i += 1
            require(cursor - before == count and len(new) - begin == size)
        new.extend(old[cursor:])
        yield path, base, ("\n".join(new) + "\n").encode()


@functools.lru_cache(32)
def syntax(path):
    src = (ROOT / path).read_text()
    tree = ast.parse(src)
    parents = {child: p for p in ast.walk(tree) for child in ast.iter_child_nodes(p)}
    return src, tree, parents


def changed_nodes(old, new):
    # Return old-side AST nodes whose scalar fields or children changed.
    if type(old) is not type(new):
        return [(old, new)]
    result = []
    for name, left in ast.iter_fields(old):
        right = getattr(new, name)
        if isinstance(left, ast.AST) and isinstance(right, ast.AST):
            if not hasattr(left, "lineno") and type(left) is not type(right):
                result.append((old, new))
            else:
                result.extend(changed_nodes(left, right))
        elif (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(isinstance(x, ast.AST) or x is None for x in left + right)
        ):
            for a, b in zip(left, right):
                if a is None or b is None:
                    if a is not b:
                        result.append((old, new))
                    continue
                if not hasattr(a, "lineno") and type(a) is not type(b):
                    result.append((old, new))
                else:
                    result.extend(changed_nodes(a, b))
        elif type(left) is not type(right) or left != right:
            result.append((old, new))
    return result


VALUE_MAP = Path(os.getenv("BR_VALUES", HERE / "values.json.gz"))
VALUE_GROUPS, VALUE_ROWS = (
    json.loads(gzip.decompress(VALUE_MAP.read_bytes()))
    if VALUE_MAP.exists()
    else ([], [])
)
VALUE_DEPS = collections.defaultdict(dict)
for path, row, col, rel, idx in VALUE_ROWS:
    VALUE_DEPS[path, row, col][rel] = VALUE_GROUPS[idx]


def operand_hits(path, a, b):
    if not (
        isinstance(a, ast.Compare)
        and isinstance(b, ast.Compare)
        and len(a.ops) == len(b.ops) == 1
        and ast.dump(a.left) == ast.dump(b.left)
        and ast.dump(a.comparators[0]) == ast.dump(b.comparators[0])
    ):
        return None
    relations = VALUE_DEPS.get((path, a.lineno, a.col_offset))
    if not relations:
        return None
    true = {
        "Is": "I",
        "IsNot": "ELGD",
        "Eq": "IE",
        "NotEq": "LGD",
        "Lt": "L",
        "Gt": "G",
        "LtE": "LIE",
        "GtE": "GIE",
    }
    before, after = type(a.ops[0]).__name__, type(b.ops[0]).__name__
    if {before, after} not in (
        {"Is", "Eq"},
        {"IsNot", "NotEq"},
        {"Lt", "LtE"},
        {"Gt", "GtE"},
    ):
        return None
    result = set()
    for relation, ids in relations.items():
        unknown = (
            relation == "?"
            or relation == "D"
            and any(op in {"Lt", "Gt", "LtE", "GtE"} for op in (before, after))
        )
        if unknown or (relation in true[before]) != (relation in true[after]):
            result.update(ids)
    return result


@functools.lru_cache(64)
def attribute_sites(name):
    sites = set()
    direct = set()
    for folder in ("sqlglot", "tests"):
        for f in (ROOT / folder).rglob("*.py"):
            source = f.read_text()
            if name not in source:
                continue
            path = str(f.relative_to(ROOT))
            if folder == "tests":
                direct.update(BYFILE.get(path, ()))
                continue
            tree = ast.parse(source)
            for n in ast.walk(tree):
                if isinstance(n, ast.Attribute) and n.attr == name:
                    sites.update(hits("line", path, n.lineno))
                elif isinstance(n, ast.Constant) and n.value == name:
                    return None, direct
    return sites, direct


def scopes(code):
    return (
        code.co_qualname,
        code.co_flags,
        code.co_argcount,
        code.co_kwonlyargcount,
        code.co_posonlyargcount,
        code.co_varnames,
        code.co_cellvars,
        code.co_freevars,
        tuple(
            scopes(child)
            for child in code.co_consts
            if isinstance(child, types.CodeType)
        ),
    )


@functools.lru_cache(1)
def registry():
    return json.loads((HERE / "registry.json").read_bytes())


def registration_clients(change, replacement):
    """Include base dispatch targets displaced by edited SQL aliases."""
    if not isinstance(change, ast.Constant) or not isinstance(
        replacement, ast.Constant
    ):
        raise ValueError("registry shape changed")
    names = (change.value, replacement.value)
    if not all(isinstance(name, str) for name in names):
        raise ValueError("unknown registry key")
    clients = set()
    for name in names:
        target = registry().get(name)
        if target is None:
            raise ValueError("previously unregistered spelling")
        observed = hits("type", *target)
        if not observed:
            raise ValueError("unobserved dispatch target")
        clients.update(observed)
    return clients


@functools.lru_cache(1)
def key_map():
    data = json.loads((HERE / "key_reads.json").read_bytes())
    require(data["signature"] == (HERE / "catalog.sha256").read_text().strip())
    return data


def table_readers(path, attribute, assignment, chain, node, replacement):
    """Narrow an existing value edit only when the producer is side-effect free."""
    if attribute not in {"NUMERIC_ESCAPES", "SHOW_PARSERS"}:
        return None
    table = assignment.value
    if not isinstance(table, ast.Dict):
        return None
    # A literal entry after the last unpack overrides all copied entries.
    # If a later unpack can override this value, retain the v2 selection.
    for index, value in enumerate(table.values):
        if value in chain:
            break
    else:
        return None
    key_node = table.keys[index]
    if not isinstance(key_node, ast.Constant) or type(key_node.value) is not str:
        return None
    key = key_node.value
    for later in table.keys[index + 1 :]:
        if not isinstance(later, ast.Constant) or later.value == key:
            return None
    if not isinstance(node, ast.Constant) or not isinstance(replacement, ast.Constant):
        return None
    if type(node.value) is not type(replacement.value):
        return None
    if attribute == "NUMERIC_ESCAPES":
        if not (
            isinstance(value, ast.Tuple)
            and len(value.elts) == 4
            and node in value.elts
            and type(node.value) is int
        ):
            return None
    elif not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "_show_parser"
        and type(node.value) in (bool, str)
        and (node in value.args or any(kw.value is node for kw in value.keywords))
    ):
        return None
    data = key_map()
    # Optimize only tables with positively observed reads on the base suite.
    if attribute not in data["reads"].get(path, {}):
        return None
    picked = set(data["unknown"])
    for owner in (path, "*"):
        picked.update(data["reads"].get(owner, {}).get(attribute, {}).get(key, []))
    require(-1 not in picked)
    return picked


def select_file(path, patched, *, use_keys=True):
    readers = hits("read", path)
    if path.startswith("tests/fixtures/") and readers:
        return readers
    if not path.startswith("sqlglot/") or not path.endswith(".py"):
        raise ValueError
    src, tree, parents = syntax(path)

    new = ast.parse(patched)
    require(
        scopes(compile(new, path, "exec", dont_inherit=True))
        == scopes(compile(tree, path, "exec", dont_inherit=True))
    )
    result = readers.copy()
    edits = changed_nodes(tree, new)
    for node, replacement in edits:
        if not isinstance(node, ast.expr) or not isinstance(replacement, ast.expr):
            raise ValueError
        compared = operand_hits(path, node, replacement)
        if compared is not None:
            result.update(compared)
            # A child can execute a comparison absent from the replay. Preserve
            # its reader when we lack an operand observation for that test.
            observed = set().union(
                *VALUE_DEPS[path, node.lineno, node.col_offset].values()
            )
            for line in range(node.lineno, node.end_lineno + 1):
                result.update(set(CHILD_LINES.get((path, line), ())) - observed)
            continue
        chain = [node]
        while chain[-1] in parents:
            chain.append(parents[chain[-1]])
        owner = next(
            (
                x
                for x in chain
                if isinstance(
                    x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
                )
            ),
            None,
        )
        if owner is None:
            raise ValueError
        loc = next((x for x in chain if hasattr(x, "lineno")), None)
        if isinstance(owner, ast.ClassDef):
            assignment = next(
                (x for x in chain if isinstance(x, (ast.Assign, ast.AnnAssign))), None
            )
            if assignment is None or assignment.value not in chain:
                raise ValueError
            target = (
                assignment.targets[0]
                if isinstance(assignment, ast.Assign)
                else assignment.target
            )
            if not isinstance(target, ast.Name):
                raise ValueError
            cls = ".".join(
                reversed([x.name for x in chain if isinstance(x, ast.ClassDef)])
            )
            if use_keys and len(edits) == 1:
                keyed = table_readers(
                    path, target.id, assignment, chain, node, replacement
                )
                if keyed is not None:
                    result.update(keyed)
                    continue
            relevant = hits("type", path[:-3].replace("/", "."), cls)
            if not relevant:
                raise ValueError
            if target.id == "_sql_names":
                relevant.update(registration_clients(node, replacement))
            if isinstance(assignment.value, (ast.Constant, ast.UnaryOp)):
                at, direct = attribute_sites(target.id)
                if at is not None:
                    relevant = (relevant & at) | direct
            if not relevant:
                raise ValueError
            result.update(relevant)
        else:
            if loc is None or loc is owner and not isinstance(owner, ast.Lambda):
                raise ValueError
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) and not any(
                x in owner.body for x in chain
            ):
                raise ValueError
            # Use the position of the enclosing expression for operator-only edits.
            for line in range(loc.lineno, loc.end_lineno + 1):
                result.update(hits("line", path, line))
                result.update(CHILD_LINES.get((path, line), ()))
    return result


def pack(tests, ids):
    chosen = sorted(
        (t for t in tests if t["number"] in ids),
        key=lambda t: (-t["seconds"], t["number"]),
    )
    best = float("inf")
    bins = []
    for count in range(1, min(8, len(chosen)) + 1):
        trial = [[] for _ in range(count)]
        loads = [0] * count
        for test in chosen:
            i = loads.index(min(loads))
            trial[i].append(test["number"])
            loads[i] += round(test["seconds"] * 1000)
        cost = max(loads) + 2000 * count
        if cost < best:
            best, bins = cost, trial
    if bins:
        durations = {t["number"]: round(t["seconds"] * 1000) for t in tests}
        loads = [sum(durations[n] for n in w) for w in bins]
        cap = max(loads)
        for n in sorted(durations.keys() - ids, key=lambda n: (durations[n], n)):
            i = loads.index(min(loads))
            if loads[i] + durations[n] > cap:
                break
            bins[i].append(n)
            loads[i] += durations[n]
    return bins


def main():
    tests = json.loads((DATA / "tests.json").read_bytes())["tests"]
    catalog = sorted((t["number"], t["id"]) for t in tests)
    signature = hashlib.sha256(
        json.dumps(catalog, separators=(",", ":")).encode()
    ).hexdigest()
    all_ids = {t["number"] for t in tests}
    missing = all_ids - set(COMPLETE)
    for t in tests:
        BYFILE[t["file"]].add(t["number"])
    catalog_matches = signature == (HERE / "catalog.sha256").read_text().strip()
    for patch in sorted((DATA / "commits").glob("*.patch")):
        picked = missing.union(NATIVE["always"])
        try:
            require(catalog_matches)
            changed = list(changes(patch.read_bytes()))
            require(changed)
            for path, _, patched in changed:
                picked.update(select_file(path, patched, use_keys=len(changed) == 1))
        except (
            ValueError,
            SyntaxError,
            OSError,
            IndexError,
            KeyError,
            RecursionError,
            OverflowError,
        ):
            picked = all_ids
        print(
            json.dumps(
                dict(commit=patch.stem, workers=pack(tests, picked)),
                separators=(",", ":"),
            )
        )


if __name__ == "__main__":
    main()
