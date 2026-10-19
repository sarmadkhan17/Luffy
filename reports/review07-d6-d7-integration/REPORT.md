# D6/D7 canonical backtester integration

Offline correction on `sol1/d6-d7-backtester-integration`, based on combined
`a11bf2495e82450e617d908a229b2ab8180a330b`. D6 head verified as
`f10f974bf8e88820428d03f1bdb9203d3748a5d4`; D7 head verified as
`e6a403788715df91a0fd6350f3acce6c43ca5da5`. Coordination used their committed
local handoffs as authorized. The overlapping source files were clean in their
owner checkouts; their working trees and branches were not edited. D6's
INTEGRATE.txt explicitly describes the pending signal-fill reconciliation.
The D7 pending patch was reconciled manually, not applied over D6's _trade.

## Resulting behavior

Canonical observations carry actual ATR(14), completed/valid metadata and the
compiler's declared signal-availability mask. ATR trails use D7's intact
15-bar input window on the declared timeframe, computed once per simulation
or trade table. Existing fixed exits do not acquire a trailing-ATR dependency.
Missing signal evidence and invalid completed-bar observations refuse; missing
trail ATR refuses surviving trail transitions. Existing shared-policy ordering
keeps already due protection effective without trail ATR.

Signal intent is separate from execution. The shared policy retains an unset
reference_exit for a signal decision; the adapter records its actual raw fill
price/index separately. D6's unchanged fill_timing.fills array supplies the
first executable open after the decision inputs become available. Pending
signal execution leaves protective levels and next-bar trail updates active.
A due signal on the last held bar may fill at the next executable open rather
than becoming a same-close time exit. A known unexecutable/stale signal cannot
create a signal fill: protection/time rules continue, or the position remains
censored at the dataset boundary. Invalid intervening OHLC refuses evaluation.

Entry fills, receipt clocks, freshness and lookback contracts, sizing, fees,
spread and signed/unknown funding arithmetic are unchanged. Both actual and
rotation tables call the same _trade with the same market signal evidence and
fill array. Only entries rotate; market exits/availability/funding stay aligned.
Signal evidence is now also passed by vector walk-forward, rolling/recent/regime
consumers, discovery evaluation and portfolio legs/null consumers. The D7
verification script now tests source on disk; it no longer patches modules in
memory. Historical pending patch retained solely as original handoff evidence.

No D7 xfail exists in the supplied combined base or D7 head, so no xfail was
removed. Two existing D7 skips represent target-outcome cases where no target
is declared. Neither skip masks a handoff defect.

## Scope and integration

No Kernel/Dashboard changes or restart, deployment, trading activation, CI
changes, provider calls, production data writes or lifecycle rework. The
previously failing real-store Referee test builds and qualifies its own
SQLite store under pytest's temporary directory; this is a storage-backed
end-to-end fixture, not a fresh look at production held-out market data.
Legacy equivalence and performance use the committed synthetic harness only.
No research budget, admission receipt or statistical threshold was changed.
Live/paper signal intent and live submission gates were not changed by this
backtester-only assignment. Engine identity changes naturally through the
existing source digest; previously stored evidence is not rewritten.

The local Claude CLI was already established as unavailable (not logged in)
in this session; the authorized local Codex fallback was used. Graphify is
updated with AST extraction only; generated graph/cache changes are excluded
from the implementation artifact. See retained logs for focused verification.

## Verification results

- Implementation commit: `a7f9397`.
- Final replay/parity/Referee run: **121 passed, 2 skipped**, 122.76 seconds.
  Exact requested Referee testcase passed (retained JUnit evidence).
- Actual-source shared-vector verification: **53 passed**, 106.84 seconds;
  includes 33 canonical D6/D7 handoff cases plus vector/null/cost regressions.
- Rolling, research-evaluate and null-baseline consumers: **37 passed**,
  168.84 seconds.
- Total final focused runs: **211 passed, 2 expected skips**, no xfails.
- Existing equivalence script: **PASS**, five synthetic strategy families.
- Existing benchmark: **PASS**, **58.8x** (13.820s legacy / 0.235s vector,
  five runs). Synthetic measurements, not a market-performance qualification.
- `git diff --check`: passed.

READY_FOR_COMBINED_INTEGRATION: YES for this offline backtester correction.
REMAINING_BLOCKERS: none within this scope. Live signal submission remains
explicitly gated and is outside this targeted backtester assignment.

Graphify AST update completed successfully (26,144 nodes, 68,437 edges);
final new regression symbols are present. No semantic labeling/API was run.
Generated graph/cache changes stay local and are excluded from commits.
