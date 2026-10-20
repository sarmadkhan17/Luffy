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
