# M3.1 cut and M8.1 review — September 19, 2026

Read-only review at 11:54–11:57 UTC. No worker invocation, venue call, memory import, trade, restart, control change, freeze rewrite, backfill, M3.2 search or Gate 2 action. Capture replay passed and was written to a new file only. `research.referee=false` and `research.handoff=false` remain unchanged.

## M3.1

Capture: `../artifacts/declared-population/2026-09-19-active-window-cut-capture-1154.json`. Exact frozen cut: 2026-09-19 04:00 UTC; capture: 11:54:29.915 UTC.

- At cut: 48 forecast registrations; 32 terminal resolved and 16 immature/unresolved. Four investigation registrations were all immature/unresolved.
- Late supplemental evidence: 16 forecast terminals and 18 investigation updates. No late registration changed the at-cut denominator; these events do not revise the cut view.
- Both streams had 274 scans through the cut. Every scan included all 16 declared symbols (`missing_symbols=[]`). Each stream has one cadence gap: 11:30:10–12:45:14 UTC (forecast) and 11:30:11–12:45:14 UTC (investigation).
- Final capture reconciliation matches indices to exports: forecast 373/373 and investigation 310/310, with no missing or pending exports. The at-cut event clock filter remains authoritative.
- Typed dataset: 32 rows, 0 sequence rows; `false_signal`, `regime_transition` and `skip` are absent. `complete_sampling_claim=false` and `search_ready=false`.

Verdict: M3.1 remains **partial, coverage not accepted (12/55)**. The cadence gap, bounded-retention/complete-sampling refusal, absent typed kinds and zero sequences still block acceptance. No M3.2 or Gate 2.

## M8.1

Read-only queue/replay artifact: `../artifacts/declared-population/2026-09-19-m8-1-accounting-refresh-1154.json`. Health is `ok`; jobs are 1 complete, 3 retry and 7 waiting-close. There are no natural complete receipts. `pos_05a5e8f981` remains the already-complete controlled diagnostic receipt (seven attempts) and was not reopened. LINK, AAVE and XRP remain `legacy_entry_receipt_missing_retry`; open positions remain `natural_close_pending`. No import occurred.

Verdict: M8.1 remains **partial; no natural complete accounting receipt**.

## Remaining blockers

1. M3.1 lacks accepted complete prospective sampling: the 75-minute cadence gap, bounded retention, absent kinds and zero sequences remain explicit.
2. The already-frozen Sep 19 08:00–Sep 20 08:00 declaration was not activated by its 08:00 deadline. Preserve it and record the missed activation; do not silently re-freeze or activate it late.
3. M8.1 still lacks a natural non-diagnostic complete receipt and forward-only memory import; legacy-entry provenance cannot be recreated by waiting.

## Single next task

Owner review and authorization of a fresh, separately frozen M3.1 forward window after the missed activation is recorded; keep M8.1 observation-only in parallel.
