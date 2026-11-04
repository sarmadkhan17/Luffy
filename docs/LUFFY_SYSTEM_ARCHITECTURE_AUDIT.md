# LUFFY — As-Built Architectural Audit

Audit date: 2026-11-04 (host time Asia/Bahrain, UTC+3)
Checkout: `ce2909221be3521063ea187f1f623d322480a1c6` (clean runtime code, per `data/kernel_instance.json`)
Method: static reading of `trader/` (about 111k lines Python, 605 test files) cross-checked against the **live** runtime (read-only SQLite opens, `/proc`, `data/cycle_runtime.json`, `systemctl --user`, `crontab`). Nothing was started, stopped, written or traded. Tests were not run.

Evidence tags: **[V]** verified in code and/or live state this session · **[I]** inference from verified facts · **[U]** not examined.

---

## 0. Executive summary and corrections to the brief

The system is a single-process, thread-per-job Python trader around one SQLite "journal" (`luffy.db`, 7.0 GB) and two market-data SQLite stores. It carries at least four generations of design at once:

| Era | Where it still lives |
|---|---|
| E1 blended-analyst cycle (`Kernel.cycle()` with 7 analysts voting) | `kernel.py:1386-1650` (legacy branch), `engine/orchestrator.py`, `strategy/legacy_blend.py` |
| E2 genome strategies (`ematrend_g_edc6`, kind `ema_trend`) | `strategy/library.py`, `strategy/genome.py` |
| E3 DSL "spec" strategies + legacy research (`research/runner.py`, combo grid, `research_*` ledger tables) | `strategy/spec.py`, `compile.py`, `research/` |
| E4 versioned / receipt / closure architecture (market receipts, Factory `strategy_versions`, `research/closure/`, owner-demo authority) | `data/market_provenance.py`, `strategy/factory_handoff.py` (2,740 lines), `research/closure/` |

**Live state at audit time [V]:**
- Kernel pid 2384161, up about 28 min, 42 OS threads, managed by a transient systemd user unit. Dashboard pid 2029828 (15 threads) is a separate process.
- Control state is **`FROZEN`** (owner freeze via dashboard on 2026-10-26 22:19 UTC). `macro_guard_operator_hold=1`.
- Equity 5,178.83 USDT against a 5,519.91 peak (about 6.2% drawdown).
- The latest row in `decisions` is 2026-10-24. The latest opened trade is 2026-09-26. The three currently active strategies have **never** produced a journalled live trade.
- `strategy_versions`, `strategy_version_events`, `research_tests`, `research_combos` and `research_candidates` are all **empty** in `luffy.db`. The closure store `research.db` has 1 study, 24 candidates, **0 looks, 0 freezes, 0 qualifications**. **No `VALIDATED` version has ever been produced.**

**Corrections to the brief (premises that do not match the code):**

| Brief says | As built |
|---|---|
| Entrypoint `trader/main.py` / `bootstrap.py` | `trader/main.py` **does not exist**. The entrypoint is `trader/kernel.py:main()` (line 3527). The real launcher is `trader/observability/bootstrap.py`, started by `restart.sh`. `kernel.main()` refuses to run without `--bootstrap-token`. |
| 27 worker threads | `KernelRuntime.jobs` holds **30** `Worker` threads under the live config (verified in `data/cycle_runtime.json`). On top of those are about 9-12 raw `threading.Thread`s. Live total is 42 OS threads. |
| Dashboard is FastHTML/Starlette/FastAPI | **FastAPI** (Starlette underneath) + a **GraphQL** router (`api/graphql_schema.py`) + a **WebSocket** `/ws/live`, served by uvicorn. There is no FastHTML. |
| `portfolio-entry-booked.legacy` → chronological accounting is a ledger migration | It is the **research portfolio simulator's** accounting label (`strategy/portfolio_evidence.py:34-35`), not the live trade ledger. The live ledger transition is a different one (see 4.3). |
| Trade ticks/candles come from exchange WebSockets | There are **no exchange WebSockets** in the data path. All market data is synchronous REST polling through ccxt. `websocket` appears only in the dashboard. |
| `candles.db` / `derivs.db` / `luffy.db` / `attention_learning.db` are the four stores | There are at least **eleven** SQLite files (see 2.1), including a dormant `attention.db`, a 2.2 GB `runtime-portfolio.db`, and external research stores. |

---

## 1. Process, threading and concurrency topology

### 1.1 Process map [V]

| Process | Launch | Supervision |
|---|---|---|
| Kernel `python -B -m trader.kernel --expect-revision <sha> --bootstrap-token <t>` | `restart.sh` → `trader.observability.bootstrap` controller → managed unit `luffy-kernel-<token>.service` | cron `watchdog.sh` (every 5 min and `@reboot`), plus per-boot transient timers `luffy-bootstrap-<token>-heartbeat-only.timer` (about 10 s) and `…-safety-only.timer` (about 30 s) running `scripts.monitor` |
| Dashboard `python -m trader.dashboard.server` | `restart.sh dashboard` → `observability.managed launch --role dashboard` (transient user unit, no unmanaged fallback) | `watchdog.sh` (`pgrep` check) |
| Research child | spawned by the kernel's `research` worker via `launch_role('research')` (transient systemd unit) | the kernel thread blocks in `wait_role` |
| Observers (short-lived, cron-driven via `watchdog.sh`) | `declared --once` (120 s timeout), `learning --once` (20 s, flock `attention_learning.lock`), `investigation --once` (25 s), `accounting --once --enable` (50 s timeout, 45 s SIGALRM) | gated by flag files `data/*.enabled` / `*.off` |
| `installation/observe.sh` | cron every 5 min | [U] contents not audited |

### 1.2 Kernel lifecycle [V]

1. **Preflight, outside the process:** `restart.sh` runs `trader.observability.preflight --target kernel`, then `runtime_identity verify --expect-revision`. A 40-char `LUFFY_EXPECT_REVISION` is mandatory. A dirty runtime tree is refused. It then runs `runtime_identity stop` (SIGTERM only, waits up to 300 s for `data/kernel.lock`; **never SIGKILL**), then starts the controller `trader.observability.bootstrap`.
2. **Bootstrap controller:** a two-phase "contained cold startup". Phase A admits observation only, never entries. It writes the `bootstrap` block (token, status PENDING→READY, intended revision) into `data/safety_health.json` under a lock. The child starts `FROZEN` with the recovery latch retained (`STARTED_FROZEN`). It never transitions to ACTIVE.
3. **`kernel.main()`:** `child_admission(ROOT, token, revision)`, then `KernelInstance().acquire()` (flock on `data/kernel.lock`, `/proc` start-ticks, and revision binding; `AlreadyRunning`→exit 3, `WrongRevision`→exit 6), then `Kernel(cfg)`.
4. **`Kernel.__init__` (kernel.py:77-130):** builds `Journal`, `ControlStateMachine`, `RiskManager`, `Heartbeat`, ccxt exchange, `DataFeed` (production public API, **not** the demo exchange, because demo klines carry simulated volume), `Universe`, 7 analysts, `Orchestrator`, `Executor`, `Supervisor`, population (genomes + compiled specs), `ExitEngine`.
5. **`Kernel.boot()` (kernel.py:~355-430):**
   - `feed.initialize_markets` runs in a daemon thread (`market-initialization`).
   - `_filter_universe_to_venue` loads venue markets and writes a `venue_metadata` receipt to `candles.db`.
   - `stage5_activation.start` launches the `paper-funding-evidence` and `stage5-public-evidence` threads.
   - `supervisor.pass_once(boot=True)` does boot reconciliation, which can adopt ghost or naked positions.
   - Starts `stall-monitor`, `tg-listener`, the owner IPC server (`owner-interface` + `owner-interface-conn` per connection), and `macro-schedule`.
   - The legacy thread set (`derivs-recorder`, `ref-recorder`, `strategy-mechanism`, `agent-validator`, `scraper`, `crawler`, `rent-check`, `research`) is **skipped** because `run()` sets `_use_split_cycles=True` first.
6. **`Kernel.run()` (kernel.py:3503):** sets `_use_split_cycles`, calls `boot()`, publishes `STARTED_FROZEN` heartbeat, starts `bootstrap-protection` (a 30 s protection-snapshot monitor), constructs `KernelRuntime`, and calls `runtime.start()`. The **main thread then only does `stop_event.wait(.5)`**.
7. **Shutdown:** SIGTERM/SIGINT → `_graceful` → `_stop=True`. `runtime.stop()` flags all workers, `join()` drains them, and the lock is released. A graceful stop has been observed at about 224 s (per `restart.sh` comments). Workers are `daemon=False`, so a wedged worker blocks exit.

### 1.3 The "central scheduler"

There is no central loop. `scheduling/runtime.py:Worker` is a single-flight, coalescing job runner. Each worker has one thread (`cycle-<name>`, non-daemon) and a `Condition`.
- A worker runs at most one callback at a time and holds at most one pending event. Excess `trigger()` calls count as `coalesced_events`. Missed interval deadlines are collapsed and counted as `missed_deadlines`, with no catch-up storm.
- Interval deadlines use monotonic time. `Calendar(hours, minute, weekday, tz='Asia/Bahrain')` deadlines use wall time. `candles` is wall-aligned (`align_to_wall`).
- The loop wakes at least every 1 s.
- Only the exception class name is stored, never the message (a deliberate secrets guard).
- Event wiring is by direct method call: `trade_closed`, `fill_event`, `owner_event`, `candle_acquired` call `.trigger()` on other workers.

### 1.4 Worker inventory (30 workers, live config) [V]

Shared-state keys: `X` = `execution_lock` (RLock), `D` = `decision_lock` (RLock), `L` = `runtime.lock` (Lock), `J` = `Journal._write_lock`, `F` = `DataFeed` caches/local conns, `CC` = `mp.CURRENT` cache.

| # | Worker | Cadence / trigger | What it does and touches | Observed (live) |
|---|---|---|---|---|
| 1 | commands | 1 s, immediate; `owner_event` | `X`: drain panic and close requests | 1,834 runs, **logs an INFO line every second** |
| 2 | account | 30 s, immediate; `fill_event`, `trade_closed` | `X`: `supervisor.cycle()`, control-state refresh, `_risk_step` (venue REST), `executor.recover_entries()`, `J`: log_equity; `L`: publish `account_data` | max 20.7 s |
| 3 | positions | 30 s, immediate; `fill_event`, held-symbol candle, intake | `X`: resume coordinated/versioned unwinds, group management; builds the "view", `_service_exposure`, peer frames; `L` | max 20.7 s |
| 4 | market | 60 s, immediate | single-flight scan reservation (`scan_in_flight`); builds the view; `_admission_plan` (selection persist+verify); `lean_scan.prepare` under `mp.cache_only()`; pins `CC`; then `decisions.trigger()` | 2.5 s wall, 2.0 s CPU (1.76 s in `admission.select_persist_verify`) |
| 5 | decisions | event only (from market) | holds **`D`**; `view.cycle()` → `lean_scan.run`; then `coordinated_intake` under `X`; discards the batch if the model generation changed or the batch is more than 60 s old; `malloc_trim` afterwards | **6.9 s wall, 6.4 s CPU**; max 17.6 s |
| 6 | portfolio | event only (`queue_portfolio`) | proposal-only `_portfolio_checkpoint` (writes `runtime-portfolio.db`) | 0 runs (nothing queued while FROZEN) |
| 7 | candles | 15 s, wall-aligned | acquires needed candle series for held and shortlisted symbols and coordinated needs; feeds `feed.on_candle_acquired` | 0.44 s |
| 8 | coordinated-paper | 30 s | `coordinated_trials.admit/run_once`, paper hedge books | |
| 9 | strategy-health | 3,600 s; `trade_closed` | `evaluate_population`, `_mechanism_once(review_only)`, `reload_model()` under `D` | |
| 10 | research | calendar 09:00 and 21:00 | closure on: launch a managed child and **block up to `slot_budget_s+30` = 3,630 s** in `wait_role`, then `_mechanism_once` | 0 runs |
| 11 | validation | Sunday 03:00 | `_maybe_validate_agents(scheduled=True)` | |
| 12 | health | 10 s, immediate | publish cycle JSON; heartbeat `CYCLE_READY` only if the essential cycles are fresh | 3 failures out of 187 |
| 13 | input-metadata | 30 s | | |
| 14 | input-warm | 2 s | `mp.run_warm` refills `CC` entries | 914 runs |
| 15 | learning | event (`trade_closed`, `learning_check`) | dispatch learning chains (maintenance) | 18.8 s once |
| 16 | learning-apply | 60 s | `apply_learning` under **`D`**: may `reload_model()` | |
| 17 | learning-check | 60 s, immediate | | |
| 18 | outcomes | 60 s; `trade_closed`; 1h candle | resolve forward-return outcomes; `_record_excursions` | |
| 19 | reports | 3,600 s | | |
| 20 | daily-report | calendar hour 0, minute 10, **daily** | | |
| 21 | postmortem | 6 h | | |
| 22 | derivatives | 900 s, not immediate | `Harvester.deriv.record_all` for 5 configured symbols | **161.8 s** (blocked in `mp.plan_append`) |
| 23 | derivative-needs | 60 s | acquires the exact derivative series the shortlist's strategies need | max 58 s |
| 24 | derivative-inputs | 300 s, immediate | `_funding_map`/`_oi_map` into `runtime.derivative_inputs` | 13.5 s |
| 25 | derivative-history | calendar 03:30 | deep backfill | |
| 26 | references | 3,600 s, immediate | reference series (`refs`) | 20.8 s |
| 27 | alts-history | 300 s, immediate; self-retrigger while `in_progress` | alt basket history | |
| 28 | scraper | 4 h | idea harvest (`brain/scraper.py`) | |
| 29 | crawler | 6 h | | |
| 30 | rent | 3,600 s | `RentKeeper.tick()` (weekly 50 USDT bar) | |

Non-worker threads [V unless noted]:
- `stall-monitor` (30 s poll; **legacy diagnostic only, the external observer owns death detection**).
- `tg-listener` (Telegram), `owner-interface` (+1 thread per IPC connection), the owner `flusher` (`owner/service.py:721`).
- `bootstrap-protection` (30 s protection snapshot), `macro-schedule`.
- `paper-funding-evidence` and `stage5-public-evidence`, `execution-public-evidence` [I], `attention-collector` [I, legacy-path only].
- Transient: `market-initialization` (boot), `candle-acquisition` (on-demand background drain), `cycle-sampler`, and `ThreadPoolExecutor` pools (`agents/validate.py`, `observability/declared.py`, `kernel._tg_worker`).

### 1.5 Locks and ordering [V]

- `execution_lock` (RLock) serialises venue-mutating work: `commands`, `account`, `positions`, `coordinated_intake` (inside `decisions`).
- `decision_lock` (RLock) is held across the **entire `view.cycle()`**, including order submission (`_try_enter`). `apply_learning` and `reload_model` also take it.
- `Journal._write_lock` (a process-wide `threading.Lock`) serialises **every** journal write from all 30 threads.
- `control_fence` is a cross-process `flock` on `luffy.db.control.lock`, taken by `ControlStateMachine.set()` and the executor's final entry boundary. It is not reentrant.
- `Risk._lock` plus `BEGIN IMMEDIATE` (`_durable_hold`) is the Risk durable-state hold.
- `Executor._entry_lock` and per-symbol locks, `DataFeed._cache_lock` / `_acquisition_lock` / `_acquisition_queue_lock`, `mp._FRONT_LOCK`, `mp._WARM_LOCK`.

### 1.6 Concurrency bottlenecks (GIL, thread-local connections, locks)

**GIL.** The runtime is CPython 3.12.3 with pandas 3.0.5, numpy 2.5.2, on 4 cores and 7.7 GB RAM. The last `decisions` run was 6.90 s wall for 6.40 s CPU, so it is almost purely CPU-bound pure-Python/pandas work. Those threads cannot run in parallel with each other. Only the numpy/SQLite/zlib C sections release the GIL (`core/evidence_zlib.py` explicitly uses `PyDLL`, which **retains** the GIL). I/O-bound workers (account, positions, derivatives) therefore compete for the interpreter with the 60 s scan. The `kernel.py:601` comment says as much about a CPU-heavy research thread. Mitigations in place: `MALLOC_ARENA_MAX=2`, `malloc_trim` after each decisions run, and the research search moved to an out-of-process child.

**`threading.local` connections.** `Journal`, `DataFeed`, `DerivFeed`, `RefStore` and others each keep one connection per thread (`Journal._conn`, `feed.db`, `derivatives.db`). With 30 workers plus the dashboard, `luffy.db` accumulates up to about 35 live connections. Each has its own page cache, its own `journal_evidence.install(conn)`, and its own user-defined SQL functions. A thread that dies without closing keeps a connection and possibly a read snapshot (WAL pinning risk [I]). `sqlite_tx.abort/discard` exists to evict a connection left in a transaction.

**Write serialisation.** In-process, `Journal._write_lock` allows one writer at a time. SQLite WAL allows one writer per database file. The two layers hide each other's waits.

**Critical friction: a venue HTTP call is made inside the journal's writer transaction [V].**
`engine/entry_authority.py:594-624` (`submit`) opens `with manager._durable_hold() as conn:`. `_durable_hold` (`risk.py:597`) takes `Risk._lock`, enters `Journal._tx()` (which takes `_write_lock`), and runs `BEGIN IMMEDIATE`. Inside that block, `executor.ex.create_order(...)` is called synchronously. For the duration of the market-order round trip:
- every other journal writer in the kernel blocks on `_write_lock` (equity, outcomes, learning, decisions);
- every other process (dashboard, observers, accounting worker) that writes `luffy.db` waits on SQLite's lock (`busy` timeout 30 s on `Journal` connections).
- The `control_fence` flock is also held.
Afterwards `_confirm_fill` sleeps up to 0.8·(1+…+6)=16.8 s while the `decision_lock` is held (outside the DB transaction), which stalls `apply_learning`/`reload_model`. A recorded `journal_write` incident in `data/safety_health.json` shows `SQLITE_BUSY_WRITABILITY_UNRESOLVED` after 13.1 s. This is an observed manifestation of this class of contention.

**Other measured contention.** `derivatives` took 161.8 s per pass, with its stack inside `mp.plan_append` (market-provenance ancestry read) and `DerivFeed.save`. `derivative-needs` has a max of 58 s. The `commands` worker logs one INFO line per second. The `decisions` batch is thrown away if the model generation changes or more than 60 s passes, which would repeat work if learning applies frequently.

---

## 2. Data storage, provenance and I/O

### 2.1 SQLite inventory [V]

| File | Size | Role |
|---|---|---|
| `data/luffy.db` | 7.0 GB, WAL | **Journal + control plane + research ledgers + Factory.** 86 tables (2.1.1) |
| `data/candles.db` | 5.6 GB | OHLCV projection + receipts (`market_revisions`) + `refs` |
| `data/derivs.db` | 8.9 GB | derivative series + acquisition cursors + receipts |
| `data/attention_learning.db` | 14 MB | attention-learning episodes and learning-registration tables |
| `data/attention.db` | 34 MB | **dormant** since 2026-10-23 (legacy `_attention_call` path only) |
| `data/runtime-portfolio.db` | 2.2 GB | portfolio-checkpoint proposals (last written 2026-10-24) |
| `data/investigation.db` | 27 MB | continuing investigations (`auto_vacuum=FULL`, size-capped) |
| `data/execution-evidence.db` | 49 KB | execution evidence |
| `data/accounting-worker/queue.db` + 847 JSON artifacts | | accounting queue |
| `/mnt/luffy-data/research/compositional/{research,questions,published-context}.db` | 0.4 / 0.3 / 6.4 MB | **Closure research store**, question bank, published context |
| `data/safety_health.json`, `heartbeat_luffy.json`, `cycle_runtime.json`, `kernel_instance.json` | | non-SQLite shared state files |

### 2.1.1 `luffy.db` table families (86 tables)

- **Decision/trade journal:** `cycles`, `votes`, `decisions` (20 cols incl. `strategy_ids`, `signals_json`, `meta_p`, `scan_id`, `instrument_binding_json`, `reason_codes`, `strategy_selection_json`), `outcomes`, `trades` (incl. `entry_identity_json`, `initial_risk`, `mfe_r/mae_r`), `equity` (55,327 rows), `equity_provenance`.
- **Execution/accounting:** `execution_requests` (`logical_id`, `intent_hash`, `client_order_id`, `state`), `execution_accounting`, `trade_accounting_bookings`, `trade_legs`, `trade_fills`, `versioned_paper_*`, `venue_position_snapshots`, `protection_boots`/`protection_evidence`.
- **Control/KV:** `state_kv` (dashboard/control writes), `kv` (not listed in the introspected set but referenced by `Journal.kv_get`), `control_events`, `owner_audit`, `owner_requests`, `paper_risk_kv`, `brain_events`.
- **Strategy/Factory:** `strategies` (legacy genome and spec rows, `spec_json`), `strategy_versions`, `strategy_version_events`, `strategy_version_installs`, `strategy_validation_receipts`, `strategy_probation_receipts`, `strategy_capacity_receipts`, `strategy_approval_requests/decisions`, `strategy_governor_events`, `strategy_first_live_deployments`, `strategy_legacy_authorities`, `strategy_owner_demo_versions`.
- **Research ledger (legacy):** `research_combos`, `research_candidates`, `research_tests`, `research_budget`, `research_controls`, `research_gauges`, `research_slices`, `research_batches`, `research_archive`, `research_combos_legacy_sg04`.
- **Research bank (new):** `research_questions`, `research_plans`, `research_evidence`, `research_results`, `research_runs`, `research_bank_objects`, `research_next_questions`, `research_registrations`, plus six `research_unreadable_*` quarantine tables. Immutability is enforced by SQL triggers (`RAISE(ABORT,…)`; 82 triggers in total).
- **Market-data receipt tables duplicated into luffy.db:** `market_revisions`, `market_raw_sources`, `market_series_meta`, `market_change_log`, `market_meta_global`.
- **Learning:** `learning_registrations`, `learning_actions`, `learning_outcome_captures`, `learning_produced_chains`, `learning_decision_source_manifests`, `learning_source_blobs`, `learning_chain_dispatch_meta_v1`, `learning_capture_failures`.
- **Admission/registry:** `attention_selections`, `attention_cursor`, `attention_fetch_outcomes`, `registry_*`, `universe_*`.
- **Blob store:** `journal_evidence_blobs_v1` (zlib, content-addressed), `journal_representation_v1`.

Live row counts: `decisions` 775,033 (Aug 115,863 · **Sep 561,299** · Oct 97,871 · none since 2026-10-24), `votes` 5,414,092, `cycles` 775,049, `outcomes` 732, `trades` 65 (all closed, `exec_mode=live`), `execution_requests` 0.

### 2.2 `candles.db` [V]

- `candles(symbol, tf, ts, open, high, low, close, volume, taker_buy)` PK `(symbol,tf,ts)`: **legacy projection**, 3,832,277 rows. `candle_floor` (65 rows): where venue history begins. `refs(key, ts, open…volume)`: reference series, 108,729 rows.
- Receipt tables: `market_revisions(revision_id PK, series_key, event_ms, available_ms, observed_ms, record_json)` with index `market_revision_cut(series_key,event_ms,available_ms,observed_ms)`; 2,260,051 rows across 229 series (210 candle + 19 reference). `market_raw_sources(content_hash, source, raw_json)` is a content-addressed text store (2,005 rows). `market_series_meta(series_key, gen, epoch, idents, partial, resolved, scan_event, scan_json)` is the "witness". `market_change_log(series_key, bucket, floor_ms, hi_ms, destructive)` is the change feed. `market_meta_global` holds the witness schema version (3).
- Write pattern: per `(symbol,tf)` single REST fetch → `mp.annotate` → `mp.prepare` → `mp.plan_append` (reads ancestry **before** the write lock) → `BEGIN IMMEDIATE` → `mp.append` + `save_projection` into `candles` → commit → `mp.refresh_entries`. Closed bars only are written to the projection (`closed_bars`); partial bars get a receipt only.

### 2.3 `derivs.db` [V]

- `derivs(symbol, series, ts, value)` PK `(symbol,series,ts)`. Series: `funding, oi, taker_ratio, ls_ratio, ls_account_ratio, basis`. `ls_ratio` (Binance topLongShortPositionRatio) and `ls_account_ratio` (globalLongShortAccountRatio) are deliberately two series (measured correlation −0.64).
- `deriv_acquisition_cursor(cursor_key, series_key, source, instrument_id, period, cov_lo, cov_hi, last_full_ms, unproven, gaps_json, updated_ms, passes, schema)` is the proven-coverage cursor for incremental tails.
- Same `market_*` receipt tables. `DerivFeed._configure_connection` sets `busy_timeout=5000`, `temp_store=MEMORY`, `cache_size=-8192`, `journal_mode=WAL`, `synchronous=NORMAL`.

### 2.4 `attention_learning.db` and `attention.db` [V]

`attention_learning.db`: `episodes(id, protocol_id, symbol, created_ms, deadline_ms, status, prediction, outcome)`, `forecast_sources`, `protocols`, `population_events`, `population_meta`, `collector_receipts`, `diagnostics`, plus `learning_*` and blob tables. It is written by the cron-run `observability.learning --once` observer (lock `attention_learning.lock`), not by the kernel. `attention.db` was last written 2026-10-23, when the lean scan replaced `_attention_call` (see 3.1). `lean_scan.py` contains no attention calls, so config `attention.enabled: true` is now largely vestigial for the trading path [V].

### 2.5 The ingest and provenance engine

**There is no tick stream.** The flow is:

```
Binance fapi REST (raw /fapi/v1/klines via ccxt, fallback fetch_ohlcv)   [DataFeed._klines]
  → DataFeed._frame  (validated DataFrame; taker_buy = real aggressor volume or NaN)
  → DataFeed._received_frame → mp.annotate(df, instrument_id, source, kind='candle',
        received_ms = self._clock_ms()  (ONE clock for the whole retrieval, incl. pagination),
        request_started_ms, raw = exact provider rows)
  → mp.prepare → mp.plan_append → mp.append (+ legacy projection write)
  → mp.refresh_entries → mp.CURRENT (CurrentCache, 256 MB / 256 entries / 400k rows, pins held+shortlist)
  → readers: DataFeed.ohlcv_asof / latest_ohlcv / replay_ohlcv / retrospective_ohlcv
```

**Receipt construction** (`market_provenance.py:225-282`):
- Per bar `body = {instrument_id, source, kind, event_time_ms, observed_at_ms=received_ms, available_at_ms=received_ms if event ≤ received and no reversed clock else None, request_started_ms, request_id, content_hash, supersedes, quality, bar_state, raw_json, transform_version, max_age_ms}`.
- `content_hash = sha256(canonical({'raw': raw_record, 'value': value}))`.
- `revision_id = sha256(canonical(body))`.
- `canonical` is `json.dumps(sort_keys=True, separators=(',',':'), allow_nan=False)`.
- Quality is derived locally: VALID, INCOMPLETE (bar not yet closed at receipt), INVALID (OHLC inconsistency, non-finite, event after receipt, reversed clocks), or UNKNOWN. `bar_state` is FINAL or PARTIAL.
- **Publication time is never inferred.** `available_at_ms` is the local receipt time, which is conservative (never earlier than the truth).
- Large `source`/`raw_json` strings (at least 256 bytes) are stored once in `market_raw_sources` and referenced as `!luffy-market-text.v1!<sha>`. Every read re-verifies the reference, `content_hash`, `revision_id`, and the indexed clocks (`_verify_row`), so corruption raises `market_revision_content_corrupt` instead of being silently consumed.
- Revisions chain by `supersedes` (the `_current_parents` ancestry). The partial-bar record for the forming 4h ETH bar is superseded repeatedly (2,982 revisions for one 4h series, which is the cost of re-observing every forming bar).

**Read modes:**
- *As-of / current* (`load`): for each `event_ms`, the **latest** eligible revision with `available_ms ≤ cut AND observed_ms ≤ cut`.
- *Replay* (`replay_tf`): for each event, the **first** eligible receipt, and only if `available_ms ≤ event_ms + replay_deadline_ms(tf)` where `replay_deadline_ms = event + TF_MS + (TF_MS + TTL)`. A correction that arrived later was not knowable then.
- *Retrospective* (`retrospective_ohlcv`): reads the `candles` **projection**, tries to match each row to a receipt by exact OHLCV equality, and tags everything unmatched `quality='UNKNOWN'`; `taker_buy` is nulled unless the matching receipt agrees. Its docstring and attrs state it "cannot establish as-of evidence".

**Why backfilled data has no REPLAY receipts [V + I].** Backfill (cold symbols, `_fetch_paged`, `deepen_*` scripts) fetches bars days or years after they closed. `mp.annotate` stamps `available_at_ms = received_ms`, the local fetch time, so for historical bars `available_ms − event_ms` is far larger than the replay deadline. The replay filter `available_ms <= event_ms + replay_deadline_ms` excludes them by construction, and they remain legitimate only for `RETROSPECTIVE`. Measured on `candle:ETH/USDT:4h`: **2,982 revisions, 87 within the replay deadline**; observed range 2026-09-02 → 2026-11-03. Pre-receipt history exists only in the `candles` projection (3.83M rows against 2.26M revisions across 229 series), so about 1.57M projection rows have no receipt [I]. This is the root cause behind the "REPLAY receipt requirement" blocking the legacy discovery path and the introduction of the `RETROSPECTIVE` mode in closure research. It is a design property, not a defect: PIT truth cannot be manufactured after the fact.

### 2.6 WAL, checkpointing and lock mechanics [V]

| Store | `busy_timeout` | `synchronous` | WAL notes |
|---|---|---|---|
| `luffy.db` via `Journal._conn` | `sqlite3.connect(timeout=30)` | **default (FULL)**: `Journal` never calls `apply_wal_pragmas`; `PRAGMA journal_mode=WAL` is in the `SCHEMA` string, plus `foreign_keys=ON` | WAL 13.9 MB |
| `candles.db` via `DataFeed.db` | 5 s (`sqlite_tx.BUSY_TIMEOUT_S`) | NORMAL (only if journal_mode is WAL) | WAL 6.8 MB |
| `derivs.db` via `DerivFeed` | `SQLITE_BUSY_MS=5000` | NORMAL, `cache_size=-8192` | WAL 10.8 MB |
| read-only handles | `?mode=ro` + `PRAGMA query_only=ON` | | |

- **Auto-checkpoint is left at SQLite's default** (1,000 pages). The only explicit `wal_checkpoint(TRUNCATE)` is in the closure auto-study snapshot path (`research/closure/auto.py:143`). Nothing in the kernel checkpoints deliberately [V]. Long read transactions (`immutable_read_batch`, `consumers.py` `BEGIN` + `query_only`) can pin WAL growth [I]; current WALs are small.
- **Transaction scopes:** `Journal._tx()` is `with _write_lock:` → connection → body → `commit()`; on any `BaseException`, `sqlite_tx.abort` rolls back or discards the connection and `safety.journal_failure` classifies storage errors. Callers who need writer ownership up front issue `BEGIN IMMEDIATE` explicitly (`Risk._durable_hold`, `attention` selection insert, `DataFeed` writes). `Journal.query()` does **not** commit (per `CLAUDE.md`).
- **Single-writer discipline** is an in-process lock plus SQLite's file lock. There is no write queue and no batching service. Producers write inline.
- **Integrity guards:** SQL triggers for immutability, UDFs `learning_target_write`, `strategy_governor_write`, `strategy_approval_write` gate sensitive tables by a per-thread flag set by the code path that is allowed to write (a convention enforced inside the DB, but per-connection and trivially settable by any code in-process).
- **Schema migration is ad hoc:** `Journal.__init__` runs `executescript(SCHEMA)` and then a list of `ALTER TABLE … ADD COLUMN` statements inside `try/except: pass`. There is no migration version table for `luffy.db`; `market_meta_global.schema` versions the witness only, and `journal_representation_v1.status` gates a "detail.v1" blob re-representation. A new column is attempted on every start.
- **Storage maintenance:** `persistence/storage_cleanup.py` (991 lines), `compact_journal.py`, `backup.py`, `cold_archive.py` exist. Their schedules are [U].

---

## 3. Quantitative engine and decision pipeline

### 3.1 What actually runs per 60 s tick [V]

```
market worker (60 s)                                   decisions worker (event)
 ├ analysis_context(runtime) → "view" (a Kernel facade object cloned per batch)
 ├ view._admission_plan()                              ├ D = decision_lock
 │   selection_persist_verify (1.76 s CPU)             ├ stale check: model gen / >60 s → discard, re-trigger market
 │   attention admission: broad=439 symbols observed,  ├ view.cycle() → lean_scan.run(view)
 │   deep=30 admitted (max_deep_slots 30)              │   for each of 30 symbols (sequential):
 ├ coordinated_inputs.active_plans                     │     snap = view._candidate_snapshot_for()   (≈ 79 ms/sym CPU)
 ├ mp.CURRENT.set_pins(held ∪ shortlist)               │     readiness(snap, plan.needs[...])
 ├ lean_scan.prepare(view) [mp.cache_only()]           │     orchestrator.decide(snap, ready, measurements=False)  (≈ 133 ms/sym)
 │   scan_plan.build → per-symbol requirements         │     if action != HOLD and allowed:
 │   BatchFeed (one read per series/window per batch)  │        view._try_enter(...) → risk → authorize → executor.open
 │   refs, BTC frames, peer frames                     ├ coordinated_intake.run_once under execution_lock
 └ decisions.trigger()                                 └ queue_portfolio(view) if anything entered/paper-entered
```

`Kernel.cycle()` (`kernel.py:1386`) dispatches to `lean_scan.run` when `_lean_scan` is set (it always is under `KernelRuntime`). The rest of `cycle()` (analyst blending, `_manage_paper`, attention causes, `learning_dispatch`, legacy `heartbeat.beat`) is the **legacy branch** and runs only when `runtime is None` (never in production) [V].

**Consequences of the lean path [V]:**
- Analyst votes are not computed (`measurements=False`). `strategy_leads` and `require_strategy_signal` make the strategy signal the whole score. In `decide()` the net is `chosen.confidence × ±1`, compared with `base_threshold` (+0.07 soft bump if the 4h trend opposes it; hard veto at |htf| ≥ 0.8 and not VOLATILE).
- **Decisions are persisted only at an order admission boundary** (`evidence()` is invoked from `decision._persist_trade_evidence`; input policy string `strategy_required.trade_evidence_only.v1`). Non-trades are no longer journalled. This is why `decisions` drops from 561k rows in September to 98k in October and nothing after 2026-10-24. It directly contradicts the stated goal in `CLAUDE.md` of "learn from outcomes including non-trades". `schedule_outcomes=False` also prevents shadow-outcome grading for HOLDs. The live `latest_scan` shows `BUY … skip_reason='state=FROZEN'` with `persisted: False`.
- 3-strategy contention: `strategy/plan_owner.select()` returns `status='REFUSED'` with basis `OPPOSING_STRATEGIES_NO_JUSTIFIED_COMPARISON` or `PLAN_OWNER_UNRESOLVED_NO_COMPARABLE_EVIDENCE` whenever two active strategies emit on the same symbol in the same scan, even in the same direction. Only one strategy may speak per symbol. With three active, same-direction coincidences (EMA stack and funding-filtered trend pullback both fire in trends) cancel each other rather than ranking. There is no comparator, so the refusal is by design.

### 3.2 Where the CPU goes (last run, 30 symbols) [V]

| Stage | calls | CPU s | share of the 6.4 s |
|---|---|---|---|
| `scan.strategies` (`orchestrator.decide`: regime classify, htf score, evaluators, select) | 30 | **3.98** | 62% (avg 133 ms, max 638 ms) |
| `scan.collection` → `deep.snapshot` → `deep.market_data` | 30 | 2.35 | 37% (1.62 s exclusive: frame assembly, receipt decode) |
| `scan.validation` | 90 | 0.65 | 10% |
| `input.candle_read` / `input.derivative_read` | 90/42 | 0.06 / 0.02 | ~1% |
| `scan.controls` / `scan.coordinated_entry` | | ~0 | |

In the `market` worker: `admission.select_persist_verify` takes 1.76 of 2.05 s CPU. `CurrentCache` is at 244/256 entries and 195 MB / 268 MB with 222 pinned entries, so at the next 4-6 symbols of growth it is full with nothing evictable [I]. In this run it had 6,238 hits against 2,046 misses and 174,678 receipt rows decoded.

### 3.3 Data movement: SQLite → `CurrentCache` → DataFrame [V]

1. `Worker.market` takes the `immutable_read_batch()` context and `mp.cache_only()` (reads may not hit SQLite/network; misses enqueue a warm refill (`_WARM`, ≤1,024) that `input-warm` services every 2 s). A cold entry returns `None` with reason `warming` or `witness_unproven`, which the strategy sees as a missing input.
2. `mp.load_current_candles` selects, per event, the latest revision. The frontier index (`_Frontier`, 128 MB LRU `FRONTIERS`) keeps the winning rowids so refresh is incremental (`incremental_refreshes` 1,490; `frontier_rebuilds` 42).
3. `_assemble` calls `_verify_row` per row: `json.loads(record_json)`, a text-reference expansion, `digest()` ×2 (each a `json.dumps(sort_keys=True)` + sha256), plus `json.loads(raw_json)` for the content hash. Then `pd.DataFrame(list_of_dicts)` builds a frame with 16 receipt columns per bar (including the raw provider row as a string).
4. `BatchFeed.fetch_ohlcv` returns `frame.tail(limit).copy(deep=True)` per consumer.
5. Snapshot (`Snapshot.dfs[tf]`) is assembled per symbol; `eligible_frame`/`usable_current` set non-VALID/STALE values to NaN (never zero).

### 3.4 Strategy contracts (the three active) [V]

Authority: `strategy_owner_demo_versions` (`schema owner-demo-strategy-version.v1`, `scope: BINANCE_DEMO_ONLY`). All three were activated by the owner on 2026-10-25/26 without a validation prerequisite; this is a deliberate lane and is excluded from the referee/Factory per `CLAUDE.md`. Entry is gated by `factory_handoff.live_entry_block` → `owner_demo.block` and `owner_demo.venue_block` (the submitting client must actually be Demo).

| Strategy | Paradigm | Rule | Exits | Notes |
|---|---|---|---|---|
| `ematrend_g_edc6` "EMA Stack Pullback" | **Legacy genome**, `kind='ema_trend'`, `spec_json` is NULL. `params`: `adx_min 22`, `pullback_atr 0.8`, `trend_tf 15m`; regime TRENDING_UP/DOWN | `eval_ema_trend` (`strategy/library.py:24`): needs ≥ 210 bars on `trend_tf`; ADX ≥ min; long if `price > EMA20 > EMA50` and pullback to EMA20 ≤ `0.8·atr'` where `atr' = max(|EMA20−EMA50|, 0.2% price)` (an "ATR" that is really the EMA spread); short mirrored | no `SpecExit`; `_protection_for(None…)` falls back to **config** `risk.stop_loss_atr_mult 2.5`, trailing 2.8, TP 4.5 on the **15m** execution ATR | confidence hard-coded 0.6+0.02·min(len(dfs),3) (long) / 0.62 (short). Owner activation bound it to an `evaluator_sha256`. |
| `auth_donchian_breakout_trail` | **DSL spec**, origin `measured` | `entry_long: close > donchian_hi(100)`; `entry_short: close < donchian_lo(100)`; both directions, futures, 4h | ATR stop 2.0, ATR trail 4.0 arming at 1.0R, no target, time stop 500 bars | provenance says walk-forward median OOS PF 1.447, win-rate 38.85%, 487 trades on 16 symbols |
| `spec_funding_filtered_trend_pullback` | **DSL spec**, origin `strategist` (LLM-authored, `idea_id tv_23713897`) | long: `close>ema(200) and ema(50)>ema(200) and prev(close,1)<ema(50) and close>ema(50) and oi_ret(24)>0.02 and funding_z(96)<1.5 and rel_volume(24)>1.0`; short mirrored; filter `adx(14)>20`; regime TRENDING_* | ATR stop 2.5, ATR trail 3.0 arming 1R, no target, time stop 200 bars | requires `funding`, `ohlcv`, `open_interest`; observed refusal `history_gap_in_consumed_tail:15m` |

**Contract between strategy and pipeline:**
- `EVALUATORS[genome.family](genome, snap) → StrategySignal | None` with `StrategySignal(strategy_id, strategy_name, symbol, action BUY|SELL, confidence∈[0,1], rationale, params)`.
- Specs register `compiled.to_evaluator()` under family `spec:{id}` at population load (`kernel._load_spec_population`). The compiled evaluator is `_diagnostic_capable` and returns the reasons `insufficient_inference_history_htf`, `history_gap…`, `required_input_missing:<key>`, `quality_condition_failed`, `world_dependency_unavailable`.
- A spec signal carries `confidence=0.6` constant and `params` with `spec_sha256`, `compile_identity`, `signal_bar_close_ms`, `signal_bar_age_min`, and a full `market_provenance` source list (revision ids and content hashes of every frame used). The `signal_bar_age_min` is **recorded, not gated** (comment in `compile.py`).
- Population gating before evaluation: `is_trade_eligible`, market in `genome.markets`, `regime_allows`, `symbol_allows` (the declared include list is the universe).
- All three are active as `state='active'`; the `strategies` table also holds 15 retired rows.

**Signal → order request:**
```
StrategySignal → plan_owner.select → Decision(action, score=±conf, strategy_selection receipt)
  → lean_scan.run: threshold gates, HTF veto, 45-min same-direction cooldown (SELECT on decisions)
  → Kernel._try_enter
       1. top_strategy = _top_strategy(d)   (None → "no justified strategy plan owner")
       2. fh.live_entry_block  (fence; for a blocked version → paper executor instead)
       3. ATR on the SPEC's timeframe (SpecExit.timeframe) else 15m; sl,tp from _protection_for / entry_contract
       4. risk.check_entry → SizingResult(ok, amount, size_usdt, risk_usdt, size_mult)
       5. meta-size shrink (0.25-1.0) and per-version allocation share (entry_authority.current_allocation)
       6. _entry_provenance → identity (frozen strategy version), position_mode.bind_new_entry
       7. risk.authorize_entry → EntryPermission  (exact, Risk-issued)
       8. executor.open(...)
```

### 3.5 Quant debt and vectorisation gaps [V unless noted]

1. **Per-row JSON + SHA-256 on every read.** About 175k rows decoded per 31 scans; each row costs 3 `json.loads/dumps` and 2 SHA-256 digests. `CurrentCache` hides most of it, but `deep.market_data` still costs 1.62 s exclusive CPU per tick. The same verification runs on cold paths, retrospective loads and research reads.
2. **Write-path `iterrows` triplication.** `annotate` (iterrows + per-row digest ×2), `seal` (iterrows + digests again) and `prepare` (iterrows once more, re-sealing) all iterate the same frame row by row; one retrieval seals each bar at least twice. Paged backfills can be tens of thousands of rows.
3. **Receipt columns ride inside the OHLCV frame.** Each bar carries 16 object/str columns including `raw_json`, so the core numeric pipeline does pandas object-dtype copies (`copy(deep=True)` per consumer in `BatchFeed`). No separation of numeric arrays and the provenance sidecar.
4. **Full-window recompute for a last-bar decision.** The DSL evaluator (`entries_detail`) builds boolean vectors for the whole window (Donchian needs 100, EMA200 needs 200+, up to 617 bars) for every symbol on every 60 s tick, but consumes only index `[-1]`. Nothing is carried incrementally between scans, even though new information arrives only at bar close (4h strategies are re-evaluated about 240 times per bar for no new data). `RunTime.changed()/input_fingerprint` exists but is skipped in the lean path (`unchanged_inputs: null`).
5. **Legacy `eval_ema_trend` recomputes EMA20/50/200 and ADX from scratch** over a ≥ 210-row DataFrame per symbol per tick. `orchestrator.decide` also calls `classify(snap.df('15m'), snap.df('1h'))` and `htf_trend_score(snap.df('4h'))` per symbol regardless of whether any strategy will be eligible.
6. **Sequential per-symbol loop** (`lean_scan.run`): 30 symbols × (snapshot + decide) in one thread; no process-level parallelism (the `Snapshot`/view object is not shareable across processes).
7. **Admission selection persisted and re-verified every tick** (1.76 s CPU) although the shortlist rarely changes.
8. **The DSL `entry_long` strings are parsed/compiled to trees at load**, which is fine; but each `StrategySignal.params['market_provenance']` re-lists every revision id and content hash of every frame (hundreds of strings) per signal, and is then stored into `decisions`/blob storage.
9. **`DataFrame` construction from lists of dicts** (`_assemble`) rather than from a column-oriented read.
10. **Backtest side:** `strategy/vector_backtest.py` (`walk_table`/`simulate`) is already vectorised and benchmarked (`scripts/bench_vector_backtest.py`, >20× target). The live evaluator and the backtest evaluator are **two code paths with an equivalence harness** (`scripts/backtest_equivalence.py`), not one engine.
11. **Time-axis fragility:** the 4h and 1h frames are separately fetched and re-derived; the legacy `backtest.resample()` is flagged in `CLAUDE.md` as unsafe for open-labelled HTF bars.

---

## 4. Trade execution, portfolio and accounting

### 4.1 Order flow [V]

```
Signal ─► Decision (strategy_selection receipt) ─► _try_enter
   ├─ fence: factory_handoff.live_entry_block → owner_demo.block (+ venue_block must be 'demo')
   ├─ Risk.check_entry (risk.py:704-804)
   │    state ACTIVE else refuse; inputs finite>0; update_equity(); halt_breached → RiskError (kernel flips HALTED)
   │    daily breaker (-6%); max_open_positions 8; one position per symbol
   │    budget = min(equity·0.5%, heat headroom (15%), per-symbol headroom (8%)) × derisk multiplier
   │       × proving multiplier (0.5 for first 30 closed trades)
   │    amount = allowed / max(price·stop_frac, 0.4% price); margin caps: 20% per position, 70% total; leverage 5
   ├─ Risk.authorize_entry → EntryPermission (exact, bound to decision, size, stops, identity)
   └─ Executor.open (executor.py:129)
        control_fence + persisted-state re-read (entry_block); recovery_pending; exec_mode must be 'live'
        live_entry_block + owner_demo.venue_block again (independent re-check)
        entry_authority.submit  (entry_authority.py:529)
           control_fence (flock) → _durable_hold (BEGIN IMMEDIATE):
              reject duplicate logical_id/decision_id; INSERT execution_requests state='RISK_AUTHORIZED';
              recovery.begin(position)           ← committed BEFORE the order (crash ⇒ reconcile)
           second _durable_hold: re-validate, then ex.create_order('market', ... client_id='lr_'+sha256(logical_id)[:28])
              InvalidOrder / InsufficientFunds → REFUSED; any other exception → recovery 'entry_submission_ambiguous'
        _confirm_fill: up to 6 polls, sleeps 0.8·(attempt+1) s; fill confirmed only when terminal and average>0
        _place_native_stop → protective.place_stop (Binance algo/conditional STOP_MARKET, MARK_PRICE trigger)
              failure ⇒ adopt a matching venue stop, else fall back to STOP_MARKET; none ⇒ _recover_unprotected_entry
        journal.add_trade(pos, accounting={basis:'entry_order_confirmation', order_id, confirmed_quantity, confirmed_price, ...})
        set_decision_entry_price; schedule_outcome
```

Observations:
- **Idempotency design is strong:** the client order id is a deterministic hash of the logical id, the reservation row commits before submission, and ambiguous submissions are *never replayed* (resolved by the reconcile/recovery loop and `Supervisor`).
- **Venue vs. journal ordering:** the position is journalled only after the fill is confirmed *and* a stop exists. A confirmed fill with a failed stop triggers `_recover_unprotected_entry` and returns `None`, so a position can exist on the venue and not in `trades` until reconciliation adopts it.
- **Exits** (`engine/exits.py`, `ExitEngine`): stop/target/trail run in-process, with the exchange stop as the independent protection. `SpecExit.from_spec(spec, versioned=…)` carries the spec's own stop/trail multiples and timeframe. Trails ratchet only tighter (hysteresis 0.1·ATR) by placing the replacement stop and cancelling the incumbent via `protective` (algo-order IDs, not normal order IDs). Versioned specs use `engine/versioned_exits.py` and `strategy/exit_policy.py` (`EXIT_SEMANTICS_ID`). Three exit engines coexist: the legacy `ExitEngine`, `versioned_exits`, and the coordinated (hedge/pair) unwind modules.
- **Control states** (`core/types.py ControlState`): `ACTIVE` entries and exits; `FROZEN` and `RECOVERY` exits only; `HALTED` neither (per `ControlStateMachine.manages_exits`). The exchange protective stops persist regardless.
- **The scan-path entry is wrapped by `scheduling/coordination.py:coordinated`** (decorator on `Kernel._try_enter`). `lean_scan.run` passes `balance=None, closed_count=0`, and the wrapper replaces them. Under `execution_lock` it: rejects if the kernel is stopping; rejects a snapshot older than 60 s (`scheduled_execution: stale_decision`); calls `root._risk_step()` (a **venue REST call**) for balance and risk status; refuses if `can_enter()` is false, `risk_state != 'ok'` or the daily breaker is hit; re-reads the closed-trade count; then calls the real `_try_enter`. On success it triggers `account` and `positions` via `fill_event()`. Lock order on an entry is therefore `decision_lock` → `execution_lock` → `Risk._lock` → `Journal._write_lock` (plus the `control_fence` flock). I found no path that takes them in the reverse order [V: grep of `execution_lock` and `decision_lock` users], but the order is implicit and enforced only by convention. Each entry also costs one extra venue round trip under both locks before any order is placed.
- **Not exercised:** no live entry has been journalled since 2026-09-26, before the lean path and the three owner-demo strategies existed. The end-to-end entry path under the present code has therefore not been observed in production. A demo dry run should precede leaving `FROZEN` [U].

### 4.2 Account state and reconciliation [V unless noted]

- `account` worker (30 s): `supervisor.cycle()` → `state_machine.refresh()` → `_risk_step()` (balance/equity from venue) → `risk_assessment` record → `log_equity`.
- `Supervisor.pass_once` (883 lines) is the recovery state machine (`outcome` PASS/DEGRADED…, `reasons`, `containment_*`). Right now `state_kv.supervisor_status` is `DEGRADED`, stage `DETECT`, `safe_to_activate=false`, with reasons `critical_storage_or_heartbeat_recovery`, `heartbeat:luffy:STALE`, `observed_cycle:luffy:STALE`, `paused_venue_protection:VENUE_STATE_UNCONFIRMED`, `contained_bootstrap_not_ready`. It has written a `supervisor_recovery` control event about every 30 s (851 rows today, 1,913 on 10-25). The reasons may be stale relative to the current fresh heartbeat file (sequence 216, current); **I could not determine from the code read whether the supervisor is mid-clear after this morning's restart or stuck [U]**. Either way the owner froze the system.
- `engine/reconcile.py` (861 lines) and `reconcile_native.py`: compare venue positions with `trades`, "adopt" ghosts, re-arm naked positions, sweep stale stops. The last 6 reconcile events show 0 positions, 0 ghosts, 0 naked.
- `protection_snapshot.py` (1,007 lines) is a read-only, bounded producer of venue-protection truth, run by `bootstrap-protection` every 30 s and evidenced into `protection_evidence` (generation-fenced per boot).

### 4.3 Accounting and ledger: what exists, and the actual "legacy → corrected" transitions

There are **three separate accounting generations**, none of which is "portfolio-entry-booked.legacy" in the live ledger.

1. **Journal booking (E1/E2).** `trades` is the realized-PnL row (`realized_pnl`, `exit_price`, `close_reason`). Historically exits were booked from the order response, and `executor._order_fills` documents that on 2026-09-11 "8 of 8 exits were journalled better than they filled" (the demo market order returns no `average`; the hint was booked). `_book_estimate` is the explicitly-labelled estimate path. Per-trade realized PnL for the 65 closed trades is therefore a mixture of venue-sourced and estimated values [I, since `trade_fills` holds only 7 rows, all `UNATTRIBUTED`].
2. **Booking receipts (E4).** `journal.add_trade(..., accounting=…)` writes `trade_accounting_bookings` (27 rows; 15 of the 65 trades) as an immutable transition chain (`receipts[].kind` = `entry`/`increase`/`partial`/`close`, `before`/`after` trade states, `evidence`). `engine/booking.py:replay_export` validates the chain. `trade_legs` (3 rows, `source='live_booking'`) and `trade_fills` (7 rows) are the venue-fill ledger. 0 of 65 trades have `entry_identity_json`: the identity-freezing feature post-dates every journalled trade.
3. **Whole-trade reconciliation (`engine/trade_accounting.py`, 562 lines).** `VERSION 'whole-trade-accounting.v1'`, plus `hedge-leg.v1`, `closed-interval.v2`, `native-coin.v1`. `legs()` derives ownership of venue fills from the *booking transitions* ("never a symbol/time subtotal"), refuses chain gaps (`booking_chain_gap_retry`), >32 receipts (`order_capture_budget_retry`), and unsupported scope (only `futures`, `live`, one-way USDT-linear/native-coin demo). Funding is paired to income rows with a ±2 s tolerance and **exact count match** (`funding_times_match`); a missing or duplicate cashflow is a refusal, never an interpolation. Native-coin results stay in their own currency and never enter USDT learning without explicit conversion.
   - **Who runs it:** `observability/accounting.py` is an off-path **cron-driven** worker (`--once --enable`, flock `worker.lock`, 45 s `SIGALRM`, `BINANCE_DEMO` only). It keeps a durable queue (`queue.db`, `MAX_JOBS 10000`, `MAX_BYTES 256 MB`, retry 5 min → 8 h, terminal statuses `complete/unsupported_scope/conflict`) and 847 JSON artifacts, and delivers completed receipts into Learning via `learning.producers`. The brief's "queue worker" is this process, not a kernel thread.
   - **Funding payments:** `observability/funding_events.py` and `engine/coordinated_cashflows.py` price funding from Binance public `fundingRate` events. Intervals with no crossing price at zero; events *exactly on* a boundary are never priced (position-held status unproven).
4. **Research-side "portfolio accounting" (the label in the brief).** `strategy/portfolio_evidence.py`: `ACCOUNTING_VERSION="portfolio-exit-realized.v1"` (cost-inclusive net R recognised **once at exit**, events ordered chronologically, "aggregate prior-position exits before same-timestamp entries", stake = closed equity at entry × risk_pct) replaced `LEGACY_ACCOUNTING_VERSION="portfolio-entry-booked.legacy"` (PnL booked at entry). `require_current_accounting` raises `AccountingMismatch` for legacy metadata; `require_compatible_accounting` refuses to compare legacy and corrected numbers, and missing metadata is classified legacy and "not upgraded in place". This is a *research simulator* migration affecting `research/runner.py` and `referee.py` evidence, not the live ledger. Its documented limitations: no open-position mark-to-market, no intrabar drawdown, no margin reservation.

**Reconciliation of balance:** equity comes from venue account reads (`account_observation`, `account_margin_observation` in `state_kv`), not from summing the ledger. `equity` (55k rows) is written with `equity_provenance`. There is no single double-entry ledger: PnL truth is split across `trades`, `trade_fills`, `trade_legs`, `trade_accounting_bookings`, the accounting worker's JSON artifacts, and venue income history. Only 15/65 trades have any booking chain and only about 3 legs / 7 fills exist in the venue-fill tables.

---

## 5. Research, point-in-time and strategy promotion

### 5.1 Two pipelines

| | Legacy discovery search | Retrospective Closure pipeline |
|---|---|---|
| Code | `research/runner.py` (865), `combo.py`, `planner.py`, `growth.py`, `evaluate.py`, `ledger.py`, `referee.py`, `job.py`, `thesis.py`, `control.py`, `thresholds.py`, `slices.py`, `dependence.py` | `research/closure/` (`auto.py` 1,018, `study.py` 755, `development.py`, `qualification.py`, `handoff.py`, `manifest.py`, `calibration.py` 656, `families.py`, `exposure.py`, `store.py`, `publish.py`, `robustness.py`, `objective_admission.py`, `paper_proposals.py`, `coordinated_trial.py`, …) + `research/compose/` (search, proposer, hedge 1,057, catalog, world snapshot) |
| Driver | `KernelRuntime.research` → `ResearchRunner.step(cycle_seconds=...)` in-process: plans a batch (40 combos, 900 s), runs a **niced child** (`core/child.py`, `nice 19`), stands down if the last trade cycle exceeded `skip_if_cycle_s` (45 s) | `KernelRuntime.research` → `launch_role('research')` managed child; `closure.auto` every 12 h creates/resumes a study: publish bounded exact-cut snapshot → derive study from *actual coverage* → bounded resumable units (`intake`, `search`, `develop`, `revise`, `freeze`) in killable, niced, memory-limited children |
| Data mode | **REPLAY** receipts only (`cached_ohlcv` default → `replay_ohlcv`) | **RETROSPECTIVE** (`development_mode`, `recent_retrospective_development: true`): reads the `candles` projection with `quality='UNKNOWN'` where unmatched; "cannot establish as-of evidence" |
| Store | `luffy.db` `research_*` ledger (now empty or archived) | `research.db` (`studies`, `envelopes`, `candidates`, `units`, `runs`, `attempts`, `looks`, `freezes`, `qualifications`, `exposure`, `budgets`, `manifests`) + question bank `questions.db` + `published-context.db` |
| Unit | combo grid over a feature vocabulary (`vocab.py`, `max_parts 6`, `beam 12`, geometries `trail`/`fixed`), horizon `4h` | manifest "study envelope" with typed `Hypothesis → candidate → development result → frozen finalist → qualification packet` |
| Config | `research.*` flat keys | `research.closure.*` (`enabled`, `slot_budget_s 3600`, `paper_intake`, `world_live_enabled`, `proposal_provider_enabled`, `auto` with horizons 4h/1h/15m, `fit_bars 6570/2000/1600`, `max_candidates 8`, `search.max_attempts 24`, `max_fits 40`) |

Since `closure.enabled` is `true`, `KernelRuntime.research` **does not run the legacy discovery search at all**: the comment states "the configured research engine owns this slot". The legacy path survives as code, as the evaluation primitives that the closure `qualification` re-uses (`referee.examine`, `job.referee_job`, the LORD++ `Ledger`), and as `luffy.db` tables (`research_combos_legacy_sg04`: 2,735 rows from 2026-09-13 → 09-18). It is therefore a **shared kernel, not a dead path**: the closure pipeline is built on top of the legacy referee.

### 5.2 Promotion gates: research candidate → `VALIDATED` version [V]

1. **Intake / question** → `research_questions` / bank objects; planner → plan; collector → evidence; resolver → result (`research_*` bank tables, each canonical-JSON + sha256 + immutability triggers).
2. **Search / develop** (RETROSPECTIVE): thresholds are learned **on development data only**, with separately measured mirrors; statuses `DEVELOPMENT_POSITIVE` etc. Ranked, capped at `max_candidates`. One shared calendar cut per horizon (per-symbol cuts leak eras).
3. **Freeze** (`closure/qualification.freeze_finalist`): deterministically pick the best `DEVELOPMENT_POSITIVE` by `(total_pct, −consistency_p, candidate_id)`, record `search_history` of every trial, exact rendered thresholds. Operator-only steps start here: `qualify` and `handoff` are never run by the auto loop.
4. **Qualify** (once per envelope): the existing referee on a *sealed copy of the protected window* with proven UNEXPOSED provenance (`closure/exposure.py`):
   - **Gate 1:** held-out A (unseen markets, same era) **and** B (all markets, later era). Cross-symbol consistency corrected for dependence (`dependence.py`, 200 matched-offset draws; the raw pooled p is never allowed), plus the common-rotation null on B. One candidate spends **one** test whose p is the max of the three.
   - **Gate 3:** book-with vs book-without compounded return over B, return/drawdown no worse. Spends nothing. In closure, the book is `EMPTY_HISTORICAL_BOOK`, so it only asserts standalone compounded return.
   - Constants: `MIN_TRADES 60`, `CONSISTENCY_DRAWS 60`, `MAX_OVERLAP 0.6` (a near-twin costs no budget), `ROTATION_FLOOR_BARS`, `referee_max_draws 19999`, FDR target 0.10, `lord_w0 0.05`, config `referee_forward_only` (B must start after each candidate's selection time) and `referee_first_batch 10`.
   - **Error budget:** LORD++ (`ledger.py`, `fdr.py`): `Ledger.next_alpha` / `record_test` / `complete_look`. Every held-out look spends alpha. The journal's `research_budget` row is `(alpha 0.1, w0 0.05, registered 2026-10-07)`. Retries, renamed slices and missing data may not waive anything.
   - **Statuses:** `QUALIFIED | NOT_QUALIFIED | INCONCLUSIVE | BLOCKED`. `QUALIFIED` requires a registered rejecting gate-1 look, a passing gate 3, and proven UNEXPOSED protection.
5. **Handoff** (`closure/handoff.hand_off`): `fh._verified_historical` → `factory_transfer.transfer` (spent looks copied verbatim into the journal's own budget; `transfer_id` is a hash of the packet) → `factory_handoff.create_version(kind=HQ_KIND)`. All checks precede any write. Outputs `status: VALIDATED`, `execution_authority: NONE`, a `validation_receipt_id`. It is idempotent, and a resume completes only the missing step.
6. **After VALIDATED** (not reachable today): Factory states PROPOSED → install → probation (paper, `strategy_probation_receipts`) → capacity (`strategy_capacity_receipts`, `ESTABLISHED`) → approval request/decision → Governor activation → separate owner `record_first_live_deployment`. `live_entry_block` re-verifies the entire chain *at every entry*. `strategy_version_events` records the transitions.
7. **Hard stop that predates all of this:** `CLAUDE.md` records a "dependence-corrected gate stop" and the cumulative error budget as inviolable; `docs/protocol-changes-20261102.md` and `docs/guard-review-20261102.md` describe the 2026-11-02 changes [U, not read].

**Live status [V]:** studies 1, candidates 24, units 19, runs 8, attempts 24, exposure 5, looks 0, freezes 0, qualifications 0. The closure pipeline has produced candidates but has never reached a freeze or a spent look. The three trading strategies bypass all of this via the owner-demo lane.

### 5.3 Friction points

- **Two evidence modes with different data.** Closure development uses RETROSPECTIVE/UNKNOWN-quality data and qualification uses protected PIT windows; the referee they share was built for REPLAY data.
- **`research/runner.py` `skip_if_cycle_s` is calibrated to a regime that no longer exists.** The comment cites cycle p50 17.7 s; the lean cycle is 7-10 s. Also the research slot waits 3,630 s inside a worker thread, and `research` is a non-daemon thread.
- **State is split across three databases:** the legacy ledger and bank in `luffy.db`, the closure store in `research.db` and `questions.db`, with a config path (`budget_journal_path: null`) that decouples the closure budget from the journal until handoff.
- **Owner-demo authority is a parallel promotion lane** (`strategy_owner_demo_versions`, `owner_demo.py`, `legacy_authority.py` grandfathering) that makes `live_entry_block` branch three ways: versioned, grandfathered, owner-demo. Each branch has its own verification.

---

## 6. Control plane, dashboard, telemetry and supervision

### 6.1 Dashboard and APIs [V]

- `trader/dashboard/server.py` (1,106 lines): `FastAPI(title="Luffy")` run by `uvicorn` at `dashboard.host:port` from config (`192.168.126.131:8080`, a LAN address, plain HTTP). Its own `Journal(luffy.db)` connection for reads. Routers: GraphQL (`api/graphql_schema.make_graphql_router(journal, gateway)`), `owner_api.install(...)` (1,179 lines, prefix endpoints such as `/bootstrap /overview /desk /risk /protection /valuation /trades /strategies /research/board /operations/log /owner-interface /needs-you /needs-you/decision /system /query/{kind} /knowledge /logs /chat`), classic `/api/*` (summary, klines, vault tree/file/graph, pipeline, org, doctrine, logs, brain autopsy, chat) and `ws /ws/live`. A static SPA is served from `frontend/` under `/assets`.
- **Auth** (`dashboard/auth.py`): `DASH_TOKEN` env var; the literal default `luffy` is rejected; `hmac.compare_digest`; header `x-luffy-token`; browser login sets an HMAC-signed 12 h `luffy_session` cookie; **GET-only `?token=` query compatibility** is retained (token in a URL, contrary to the module docstring's "no credentials in URLs"). Origin check on mutating requests.
- **The dashboard never sets control state.** `dashboard/server.py` docstring: "Dashboard → kernel Owner Interface". Mutations go over the owner IPC.
- **Owner IPC** (`owner/ipc.py`): a single `AF_UNIX` stream socket `owner.sock` in a private dir (config `ipc_dir /home/sarmad/.luffy/run`; mode 0700 dir, 0600 socket, `SO_PEERCRED` uid check). Frames are `{"v":2,"channel","nonce","request","mac"}` with HMAC-SHA256 under a per-channel key (`keys/<channel>.key`, channels `dashboard, openclaw, whatsapp, cli`); responses are HMAC-bound to the nonce. 64 KiB frame cap. Semantics: connect failure → `UNAVAILABLE` (never delivered); failure after first byte → `ERROR` with unknown outcome; retry with the same `request_id` returns the recorded result. The module itself states the limit: any process under the kernel's uid can read the keys, the journal, and the trading credentials. `owner/service.py` (863 lines) persists `owner_requests` / `owner_audit` and classifies operations as `READ`, `INTENT`, `CONTAINMENT`, `RECOVERY`. Adapters: `dashboard`, `openclaw`, `telegram` (principal map in config).
- **Telemetry files:** `data/cycle_runtime.json` (per-worker and per-stage timings; `slow_worker_stacks`), `heartbeat_luffy.json` (`luffy-heartbeat.v1`, instance-bound), `safety_health.json` (conditions with incident ids, journal-write incidents, bootstrap block), `kernel_instance.json`, and `logs/luffy.log` (contains NULs; at INFO the `commands` worker alone writes one line per second).

### 6.2 Operator controls [V]

- States live in `kv['control_state']` (read through `Journal.kv_get`); `state_kv` holds dashboard/control side keys. `ControlStateMachine.set()` takes the `control_fence` flock. `set_if_current` does compare-and-set against both the state and the latest control-intent event id, with an optional guard (e.g. Risk release proof).
- `can_enter()` is true only if `entry_refusal(journal, probe=True)` is clear **and** the persisted state is readable **and** `ACTIVE`. An unreadable persisted state means entries are blocked, never "keep the cached value".
- Kill switches: `panic` (`--panic` → owner IPC `cli` channel; no local fallback, so it fails if the kernel is down), `close_requests`, `risk` halt (`RiskError` on drawdown ≥ `halt_drawdown_pct 20` flips HALTED), `daily breaker` (−6%), `MacroGuard` (30 min before / 120 min after high-impact US events; fails open; `macro_guard_operator_hold` holds a macro freeze until the owner releases it), drawdown de-risk steps (8% → 0.5×, 12% → 0.25×), `entry_refusal` (storage/health), `execution_recovery` latch, and `proving_period_trades 30` at 0.5×.
- **Waivers:** no general "manual waiver" mechanism exists. The only matches for "waive/waiver" in `trader/` are `strategy/capacity.py` and `observability/preflight.py`. The nearest constructs are the owner-demo authorisation receipt (`strategy_owner_demo_versions.authorization_reason`), `legacy_authority` grandfather receipts, `Risk.repair_baseline(actor, reason)`, and the `frozen_operator_hold_required` preflight rule. Gates cannot be waived by design (`CLAUDE.md`).
- **Recovery gating:** after a restart the child comes up `FROZEN`; `supervisor.safe_to_activate` and the `RECOVERY_OPERATIONS` owner ops gate resume. `preflight` demands `frozen_operator_hold_required`, `watchdog_hold_missing`, `environment_not_demo`, `providers_not_disabled`, `research_gates_invalid`, `pending_execution_recovery`, `pending_partial_exits`, `pending_execution_requests`, etc. (`observability/preflight.py:258-376`).

### 6.3 Supervision and watchdog [V]

`scripts/watchdog.sh` (cron `*/5 * * * *` and `@reboot sleep 60`):
1. `flag_present safety_monitor.off`? If absent run `scripts.monitor --safety-only` (independent, no venue contact, publishes without needing Journal writes).
2. `pause_state --validate`: invalid control paths refuse all further supervision (exit 2).
3. `watchdog.off` present → log "supervision paused; independent safety pass retained" and exit. **(Not present now.)**
4. `runtime_identity status-only` (lock table + record + `/proc` start ticks + real argv): exit 10/11 → `restart.sh kernel`; exit 0 → restart only if uptime > `STALE_S` (600) **and** the heartbeat bound to *this* instance is missing or older than 600 s; anything else → no action.
5. No dashboard process → `restart.sh dashboard`.
6. `observers.off` pauses the observers; otherwise run `declared --once`, `learning --once` (under `pause_state --lock-run attention_learning.lock`, 20 s), `investigation --once`, `accounting --once`, each gated by its own `data/*.enabled` file.
- Revision gate: `EXPECTED_REV = $LUFFY_EXPECT_REVISION` or `git rev-parse HEAD` (recent commit `dc1fad5f`); `restart.sh` still refuses dirty runtime code, so **a watchdog restart is only as safe as the checked-out working tree**, since HEAD is what is trusted.
- Per-boot transient timers `luffy-bootstrap-<token>-heartbeat-only.timer` (observed about 10 s) and `…safety-only.timer` (about 30 s) run `scripts.monitor` independently of the kernel.
- The in-kernel `stall-monitor` is explicitly diagnostic ("external observer owns death detection").
- Process-liveness model: kernel liveness = heartbeat file freshness (`CYCLE_READY` is only set when `account` + `positions` + other essential cycles are fresh; threshold `scan_interval_seconds × 4` = 240 s for the diagnostic, 600 s for the watchdog).

---

## 7. Cross-cutting friction register (legacy vs new)

| # | Friction | Evidence |
|---|---|---|
| F1 | A venue HTTP order is submitted while holding the journal writer lock and a SQLite `BEGIN IMMEDIATE`; `_confirm_fill` then sleeps up to about 17 s under `decision_lock` | `entry_authority.py:594-624`, `risk.py:597`, `executor.py:386-408`, observed 13.1 s `SQLITE_BUSY` incident |
| F2 | `Journal` does not use the shared WAL pragma helper (synchronous FULL on the heaviest store; 30 s busy wait against 5 s elsewhere) | `journal.py:725-745` vs `sqlite_tx.py:41` |
| F3 | Two decision pipelines: `Kernel.cycle()` legacy branch (~260 lines) is dead in production but still defines semantics (cooldown, attention, learning dispatch) | `kernel.py:1386-1650` |
| F4 | Lean scan stopped journalling non-trades, which starves the "learn from non-trades" objective and the `outcomes` table (732 rows) | `lean_scan.py` `evidence()`, decisions timeline |
| F5 | Only one strategy may speak per symbol; overlapping signals produce a REFUSED selection, not a ranking | `plan_owner.select` |
| F6 | One legacy genome (`ematrend_g_edc6`, no spec, config ATR geometry, hard-coded confidence, uses an EMA-spread "ATR") sits beside two DSL specs with their own exit geometry | `library.py:24`, `kernel._try_enter` |
| F7 | Three promotion lanes (Factory-versioned, grandfathered legacy, owner-demo) and three exit engines | `factory_handoff.live_entry_block`, `exits.py`, `versioned_exits.py`, coordinated modules |
| F8 | Receipts are embedded in OHLCV frames and re-verified by JSON+SHA-256 on every read | `market_provenance.py` |
| F9 | Receipt tables are duplicated in three DBs (`candles`, `derivs`, `luffy`); `luffy.db` copies are mostly for venue-metadata/context | schema |
| F10 | No exchange streaming; polling cadence bounds latency (candles 15 s, positions 30 s) | no `websocket` in the data path |
| F11 | Research state split across `luffy.db` (legacy + bank), `research.db`, `questions.db`, `published-context.db`; closure never reached a freeze | counts above |
| F12 | PnL truth split across 5+ locations; only 15/65 trades have booking chains; `trade_fills` 7 rows all UNATTRIBUTED | counts above |
| F13 | Schema evolution by repeated `ALTER … try/except`; no version table for `luffy.db`; `kv` vs `state_kv` split (documented only in `CLAUDE.md`) | `journal.py:~665-730` |
| F14 | Dormant subsystems still configured as enabled: `attention.db` (legacy attention), `brain.enabled: false` but scraper/crawler (4 h/6 h) run, harvesting ideas for a strategy creation path that is separately gated | config, workers |
| F15 | CPU-bound GIL-serialised scan on 4 cores; `CurrentCache` near full with 222 pinned entries | `cycle_runtime.json` |
| F16 | Supervisor reports `DEGRADED` with `heartbeat STALE` reasons while the heartbeat file is fresh. Could be mid-clear after restart or a stuck latch | `state_kv.supervisor_status`, `heartbeat_luffy.json` |
| F17 | Watchdog trusts `git HEAD` when `LUFFY_EXPECT_REVISION` is unset; revision binding is only as strong as the working tree | `watchdog.sh` |
| F18 | Dashboard token accepted as a GET query parameter; plain HTTP on a LAN address | `auth.py`, config |
| F19 | Owner-IPC trust boundary equals the OS user; keys, journal and exchange credentials are all readable by the same uid | `ipc.py` docstring |
| F20 | `commands` worker logs INFO every second; `luffy.log` contains NUL bytes | log tail, `CLAUDE.md` |

---

## 8. What was not examined (so the architect does not assume it was)

- Exits in detail (`engine/exits.py` beyond the trail logic), `engine/supervisor.py` and `reconcile.py` internals, `engine/recovery.py`.
- `learning/` (adaptive application, dispatch, targets), `cognition/` (`m32_*`), `world/`, `knowledge/`, `chat/`, `agents/` (analysts, indicators), `brain/` (LLM; currently `brain.enabled: false`), `notify/`, `frontend/`.
- The `docs/superpowers/**` design and report history, `SDD.md`, `ROADMAP.md`, and the 2026-11-02 protocol/guard review documents.
- `persistence/*` retention schedules, `installation/observe.sh`, `scripts/` (90+ research and shadow scripts, `m32_*` bundles).
- Test results. 605 test files exist; none were run in this audit.
- Whether a live entry succeeds end to end under the current lean path (see 4.1); no entry has occurred since the path was introduced.
