# Smallest forward hypothesis / prediction / outcome loop

This is a bounded shadow experiment, not strategy admission or evidence of edge.
It connects the deployed attention scan to testable competing explanations and
later observations. No LLM, trading imports, order actions, risk changes, Gate 2
activity, or frozen-artifact writes are involved.

For each eligible symbol in a newly observed scan, register at most one episode
per non-overlapping forecast window. Include both attention-selected and ignored
eligible symbols, retaining their selection reason. The templates are **recent
price direction persists**, **recent price direction reverses**, and **unresolved**.
These are descriptive hypotheses, not claims about market participants or causes.
Probability is null (uncalibrated). Unknown or flat inputs cannot create a forecast.

Freeze the source scan, evidence IDs, input version IDs, observed recent move,
baseline last closed price, template/protocol version, creation time and deadline
before evaluating any future outcome. Do not backfill proposals from historical
scans. Reject stale scans, future timestamps and events from before the current
protocol was activated. Repeated scans cannot multiply overlapping episodes.

Target: the close of the first complete 4h bar that begins strictly after
registration. Measure its price change relative to the last closed price known
at registration. This is a price forecast with a potentially stale reference
price, not an executable entry, simulated P&L, or realized P&L. The whole target
bar is in the future; outcomes are only resolved from subsequently observed,
fully closed bars with the exact target timestamp. Missing targets remain
pending, then become unavailable after a fixed grace period. No nearest-price
substitution. Outcome records retain the resolving scan and input version.
Registration is skipped within 30 seconds of target-bar opening; scheduled
invocations have a 20-second timeout so publication precedes that opening.

Use a frozen 25-basis-point neutral band as an explicit experiment parameter,
not an economic hurdle. The outcome supports persistence, reversal, or neither.
Record descriptive counts by attention selection and hypothesis; repeated
symbols and correlated markets are not independent trials. No significance,
calibration, profitability, automatic ranking update or trading admission follows.

Implement as a separate read-only consumer of attention.db with its own SQLite
ledger and bounded runtime work. This avoids adding work to the trading producer
or changing the deployed collector. A once command supports verification; an
watchdog invocation every five minutes can collect forward evidence. Owner-readable status
must distinguish pending, resolved, unavailable, and input/collector degradation.
Retention is explicit and bounded; retain immutable input references and numeric
evidence in each episode so attention retention does not erase its meaning.

Acceptance: synthetic tests for end-to-end registration and resolution, future
and stale refusal, restart deduplication, overlapping-window exclusion, missing
and revised prices, ignored-symbol inclusion, and no trading/network dependency.
Live activation can establish pending forward predictions. Only actual later
observations establish resolved live outcomes; synthetic resolution must be
labelled separately.
