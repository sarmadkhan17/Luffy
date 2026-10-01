# LUFFY-STAGE7-DECISION-REPLAY-SOURCE-INTEGRATION-R1

Base: committed `d400394`, `luffy-stage7-outcome-capture-recovery-r1`.
Implementation is uncommitted in the isolated
`/mnt/luffy-data/luffy/workspaces/stage7-decision-replay-source-integration-r1`
worktree. No production rollout, restart, order, Risk/config change, allocation
authority change, strategy logic change, frontend or M4 change.

## Decision and outcome sources

`decision-source-manifest.v1` is created automatically in the existing original
registration transaction. Its canonical content hash identifies an append-only
row protected against replacement, update and deletion. It records the cycle,
decision, cut, lineage and exact dependencies, with AVAILABLE, UNAVAILABLE or
NOT_APPLICABLE. The registration and every subsequent outcome bind its ID; the
outcome also embeds its exact body for detached replay.

WorldModel records and frozen Opportunity Contexts verify their original
producer identities and cuts. The portfolio source is an exact, verified venue
position snapshot with its embedded observation and availability. Missing venue
state stays UNAVAILABLE. The old journal portfolio fallback has been removed;
journal positions never satisfy this requirement. Risk and relevant allowlisted
configuration carry independent content versions. The actual control state used
by the Kernel is captured before its decision, without changing control behavior.
The manifest also binds the actual StrategyVersion, spec hash, exit semantics,
economics receipt, AllocationProposal and TradeIntent when supplied/used. Missing
unused stages are NOT_APPLICABLE; missing known-used stages are UNAVAILABLE.

The `journalize_allocation` adapter consumes actual Stage-6 producer objects. It
extracts their exact original source bodies, availability/freshness boundaries,
strategy/exit authority, economics, whole-book proposal and typed intent into the
normal `Journal.log_decision` registration path. No caller manually inserts
source links. It verifies the proposal using its original frozen inputs and
records evidence only; it never routes an intent or invokes Risk/Execution.

Both original decision-source and later outcome replay verification must pass
before LearningEvidence is authoritative. All targets reverify the complete
historical chain, including `existing-recent-decay.v1`; a forged quality label,
source omission, changed hash, current state or unbound legacy receipt cannot
bypass the gate. Research uses its own original source profile; unused trading
stages are NOT_APPLICABLE, without waiving required research/outcome sources.
Unbound legacy decisions are never upgraded or repaired.

## Prospective acceptance

The focused acceptance uses real existing producer implementations with
explicitly synthetic market/Factory inputs:

market frames → Attention capture → WorldModel producer → Opportunity Context
producer → Strategy candidate bridge → Economics receipt → AllocationProposal
→ TradeIntent / NO_ALLOCATION → normal journal decision registration and its
decision-source manifest → existing scheduled forward outcome resolver →
outcome ReplayManifest → verified LearningEvidence.

CASH and rejected BUY acceptance variants require no executed trade. Separate
cases cover Risk-blocked and missed/skipped observations, exact source/hash
checks, current/latest refusal, immutable storage, process restart, missing
sources, forged quality labels and unchanged authority/control state. Unknown
expected economics can be represented by an exact unavailable-economics receipt;
replaying that receipt does not establish economic maturity or a tradable edge.

## Real read-only shadow

The shadow opened the production journal using `mode=ro`, `query_only=ON` and
one consistent read transaction. It inspected the latest 100 retained real
live-journal decisions; it did not invoke a new trading cycle or manufacture
historical registration. Result: 100 observed, 0 source manifests, 0 complete,
100 incomplete. All 100 lack original registration, so their exact original
world, context, portfolio, Risk/config, control, data, cycle/decision and reasons
are unavailable as decision-time sources. Existing journal IDs are contextual
observations, not proof of original immutable source availability. No production
source manifest was created or inserted. The complete per-decision reasons are
in `../evidence/stage7-decision-replay-source-integration-r1/real-shadow.json`.
The newest inspected row is dated `2026-10-01T21:11:01.687037+00:00`.

This proves prospective plumbing and truthful current gaps, not deployment,
retrospective repair, a complete real future trade or general Stage-7 completion.
The unchanged normal Kernel currently supplies no WorldModel/context input;
those unused sources are explicitly UNAVAILABLE. The prospective allocation
producer adapter is ready to carry them when that producer is used.

## Validation and next gap

Final focused result: **240 passed** in the adjacent `tests.txt`.
`git diff --check` passed. Graphify was updated with code-only AST extraction (zero semantic
API use); its aggregate HTML/report are generated graph artifacts.

A pre-existing kernel wiring mock failure was reproduced at committed base
`d400394`. The fixture represented exchange URL metadata as a mock, counting a
dictionary read as a method/network call. The fixture now uses an ordinary URL
mapping; trading decision, Risk and Execution call comparisons are unchanged.
The reproduction is retained as `baseline-mock-failure.txt`.

Next implementation gap: automatically connect already verified whole-trade
fill/commission/funding/position-ownership accounting to later learning outcomes.
The current automatic booking path still cannot establish complete realized
money; its outcome remains UNASSESSABLE. Keep Learning rules and Strategy
Governor authority unchanged. Stage 7 remains incomplete.
