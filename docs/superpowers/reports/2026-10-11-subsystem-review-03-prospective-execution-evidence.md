# Subsystem review 03: execution_observation (Prospective Execution Evidence)

Date 2026-10-11, HEAD 8d57e8f, agent 2 of 3. Scope: six files under `trader/observability/`.
Labels: SC = source contract (read in source), VT = verified by test.

## Part A: necessity and connections

**Decision: KEEP the subsystem (SIMPLIFY the dormant tail).** It is observation-only evidence for the
owner's execution-cost question (slippage, fees, funding, late-book labelling). No collector computes
size, risk, thresholds or admission. The recorded stage-5 window has expired (deadline_ms 1791121695246,
`data/stage5-public/status.json` = EXTERNAL_BLOCKED/WINDOW_EXPIRED), so the stage-5 public producer is
dormant by design, but `ObservedExchange`/`Capture`/`environment()` are live on the order path whenever an
Executor is built.

Entry points and wiring (SC):
- `Executor.__init__` (engine/executor.py:76) wraps the exchange in `ObservedExchange` when `for_journal()` returns a Capture. This is the only collector on the order path.
- `PaperExecutor` (engine/paper.py:92) uses `for_journal` capture; `kernel._paper_funding_recorder` -> `Capture.funding`.
- `kernel.boot` (kernel.py:380) calls `stage5_activation.start(kernel)` unguarded.
- `kernel._paper_executor` uses `prospective_execution.environment` for PAPER venue_environment.
- `engine/paper_cost_evidence.py:14` imports `funding_events.METHOD/validate_no_crossing`; `learning/capture.py:420` uses `funding_events.replay`.
- `persistence/backup.py:109-110` and `dashboard/owner_reads.py:1044` only inventory the two DB files.
- No production caller at all for: `execution_calibration.dataset/inventory/book_walk/exact_orders`, `depth_evidence.connect/store/reject/storage`, `prospective_execution.replay_observations`. `depth_evidence` docstring cites `scripts/capacity_depth_shadow.py`, which does not exist.

Connections: Kernel (boot hook, paper funding thread, Executor wrap); Safety (reads control state string for the
startup record, honours `<db>.STOP` file and `stage5-public/STOP`; no Safety API calls); Portfolio (none);
Market Data (public unauthenticated `/fapi/v1/depth`, `/fapi/v1/fundingRate` via `registry_provider.urllib_fetch`);
Learning (`learning/capture.py` replays funding receipts; paper_cost_evidence validates no-crossing source);
Journal (reads `versioned_paper_trades`; `execution_calibration` reads `trade_legs/trade_fills` via an injected query;
`stage5_activation.start` makes the journal run `factory_handoff.ensure`, `capacity.ensure`, `SNAPSHOT_DDL`, paper schema).

Tables written (all outside luffy.db except boot DDL): `observations` (append-only, UNIQUE observation_id, update/delete
triggers) in `data/execution-evidence.db` and `data/stage5-public/public.db`; `depth_observations` / `depth_rejections`
only via the uncalled `depth_evidence.connect` (caller-supplied shadow DB). Reads: `versioned_paper_trades`,
`trade_legs`, `trade_fills`. Live state (read-only mode=ro): execution-evidence.db holds 26 `capture_startup`
rows only (no executed order ever observed); public.db holds 7541 depth, 1510 funding, 4 failure rows.

Overlap/duplication:
- vs `engine/accounting.py`: `safe_fill` is imported, not duplicated. But `ObservedExchange` writes `venue_fill`/`reported_order_fee` to a second store while `trade_accounting`/`booking` persist the authoritative `trade_fills` in luffy.db; the observer copy is only consumed by the uncalled `replay_observations`. Two parallel fill records, one without consumers.
- vs `observability/accounting.py` (another agent's off-path observer): no shared code; both derive from venue fills but the observer there is the account-level path. No functional conflict.
- Among the six: `digest()` wrapper is copy-pasted in `execution_calibration` and `funding_events`; `execution_shadow` imports `positive` from `execution_calibration` only for a 5-line helper (pulls in `engine.execution_evidence`); `stage5_activation.run` re-implements the depth+capture flow that `execution_shadow.capture` already does (the file says supplemental).
- Two storage-bound constants disagree: stage5 `MAX_BYTES` 64 MiB vs Capture `MAX_STORAGE_BYTES` 256 MiB (intentional per activation file, but two status files: `status.json` and `public.status.json`).

Influence on trading: none by design. Order/exit calls return the wrapped exchange result unchanged; the only trading-path risk is an observer exception (see issues). `grants='OBSERVATION_ONLY'`, `authority=` labels present (SC).

## Part B: per-file table

PATH | LINES | PURPOSE | FUNCTIONS/CLASSES | CALLERS | CONSUMERS | SIDE_EFFECTS | NECESSITY | ISSUE | DECISION | FIX_STATUS
---|---|---|---|---|---|---|---|---|---|---
observability/depth_evidence.py | 200 | validate/store public depth snapshot | request_url, observe, _side, _level, DDL, connect, store, reject, storage, DepthRejected | execution_shadow, stage5_activation, execution_calibration | linked_book / supplemental_public_depth bodies | none in observe; sqlite DDL only via connect | Required (validator); store/reject/storage dead | connect/store/reject/storage have no caller; cited runner script missing | SIMPLIFY (drop or relocate dead storage helpers) | TEST_ONLY (rejection contracts tested); removal PROPOSED_ONLY
observability/execution_calibration.py | 138 | static book walk, exact-order calibration dataset, inventory | positive, book_walk, exact_orders, dataset, inventory, digest | execution_shadow, prospective_execution (only `positive`) | none (no caller of walk/dataset/inventory) | read-only queries | Largely dormant research primitive | `positive` misplaced here; dataset/inventory/book_walk dead; mid-file import; requested_fill_fraction would TypeError on None fill_qty (guarded by upstream) | SIMPLIFY / MERGE_CANDIDATE (move `positive` to depth_evidence) | PROPOSED_ONLY
observability/execution_shadow.py | 44 | one public depth capture linked to an intent | capture | prospective_execution.Capture.book | linked_book records | one public GET | Needed (linked-book path) | tiny; depends on calibration only for `positive` | MERGE_CANDIDATE into depth_evidence or prospective_execution | PROPOSED_ONLY
observability/funding_events.py | 152 | bounded public funding lookup, replay, no-crossing proof | url, replay, lookup, no_crossing_source, validate_no_crossing | prospective_execution, stage5_activation, paper_cost_evidence, learning/capture | paper cost protocol, funding receipts | public GET only | Required (cost evidence authority) | `lookup` raw KeyError/InvalidOperation on malformed rows (callers catch broadly); environment hard-wired production | KEEP | NO_CHANGE_REQUIRED
observability/prospective_execution.py | 359 | append-only evidence store, bounded worker, exchange observer, replay | environment, PublicWorker, Capture, ObservedExchange, for_journal, replay_observations | executor, paper, kernel, stage5_activation | backup/owner_reads (inventory only) | writes execution-evidence.db synchronously on order thread; daemon thread | Required (live wrapper) | two observer-raise paths on order/fill path (fixed); stale EXTERNAL_BLOCKED status never cleared; sync sqlite write + DDL per record; `replay_observations` uncalled | KEEP (SIMPLIFY later) | IMPLEMENTED_OFFLINE
observability/stage5_activation.py | 117 | opt-in 72h public BTC/ETH depth+funding collector and startup | read_window, run, start | kernel.boot | public.db / status.json | boot-time DDL in luffy.db, spawns 2 threads, writes status files | Dormant (window expired) | `start()` unguarded in `kernel.boot`; invalid/future window file aborts boot; still ensures journal schemas and starts paper-funding thread after expiry; stop status stale on storage bound | SIMPLIFY | PROPOSED_ONLY

## Part C: contract verification

- Observer isolation: `Capture.write` catches all exceptions (SC); `submit` swallows worker errors (SC); worker is daemon, queue bounded 32 (SC). Gaps found and fixed: `create_order` raised AttributeError on a non-dict `reference` before the order was sent; `fetch_my_trades` raised from `safe_fill` on malformed rows after the venue call. Both now VT. Remaining: `stage5_activation.start` can raise into `kernel.boot` (SC, proposed).
- Idempotency: observation_id = sha256(kind+canonical body); INSERT OR IGNORE; immutability triggers (VT). Depth `UNIQUE(symbol,response_sha256)` (SC). Repeated fills dedupe; repeated startup/funding receipts do not (time fields), which is acceptable growth.
- Restart: window file fixed, restart never extends it (SC); expired window writes EXTERNAL_BLOCKED and returns (SC, live status file confirms). Capture reopens the append-only DB.
- Point-in-time / NaN: canonical JSON uses `allow_nan=False`, so NaN bodies are refused, not zeroed (VT); depth rejects NaN/zero/crossed/stale (VT); funding replay refuses boundary/hash/chain/environment tampering (VT); missing mark -> `position_notional=None`, status UNAVAILABLE (SC); late books carry `event_to_received_ms` and "NOT_EXACT_EVENT_TIME_BOOK" (SC).
- PAPER vs DEMO: `environment()` derived from public and private API host, mismatched hosts return None (SC); `Capture.funding` accepts only `production` frozen exposure and funding lookup is production-only (SC); `book` marks non-production/demo as ENVIRONMENT_UNPROVEN (SC). Demo books are fetched from demo host and labelled `environment=demo`.
- Estimates vs actual: book walk labelled STATIC_BOOK_QUOTE / NOT_REALIZED_SLIPPAGE; fees `NOT_ACCOUNT_COMMISSION_AUTHORITY`; funding `NOT_A_FINALIZED_COST_RECEIPT` (SC).
- Secrets: public GETs without auth; stored order fields are whitelisted; error text capped at 160 chars in stage5 failure records (SC). `Capture.blocked` logs only reason codes. No `.env` access.

## Issues

Confirmed defects (fixed):
1. prospective_execution.py `create_order` (was :237-238): `ctx['reference']` not a dict -> AttributeError aborting order submission. Fixed with isinstance guard. VT.
2. prospective_execution.py `fetch_my_trades` (was :290-292): unguarded `safe_fill` could raise through to the caller. Fixed per-row. VT.

Possible improvements (proposed only): guard or relocate `stage5_activation.start` call in kernel.boot (kernel.py:380, outside scope); stop `start()` from running journal DDL and the paper-funding thread once the window has expired; clear stale `.status.json` after recovery; move `positive` to depth_evidence and merge execution_shadow; delete or relocate dead depth storage helpers and `execution_calibration` dataset/inventory unless a consumer is planned; make observer writes fully async to remove synchronous sqlite+DDL from the order thread; wire or remove `replay_observations`.

## Part D: changes and tests

Modified: `trader/observability/prospective_execution.py` (two small guards, no semantic/authority change).
Added: `tests/test_review03_prospective_execution.py` (11 tests; the two isolation tests failed before the fix, pass after).
Baseline (before edit): 7 related test files, 309 passed, 1 failed:
`tests/test_strategy_factory_handoff.py::test_factory_writes_no_trading_state_and_has_no_trading_caller` (owner_reads/_table_exists reference; unrelated to these six files).
After: new file + stage1_entry_authority + whole_trade_accounting + port02_economics_authority = 149 passed.
No DB writes, no network, no config changes; nothing staged or committed.
