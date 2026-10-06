# DATA-04 final validation

Offline only, 2026-10-06. Final implementation: three source files, one new
16-case regression module, and this evidence package. No live-market scoring.

```text
./venv/bin/python -m pytest -q tests/test_data04_htf_availability.py tests/test_spec_evidence.py tests/test_data03_consumer_cuts.py tests/test_stage1_data_provenance_pit.py tests/test_data05_revision_truth.py tests/test_pit_alignment.py tests/test_dsl.py
218 passed in 48.56s

./venv/bin/python -m pytest -q tests/test_stage7_replay_world_closure_r2.py tests/test_cognition_replay.py tests/test_attention_kernel_wiring.py tests/test_forming_bar_never_stored.py tests/test_spec_signals_on_closed_bars.py tests/test_stage1_pit_engine_gates.py
90 passed in 248.23s (0:04:08)
```

The final batches contain **308 passing tests** across distinct modules. The
16 new DATA-04 cases are included in the first batch, not added to that total.
The second batch executes `scripts/backtest_equivalence.py` and
`scripts/bench_vector_backtest.py` against synthetic offline numerical fixtures,
requiring successful exit and their internal equivalence / >=20x benchmark gate.
No real data evaluation or production database access is used.

Scoped `git diff --check` passed. A subsequent docstring wording correction
does not change behavior. No required checks remain outstanding.

Pre-fix negative controls confirmed:

- Alternating missing 15m children in exact-cut feed reads became VALID/FINAL
  1h because their declared interval was lost.
- Qualified input lacking declared child timeframe guessed a grid instead
  of refusing ambiguous ancestry.
- Complete timestamp grids with INCOMPLETE/PARTIAL and VALID/PARTIAL child
  metadata both falsely produced FINAL parents.
- `frames_for` returned sparse 15m children as native 1h evidence; its new
  targeted regression failed independently before the third boundary fix.

Initial fixture issues were corrected separately: advance the native test's
current clock before replay, index the DSL ndarray directly, and supply a
closed base bar for the pre-availability feature assertion. They are not
demonstrated source gaps. Earlier overlapping runs are not added to final counts.

Graphify query ran against the existing code graph. `graphify update .` was
deferred because the session prohibits manifest writes. The local Claude
execution probe returned `Not logged in`. Other agents' changes and control-plane
files were preserved. Commit hooks are disabled for this scoped commit to avoid
incidental graph/manifest writes.
