#!/usr/bin/env python3
"""Blast Radius selector v6: line-level coverage maps + harmless-commit detection.

Data:
  /data/tests.json           every test, numbered, with recorded seconds
  /data/commits/<id>.patch   one unified diff per commit
Bundled:
  /app/maps.json             file-level coverage, import dependents, subtest unions
  /app/linemap.json          per-file RLE line coverage: {file: {r:[[s,e,setid]], s:[[tests]], t:[[s,e]]}}
  /opt/repo                  pristine sqlglot checkout at the pinned commit
stdout: one JSON line per commit {"commit": id, "workers": [[...], ...]}
"""
import ast, gzip, json, os, re, sys
from pathlib import Path

DATA = Path(os.environ.get("BR_DATA", "/data"))
REPO = Path(os.environ.get("BR_REPO", "/opt/repo"))
MAPS = json.loads(Path(os.environ.get("BR_MAPS", "/app/maps.json")).read_text())
_lmp = Path(os.environ.get("BR_LM", "/app/linemap.json.gz"))
LM = json.loads(gzip.decompress(_lmp.read_bytes()) if _lmp.suffix == ".gz" else _lmp.read_text())
BYFILE = {k: v for k, v in MAPS["byfile"].items()}
TEXT_CACHE = {}

def log(*a):
    print(*a, file=sys.stderr)

def repo_text(rel):
    if rel not in TEXT_CACHE:
        p = REPO / rel
        try:
            TEXT_CACHE[rel] = p.read_text(errors="replace")
        except Exception:
            TEXT_CACHE[rel] = None
    return TEXT_CACHE[rel]

# ---------- pure-python unified diff parsing / application ----------
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)

def parse_patch(patch_text):
    """Yield (new_path, old_path, hunks, body) per file. hunks = [(old_start, old_count)].
    new_path/old_path is '/dev/null' for creations/deletions."""
    sections = re.split(r"(?m)^(?=diff --git )", patch_text)
    for sec in sections:
        if not sec.strip():
            continue
        mo = re.search(r"(?m)^--- (?:a/(.+)|(/dev/null))$", sec)
        mn = re.search(r"(?m)^\+\+\+ (?:b/(.+)|(/dev/null))$", sec)
        if not mn:
            continue
        new = mn.group(1) or "/dev/null"
        old = (mo.group(1) or "/dev/null") if mo else (new if new != "/dev/null" else None)
        hunks = [(int(m.group(1)), int(m.group(2) or 1)) for m in HUNK.finditer(sec)]
        hi = sec.find("\n@@")
        body = sec[hi + 1:] if hi != -1 else ""
        yield new, old, hunks, body

def apply_patch(text, body):
    """Apply a single-file unified diff body to text. Return new text or None."""
    lines = text.split("\n")
    out = []
    cur = 0
    plines = body.split("\n")
    i = 0
    while i < len(plines):
        if not plines[i].startswith("@@"):
            i += 1
            continue
        m = HUNK.match(plines[i])
        if not m:
            return None
        old_start = int(m.group(1)) - 1
        old_n = int(m.group(2) or 1)
        new_n = int(m.group(4) or 1)
        if old_start < cur:
            return None
        out.extend(lines[cur:old_start])
        cur = old_start
        i += 1
        co = cn = 0
        while i < len(plines) and (co < old_n or cn < new_n):
            ln = plines[i]
            if ln.startswith("\\"):
                i += 1
                continue
            tag = ln[:1]
            b = ln[1:]
            if tag in (" ", ""):
                if cur >= len(lines) or lines[cur] != b:
                    return None
                out.append(lines[cur]); cur += 1; co += 1; cn += 1
            elif tag == "-":
                if cur >= len(lines) or lines[cur] != b:
                    return None
                cur += 1; co += 1
            elif tag == "+":
                out.append(b); cn += 1
            else:
                return None
            i += 1
    out.extend(lines[cur:])
    return "\n".join(out)

def ast_sig(text):
    try:
        t = ast.parse(text)
    except Exception:
        return None
    for node in ast.walk(t):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:]
    return ast.dump(t)

def harmless(patch_text, entries):
    files = [n if n != "/dev/null" else o for n, o, _, _ in entries]
    for f in files:
        if not f.endswith(".py"):
            return False
        if repo_text(f) is None:
            return False
    bodies = {n: b for n, _, _, b in entries if n != "/dev/null"}
    for f in files:
        body = bodies.get(f)
        if body is None:
            return False
        new = apply_patch(repo_text(f), body)
        if new is None:
            return False
        o, n = ast_sig(repo_text(f)), ast_sig(new)
        if o is None or n is None or o != n:
            return False
    return True

# ---------- line-level coverage lookup ----------
def file_all_tests(f):
    e = LM.get(f)
    if not e:
        return set()
    out = set()
    for s in e["s"]:
        out.update(s)
    return out

def line_tests(f, lo, hi):
    e = LM.get(f)
    if not e:
        return None
    out = set()
    for s, ee, sid in e["r"]:
        if ee < lo:
            continue
        if s > hi:
            break
        out.update(e["s"][sid])
    return out

def touches_toplevel(f, lo, hi):
    e = LM.get(f)
    if not e:
        return False
    for s, ee in e["t"]:
        if ee >= lo and s <= hi:
            return True
    return False


_CLASSBODY_CACHE = {}

def class_body_ranges(f):
    if f in _CLASSBODY_CACHE:
        return _CLASSBODY_CACHE[f]
    ranges = []
    try:
        tree = ast.parse(repo_text(f) or "")
    except Exception:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and hasattr(node, "end_lineno"):
                inner = []
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and hasattr(sub, "end_lineno"):
                        inner.append((sub.lineno, sub.end_lineno))
                ranges.append((node.lineno, node.end_lineno, inner))
    _CLASSBODY_CACHE[f] = ranges
    return ranges

def in_class_body(f, lo, hi):
    for cs, ce, inner in class_body_ranges(f):
        olo, ohi = max(lo, cs), min(hi, ce)
        if olo > ohi:
            continue
        covered = False
        for s, e in inner:
            if s <= ohi and e >= olo:
                covered = True
                break
        if not covered:
            return True
    return False


_FUNC_CACHE = {}

def enclosing_function(f, lo, hi):
    if f not in _FUNC_CACHE:
        spans = []
        try:
            tree = ast.parse(repo_text(f) or "")
        except Exception:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and hasattr(node, "end_lineno"):
                    spans.append((node.lineno, node.end_lineno))
        _FUNC_CACHE[f] = spans
    best = None
    for s, e in _FUNC_CACHE[f]:
        if s <= lo and e >= hi:
            if best is None or (e - s) < (best[1] - best[0]):
                best = (s, e)
    return best

FAMILY_DIRS = ("sqlglot/dialects/",)

def family_tests(f):
    # f is sqlglot/dialects/<name>.py: add tests covering sibling modules whose
    # classes inherit this dialect's class attributes (line coverage cannot see them)
    stem = Path(f).stem
    out = set()
    for d in ("sqlglot/dialects/", "sqlglot/generators/", "sqlglot/parsers/", "sqlglot/typing/"):
        out |= file_all_tests(d + stem + ".py")
    return out

def token_net(stems, tests):
    picked = set()
    pats = [re.compile(r"(?i)(?<![a-z0-9_])" + re.escape(s) + r"(?![a-z0-9_])") for s in stems]
    for t in tests:
        body = repo_text(t["file"]) or ""
        if any(p.search(body) for p in pats):
            picked.add(t["number"])
    return picked

def pack(tests_sel, maxw=8, all_tests=None):
    if not tests_sel:
        return []
    best = None
    for w in range(1, maxw + 1):
        ws = [[] for _ in range(w)]
        ld = [0.0] * w
        for t in sorted(tests_sel, key=lambda x: x["seconds"], reverse=True):
            i = ld.index(min(ld))
            ws[i].append(t["number"])
            ld[i] += t["seconds"]
        cost = max(ld) + 2 * w
        if best is None or cost < best[0]:
            best = (cost, [x for x in ws if x])
    bins = best[1]
    if all_tests:
        chosen = {n for worker in bins for n in worker}
        secs = {t["number"]: t["seconds"] for t in all_tests}
        loads = [sum(secs[n] for n in worker) for worker in bins]
        cap = max(loads)
        for t in sorted((t for t in all_tests if t["number"] not in chosen),
                        key=lambda x: (x["seconds"], x["number"])):
            i = loads.index(min(loads))
            if loads[i] + t["seconds"] > cap:
                break
            bins[i].append(t["number"])
            loads[i] += t["seconds"]
    return bins

def select(patch_text, tests):
    entries = list(parse_patch(patch_text))
    if not entries:
        return list(tests)
    files = [n if n != "/dev/null" else o for n, o, _, _ in entries]
    try:
        if harmless(patch_text, entries):
            return []
    except Exception as e:
        log("harmless check failed:", e)
    picked = set()
    src_touched = False
    for new, old, hunks, body in entries:
        path = new if new != "/dev/null" else old
        is_src = path.startswith("sqlglot/") and path.endswith(".py")
        is_fix = path.startswith("tests/fixtures/")
        is_test = path.startswith("tests/") and path.endswith(".py") and not is_fix
        if not (is_src or is_fix or is_test):
            return list(tests)
        if is_test:
            if new == "/dev/null":
                continue
            nums = BYFILE.get(path)
            if nums is None:
                return list(tests)
            picked.update(nums)
            continue
        if is_fix:
            stem = Path(path).stem
            matched = {t["number"] for t in tests
                       if stem in (repo_text(t["file"]) or "") or stem in t["file"]}
            if not matched:
                return list(tests)
            picked |= matched
            continue
        # sqlglot source file
        src_touched = True
        if new == "/dev/null":            # deleted module: everything importing it
            picked |= file_all_tests(old)
            continue
        if path not in LM:                # brand-new module: nothing can cover it
            continue
        for os_, oc in hunks:
            lo, hi = (os_ - 2, os_ + oc + 1) if oc > 0 else (os_ - 2, os_ + 2)
            lo = max(lo, 1)
            if touches_toplevel(path, lo, hi):
                picked |= file_all_tests(path)
                if any(path.startswith(d) for d in FAMILY_DIRS) and in_class_body(path, lo, hi):
                    picked |= family_tests(path)
            else:
                got = line_tests(path, lo, hi)
                if got:
                    picked |= got
                span = enclosing_function(path, os_, max(os_, os_ + oc - 1))
                if span is not None:
                    fgot = line_tests(path, span[0], span[1])
                    if fgot:
                        picked |= fgot
                if any(path.startswith(d) for d in FAMILY_DIRS) and in_class_body(path, lo, hi):
                    picked |= family_tests(path)
    if src_touched and not picked:
        for new, old, hunks, body in entries:
            p = new if new != "/dev/null" else old
            if p.startswith("sqlglot/") and p.endswith(".py"):
                picked |= file_all_tests(p)
        if not picked:
            picked = token_net({Path(f).stem for f in files}, tests)
        if not picked:
            return list(tests)
    return [t for t in tests if t["number"] in picked]

# ---------- one-time CPU-charging calibration probe (guarded, stderr only) ----------
def probe():
    try:
        import shutil, subprocess, tempfile, time
        if not (REPO / "setup.py").exists() and not (REPO / "pyproject.toml").exists():
            return
        tmp = tempfile.mkdtemp(prefix="brp")
        try:
            import pytest  # noqa: F401
        except Exception:
            log("probe: pytest unavailable")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        ids = ["tests/test_executor.py::TestExecutor::test_execute_tpch",
               "tests/test_executor.py::TestExecutor::test_optimized_tpch"]
        for n in ("2", None):
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + ids
            if n:
                cmd += ["-n", n]
            t0 = time.time()
            r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                               timeout=120, env={**os.environ, "HOME": tmp})
            wall = time.time() - t0
            tail = (r.stdout or "").strip().splitlines()
            log(f"probe xdist={n}: wall={wall:.2f}s rc={r.returncode}",
                tail[-1] if tail else (r.stderr or "")[-120:])
            if n is None:
                break
            # if xdist missing, first run already errored; serial fallback next loop
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        log("probe failed:", repr(e))

def main():
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    commits = sorted((DATA / "commits").glob("*.patch"))
    if os.environ.get("BR_PROBE", "0") == "1":
        probe()
    for patch in commits:
        sel = select(patch.read_text(errors="replace"), tests)
        workers = pack(sel, all_tests=tests)
        print(json.dumps({"commit": patch.stem, "workers": workers}))
        log(f"{patch.stem}: selected {len(sel)} tests on {len(workers)} workers")

if __name__ == "__main__":
    main()
