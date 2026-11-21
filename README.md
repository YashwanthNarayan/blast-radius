# Blast Radius measured-coverage v2

The root Dockerfile runs `coverage_selector/selector.py` against SQLGlot at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. It decodes the committed maps at build time. Runtime dependencies are limited to the Python standard library.

The selector combines base-run line, type, operand, file-read and native child-process observations with the pinned SQL function registry. Test numbers are checked against the catalog fingerprint. See [coverage collection and local execution](coverage_selector/README.md).
