# Subsystem review 03 - accounting_observer (agent 3)

File: `trader/observability/accounting.py` (231 lines after fix). HEAD 8d57e8f. Labels: [SRC] source contract, [TEST] verified by test.

## Part A
- Necessity: NEEDED while whole-trade accounting evidence and verified-outcome delivery are wanted. It is the only scheduled consumer of `engine/trade_accounting.capture` (venue fill/funding history) and the only caller of `learning.producers.deliver_accounting`. Not required by the trading loop.
- Entry points: CLI `python -m trader.observability.accounting --once --enable` (run from `scripts/watchdog.sh:76`, only if `data/accounting.enabled` exists, `timeout 50s`). Functions: `step` (queue scan -> capture -> finish -> deliver), `finish`, `initialize`, `connect`, `deliver_completed`, `main`, `Deadline`.
- Reads: journal `trade_accounting_bookings` (cursor scan), `trades` (overlap query), via `booking.export` (all mode=ro). Venue: demo `fapiPrivate*` reads via `trade_accounting.capture` through lazily-built `make_exchange('futures', demo=True)`.
- Writes: `data/accounting-worker/queue.db` (meta/jobs/attempts/learning_deliveries), `<uuid>.json` artifacts (open 'xb', fsync, never overwritten), `health.json`, `worker.lock`. Journal: ONLY through `producers.deliver_accounting` (mode=rw; `capture.ensure` + `safely` write learning_* tables / learning_capture_failures). No trades/orders/risk tables.
- Connections: Kernel - none (`kernel.py` does not import it; asserted by test [TEST]). Safety/Portfolio - none; no import of risk/executor/exits/protective. Learning - delivery via producers (above). Journal - read bookings, rw only learning tables. Backup (`persistence/backup.py:111,147,429`) and dashboard (`owner_reads`, `economics`) read the bookings table / queue evidence, not this module's API.
- Overlap: `engine/booking.py` writes receipts at trade time (hot path, owned by agent 1); `engine/trade_accounting.py` does capture/replay/assess (the actual accounting logic). This file is only a durable queue/scheduler around them - no duplicated accounting arithmetic. Overlap with Learning delivery: `deliver_completed` duplicates a minimal retry/terminal ledger (`learning_deliveries`) next to `learning_capture_failures`; idempotency is in `capture.safely` key `verified-trade:<sha256>` [SRC].
- Off-path: YES for orders/risk [SRC + TEST `test_no_kernel_integration_or_order_write_api`]: no order API, no kernel import, demo-only gate, SIGALRM 45s + external timeout. Caveat: it is not read-only on the journal (rw learning writes) and holds venue API weight (bounded: <=2 trades/run).
- Obsolete paths: none confirmed. Docstring "no live learning application" is stale-ish (it does deliver verified outcomes to learning; `automatic_memory_import=False` is a constant).
- Confirmed defects: D1 below (fixed). Possible improvements: P1-P4.
- Decision: KEEP.

## Part B
`trader/observability/accounting.py | 231 | Off-path durable queue that captures whole-trade venue accounting for closed demo trades, replays it, delivers verified outcomes to learning | connect,initialize,finish,step,deliver_completed,Deadline,main | scripts/watchdog.sh:76 (CLI); tests | data/accounting-worker/*, journal learning tables, health.json, dashboard/backup read artifacts | writes queue.db+artifacts+health; rw journal learning_* via producers; demo venue reads | NECESSARY (only accounting-evidence scheduler) | D1 non-live trades retried forever (fixed); P1-P4 | KEEP | IMPLEMENTED_OFFLINE`

| Function | Role | Side effects | Note |
|---|---|---|---|
| connect | sqlite URI open, default mode=ro | none | resolve().as_uri() safe for odd paths |
| initialize | create meta/jobs/attempts | queue.db DDL | idempotent |
| finish | replay attempt file, set complete/retry, exponential backoff 5m..8h | queue.db update | exception text discarded, only type name kept |
| step | cursor scan, reserve attempt durably, capture, write artifact, finish, deliver | queue.db, artifact, venue reads | batch<=2, caps MAX_JOBS/ATTEMPTS/BYTES pause not drop |
| deliver_completed | 2 completed attempts/run -> producers.deliver_accounting; ledger learning_deliveries | journal rw learning tables | ValueError = terminal UNBOUND |
| Deadline | BaseException for SIGALRM | - | not swallowed by `except Exception` |
| main | flock, demo-only check, alarm 45s, health.json | health file | `Env BINANCE_DEMO` default 'true' |

## Part C
- Consistency/idempotency [TEST]: terminal artifacts reused without refetch; crash after file/before queue commit recovers without refetch; crash during capture stays retryable; retries create new files, old bytes untouched; cursor not lost on capacity pause (`tests/test_accounting_worker.py`).
- Restart [SRC+TEST]: running attempts replayed first in `step`; cursor in queue.db; lock via flock. Delivery crash between journal commit and `learning_deliveries` insert re-delivers but is deduplicated by `safely` key [SRC only; not tested here].
- Actual vs estimated: artifact is venue-sourced (`assess` yields learning_eligible only when attribution complete); incomplete -> retry, never complete [SRC+TEST]. Health reports status only.
- PAPER vs DEMO: `capture` hardcodes environment='demo'; `legs()` raises `unsupported_trade_scope` for exec_mode!='live'. Before fix such trades were retried indefinitely (D1). `main` refuses BINANCE_DEMO false; default (unset) is treated as demo [SRC].
- Failure isolation [TEST]: worker error -> health `worker_error_retry`/`deadline_retry`; learning errors swallowed to retry counter; no secrets: exception message not recorded in attempts (test: 'secret credential' absent). Residual: UNBOUND row stores `str(exc)` (producers' fixed strings; JSONDecodeError/UnicodeDecodeError are ValueError and would also become terminal UNBOUND) - P2.

## Part D - fixes
D1 (confirmed, IMPLEMENTED_OFFLINE): a closed trade with `exec_mode != 'live'` (or non-futures) passed the closed check, got an attempt row + exchange factory call, `legs()` raised, artifact non-eligible, status 'retry' with backoff - forever, consuming MAX_ATTEMPTS (10000 global cap) and disk, with no way to complete. Now marked terminal `unsupported_scope` before any attempt/venue construction; excluded from the due-job query. No change to financial semantics, thresholds or authority.
Test: `tests/test_review03_accounting_observer.py` - failed before ({'retry':1}), passes after.

Proposed only:
- P1 non-retryable reasons (`booking_owner_conflict`, integrity errors) share the infinite 'retry' path; `capture` stores only `capture_failed:<Type>` so reasons are indistinguishable (change in engine/trade_accounting.py, agent 1).
- P2 narrow `except ValueError` in `deliver_completed` to specific binding errors; do not persist `str(exc)`.
- P3 jobs for a trade stay 'complete' even if a later receipt arrives; no re-open (design decision).
- P4 health.json lacks fsync; stale-docstring cleanup; surface `unsupported_scope` count in dashboard/economics (outside file).

## Tests
Baseline before edit: tests/test_accounting_worker.py + test_verified_outcome_producers.py 27 passed; test_stage1_recovery_monitoring -k accounting 1 passed. After: those plus new file and full test_stage1_recovery_monitoring: 109 passed, 0 failures. Files modified: trader/observability/accounting.py, tests/test_review03_accounting_observer.py (new), this report.
