# LUFFY Admitted Exit Semantic Parity R1

**Verdict: TESTED.** The immutable contract is `factory-exit.closed-bars.v1:51758284b0b961695203b5cbc1569dfb1d5805a68506d594a40a5d5df30d3fcc`.

Factory exits consume consecutive completed bars on the frozen StrategySpec timeframe, beginning after the signal bar. Initial ATR and percentage stop levels use the signal bar close; ATR is the existing rolling mean True Range(14), computed through that closed bar. The stop floor is 0.4%, and initial R is the distance from that reference to the frozen stop. Fill movement remains separately recorded execution evidence.

Price ordering is open gap, stop, target, then unconditional `max_bars`. A bar that reaches both stop and target without a gap closes at the stop and exposes an ambiguity count. Duplicate observations are idempotent. Missing bars do not get skipped. Research retains censored position occupancy through the end of a slice and does not invent a terminal fill.

The same pure state transition drives vector backtests, paper and the live exit adapter. A final target closes the full remaining intent with reason `target`. `trail: none` never moves the stop. Automatic TP1, breakeven, score flip and config trailing/target fallbacks are absent from the versioned path. ATR trailing, `signal_exit`, swing stops, undeclared partial/flip/regime exits and unknown exit fields are refused.

Research evidence, the immutable validation receipt, exact install, paper entry identity and probation bind the semantics ID. Existing receipts without this ID cannot validate the current contract. Live stop protection, Risk and recovery continue under their separate authority labels. The first-live fence, approval gate, capacity gate, Risk limits, StrategyVersion identity, paper isolation and Intelligence Spine behavior remain unchanged.

The machine-readable behavior matrix is `2026-10-01-admitted-exit-semantic-parity-r1-matrix.json`. Cross-adapter replay exercises both sides, price exits, time exits, gaps, stop/target ambiguity, different fills, duplicate observations, restart and retry after partial submission. An open censored research position blocks later entries in both vector and rolling/null table walks.

Focused validation: 442 tests passed across the semantic matrix/replay, paper/factory/authority/referee suites, live protection/provenance/Risk suites and research evaluator/referee suites. `scripts/backtest_equivalence.py` passed. The isolated 100,000-transition microbenchmark measured 11.548 µs per pure transition with zero network calls. The synthetic vector-backtest benchmark measured 524.7x speedup and passed the existing 20x threshold. The ResearchRunner unit file has an existing fixture failure: its `_symbols()` discovers zero local symbols; the same first-step failure reproduces unchanged at base `d4de42a`.

No venue order, production write, restart, deployment, research activation, Risk/approval/capacity change, frontend/M4 edit was performed during implementation and review. Package commit and branch push are separately authorized for closure. Strategy Factory is TESTED, not PROVEN or ACTIVE. Effective capacity remains UNAVAILABLE. Production paper probation remains `INCOMPLETE_COST_EVIDENCE`; this earlier Stage-5 truth gap is the next package, before liquidity-capacity calibration.


Final independent P1 recheck: **PASS; SAFE_TO_COMMIT: YES.** All six checks passed: semantics match, missing binding, tamper resistance, approval re-verification, cost gate and live fence. No P0 or P1 issues remain within the bounded recheck.

The focused binding tests plus the Kernel paper/live fence test passed (26 tests). Independent temporary-journal probes reproduced a B-bound version/install with 15 A-bound trades: zero counted, incomplete probation, no approval request, approval refused and first-live false. An A probation receipt was refused under B for approval and eligibility without rewriting historical evidence. Rehashed, replay-consistent observation tampering also invalidated the existing receipt at approval and first-live verification. Missing, blank and unknown semantics IDs, absent canonical evidence and conflicting replay evidence cannot count. Matching valid exits may count; production unknown costs remain `INCOMPLETE_COST_EVIDENCE`. No real execution authority was added.

Closure returns `NEXT.yaml` to `NO_ACTIVE_PACKAGE`. The paper cost evidence protocol remains a deferred candidate. This closure authorizes commit and branch push only; deployment, merging main and production restart remain unauthorized.
