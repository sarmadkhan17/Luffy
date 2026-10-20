# Final consolidated integration (offline) — 2026-10-20

CANONICAL_BASE: ef9fca8 (release); CI integration head f334f44 is an ancestor.
BASE_OF_STACK: dc9653e = b024d37 (Review 07 combined candidate, includes completed operational-data lifecycle 147fae0,
D5/D6/D7, quantitative D1-D4) + D7 signal-exit correction.

Cherry-picked in order (-x trailers retained), linear, no merge commits:
938f1dd tooling guidance | 4f8cfae R08 | c8cdd80, 4deca5c R09 | 1cfbddd, 753b45e R10 | c1571fb, b31d80e R11 | b6b2af9 R12.
R11's branch carries R09+R10 (already picked once; not repeated). R12's parent was R10 (not R11) and applies cleanly.

Resolution: Review 10 edited scripts/validate_population.py and trader/brain/tv.py, which Review 07 (ef53aa3) deleted.
The deletion removes the retire-on-duplicate authority Review 10 was bounding, so the deletion is kept and the two
Review 10 tests that only exercised validate_population are retired (tests/test_review10_legacy_boundaries.py keeps
its independent legacy-drawdown test).

Deferred, not fixed (Review 11, hash-pinned frozen research; re-freeze needs separate owner approval):
continuation permutation (m32_search.py:329-332), persistence sign handling (same path),
qualify_v4 "v3 untouched" completeness. Review 12: test_m32_qualification.py still needs OPENBLAS_NUM_THREADS=1
(hash-pinned manifest); luffy-ci's sandbox exports it.

Source identity: D6 adds fill_timing to engine_digest and retrospective source hashes, so a NEW evaluation of the same
inputs yields a new identity. Stored qualification records self-verify by their own content hash and are not recomputed
against engine_digest; no stored record is rewritten.

Focused verification: focused-tests.list / focused-tests.log (961 passed, 2 skipped); frontend tsc clean, vitest 206/206.
No production database, service, venue or provider was touched. referee=false, handoff=false unchanged.

## CI submission 20261020T125150Z-1e60 (tip 060ff91): BLOCKED, EARLY_STOP — not promoted

Findings were reproduced locally; three causes were pre-existing in the Review 07 candidate (b024d37 / dc9653e) and
are corrected here without weakening any assertion:
1. tests/test_data03_consumer_cuts.py: lifecycle stores learning_source_blobs payloads as shared journal-detail
   pointers; the test now resolves them with capture.payload (exact round-trip assertions unchanged).
2. tests/test_final_audit_research_snapshot.py: D1-D4 raised the shared `population` fixture from 2100 to 3000 bars
   (c9cfe7a, to reach the required null offsets); the stale `len==2100` is now 3000 (passes at base ef9fca8, fails from c9cfe7a).
3. trader/learning/application.py: prepare_captured's Reader shim lacked create_function, so capture.read's
   E.install(db) failed on read-only shadow connections; the shim now registers the verifying SQL function on the
   underlying connection (no write capability added).
4. tests/test_rp03_kernel_handoff.py (7 tests, failing at b024d37): the lifecycle archives completed experiment
   ledgers into the Bank and retires the working quantitative.db. Tests now read archived evidence via completed_reader
   and, for working-file fault scenarios (locked/corrupt/alias/binding), materialize the working ledger from the archive
   exactly (crash window before unlink). The former "working ledger missing" case becomes an explicit archive-fallback test.

Not a source defect: tests/test_res08_governed_bridge.py (2 tests, 59-68 s each) exceed CI's default 60 s per-test alarm
after the 3000-bar fixture change. 20/20 pass at per-test 900 s (luffy-ci rerun, rerun-res08.json) and locally.
policy.file_limits already carries evidence-backed entries for the sibling rp03/predictive-bridge/rp05 files; adding one
for res08 is a CI policy change that needs owner authorization and was NOT made.
