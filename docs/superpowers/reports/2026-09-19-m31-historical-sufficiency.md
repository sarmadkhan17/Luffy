# M3.1-H historical sufficiency — September 19, 2026

## Sources integrated

The existing read-only `typed_outcome_store` adapter was run against
`data/investigation.db` (229 retained receipts; 9,218 evicted records reported
by the store). It already carries the legitimate source joins needed here:

- journal decisions/outcomes: skip receipts; decision registration is retained
  as a claimed timestamp but features remain observation-only at local import;
- attention forecasts: embedded six-bar PIT baselines, exact target receipts,
  source versions and availability clocks;
- investigations: embedded registered case state, input versions, membership
  JSON and terminal update clocks for one `false_signal` and one
  `regime_transition`;
- strategy attribution/skip evidence: retained attribution IDs are preserved,
  but no absent historical strategy definition is inferred;
- collector/observability evidence: source receipts and local import clocks are
  preserved where embedded; standalone health/scan receipts are not promoted
  to cases because they do not carry a typed outcome contract;
- regime/state evidence: the registered volatility-transition case is included;
  journal cycle regime fields were not joined because their independent PIT
  availability receipt is not retained;
- universe/membership: the frozen 16-symbol declared population and the
  investigation membership provenance are preserved; 66 receipts outside that
  declared universe remain explicit exclusions;
- execution/reconciliation: no complete typed execution-failure receipt was
  present. Unknown accounting remains unknown and is not imputed.

All accepted receipts had valid chronology and local import clocks. Adapter
rejections were zero. No archive-read time or reconstructed provenance was
used.

## Declaration and result

Declaration: `2026-09-19-historical-discovery-declaration.json`.
Discovery cut: **2026-09-19 15:15:09.506 UTC** (`1789830909506`), one
millisecond after the latest retained source local-import clock. Freeze:
**2026-09-19 15:23:43.039 UTC**.

The immutable artifact is
`2026-09-19-historical-discovery-artifact.json`. Replay passed. The historical
sufficiency audit is **sufficient** with `population_sampling_claim=false`.

Counts: 16 selected, 78 ignored, 67 skips, 1 failure (`false_signal`), 1
regime transition, 64 sequence rows, 163 resolved base outcomes, and 0
unresolved observed outcomes. There are 227 total rows including sequences,
one conservative dependence group, 16-symbol coverage, calendar coverage in
September 2026, and explicit `unknown` regime labels for all 163 base rows.

This is historical discovery evidence only. It does not establish complete
population sampling, strategy performance, execution economics, or live
authority. M3.2 was not started.
