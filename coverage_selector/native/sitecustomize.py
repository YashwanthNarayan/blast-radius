"""Trace SQLGlot lines in a validation child from interpreter startup through exit."""

import os

if os.environ.get("BR_CHILD_TEST") and os.environ.get("BR_CHILD_DIR"):
    import atexit
    import json
    import sys
    from pathlib import Path

    root = os.path.realpath(os.environ["BR_REPO"]) + "/"
    events = set()

    def line(code, number):
        path = code.co_filename
        if path.startswith(root + "sqlglot/"):
            events.add((path[len(root) :], number))
        return sys.monitoring.DISABLE

    tool = 4
    sys.monitoring.use_tool_id(tool, "native-child-lines")
    sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, line)
    sys.monitoring.set_events(tool, sys.monitoring.events.LINE)
    destination = Path(os.environ["BR_CHILD_DIR"]) / (
        os.environ["BR_CHILD_TEST"] + "-" + str(os.getpid()) + ".json"
    )
    destination.with_suffix(".start").touch()

    def finish():
        sys.monitoring.set_events(tool, 0)
        destination.write_text(json.dumps(sorted(events), separators=(",", ":")))

    atexit.register(finish)
