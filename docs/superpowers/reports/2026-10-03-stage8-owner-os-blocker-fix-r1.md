# Stage8 owner OS blocker fix R1

Package: `LUFFY-STAGE8-OWNER-OS-BLOCKER-FIX-R1`.
Branch: `luffy-stage8-owner-os-closure-r1`.
Base HEAD: `f5e5156b8a294cd53db277e8d46cb30645611a61`.

Result: **FIXED_TESTED_PENDING_INDEPENDENT_REVIEW**. The owner-provided independent R1 review supersedes the prior self-reported Stage8 PASS. Stage8 build completion and architecture freeze remain false; safe-to-commit remains false. Original closure reports and evidence are retained as historical records, not current authority. No independent R2 result is fabricated.

## Four defects and fixes

1. Exact research-question identities are now queried regardless of catalog position. The typed API and LUFFY tool expose `questions_page` and an integer `offset` for traversing all stored questions in pages of 50. This page applies to questions; other evidence retains its existing bounded catalogs. The owner interrogation view exposes Previous/More questions. An offline 51-question reproduction verifies the oldest record by exact API identity, chat dispatch, and catalog page 2.
2. Factory `eligible_for_first_live` now consumes current configuration binding, including the gateway envelope. Factory-native owner decisions also hash configuration into their immutable decision identity. Missing historical configuration proof refuses eligibility instead of silently passing. Needs You and Factory share the same configuration verifier. Doubling risk configuration yields `approved_configuration_changed` at the consumer. The exact entry fence continues to refuse live execution; no activation or order path was enabled. This is a stricter admission predicate, not a Risk policy change.
3. Needs You orders pending/stale actions ahead of completed history across both governance and Factory ledgers, then applies pagination. The response includes full `pending_total`, `total`, and page information. Overview displays the full pending count; LUFFY can traverse later pages. Exact identity lookups remain independent of pagination. A 101-completed-item reproduction verifies the pending first-live request remains first and all 102 records remain reachable. Stale approvals also stay visible.
4. The Research reader now exposes persisted questions with exact identities, hashes, source and recorded/available times, plus pagination. It no longer declares an available store absent. Questions-only responses explicitly mark quantitative results unavailable. The existing frontend route renders question evidence and LUFFY detail links, including an explicit typed partial-response contract when quantitative tables are absent. Empty pages are distinct from missing stores. No layout wholesale replacement was performed.

## Validation

All backend proof uses temporary journals and fixtures. Browser tests use the fixture server and intercepted test responses; they do not prove production evidence or predictive edge.

- Focused pytest: **191 passed in 53.31s** across `test_stage8_owner_os.py`, `test_strategy_factory_handoff.py`, `test_owner_frontend_api.py`, `test_chat_agent.py`, `test_owner_reads.py`, and `test_owner_reads_m2.py`.
- Frontend typecheck: **PASS**.
- Frontend LIVE build: **PASS**.
- Focused Vitest: **30 passed across four files** (`stage8`, `live`, `contracts`, `workspace`). Includes questions-only response validation and malformed-record refusal.
- Focused Playwright: **5 passed in 15.9s** (`stage8.spec.ts`). Includes Overview pending count/LUFFY pagination and truthful persisted Research question rendering.
- Targeted whitespace checks and STATE/NEXT YAML/closure-state checks: **PASS**.
- Graphify update: AST engineering-only, no semantic extraction or API spend. Generated community labels may be refreshed automatically from hubs; no runtime dependency added.

Initial attempts are preserved in this package's evidence directory. Corrections included a test's incorrect chat class name, a stricter refusal assertion now including missing historical configuration proof, an ambiguous UI text assertion, and a browser mock missing required response fields. The browser attempt also exposed the real questions-only frontend contract gap, which was implemented and regression-tested. An older Research test expected the false missing-store message; it now asserts an available empty question store.

## Control plane and boundaries

STATE/NEXT record independent R1 BLOCKED and revoke the prior self-reported completion. The fix package is tested but pending independent closure. Stage3 stays TESTED / build complete / formal COMPLETE with partial operational/evidence maturity. The immediate next package is `LUFFY-STAGE8-INDEPENDENT-REVIEW-R2`. Stage1 safety/truth closure is deferred until Stage8 closes, and Stage9 is not selected.

Existing policy, real-evidence, deployment, same-UID/session security maturity and owner visual approval limitations remain. No commit, push, deploy, production restart, production journal mutation, venue call, order, StrategySpec mutation, activation, paid spend or Scheme-D occurred. Normal trading still requires zero LLM calls. Execution, Risk, Portfolio and Learning sources are unchanged by this package. Factory changes only strengthen owner-approval eligibility. Existing owner frontend/M4/knowledge work is preserved; frontend edits are surgical EXTEND changes.

## Authored/surgically changed files

- `STATE.yaml`
- `NEXT.yaml`
- `trader/owner/queries.py`
- `trader/owner/approvals.py`
- `trader/dashboard/owner_api.py`
- `trader/dashboard/owner_reads.py`
- `trader/chat/tools.py`
- `trader/strategy/factory_handoff.py`
- `frontend/src/components/OwnerEvidence.tsx`
- `frontend/src/views/LiveReads.tsx`
- `frontend/src/adapters/readContracts.ts`
- `tests/test_stage8_owner_os.py`
- `tests/test_strategy_factory_handoff.py`
- `tests/test_owner_reads.py`
- `frontend/tests/stage8.test.tsx`
- `frontend/tests/browser/stage8.spec.ts`
- `docs/superpowers/reports/2026-10-03-stage8-owner-os-blocker-fix-r1.md`

Generated engineering/evidence changes: `graphify-out/` AST output and `docs/superpowers/evidence/stage8-owner-os-blocker-fix-r1/`. Existing unrelated dirty work is not attributed to this fix package.
