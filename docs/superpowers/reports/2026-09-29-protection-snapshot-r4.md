# LUFFY-PROTECTION-SNAPSHOT-R1 — Revision 4 (Astra's two R3 blockers)

Date: 2026-09-29. Implementer: Opus (Claude Code). Reviewer next: Astra.
This is a narrow correction on top of R3; everything else in R2 and R3
stands. **It supersedes two things in R3: the Q3 deadline mechanism (the
`expired` event) and the continuity wording.**

Not done: no commit, deploy, restart, watchdog change, `graphify update .`,
production access or live venue call. **CURRENT_LIVE_STATUS = NOT_ESTABLISHED.**

Results:

- **Python:** the exact 37-file pytest manifest gives **1087 passed**: R3's
  1065 plus 22 new, most of them the parametrized malformed-timestamp cases
  (`…-r2-evidence/pytest-manifest-r4.log`).
- **Mutants:** both targeted mutants **CAUGHT** (`r4-mutants.log`).
- **Timing:** the selection of 10 tests, including the new delayed-monitor
  test, passed **5/5 runs** (`repeat-timing-r4.log`).
- `git diff --check` is clean.
- **Frontend and API were not rerun.** No frontend, API, kernel or fixture
  source changed since R3.

## Blocker 1: deadline enforcement

In R3, `_check` recorded whether the `expired` event had been set, not when
the check completed. A descheduled monitor could therefore accept a result
that finished after the deadline.

**Fix:**

- The worker stamps `box["done_at"] = budget.clock()` after evaluating, on
  the monitor's monotonic clock. It sets this before `box["snap"]`, so any
  reader that sees the result also sees its completion time.
- The monitor accepts a result only if `"snap" in box and box["done_at"] <=
  deadline`. The `expired` event is removed.
- The decision now uses the worker's own completion time. It no longer
  depends on when the monitor happened to look.

**Test:** `test_delayed_monitor_never_accepts_a_result_completed_after_the_deadline`
is deterministic and uses a fake monotonic clock.

- Deadline 60 ms, and the last venue read completes at about 65 ms, which is
  Astra's probe. The result is discarded, `venue_timeout` UNREADABLE is
  published and stored, `timeouts == 1`, and `late_discarded == 1`.
- A later check that completes at about 50 ms is still accepted as VERIFIED.

**Mutant:** "judge by when the monitor looked" (`done = "snap" in box`), which
is equivalent to R3 here, is **CAUGHT**.

## Blocker 2: timestamp validation

**Fix:** `_update_time(value)` accepts only evidence it can trust:

- a plain `int`, or an ASCII-digit `str`, of epoch milliseconds;
- in the range 2017-01-01 to 2100-01-01. USD-M did not exist before 2017.

It rejects booleans, floats, negatives, "NaN", empty, whitespace-padded,
signed, non-ASCII digits, dicts, lists, out-of-range values and `None`.

Continuity requires a valid stamp on every position in **both** reads. The
stamps must also be equal, because they are part of the position identity.
When continuity is not established:

- the reason is `observation_continuity_unproven:position_update_time_missing`
  or `…_invalid`;
- the result is never VERIFIED.

`venue_reads._plain_position` now passes the venue value through unchanged
(`str`/`int` only; anything else becomes `None`). It no longer stringifies
arbitrary values into apparent evidence.

**Tests:**

- `test_malformed_update_time_never_establishes_continuity` runs through the
  real `_plain_position`. It covers `"not-a-timestamp"`, `-1`, `True`,
  `False`, `{}`, `[]`, `"NaN"`, `"nan"`, `1.79e12`, `"1.79e12"`, `""`,
  `"0"`, `0`, `" 1790000000000"`, `"+1790000000000"`, full-width digits, an
  out-of-range value, `1000` and `None`. Each one is not VERIFIED, with
  `position_continuity=False` and an explicit reason.
- `test_valid_update_time_establishes_continuity` checks that `int` and digit
  `str` values give VERIFIED.

**Mutant:** "any non-empty updateTime" (R3 behaviour) is **CAUGHT** (12 cases
fail). The 7 cases that survive this mutant still pass because of R4's
normalization in `_plain_position` (non-`str`/`int` values become `None`);
this does not mean R3 rejected them — R3 accepted, for example, `True`
and `{}`.

## Corrected wording

The continuity claim is no longer presented as unconditional proof. It now
reads: equal valid stamps are evidence that the venue *reported* no position
update in between. That holds only if Binance's `updateTime` changes on every
position update, and only then. Those semantics are unverified and remain a
pre-deploy gate item. See the module docstring in `protection_snapshot.py`.

**Historical replay disclosure:**
`test_captured_2026_09_28_state_is_compatible_but_not_live_status` now states
two things:

- its continuity timestamps are **synthetic**, supplied by the test's `Reads`
  fixture;
- the capture has no `updateTime`, so the replay does not show that the
  historical state satisfies continuity. It is compatibility evidence only.

## Unchanged, still outstanding (the gate, after package approval)

A separately authorized, read-only pre-deploy gate must confirm five things:

- the read key's actual permissions;
- the real ccxt request paths;
- the `X-MBX-USED-WEIGHT-1M` header;
- the positionRisk `updateTime` behaviour. It must be present, valid, and
  stable between fills. If it moves with the mark price, checks fail closed
  as `venue_positions_changed`.
- the first live snapshot.

Aggregate headroom is still a source-derived estimate. Deployment stays
blocked.

## Changed files in R4

- `trader/engine/protection_snapshot.py`:
  - `_check` stamps `done_at`;
  - the deadline acceptance uses it, and the `expired` event is removed;
  - `_update_time` validation and the `UPDATE_TIME_MIN/MAX_MS` constants;
  - continuity over both reads, with missing/invalid reasons;
  - docstring wording.
- `trader/engine/venue_reads.py`: `_plain_position` passes `updateTime`
  through as `str`/`int` only.
- `tests/test_protection_snapshot.py`:
  - 22 new cases (19 malformed-timestamp, 2 valid-timestamp, 1
    delayed-monitor);
  - realistic epoch-ms fixture stamps;
  - the replay docstring.
- `tests/test_venue_reads.py`: a realistic `updateTime` in the canned
  response.
- `scripts/bench_protection_snapshot.py`: a realistic stamp. The rerun
  (`performance-r4.json`, run concurrently with the manifest, so timings are
  indicative only) shows unchanged request counts of 4 + N and weight 90 + N.
- New evidence: `r4-mutants.log`, `repeat-timing-r4.log`,
  `pytest-manifest-r4.log`, `performance-r4.json` (all under
  `…-r2-evidence/`).
- This report.
