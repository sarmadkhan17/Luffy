# Attention collector errors and watchdog gaps — diagnosis, September 18, 2026

**Revision 2: coordinator-reviewed, documentation-only revision.** It supersedes
revision 1 of this file, which was written on the same day. The evidence files are
unchanged. Implementation and runtime verification of the proposed fix are
**pending**. The only test run was the reproduction of the existing failure; no
proposed-fix test has been run or passed.

Final [coordinator verification](../artifacts/collector-health/2026-09-18-coordinator-review.json)
checks the reproduction results independently and confirms 406 pre-existing
files unchanged, including production sources, configuration, earlier artifacts
and the unrelated knowledge edits. The brief additionally requires persisted
scan/cause identity, malformed-health refusal, measured publication delay, and
a drained stop barrier before changing runtime files. `git diff --check` passed.

## Scope

This was a read-only investigation on `main` `b6cc08a`, 01:30–01:40 UTC.

- **Production was not changed.** No source, config or control change. No
  trading, no restart, no worker run against live data, no memory import, no new
  freeze, no M3.2 search and no Gate 2.
- **Production SQLite files were byte-copied, never opened.** Each copy was taken
  only when no journal file existed and the source size and mtime stayed stable,
  and each copy passed `integrity_check`. The copies were analysed in temporary
  directories.
- **The accounting queue and journal were opened with `mode=ro` only.**
- **Research flags and frozen files are unchanged.** `research.referee=false` and
  `research.handoff=false`. `doctrine.json` (`e174dbe8…`) and
  `pit_population.json` (`46360685…`) match the
  [11:07 runtime artifact](../artifacts/execution-accounting/2026-09-17-postpublication-runtime-110713.json).
- **The window-close review stands as recorded.** See the
  [window-close review](2026-09-18-m31-window-close-review.md) and its
  artifacts. For its "unresolved" items, read this report.
- **The proposal is in the
  [brief](../plans/2026-09-18-collector-recovery-health-brief.md).**

The host clock is UTC+3. Times in this report are UTC.

## 1. Verdicts and evidence class

| Question | Answer | Class |
|---|---|---|
| Origin of the 74 worker errors | Worker children could not import `trader.observability.worker`. Git operations in the live tree had removed `trader/observability/`: `checkout main` at 15:20:50, then the tree was rewritten by the merge at 15:57:48. | **Strongly supported inference, not observed per event.** Three things agree: 37 kernel cycles ended inside the window, 37 × (scan + causes) = 74, and the module was absent from the checked-out trees. No per-job trace was retained, and a cycle's end time does not prove when its events were submitted. The exception class is **reproduced**, not retained. |
| Onset of `collector_unhealthy` at 16:00 | This was the first consumer pass after the tree was restored. The cumulative `errors` count was already non-zero and it cannot decrease, so every later pass refuses. | Observed: the pass times and refusals |
| Watchdog gap 15:20→16:00 | cron ran the checked-out *old* `watchdog.sh`, which has no consumer lines. | Observed: cron session lengths, and the script's content at 6da9818 and 0e7be4a |
| Watchdog gap 07:05→11:05 | Guest execution was interrupted. | **Hypothesis supported by morning observations.** The guest journal is empty from 07:09:55 to 11:04:25. The NIC went down at the start and up at the end. `Clock change detected` was logged on resume. The kernel's last cycle ended at 07:08:53. A host-side suspend or pause fits these facts but is **not observed**. The cause is not visible from the guest. |
| Transient refusals (learning 13:25; investigation 01:10, 02:05, 05:20) | A consumer clock race: `now` is taken at the start of the pass, and the health file is read later. | **Possible explanation, reproduced; not determined.** No health snapshot from those moments exists. |

## 2. Failure and recovery sequence (UTC, Sep 17)

| Time | Event | Source |
|---|---|---|
| 11:05:09 | The watchdog restarts the kernel as PID 341703. The collector counters start at 0. | `watchdog.log`; `ps` |
| 13:30 → 15:20:02 | Learning passes are `forward_scan_processed`, which implies `errors == 0`. | learning diagnostics (copy) |
| 15:20:16.985 | Cycle #254 is logged as ended. | `luffy.log` |
| **15:20:50** | `checkout main` (6da9818). This tree has no `trader/observability/`, no `trader/cognition/`, and a `watchdog.sh` without consumer lines. | reflog; `git cat-file` presence table |
| 15:23:12–15 | `pull --rebase`: 0cbf3f5 (no `kernel.py`, no `watchdog.sh`), then 0e7be4a (still no observability). | reflog |
| 15:21:20 → 15:57:22.985 | Cycles #255–#291, **37 cycles**, are logged as ended. | `luffy.log` |
| **15:57:48** | The merge resolution rewrites the files. Observability, cognition, `kernel.py` and `watchdog.sh` all have mtime 15:57:48.47x. | `stat` |
| 15:57:55 / 16:05:26 | Merge commits 7a7b55b and b6cc08a. The worker-manifest files and `watchdog.sh` are identical in 214947a and b6cc08a. | `git diff --stat` (empty) |
| 16:00:01 → now | Every consumer pass refuses `collector_unhealthy` (116 refusals and 0 processed passes as of the timeline). | learning diagnostics |

**Counters.** These come from the saved
[timeline](../artifacts/collector-health/2026-09-18-failure-timeline.json)
(generated 01:38:05.970 UTC): `submitted 1736`, `processed 1662`,
`worker_errors 74`, `capture_errors 0`, `dropped 0`, `timeouts 0`,
`last_error null`, `status ok`. So submitted = processed + errors. Later live
readings, which are not saved, showed the same 74 errors.

**Retained scans.** 146 retained scans run from 23:11:48.994 to 01:37:18.890 UTC.
All have a payload and complete causes, so there is no failure in the retained
period. The failure window itself has been pruned. The configured retention is
`max_scans 512`, `max_age_seconds 604800` and `max_bytes 67108864`. The store
keeps the main file at or below `max_bytes // 2` (32 MiB), and the retained
page bytes were 33,419,264, which is about 2.4 h of scans at current size.

**Reproduced mechanism.** When the module is absent, the child writes nothing to
stdout. The parent's `json.loads("")` then raises `JSONDecodeError`, and the
collector records:

- `worker_errors` incremented
- `status=error`
- `last_error=JSONDecodeError`

When the module returns, the next successful scan sets `status=ok` and clears
`last_error`, but `errors` keeps the full count.

The kernel logged no warnings or errors during the window. The collector does
not log per-event worker failures.

## 3. Watchdog gaps

**15:20 → 16:00.** User cron sessions varied in length:

| Sessions | Length |
|---|---|
| 15:10, 15:15, 15:20 | 3.0 s, 3.9 s, 3.3 s |
| 15:25 … 15:55 | 0.14–0.23 s |
| 16:00, 16:05 | 4.3 s, 2.8 s |

`watchdog.sh` in 0e7be4a lacks 17 consumer lines. It still supervised the kernel
and dashboard, and it logs only when it acts.

**07:05 → 11:05.** The journal entries from 07:05:07 to 11:04:26 are in the
timeline JSON. There was only one boot since Sep 15, and there is no guest
`PM: suspend` entry. On resume, the in-process watchdog logged
`HEARTBEAT STALE 14164s`, and the cron watchdog restarted the kernel at 11:05:08.
**These facts support, but do not prove, a host-level suspend or pause.**

## 4. Reproduction (isolated; existing failure only)

The
[repro script](../artifacts/collector-health/2026-09-18-worker-error-repro.py)
copies `trader/` to a temporary directory. It runs the real `Collector` (thread
plus child) against a temporary `attention.db`, and the real `learning.step` and
`investigation.step` against temporary ledgers. Results are in
[repro JSON](../artifacts/collector-health/2026-09-18-worker-error-repro.json).

| Phase | Result |
|---|---|
| A: baseline | 4/4 processed, `errors 0` |
| R: consumer `now` 1 ms before `health.updated_ms` | Both consumers refuse, with `errors 0` |
| B: worker module removed | `worker_errors 6`, `last_error JSONDecodeError`; no scan rows written |
| C: module restored | `status ok`, `last_error null`, `errors 6`; both consumers refuse |
| D: scan event lost, causes event written | A row with a NULL payload; the consumer falls back to the previous complete scan |

## 5. Contract defects that inform the proposal

1. **Recovery.** The counters cannot express recovery. Only a restart of the
   trading process clears them, and that restart erases the in-memory history.
2. **Error history.** No error history is retained: no times, scan ids or kinds.
3. **Code skew.** Producer and child code skew is invisible. The children import
   whatever is on disk.
4. **Unknown outcomes.** A timeout can happen after SQLite has committed. The
   scan and causes events can also fail independently of each other.
5. **Silent fallback.** When the newest scan is incomplete, consumers silently
   fall back to an older complete scan.
6. **Queue overflow.** Overflow sets `last_error=queue_full`. That is never
   cleared while `dropped > 0`. None has been observed.
7. **Producer failures.** Capture and overflow failures happen on the producer
   side, so a worker-only error log would miss them.
8. **Watchdog passes.** Watchdog passes leave no identity, so an old script
   running looks the same as no pass.
9. **Correction.** The window review's "scans stop at 15:20:02" means scans
   *processed by consumers*. Collector scans continued.

## 6. Unknown

- The exception class of each production error.
- The exact time each of the 74 events was submitted and failed.
- The host-side cause of the morning interruption.
- Whether the kernel's in-memory code exactly equals 214947a.
- The registrations that the unobserved 08:00, 16:00 and 20:00 cycles would have
  produced. The count is **unknown, not zero**.

## 7. M8.1 refresh (read-only, 01:37 UTC)

Sources: the
[refresh JSON](../artifacts/execution-accounting/2026-09-18-collector-diagnosis-accounting-refresh.json)
and its
[script](../artifacts/execution-accounting/2026-09-18-collector-diagnosis-accounting-refresh.py).
Nothing has changed since 01:20.

| Trade | State |
|---|---|
| SOL `pos_05a5e8f981` | Complete (diagnostic) |
| LINK `pos_0136aef3df` | Retry after 8 attempts; `legacy_entry_receipt_missing_retry` |
| AAVE `pos_57c828f65a` | Retry after 7 attempts; `legacy_entry_receipt_missing_retry` |
| NEAR, ZEC, UNI | `waiting_close` |
| XRP `pos_3f5e7f308c` | Open, no job |

There is **no natural complete receipt**, and `automatic_memory_import` is false.
Journal P&L is not venue-reconciled.

## 8. Artifacts

These are new files. The `artifacts/` directory is git-ignored.

- `artifacts/collector-health/2026-09-18-failure-timeline.{py,json}`
  (JSON `b2704d7b…`)
- `artifacts/collector-health/2026-09-18-worker-error-repro.py` (`6162f514…`)
  and `.json` (`60a25c41…`)
- `artifacts/execution-accounting/2026-09-18-collector-diagnosis-accounting-refresh.{py,json}`

All of these refuse to overwrite. Earlier reports and artifacts are untouched.

## 9. Next step

The coordinator has reviewed the proposal. It is **not implemented**. Implementation
and runtime verification are pending, and each needs its own authorization.

1. **Slice 1:** recovery-aware health and diagnostics (brief §1–§3).
2. **Slice 2 (optional, separate):** scheduler pass receipts (brief §4).

Two conditions apply:

- **No new frozen window** until the deployed fix has been verified healthy.
- **No branch operations in the live tree** while the kernel runs.

M8.1 is waiting for the natural closes of NEAR, ZEC and UNI.

No trading decision changed. No performance claim is made. The count stays at
**12/55**.
