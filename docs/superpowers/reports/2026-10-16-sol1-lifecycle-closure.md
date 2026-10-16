# SOL1 strategy lifecycle — final closure (offline)

Base: `56325a6` (lifecycle). Branch `sol1-closure`. No production integration, venue calls, Kernel/Dashboard starts, real trading or live enablement.

## Owner decisions applied
1. Paper commission/slippage reuse `quant_contract.cost_model` (the version's Backtest PASS now records `cost_config` + `cost_model`; `cost_config` must digest to `run.config_digest`). Funding stays only the existing no-crossing evidence.
2. The approved per-version allocation is enforced through Risk, EntryAuthority and Execution.
3. No new statistical, calendar, drawdown or win-rate thresholds.
4. Live execution remains disabled.

## Paper cost contract
Method `LUFFY_BACKTEST_COST_MODEL_ESTIMATE_V1` (`paper-cost-model-estimate.v1`), labelled `ESTIMATED_MODEL_NOT_VENUE_ACTUAL`. Per leg: taker fee `taker_fee_pct/100` x executable notional; slippage as an adverse executable price `fill ± slippage_atr_frac x entry_ATR` (ATR from the trade's own frozen exit state), entering once. The model's flat funding fallback is not used (no double counting). Source records model, qualification id, ATR, assumptions and per-leg arithmetic; `verify` cross-checks them against the version's PASS and the trade. Historical receipts and legacy PASSes (no `cost_model`) are never rewritten and give no estimate. Missing/ambiguous evidence is refused; a trade whose funding cannot be proven (crossing/boundary) gets an UNAVAILABLE receipt and is excluded and reported (`cost_evidence_refused`), not counted toward ten (selection-bias caveat). Estimates never establish live economics or capacity.

## Ten-trade proof
`tests/test_sol1_paper_cost.py` (23): real PaperExecutor, ten genuine trades -> SATISFIED on net-with-complete-cost basis; nine insufficient; win-rate fail; costs flip gross pass to net fail; pending blocks; fetch failure retried; stored receipt replays identically in a fresh DB; Kernel `_manage_paper` completes costs and reaches SATISFIED.

## Allocation ceiling
Allocation is a fraction in (0,1] of the size Risk computes (Governor ACTIVE/REACTIVATED event, <= ceiling); it can only shrink. EntryAuthority records it and re-derives it at both submission checks (`allocation_binding_differs`, `allocation_ceiling_exceeded`); Executor quantization may only shrink. Kernel scales size/risk and denies below min notional. `tests/test_sol1_allocation_ceiling.py` (15), `tests/test_sol1_live_fence.py` (14).

## Test-count reconciliation
372 = `tests/test_*.py` at `56325a6`; 376 = those plus 4 fixture modules collected as "no tests ran". The earlier candidate log had 377 entries (stale entry for a deleted file plus the new file) and three files twice from reruns. Now 375 `test_*.py`, 392 `tests/*.py`.

## Regression
Per-file pytest, baseline `56325a6` vs final head: 389/392 files; 252 failing/erroring tests in both, identical sets, 0 new, 0 fixed; 3 new files all pass (52 tests). Two intermediate regressions found and fixed (frozen-reader query form; registry/boundary tests updated for the approved adapter). Inherited failures are environmental or pre-existing, e.g. missing `fastapi` module (dashboard), artifact files absent from the clone (`exit-geometry` protocol JSON), and unrelated assertion/TypeError failures in first-boot, spec-arm, owner-interface tests.

## Disk (read-only)
`/mnt/luffy-data` 96% used (~4-5 GB free, down from 5.0 GB during this task as the shared test-tmp grew); `/mnt/luffy-recovery` ~45 GB free. `test-tmp/sarmad` holds ~8-11 GB of foreign Oct 14-15 pytest basetemps. A foreign `gui01-baseline` pytest ran during the zero-free event; cause not proven. No foreign files touched.

## Remaining gaps
- Estimates are paper estimates, not venue economics; live venue-cost/capacity evidence remains a separate live-readiness refusal. No production capacity producer records receipts, so live stays HELD (`version_capacity_not_established`).
- Funding-crossing/boundary trades cannot be priced and are excluded.
- Futures cost completion uses public funding-history GETs (bounded, throttled; injected in tests).
- Nothing writes `record_first_live_deployment`.
- Trailing stops unsupported; `graphify` not run.
