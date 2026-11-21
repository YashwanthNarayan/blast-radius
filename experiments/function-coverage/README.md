# Blast Radius selector experiment
Build the map offline on the pristine pinned sqlglot tree, using the starter image's exact Python and requirements.lock, then commit the compressed map. The isolated per-test builder favors attribution quality over build speed. Use one job for timing-sensitive tests. Its full build has not been timed. It is designed for precomputation outside the image build window.

    python build_map.py --repo /opt/repo --tests /kit/tests.json --nodes /kit/nodes.json --out functions.json.gz

nodes.json is an identity join, e.g. {"12":"tests/test_example.py::TestExample::test_case"}. Use the starter kit's ID mapping. Never enumerate IDs by pytest order. If tests.json already contains nodeid, omit --nodes. Missing identities or unreliable traces become always-include tests. Check the printed count. A map that marks every test always-include provides no saving. Ensure the pinned checkout takes precedence over installed sqlglot. Compare selected and full-suite results for order-sensitive tests. Only validated reproducible traces are evidence for pruning.

Copy selector.py and functions.json.gz to /app in the pinned Dockerfile and retain /opt/repo. Command is python /app/selector.py. Set BR_DATA, BR_MAP or BR_REPO to override paths locally. Runtime uses only the standard library. This is a standalone conservative experimental arm, not v6. Unsupported changes select the full suite; compare its fallback cost explicitly before replacing v6. Validate image stdout, CPU, wall time, and map/repo size.

    python selector.py > predictions.jsonl
    python replay.py /kit/tests.json labels.json predictions.jsonl

labels.json maps commit IDs to lists of every failing kit test number. The comparison schedule lists only labeled failures and uses LPT; it is a reference, not a proof of optimal packing. Never ship labels, replay outputs, practice-answer caches, or synthetic-mutant outcomes in the competition image. Use an explicit Docker COPY list.

Function dependency maps record executed calls across collection, setup, call and teardown in an isolated base interpreter for each test. The fast path accepts regular same-path source modifications only when every changed file contains a supported change to named leaf function code and enclosing compiled code stays equal. It deliberately declines equivalent bytecode, changed enclosing line tables, module/class changes, and uncertain patch metadata. Passing the filter is still empirical: source/bytecode introspection, native extensions, indirect filesystem access, nondeterminism, shared test state, and incomplete instrumentation can invalidate non-execution evidence. The trace suppresses known process events and checks for preloaded sqlglot; neither check proves complete attribution. Compare against the pinned image including memory/timeout tests. Do not use offline profiled wall time to price decision runs.

The scheduler chooses the best longest-processing-time (LPT) schedule across 1–8 workers. It freezes that schedule and fills remaining slack with optional tests cheapest first. The free-fill policy maximizes count for the implemented greedy rule; it does not estimate failure probabilities. The full-suite fallback is prepacked once per run.
