# Review08 Portfolio group A

Reviewed all 10 assigned files in full (2,095 baseline lines), including all three complete test modules. Exact file evidence/hashes/classifications are in `group-a.json`. Seven KEEP, three SIMPLIFY; no file removal or merge justified.

## Useful connected capabilities

Kernel `cycle` calls `_portfolio_checkpoint` (`trader/kernel.py:1463,1490`), which refreshes venue truth through existing Kernel ownership and calls `current.checkpoint`. Current freeze independently retains whole-book holdings and admitted market observations; candidate omission cannot erase holdings. Current sources flow through existing persisted publication adapter, Factory/capacity/lineage, common-factor and Learning readers. Consumer atomically retains cut evaluations, event checkpoints and prior input state in its separate private ledger. Exact duplicate/restart paths verify retained replay. Metrics reuse existing RiskManager formulas while keeping mark exposure, current equity and actual initial margin independent. Typed intents reach isolated Risk evaluation and stop there. Learning dynamically verifies retained Risk records (`capture.py:393-395`) and receives allocation evidence at checkpoint.

These are IMPLEMENTED AND CONNECTED source contracts. Tests exercise synthetic normal persisted publications through actual reader/allocator/intent/Risk and restart. They establish no live alpha, economically optimal real allocation, production current-source completeness, or execution integration. Current checkpoint is deliberately proposal-only; normal execution remains independent. The SDD Stage6 exit criterion of whole-book competition influencing actual trading therefore remains incomplete.

## Confirmed defect corrected

`runtime.replay` verified nested event/arithmetic but accepted changed outer schema, cut ID, candidate hash, checkpoint event list, execution-routing/order count and replay status. Seven new deterministic tamper cases each failed before the correction because no error was raised. The fix derives outer bindings from the already-verified event/current candidate set and checks established proposal-only metadata. No authority, economics, allocation, Risk limit, persistence format or identity changed. Earliest runtime history `640a5f2` and inventory source `8d57e8f` already emitted all these fields with the same semantics. Duplicate `True` remains supported. Existing stored bodies need no migration.

`current.py` module documentation incorrectly claimed no trading-journal writes, although checkpoint explicitly delivers allocation evidence through Learning capture. Documentation now distinguishes read-only freeze, separate ledger and existing journal evidence delivery.

## Missing intelligence and costs

`reoptimization.MATERIALITY_MODELS` is empty. Exact new context/opportunity, open/exit and known discrete control/lifecycle events operate. Numerical confidence/world/relationship/capital changes remain context-only with MATERIALITY_POLICY_UNAVAILABLE. Registering models or forcing these triggers would be policy work and is deferred. Normal economics/allocation registry completeness belongs to the other Portfolio group; TEST-ONLY models prove routing, not production calibration. Profiler remains an operator utility, never executed: it ends in Kernel.main and can run providers and writers.

Every new cut appends an evaluation even without allocation; complete previous/current source trees plus independent replay can be expensive. Existing >64KiB shared evidence codec is reused. Source publications cap at64 files/directory and8MiB/file; each candidate can rescan the economic directory. Current source read uses existing bounded source readers,65-row sentinels, complete-held-book reconciliation and existing freshness contracts. No new CPU/storage benchmark or exclusive-resource attribution was performed. Simplification should consolidate repeated decoding/preparation only while preserving byte-exact receipts and concurrent-state guards.

## Lifecycle and integration boundaries

Reused the prior operational lifecycle report rather than re-reviewing its implementation. Portfolio cleanup in offline `147fae0` is not assumed integrated/deployed. It preserves logical evaluations/checkpoints and only retires reconstructed unreferenced codec representation under complete consumer checks. No cleanup, data deletion, DB inspection, profile run, provider call, restart or CI occurred here. Lifecycle package integration remains separately deferred; no missing cleanup was silently installed.

## Verification

Baseline assigned modules:73 passed in51.96s. New seven tamper cases:7 failed before fix (accepted tampered envelope). Focused corrected tamper + duplicate + separate-process + context replay:10 passed in5.87s. Final assigned modules:80 passed in48.45s; commands and overlap recorded in `group-a-tests.json`, compact captured output in `group-a-pytest.log`. Tests use installed venv and temporary deterministic stores. `git diff --check` for changed owned source/tests passed.
