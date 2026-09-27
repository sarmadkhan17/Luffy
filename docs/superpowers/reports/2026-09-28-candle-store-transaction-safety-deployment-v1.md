# LUFFY-CANDLE-STORE-TRANSACTION-SAFETY-DEPLOYMENT-V1

Date: 2026-09-28 (times local, UTC+03:00)
Status: **DEPLOYED_HEALTHY** — kernel held FROZEN throughout.
Evidence: `2026-09-28-candle-store-transaction-safety-deployment-v1-evidence/`

## Change

- PRE: `f74c8b8` → deployed `97ccebd` ("Harden candle store transaction cleanup"),
  fast-forward, parent f74c8b8, exactly 5 paths:
  `trader/data/sqlite_tx.py`, `trader/data/feed.py`, `trader/data/references.py`,
  `tests/test_candle_store_transaction_safety.py`,
  `docs/superpowers/reports/2026-09-28-candle-store-transaction-safety-v1.md`.
- Unrelated dirty production files preserved (verified by sha256 after merge;
  `dirty_pre.txt`, `dirty_pre.sha`).
- Scope: kernel-only restart. Dashboard, health candidate, gateway untouched.

## Pre-deploy recheck (twice; `venue_pre.json`, `venue_pre2.json`)

HEAD f74c8b8, FROZEN, heartbeat 22–40 s, 8 venue positions == 8 journal-open,
8/8 native algo STOP_MARKET stops matched (id, reduceOnly, size, trigger),
complete stop snapshot, 0 ordinary orders, dashboard PID 418811,
`data/watchdog.off` absent, disk 3.1 GB free (94%).

## Timeline

| Time | Event |
|---|---|
| 01:20:28 | `data/watchdog.off` created; `./restart.sh kernel` killed PID 2314203 |
| 01:20:30 | LUFFY BOOT, new PID **2324544**, state=FROZEN |
| 01:21:04 | Reconcile: 8 positions, adopted/ghosts/swept/rearmed 0, no safety issues |
| 01:23:34 | One `HEARTBEAT STALE 252s` CRITICAL (log-only monitor; restart gap + 157.6 s cold first cycle; not repeated) |
| 01:23:42 | Cycle #1 complete |
| 01:24:36 | Post-start venue check identical to pre-stop (`venue_post.json`); `watchdog.off` removed |

## Monitoring (`cycles.txt`)

Cycles #2–#8: 16.6, 21.6, 16.6, 15.9, 20.4, 17.2, 15.7 s; ~60 s cadence;
heartbeat 5–15 s once sampling caught up; FROZEN throughout. CPU 49%→28%,
RSS 371→465 MB (flat from #6). Disk unchanged.

Since boot: candle-store `locked` 0, `rollback failed` 0,
`discarding connection` 0, `close of failed connection` 0, ERROR 0,
tracebacks 0, CRITICAL 1 (boot heartbeat gap above).

Final venue check (`venue_final.json`): positions, stop IDs, triggers and sizes
identical to pre-stop; 0 ordinary orders; no order activity.

## Follow-up: LUFFY-KERNEL-OWNER-RESUME-V1 — **ACTIVE_HEALTHY**

Pre-resume gate (01:34, `venue_preresume.json`): HEAD 97ccebd, FROZEN,
heartbeat advancing, `risk_state` parses, baseline marker established,
no `risk_state_corrupt_latch`, `execution_recovery` null, no close requests,
panic 0, boot Supervisor checks all true (DEGRADED only
`control_state_not_supervisor_owned`), 8 == 8 positions, 8/8 stops identical
to pre-deploy, 0 ordinary orders, 0 lock/cleanup/ERROR lines since boot.

Owner sent Telegram `/resume` (principal 1807747201, request_ref
`783631784:11276`). Kernel `owner_resume` → `Supervisor.request_owner_recovery`:

| Event | Content |
|---|---|
| 11493 | `owner_recovery_requested` (observed FROZEN) |
| 11494 | FROZEN → RECOVERY (contained recheck) |
| 11496 | reconcile: 8 positions, nothing adopted/swept/rearmed |
| 11497 | Supervisor SAFE/PROVED, 6/6 checks incl. `risk_release` (allowed, `risk_release_ok`, equity 5413.57, dd 1.93% vs halt 20%, generation 20, authoritative) |
| 11498 | RECOVERY → ACTIVE by `supervisor` (guarded transition) |
| 11500 | `owner_recovery_result` status **ACTIVATED**, outcome SAFE, reasons [] |

No control events after 11500. Post-resume venue checks (`venue_postresume.json`,
`venue_active_final.json`): 8 == 8, stops identical, 0 ordinary orders.

ACTIVE cycles #20–#26 (`active_cycles.txt`): 21.5, 22.0, 15.4, 15.9, 22.0,
16.3, 15.6 s; heartbeat 1–5 s; ACTIVE / Supervisor SAFE every cycle; no Risk
latch; no pending intent; no trade-row changes; no order activity observed
(entries allowed; none occurred in this window); 0 locked/cleanup/ERROR/CRITICAL/traceback.
Kernel PID 2324544, CPU 14%→12%, RSS ~470 MB flat, disk 3.25 GB free.
Dashboard 418811 unchanged; watchdog active.
