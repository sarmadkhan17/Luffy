# M3.1 fresh forward-window preparation — September 19, 2026

Read-only delivery continuation. The exit A/B study remains frozen/shadow-only and was not changed. No live trading, risk, exit, authored strategy, research flag, Gate 2 or handoff state was changed.

## Exact status

M3.1 remains **partial and unaccepted**. The latest reviewed cut (`2026-09-19-m31-cut-and-m81-review.md`) reports a 75-minute cadence gap, bounded-retention/complete-sampling refusal, absent `false_signal`, `regime_transition` and `skip` kinds, and zero sequence rows. `complete_sampling_claim=false` and `search_ready=false` remain authoritative.

The previously frozen Sep 19 08:00–Sep 20 08:00 window was missed and was not activated late.

## Fresh preparation

A distinct forward declaration was frozen at 2026-09-19 14:24:31.547 UTC:

- window: 2026-09-19 16:00 UTC through 2026-09-20 16:00 UTC;
- declaration version: `e310269be8507cd891ab48a78588dd499eae9ea95a88ecb8ca88c812f2793144`;
- same 16-symbol declared observation universe and typed coverage requirements;
- separate config/export path; current `data/pit_population.json` was not changed;
- activation remains pending operational review and the pre-window completeness check.

## M3.2 gate

M3.2 was **not started**. No quantitative search, scoring, baseline/null comparison, admission, or live handoff was performed. `research.referee=false` and `research.handoff=false` remain unchanged.

## Next task

Before 16:00 UTC, verify the collector is healthy and activate the fresh frozen config through the guarded population-consumer procedure. Then collect the complete window, perform the at-cut replay/coverage review, and accept M3.1 only if every declared member, receipt/index/export, required typed kind, sequence coverage, gap condition and outcome maturity pass the existing protocol.
