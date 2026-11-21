"""Record the pinned expression registry's key-to-class dependencies."""

import json
import os
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(os.environ["BR_REPO"]).resolve()))
    from sqlglot import exp

    rows = {
        key: [cls.__module__, cls.__qualname__]
        for key, cls in exp.FUNCTION_BY_NAME.items()
    }
    target = Path(sys.argv[1])
    target.write_text(json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n")
