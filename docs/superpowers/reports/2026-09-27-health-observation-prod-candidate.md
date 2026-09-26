# SDD Stage 3 — Health Observation Production Candidate v1

Status: **BUILT, NOT DEPLOYED.** Recommendation: **BLOCK** until the owner
separately accepts the main restart delta described in §5. The package itself
is ready (GO on its own merits).

- Worktree: `/home/sarmad/trader-health-prod-candidate`
- Branch: `health-observation-prod-candidate`, based on `main @ d46d045`
- Code SHA: `d9d5c3f` (trader/ and tests/). The deploy SHA is the commit that
  adds this report. It is docs-only on top of `d9d5c3f`:
  `git diff --stat d9d5c3f <deploy-sha>` lists only files under
  `docs/superpowers/reports/`.
- Production checkout `/home/sarmad/trader` stays at `d46d045` and was not
  modified. `/home/sarmad/trader-world` was read only (grep/sed); its Python
  ran from a `git archive` extract in scratch space.
- STATE.yaml, NEXT.yaml, knowledge/ and graphify-out/ are untouched.
  `graphify update` was intentionally not run.

## 1. Runtime baseline

**RUNTIME_COMMIT = UNKNOWN.** The best evidence points to `51101d0`, but that
cannot be proven.

| Evidence | Value |
|---|---|
| Kernel PID / cmdline | 418802, `./venv/bin/python -m trader.kernel` |
| cwd / exe | `/home/sarmad/trader` / `/usr/bin/python3.12` |
| Supervisor | none. It runs in a GNOME terminal scope (`vte-spawn-…scope`), with parent 1709. There is no systemd unit; cron `*/5` runs `scripts/watchdog.sh` |
| `ps` start | 2026-09-18 06:43:00 +03 (computed from uptime) |
| Log boot | `LUFFY BOOT` 2026-09-18 05:29:59, right after `kernel stopped cleanly` 05:29:50. No later BOOT line, and `cycle #` counts continuously from #1 (05:31) to #12469 now |
| Reconciliation | the ~73 min offset matches VM-paused time. There is a 70.8 min log silence 2026-09-18 14:31→15:42 plus sub-5-min gaps. `ps` start is derived from uptime, which does not advance while the VM is paused. So PID 418802 **is** the 05:29:59 boot |
| HEAD at boot | reflog: `51101d0` at 05:29:57 (ff-merge of fix/declared-population), 2 s before BOOT. The next HEAD move was 2026-09-19 06:25 |
| Watchdog restarts since | none in `logs/watchdog.log` |
| Build identifier | none recorded by the kernel |

Why this remains UNKNOWN: the reflog records HEAD, not the working tree.
Uncommitted changes at 05:29 cannot be ruled out. The kernel also imports
modules lazily inside functions (e.g. `_mechanism_once` imports
`brain.analyst`, `brain.spec_writer`), and any module first imported after a
later checkout loaded newer code. The dashboard (PID 418811) booted at the
same moment. Watchdog `--once` consumers (`observability.declared`,
`learning`, `investigation`, `accounting`) start fresh every 5 minutes, so
they **already run current `d46d045` code**.

## 2. Backport provenance

Authoritative source: `b3639d8` "Add strategy health sweep observations", a
single commit whose merge-base with `main` is `d46d045` itself.

| Candidate commit | File | Provenance |
|---|---|---|
| 849b27e | `trader/strategy/health_observation.py` | b3639d8 verbatim, except the `from . import signal_occurrence as so` import. It is replaced by `SIGNAL_FINGERPRINT_SCHEMA`, `SIGNAL_FINGERPRINT_FIELDS` and `signal_fingerprint()`, copied verbatim from `signal_occurrence.py` @ 230eaa0 (`spec_fingerprint`, `FINGERPRINT_SCHEMA`, `FINGERPRINT_FIELDS`). This avoids porting the occurrence-identity module and its `engine.protective` import |
| 849b27e | `trader/brain/analyst.py` | b3639d8 hunks, applied unchanged. Main's file is byte-identical to `b3639d8^` |
| 849b27e | `trader/strategy/rolling.py` | b3639d8 hunks, applied unchanged. Main's file is byte-identical to `b3639d8^` |
| 849b27e | `trader/kernel.py` | b3639d8's 3 hunks (`_flush_health`, flush after retirement, flush after `mechanism:` log), re-anchored. The `+/-` lines are identical to b3639d8's |
| 849b27e | `trader/core/journal.py` | b3639d8's `strategy_health_rows()` (SELECT-only), re-anchored before `open_trades`. The b3639d8 context (the `decision_*` readers) is world-branch and was not ported |
| 849b27e | `tests/test_strategy_health_observation.py` | b3639d8 verbatim, except `so.spec_fingerprint`/`so.FINGERPRINT_SCHEMA` → `ho.signal_fingerprint`/`ho.SIGNAL_FINGERPRINT_SCHEMA` |
| 87cfa47 | `rolling.py`, `health_observation.py` | new: diagnostics isolation (§3) and bounded locked flush (§4) |
| d9d5c3f | `tests/test_health_observation_production.py`, `tests/test_health_observation_lock.py` | new |

Not ported: signal_occurrence, Attention, research-shadow/cognition,
opportunity context, positioning/rejection, Scheme-D, world model.
`git ls-tree` shows no `research_shadow` or `cognition/research_*` files.

Delta from d46d045 (code): 8 files, +2461/−14. By function (AST): kernel
changes `Kernel._mechanism_once` and adds `_flush_health`. Journal adds
`strategy_health_rows`. Analyst changes `__init__` and `review_deployed` and
adds `flush_health_observations`. Rolling changes `_score_window`,
`recent_verdict` and `has_decayed`, and adds private diagnostics helpers.
No other def changed.

## 3. Diagnostics-failure isolation

The review finding was real. On b3639d8, diagnostics writes in
`_score_window` were unguarded:
- the `rec = {… r.wins …}` build and `d["scored"][sym] = …`;
- `d.clear()/d.update()`;
- `d["errored"][sym] = _diag_error(...)` **inside the per-symbol `except`**;
- `d["branch"] = …` in `has_decayed`.

One failure escaped `has_decayed`, aborted `review_deployed` and skipped
retirement for every later spec. Reproduced on b3639d8 (§9, `diag_failure`):
review raised `RuntimeError` and its 2 retirement actions were lost.

Design:
- Every diagnostics write runs through `rolling._Diagnostics.step(fn, …)`.
  The write points are `_d_open`, `_d_frame`, `_d_errored`, `_d_scored`,
  `_d_close` and `_d_branch`.
- The first failure calls `_diag_failed`, which clears the caller's dict,
  leaves `{"diagnostics_failed": {stage, error_class, message}}` and switches
  diagnostics off for the rest of the window. A half-built description is
  never passed on. The handler itself never raises.
- `diagnostics=None` is an all-no-op path, so admission (`recent_verdict`)
  and all non-health callers behave exactly as before.
- Genuine evaluation exceptions are untouched: `derivs_for`, `funding_for`,
  `bars`, `warm_window` and a raising `_ctx` still propagate. Per-symbol
  evaluation errors are still logged and skipped.
- `health_observation`: when `diagnostics_failed` is present, the record is
  `evaluation_failed / coverage_unavailable` with `error: null` and
  `unavailable_coverage()`. That means `coverage_complete: false`,
  `coverage_faults: []`, every derived symbol list `null` (unknown, not
  "none"), and `diagnostics_error`. `lifecycle_branch` and
  `retirement_action_selected` are recorded as authority decided them.

Proof: the tests inject a failure at each of the 7 write points (including
`_diag_error`) across 3 frame sets, plus a broken sink and a branch-refusing
sink. In every case `has_decayed` returns an `==` and JSON-byte-identical
tuple, and the Analyst's actions and `spec_decayed` events are identical.
A real derivatives error raises identically with diagnostics None, `{}` or a
broken sink. Scoring vs `d46d045` is byte-identical over 90
`has_decayed`/`recent_verdict` results, including window widening
(`score_dump.py`).

## 4. Bounded flush under a lock

The journal's busy timeout stays at 30 s (`sqlite3.connect(timeout=30)` in
`Journal._conn`, unchanged). After the first failed spec-record write, the
flush marks the remaining spec records failed (`step: "write"`, the same
shape the TESTED consumers accept) and tries the sweep record once. A held
lock therefore costs at most two timeouts, not one per record.

## 5. Restart delta — the blocking item

Any restart loads the checkout. The candidate is `d46d045` + this package, so
a deploy activates **everything on main since the running code**. Taking
`51101d0` as the running baseline, that is 60 commits. Runtime-relevant files
(24, +3918/−96):

| Commit | Trading-path effect on restart |
|---|---|
| a113de0 "recent changes" (09-22) | `engine/exits.py` makes a trail arm at the spec's `arm_at_r`. It is neutral for both live specs today: both declare 1.0 and `trail_after_r` defaults to 1.0. Also adds `engine/excursion.py` (MFE/MAE booking each cycle, swallowed errors), `kernel.py` `_record_excursions`, `strategy/promotion.py` (+43/−7), `journal.py` +8, and cognition m32 modules |
| f6d6b2e | RECOVERY `ControlState`; `risk.py` fails closed on any state ≠ ACTIVE; new `engine/control_fence.py`; `executor.py` entry fence (62 lines); `state.py`; dashboard shows RECOVERY |
| 5d586f4 | new `engine/supervisor.py` (325). Boot reconciliation now goes through `Supervisor.pass_once(boot=True)`, and `supervisor.cycle()` runs every loop. Also `protective.py` (+171), `reconcile.py` (+157), `recovery.py`, `state.py`, `journal.py`, and `kernel.py` changes |

None of this is part of the health package, and the package cannot be
deployed without it. It already takes effect on any unplanned restart: the
watchdog restarts the kernel if its heartbeat is >10 min stale, which a host
sleep will trigger. Eight Donchian positions are open now; on the next boot
they are reconciled by the new Supervisor/protective code.

## 6. Kernel-level persistence (real temporary Journal, real `_mechanism_once`)

| Case | Result |
|---|---|
| A. normal cycle (2 paper + 1 active) | 3 `strategy_health_observed` + 1 `strategy_health_sweep`. intended = attempted = completed = recorded = the `list_specs` order; failed/compile_failed/not_evaluated empty; one sweep_id; `history_journal` shows no invalid records and no sweeps without a record; no open transaction left |
| B1. retirement, room to replace | report and `strategies` states identical with telemetry off. Event order: `spec_decayed ×2, spec_write_failed, observed ×3, sweep`. Health rows appear only after the write that committed the retirement UPDATE |
| B2. retirement, book still full | both flushes defer. The retirement UPDATE stays uncommitted exactly as with telemetry off, and telemetry neither commits nor rolls it back. After the kernel's later commit: retirement durable, **no** health rows (lost, never backfilled) |
| C. killed mid-cycle, restart | cycle-1 rows unchanged. The in-flight sweep is lost. After restart exactly one new sweep; history has 2 sweeps, none without a record, no backfill |
| D. evaluation exception | `(RuntimeError, "feed down")` raised identically with telemetry on and off; `strategies` identical. Rows: s1 `still_working`, s2 `evaluation_failed/evaluation_exception` (stage `context`), sweep `aborted`, `not_evaluated=[s3]`, nothing invented for s3 |
| E. compile failure | report and states identical on and off (`c1` kept paper, `r2` retired). `c1` `compile_failed` (stage `compile`, no coverage); sweep `compile_failed_spec_ids=[c1]`, `completed` |

Pre-existing main behavior, surfaced by B2 and not changed: the kernel
applies retirement with `journal.query("UPDATE …")`, which does not commit.
If nothing on the mechanism thread commits afterwards, the UPDATE stays open
and holds the WAL write lock until a later write commits. That cannot happen
with today's book (2 < max 8, so generation always writes).

## 7. DB-lock evidence (`tests/test_health_observation_lock.py`)

A separate process holds `BEGIN IMMEDIATE` on a temporary WAL journal. The
lock starts at the kernel's final flush, after the retirement committed.
3 spec records + 1 sweep are pending.

| Build | Locked flush elapsed |
|---|---|
| candidate d9d5c3f | **60.40 s** (1 spec write + 1 sweep write, 30 s each) |
| pre-fix backport 849b27e (= b3639d8 behavior) | **120.86 s** (every record waits 30 s) |

Results: report and states identical to the unlocked run; zero health rows;
the holder's row rolled back; `PRAGMA integrity_check = ok`; exactly one
spec-write and one sweep-write warning, both `database is locked`. The
journal is usable afterwards. The global timeout was not changed.

While blocked, the mechanism thread holds the process-wide
`Journal._write_lock`, so other kernel writes wait behind it for up to that
bound. That is the same exposure any journal writer has under a lock.

## 8. Zero trading-authority diff

- Files touched outside tests/docs: analyst.py, journal.py, kernel.py,
  rolling.py, health_observation.py. No path matching
  engine/, risk, execut*, order, protective, supervisor, control, orchestrat*,
  exits, sizing, account, exchange, venue, config.yaml, org.yaml, restart.sh,
  scripts/ or watchdog is touched. Those files are byte-identical to d46d045.
- AST: the only kernel def changed is `Kernel._mechanism_once`, which runs on
  the `strategy-mechanism` thread and calls `_flush_health` twice. The trade
  loop, entry, exit, reconcile, supervisor and control code are identical.
- `health_observation` imports only stdlib and `..core.types` (AST-guarded
  test). New imports elsewhere: `health_observation` into analyst, and
  `WARMUP` / `_SERIES_FOR` / `atr_series` into rolling (diagnostics only).
- Scoring is byte-identical to d46d045 (90 results). Retirement is identical
  with telemetry on/off/failed/locked (§3, §6, §7).

## 9. b3639d8 compatibility

`compat_dump.py` runs 12 fixture scenarios in a b3639d8 `git archive` and in
the candidate, with uuid4 and the clock pinned.

- 11/12 are **byte-identical** records after dropping `code_manifest`: idle,
  still_working, decayed ×3, compile_failed, evaluation_exception/aborted,
  coverage faults (errored, insufficient history, no frame, declared not
  loaded), no_scorable_bars, required_context_missing, errored→decayed, and
  the diag reference. That covers schema, all canonical fields, verdict and
  reason, sweep sets and status, sweep_id, `health_fingerprint`,
  `spec_fingerprint` (inlined function gives the identical hash),
  `spec_content_sha256` and `simulation_settings.sha256`.
- `diag_failure` is the only difference, and it is intentional and
  failure-only. b3639d8 raised and lost the retirement actions. The
  candidate returns actions and `spec_decayed` events identical to the
  uninjected reference, a `completed` sweep, and records refused as
  `evaluation_failed/coverage_unavailable`.
- `code_manifest` hashes differ for `health_observation.py` and
  `rolling.py` (this package) and for `compile.py` and `features.py`
  (world-branch commits 230eaa0 and 8923245, not ported). The manifest is
  partial provenance by contract.
- TESTED consumers (1eb00e1 extract, `consumer_check.py`): all 32
  candidate rows pass `ho._decode`, `ho.history`,
  `research_plan._observation_variant` / `_sweep_ok`,
  `research_question._observation` / `_sweep` / `derive` and
  `research_unreadable_question._reasons` / `derive`. That includes the 3
  diagnostics-refused records (evaluated variant). The world branch also has
  `strategy_health_rows_for_spec` / `_by_id` readers, which research-shadow
  will need later. They are not in this package.

Documented shape differences, failure paths only: `coverage` gains
`diagnostics_error` with `null` symbol lists when diagnostics failed. After a
failed spec write, later spec records are listed failed rather than attempted.

## 10. Tests (on d9d5c3f)

- `test_strategy_health_observation` 40, `test_health_observation_production`
  41 and `test_health_observation_lock` 1: all pass.
- Focused regression (31 files: the above + idea_pipeline, ideas_streams,
  single_creation_path, analyst, analyst_tv_and_vault, rolling,
  selection_window_widens, warm_window, universe_wiring, ref_evidence,
  lifecycle_kind_routing, rent_wiring, and every kernel-importing test):
  **419 passed, 18 failed**.
- The 18 failures are all in `tests/test_research_runner.py`. The same 18
  names fail on a clean d46d045 extract (KeyError `kind`/`ok`/`round` from
  runner output shape). They are baseline and not related to this package.
- `git diff --check d46d045..HEAD`: PASS.
- Not run: full suite, `scripts/backtest_equivalence.py` (no engine file
  changed).

## 11. Deployment procedure — PREPARED, DO NOT RUN

Requires a separate owner authorization that explicitly accepts §5.

```bash
cd /home/sarmad/trader
CAND=<deploy-sha>                       # the commit adding this report
# 0. preflight — abort on any mismatch
test "$(git rev-parse HEAD)" = d46d045af38497712ae5fbcfcc9b3bdeb78feee5
git merge-base --is-ancestor HEAD "$CAND"
test -z "$(git status --porcelain -- trader tests config.yaml org.yaml)"
# 1. disarm the watchdog BEFORE the stop
touch data/watchdog.off
# 2. capture pre-deploy evidence
EVID=data/deploy_evidence/health-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$EVID"
KPID=$(pgrep -f -x '.*/python[0-9.]* -m trader\.kernel')
DPID=$(pgrep -f -x '.*/python[0-9.]* -m trader\.dashboard\.server')
ps -o pid,lstart,etimes,rss,pcpu,cmd -p "$KPID,$DPID" > "$EVID/ps.txt"
readlink /proc/$KPID/cwd > "$EVID/kernel_cwd.txt"
git rev-parse HEAD > "$EVID/source_sha.txt"; git status --porcelain > "$EVID/git_status.txt"
cp data/heartbeat_luffy.json "$EVID/heartbeat_pre.json"
./venv/bin/python - "$EVID" <<'PY'
import json, sqlite3, sys
c = sqlite3.connect("file:data/luffy.db?mode=ro", uri=True, timeout=30); c.row_factory = sqlite3.Row
q = lambda s: [dict(r) for r in c.execute(s)]
json.dump({"quick_check": q("PRAGMA quick_check"),
  "health_rows": q("SELECT count(*) n FROM brain_events WHERE kind IN ('strategy_health_observed','strategy_health_sweep')"),
  "max_brain_event_id": q("SELECT max(id) m FROM brain_events"),
  "specs": q("SELECT id,state,retire_reason FROM strategies WHERE kind='spec' AND state IN ('paper','active')"),
  "open_trades": q("SELECT id,symbol,side,strategy_id,amount,entry_price,stop_loss,sl_order_id FROM trades WHERE status='open'"),
  "control": q("SELECT key,value FROM state_kv WHERE key IN ('control_state','execution_recovery','panic_requested')")},
  open(sys.argv[1] + "/db_pre.json", "w"), indent=1, default=str)
PY
./venv/bin/python - "$EVID" <<'PY'      # read-only venue snapshot
import json, sys
from trader.data.feed import make_exchange
from trader.engine import protective
ex = make_exchange("futures")
pos = [{k: p.get(k) for k in ("symbol", "side", "contracts", "entryPrice")}
       for p in ex.fetch_positions() if float(p.get("contracts") or 0)]
stops = protective.open_stops(ex, strict=True)
json.dump({"positions": pos, "stops": list(stops), "complete": stops.complete},
          open(sys.argv[1] + "/venue_pre.json", "w"), indent=1, default=str)
PY
# 3. intentional graceful stop (SIGTERM → _graceful). NOT restart.sh (kill -9).
kill -TERM "$KPID"
for i in $(seq 120); do kill -0 "$KPID" 2>/dev/null || break; sleep 1; done
kill -0 "$KPID" 2>/dev/null && { echo "kernel did not exit — STOP, escalate; do not kill -9 without owner"; exit 1; }
rg -a 'kernel stopped cleanly' logs/luffy.log | tail -1
kill -TERM "$DPID"                      # dashboard: no trading authority
# 4. switch code (fast-forward only; dirty docs/knowledge files are untouched)
git merge --ff-only "$CAND" && test "$(git rev-parse HEAD)" = "$CAND"
# 5. start
./restart.sh kernel && ./restart.sh dashboard
# 6. verify healthy startup
sleep 90
NPID=$(pgrep -f -x '.*/python[0-9.]* -m trader\.kernel'); readlink /proc/$NPID/cwd
rg -a 'LUFFY BOOT|recovery|Supervisor|Traceback|ERROR' logs/luffy.log | tail -20
./venv/bin/python -c "import json,time;h=json.load(open('data/heartbeat_luffy.json'));print(h['state'],round(time.time()-h['timestamp']))"
# 7. re-arm the watchdog, then dry-run it
rm data/watchdog.off
DRY_RUN=1 ./scripts/watchdog.sh; tail -5 logs/watchdog.log   # expect no "would run ./restart.sh"
# 8. post snapshot: re-run the two capture blocks into db_post.json / venue_post.json and diff
```

Post-start expectations to confirm before step 7: one kernel PID, cwd
`/home/sarmad/trader`, a new `LUFFY BOOT`, a heartbeat age under 120 s and
cycles resuming. The control state will be ACTIVE, or RECOVERY with stated
reasons from the supervisor boot pass (main behavior, §5). If the state is
RECOVERY, leave the watchdog off and stop for owner review.

## 12. Post-deploy acceptance

HEALTH_OBSERVATION counts as deployed only when all of these hold:

1. Within the first mechanism sweep (about boot + 300 s, and a `mechanism:`
   log line): exactly **one** new `strategy_health_sweep` and one
   `strategy_health_observed` per intended spec. Today that is 2 (the
   `auth_donchian_breakout_trail` and `spec_funding_filtered_trend_pullback`
   paper specs). `intended = attempted = completed = recorded`; failed and
   not_evaluated are empty; `status=completed`.
2. The pre-deploy health row count was 0. Every health row has an `id >`
   pre-deploy `max_brain_event_id` and `sweep_started_at` after the new BOOT,
   so nothing was backfilled.
3. Every row passes `ho._decode`, and `ho.history_journal` shows
   `invalid_records == []` and `sweeps_without_record == []`. The
   `consumer_check.py` equivalents pass.
4. Each observation's `retirement_action_selected` and `lifecycle_verdict_text`
   agree with the `mechanism:` report, the `spec_decayed` events and the
   `strategies` states. A verdict of `evaluation_failed` with a coverage
   reason is acceptable and truthful (for example, declared symbols not
   loaded). It is not a deploy failure.
5. No `health observation:` / `health diagnostics disabled` warnings, or only
   bounded and truthfully recorded ones.
6. Main-loop `cycle #… Ns` durations stay in the pre-deploy band (about
   14–23 s). Kernel RSS/CPU in `ps` stays comparable to `ps.txt`.
7. `venue_post` vs `venue_pre`: same positions and sizes. Any stop change must
   be explained by the journal (normal trail/exit), and no order or stop
   change may be attributable to the package.

STOP / ROLLBACK if any of these occur:
- a mechanism cycle fails because of telemetry;
- an observation contradicts the authoritative result;
- a DB lock stalls the main loop beyond one cycle;
- abnormal CPU or memory;
- a malformed or invalid health row;
- any order, stop, sizing or control difference;
- RECOVERY or HALTED without an explained reason;
- a watchdog restart loop, or an abnormal supervisor.

Rollback: `touch data/watchdog.off` → graceful stop →
`git reset --keep d46d045`. That is safe because the dirty files are not in
the delta. Then restart and re-arm. Health rows stay as evidence; do not
delete them.

Rolling back to d46d045 does **not** remove §5. There is no proven SHA for the
pre-deploy runtime, so a return to it is an owner decision.

## 13. Research shadow stays unauthorized

This candidate contains no research-shadow code and schedules nothing. The
sequence after deploy is:

1. obtain the first real completed health sweep;
2. verify its rows (§12);
3. a separate one-shot RESEARCH_SHADOW authorization;
4. a `max_sources=1` timing run;
5. evidence review;
6. only then consider a recurring cadence.

`research.referee` and `research.handoff` stay false (config untouched).

## 14. Remaining risks

1. §5 main restart delta, including an unproven runtime baseline. This is
   the blocking item.
2. A retirement cycle loses its telemetry if nothing on the mechanism thread
   commits afterwards (pre-existing uncommitted-UPDATE semantics; §6 B2).
3. A locked flush can hold `_write_lock` for up to about 60 s on the
   mechanism thread.
4. Fixture equality with b3639d8 does not prove equal verdicts on live data,
   because production `compile.py`/`features.py` differ from the TESTED
   branch. Verdicts are defined by production evaluation code.
5. Records are about 1.9–5.2 KB each, roughly 3 rows per 12 h. The dashboard
   "last brain event" indicator will show the new kinds (cosmetic).
6. Evidence reproducibility: the harness in
   `2026-09-27-health-observation-prod-candidate/` needs `git archive`
   extracts of b3639d8, 1eb00e1 and d46d045.
