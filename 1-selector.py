#!/usr/bin/env python3
"""Blast Radius selector: per-test coverage maps + harmless-commit detection.

Data:
  /data/tests.json           every test, numbered, with recorded seconds
  /data/commits/<id>.patch   one unified diff per commit
Bundled:
  /app/maps.json             coverage map, import dependents, subtest-file unions
  /opt/repo                  pristine sqlglot checkout at the pinned commit
stdout: one JSON line per commit {"commit": id, "workers": [[...], ...]}
"""
import ast, json, os, re, sys
from pathlib import Path

DATA = Path(os.environ.get("BR_DATA", "/data"))
REPO = Path(os.environ.get("BR_REPO", "/opt/repo"))
MAPS = json.loads(Path(os.environ.get("BR_MAPS", "/app/maps.json")).read_text())
COV = {int(k): set(v) for k, v in MAPS["cov"].items()}
DEP = MAPS["dep"]
UNION = {k: set(v) for k, v in MAPS["union"].items()}
SUBFILES = set(MAPS["subfiles"])
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

# ---------- pure-python unified diff application ----------
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

def apply_patch(text, patch):
    """Apply a single-file unified diff body to text. Return new text or None."""
    lines = text.split("\n")
    out = []
    cur = 0  # 0-based index into lines
    plines = patch.split("\n")
    i = 0
    # skip to first hunk
    while i < len(plines) and not plines[i].startswith("@@"):
        i += 1
    while i < len(plines):
        m = HUNK.match(plines[i])
        if not m:
            return None
        old_start = int(m.group(1)) - 1
        if old_start < cur:
            return None
        out.extend(lines[cur:old_start])
        cur = old_start
        i += 1
        while i < len(plines) and not plines[i].startswith("@@"):
            ln = plines[i]
            if ln.startswith("\\"):
                i += 1
                continue
            tag = ln[:1]
            body = ln[1:]
            if tag == " ":
                if cur >= len(lines) or lines[cur] != body:
                    return None
                out.append(lines[cur]); cur += 1
            elif tag == "-":
                if cur >= len(lines) or lines[cur] != body:
                    return None
                cur += 1
            elif tag == "+":
                out.append(body)
            elif tag == "":
                # tolerate blank context line
                if cur < len(lines) and lines[cur] == "":
                    out.append(lines[cur]); cur += 1
                else:
                    return None
            else:
                return None
            i += 1
    out.extend(lines[cur:])
    return "\n".join(out)

def split_file_patches(patch_text):
    """Yield (new_path, body) for each file section of a unified diff."""
    sections = re.split(r"(?m)^(?=diff --git )", patch_text)
    for sec in sections:
        if not sec.strip():
            continue
        m = re.search(r"(?m)^\+\+\+ b/(.+)$", sec)
        if not m:
            continue
        path = m.group(1).strip()
        # body from first hunk header
        hi = sec.find("\n@@")
        if hi == -1:
            body = ""
        else:
            body = sec[hi + 1:]
        yield path, body

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

def harmless(patch_text, files):
    for f in files:
        if not f.endswith(".py"):
            return False
        orig = repo_text(f)
        if orig is None:
            return False
    sections = dict(split_file_patches(patch_text))
    for f in files:
        body = sections.get(f)
        if body is None:
            return False
        new = apply_patch(repo_text(f), body)
        if new is None:
            return False
        o, n = ast_sig(repo_text(f)), ast_sig(new)
        if o is None or n is None or o != n:
            return False
    return True

def token_net(stems, tests):
    picked = set()
    pats = [re.compile(r"(?i)(?<![a-z0-9_])" + re.escape(s) + r"(?![a-z0-9_])") for s in stems]
    for t in tests:
        body = repo_text(t["file"]) or ""
        if any(p.search(body) for p in pats):
            picked.add(t["number"])
    return picked

def pack(tests_sel, maxw=8):
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
    return best[1]

def select(patch_text, tests):
    files = [p for p, _ in split_file_patches(patch_text)]
    if not files:
        return list(tests)
    try:
        if harmless(patch_text, files):
            return []
    except Exception as e:
        log("harmless check failed:", e)
    src = [f for f in files if f.startswith("sqlglot/") and f.endswith(".py")]
    fixtures = [f for f in files if f.startswith("tests/fixtures/")]
    testfiles = [f for f in files if f.startswith("tests/") and f.endswith(".py")]
    other = [f for f in files if f not in src and f not in fixtures and f not in testfiles]
    if other:
        return list(tests)
    picked = set()
    if src:
        affected = set()
        for f in src:
            affected.add(f)
            affected |= set(DEP.get(f, []))
        for t in tests:
            n = t["number"]
            if t["file"] in SUBFILES:
                c = UNION.get(t["file"], set())
            else:
                c = COV.get(n)
                if c is None:
                    c = UNION.get(t["file"], set())
            if c & affected:
                picked.add(n)
        if not picked:
            picked = token_net({Path(f).stem for f in src}, tests)
        if not picked:
            return list(tests)
    for fx in fixtures:
        stem = Path(fx).stem
        matched = {t["number"] for t in tests
                   if stem in (repo_text(t["file"]) or "") or stem in t["file"]}
        if not matched:
            return list(tests)
        picked |= matched
    for tf in testfiles:
        nums = BYFILE.get(tf)
        if nums is None:
            return list(tests)
        picked |= set(nums)
    return [t for t in tests if t["number"] in picked]

def main():
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    commits = sorted((DATA / "commits").glob("*.patch"))
    for patch in commits:
        sel = select(patch.read_text(errors="replace"), tests)
        workers = pack(sel)
        print(json.dumps({"commit": patch.stem, "workers": workers}))
        log(f"{patch.stem}: selected {len(sel)} tests on {len(workers)} workers")

if __name__ == "__main__":
    main()
