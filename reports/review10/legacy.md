# Review 10 — legacy partition

Reviewed all 19 canonical paths: 18 present files plus full historical source of `trader/brain/strategist.py` at inventory revision `8d57e8f23343505660a07c1a8aa6218279121de9`. The latter was already removed by `2074b92290273578174405b80d9d36f71e22e438`; Review 10 does not count it as a new removal. Baseline HEAD: `4deca5c275c30c2c59c227b5170bab651cf777fe`. Exact hashes, purposes, callers/readers/writers, costs and classifications are in `legacy-files.json`: KEEP 14 / SIMPLIFY 5 / MERGE 0 / REMOVE 0.

## Functionality and authority

The current hourly Kernel tick invokes `promotion.evaluate_population`, which commits only descriptive `stats_json` for nonretired rows. Factory probation consumes `stats_of` only after finite numeric gross-trade filtering and verified net-cost transformation. Frozen retired stats and prior model bytes remain untouched. Grandfather compatibility requires an exact immutable owner receipt, definition hash and pre-Factory issuance; old creation dates are insufficient.

Calibration/EWA/meta fitting and direct persistence refuse before label reads/writes under `learning.foundation.refuse_legacy_learning`; current orchestrator does not apply these models even though retained config flags say enabled. These are intentional authority boundaries, not missing active-learning defects. Added current-status module documentation. Kernel `_strategist_knowledge` still supplies doctrine/coverage to current candidate work; it does not resurrect the removed Strategist.

Legacy backtest/result/resample helpers still support evaluator compatibility, vector results, offline grid work, evidence loading and numerical equivalence. Retained Pine manifests support the separate TV harness. The family proxy in `brain/tv.py` is an explicit CLI advisory path, not exact-genome validation or a production lifecycle scheduler. Static scarcity alone did not justify deleting replay, source or test artifacts. SDD §§21, 28.4 and 35.11–35.13 checked against implemented current authority.

## Confirmed defects and bounded corrections

1. `TV.validate_population` described an unchanged advisory population but retired supposed duplicates via raw SQL, bypassing Governor lifecycle. Its key compared serialized params only, missed equivalent JSON formatting and conflated different regime/market definitions. Removed retirement mutation; emit `duplicate_advisory`, retain the `duplicates` result list, and compare parsed params plus sorted regime/market declarations. Exact row fields, clocks, reasons and restart state remain unchanged. No historical events or grants rewritten.
2. Legacy backtest initialized its drawdown peak at zero, so an initial/all-loss account path could report zero maximum drawdown and evade the drawdown test. Initialize the high-water mark from supplied equity. A synthetic first stop loss of 100 on 2000 now reports 5% and fails a 4% DD gate. Trades/wins/P&L remain equal to vector simulation under existing synthetic engine gates. Current vector behavior is unchanged.

Root-authorized dependency-only documentation corrections: `scripts/validate_population.py` now describes advisory duplicate reporting and Governor lifecycle; `trader/strategy/vector_backtest.py` describes the corrected legacy historical comparison. No behavior changed outside canonical legacy ownership.

No files removed/merged; no replacement machinery, trading authority, provider calls, services, production databases or integration changed. Historical gate values, unknown-data failures, model bytes and Pine proof artifacts remain available. Old grid winner PF is explicitly selection-biased; proxy family evidence and Pine lint are not exact strategy admission proof. Some retained family smoke assertions are weak (including a vacuous neutral-RSI assertion); no unsupported claim of comprehensive signal correctness is made. Repeated stats refresh and old per-bar evaluators are possible future optimization candidates, without production resource measurements.

## Verification and exact commands

All commands ran in the isolated Review 10 worktree using `./venv/bin/python` and temporary/synthetic fixtures.

- Before-fix source proof: an isolated Python process imported `trader.brain.tv` and `trader.strategy.backtest`, executed each `git show HEAD:<module>.py` body into that process's module namespace, then `pytest.main(['tests/test_review10_legacy_boundaries.py','-q'])`. Shared files were never swapped. **2 expected failures**: incorrect duplicate identity; first-loss DD 0 instead of 5. Log: `legacy-failing-before.txt`.
- `./venv/bin/python -m pytest tests/test_review10_legacy_boundaries.py -q` — **11 passed**, `legacy-remediation-final.txt`.
- `./venv/bin/python -m pytest tests/test_stage1_pit_engine_gates.py -q -s` — **2 passed**, `legacy-engine-gates.txt`. Existing fixtures replace `DataFeed` and `evidence.load_frames` with a 1200-bar synthetic frame and run the actual `scripts/backtest_equivalence.py` and `scripts/bench_vector_backtest.py` entry points. ENGINE EQUIVALENCE PASS for all five founding genomes (rotation emits no signals; four other families exercise trades); BENCH PASS **145.3x** (legacy 16.16s / vector 0.111s, five runs). This is numerical/offline validation, not market-performance evidence or a production benchmark.
- `./venv/bin/python -m pytest tests/test_lifecycle_kind_routing.py tests/test_new_families.py tests/test_phase1_brain.py tests/test_phase2.py tests/test_pine_forge.py tests/test_promotion_rules.py tests/test_spec_observation_only.py tests/test_transition_provenance.py tests/test_legacy_learning_replay_gate.py tests/test_universal_strategy_authority.py tests/test_vector_backtest.py tests/test_single_creation_path.py -q` — **101 passed**, `legacy-consumer-tests.txt`. Includes all eight canonical legacy test files plus four affected authority/creation/vector consumers.
- `git diff --check` — passed.

Final verification totals **114 unique passing test cases** in this partition's final command sets (101 + 11 new + 2 gate cases). `legacy-remediation-tests.txt` retains the initial combined run: gates passed at 134.0x, but the first new TV fixture omitted creation/state timestamps, causing restart initialization to modify that fixture. Fixture corrected to explicit timestamps; the final 2-test run passed. The earlier failed fixture run is not counted as a final pass.

## Changed files

Owned behavior: `trader/brain/tv.py`, `trader/strategy/backtest.py`.
Owned current-status docs: `trader/agents/calibration.py`, `trader/agents/weights_online.py`, `trader/brain/meta_label.py`.
Added regression: `tests/test_review10_legacy_boundaries.py`.
Dependency docs only: `scripts/validate_population.py`, `trader/strategy/vector_backtest.py`.
Report artifacts: `reports/review10/legacy*`.

No owner policy decision required. Consolidated integration must retain the historical Strategist removal, current Governor ownership, refused adaptive writers and revised advisory event meaning; no activation is requested. Commits are delegated to the root reviewer for consolidated offline preservation.

Independent follow-up found schema-permitted NULL/JSON-null regime and market declarations would crash the initial normalization. The bounded guard now declines duplicate identity for missing/malformed params or declaration lists while continuing the existing family advisory; unknown is never normalized to an empty declaration. Nine parametrized cases (SQL/JSON nulls, wrong types, invalid JSON and empty market declaration) preserve every strategy row and still evaluate both advisory subjects. The pre-guard in-process module-source probe failed all nine cases (`legacy-null-identity-failing-before.txt`); final focused module: 11 passed. No broad suite repeated. Skill `luffy-review` became available during this follow-up and was read; current Review 10 artifacts remain active review outputs, not frozen historical evidence.
