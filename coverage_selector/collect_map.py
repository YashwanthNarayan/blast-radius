"""Record executed source lines, instantiated types, and opened local data files."""

import collections
import gzip
import json
import os
import sys
from pathlib import Path
import pytest

root = Path(os.environ["BR_REPO"]).resolve()
catalog = json.loads(Path(os.environ["BR_TESTS"]).read_text())["tests"]
ids = {row["id"]: row["number"] for row in catalog}
observed = collections.defaultdict(set)
completed = set()
current = None
mon = sys.monitoring
tool = 3
prefix = str(root) + "/"


def record(key):
    if current is not None:
        observed[key].add(current)


def source_line(code, lineno):
    path = code.co_filename
    if path.startswith(prefix + "sqlglot/"):
        record(("line", path[len(prefix) :], lineno))
    return mon.DISABLE


def start(code, offset):
    if code.co_name not in {"__init__", "__new__"} or not code.co_filename.startswith(
        prefix + "sqlglot/"
    ):
        return mon.DISABLE
    frame = sys._getframe(1)
    args = frame.f_locals
    obj = args.get("self")
    cls = type(obj) if obj is not None else args.get("cls")
    if isinstance(cls, type) and cls not in seen_types:
        seen_types.add(cls)
        for base in cls.__mro__:
            if base.__module__.startswith("sqlglot"):
                record(("type", base.__module__, base.__qualname__))


def audit(event, args):
    if current is not None and event == "open" and isinstance(args[0], str):
        path = os.path.abspath(args[0])
        if (
            path.startswith(prefix + "tests/fixtures/")
            or path.startswith(prefix + "sqlglot/")
            and path.endswith(".py")
        ):
            record(("read", path[len(prefix) :]))


seen_types = set()


class Plugin:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        global current
        seen_types.clear()
        current = ids.get(item.nodeid)
        mon.restart_events()
        yield
        current = None

    def pytest_runtest_logreport(self, report):
        if (
            report.nodeid in ids
            and report.when == "call"
            and report.outcome in {"passed", "failed"}
        ):
            completed.add(ids[report.nodeid])


if __name__ == "__main__":
    os.chdir(root)
    sys.path.insert(0, str(root))
    mon.use_tool_id(tool, "base-dependencies")
    mon.register_callback(tool, mon.events.LINE, source_line)
    mon.register_callback(tool, mon.events.PY_START, start)
    mon.set_events(tool, mon.events.LINE | mon.events.PY_START)
    sys.addaudithook(audit)
    try:
        if os.environ.get("BR_POOL"):
            from pool_trace import install

            install()
        pytest.main(
            ["-q", "--disable-warnings", "--tb=no", *(sys.argv[1:] or ["tests"])],
            plugins=[Plugin()],
        )
    finally:
        mon.set_events(tool, 0)
        # Factor repeated test sets, especially common executed lines.
        groups = {}
        entries = []
        for k, v in sorted(observed.items()):
            s = tuple(sorted(v))
            idx = groups.setdefault(s, len(groups))
            entries.append([*k, idx])
        with gzip.open(os.environ["BR_OUT"], "wt") as f:
            json.dump(
                [list(groups), entries, sorted(completed)], f, separators=(",", ":")
            )
