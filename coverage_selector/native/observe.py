"""Audit real process boundaries. A fresh native pool per parent test tags worker startup and all serialized tasks with the correct origin."""

import concurrent.futures as futures
from collections import defaultdict
import json
import os
from pathlib import Path
import sys
import pytest

root = Path(os.environ["BR_REPO"]).resolve()
out = Path(os.environ["BR_CHILD_DIR"]).resolve()
catalog = json.loads(Path(os.environ["BR_TESTS"]).read_text())["tests"]
ids = {t["id"]: t["number"] for t in catalog}
current = None
uses = defaultdict(set)
counts = defaultdict(int)
completed = set()
real_submit = futures.ProcessPoolExecutor.submit
private = {}


def submit(pool, fn, /, *args, **kw):
    if current is None:
        return real_submit(pool, fn, *args, **kw)
    uses[current].add("pool")
    counts[current] += 1
    if pool not in private:
        private[pool] = futures.ProcessPoolExecutor(
            max_workers=1,
            mp_context=pool._mp_context,
            initializer=pool._initializer,
            initargs=pool._initargs,
        )
    return real_submit(private[pool], fn, *args, **kw)


def audit(event, args):
    if current is None:
        return
    if event == "subprocess.Popen":
        cmd = args[1]
        recognized = (
            isinstance(cmd, (tuple, list))
            and Path(cmd[0]).name.startswith("python")
            and not any(x in cmd[1:3] for x in ("-S", "-I", "-E"))
            and args[3] is None
        )
        uses[current].add("python" if recognized else "uncertain")
    elif event in {"os.fork", "os.forkpty", "os.system", "os.posix_spawn", "os.exec"}:
        uses[current].add("uncertain")


class Tracker:
    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed and report.nodeid in ids:
            completed.add(ids[report.nodeid])

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        global current
        current = ids.get(item.nodeid)
        if current is not None:
            os.environ["BR_CHILD_TEST"] = str(current)
        try:
            yield
        finally:
            for pool in private.values():
                pool.shutdown()
            private.clear()
            os.environ.pop("BR_CHILD_TEST", None)
            current = None


if __name__ == "__main__":
    os.chdir(root)
    sys.path.insert(0, str(root))
    out.mkdir(exist_ok=True)
    sys.addaudithook(audit)
    futures.ProcessPoolExecutor.submit = submit
    rc = pytest.main(
        ["-q", "--tb=no", "--disable-warnings", "tests"], plugins=[Tracker()]
    )
    (out / "origins").write_text(
        json.dumps(
            {
                "uses": {k: sorted(v) for k, v in uses.items()},
                "submitted": counts,
                "completed": sorted(completed),
                "rc": int(rc),
            }
        )
    )
    raise SystemExit(rc)
