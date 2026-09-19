# Collector recovery-aware health — implementation brief

**Revision 2 (coordinator-reviewed), September 18, 2026.** This revision
supersedes revision 1 and incorporates the coordinator's required changes.

**Status: Slice 1 implemented, tested and deployed September 18 with owner authorization.**
See the [delivery report](../reports/2026-09-18-collector-recovery-delivery.md) for commits,
validation, guarded rollout and forward-window evidence. Optional Slice 2 remains unimplemented.
The matrix below records the original review requirements; passing checks are listed in the
report, not implied for every optional row. Deployment authorization was supplied in the
continuing owner conversation; the separate-authorization wording below is the original plan.

Evidence: [diagnosis](../reports/2026-09-18-collector-health-diagnosis.md).

## Problem

`errors = capture_errors + worker_errors` is cumulative for the life of the
process. Consumers refuse any non-zero value, and nothing can express recovery. A
37-minute outage caused by an operator (inferred) therefore blocks consumers until
the trading process restarts, and the restart erases the in-memory history.

The fix keeps every refusal of a present failure and adds an **exact recovery
certificate** tied to scan identity. It does **not** remove the `errors` check and
does **not** accept on `status` alone.

## Slices

1. **Slice 1:** recovery-aware collector health, a shared predicate, consumer
   diagnostics and gap receipts (§1–§3).
2. **Slice 2 (optional, separate):** scheduler pass receipts (§4).

No new frozen window before the deployed Slice 1 has been verified healthy.

## 1. Collector (`trader/observability/collector.py`, `worker.py`)

### Identity

- **Instance.** Each process gets `instance_id`, a fresh UUID4 created when the
  `Collector` is constructed, plus `process_started_ms` and `boot_id`. A
  restart therefore always produces a new identity.
- **Attempt sequence, stamped first.** `begin()` takes the next `seq` from a
  producer-owned counter **before** capture and before enqueue. The scan event
  and its causes event share that `(scan_id, seq)`. A capture exception or a
  `queue_full` therefore still has an identity.

### Failure fence and generation

Every observed failure increments `failure_generation` and sets `fence_seq` to
the **highest seq issued at the moment the failure is observed**, not only the
seq of the failed event. So scans already issued or queued before a failure that
is observed late never qualify for recovery.

| Failure | Observed by | Fence taken as |
|---|---|---|
| capture exception, `queue_full` | producer | the current `issued_seq` |
| worker failure | worker thread | the producer's current `issued_seq`, read when the failure is observed |

### Producer failure signal (non-waiting)

The producer publishes by assigning one immutable tuple
`(issued_seq, producer_failures, producer_fence_seq, producer_generation)`. A
single reference assignment is atomic under CPython, so no lock and no I/O are
needed.

Failure details go into a producer-side `deque(maxlen=64)`. Appending to it is
non-blocking. The loss of any detail is counted as
`producer_failures − details_seen`, so a lost detail is never silent. This
channel is separate from the event queue, so a full queue cannot hide a
queue-full failure.

### Publication (single writer)

- Only the worker thread writes the health file. It writes after each event and
  at least once per idle second (`os.replace`).
- The file merges the producer tuple with the worker's own state. The existing
  worker can block in a child for its configured timeout, so an idle one-second
  cadence is not a one-second failure-publication guarantee. Implementation
  must measure this delay and retain the observation/publication clocks.
  Acceptance describes the published evidence, not instantaneous knowledge of
  an unpublished concurrent failure. A pending producer-generation change
  must prevent certificate publication; never advertise a stronger guarantee.
- **Retained history:** cumulative counters (never decreasing), `recent_errors`
  ≤ 64 entries, `first_error_ms`, `last_error_ms`, and `details_lost`.
- The health JSON is bounded at about 16 KB.

### Per-scan completion (bounded)

- The worker keeps an in-flight map `scan_id → {seq, scan_ok, causes_ok}`, capped
  at `2 × queue_size`.
- If the map overflows, that is a **failure**: the generation increments, the
  reason is `tracking_overflow`, and the incident is retained. It is never
  silently dropped.
- A scan is **complete** only when both of its events persisted.
  `last_complete = {scan_id, seq}`.
- Persist the instance UUID and sequence in the scan payload and cause-event
  metadata through `store.py`; verify both halves agree before publishing the
  certificate. These are new versioned metadata fields, not rewritten source
  availability clocks or a backfill of old scans. Old or malformed records
  cannot provide v2 identity proof. No new SQLite columns are required.

### Recovery certificate

The certificate is:

```
{instance_id, failure_generation, fence_seq,
 scans: [(scan_id, seq) × 2], issued_ms}
```

It is issued only when all of the following hold:

- 2 consecutive complete scans have `seq > fence_seq`;
- no failure happened in between;
- `now − last_error_ms ≥ 300 s`.

Any new failure invalidates it: the generation changes and the certificate is
set to null. The values **2 scans and 300 s are conservative engineering
defaults, not statistically derived**.

### Timeouts

The outcome of a timeout stays **unknown**. A timeout counts as a failure. A
read-only check of whether the row exists is diagnostic only, recorded as
`row_present_after_timeout`, and **never** earns recovery credit.

### Child diagnostics

- Empty or unparsable stdout is recorded as `child_no_result` together with the
  return code.
- From stderr, take only the last non-empty line and the token **before the
  first `:`**. Keep it only if it matches
  `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*(Error|Exception)$`;
  otherwise record `unclassified`. Message text is never stored.

### Code handshake

- The child returns a hash of the files it imports.
- The parent hashes the same files at construction.
- A mismatch is a failure recorded as `code_mismatch`.

### Truncation vs failure

- `omitted_causes` and `dropped_cause_symbols` are **telemetry-only
  truncation** from the declared caps (16 symbols, 64 receipts). They are
  reported separately and are not failures.
- Only failed or unknown events, drops, capture errors and tracking overflow
  change the generation.

### Status

- `last_error` clears only when a certificate is issued. The history fields stay.
- `queue_full` follows the same rule, so it is no longer a permanent refusal.
- Health schema: `attention-collector-health.v2`.

## 2. Shared predicate (`trader/observability/collector_health.py`, new)

This predicate replaces the inline checks in `learning.py:122-131`,
`investigation.py:206-216` and `store.read_latest`.

### Clocks (strict)

- `health_read_ms` is sampled **after** the health file has been read.
- `snapshot_observed_ms` is sampled after the read-only DB snapshot transaction.
- Any timestamp later than the actual observation (`updated_ms`, the scan's
  `as_of_ms`) is refused, with **no future tolerance**. Anything older than
  `fresh_ms` is refused.
- The pass's registration and PIT clocks (`now` used for `created_ms` and
  deadlines) keep their frozen-protocol meaning.
- With an explicit `now_ms`, the observation clocks come from an injected clock,
  so replay stays deterministic.

### Binding

1. Read the health file. It must be v2, `worker_alive`, with `status` not
   `error` and `last_error` null.
2. Accept if either:
   - `failure_generation == 0`, or
   - a certificate exists with the same `instance_id` and generation.
3. Read by exact identity: `snapshot = scan WHERE scan_id = health.last_complete.scan_id`.
   It must have a payload, `causes_complete=1`, and a seq equal to the one in
   health.
4. If a certificate exists, `last_complete.seq` must be ≥ the certificate's last
   scan seq. This is a seq comparison within the same instance. **There is no
   `as_of` ordering and no "or newer" acceptance.**
5. **Re-read** the health file after the snapshot. Refuse
   `health_changed_during_read` if any of these changed: `instance_id`,
   `failure_generation`, certificate, or `last_complete`. One bounded retry
   inside the pass is allowed.

Validate required fields, types, counter consistency and certificate structure
before these checks. Missing fields, malformed v2 data, or cumulative failures
inconsistent with generation zero refuse; a schema string alone proves nothing.

### Pending scans are normal

- A newer attempt that has been issued but is not yet complete does not
  invalidate the certificate.
- The consumer reads only the health-named `last_complete` scan. It never uses
  "latest row" and never uses a grace timer.
- If that newer attempt fails, the generation changes and the re-read refuses.
- If it hangs, the worker timeout records a failure. If publication stops, the
  health goes stale.
- Either way the result is a refusal, never an acceptance based on a grace
  period.

### Refusal codes

- `collector_health_missing`, `_schema_old`, `_stale`, `_future`
- `collector_worker_dead`, `collector_failing`, `collector_recovery_pending`
- `instance_mismatch`, `snapshot_identity_mismatch`, `snapshot_incomplete`,
  `snapshot_future`, `health_changed_during_read`

### Evidence on every result

- `instance_id`, `failure_generation`, `fence_seq`, certificate
- `errors_total`, `first_error_ms`, `last_error_ms`, `details_lost`
- both observation clocks and `health_schema`

## 3. Consumers (`learning.py`, `investigation.py`)

- Use the predicate, and copy its evidence into diagnostics and receipts.
- **While refusing:** emit a gap receipt with the specific reason code.
- **On the first acceptance after a refusal:** emit one `collector_outage` gap
  with interval `[first_error_ms, certificate.issued_ms]`, its generation, and
  the error delta.
- **On an instance change:** emit `collector_instance_changed`. The history of
  the previous instance survives only in these receipts.
- Missed registrations remain **unknown, not zero**.

## 4. Slice 2 (optional): scheduler pass receipts (`scripts/watchdog.sh`)

- **Receipts.** Keep an append-only, bounded, off-path receipt log. Rotate it by
  size, and make each rotation leave its own receipt.
- **Contents of each pass receipt:**
  - `pass_id` and `prior_pass_id`
  - the `watchdog.sh` sha
  - wall, monotonic and boottime clocks, plus `boot_id`
  - the exit codes of the consumers that ran
- **Clock discontinuity.** A jump in `wall − monotonic` shows a **clock
  discontinuity**. It does **not** show a host suspend: NTP steps and VM clock
  sync are ambiguous with it.
- **Missing receipt.** A missing receipt means **unknown**. It could be no cron
  run, an old script that emits nothing, or a failure before the write.
- No attribution is guaranteed.

## Invariants

1. The producer does O(1) work with no I/O, no waiting and no new locks, uses
   `put_nowait`, and keeps the 50 ms budget.
2. The configured bounds are unchanged:
   - Collector: `queue_size 16`, `max_symbols 16`, `max_causes 64`,
     `timeout_seconds 10`, `stale_seconds 300`.
   - Store: `max_scans 512`, `max_age_seconds 604800`, `max_bytes 67108864`. The
     main file is pruned to at most `max_bytes // 2`.
   - Consumer runtime limits are unchanged.
   - New bounds: 64 entries each for `recent_errors` and producer details, a
     tracking map of 2 × `queue_size`, and health JSON of about 16 KB.
3. Any current failure, uncertainty, identity mismatch or future clock is
   refused (fail closed).
4. History is never erased within an instance. Across instances it survives in
   consumer receipts.
5. The meanings of scan identity, `available_ms`, source clocks and `fresh_ms`
   are preserved. Frozen registration and PIT clocks are unchanged. Replay with
   an explicit `now` is deterministic.
6. The frozen September 17 window, its artifacts and its hashes are untouched.
   New reason codes apply only to future windows.
7. Restarting the trading process is never needed to restore observation.
   Consumers never write `attention.db`.

## Original test matrix (delivery results linked above)

| # | Case | Expected |
|---|---|---|
| T1 | Baseline, generation 0 | Accept exactly `last_complete` |
| T2 | Worker module removed | `collector_failing`; `child_no_result` or `ModuleNotFoundError` class |
| T3 | Restored, 1 complete scan | `collector_recovery_pending` |
| T4 | 2 complete scans after the fence, 300 s elapsed | Certificate issued; accept with evidence; one `collector_outage` gap |
| T5 | Scans enqueued before a failure observed late | seq ≤ fence, so no credit |
| T6 | Timeout, row present | Unknown; no credit; diagnostic flag only |
| T7 | Scan event fails, causes event succeeds | Failure; no fallback to an older scan |
| T8 | Causes event fails, scan event succeeds | Failure |
| T9 | `queue_full` while a child is running and the queue is full | Separate producer signal survives; pending generation blocks certificate publication; publication delay measured |
| T10 | Capture exception | Seq-stamped failure; fence equals the issued seq |
| T11 | More than 64 producer failures | `details_lost` counted; generation exact |
| T12 | Tracking-map overflow | Failure `tracking_overflow`, retained |
| T13 | A failure between the health read and the re-read | `health_changed_during_read` |
| T14 | A newer attempt pending, no failure | Accept the certified `last_complete`; the pending attempt is ignored |
| T15 | A newer attempt fails after the snapshot | The re-read refuses |
| T16 | Snapshot is an arbitrary row newer than the certificate or `last_complete` | `snapshot_identity_mismatch` |
| T17 | Restart (new `instance_id`) | `instance_mismatch` against old evidence; `collector_instance_changed` receipt |
| T18 | Health `updated_ms` or scan `as_of` 1 ms in the future | `_future` / `snapshot_future` |
| T19 | Stale health; dead worker after recovery | Refuse |
| T20 | Child code hash ≠ parent code hash | `code_mismatch` |
| T21 | v1 health file | `collector_health_schema_old` |
| T22 | stderr is `ModuleNotFoundError: No module named x` | Class token only; the message is never stored |
| T23 | Caps: 17 symbols, 70 evaluations | Truncation counts only; generation unchanged |
| T24 | Producer timing | Existing budget test passes; no I/O |
| T25 | Deterministic replay with explicit `now` | Identical output |
| T26 | Frozen Sep 17 cohort replay | Byte-identical; hashes unchanged |
| T27 | Dashboard `read_latest` | Same verdicts as the consumers |
| T28 | (Slice 2) receipt chain, rotation, discontinuity | Prior linkage; "unknown" where a receipt is absent |
| T29 | Missing/malformed v2 fields or nonzero failures with generation zero | Refuse, never treat missing counters as zero |
| T30 | Scan and causes carry different instance/sequence metadata | Refuse identity proof |

Relevant tests only:

- `tests/test_attention_telemetry.py`
- `test_attention_learning.py`
- `test_investigation_consumer.py`
- `test_attention_view.py`
- population and cohort tests
- a new `test_collector_recovery.py`

## Deployment (requires separate operational authorization)

**Important:** `data/watchdog.off` only stops the watchdog from acting. **It does
not stop the kernel.** Never check out, rebase or merge in the live tree while the
kernel runs.

1. Implement and review in an isolated worktree. Prepare the reviewed deploy
   commit **and** a reviewed revert commit there in advance.
2. **Pre-checks (read-only):**
   - Record `git status` and keep unrelated dirty files, such as `knowledge/`.
   - Record the current control state (`ACTIVE`, `FROZEN` or `HALTED`).
   - Verify demo mode, the venue positions and the native protective stops, using
     read-only enumeration through `engine/protective.py`.
3. **Stop barrier:**
   1. `touch data/watchdog.off`.
   2. Send SIGTERM to the verified kernel PID; `kernel.py` handles it through
      `_graceful`. Wait for confirmed exit with a bounded rollout timeout;
      abort the update if it remains running rather than silently escalating.
   3. Verify that no kernel, its child worker, or in-flight watchdog consumer
      remains before changing files. The flag does not stop an existing pass.
4. **Update** by one of two methods:
   - a fast-forward of the reviewed commit in the live tree, which leaves
     unrelated dirty files untouched and aborts on any conflict; or
   - an immutable release directory. This needs its own path and launcher
     design.
5. **Start and verify:**
   1. Start the kernel.
   2. Restore the **recorded** control state. Do not assume `ACTIVE`.
   3. Verify health v2, unchanged frozen hashes, and unchanged demo and protection
      state. Preserve any errors encountered during startup as evidence.
   4. Restore the watchdog flag to its recorded prior state. If it was originally
      absent, remove it and observe the next naturally scheduled consumer passes.
      Require `forward_scan_processed` with exact identity evidence before
      declaring runtime recovery. A flag deliberately left disabled cannot
      establish natural scheduled recovery.
6. A new frozen window only after the healthy verification.

**Rollback.** A `merge --ff-only` to an older sha is **not** a rollback. Apply
the pre-reviewed revert commit, or switch to the preserved previous release,
under the **same** stop barrier. Never reset or discard unrelated dirty changes.
`attention.db` has no schema change. Old consumers remain strict on the
cumulative `errors`.

## Out of scope

- The frozen window.
- M3.2, Gate 2 and research flags.
- M8.1 logic.
- Hypervisor settings.
- Any trading or risk path.
