# Review 12 — engineering (offline engineering and repository reference)

REVIEW_12_SCOPE: 1,194 files, `primary_subsystem=engineering` in sol1-system-architecture subsystem-files.csv (sha256 90f73dd3…d995, inventory rev 8d57e8f). Excludes Review 11 and Reviews 01–10.
SOURCE_REVISION: 753b45e27d45c21e890c535afcbea31f5177afd4 (Review 10 final), branch opus/subsystem-review-12-20261020, isolated worktree.
FILES_REVIEWED: 1,194 (register: files.json / files.csv). 1,193 present; 1 fixture already removed upstream; 1 file changed since scan (tests/test_child_runner.py, Reviews 07–10).
KEEP_SIMPLIFY_MERGE_REMOVE: KEEP 1,187 · SIMPLIFY 2 (proposed) · MERGE 0 · REMOVE 5 (4 scripts removed; 1 fixture already absent).

ACTUAL_ENGINEERING_CAPABILITIES (source contract):
- Lean test selection already exists (pytest.ini core of 74 paths + validation/lean-ci-manifest.json extended/archive tiers); the 20 owned test modules sit in core/ext/archive tiers; m32/gate2 tests are archive tier.
- memory_lookup.py (AGENTS/CLAUDE-documented), read_prod_fee.py (tested), compact_journal/prove_journal_representation (journal chain), stage1 recovery guard/evidence and RSS measure (report-referenced evidence tools) — all live or evidence-reproduction.
- Frozen evidence trees (docs/tracker/evidence 574, docs/superpowers/evidence 329) and 180 dated reports: provenance, KEEP unedited. Tracker yaml/md/xlsx and STATE/NEXT are bound by bundle-manifest.json hashes.

CONFIRMED_DEFECTS:
1. tests/test_m32_host_qualification_v4.py (quick-host test) and tests/test_m32_qualification.py call `Q.main` in-process; the environment gate needs OPENBLAS_NUM_THREADS=1 before numpy loads, so both fail (rc 2, environment_gate FAIL) unless the caller exports it. Evidence: owned-tests.txt (2 failed, 359 passed); passes with the env var. Fixed for the v4 file only.
2. Root ROADMAP.md, REQUIREMENTS.md, LUFFY_Official_Solution_Design_v3.md read as authority ("Single build authority", "FROZEN") but STATE.yaml known_stale_documents declares them historical. Banners added.

INTENTIONAL_ARCHITECTURAL_BOUNDARIES: m32 v1–v4 bundle/shard test versions (each pins a distinct sealed bundle); stage1_pit_offline_plugin.py and active_setter_inventory.py (needed to reproduce historical evidence/activation baseline; callers.py "DEAD" pytest hooks are plugin hooks, false positive); duplicated evidence files (19 byte-identical copies in 8 groups, e.g. obs02/03/04 collect_prepared.py) are per-run provenance; design/ v2 owner assets.

REDUNDANT_OR_OBSOLETE_TOOLS: scripts/apply_atomic_merge_plan.py, convert_vault_to_atomic.py, review_atomic_duplicates.py, write_atomic_candidates.py — one-shot 2026-09-19 vault→atomic pipeline (/tmp inputs, urllib LLM calls), zero references outside generated graphify-out; knowledge/60 Atomic output retained. Ruff on owned scripts: clean. Whole-repo Ruff shows 871 findings in tests (F811/F401), outside ownership and not touched.

DOCUMENTATION_FINDINGS: see defect 2. Proposed only: docs/NEXT_SESSION.md is called "stable" but is 810 lines of appended session blocks (checklist owns status); design/agents-cockpit (v1, 2.5 MB html) is superseded by v2.

SIMPLIFICATIONS_IMPLEMENTED: removal of 4 scripts; 3 historical banners; test env fix (v4).
FILES_REMOVED_OR_MERGED: the 4 scripts above (recoverable from git history).
MEASURED_COMPLEXITY_REDUCTION: −4 maintained scripts, −~567 lines code, −2 unmanaged provider-calling tools; 2 test failures removed.
TEST_RESULTS: all 20 owned test modules, 361 tests, before: 359 pass / 2 fail (510 s, owned-tests.txt). After: both quick-host tests pass with env var unset (test_m32_host_qualification_v4 fixed; test_m32_qualification passes only with env exported). Ruff on owned scripts clean; callers.py only plugin-hook false positives. No full CI run.
COMMIT_HASHES: see git log on branch (single review commit).

CROSS_REVIEW_DEPENDENCIES: Review 11 (m32): same env fix for tests/test_m32_qualification.py is PROPOSED_ONLY because scripts/m32_scheme_d_qualification_20260922/QUALIFICATION_MANIFEST.json pins that file's sha256 (083b34bc…) and docs reference it; decide with m32 review. 6 archived m32 test versions' retirement depends on Review 11.
OWNER_DECISIONS_REQUIRED:
1. AGENTS.md says "run `graphify update .` after modifying code", but graphify-out (881 files) and graphify-out-full-backup (685 files), ~49 MB, are tracked in git, and the tooling pilot decided on an external per-revision graph. Adopting the pilot (drop tracked graph dirs, change AGENTS.md) is a rollout decision.
2. Whether to retire design/agents-cockpit (v1) and trim docs/NEXT_SESSION.md.
3. Whether to wire the m32 qualification test fix despite the hash-pinned manifest.
DEFERRED_INTEGRATION_DEPENDENCIES: none beyond Review 11; no integration with Reviews 07–11 performed.
REVIEW_12_COMPLETION: COMPLETE for offline review and bounded remediation. No production, provider, service or integration action.
