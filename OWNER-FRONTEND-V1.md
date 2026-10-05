# LUFFY — OWNER-FRONTEND-V1

## Purpose and provenance

This file consolidates the frontend implementation brief and performance addendum previously supplied in the ChatGPT conversation. They were chat instructions, not existing repository documents. This is the single handoff for that work; it replaces the overlapping chat prompts, not SDD.md.

**Package:** SDD-STAGE-8-OWNER-FRONTEND-V1  
**Implementer:** Astra  
**Independent reviewer:** Opus  
**Delivery boundary:** reviewable, tested implementation; NOT_DEPLOYED. No commit before independent review.

## Current checkpoint — do this first

Read this handoff and the local SDD.md, STATE.yaml, NEXT.yaml and AGENTS.md. Continue the isolated baseline profiling already underway. Return the measured loading bottleneck, test conditions and proposed affected files before beginning the wider frontend replacement. This checkpoint does not authorize production profiling, deployment, service changes or another design round.

The milestone requirements below define the implementation scope after that checkpoint.

## 1. Owner priorities

1. Correctness and trading-core safety.
2. Fast access to real, useful information.
3. Reliable interaction and evidence navigation.
4. The approved visual direction and purposeful animation.

The owner reports dashboard loading takes approximately one minute or more and disrupts operations. Investigate this report rather than assuming its cause. A prettier dashboard with the same delay is not acceptable. Do not let frontend work become a separate open-ended project that delays LUFFY itself.

## 2. Checkout and safety boundary

- Use `/home/sarmad/trader-world`. Verify its actual local branch, HEAD and dirty files first. The last reported world-model-foundation HEAD was `1eb00e1`; do not assume it is still current.
- Do not use a stale remote branch as the implementation baseline.
- Do not edit the production checkout `/home/sarmad/trader` or `/home/sarmad/trader-health-prod-candidate`.
- Preserve unrelated changes, including `tests/test_strategy_health_unreadable_plan.py`, `knowledge/`, `graphify-out/` and stray untracked files.
- No production stress test, production-database copy, venue/API trading call, restart, deployment, watchdog change, control-state mutation or production database write.
- Use temporary/test stores or an existing authorized representative copy. Do not open the live production store merely to improve realism.
- Do not edit SDD/STATE/NEXT in the implementation patch. Report any genuine architecture conflict rather than silently changing the authority.

## 3. Existing locked architecture

Read the exact local SDD rather than treating this summary as a replacement.

**Navigation, in order:**

`Overview | Trades | Research | Strategies | LUFFY | Operations | Live System | Knowledge | Diagnostics`

**Stack:** React, TypeScript and Vite; Radix primitives and product-owned LUFFY components; TanStack Query; Motion; selective Three.js/React Three Fiber/Drei; Zustand only for small client state; Lightweight Charts and D3 where appropriate. GSAP is optional when justified, not a mandatory second animation system. Tailwind is selective.

FastAPI serves compiled frontend assets and authenticated APIs. Reuse the existing GraphQL/REST/WebSocket contracts where suitable. No production Node server, Next.js, Electron, microfrontends or new UI-state service.

ThreeUI, 21st.dev, UI UX Pro Max and similar resources are optional references, not mandatory dependencies or architecture authorities. Install only what the implementation needs.

## 4. Approved visual and interaction direction

The chosen direction is a professional trading workstation with restrained One Piece ocean/navigation atmosphere, readable dark panels, consistent typography and restrained accents. Animation means meaningful system flow and interaction—not anime, cartoon islands, storybook scenes, giant quotations or fictional agent activity.

Use the approved screenshots from the conversation when supplied as visual references. They do not authorize invented numbers, changed navigation or the accidental NEXUS branding. The product name is **LUFFY** throughout. Build actual components; do not use a mockup screenshot as the functional interface.

Share tokens and components across pages. Avoid another visual-design selection cycle. If a decorative asset is unavailable, use a simple local fallback without blocking profiling or functional work. Do not include Higgsfield/video production in this milestone.

### LUFFY — interaction first

- The main experience is conversation with LUFFY, not a statistics dashboard or capabilities manual.
- Use the existing grounded chat backend; do not rebuild the LLM architecture.
- A mechanical avatar/core follows real UI states: idle, request pending, responding and error. Audio-reactive speaking/listening states must correspond to actual playback/capture.
- Preserve a readable transcript, responsive composer, cancellation/error handling and evidence links into relevant trade, strategy or research detail.
- No invented internal thoughts, confidence, beliefs or simulated ongoing activity.
- Reuse approved voice functionality if it exists. Otherwise mark voice unavailable and keep text chat fully functional; no new paid/provider-dependent voice project. Microphone capture requires explicit consent.

### Knowledge — evidence exploration

- Preserve `Knowledge | Evidence | Timeline | Code` lenses.
- Provide search, filters, pan/zoom, selection, path tracing and a useful provenance inspector.
- Show actual nodes and typed relationships from available contracts. Distinguish inferred relationships, code references and verified evidence.
- Graphify is an engineering tool, not a substitute for runtime domain knowledge.
- Animate selection and evidenced paths, not fabricated discoveries. Provide an accessible list/detail fallback.

### Live System — actual runtime visibility

- Show real component identities and documented connections.
- Distinguish architectural topology from observed runtime activity.
- Flow animation indicating activity must be supported by telemetry/events. Missing or stale telemetry cannot appear healthy or active.
- Component selection opens state, freshness and supporting evidence. No fictional agents or decorative success indicators.
- Keep raw internals in advanced detail.

These three tabs receive the strongest visual emphasis. The other six use the same components and straightforward functional layouts.

## 5. Functional coverage using existing backend capabilities

- **Overview:** truthful account, exposure, position/protection and control summary; genuine owner-action items only.
- **Trades:** positions, history, decisions/rejections and available forensic lineage.
- **Research:** both research families; Q/P/E/R/Run/Bank; registration, recall, cost, and shadow invocation/report artifacts.
- **Strategies:** immutable versions, lifecycle, health, approval and evidence already available.
- **Operations:** current activity, investigations, scanning/decisions/rejections and research activity where recorded.
- **Diagnostics:** health, freshness, failures, resources and recovery evidence.

Add thin authenticated read adapters only where needed. Do not implement missing trading/research intelligence just to populate a page.

Preserve `context_only`, `INCONCLUSIVE`, `NOT_ASSESSED`, `NOT_ESTABLISHED`, deployment maturity and all other source contract vocabulary. Keep research cost families separate and do not sum copied Bank telemetry as additional cost.

`Needs You` is a global owner-action surface, not another primary tab. Do not invent pending approval objects or enable unavailable approval actions.

Server-side configuration selects permitted data sources. Do not accept arbitrary database/filesystem paths from the browser. Clearly distinguish LIVE, SHADOW, REPLAY and DEMO. Fixtures belong only in explicitly labelled isolated preview/test mode; never substitute them when a real API fails.

## 6. Performance addendum — required

### Measure before changing

Profile the existing behavior on the isolated setup:

- Initial document response and usable navigation.
- Time until real Overview data appears.
- Each startup API duration and payload size.
- Database query duration and lock waits.
- Browser parse/render time and long tasks.
- Duplicate requests, polling loops, subscriptions and reconnects.
- External-service dependencies on the startup path, without making unauthorized external calls.

Identify specific responsible functions/queries. A framework migration alone is not evidence of a fix. If no representative copy is available, state the fidelity limitation of a synthetic dataset; do not claim production-equivalent measurements.

### Engineering acceptance targets

Validate these on a stated browser/VM and dataset:

| Metric | Target |
| --- | --- |
| Usable navigation | Within 1 second |
| First useful Overview data | Within 3 seconds |
| Cached tab content | Within 300 milliseconds |
| Common summary/list API responses | p95 below 500 milliseconds |
| Chat input and navigation | Remain responsive while heavy panels load |

These are engineering targets, not achieved results or automatic production authority. Report misses explicitly. A shell, spinner or skeleton does not count as useful data. Separate cold-browser and warm-cache measurements and distinguish client rendering from backend response time.

### Implementation constraints

- Fetch only what the visible page needs. Do not load all nine tabs, entire graphs, full trade history or all research chains at startup.
- Overview must not wait for 3D, mechanical-avatar assets, graph layout, LLM responses or nonessential imagery.
- Lazy-load heavy code/assets. Suspend hidden animations and redundant subscriptions. Prevent overlapping refreshes and reconnect duplication.
- Paginate/virtualize large lists and bound graph rendering scope. Do not rebuild layouts on every update.
- Investigate unnecessary full scans, per-row repeated queries, full-history serialization and write-capable Journal initialization/backfills in read paths.
- Preserve existing verification guarantees. Never improve speed by bypassing source checks or silently presenting stale protection/control data as freshly verified.
- Caches need explicit scope, freshness and invalidation. Show source time, last refresh, stale and error state. Never cache permission decisions as an authorization shortcut.
- Any proposed index/schema change must be explicit, measured and independently reviewed; never applied to production in this task.
- A failed API affects its panel, not navigation or the whole application.
- UI errors, animation and GPU work must not affect trading-core execution or protective-stop management.

### Measure isolation

Compare, on an isolated test setup:

- Dashboard closed.
- Overview open.
- LUFFY, Knowledge and Live System open.
- Rapid navigation and repeated reconnects.

Capture API latency, browser/server CPU and memory, Journal write latency, representative core-loop timing and WAL/checkpoint behavior where applicable. Look for attributable stalls, lock timeouts, unbounded refresh work and material latency regressions. Separate processes alone do not prove isolation.

## 7. Migration and command safety

Inventory and preserve existing owner capabilities: auth/session, control state, positions/trades, chat, Attention, investigations, knowledge/vault, logs, autopsy and live updates.

Keep legacy fallback available until replacement parity is independently reviewed. Preview only on a separate loopback port with test/authorized non-production stores.

Reuse existing typed authenticated control commands and confirmation requirements. Do not widen authority or add venue commands through chat/graphs. Exercise all control actions against mocks/test services only. Secrets must never reach frontend bundles, logs or screenshots.

## 8. Acceptance and independent review

Demonstrate:

- All nine routes/deep links, with real API-backed behavior where available.
- Working grounded chat and evidence navigation.
- Usable Knowledge/Live System inspection and explicit unavailable states.
- Auth/session-expiry behavior and safe typed control handling.
- Stale, disconnected, error and missing-data states.
- Reconnect behavior without duplicate events/subscriptions.
- No fabricated activity, metrics, approvals or silently substituted fixtures.
- Keyboard support, visible focus, responsive layout, reduced motion and WebGL fallback.
- Preserved research/trading contracts and isolated UI/backend behavior.

Run frontend type checks, production build, component/browser tests and focused affected backend regressions. Reproduce exact suspected baseline failures on a clean baseline; do not launch the whole repository suite by default. Inspect rendered pages as well as test results.

Astra implements; Opus independently reviews the actual diff, rendered application, adversarial tests and measurements. Implementation success is not deployment authorization.

## 9. Final handoff after implementation

Return:

1. Actual base HEAD and changed files.
2. Supported root cause of the slow load.
3. Before/after measurements with dataset, browser, machine, cache and test conditions.
4. Exact local preview command, port and data-source mode.
5. Functional coverage and explicit backend gaps.
6. Screenshots of Overview, LUFFY, Knowledge and Live System.
7. Frontend/affected backend tests, baseline failures and whitespace checks.
8. Legacy parity, safety findings and remaining blockers.

Do not commit, deploy, restart production, change watchdog state or continue into another package automatically.
