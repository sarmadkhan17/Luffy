# Review 09 — frontend views and test contracts

Scope: existing `frontend` subsystem (Owner frontend), exact 68 inventory paths in `frontend/src/views/**` and `frontend/tests/**`. The existing inventory is the denominator; `frontend-views-files.json` contains one classification/evidence row per original path. New `frontend/tests/review09.test.tsx` is recorded separately, not a new ownership inventory. Source baseline: `ef9fca839c23d48198c5f2361bbf3623d94266fa`.

## Confirmed defects corrected

1. **Recorded links silently lost their destination.** `links.ts` emits exact `trade`, `id`, `combo`, `note` and `family` hash parameters. Existing Evidence/LiveReads/GraphView and milestone contracts consume them. Current Shell dispatches modern Trades/Strategies/Research/Knowledge, which ignored those parameters, so LUFFY's recorded evidence cards and linked lineage opened only the generic page. Restored destination rendering using the existing TradeLineage, StrategyDetail, ResearchItem, NoteDetail and LinkedPanel; restored registry family filtering. No invented joins or new backend reads/contracts. Encoded IDs, close links and same-route hash updates are regression-tested.
2. **Overview signal link used the wrong parameter.** Its “Open in Operations” URL used `id`; OperationsLog reads `decision`. Corrected to the existing `decision` contract, with a rendered signal-dialog regression.
3. **LUFFY recovery guidance pointed to the read-only Operations log.** Updated its Owner controls card to Trades, where current OwnerControls resides. The runtime-review agent corrected the matching NeedsYou link/context greeting and root corrected backend approval guidance. No new action or authority.

The new regressions also verify runtime-agent fixes: cached AVAILABLE health cannot keep controls enabled after a failed refresh; the LIVE shell banner makes no unsupported demo-account claim; read-only adapters expose neither control nor approvalDecision; a valid NeedsYou request remains disabled in that adapter.

## Classification and design boundaries

All 68 owned paths are **KEEP**. Six current views received bounded corrections; no owned file was merged or removed. Existing Evidence/LiveReads panels were reused, keeping record rendering and source/unavailable semantics in one implementation. No new verification/approval machinery.

- Declared Live System nodes/edges remain declared even when isolated or lacking exact flow proof. Aggregate counts never become an observed path. The explanatory walkthrough is not trading activity.
- Versioned authority is separate from legacy registry state/performance. ACTIVE lifecycle/control is not final order permission. Disabled Referee/handoff and absent calibrated economics stay explicit.
- Read-only Tracker remains engineering evidence with workflow counts, not runtime/trading readiness. YAML/SDD/NEXT retain their established roles.
- Missing venue fills, costs, allocation, learning links or later lens capability are limitations/future capability, not assumed defects. Typed evidence queries remain separately available in LUFFY.
- Operations.tsx and Routes.tsx retain direct unit-test/compatibility consumers. Removing them would discard established request/pagination contract tests; their absence from current Shell dispatch is not itself a runtime defect. The runtime agent removed only unreachable Shell dispatch scaffolding.
- Many older browser specs assert prior shell headings/selectors, legacy route composition or a Routes chunk. They are retained milestone regression material, not a claim that every suite passes against the current presentation. Retargeting/retiring this historical test surface is optional future maintenance, not needed for these corrections.

## Functionality, usability and cost assessment

The ten-tab current Shell, record link generator, actual modern destination views, shared evidence renderers and tests were cross-checked against SDD section25 and current code consumers. Owner usability is materially improved: exact recorded evidence now opens from the existing links; recovery guidance reaches the actual controls; closed records retain a way back and family scope has a visible reset. Displayed journal economics remain explicitly journal-booked; missing stored evidence is not admission/readiness.

Static/component inspection found deliberate bounded resource choices: Trades reads at most 10 pages of 200 journal rows each minute and discloses truncation; GraphView caps 40 visible nodes; current Knowledge map caps 12 neighbors/60 contextual dots and retains the full accessible neighbor list. No production CPU/RSS/storage reduction was measured. View changes add no database writes, provider calls, framework or service.

Method: individual source paths, imports/consumers, query/state/render/authority branches, test names/assertions, fixture construction and endpoint schemas were inspected; critical changed paths were exercised with real React component DOMs and adapter spies. This is an offline code/component assessment, **not a fresh real-browser/mobile visual assessment**. Existing historical screenshots were not presented as current evidence. The shared dependency tree lacks `@fontsource/cinzel/400.css`; no usable browser executable was located in inspected existing cache paths. No dependency install or production/default Playwright server was attempted. The Shell unit regression mocks only the unrelated font imports.

## Focused checks

Final command (cwd `frontend`):

```text
npm test -- --run tests/review09.test.tsx tests/glacierTruth.test.tsx tests/m2.test.tsx tests/graphPath.test.ts tests/tradeHistory.test.tsx tests/glacierV2.test.tsx
```

**6 files /112 cases PASS**, including 13 new Review09 cases. Exact output: `frontend-views-focused.log` and identical final `frontend-views-green.log`. `npm run typecheck`: **PASS** (`frontend-views-typecheck.log`). No full CI, canonical integration, service restart, production database access/mutation, provider or venue call.

Red evidence: `frontend-views-red.log` initially reproduces 5 failures (three modern record destinations, same-route destination update, cached health). `frontend-views-links-red.log` adds failing note/family destinations. `frontend-views-overview-red.log` reproduces the wrong Operations parameter. Intermediate Shell-test dependency/fixture-shape failures are retained as `frontend-views-intermediate-shell-fixture-failure.log`; they were test setup issues resolved by mocking fonts and supplying the Bootstrap backend shape, not product defects.

No owner decision required for bounded corrections. No commits from this agent; root coordinates branch commit and graph update. Source frozen after final passing run; reports carry final source SHA-256 values.
