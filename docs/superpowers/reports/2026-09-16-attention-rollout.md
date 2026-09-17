# Attention demo rollout and first forward learning loop

The owner end goal remains authoritative. This slice observes and measures;
it does not establish profitable strategies or a complete autonomous trader.
Status: **deployed and verified in demo; forward predictions pending**.

## Workspace and working roles

Read CLAUDE.md and both requested handoff documents. The workspace contained
extensive pre-existing changes, including execution/state/kernel changes that
a restart would load. Preserved those changes, research artifacts and doctrine.
No commit, reset, cleanup, research evaluation or Gate 2 resumption was performed.
Claude CLI was checked: session limit, reset reported as 10:20pm Asia/Bahrain.
The coordinator disclosed the limitation and performed direct work under the
owner's explicit authorization to change working processes.

Structured troubleshooting: [rollout events](2026-09-16-attention-rollout.jsonl).
Source hashes and browser evidence: [artifacts](../artifacts/attention-rollout/).

## Review and corrections

Reviewed capture/collector/store, receipt propagation, journal scan links,
API authentication and DOM rendering, plus the existing executor changes,
control-state refresh/operator hold, macro clock and kernel risk/exit gating.
The executor changes reject guessed fill prices, retain stops until confirmed
closes, handle partial closes, and attempt a reduce-only close after stop
placement failure. This is not a claim that all execution safety is complete:
unconfirmed entries and failed emergency closes still require better continuous
reconciliation; they remain an explicitly recorded follow-up.

The new producer measurement initially failed its 50ms p99 budget: 61.20ms
(p50 18.94ms, maximum 141.99ms). Paused deployment and profiled. Avoided six
temporary pandas Series slices per symbol by detaching bounded array slices.
At unchanged caps (16 symbols, 64 receipts), the corrected 200-sample synthetic
measurement was p50 15.10ms, p99 23.54ms, maximum 57.06ms. This is a p99 budget,
not a hard real-time bound. All 33 then-current attention tests passed.

The first deployed scan exposed 22 missing-evaluator receipts. These were
duplicates: stored specs were loaded both as legacy `kind=spec` genomes and as
working compiled evaluators. Skipping specs in the legacy loader fixes the
population count and diagnostics while preserving the compiled evaluation path.
A regression test covers valid compiled specs, invalid specs and legacy rows.

## Deployment and evidence

Confirmed `BINANCE_DEMO=true` and the private futures host
`demo-fapi.binance.com`, including the absence of process-level demo overrides.
Read the venue directly: three positions (XRP 68.9 short, LINK 36.24 short,
AAVE 0.9 short) matched the journal and had native stops on matching sizes.
Kept operational state ACTIVE. Research referee and handoff remain false.

For each coordinated restart, paused the watchdog, sent SIGTERM, verified the
old processes exited, and started kernel and dashboard. First boot reconciliation
reported zero adoptions, ghosts, size adjustments, missing protection, or failed
rearms. The corrective boot reported the same, with population reduced from five
entries to three. The watchdog was restored after the initial rollout; final
corrective-rollout verification is recorded in the structured log. Watchdog
supervision is restored and its actual invocation successfully ran the consumer.

The initial fully verified scan `scan_ffd2db73c4694c61b98c7767d56b4b9c` linked
19 journal decisions. All 16 captured decision IDs matched; 192 receipts were
persisted, including the duplicate-path problem above. Collector drops, errors,
timeouts and queue depth were zero. Chromium rendered the live authenticated
dashboard with attention status OK and no JavaScript errors. Capture coverage
was explicitly 16 of 19 candidates, not venue-wide coverage.

After the corrective restart, scan `scan_07f784e41dcd40a09459855448d007b3`
linked 19 decisions and persisted 160 receipts for 16 captured symbols, with
**zero missing evaluators**. Collector drops/errors/timeouts/overruns remained
zero; its observed maximum producer time was 26.22ms at final verification.
The three venue positions were rechecked and remained protected. Chromium
verified attention OK, learning OK, 16 pending predictions, populated account
widgets, and zero JavaScript errors. The screenshots were visually inspected.

## Connected shadow learning

[Protocol](../specs/2026-09-16-forward-learning-loop.md) and
`trader/observability/learning.py` implement a separate forward-only consumer.
It compares recent-direction persistence, reversal and unresolved outcomes for
both attention-selected and ignored eligible symbols. It records evidence,
input versions/numeric values, source scan/decision IDs, code hash, protocol,
registration time, target timestamp and explicit invalidation criteria.
Probabilities are null; templates are unvalidated hypotheses.

The baseline is the last observed closed price. The target is the close of the
first entire 4h bar opening after registration, with a 30-second registration
margin. A fixed 25bp neutral band is an experiment parameter, not an economic
threshold. Returns are observed price changes, never executable-entry estimates
or P&L. Exact future target data resolves the forecast; missing/invalid data
stays pending and becomes unavailable after 24 hours. Published outcomes do not
change when source candles are revised.

The ledger is `data/attention_learning.db`, capped at 4096 episodes and 64MiB;
terminal episodes older than 30 days are pruned on each invocation. Structured
diagnostics retain 512 entries. One pending episode per symbol prevents repeated
scans multiplying observations. Descriptive outcome counts are grouped by
attention-selected/ignored status. They are not independent-trial statistics,
probability calibration, edge claims, learned ranking or trading admission.

The existing five-minute watchdog invokes the consumer with `flock` and a
20-second timeout. It reads attention.db and writes only its own ledger/health;
no new work is added to the trading producer and no network call is made.
The dashboard shows pending/resolved/unavailable counts, prediction deadlines,
source references and descriptive feedback. A stale health file is labelled stale.
Protocol activation refused the prior scan, confirming no historical backfill.

Live protocol activation: **2026-09-16 15:59:09.942 UTC**. A later completed
scan registered **16 pending forecasts: 2 selected and 14 ignored**. First
target close: **2026-09-17 00:00 UTC (03:00 Bahrain)**. No live outcomes have
resolved. Re-running through the watchdog registered zero duplicates and
recorded `existing_episode: 16`. The loop will consume later scans naturally;
no historical evaluation was run to manufacture immediate results.

Verification: **295 relevant tests passed**, with seven existing pandas
deprecation warnings. Eleven synthetic learning tests cover actual resolution,
selected/ignored inclusion, activation/freshness/future refusal, restart and
overlap deduplication, missing/malformed targets, revisions, neutral outcomes,
storage capacity, collector degradation, availability and boundary timing.
Synthetic outcomes are not live evidence; forward outcomes must mature naturally.

## Remaining work

Observe live target resolution and unavailable rates before expanding templates.
Investigate the distribution of selected versus ignored outcomes with appropriate
episode/market dependence handling; do not promote raw counts into admission.
Improve continuous handling of unconfirmed exposure as a separate deterministic
safety change. Dynamic scouting, causal competing explanations, richer world
state, calibrated forecasts, adaptive attention and economic survival accounting
remain unimplemented. Cash/no-trade remains valid; no cost target forces entries.
