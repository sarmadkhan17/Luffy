# LUFFY-PROTECTION-SNAPSHOT-R1 — Revision 3 (Astra's R2 blockers)

> **Superseded in part by [R4](2026-09-29-protection-snapshot-r4.md):** the Q3 `expired`-event deadline decision and the continuity wording were found defective by Astra and are replaced there.

Date: 2026-09-29. Implementer: Opus (Claude Code). Reviewer next: Astra.
Same worktree and base layers as R2. This revision builds on R2, and R2's
report still describes everything not listed here. **This report supersedes
R2 §B's credentials paragraph, §C's "held throughout" proof and ABA residual,
and §G/§L's invalidation mechanism.**

Not done: no commit, deploy, restart, watchdog change, `graphify update .`,
production access or live venue call. **CURRENT_LIVE_STATUS = NOT_ESTABLISHED.**

Results:

- **Python:** the exact 37-file pytest manifest gives **1065 passed**: R2's
  1058 plus 7 new tests (`pytest-manifest-r3.log`).
- **Frontend:** typecheck **PASS**, Vitest **51/51**.
- **Mutants:** all five R3 mutants **CAUGHT**.
- **Timing:** the selection of 9 tests, including the new race and publisher
  tests, passed **5/5 runs** (`repeat-timing-r3.log`).
- `git diff --check` is clean.
- **Playwright was not rerun.** No frontend, fixture or API source changed
  since R2, where it passed 67/67.

## Q1. Reachable signed mutation transport → fixed by removing the credential, not by a better guard

Astra showed that `super(GetOnlyAdapter, adapter).send(...)` reaches the
transport under the guard. The same holds for urllib3 pools and any other
inherited path. No Python-level guard can make a *trading* credential
read-only. The boundary therefore moves to the venue:

- **A read-only key is required.** `venue_reads._read_credentials` accepts
  only `BINANCE_READ_API_KEY`/`BINANCE_READ_SECRET_KEY`. The trading key
  (`Env.binance_keys()`) is never given to the snapshot client:
  - If no read key is configured, it raises `read_only_api_key_not_configured`.
  - If the "read" key equals the trading key (either half), it raises
    `read_only_key_equals_trading_key`.
  - When it raises, the reader is not built and every check publishes
    `UNREADABLE venue_reader_unavailable:<reason>`. This is visible to the
    owner and never silent (`test_without_read_only_key_the_monitor_publishes_unreadable`).
- **The GET-only transport is kept as defence in depth only**, and is no
  longer claimed as a boundary.
  `test_inherited_transport_bypass_carries_only_the_read_only_key` reproduces
  Astra's bypass. A signed `POST /fapi/v1/order` still escapes through the
  inherited adapter, but it carries `X-MBX-APIKEY = <read-only key>`. The
  client's key and secret are the read-only pair; the trading pair is absent.
- **The structural detector now flags reachable trading credentials**: any
  ccxt exchange whose `apiKey`/`secret` equals the trading pair gives
  `trading_credentials_reachable`. It also flags any reachable
  `SnapshotStore`/`ProtectionMonitor` (see Q3). The kernel-built observer
  passes.
- **Residual for the pre-deploy gate:** it cannot be shown offline that the
  configured key really lacks trading permission. The owner must create it
  with "Enable Reading" only, and the gate must confirm this. There is no
  read key in `.env` today (checked by variable name only), so **until the
  owner adds one, the monitor will publish UNREADABLE**. That is the correct
  fail-closed outcome.

## Q2. Second-observation validation loss → fixed

- `_positions` now returns `(by_symbol, contradictory, identity)`. The
  identity lists *every* non-zero row, duplicates included, so a dict that
  drops a row cannot hide it.
- The contradiction flags from **both** reads are combined
  (`contradictory1 or contradictory2`).
- Astra's reproduction (P1 has one BTC long q=1; P2 has two BTC rows, q=2 and
  q=1) now gives `contradictory_venue_ownership` **and**
  `observation_inconsistent:venue_positions_changed`, and is never VERIFIED
  (`test_contradictory_second_position_read_never_verifies`).

## Q3. Timeout/publication race → fixed by a single publisher

- **Workers never publish.** The worker is a module-level function,
  `_check(observer, gen, budget, box, expired)`. It receives an `Observer`
  and nothing else: no store and no monitor. A structural test walks the
  live worker thread's target and arguments and finds no publisher. The
  negative control shows that R2's worker (a bound monitor method) reached
  one (`test_worker_has_no_path_to_the_publisher`).
- **The monitor thread decides first, then publishes.** After waiting up to
  the deadline, the monitor sets `expired` and only then reads the box. A
  result counts only if it completed before that point
  (`box["late"]` records `expired` as seen by the worker at completion).
  A result that arrives later is never published, whether or not the timeout
  evidence was written. It is counted `late_discarded` on the next run
  (`collect_late()`).
- **No lock is shared with a worker.** The publication lock and the
  invalidation set are gone, so the timeout decision can never wait behind a
  worker.
- **In-time results, DB locked:** the monitor's publication fails within the
  busy timeout, and nothing commits later
  (`test_astra_race_timeout_during_publication_cannot_commit_old_generation`,
  part 1).
- **Late result, DB free:** it is discarded, and the stored row is the
  timeout's UNREADABLE (part 2).

## Correction: what the observation proves (was "held throughout")

R2 wrongly claimed that equal endpoint samples prove the state held
throughout the interval. Astra's 1 → 2 → 1 counterexample holds. R3 claims
this instead:

- **Matching samples.** Positions P1 == P2 over every row, and stops A1 == A2
  over the raw algo rows (with the venue `updateTime` when supplied). The
  journal J1 == J2; its latest `control_events` id is monotonic.
- **Position continuity.** VERIFIED additionally requires each position's
  venue `updateTime` to be present in both reads and equal, which means no
  position update happened between the two reads.
  - Without `updateTime`: `observation_continuity_unproven:position_update_time_missing`,
    never VERIFIED (`test_missing_update_time_means_continuity_unproven` is
    the 1 → 2 → 1 case with no `updateTime`).
  - With it, 1 → 2 → 1 changes `updateTime` and reads
    `venue_positions_changed` (`test_quantity_aba_between_samples_is_never_verified`).
  - `VenueReads.positions()` now carries positionRisk's `updateTime`.
- **Residuals, stated plainly:**
  - Continuity depends on Binance's `updateTime` semantics: it changes on
    every position update and does not change without one. This is
    unverified live and is a pre-deploy gate item. If it moved with the mark
    price, every check would read `venue_positions_changed`, a visible false
    negative, never a false VERIFIED.
  - Stop continuity rests on unique algo IDs plus identical parameters (and
    `updateTime` when present). Luffy never amends a stop in place; it
    places a new one and cancels the old.
  - Journal continuity is matching samples plus the monotonic control-event
    id; a journal value change-and-revert with no control event in about 1 s
    is not excluded.

## Correction: post-bracket overlap contract

The ordinary-order reads now happen **inside** the bracket:

```
J1 → P1 → A1 → P2 → A2 → ordinary orders → J2 → supervisor_busy()
```

A Supervisor pass during those reads changes J2, or still holds its lock
afterwards. Either way the observation is inconsistent
(`test_supervisor_pass_during_ordinary_reads_is_detected`: a real
`pass_once` during the first ordinary read gives
`observation_inconsistent:journal_changed`). Ordinary orders still support
cleanliness only, never protection.

## Mutants (source mutated, targeted test run, source restored byte-identical)

Log: `…-r2-evidence/r3-mutants.log`.

| Mutant | Test | Result |
|---|---|---|
| P2 contradiction discarded (R2 behaviour) | `test_contradictory_second_position_read_never_verifies` | CAUGHT |
| `updateTime` dropped from position identity | `test_quantity_aba_between_samples_is_never_verified` | CAUGHT |
| Continuity requirement removed | `test_missing_update_time_means_continuity_unproven` | CAUGHT |
| J2 read before ordinary reads (R2 order) | `test_supervisor_pass_during_ordinary_reads_is_detected` | CAUGHT |
| Late result published after the deadline | `test_astra_race_timeout_during_publication_cannot_commit_old_generation` | CAUGHT |

## Budget note (Astra N)

Steady state is unchanged: 4 + N requests, and weight 90 + N by documented
weights (`performance-r3.json`). The first positions read on a new client also
loads leverage brackets, one extra request (weight 1) once per process.
Aggregate headroom and the presence of the weight header remain
**unestablished** until the live gate. Without the header, the aggregate
guard is permissive.

## Remaining blockers / gate items

1. The owner must create a Binance "Enable Reading"-only key and set
   `BINANCE_READ_API_KEY`/`BINANCE_READ_SECRET_KEY`. Without it the snapshot
   is UNREADABLE by design.
2. Pre-deploy gate, each item explicitly authorized and read-only:
   - confirm the key has no trade permission;
   - confirm the ccxt read paths against demo;
   - confirm the `X-MBX-USED-WEIGHT-1M` header is present;
   - confirm positionRisk `updateTime` is present and stable between fills;
   - observe the first snapshot.
3. The journal change-and-revert residual (above).

**Deployment recommendation:** keep it blocked until Astra reviews R3 and the
gate above passes.

## Changed files in R3 (on top of R2)

- `trader/engine/venue_reads.py`: read-only key required; the trading key is
  never used; `updateTime` is carried through; the docstring now says the
  transport is not a boundary.
- `trader/engine/protection_snapshot.py`:
  - both-read validation and a full-row identity;
  - raw algo stop identity;
  - `updateTime` continuity;
  - ordinary reads inside the bracket;
  - a single publisher (`_check` worker; the lock and invalidation set are
    removed; `collect_late`);
  - a corrected docstring.
- `tests/test_protection_snapshot.py`: 6 new tests, updated detectors and
  statistics.
- `tests/test_venue_reads.py`: 1 new test; the credential tests are updated.
- `scripts/bench_protection_snapshot.py`: `updateTime` in the simulated
  reads; `late_discarded`.
- New evidence: `…-r2-evidence/r3-mutants.log`, `…-r2-evidence/repeat-timing-r3.log`,
  `…-r2-evidence/performance-r3.json` and
  `…-r2-evidence/pytest-manifest-r3.log`.
- This report.

Unchanged since R2: `owner_api.py`, `kernel.py`, the fixture, and all
frontend sources and tests. Their R2 results stand: Vitest 51/51 and
Playwright 67/67. Typecheck and Vitest were rerun for R3 (see Results).
