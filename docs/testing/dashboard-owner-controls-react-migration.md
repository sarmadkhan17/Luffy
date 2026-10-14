# Dashboard owner-control test migration

The former `trader/dashboard/web/index.html` embedded the Owner Controls JavaScript. Commit `8385e9deb882ba11e13f4faa952f1ba3d2b22842` removed that 1,751-line legacy page and switched the Dashboard to the React application in `frontend/`. The repository build creates `frontend/dist`; it does not generate the retired HTML path.

Five W2/W3 test cases became uncollectable because their module-level setup extracted JavaScript from that removed page:

| Legacy check | Behavior protected | Current React coverage |
| --- | --- | --- |
| W2 late answer | Completing an older request must not erase a newer unresolved request for the same action. | `frontend/tests/live.test.tsx`: per-id completion removes only the older ID and retains the newer one. The browser test for unknown outcomes also confirms retry reuses the same ID and issue time. |
| W2 storage failure | If durable browser storage fails, retain the pending ID in page memory and warn the owner. | `frontend/tests/live.test.tsx`: render Operations, force `localStorage.setItem` to fail, verify warning and unresolved in-memory ID. |
| W2 negative control: action-wide deletion | Detect code that clears every same-action request after one response. | The obsolete mutant edited the deleted inline JavaScript. Its core invariant is now asserted directly against the current per-ID store. |
| W2 negative control: lost-memory fallback | Detect code that drops an ID after storage failure. | Covered by the React Operations storage-failure test above. The old mutant was implementation-specific. |
| W3 cross-tab | One tab's completed action must not erase another tab's unresolved request; unresolved IDs survive reload and non-final `IN_PROGRESS`. | Current storage has one key per request ID. React unit coverage verifies independent action IDs and per-ID deletion; browser tests verify retry after unknown outcome and retention of `IN_PROGRESS` across route exit/re-entry. |

The five cases are removed only from the retired page test blocks in `tests/test_owner_interface_r3.py` and `tests/test_owner_interface_r4.py`; all unrelated ownership, recovery and service tests in r3-r6 remain. Negative controls tied to mutating obsolete inline JavaScript were retired after their underlying invariants were moved to current React tests.

Separately, `tests/test_exit_ab_shadow_insert_columns.py` also failed collection in a bare candidate bundle because its three frozen exit-geometry protocol inputs are preserved historical evidence outside the bundle. That is unrelated to the Dashboard migration; the final gate stages the original checksum-verified evidence in the disposable checkout and runs the test without modifying source evidence.
