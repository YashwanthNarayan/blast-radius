"""Observe value-consuming operations on the audited tables of pinned SQLGlot.

Keys and membership drive separate derived tables. The selector only narrows
edits to existing values with a fixed key set.
"""

import ast
import atexit
import json
import os
from pathlib import Path
import sys
import uuid


def install():
    root = Path(os.environ["BR_REPO"]).resolve()
    output = Path(os.environ["BR_KEY_DIR"])
    marker = output / f"{os.getpid()}-{uuid.uuid4().hex}"
    origin = os.environ.get("BR_CHILD_TEST")
    marker.with_suffix(".start").write_text(json.dumps(origin))
    observations = set()
    # Discover actual class declarations and primitive read sites in the base.
    definitions = []
    for folder, attribute in (
        ("dialects", "NUMERIC_ESCAPES"),
        ("parsers", "SHOW_PARSERS"),
    ):
        for path in sorted((root / "sqlglot" / folder).glob("*.py")):
            src = path.read_text()
            if attribute not in src:
                continue

            def visit(body, parents):
                for n in body:
                    if isinstance(n, ast.ClassDef):
                        visit(n.body, (*parents, n.name))
                    if (
                        isinstance(n, ast.Assign)
                        and isinstance(n.value, ast.Dict)
                        and any(
                            isinstance(t, ast.Name) and t.id == attribute
                            for t in n.targets
                        )
                    ):
                        definitions.append(
                            (str(path.relative_to(root)), parents, attribute)
                        )

            visit(ast.parse(src).body, ())
    points = {}
    for rel, needle, attribute in (
        (
            "sqlglot/tokenizer_core.py",
            'spec = self.numeric_escapes.get("0" if is_octal else peek)',
            "NUMERIC_ESCAPES",
        ),
        ("sqlglot/parser.py", 'subparser = parsers[" ".join(this)]', "SHOW_PARSERS"),
    ):
        p = root / rel
        found = [
            i
            for i, line in enumerate(p.read_text().splitlines(), 1)
            if line.strip() == needle
        ]
        if len(found) != (2 if attribute == "NUMERIC_ESCAPES" else 1):
            raise RuntimeError(f"unexpected lookup sites in {rel}")
        points[str(p)] = {i: attribute for i in found}

    def observe(code, line):
        attr = points.get(code.co_filename, {}).get(line)
        if not attr:
            return sys.monitoring.DISABLE
        args = sys._getframe(1).f_locals
        if attr == "NUMERIC_ESCAPES":
            mapping = args["self"].numeric_escapes
            key = "0" if args["is_octal"] else args["peek"]
        else:
            mapping = args["parsers"]
            key = " ".join(args["this"])
        owners = []
        for rel, parents, name in definitions:
            if name != attr:
                continue
            obj = sys.modules.get(rel[:-3].replace("/", "."))
            for part in parents:
                obj = vars(obj).get(part) if obj is not None else None
            table = vars(obj).get(attr) if obj is not None else None
            if table is mapping or (
                attr == "SHOW_PARSERS"
                and isinstance(table, dict)
                and key in table
                and table[key] is mapping.get(key)
            ):
                owners.append(rel)
        for rel in owners or ["*"]:
            observations.add((rel, attr, key, os.environ.get("BR_CHILD_TEST", "*")))

    monitor = sys.monitoring
    monitor.use_tool_id(4, "dictionary key observations")
    monitor.register_callback(4, monitor.events.LINE, observe)
    monitor.set_events(4, monitor.events.LINE)

    def finish():
        monitor.set_events(4, 0)
        marker.with_suffix(".json").write_text(json.dumps(sorted(observations)))

    atexit.register(finish)


if os.environ.get("BR_KEY_DIR"):
    install()
