# LUFFY-FRONTEND-COMPLETION-M2 — functional + evidence completion

Base `ce2fa2c`, branch `owner-frontend-live-binding-v1`. **Uncommitted, not
deployed, production not restarted.** No kernel, trading, Risk, Execution or
watchdog code changed. No Graphify run. M1 routing, GraphQL threading, lazy
loading and Trade History paging were not redesigned.

## A. M2_STATUS: PASS

All 9 routes are FUNCTIONAL against their existing authoritative stores.
Evidence the kernel does not record stays UNAVAILABLE with a reason (§F).

## B. Route matrix

| Route | Status | Completed in M2 |
|---|---|---|
| Overview | FUNCTIONAL | Owner activity island (`/overview/activity`): executed and skipped decisions (linked), risk-block classes, active/paper strategies with journal economics, recent lifecycle events, latest research run and newest results (linked, with seed strategy), recorded `risk_state` / `rent_state`, unresolved owner requests. Positions link to their trade story. Approvals and News Guard are explicitly unavailable. Close Trade is still absent. |
| Trades | FUNCTIONAL | `#trades?trade=<id>` trade story: 8-step chain (opportunity/scan → market context → decision → signals & votes → strategy → execution → accounting → outcome), each step RECORDED/VERIFIED/OPEN/UNAVAILABLE. Also shown: cycle regime/ADX/BTC trend, the analyst votes of the exact cycle, the full journal trade record (qty/side/prices/times/P&L/MFE/MAE), receipts with integrity, and the outcome. Every drawer links to the story. Paging is unchanged. |
| Research | FUNCTIONAL | Results paging (`offset`, rowid order) with record links and a seeding-strategy column. `#research?combo=<hash>` item: parts/ablation/evaluation (≤16 kB), terminal reason, parent/children navigation, candidate bank row, registrations, seeding strategy. Research Bank adds assessed ideas (idea → writer outcome → spec). Questions, Plans, Prior recall, Costs and Shadow reports keep their UNAVAILABLE reasons. |
| Strategies | FUNCTIONAL | Family filter (`#strategies?family=<kind>`), and a `#strategies?id=<id>` workspace. The workspace shows identity/hashes, hypothesis, full declared spec, and lifecycle (registry + exact-id `strategy_*` control events + `brain_events` lifecycle kinds). It also shows family, parent, children and siblings, the newest postmortem verdict (labelled as a verdict at its time, not current health), the source idea (provenance `idea_id` / `idea_consumed.spec`), the research it seeded, brain events by exact subject, and linked trades. |
| LUFFY | FUNCTIONAL | Each reply lists the stored records it mentions and the read-only tools the agent consulted. A mention is an exact primary-key id only; names are never linked (correction pass). Id-shaped tokens that match no record are listed as unresolved. A failed lookup is shown as unavailable. The UI states that a mention is not the reply's source. Chat stays non-operational: the test asserts zero gateway calls. Transcript, composer and cancellation are unchanged. No voice work. |
| Operations | FUNCTIONAL | A chronological "What Luffy recorded doing" timeline now sits directly under the owner controls: directional/executed decisions, trade opens/closes, non-cooldown control events and strategy lifecycle events. Each carries its record link and can be filtered by type; signal cooldowns are counted separately. `#operations?decision=<id>` shows the decision record with cycle, votes, named strategies (registry-checked), trades opened from it, and the outcome. |
| Live System | FUNCTIONAL | An "Observed data movement" panel (`/system/observed`) lists records written between components over stated windows, marked OBSERVED / NONE_IN_WINDOW, DECLARED vs "not in the declared map", with a ≥ marker when the bounded window is saturated. Declared edges with no proving record are listed as unobserved. Nothing is animated (`events` is still `[]`). |
| Knowledge | FUNCTIONAL | `#knowledge?note=<id>` selects the note in the graph. If the note is outside the 500 loaded nodes, it is read and shown directly. Each note lists linked records: ids in its text that exist exactly in the journal, plus a frontmatter `family` → registry `kind` link. The existing body, source/code links, chronology/git history and path trace are unchanged. |
| Diagnostics | FUNCTIONAL | Probes measured per request (journal read, newest decision, vault, frontend build). Control-event history uses keyset paging with an optional cooldown filter. M1 storage, resources, watchdog, collectors, owner audit and recovery are unchanged. |

## C. Connected workflows (all exact recorded ids)

- Overview → decision / strategy / research result / trade story.
- Trade → decision (`trades.decision_id`) → cycle (`decisions.cycle_id`) → votes (`votes.cycle_id` equality).
- Trade → strategy (`trades.strategy_id`) → trades, seeded research, family.
- Decision → trades opened from it, and strategies (`strategy_ids` / signal `strategy_id`, each checked against the registry).
- Research result → parent/children (`research_combos.parent`), candidate/tests (`hash`), and seeding strategy (`trigger = 'seed:<id>'`).
- Strategy → research seeded, source idea (`idea_id`), parent/children (`parent_id`), family (`kind`).
- Knowledge note → strategy / trade / decision / research (ids present verbatim), and family.
- LUFFY reply → the records it mentions.
- Every in-drawer link now closes the drawer on navigation.

Never linked: strategy notes to strategies by title (notes store only the name), scan ids (no by-id scan record), ids absent from the registry (shown as "not in registry").

## D. Backend contracts (read-only; `trader/dashboard/owner_reads.py`, `owner_api.py`)

| Endpoint | Change |
|---|---|
| `GET /trades/{id}/lineage` | + `cycle`, `votes`, `votes_basis`, `chain[]` (`step`, `status`, `at`, `ref`, `summary`), `links` |
| `GET /decisions/{id}` | new |
| `GET /research?limit&offset` | + `offset`, `results_page`, `results[].seed_strategy`, `ideas[]` |
| `GET /research/combos/{hash}` | new |
| `GET /strategies/{id}` | + `brain_events`, `family`, `source_idea`, `postmortem`, `research_seeded`; lifecycle includes `brain_events` lifecycle kinds |
| `GET /operations/activity` | + `timeline[]`, `signal_cooldowns`; `control_events` excludes `signal_cooldown` (12 238 of 12 381 production rows) |
| `GET /overview/activity` | new |
| `GET /system/observed` | new |
| `GET /knowledge/note` | + `records`, `family`, `records_basis`, `records_error` |
| `GET /diagnostics` | + `probes` |
| `GET /diagnostics/events?before&limit&include_cooldown` | new (keyset by id) |
| `POST /chat` | + `links`, `links_basis`, `links_error`, `consulted`, `consulted_note` (`operational: false` unchanged) |

Chat provenance: `AnalystAgent` records `consulted` (tool name, bounded arguments, row count, error), and `ChatEngine.consulted` exposes it. This is not operational: chat still issues no owner request.

**No schema change. No index added.** Every new read uses an existing primary key or index, or a bounded window:
- votes are read through `idx_votes_symbol` over ±300 s around the cycle, then filtered by exact `cycle_id` (0.5 ms, versus a ~0.9 s full scan);
- `brain_events` reads cover the newest 20 000 ids;
- decisions and control-event windows use rowid/id order;
- `research_combos` parent/trigger lookups are LIMITed.

The `trades.decision_id` lookup is unindexed but the table holds one row per entry (65 rows in production).

## E. Evidence / provenance behaviour

- Only ids returned by a backend record become links (`a[data-record]`). A reference that is recorded but not openable shows the id and "not openable here".
- Malformed data is shown as malformed, never repaired:
  - unreadable `signals_json` → `signals` UNAVAILABLE;
  - broken `brain_events` detail → `{"_unparseable":true}`;
  - a bad `rent_state` → UNAVAILABLE;
  - a malformed chat mention list or a failed lookup → "Stored-record lookup unavailable (<reason>)";
  - a result over 16 kB → withheld, with the reason.
- Missing ids return 404 and render as an error with zero links. A failed read renders as an alert while navigation stays available.
- The postmortem verdict is shown with its event time and "not current health". Strategy hashes keep "current registry row, not the version at entry".

## F. Kernel-owned evidence still unavailable

- strategy version/spec hash at entry;
- venue-fill-verified accounting (production: 0 fully verified);
- funding attribution;
- continuously maintained per-strategy health, capacity, allocation, and spec version history;
- research questions, frozen plans, costs, shadow reports, prior recall;
- approval objects; a by-id attention scan record;
- kernel process metrics, API latency history, a structured incident ledger, and open venue orders;
- News Guard (parked);
- strategy-id ↔ vault-note mapping (the vault writer names notes by strategy name).

## G. Performance / bounded reads

Production journal (1.8 GB), read-only `mode=ro` (`frontend/evidence/m2/m2-prod-reads.json`):

| Read | Time |
|---|---|
| trade lineage (7 votes) | 3–6 ms |
| decision | <1 ms |
| strategy | 9–18 ms |
| operations (80 timeline items) | 19–21 ms |
| overview activity | 6–7 ms |
| observed flows | 7 ms |
| research item | 7–8 ms |
| research page | 92–353 ms, cache-dependent (the M1 `GROUP BY` counts) |

Concurrency (`frontend/evidence/m2-concurrency.json`): with every decisions read slowed to 2 s, reads that do not touch decisions stayed under 1 s, and the strategy workspace rendered before any slow read finished. Six decisions-touching reads ran side by side, not serially.

## H. Tests

| Suite | Result |
|---|---|
| Backend `tests/test_owner_reads_m2.py` (auth, story chain + exact-cycle votes, unlinked trade, decision malformed/unregistered/404, research paging/links/item/oversize, strategy exact subject/family/idea/postmortem, timeline refs + cooldowns, overview, observed vs declared, note exact ids, chat links + duplicate-name refusal, keyset paging, probes, empty journal, 1 200-row bounded windows, slow-read concurrency) | 21 |
| Relevant backend suite (owner reads/API, chat, trade history, auth, pipeline, read cache, owner interface, rent, attention view, single creation path) | 396 passed |
| Frontend typecheck and production build | pass |
| Vitest (`tests/m2.test.tsx` adds 14) | 101/101 |
| Playwright full suite (`m2-routes.spec.ts` adds 9) | 97/97 |

Playwright M2 coverage: 9 routes, the connected workflow, drawer → record, LUFFY mentions with zero gateway calls, injected read failures, unknown ids, session loss on a deep link, slow-read concurrency, phone viewport. It also covers the M1 route delivery, Trade History and protection specs.

`tests/test_owner_interface_r3–r6.py` fail at collection because they read `trader/dashboard/web/index.html`, deleted in 8385e9d (before this base). This predates M2 and was not touched.

## I. Changed files

Backend:
- `trader/dashboard/owner_reads.py`
- `trader/dashboard/owner_api.py`
- `trader/chat/agent.py`
- `trader/chat/engine.py`

Backend tests:
- `tests/owner_frontend_fixture.py` (opt-in `seed_m2`; the reset also clears outcomes, research candidates/tests and M2 notes)
- `tests/owner_frontend_server.py` (`{"m2": true}`)
- `tests/test_owner_reads_m2.py` (new)

Frontend:
- `frontend/src/links.ts` (new)
- `frontend/src/components/records.tsx` (new; primitives moved out of `LiveReads`)
- `frontend/src/views/Evidence.tsx` (new)
- `frontend/src/views/{LiveReads,Routes,Operations,Overview,GraphView,Luffy,Activity}.tsx`
- `frontend/src/components/workspace.tsx` (drawer closes on navigation)
- `frontend/src/adapters/{contracts,live}.ts`
- `frontend/src/context.tsx`
- `frontend/src/Shell.tsx` (route parsed before `?`; Live System observed panel)
- `frontend/src/live.css`

Frontend tests:
- `frontend/tests/m2.test.tsx` (new)
- `frontend/tests/browser/m2-routes.spec.ts` (new)
- `frontend/tests/live.test.tsx` (the chat reply now carries mention fields)

Evidence:
- `frontend/evidence/m2/*` (route and record screenshots, mobile trade story, browser results, production read timings)
- `frontend/evidence/m2-{routes,workflow,concurrency}.json`

The full browser run also regenerated existing M1 screenshots and performance JSON, which were already modified before M2.

## J. Blockers remaining for M3

None block M3's visual/product work. Open items for M3 or kernel follow-ups:
1. Visual treatment of the new panels. They reuse existing components and add minimal CSS. The trade story and strategy workspace are long single columns.
2. The research page's GROUP BY counts reach ~350 ms cold. Caching or a summary would be kernel-side or a later read change.
3. Kernel-owned evidence in §F, notably strategy version at entry, funding attribution, a by-id scan record, and a strategy-id field in vault notes.
4. Chat evidence stays mention-based until the agent's tools return record ids.

## Correction pass (Astra's three blockers)

Scope: no redesign, no new features, no M3 visual work, no kernel changes, no deploy.

1. **Malformed evidence is never empty or true.**
   - `frontend/src/adapters/readContracts.ts` checks the shape of every M2 read at the adapter boundary: objects are objects, lists are lists, list entries have their required fields and types, ids are non-empty strings, and nullable fields are declared as such.
   - A violation throws `ContractViolation` naming the field: "MALFORMED evidence: the operations/activity response timeline is not a list…". The view shows "Data unavailable" with that reason, never "none", "no activity" or "no records". Views therefore only see VALID_WITH_DATA or VALID_EMPTY.
   - Backend: `signals_json` that is not a list, or a list with non-record entries (e.g. `[17]`), is reported under `unavailable` (`signals`: "1 of 1 signals_json entries are not signal records"). The trade story's signals step is marked `malformed`, and Operations counts `window.malformed_signals`.
   - All 95 real production payloads (every strategy, every trade lineage and each other read) pass the contracts, so honest data is not rejected.
2. **Live System completeness.**
   - Backend: only a flow with `status=observed` and count > 0 proves a declared edge (a zero count or failed read no longer counts). The response now lists `declared_edges`.
   - Frontend: "Every declared edge has an observed record in its window" appears only when the response is valid, `declared_edges` is non-empty, every declared edge has an observed flow with records, and the backend's unobserved list agrees. Otherwise the view shows the unobserved table, "No declared edges were returned", or UNAVAILABLE ("inconsistent… No completeness claim is made").
   - Astra's reproduction (`unobserved_declared_edges: "garbage"`) now renders MALFORMED with no claim.
3. **Chat attribution is exact-id only.**
   - Name matching is removed (`names=`, `exact_unique_name` and the 500-row window are gone). `recognize()` resolves only by primary-key `IN` lookup, so there is no row window at all.
   - `unresolved_ids()` reports id-shaped tokens (`pos_…`, `dec_…`, 16-hex, strategy id prefixes) that resolve to nothing, as unresolved. They are never linked.
   - The frontend rejects any basis other than `exact_id`.
   - Tests cover: exact id linked; name only, unique name and duplicate names never linked; name plus an unrelated id resolves only the id; an id beyond 650 bulk rows resolves.
   - Note ids are not a supported chat id form (vault paths contain spaces).
4. **Chat lookup failure is not empty success.**
   - The backend lookup is bounded (`CHAT_LINK_TIMEOUT_S = 5`). On failure it returns `links: null`, `unresolved: null` and `links_error` (`lookup_failed:<Type>` or `lookup_timeout`); the reply itself still stands.
   - The adapter keeps three states: found, zero ("No stored record was mentioned by id."), and unavailable ("Stored-record lookup unavailable (<reason>)"). The unavailable state covers an error, a malformed list, or a backend that reports no lookup. `links: []` with an error is unavailable, never zero.
   - LUFFY shows unresolved ids separately ("Not found in the stores (unresolved ids): …").
   - The browser suite found a real bug in this path: `send()` did not copy `unresolved` into the message. Fixed.

Correction-pass tests:

| Suite | Result |
|---|---|
| Backend `tests/test_owner_reads_m2.py` (27 tests) plus the relevant suite | 402 passed |
| Vitest (+32 in `tests/m2.test.tsx`: 8 valid-empty contracts, 15 malformed contracts, 4 malformed renders, 3 Live System claim cases, 4 chat-state cases) | 133/133 |
| Typecheck and production build | pass |
| Playwright full suite (9 routes, M2 workflow, LUFFY exact-id, M1 route delivery, Trade History) | 97/97 |

Additional files changed in the correction pass:
- `frontend/src/adapters/readContracts.ts` (new)
- `frontend/tests/live.test.tsx` (wording for a backend without a lookup)

M2_STATUS after correction: **PASS**.

## Final correction (existence vs display cap)

**Defect.** `recognize()` capped its matches at 50, and `unresolved_ids()` compared tokens only against those 50. The 51st existing id was therefore reported as not found.

**Backend fix.** Existence is now separated from display:
- `recognize()` returns the complete exact-id result, uncapped.
- `unresolved_ids()` classifies against that complete result.
- `resolve_mentions()` caps the lists only after classification (`DISPLAY_LINKS = 50`). It returns `links`, `resolved_count`, `truncated_count`, `unresolved`, `unresolved_count` and `unexamined_tokens`.

Chat (`POST /chat`) and Knowledge (`records_resolved_count`, `records_truncated_count`, `records_unresolved_count`) both use it. On lookup failure all counts are `null` and `links_error` is set.

**Frontend fix.**
- The adapter requires shown + truncated = resolved, and shown unresolved ≤ unresolved. Counts that disagree are malformed ("Stored-record lookup unavailable (malformed response)" / note contract violation).
- LUFFY and Knowledge show "50 of 51 matched stored records shown." Truncated valid ids are never listed as unresolved.
- Exact-id-only attribution, no name matching, lookup-error semantics and the mention ≠ source wording are unchanged.

**Astra's reproduction** (`spec_review_000` … `spec_review_050`, all mentioned): chat API and Knowledge API each return 50 displayed, 51 resolved, 1 truncated, 0 unresolved.

**Also tested on both surfaces:**

| Case | Displayed / resolved / truncated | Unresolved |
|---|---|---|
| 49 valid | 49 / 49 / 0 | 0 |
| 50 valid | 50 / 50 / 0 | 0 |
| 60 valid | 50 / 60 / 10 | 0 |
| 50 valid + 1 missing | 50 / 50 / 0 | the 1 missing id |
| 60 valid + 2 missing | 50 / 60 / 10 | the 2 missing ids |

Further backend cases:
- duplicate ids count once;
- case and hyphen variants are not ids;
- id-shaped absent tokens are unresolved;
- a lookup failure with 51 ids still reports unavailable with null counts.

Frontend unit tests cover the 51, 60+2 and 50 cases, counts that disagree, and the note contract and display.

**Tests:**

| Suite | Result |
|---|---|
| Backend owner reads + owner API + chat | 98 passed |
| Vitest | 139/139 |
| Typecheck and production build | pass |
| M2 browser workflows | 9/9 |
| Current production payloads (95) against the read contracts | all valid |

**Files changed in this correction:**
- `trader/dashboard/owner_reads.py`
- `trader/dashboard/owner_api.py`
- `tests/owner_frontend_fixture.py` (FakeChat `echo` mode)
- `tests/test_owner_reads_m2.py`
- `frontend/src/adapters/{live,contracts,readContracts}.ts`
- `frontend/src/context.tsx`
- `frontend/src/views/{Luffy,Evidence}.tsx`
- `frontend/tests/m2.test.tsx`
- this report

M2_STATUS: **PASS**.
