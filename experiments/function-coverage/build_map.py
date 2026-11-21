"""Offline pinned-base function dependency builder. Never supply a patched tree."""
import argparse
from collections import defaultdict
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def trace_one(root, node, output):
    import pytest
    import threading
    root = root.resolve()
    prefix = str(root / "sqlglot") + os.sep
    hits = set()
    uncertain = set()
    reports = []
    paths = {}
    def profile(frame, event, arg):
        if event != "call":
            return
        code = frame.f_code
        key = paths.get(code)
        if key is None:
            filename = code.co_filename
            key = (filename[len(str(root))+1:] + "::" + code.co_qualname
                   if filename.startswith(prefix) else "")
            paths[code] = key
        if key:
            hits.add(key)
    def audit(event, args):
        if event in {"subprocess.Popen", "os.fork", "os.posix_spawn", "os.system",
                     "os.exec", "_thread.start_new_thread", "_thread.start_joinable_thread"}:
            uncertain.add(event)
    class Reports:
        def pytest_collection_finish(self, session):
            if [item.nodeid for item in session.items] != [node]:
                uncertain.add("collection")
        def pytest_runtest_logreport(self, report):
            reports.append((report.nodeid, report.when, report.outcome))
            if getattr(report, "context", None) is not None and report.outcome != "passed":
                uncertain.add("subtest")
    if any(name == "sqlglot" or name.startswith("sqlglot.") for name in sys.modules):
        uncertain.add("preloaded")
    sys.addaudithook(audit)
    sys.setprofile(profile)
    threading.setprofile(profile)
    try:
        rc = pytest.main(["-q", "-p", "no:cacheprovider", node], plugins=[Reports()])
    finally:
        threading.setprofile(None)
        sys.setprofile(None)
    phases = {when for ident, when, status in reports if ident == node and status == "passed"}
    if (rc == 0 and not uncertain and phases >= {"setup","call","teardown"}
            and all(status == "passed" for _, _, status in reports) and hits):
        output.write_text(json.dumps(sorted(hits)))


def build():
    p = argparse.ArgumentParser()
    for name in ("repo","tests","out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--nodes", type=Path, help="JSON object mapping kit number to exact pytest nodeid")
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--timeout", type=float, default=180)
    args = p.parse_args()
    root = args.repo.resolve()
    tests = json.loads(args.tests.read_bytes())["tests"]
    nodes = json.loads(args.nodes.read_bytes()) if args.nodes else {}
    calls = defaultdict(list)
    always = []
    env = dict(os.environ, PYTHONPATH=str(root), SKIP_INTEGRATION="0",
               PYTHONDONTWRITEBYTECODE="1")
    def run(t):
        node = nodes.get(str(t["number"]), t.get("nodeid"))
        if not node or node.split("::")[0] != t["file"] or "::" not in node:
            return t["number"], []
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "trace"
            try:
                proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--one",
                                       str(root), node, str(out)], env=env, cwd=root,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      timeout=args.timeout)
                if proc.returncode == 0 and out.exists():
                    return t["number"], json.loads(out.read_bytes())
            except subprocess.TimeoutExpired:
                pass
        return t["number"], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for number, hits in pool.map(run, tests):
            if not hits:
                always.append(number)
            for key in hits:
                calls[key].append(number)
    files = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
             for f in (root / "sqlglot").rglob("*.py")}
    data = dict(python=sys.version, tests=tests, files=files, always=always, calls=calls)
    with gzip.open(args.out,"wt") as out:
        json.dump(data,out,separators=(",",":"))
    print(f"{len(always)} of {len(tests)} tests always included", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--one"]:
        trace_one(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
    else:
        build()
