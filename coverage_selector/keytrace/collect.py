"""Run the pinned test catalog with real subprocesses and attribute lookup keys."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        env = dict(
            os.environ,
            BR_KEY_DIR=tmp,
            BR_CHILD_DIR=tmp,
            PYTHONPATH=str(here) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            PYTHON_CPU_COUNT="1",
        )
        # Keep the key collector first on the startup path in every interpreter.
        source = (here.parent / "native" / "observe.py").read_text()
        subprocess.run([sys.executable, "-c", source], env=env, check=True)
        audit = json.loads((directory / "origins").read_bytes())
        seen = set()
        unknown = set()
        records = set()
        for marker in directory.glob("*.start"):
            number = json.loads(marker.read_text())
            log = marker.with_suffix(".json")
            if not log.exists():
                if number is None:
                    raise RuntimeError("incomplete parent trace")
                unknown.add(int(number))
                continue
            if number is not None:
                seen.add(int(number))
            records.update(map(tuple, json.loads(log.read_text())))
        for number, methods in audit["uses"].items():
            if int(number) not in seen or "uncertain" in methods:
                unknown.add(int(number))
        catalog = json.loads(Path(os.environ["BR_TESTS"]).read_bytes())["tests"]
        # An observation outside a numbered test can affect any later test.
        rows = {}
        for path, attr, key, test in sorted(records):
            group = rows.setdefault(path, {}).setdefault(attr, {}).setdefault(key, [])
            group.append(-1 if test == "*" else int(test))
        if not any(k != "*" for k in rows) or audit["rc"]:
            raise RuntimeError("incomplete run")
        unknown.update({t["number"] for t in catalog} - set(audit["completed"]))

        ids = sorted((t["number"], t["id"]) for t in catalog)
        signature = hashlib.sha256(
            json.dumps(ids, separators=(",", ":")).encode()
        ).hexdigest()
        Path(sys.argv[1]).write_text(
            json.dumps(
                dict(signature=signature, unknown=sorted(unknown), reads=rows),
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
