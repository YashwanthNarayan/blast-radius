# Blast Radius entry (team Kanyarasi)

Builders Buildathon, RWX Blast Radius track.

For each incoming patch against sqlglot at the pinned commit, `selector.py`
decides which tests must run and packs them onto up to 8 workers:

- `maps.json` holds a per-test coverage map (which sqlglot files each test
  executes) and a static import-dependents map, both built from the pinned
  commit, plus unions for subtest-driven test files.
- A commit whose changed files parse to an identical AST (comments/docstrings
  only) gets an empty selection: nothing runs.
- Otherwise tests are selected by coverage intersection with the changed files
  and everything that transitively imports them, with name-token and fixture
  fallbacks, and a run-everything fallback for anything unrecognized.

Open-source libraries and pre-weekend code used: none beyond the Python
standard library and the starter kit's environment pins. sqlglot itself is
downloaded from its own repository at the pinned commit during the image
build, as the track rules allow.
