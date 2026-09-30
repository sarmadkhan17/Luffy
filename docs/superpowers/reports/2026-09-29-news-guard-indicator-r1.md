# LUFFY-NEWS-GUARD-INDICATOR-R1

> **STATUS: DEFERRED / TRUTH_BLOCKED / NOT_DEPLOYABLE** (parked 2026-09-29)
>
> **Astra's verdict: BLOCK.** The owner decided not to redesign NewsGuard,
> the scraper, kernel publication or clock semantics in this frontend
> milestone.
>
> **Truth blockers, as recorded by Astra:**
>
> 1. **The source cannot prove CLEAR.** `trader/brain/scraper.py` catches
>    request and parser failures and returns `[]`. A network timeout, an HTTP
>    503 or a malformed feed therefore becomes `[]` → `active: false` →
>    `why: "feed quiet"`, which is a false CLEAR. This R1 classifier treated
>    "feed quiet" as proof of a successful quiet read. Its `FAIL_OPEN`
>    mapping only covers exceptions that reach `NewsGuard.check()`, and the
>    scraper swallows these first. (Section A's "fails open" finding was
>    therefore incomplete.)
> 2. **The classifier accepts contradictory semantic states.** Astra reproduced these independently:
>    - `active: false` with severe headline evidence reads CLEAR;
>    - impossible count combinations read CLEAR;
>    - the frontend can accept `status: CLEAR` with a failure reason.
> 3. **Browser wall-clock handling can restore or extend CLEAR after it
>    should have expired.** (Implementer's reading of the mechanism, not
>    Astra's wording: the local `expiresAt` is recomputed from each
>    response's server-clock offset, so a clock change or a later response
>    can move a reading from STALE back to CLEAR or extend it.)
>
> These are truth-contract problems, not visual bugs.
>
> **What was parked:**
> - The R1 implementation is removed from the active candidate. Every source, test, fixture and CSS change in sections B–G is reverted, and the React frontend, owner API and test fixtures match `3732871` exactly.
> - The LIVE Overview again shows the static placeholder "News guard: unavailable". React has no other News Guard rendering, so nothing claims CLEAR, ARMED, FAIL-OPEN or any other current state.
> - The full R1 implementation (12 files) is kept as a patch that applies cleanly to `3732871` (`git apply --check` passes): [`2026-09-29-news-guard-indicator-r1-evidence/news-guard-indicator-r1.patch`](2026-09-29-news-guard-indicator-r1-evidence/news-guard-indicator-r1.patch). It is a reference only; it must not be re-applied as is.
>
> **Unchanged:**
> - `trader/brain/scraper.py`
> - `NewsGuard.check()`
> - the orchestrator and kernel publication
> - the legacy dashboard and the GraphQL `news_guard` field
> - Trade History and Protection Snapshot
> - the Close Trade backend
> - production
>
> **Future package: LUFFY-NEWS-GUARD-TRUTH-R1** (recorded, not implemented). Required scope:
> - explicit fetch-success / fetch-failure truth from the scraper;
> - a successful quiet feed distinguished from a failed or unreadable one;
> - semantic invariants that tie counts, reasons and state together, so contradictory combinations are refused;
> - the publication timestamp kept separate from the actual feed-fetch timestamp;
> - conservative freshness that cannot revert from STALE to CLEAR;
> - no CLEAR under timing uncertainty.
>
> Only after that package should a React indicator be rebuilt against the new contract.
>
> Everything below is the original R1 report, kept for the record. Its
> claims were made before Astra's review; the blockers above overrule them
> where they conflict.

Date: 2026-09-29. Implementer: Opus (Claude Code). Reviewer next: Astra.
Base: `3732871` (`owner-frontend-live-binding-v1`), worktree
`.claude/worktrees/owner-frontend-live-binding`. Uncommitted.

**Scope.** Observability only. There is no new News Guard logic, authority or
control.

**Not done:**
- no commit, deploy or restart;
- no watchdog change;
- no Binance access;
- no production data read or written;
- no `graphify update`.

**Untouched:** Trade History, Protection Snapshot, Close Trade (parked),
execution, the kernel and `agents/news_guard.py`.

## A. Backend source of truth

| Layer | Code | What it establishes |
|---|---|---|
| Evaluator | `trader/agents/news_guard.py::NewsGuard.check()` | Returns `{"active": bool, "why": str}`. It re-reads its RSS feed at most once per `refresh_minutes` (config: 10) and returns the cached result in between. |
| Publication | `trader/kernel.py::cycle` (≈ line 905) | Every kernel cycle writes `state_kv.news_guard_state = {"active", "why", "ts"}`, where `ts` is the publish time. It runs in every control state. |
| Transitions | `NewsGuard.check()` | Logs control events `news_blackout` / `news_clear` (actor `news_guard`, detail `{"hits": [≤5 headlines]}`), only when the armed state flips. |
| Effect | `trader/engine/orchestrator.py::decide` | When `news.get("active")`: `threshold += 0.08` and `net *= 0.75`. |
| Prior readers (unchanged) | GraphQL `news_guard` field; legacy `server.py` risk card | Both default missing or unreadable state to **not active** — the unsafe default this package avoids. |

Verified facts that shape the model:

1. **News Guard does not block entries.** When armed it dampens them: the entry threshold rises by 0.08 and conviction is multiplied by 0.75. The indicator says "ARMED — entries are dampened, not blocked". It never says BLOCKING, because the backend does not block. A test pins the orchestrator lines so a change there fails the test.
2. **NewsGuard fails open.** A feed error publishes `active: false` with `why: "fetch error: …"`, and a disabled guard publishes `active: false, why: "disabled"`. So `active: false` alone does **not** mean clear.
3. **`ts` is the publish time, not the evaluation time.** The evaluation can be up to `refresh_minutes` older. The report and UI call it "Published by kernel".
4. **No expiry exists.** Neither an expiry nor an affected-symbols field exists. Severity exists only inside the reason text and the recorded headline tags.

## B. Exact state model

The owner API (`trader/dashboard/owner_api.py::read_news_guard`) adds a
`news_guard` section to `GET /owner-api/v1/overview`. It uses the existing
`section()` pattern: the value, or `null` plus `errors.news_guard`.

**Strict parse.** The value must be JSON with exactly the keys `active`
(bool), `why` (str) and `ts` (a parseable time). Anything else is
`news_guard_state_malformed`, and an absent or empty value is
`news_guard_state_missing`.

`status` maps only the exact reason strings `news_guard.py` produces:

| status | condition |
|---|---|
| `ARMED` | `active: true` |
| `CLEAR` | `active: false` and `why` is `feed quiet` or `N impact headlines (M severe)` (below threshold) |
| `DISABLED` | `active: false`, `why == "disabled"` |
| `FAIL_OPEN` | `active: false`, `why` starts with `fetch error` |
| `UNRECOGNIZED` | `active: false`, any other reason |

**Other fields in the section:**
- `why` (≤200 chars);
- `published_at`, `age_s`;
- `freshness`: `fresh`; `stale` when older than `NEWS_GUARD_STALE_S` = 300 s (the kernel republishes every cycle; same bound as equity); or `invalid` when more than 5 s in the future;
- `transition`: the latest `news_blackout`/`news_clear` event among the last 2000 control events, reported **only if it agrees** with the current `active`; otherwise `null`, meaning "not recorded";
- `effect`, `source`.

A pytest runs the **real** `NewsGuard.check()` with a patched feed for each case: severe arm, two-headline arm, quiet, one impact headline, fetch error and disabled. Each result goes through the kernel's exact publish format and must map to the expected status.

**Frontend** (`adapters/live.ts::mapNewsGuard`). It re-validates the section
strictly. Any off-contract value becomes `news_guard_contract_violation`, is
shown as UNAVAILABLE, and is never read as CLEAR. Checks include:
- `active` must equal `status == ARMED`;
- the transition must agree with `active`;
- `published_at` must parse;
- `stale_after_s` must be a number.

It computes a browser-clock `expiresAt` from `published_at + stale_after_s`,
applying the server clock offset, the same way the Protection Snapshot does.

**Displayed labels** (`components/NewsGuard.tsx::newsGuardPresentation`):

| Shown | When |
|---|---|
| **CLEAR** (mint) | fresh, unexpired `CLEAR` — the only CLEAR |
| **ARMED** (rose) | fresh, unexpired `ARMED`: "Armed: \<why\>. Entries are dampened, not blocked." |
| **DISABLED** (amber) | fresh `DISABLED`: "no news protection is applied" |
| **FAIL-OPEN** (amber) | fresh `FAIL_OPEN`: "feed check failed … not armed. News risk is unknown." |
| **UNKNOWN** (amber) | `UNRECOGNIZED`, or `freshness: invalid` (future time) |
| **STALE** (amber) | backend `stale`, or local expiry passed: "Last reported \<status\> at …; current state is unknown" |
| **UNAVAILABLE** (amber) | missing / malformed / contract violation / not reported, with the reason code |

## C. Frontend placement

- **Primary: Overview, LIVE only.** The indicator is the first row of the existing owner status strip. It replaces the old placeholder text "News guard: unavailable". It shows:
  - the badge and a one-line answer (armed? why? current?);
  - "read-only";
  - a collapsed **News Guard evidence** disclosure with: publish time and freshness, the kernel's reason, the last recorded arm/clear event and its headlines, "Until: no expiry is recorded", the effect while armed, and the source.
- **Not added to Operations or Live System.** Neither has a natural slot for it in V2.1, and the brief asked not to clutter every route.
- **DEMO:** the strip renders only in LIVE, so the demo build shows nothing and needs no fixture.

## D. Missing / stale semantics

- **Missing, malformed, off-contract, fail-open, disabled, unrecognized and future-dated states are never shown as CLEAR.** Missing, malformed and off-contract states show as UNAVAILABLE with the reason; the other four show as their own labels (see B).
- **STALE.** The backend reports STALE after 300 s without republication. The browser also expires the reading on its own clock at the same bound, so a failed poll cannot keep an old CLEAR current.
- **Failed refresh with data on screen.** The indicator adds "Latest refresh failed; this is the reading from \<read time\>".
- **Backend unavailable with no data.** No guard reading is rendered at all.
- **Session expiry.** The app signs out and clears the page, so no guard reading remains.
- **Late responses.** A superseded overview response never replaces a newer one (React Query cancellation, covered by a unit test).
- **Reload.** Each reload re-reads the backend. Nothing is stored locally.

## E. Proof no control authority was added

- **Backend.** `read_news_guard` only reads `state_kv` and `control_events`. Tests check:
  - `news_guard_state` and the control-event count are unchanged after reads;
  - no route whose path mentions "news" accepts anything but GET/HEAD;
  - the GraphQL mutation list has no "news" field.

  No kernel, NewsGuard or orchestrator code changed.
- **Frontend.** The indicator has no button, input, select, textarea, form or click handler. Tests check:
  - no mutation requests and no off-origin requests while the page is used;
  - the source file contains no `fetch(`, `mutation`, `onClick`, `<button` or `<form`;
  - the live adapter has no news mutation.

## F. Tests / results

| Suite | Result |
|---|---|
| `npm run typecheck` | pass |
| `npx vitest run` (all) | **104 passed** (11 files); new `tests/newsGuard.test.tsx`: 25 |
| `npx playwright test` (all) | **92 passed**; new `tests/browser/news-guard.spec.ts`: 12; `v21.spec.ts` placeholder assertion updated to the real CLEAR |
| pytest owner / dashboard / trade-history / protection / news-related set (incl. new `tests/test_owner_news_guard.py`, 11) | **685 passed** |

**New pytest `tests/test_owner_news_guard.py`:**
- real NewsGuard outputs mapped to status (6 cases);
- the fixture publishes CLEAR, and the overview then reports no errors;
- missing, empty, malformed (9 variants), stale, future and unrecognized states are never CLEAR;
- the transition is reported only when it agrees with the current state;
- the read is read-only, with no mutation or POST route;
- the effect text matches the orchestrator.

**New unit tests (`newsGuard.test.tsx`):**
- ARMED mapping with its transition;
- missing reason code, and a section that is not reported;
- 8 contract violations, each shown as UNAVAILABLE;
- presentation: fresh CLEAR is the only CLEAR; ARMED reads dampened, not blocked; fail-open, disabled, unrecognized, backend-stale and future-dated readings are never CLEAR; local expiry; missing/malformed readings show their reason;
- Overview: ARMED with evidence and no controls; missing state shown as UNAVAILABLE; a failed refresh labels the old reading, which then goes STALE on the local clock; session expiry shows no guard; a late superseded response never replaces a newer one;
- the live adapter and indicator import no fixture and contain no control path.

**New browser tests (`news-guard.spec.ts`, LIVE build + real FastAPI app):**
- CLEAR, read-only, with no mutation or off-origin request;
- ARMED, with reason, recorded transition, headlines and effect;
- missing, malformed, fail-open, stale and unrecognized states, none shown as CLEAR;
- backend 500 renders no reading;
- a failed refresh labels the older reading;
- reload follows kernel state (ARMED → CLEAR → UNAVAILABLE);
- session expiry clears the reading;
- the LIVE JS bundle contains no fixture marker. Negative control: the marker is present in the DEMO build and absent from the LIVE `dist`.

**Test fixture (test-only).** `seed()` publishes `news_guard_state` the way
the kernel does: CLEAR in `normal`, 900 s old in `stale`, absent in `missing`.
A new `/__test__/state` key `news_guard` can delete the state, write raw text,
or write a structured state with an optional transition event.

**Evidence directory.** The full Playwright run rewrites
`frontend/evidence/`, which holds your uncommitted work. It was copied first
and restored byte-for-byte afterwards (`diff -rq` clean).

## G. Exact changed files

New:
- `frontend/src/components/NewsGuard.tsx`
- `frontend/tests/newsGuard.test.tsx`
- `frontend/tests/browser/news-guard.spec.ts`
- `tests/test_owner_news_guard.py`
- `docs/superpowers/reports/2026-09-29-news-guard-indicator-r1.md`

Modified:
- `trader/dashboard/owner_api.py`: `NEWS_GUARD_STALE_S`, `NEWS_GUARD_EVENT_WINDOW`, `read_news_guard`, and the `news_guard` overview section
- `frontend/src/adapters/contracts.ts`: `NewsGuard`, `NewsGuardStatus`, and `LiveOverview.newsGuard` / `newsGuardError`
- `frontend/src/adapters/live.ts`: `mapNewsGuard`, wired into `mapOverview`
- `frontend/src/views/Overview.tsx`: the indicator replaces the "News guard: unavailable" placeholder in the owner status strip
- `frontend/src/live.css`: `.news-guard`
- `frontend/tests/browser/v21.spec.ts`: one assertion now expects the real CLEAR instead of the old placeholder
- `tests/owner_frontend_fixture.py`: `set_news_guard`, `news_guard_state`; `seed` publishes the state
- `tests/owner_frontend_server.py`: `news_guard` test hook
- `docs/superpowers/reports/2026-09-29-owner-close-trade-ui-r1.md`: the TODO is replaced by Astra's two idempotency findings (documentation only)

## H. Remaining limitations (not blockers)

1. **The evaluation time is not published.** The kernel publishes `ts` at publication, so a reading can be up to `refresh_minutes` (10) older than its `published_at`. Publishing NewsGuard's own `checked` time would need a kernel change, which was out of scope.
2. **"Since" is best-effort.** NewsGuard logs an arm/clear event only on a flip within one kernel process. After a restart, the first arm is logged again and a restart into CLEAR logs nothing. The UI therefore labels the field "last recorded arm/clear event" and hides it when it disagrees with the current state. The lookup scans only the last 2000 control events; `control_events` has no index on `event`.
3. **The reason mapping is coupled to text.** The status mapping depends on the exact `why` strings in `news_guard.py`. A pytest drives the real NewsGuard for every branch, so a wording change fails the test instead of silently turning into CLEAR; an unknown string reads UNKNOWN.
4. **Other readers still default to "not active".** The legacy GraphQL `news_guard` field and the legacy dashboard risk card still treat missing or unreadable state as not active. They are unchanged because they are outside this package, and the React frontend does not use them.

## I. PACKAGE_STATUS

**READY_FOR_REVIEW**
