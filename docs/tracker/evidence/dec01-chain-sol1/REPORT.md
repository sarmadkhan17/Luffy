# SOL-1 — DEC-01 dependency-chain mapping

Result: **DEC-01 BLOCKED**. DEC-04 is not started. No Kernel, provider, venue,
Dashboard or live/trading call is made.

The original DEC-01 condition requires the normal source path to bind canonical
identity, exact spec, required/optional analysts, clocks, costs, book and
supporting/opposing evidence in one Opportunity Context. Missing required input
must block; optional absence must not block by default; mixed cuts must refuse.
None of these conditions or dependency edges is weakened.

| Inspected row | Existing evidence | Classification / terminal boundary |
|---|---|---|
| DATA-05 | Engineering closure at fc16e07; data05-revision-truth/closure.md reports 208 offline passes and append-only revision/index/clock/reopen controls | Already satisfied but canonically unmapped. Owner explicitly confirmed engineering closure. Reconciled to CLOSED at architecture/offline scope; no implementation change or repeat DATA-05 tests |
| STR-02 | compile_spec parses entry/filter/exit DSL once and derives data requirements; FeatureCtx carries candle/BTC/derivative/reference/universe contexts to live and historical paths. test_compile, test_evaluation_context, DSL and population regressions | Existing supported feature path PROVEN offline. One ACTUAL_GAP in compiled spec version binding reproduced and fixed. Full row remains BLOCKED on STR-01 and WRLD-05, including missing WorldModel integration |
| WRLD-05 | WorldModel has typed Scope/Horizon observation/claim queries and retained WorldModelRecord/WorldHistory reconstruction; these are existing standalone APIs | IMPLEMENTED_BUT_UNMAPPED for standalone API/replay evidence; ACTUAL_GAP in normal strategy/research integration. FeatureCtx, compiled entries and research Bundle have no typed WorldModel query input; the inspected normal consumers import/use no WorldModel reader. WRLD-03 is also EVIDENCE_TO_MAP; DATA-05 is satisfied |
| DEC-01 | opportunity-live-inputs.v1 validates canonical candidate identity, strategy version/signal fingerprint, source clocks/freshness, venue book projection and world cut; required_roles distinguishes required/optional sources; test_opportunity_live_integration covers these controls | Supporting clauses PROVEN offline, not full closure. Required/optional analyst declarations and supporting/opposing analyst bundle are not bound through the inspected source contract; its source factory refuses an analysts role. STR-02/WRLD-05 remain unmet. DEC-01 stays BLOCKED |

The normal WorldModel integration finding is scoped to the inspected normal
strategy and research interfaces, not a claim that WorldModel itself is absent.
world-normal-path-gap.json records AST imports and actual dataclass/signature
fields. Arbitrary data inserted into `market` is not a stable typed WorldModel
interface. Frame-context parity alone cannot certify historical WorldModel
support. The standalone API has not been relabeled a normal consumer.

context-analyst-gap.json records the unsupported role and upstream gates. Source
required_roles operate on source families, not an explicit required/optional
analyst roster. Existing investigation or world references do not independently
prove that the analysts used by the decision, their requirements and their
supporting/opposing evidence were bound. This is an unmet original clause;
there is no assumption that a future schema must literally use the name
`analysts`.

Upstream prerequisites are explicit:

- STR-02 → STR-01 and WRLD-05. STR-01 remains EVIDENCE_TO_MAP and depends on
  RES-08 and QNT-08.
- WRLD-05 → WRLD-03 and DATA-05. WRLD-03 remains EVIDENCE_TO_MAP and depends
  on DATA-02 and WRLD-01. WRLD-01 remains EVIDENCE_TO_MAP.

The compiler correction is a bounded demonstrated defect; it does not close
these prerequisites. Normal WorldModel integration and the DEC-01 analyst
contract remain unresolved. Their acceptance must preserve the prerequisite
WorldModel/strategy contracts; this package does not invent an alternative
world-state truth system or proceed to DEC-04.

## Demonstrated compiler defect and correction

The compiler retained the caller's mutable StrategySpec. After compilation,
changing entry_long from `close > 0` to `close < 0` and timeframe from 15m to
1h left the compiled AST and spec hash unchanged while its public spec pointed
at the changed object. It evaluated the old expression on the changed timeframe
and returned `[true, true, true]`. baseline-reproducer.json records the result;
three new regression cases failed before the fix (baseline-tests.txt).

compile_spec now keeps a deep copy of the exact derived spec. Historical entry
and exit evaluation verify its hash before using it. The live evaluator performs
the same check before timeframe filtering and reports
compiled_spec_version_mismatch instead of emitting a stale-version signal.
Tests cover mutation of the original caller's logic/timeframe/nested universe,
mutation of the compiled nested universe in entries/exits, and live version
mismatch diagnostics. Existing numerical rules, DSL features, data readers,
Risk and execution are unchanged.

Validation: **53 focused tests passed**, including the four new regression cases;
**71 DSL/reference/population regressions passed**. These are 124 distinct cases.
No DATA-05 tests were rerun. The local Claude execution probe returned
`Not logged in`; the bounded fix used the available Sol runtime. A scoped code
commit contains only trader/strategy/compile.py and tests/test_compile.py;
terminal.yaml records its exact identity. Existing other-lane work is preserved.

Cross-tracker changes are limited to STR-02, WRLD-05, DATA-05 and DEC-01
mapping/status, synchronized STATE/NEXT and bundle records, and optional
Markdown rows. DEC-04, OUT-01, OUT-03, GUI-02, DATA-03 and learning/outcome
rows do not change. DATA-05 retains its original requirement and offline scope.
No visual or deployed/runtime approval is inferred.

OUT-01 is **not unblocked**: DEC-04 cannot close while DEC-01 remains blocked;
OUT-01's separately recorded full normal execution-path evidence is also still
unmapped. Next dependency work is WRLD-03/WRLD-01 evidence mapping, then normal
WRLD-05 strategy/research query integration and the STR-01 gate. DEC-01's
analyst contract must be completed before its own closure. DEC-04 remains gated.

Final control validation: **22 checks passed**. Tracker identity,
original conditions/edges, isolated row scope, DATA-03/DEC-04 preservation,
DATA-05 reuse, four-way control parity, commit/source hashes and read-only
Tracker availability pass. Scoped diff checks and AST-only graph refresh pass.
Compiler/test commit: `f55ec97c4a2bef04712abc45832e6d3f44f01162`. Evidence/control documentation remains local
and uncommitted; it contains no runtime closure or activation authorization.
