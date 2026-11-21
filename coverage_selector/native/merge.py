"""Fold the pinned child observations into deduplicated line sets."""

import collections
import json
from pathlib import Path
import sys


def merge(directory):
    audit = json.loads((directory / "origins").read_text())
    if audit["rc"]:
        raise ValueError("pristine test suite failed")
    lines = collections.defaultdict(set)
    uncertain = set()
    for number, methods in audit["uses"].items():
        markers = list(directory.glob(f"{number}-*.start"))
        reports = [marker.with_suffix(".json") for marker in markers]
        if (
            "uncertain" in methods
            or not reports
            or not all(p.exists() for p in reports)
        ):
            uncertain.add(int(number))
            continue
        records = set()
        for report in reports:
            records.update(map(tuple, json.loads(report.read_text())))
        if not records:
            uncertain.add(int(number))
        for location in records:
            lines[location].add(int(number))
    groups = sorted(set(tuple(sorted(ids)) for ids in lines.values()))
    indices = {group: i for i, group in enumerate(groups)}
    rows = [
        [path, line, indices[tuple(sorted(ids))]]
        for (path, line), ids in sorted(lines.items())
    ]
    return dict(always=sorted(uncertain), groups=groups, rows=rows)


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(
        json.dumps(merge(Path(sys.argv[1])), separators=(",", ":")) + "\n"
    )
