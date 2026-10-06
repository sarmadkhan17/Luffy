# GUI-01 functional/offline Overview closure

Scope: functional/offline screen verification only, over temporary journals and fake gateways. The real read-only FastAPI Dashboard and built frontend were exercised with Kernel STOPPED. No production Kernel or Dashboard was started, no venue/provider reads were performed, and no activation is authorized. Deployed/runtime identity and VIS-01 owner visual approval are separate, unproven obligations.

Demonstrated gaps fixed:

- Journal realized-today aggregation could silently ignore individual null P&L values. Any missing/non-finite row now makes the value null; a readable empty day remains a measured zero. Source, read time, journal-only quality, known/total coverage and calculation version are explicit. This is journal-booked P&L, not proof of venue net profitability.
- Derived gross exposure lacked independent coverage/version and its equity input time. These are now returned, with journal-open scope and unproven current venue coverage.
- Per-figure evidence and global News/Risk records were discarded or hidden behind generic source labels. Overview retains and exposes exact account, series-point provenance, positions, control, process, heartbeat, News Guard, Risk and Supervisor records. Configured Risk policy remains distinct from observed current assessment. Raw records are explicitly the last response, not fresh observations after disconnect. Source version/coverage absent from legacy evidence remain UNKNOWN rather than invented.
- Needs You offered generic navigation and an approval-ID link with no handler. Each returned item now opens its exact object, bindings, evidence, validity and action context inline. Full-page guarded decisions remain available in LUFFY; no approval executes or activates trading. The historical activity footer now accurately scopes its missing reads instead of claiming no approval store exists. Missing ledgers do not establish no pending actions.
- Overview's optional unrealized estimate could remain current indefinitely in cached quote data and use only a symbol instead of exact trade identity. It now uses per-trade evidence, enforces the quote's age plus browser cache age, clears estimates on failed polling and preserves source/quality evidence. Estimates exclude unproven fees/funding; unsupplied source versions remain UNKNOWN.

Evidence map:

| Overview surface | Source and interpretation |
| --- | --- |
| Account equity | `current_truth.read_account`: original observation time, source status/basis, authoritative flag and freshness; browser expiry retained. Legacy values without provenance remain labelled unproven. |
| Realized today | `journal trades`, UTC day, journal booked, complete/null coverage; `overview-journal-realized.v1`. |
| Exposure | Journal-open entry notionals divided by recorded equity; input observation time and journal position coverage; `overview-entry-exposure.v1`. This does not establish current venue exposure. |
| Chart | Individual equity row provenance retained in exact source record, with source time distinct from row write time; historical hourly sampling scope explicit. |
| Position P&L | Optional venue ticker estimate with exact trade identity and quote age; null on unavailable/expired/disconnected input. Read-only mode supplies no venue enrichment. |
| Current control | Stored control permission, separately observed Kernel lock/process and heartbeat/work evidence; configured permission never implies running work. |
| Global signals | Last reported News Guard and Risk, with exact evidence, assessment times, quality and configured/observed policy separation. Unsupplied global market signals remain UNKNOWN. |
| Needs You | Exact returned item expands inline; missing ledgers explicit, validity time and pending totals scoped to available ledgers. |

Verification artifacts:

- `backend-final.txt`: 70 passing tests (27 focused tests plus 43 historical-activity regressions), including real READ_ONLY_GUI HTTP reads with Kernel STOPPED and disabled provider/chat actions.
- `frontend-tests-final.txt`: 166 passing tests across all 16 frontend test files, including exact quote identity, expiry, null propagation and evidence retention.
- `typecheck.txt`: passing TypeScript check. Browser server startup also builds production/demo/test assets.
- `browser-scope.txt`, `browser-scope-results.json`: 10 passing scoped Overview/OWN-04/Stage8 regressions. `browser-readonly-final.txt`, `browser-readonly-results.json`: 1 passing dedicated real read-only server proof. `browser-live-check-final.txt`: 2 passing adjacent Overview/Owner Interface checks, for 13 browser checks total.
- Desktop/mobile `overview-stopped-*.png` and `readonly-stopped.png`: offline fixture screenshots; no owner approval inferred.
- `backend-tests.txt` and `backend-baseline.txt`: broader run has the same 37 failures on baseline 4a39a74; exact identities compared in `baseline-comparison.json`. Existing ticker/mode/Kernel fixture issues remain outside GUI-01.
- `browser-tests.txt`, `browser-final.txt`: exploratory wider regressions retained, including old unrelated Research expectation reproduced on baseline in `browser-baseline-other-routes.txt`. Intermediate runs also exposed harness ordering/assertion issues; final adjacent checks pass. The source-panel provenance legitimately includes the test server’s source labels: the substitution guard now checks the command deck rather than treating hidden backend evidence as fake data. These are not represented as all-green full browser proof.

Test harness repairs: conversation tests now provide the required QueryClient; Needs You browser mocks install before login to avoid already-cached unmocked reads. Browser executable is supplied explicitly for this environment. Production data, configuration, stops and services were not changed. Unrelated concurrent working-tree changes are preserved.

Tracker impact: only GUI-01 closes at functional/offline scope. Original requirements/dependency edges, future activation requirements and VIS-01 remain unchanged. Canonical YAML, STATE, NEXT, optional Markdown and bundle hashes are synchronized. GUI-02 is the next GUI recommendation for dependency/evidence mapping, unselected; this does not close its prerequisites or authorize live work.
