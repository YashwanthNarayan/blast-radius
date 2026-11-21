"""Union observations from native collection and instrumented pool workloads."""

import gzip, json, sys
from collections import defaultdict


def merge(paths, target):
    mapping = defaultdict(set)
    complete = set()
    for p in paths:
        with gzip.open(p) as f:
            data = json.load(f)
        for row in data[1]:
            mapping[tuple(row[:-1])].update(data[0][row[-1]])
        if len(data) > 2:
            complete.update(data[2])
    groups = {}
    rows = []
    for key, values in sorted(mapping.items()):
        i = groups.setdefault(tuple(sorted(values)), len(groups))
        rows.append([*key, i])
    data = [list(groups), rows]
    if complete:
        data.append(sorted(complete))
    with gzip.open(target, "wt") as f:
        json.dump(data, f, separators=(",", ":"))


if __name__ == "__main__":
    merge(sys.argv[2:], sys.argv[1])
