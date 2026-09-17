# Cognition historical replay — retrospective slice (2026-09-15)

Design: Astra. Implementation: Claude. Status: **offline, retrospective reconstruction only.**

Runs the existing offline cognition loop (`docs/superpowers/specs/2026-09-15-cognition-offline.md`)
over stored candles. It never changes `trader/cognition`.

```bash
./venv/bin/python -m scripts.cognition_history --db PATH --timeframe 4h \
    --start 2026-08-31T00:00:00Z --stop 2026-09-14T00:00:00Z \
    --resolve-until 2026-09-15T00:00:00Z --output-dir /tmp/luffy-history-20260915 \
    [--allow-incomplete] [--max-decisions N<=500] [--max-symbols N<=100] [--max-rows N<=100000]
```

Code: `scripts/cognition_history.py` (stdlib + `trader.cognition` API). Tests:
`tests/test_cognition_history.py`. It lives outside `trader/cognition`, so that package's
no-sqlite import isolation tests still hold.

## What it claims, and what it does not

The store (`candles(symbol, tf, ts open-ms, open, high, low, close, volume, taker_buy)`)
does not record arrival times, revisions, eligibility history or per-row venue. The export is therefore a
**retrospective reconstruction**, not a verified point-in-time record. Assumptions are written
into `input.json` metadata and `manifest.json`:

- every candle has `available_ms = close_ms` (an assumed zero arrival lag), `closed: true` and a source
  `local_candle_store:<file>:unknown_venue_provenance`;
- one row per bar as it is stored now, with no revisions;
- membership is an **assumed static research cohort**, not historical tradability;
- symbol keys are kept as stored; possible aliases (same key after dropping `:suffix` and
  non-alphanumerics) are disclosed, never merged;
- the rows may reflect storage selection or survivorship;
- there is no interpolation, resampling or fill-in, and no participation (taker_buy/OI unused).

No significance, predictive, causal, edge, PnL or profitability claim. Nothing trades.

## Inputs and bounds

- Timestamps: exactly `YYYY-MM-DDTHH:MM:SSZ`. Naive, date-only or offset forms are refused.
- Decisions: every tf-aligned time in `[start, stop)`, with `start` and `stop` grid-aligned.
- `resolve_until` must be ≥ `stop`, ≤ wall-clock now, and ≥ the last decision's deadline
  (`last decision + horizon*tf`). Otherwise the run is refused unless `--allow-incomplete` is given,
  in which case `window.complete_horizon=false` is recorded and outcomes stay `unresolved`.
- Config: frozen `CognitionConfig()` defaults (K=3, window 20, short 5, horizon 5). No knobs.
- Caps: decisions ≤ 500, symbols ≤ 100, rows ≤ 100 000. Exceeding a cap fails; nothing is silently dropped.

## Store access

`Path.resolve().as_uri() + "?mode=ro"`, `PRAGMA query_only=ON`, one read transaction for
schema check, universe, `COUNT(*)` guard and row fetch, then close, all before replay. It never uses
`immutable=1`, never creates a missing file, and never touches DataFeed or trading modules. It fails
clearly on a missing table or columns, no warmup history, or a cap breach.

Rows fetched: `tf = ? AND symbol IN (universe) AND ts >= start - 26*tf AND ts <= resolve_until - tf`,
so only bars fully closed by `resolve_until`, from the warmup's first bar.

## Universe rule

All distinct non-empty text symbol keys with at least one stored candle whose open is in
`[start - (window+short+1)*tf, start)`, sorted. The set is chosen only from pre-start rows and frozen from start
to `resolve_until`. There is no coverage, survival or forward-return filter. Symbols with poor coverage or gaps stay in,
and the replay's own `stale/warmup/gap` omission rows record them. Symbols first stored after
start are counted (`excluded_post_start_symbols`) and not exported.

## Outputs (refused unless the output dir is absent or empty; files opened `x`)

- `input.json` — `cognition.input.v1` fixture, including `metadata`.
- `trace.json` — `replay.dump(replay.run(input, CognitionConfig(), end_ms=resolve_until))`.
- `report.json` — descriptive summary: decisions; eligible/ineligible/selected/eligible-unselected
  counts; row status and omission-reason distributions; episodes, framing, and hypothesis status by
  template; per `raw|relative × selected|unselected`: samples, status, unresolved reasons and
  fraction, and n/mean/median of |z| and |log return| (relative: minus the cohort median).
- `manifest.json` — source path and schema SQL, exact queries and params, window, universe
  and rule, aliases, assumptions, export-refused rows (non-JSON values such as inf), loader
  rejections, per-symbol expected/valid/missing/rejected bar counts, config, config_id, end_ms,
  canonical SHA256 of input, config and trace, file SHA256s, and `rerun_identical`.
- `report.md` — the same, rendered.

## Reproducibility

The trace is a pure function of the exported `input.json`, config and `end_ms`. Reruns use the file,
not the live DB. Rows after `resolve_until`, or symbols first seen after start, cannot change the export
(tested). This makes the replay reproducible. It does not prove that candles were actually available at those times.

## Tests → requirements

| requirement | test |
|---|---|
| read-only, writes fail, missing DB not created | `test_connection_is_read_only_and_never_creates_a_db` |
| exact UTC format | `test_timestamps_must_be_exact_utc` |
| bounds, deadline, caps | `test_bounds_are_validated`, `test_store_refusals` |
| warmup extraction, future-only exclusion, gaps kept | `test_window_universe_and_gaps` |
| later rows cannot affect the export | `test_rows_after_the_window_cannot_change_the_export` |
| deterministic export/replay; replay CLI identical | `test_export_replay_and_summary_are_deterministic` |
| denominators, unresolved | `test_summary_denominators_and_unresolved` |
| malformed rows, status propagation | `test_malformed_rows_are_rejected_auditably` |
| CLI explicit paths, nonempty-output guard | `test_cli_requires_explicit_paths_and_an_empty_output_dir` |

Sockets are blocked (`no_network`). Only temporary SQLite files are used.
