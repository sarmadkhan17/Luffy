# LUFFY-STAGE8-OWNER-OS-CLOSURE-R1

Result: **PASS — Stage8 TESTED / BUILD COMPLETE**, uncommitted and undeployed.
SDD v3.2 remains the authority. Branch: `luffy-stage8-owner-os-closure-r1`.
HEAD remains `f5e5156b8a294cd53db277e8d46cb30645611a61`.

The existing authenticated Owner Interface, OwnerService/control gateway,
React/Vite frontend, journal, Research Bank and vault are reused. No second
API, control executor, conversation authority, knowledge database or frontend
framework was introduced. Normal trading still requires zero LLM calls.

## Functional closure

`GET /owner-api/v1/query/{kind}?identity=...` is the finite owner-query contract.
Kinds: decision, trade, research, portfolio, world, strategies, cost, approvals,
learning, capability. Omitted identity returns a bounded catalog; exact identity
reads the relevant stored object. SQL, shell, provider retrieval and arbitrary
paths are not request inputs or runtime conversation tools.

Decision/trade readers reuse existing forensic contracts and follow Stage7's
immutable registrations, captured dependencies, exact action/risk references,
and retained outcome records. Observation/data, WorldModel/context, strategy,
portfolio, economics, intent, Risk, execution and learning are returned only
when recorded. Unavailable dependencies and original non-consulted stages remain
explicit. The API never substitutes a registry's current strategy for an absent
historical version or a query configuration for historical decision config.

Research queries reuse the strict Bank chain verifier, expose exact linked
internal evidence values, and use the existing quantitative experiment/referee
readers. External Bank objects retain their original question, routing, source
metadata, retrieval receipts, collection result, limitations, next questions
and resource measurements. Empty/inconclusive objects and unresolved questions
remain visible. Supporting/contradicting classification and predictive validity
are never invented. Descriptive external evidence remains research-only.

Portfolio queries read the existing `data/runtime-portfolio.db` evaluations
through a read-only connection and exact cut identity. Retained candidate sets,
proposal, cash/rejection, economics/uncertainty/capacity/factor records, TradeIntents
and Risk results are available in the cut. Historical cuts are not current
venue exposure. Journal positions are explicitly journal-booked. Missing factor,
capacity, economic or venue evidence stays UNKNOWN/UNAVAILABLE.

World/learning queries expose original captured contexts, exact retained target
revisions/application identities and application receipts. This does not rewrite
historical WorldModel snapshots. Cost queries expose measured run telemetry,
external retrieval cost/resource records and the existing daily/purpose token
usage file when present. Tokens do not establish monetary cost; total LLM/data/
operating cost remains NOT_ESTABLISHED without authoritative receipts.

Record envelopes carry identity, content SHA256, source, stored event/availability
time when available, version/decision/cycle references, verification status and
explicit missing evidence. RECORDED is not VERIFIED or CURRENT; journal is not
venue. Oversized record details are explicitly unavailable beyond the 1 MiB
per-record bound, retaining the content hash. Catalogs are partial windows, not
complete history. Exact identities should be used for detailed interrogation.

LUFFY conversation advertises only `owner_query` to its bounded agent. It returns
actual consulted records separately from exact textual mentions. Factual synthesis
without consulted evidence or an exact consulted-record citation is refused as
UNAVAILABLE/UNKNOWN. Historical prompts and compatibility `ask` use the same
query authority. No control/approval/spend function is a runtime LLM tool.

## Needs You and approvals

Overview has a minimal addition within the existing M4 attention area; full
objects, evidence and structured decisions live in LUFFY. The six classes are:
first-live StrategyVersion, capability beyond approved scope, paid spend,
owner risk/survival boundary, serious recovery and production-code/architecture.
Ordinary trade/skip, Attention, research, approved sizing/allocation and Governor
lifecycle actions are not added as approval classes.

The existing first-live Factory requests and their spec, validation, probation
and installation verifiers are reused. Approval records first-live eligibility;
it never activates the version. Other explicit producer proposals live in the
Owner Interface's governance ledger, with retained context/source bindings,
configuration hash, immutable proposal identity, clock/validity window and exact
owner receipt. This is an explicit local producer seam; chat/browser cannot
create proposals or alter their contexts. Code/artifact bindings rehash registered
repository paths; scope/cost/safety/evidence/config changes, expiration and
supersession invalidate prior approval. A changed proposal needs a new exact
request; stale historical receipts are retained.

`POST /owner-api/v1/needs-you/decision` accepts only item identity, binding hash,
APPROVED/REJECTED, owner request identity and issue time. It forwards through the
existing authenticated dashboard gateway. OwnerService resolves the actor,
reserves/claims the request, records its audit and replays its durable result.
The dashboard never writes approval decisions locally. Generic decisions grant
no automatic spend, capability, configuration, risk or code mutation. Recovery
items link to the existing guarded resume path; a generic approval cannot release
recovery. Prior Factory approvals lacking the new full configuration envelope
are UNKNOWN rather than retroactively declared fully bound.

## Knowledge and Live System

The four lenses reuse the existing vault, read contracts and note `source_paths`.
Evidence nodes expose exact record provenance. Code nodes expose read-only content
hash/path metadata from registered repository code references. Missing links stay
UNLINKED; no inferred causal/trace edges are added. Timeline sorts stored times
and labels event chronology separately from file modification time. Original note
relations remain declared note relations, not independently validated evidence.
Graphify is used only for engineering navigation/update; it is not imported or
consulted by the runtime Knowledge implementation.

The nine-route navigation is retained. Existing declared-vs-observed Live System
contracts and absent-event-stream handling are preserved. Components are not
made active by a declared edge. LIVE failures do not load fixtures. Session/auth
failure clears private query/transcript/UI state under the existing auth gate.

## Preservation

The initial file hashes/copies were recorded under `/tmp/luffy-stage8-start` for
comparison only; no reset, clean, stash, restoration or wholesale replacement was
performed. Owner-modified knowledge notes and all existing M4 capture files remain
unchanged. The original local frontend files were classified KEEP or EXTEND;
none was superseded. Surgical extensions are Overview, LUFFY and GraphView.
The original M4 styling, assets, route composition and controls remain present.
A generated ignored daily note changed independently during this session and was
left untouched. See [preservation.json](../evidence/stage8-owner-os-closure-r1/preservation.json).

## Validation

All providers/venues/LLMs used in tests are doubles. Journals are temporary.
No production journal writes, venue calls, real orders, deployment, restart,
activation, paid spend, Scheme-D, commit or push were performed.

| Proof | Evidence |
|---|---|
| A/B grounded entry and no-trade / retained chain | `test_stage8_owner_os.py`, owner forensic read regressions |
| C research question/source/evidence/result/cost and negative result | Stage8 external and standard Bank query cases, existing Research reads |
| D portfolio candidate/cut/proposal/intent/Risk | Stage8 exact portfolio evaluation case |
| E absent evidence never becomes zero or invented facts | ten parametrized query kinds and ungrounded-chat refusal |
| F/G six approval classes and hash/context staleness | scope/cost/safety/config/expiry/supersession, code hash and exact Factory cases |
| H/I request replay, actor/audit and no conversational controls | OwnerService proofs, existing gateway tests, LIVE browser chat tests |
| J LIVE fixture isolation | LIVE adapter and browser failure tests |
| K/L/M Knowledge Evidence, chronology and Code | Stage8 backend, Vitest and browser cases |
| N declared-vs-observed runtime | existing owner system tests and LIVE browser tests |
| O auth failure clears private state | LIVE session expiry/logout tests |
| P Risk/Execution authority unchanged | unchanged engine sources, boundary suites, narrowed Factory/Bank caller guards |

Final checks:

- Focused backend regression: **380 passed** (`pytest.txt`).
- Final query regression after adding exact research evidence/experiment reads:
  **132 passed** (`pytest-query-final.txt`).
- Latest Stage8 proof: **35 passed** (`pytest-stage8-final.txt`).
- Research Bank read-only owner boundary guard: **1 passed** (`pytest-bank-boundary.txt`).
- Corrected owner/Factory/chat/API batch: **114 passed** (`pytest-final.txt`).
- Frontend typecheck and LIVE build: **PASS** (`build.txt`).
- Focused Vitest: **80 passed / 6 files** (`vitest.txt`).
- Focused Playwright: **19 passed** (`playwright.txt`); final UI repeat:
  **3 passed** (`playwright-final-ui.txt`). The latest JSON records that 3-case repeat.
- AST-only `graphify update .`: completed without semantic extraction/LLM spend;
  generated engineering artifacts are enumerated in the changed-file manifest.
- Targeted `git diff --check` and STATE/NEXT YAML parsing: PASS.

Earlier attempts are retained honestly: the browser first used an absent redirected
home executable path; rerun used the installed Chromium path. Legacy r3-r6 test
collection references `trader/dashboard/web/index.html`, which is absent in the
required base HEAD. It was not restored. A combined pytest run reached the 1,024
FD limit; the final process used 4,096. One old Factory caller guard required an
explicit Stage8 read/owner-approval allowance; it now also forbids owner-side
create/install/activation/Governor calls. No functional failure remains in the
final focused checks. These are offline functional proofs, not production proof,
owner visual approval or a long-duration resource/leak claim.

## Control plane and remaining limitations

Stage3 is TESTED / build_complete true / formal closure COMPLETE, based on the
owner-provided completed read-only R2 PASS. Historical R1 audits/reports remain
unchanged. Its external worker is undeployed, operational/evidence maturity is
partial, predictive transformation remains policy-blocked, and usefulness is
unproven. Stage8 is TESTED / build_complete true / formal closure COMPLETE.

MISSING_IMPLEMENTATION: **NONE for Stage8**.

POLICY_BLOCKED:

- External descriptive-to-predictive transformation and wider supported source/
  capability scope still require registered policy/protocol; no gate was bypassed.
- First-real-money eligibility, paid adoption, risk-boundary and production-code
  adoption remain exact owner/workflow decisions; this package applies none.
- Real cost/capacity/economic calibration and Stage5-7 learned/predictive rules
  remain existing policy/evidence work.

REAL_EVIDENCE_BLOCKED:

- Complete latest production decision/execution/outcome/learning chains are sparse
  or absent; current venue exposure/protection is not verified by these readers.
- External worker usefulness, broad WorldModel relationships, calibrated capacity/
  economics and total authoritative operating monetary cost are unproven.
- Missing semantic trace links are shown unlinked; test fixtures are not real edge.

DEPLOYMENT_BLOCKED:

- This working tree is uncommitted/undeployed; the latest reviewed architecture and
  external research worker are not deployed by this package.
- No production service binding, restart or production UI resource observation was
  performed. Local Vite artifacts are test/review artifacts, not deployment.

SECURITY_MATURITY_BLOCKED:

- Accepted authenticated/typed same-UID V1 boundary remains; separate OS users,
  process privilege and least-privilege per-service secrets are not proven.
- Existing copied dashboard cookies remain valid until expiry; server-side session
  revocation is not established. Browser private state clearing is tested.

OWNER_VISUAL_APPROVAL_PENDING:

- Overview/LUFFY Needs You additions, Knowledge Evidence/Timeline/Code additions,
  and any routes without separately recorded owner visual approval remain pending.

NEXT recommends **LUFFY-STAGE1-SAFETY-TRUTH-CLOSURE-R1**, a read-only mandatory
safety/truth build-closure check, before considering Stage9. Stage1 remains
build_complete false; listed protection/accounting/eligibility/recovery limitations
appear maturity-related, but the mandatory exit including failure/restart and
backup/restore must be classified from actual evidence. Stage2 is already build
complete with evidence maturity gaps. This package neither invents a new Stage1
architecture gap nor assumes that multi-market expansion is ready.

SAFE_TO_REVIEW: **YES**. Exact authored and generated file paths are in
[files-changed.json](../evidence/stage8-owner-os-closure-r1/files-changed.json).
