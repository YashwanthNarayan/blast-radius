"""Replay JSONL selections against local labels, including an oracle reference."""
import argparse
import json
from pathlib import Path
from selector import schedule

def main():
    parser = argparse.ArgumentParser()
    for name in ("tests", "labels", "predictions"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    tests = {t["number"]: t for t in json.loads(args.tests.read_bytes())["tests"]}
    labels = json.loads(args.labels.read_bytes())
    seconds = {n: t["seconds"] for n, t in tests.items()}
    def cost(workers):
        return max((sum(seconds[n] for n in w) for w in workers), default=0) + 2*len(workers)
    seen = set()
    total = oracle = misses = 0
    for line in args.predictions.read_text().splitlines():
        row = json.loads(line)
        commit, bins = row["commit"], row["workers"]
        ids = [n for w in bins for n in w]
        if (commit in seen or len(bins)>8 or set(ids)-tests.keys() or
                len(ids)!=len(set(ids)) or any(not w for w in bins)):
            raise ValueError(row)
        seen.add(commit)
        failed = set(labels[commit])
        missed = sorted(failed - set(ids))
        reference = cost(schedule([tests[n] for n in sorted(failed)], []))
        actual = cost(bins) + 300*bool(missed)
        print(json.dumps(dict(commit=commit, simulated=actual, failure_only_lpt=reference,
                              missed=missed, excess=actual-reference)))
        total += actual
        oracle += reference
        misses += bool(missed)
    if seen != labels.keys():
        raise ValueError("Missing commits")
    print(json.dumps(dict(simulated=total, failure_only_lpt=oracle, missed_commits=misses)))
if __name__ == "__main__":
    main()
