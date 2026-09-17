# Forward attention and evaluation diagnostics

Date: 2026-09-16. Status: implemented and tested; disabled, not deployed.
This was the implementation checkpoint. Subsequent demo deployment, corrections
and forward-loop activation are recorded in the
[rollout report](2026-09-16-attention-rollout.md).
The [owner end goal](../specs/2026-09-16-luffy-end-goal.md) remains the objective.
This is the first observability slice, not completion of the autonomous trader.

## Delivered

- `trader/observability/`: captures detached, closed OHLCV windows from frames
  already fetched by the kernel. A fresh scan ID joins forward decisions to
  observations, inputs and evaluation receipts. Historical decisions retain NULL.
- A bounded queue and daemon supervisor run each storage/evaluation job in a
  disposable subprocess with a 10-second timeout. The producer does no file,
  database or network I/O. Trading never waits for worker completion.
- Separate `data/attention.db`: immutable scan payloads; content-versioned candles,
  observed availability, revision predecessor hashes, membership, evidence IDs,
  config hashes and source-file hashes. Unchanged retained values share versions.
  First availability is the end of capture, never inferred from candle close.
- Per-analyst and per-strategy receipts: emitted output, eligibility rejection,
  missing evaluator, missing closed spec timeframe, empty spec entry series,
  returned None and evaluation error. Errors inside the library and compiled spec
  wrapper are captured where they are caught. Stack frames include file/function/
  line; new telemetry excludes exception messages, locals, prompts and credentials.
- The real scan loop records missing snapshots and final decision/execution status.
  Only the nullable scan ID is added to the trading journal write. Existing
  decision IDs already join to orders/trades/outcomes; this slice does not add
  a comprehensive order/fill event stream.
- `GET /api/attention/latest`, protected by the existing API token guard, and an
  independent overview panel display scope, selection reasons, evidence, receipts,
  freshness, incomplete scans, errors and drops. API reads never contact a venue.
  All dynamic panel content uses DOM text nodes, not HTML interpolation.
- Collector health is published separately and in the kernel heartbeat. Queue
  saturation, worker errors/timeouts, capture errors, health-file errors and
  producer budget overruns are observable. Missing telemetry does not mean no edge.

## Limits and explicit changes to the design

The unchanged offline cognition package has strict import-isolation tests.
Runtime wrapping therefore lives in `trader/observability`, not `trader/cognition`.
No strategy score, sizing rule, risk gate, execution behavior or research gate
was intentionally changed. Pre-existing unrelated runtime changes were preserved.

Default capture is **16 symbols and 64 evaluation receipts per symbol**. This
was reduced after a worst-case 64-symbol/256-receipt benchmark exceeded the
50ms p99 producer budget. The current strategy/volume-selected scan subset is
labelled explicitly; it is not venue-wide scouting. Symbols beyond the cap are
counted as excluded, and receipt truncation is counted. All trading symbols are
still evaluated as before. Raising limits needs another latency measurement.

The DataFeed interface does not expose the exact endpoint/cache provenance of
individual frames. Source is labelled `kernel.universe_frames` and that upstream
provenance is explicitly unknown. This implementation does not invent it.

A generic evaluator's `None` return does not distinguish insufficient inputs
from a false predicate. It is labelled `returned_none`, with input sufficiency
`not_reported`. Concrete missing-data causes are recorded only at known branches.
Analysts can still swallow internal errors not exposed by their interface.

Operational retention is 512 scans, seven days (pruned on writes), and a 64MiB
SQLite main-file ceiling. SQLite's temporary rollback journal needs additional
space. Idle stores are not physically age-pruned until the next write. First-seen
and revision lineage extend only over retained snapshots; after eviction they
cannot establish earlier availability. `store.export_scan(path, scan_id, destination)`
creates a no-overwrite JSON export outside operational retention, including input
versions and receipts. Export is not research admission or a claim of untouched data.

Queue loss, worker failure or retention can leave a journal scan ID without
available telemetry. Durable cross-database atomic joins cannot be promised by a
non-waiting best-effort collector. The API reports incomplete/pending/error/stale
states rather than treating those records as successful. Shutdown does not wait
for or guarantee delivery of pending diagnostics. The current scan is not a
transactional freeze of every analyst's derivatives, depth or macro input.

No hypotheses, learned attention, survival-cost accounting, dynamic venue-wide
scouting, calibrated probabilities or opportunity-to-strategy learning loop were
implemented here. These remain end-goal gaps.

## Verification

All inputs added for these tests are synthetic. No real-data evaluation or venue
request was used for this implementation. New telemetry tests prohibit socket
connections. Existing imported modules can load configuration; no secrets were
printed.

- **237 passed** in the combined observability/cognition/trading-safety regression
  set, including the then-current 32 new telemetry/view tests.
  Final focused suite: **33 passed**, including an added empty-store failure-state check.
- **23 passed** in `test_compile.py` and `test_spec_signals_on_closed_bars.py`.
  Seven existing pandas Timestamp.utcnow deprecation warnings remain.
- New tests cover frozen input ownership, availability/revisions/late arrivals,
  reconstruction of exported inputs and evidence joins, missing/capped inputs,
  retention and file bounds, actual child persistence, timeout/error reporting,
  a genuinely locked telemetry DB alongside a writable trading journal, full
  queues, callback failure, nullable journal links, real `Kernel.cycle()` behavior
  in ACTIVE/FROZEN/HALTED modes, orchestrator flag-on/off decision equality,
  authenticated API states, and text-only DOM rendering through Node.
- `scripts/bench_attention_capture.py`: 200 measured synthetic cycles plus 10
  warmups, at the shipped caps. Includes snapshot capture, receipt construction
  and cause publication. Initial optimized run: p50 **14.32ms**, p99 **34.18ms**,
  maximum **58.15ms**. Final run after availability/connection cleanup:
  p50 **11.12ms**, p99 **26.79ms**, maximum **48.82ms**. The budget is p99 <=50ms, not a hard real-time guarantee;
  OS scheduling can exceed it. Evaluation/storage is timed separately by the
  worker timeout and is not part of this producer benchmark.

Regression invocation:

```bash
./venv/bin/python -m pytest -q \
  tests/test_attention_telemetry.py tests/test_attention_view.py \
  tests/test_cognition_contracts.py tests/test_cognition_attention.py \
  tests/test_cognition_replay.py tests/test_cognition_history.py \
  tests/test_cognition_coverage_audit.py tests/test_strategy_signal_gate.py \
  tests/test_orchestrator_abstention.py tests/test_orphan_positions.py \
  tests/test_protective_stops.py tests/test_risk_state_persistence.py \
  tests/test_exchange_exit_is_size_aware.py tests/test_exit_books_the_fill.py \
  tests/test_exits.py tests/test_spec_exits_are_honoured.py \
  tests/test_single_creation_path.py tests/test_llm_purpose_budget.py
./venv/bin/python -m pytest -q tests/test_compile.py tests/test_spec_signals_on_closed_bars.py
./venv/bin/python scripts/bench_attention_capture.py
```

## Rollout and next session

`config.yaml` now documents `attention.enabled: false` and the limits. No process
was restarted and no operational state was changed. Both kernel and dashboard
read this config on startup. A coordinated demo rollout must account for the
unrelated dirty safety/executor/kernel changes before restarting either process.
Review the diff, verify demo routing and operational state without printing
credentials, then enable the flag and verify a persisted scan, matching decision
links, completed receipts and the rendered panel. The recorded research flags
remain `referee: false` and `handoff: false`.

After rollout and observation-quality review, progress toward the smallest
connected hypothesis/prediction/outcome loop. Preserve missing-data honesty,
competing explanations, deterministic risk and owner-visible activity. Do not
resume Gate 2 automatically or treat these diagnostics as evidence of profit.

Claude CLI was checked and remained unavailable due to its session limit (reset
reported as 10:20pm Asia/Bahrain). The coordinator disclosed this and implemented
and tested directly under the owner's standing process-change authorization.
