# WRLD-06 canonical reconciliation

Original condition: Exact-cut, future, multiple-revision and restart queries select the correct applied revision; immutable base claims remain unchanged.

Result: CLOSED at architecture/offline engineering scope. No implementation changed.

- The retained real WorldModel application test applies revisions at T-1, T and T+1; exact WorldModel cuts select .7, .8/revision 2 and .9. A later application remains visible to latest but cannot affect WorldModel/Attention at T.
- Journal reopen preserves historical revision selection; frozen governed-state replay reproduces original Attention rows after newer learning. Injected future applied state into earlier replay raises ValueError.
- WRLD-04 tests retain model JSON, embedded observations, base values/qualities and both evidence polarities across two real applications, revision history, archival reconstruction and restart; UNKNOWN/null is not rewritten by confidence.
- Fresh adjacent Attention and Portfolio tests qualify learned state at the original cut, preserve selection after reopen and frozen replay, and refuse future/tampered projections. Existing DATA-05 inclusive availability-cut semantics and learning applied_at qualification are reused; no new clock model.

Retained validation: f353b9f pinned proof: 43 passed in 12.38s (2 WorldModel temporal, 8 WRLD-04 immutable/history, 33 DATA-05 cases). Exact retained source and test bytes match f353b9f; no unnecessary repeat of that passing run.

Fresh validation: 2 adjacent historical Attention/Portfolio future-revision/restart/replay cases passed in 5.10s from tests/test_final_audit_attention_allocation_time.py.

OUT-03 remains BLOCKED only on its direct OUT-01 dependency; DATA-03 is CLOSED and WRLD-06 is now CLOSED. WRLD-04 engineering closure is pinned; its separate canonical mapping remains pending. No successor selected.

Exact sources, dependencies, limits and control validation are in reconciliation.json and control-validation.json.

OUT-03 dependency summaries in the canonical row and ledger/STATE/NEXT/bundle map now retain the original intake blocker list as historical and show only OUT-01 as currently unmet. OUT-03 status, conditions and original evidence files are preserved. WRLD-04 canonical mapping is separately pending; its supplied engineering prerequisite remains verified.
