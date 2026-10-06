# DATA-03 final validation

Offline synthetic verification, 2026-10-06. Counts overlap with earlier batches
in [closure.md](closure.md) and must not be added as unique test counts.

## Final source-cut/admission controls

```bash
./venv/bin/python -m pytest -q --basetemp=/mnt/luffy-data/data03-final-focused-20261006 tests/test_data03_consumer_cuts.py tests/test_hierarchical_admission.py tests/test_stage1_pit_blocker_fix.py tests/test_stage1_data_provenance_pit.py
```

```text
........................................................................ [ 46%]
........................................................................ [ 93%]
..........                                                               [100%]
154 passed in 21.94s
```

## Readers, alignment, engine gates and outcome backfill

```bash
ulimit -n 8192
./venv/bin/python -m pytest -q --basetemp=/mnt/luffy-data/data03-recovery-readers-20261006 tests/test_outcome_backfill.py tests/test_outcome_24h_upgrade.py tests/test_reference_store.py tests/test_derivatives.py tests/test_features_deriv_extra.py tests/test_stage1_pit_engine_gates.py tests/test_pit_alignment.py tests/test_evaluation_context.py
```

```text
........................................................................ [ 94%]
....                                                                     [100%]
76 passed in 69.75s (0:01:09)
```

## World replay, historical capture and decision sources

```bash
ulimit -n 8192
./venv/bin/python -m pytest -q --basetemp=/mnt/luffy-data/data03-recovery-capture-20261006 tests/test_stage7_replay_world_closure_r2.py tests/test_historical_outcome_capture.py tests/test_decision_sources.py
```

```text
........................................................................ [ 97%]
..                                                                       [100%]
74 passed in 484.64s (0:08:04)
```

The three final batches cover 304 distinct tests. The earlier complete-batch attempts failed on file-descriptor and temporary-filesystem exhaustion; their diagnostics and recovery are documented in closure.md. They are not reported as successful runs.
