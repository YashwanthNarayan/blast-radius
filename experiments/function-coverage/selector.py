"""Standalone runtime, standard library. See README for build inputs."""
import gzip
import hashlib
import json
import marshal
import os
import re
import sys
import types
from pathlib import Path

DATA = Path(os.getenv("BR_DATA", "/data"))
ROOT = Path(os.getenv("BR_REPO", "/opt/repo"))
MAP = Path(os.getenv("BR_MAP", "/app/functions.json.gz"))
HEADER = re.compile(r"diff --git a/(sqlglot/[\w./-]+\.py) b/\1\Z")
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
        require(lines[i:i+2] == ["--- a/" + path, "+++ b/" + path])
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
            start, count, dest, size = [int(x if x is not None else 1) for x in h.groups()]
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


def leaf_changes(a, b):
    # All enclosing executable code and metadata must compare equal when
    # corresponding nested code objects are factored out.
    require(a.co_qualname == b.co_qualname)
    children = set()
    def shell(code):
        return tuple(getattr(code, attr) for attr in (
            "co_code", "co_names", "co_varnames", "co_flags", "co_argcount",
            "co_posonlyargcount", "co_kwonlyargcount", "co_freevars", "co_cellvars",
            "co_stacksize", "co_firstlineno", "co_linetable", "co_exceptiontable"))
    def constants(code):
        return [("child", x.co_qualname) if isinstance(x, types.CodeType)
                else ("value", marshal.dumps(x)) for x in code.co_consts]
    if shell(a) != shell(b) or constants(a) != constants(b):
        require(a.co_flags & 1 and b.co_flags & 1 and not a.co_name.startswith("<"))
        require(a.co_consts and b.co_consts and
                marshal.dumps(a.co_consts[0]) == marshal.dumps(b.co_consts[0]))
        for attr in ("co_flags","co_argcount","co_posonlyargcount","co_kwonlyargcount",
                     "co_freevars","co_cellvars","co_firstlineno"):
            require(getattr(a, attr) == getattr(b, attr))
        nargs = a.co_argcount + a.co_kwonlyargcount + bool(a.co_flags & 4) + bool(a.co_flags & 8)
        require(a.co_varnames[:nargs] == b.co_varnames[:nargs])
        # Do not attribute an altered nested function solely to its creator.
        require(not any(isinstance(x, types.CodeType) for x in a.co_consts + b.co_consts))
        return {a.co_qualname}
    for x, y in zip(a.co_consts, b.co_consts):
        if isinstance(x, types.CodeType):
            children.update(leaf_changes(x, y))
    return children


def owners(raw, index):
    keys = set()
    touched = False
    for path, old, new in changes(raw):
        require(index["files"].get(path) == hashlib.sha256(old).hexdigest())
        touched = True
        opts = {"dont_inherit": True, "optimize": 0}
        names = leaf_changes(compile(old, path, "exec", **opts), compile(new, path, "exec", **opts))
        # Equivalent bytecode can hide source reads. Refuse this whole patch.
        require(names)
        keys.update(path + "::" + n for n in names)
    require(touched)
    return keys


def schedule(selected, optional):
    if not selected:
        return []
    ordered = sorted(selected, key=lambda t: (-t["seconds"], t["number"]))
    best = None
    for k in range(1, min(8, len(ordered)) + 1):
        loads = [0.] * k
        bins = [[] for _ in loads]
        for t in ordered:
            j = loads.index(min(loads))
            loads[j] += t["seconds"]
            bins[j].append(t["number"])
        score = max(loads) + 2*k
        if best is None or score < best[0]:
            best = score, loads, bins
    _, loads, bins = best
    cap = max(loads)
    for t in sorted(optional, key=lambda t: (t["seconds"], t["number"])):
        j = loads.index(min(loads))
        if loads[j] + t["seconds"] > cap:
            break
        bins[j].append(t["number"])
        loads[j] += t["seconds"]
    return bins


def read_map(tests):
    with gzip.open(MAP, "rt") as f:
        data = json.load(f)
    require(data["python"] == sys.version and data["tests"] == tests)
    return data


def main():
    tests = json.loads((DATA / "tests.json").read_bytes())["tests"]
    try:
        data = read_map(tests)
    except (OSError, ValueError, KeyError, EOFError):
        data = None
    fallback = schedule(tests, [])
    for patch in sorted((DATA / "commits").glob("*.patch")):
        bins = fallback
        if data is not None:
            try:
                keys = owners(patch.read_bytes(), data)
                hits = set(data["always"])
                for key in keys:
                    hits.update(data["calls"].get(key, []))
                bins = schedule([t for t in tests if t["number"] in hits],
                                [t for t in tests if t["number"] not in hits])
            except (ValueError, KeyError, SyntaxError, IndexError, OSError, OverflowError, RecursionError):
                pass
        print(json.dumps({"commit": patch.stem, "workers": bins}, separators=(",", ":")))
if __name__ == "__main__":
    main()
