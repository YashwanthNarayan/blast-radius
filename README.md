# Blast Radius entry (team Kanyarasi)

Builders Buildathon, RWX Blast Radius track.

For each incoming patch against sqlglot at the pinned commit, `selector.py`
decides which tests must run and packs them onto up to 8 workers:

- `linemap.json` holds line-level coverage: for every source line of the
  pinned sqlglot commit, the tests that execute it (run-length compressed),
  plus each file's module/class-level (import-time) line ranges. Built once
  from the pinned commit with coverage.py.
- `maps.json` holds file-level coverage, a static import-dependents map and
  test-file membership, used for fixture/name fallbacks.
- Selection for a patch: tests executing the changed lines, widened to the
  whole importing set when a hunk touches import-time lines, and to whole
  test files for subtest-driven files (cross-subtest state). Changes to
  fixtures select the tests that name them; unknown shapes select everything.
- A patch whose changed files parse to an identical AST with docstrings
  stripped (comment/docstring-only edit) selects nothing.
- The selector runs two small known tests once per batch as a self-check of
  the sandbox; results go to stderr and never affect selections.

Open-source libraries and pre-weekend code used: coverage.py (map building),
pytest/pytest-xdist/pytest-subtests/duckdb (sandbox self-check), sqlglot
itself downloaded from its own repository at the pinned commit during the
image build, as the track rules allow.
