# Measured coverage selector

The repository-root Dockerfile copies selector.py and both prebuilt coverage maps into /app. The only build download is the pinned SQLGlot source archive. The submission reads the base checkout, the two coverage maps, a fingerprint of the official test-number mapping, and the batch inputs. The runtime uses only the Python standard library.

The selector validates every diff hunk against the base file and compares Python syntax trees to find changed expressions. It unions the tests observed on those source lines, the readers of a changed fixture, and the tests that instantiated affected classes. For supported changes to a comparison operator, it uses the observed relationship between its two operands on the base run to identify tests for which the expression could evaluate differently. Unknown operand relationships are retained. Unsupported patches or changes to Python scope layouts select the whole suite. When the syntax tree is unchanged, observed source-file readers are included. Introspection, order dependence, import-time effects, and unseen workloads are limitations of empirical coverage.

Each candidate set is packed in integer milliseconds using the cheapest of 1 through 8 longest-first worker schedules. Additional tests are placed only into unused capacity. An empty candidate set starts no workers.

## Rebuild the maps offline

Use CPython 3.13, the SQLGlot checkout at eb11aec4be8cb2122afe713a4816662c3f0fb2fa, and the requirements.lock from the track. Install the lock in a clean environment, then set BR_REPO to the absolute path of that checkout and BR_TESTS to the absolute path of the starter's sample/tests.json. Run the following from coverage_selector. The collectors only need the catalog and pristine checkout. Do not patch the checkout while collecting.

    export PYTHONPATH="$BR_REPO" SKIP_INTEGRATION=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OPENBLAS_NUM_THREADS=1
    build_dir=$(mktemp -d)
    for collector in collect_map collect_values; do
      BR_OUT="$build_dir/$collector.native.gz" python "$collector.py"
      BR_POOL=1 BR_OUT="$build_dir/$collector.pool.gz" python "$collector.py" tests/test_optimizer.py tests/test_executor.py
    done
    python merge_maps.py measured.json.gz "$build_dir/collect_map.native.gz" "$build_dir/collect_map.pool.gz"
    python merge_maps.py values.json.gz "$build_dir/collect_values.native.gz" "$build_dir/collect_values.pool.gz"

The second pass attributes process-pool tasks to the originating test, including their initializer and pickle serialization boundaries, by replaying those calls synchronously. The native pass remains part of the map. measured.json.gz contains test sets for source lines, instantiated types and file reads. values.json.gz contains test sets for comparison sites and observed operand relationships such as equal, identical, less, greater or unknown. Neither collector consumes commit patches or failure labels. The two maps together are about half a megabyte.

## Run

Use the starter's make sample workflow with this repository as the Docker build context, or set BR_REPO and BR_DATA and run python coverage_selector/selector.py. BR_DATA should contain tests.json and commits/. Pass stdout to the official score.py. Competition CPU time is machine-dependent.
