# Stage1 data provenance/PIT R1 blocker fix

Package: `LUFFY-STAGE1-DATA-PROVENANCE-PIT-BLOCKER-FIX-R1`.
Implementation: PASS. MI-2 and MI-3 are IMPLEMENTED / awaiting independent re-review. The R1 independent review remains BLOCKED; this implementation does not independently close either gap. MI-6/MI-7 remain open. Next: `LUFFY-STAGE1-DATA-PROVENANCE-PIT-INDEPENDENT-REVIEW-R2` (`READ_ONLY_INDEPENDENT_REVIEW`).

Work remained on `luffy-stage1-data-provenance-pit-r1` under `/mnt/luffy-data/luffy/production`, on the current uncommitted base. No commit, deployment, startup, live venue call, Scheme-D, MI-6/MI-7 implementation or authority change occurred. Claude CLI execution probe returned “Not logged in”; implementation proceeded in the authorized session.

## Future universe cache

The existing Universe cache now has an exact selection result receipt alongside its existing payload and refresh timestamp. The receipt binds membership, retrieval ID, observation/availability clocks, source/content/result identities, selection version/configuration, canonical volume receipts and required listing receipts. `_last_scan` remains refresh scheduling state; it does not grant receipt eligibility. No second cache subsystem was added.

Failed refresh never changes the result or its timestamp. Earlier cuts return only explicitly configured majors; unavailable dynamic membership is excluded, not asserted to be a historically empty venue universe. Missing ticker responses do not manufacture empty results. A future result stays stored and remains eligible at its original later cut. Successful refresh also refuses to replace a later result at an earlier clock or publish a result before its required component receipts.

Kernel normal scan selection detaches membership receipts. Collector forwards them to Attention capture, which excludes missing/future membership and associated candles. Capture preserves the source result identity and receipt without backdating it; normal Attention Store persistence retains the membership envelope. Explicit strategy includes and configuration majors remain separately labelled selection sources. Snapshot lineage includes eligible membership receipts. Attention's decision architecture is unchanged.

## Basis provenance and real path

The tests execute actual `basis → _basis_between → _klines → _get`, substituting only `requests.get`. A call trace checks production function execution and both HTTP legs. Deterministic transport supplies real-shaped kline records and independent controlled receipt clocks. Socket/DNS guards see zero attempts; passing depends on derivation/PIT assertions, not denial or an empty response.

Basis raw provenance retains both source endpoints, canonical `binanceusdm:futures` and `binance:spot` identities, actual provider close-event stamps, local receipt/availability clocks, request/content/revision identities, exact raw records and derivation version. Derived availability cannot precede either leg or the derivation clock. An unproven/invalid leg cannot yield VALID basis evidence. No provider publication timestamp is invented.

Actual record_all and backfill/basis_history persist the evidence in the existing append-only market revision ledger. A later perp correction changes the basis revision, links supersedes, and leaves earlier knowledge unchanged. Reopening the temporary store and per-cut feature alignment reconstruct old and new revisions only after their availability. Individual input revisions are retained inside each derived receipt; no parallel input store was added.

## Verification

Evidence directory: `docs/superpowers/evidence/stage1-data-provenance-pit-blocker-fix-r1/`.

- `focused-tests.txt`: 663 passed in 121.30s; prior 649-case campaign plus the then-current 14 blocker cases. Zero network and production-store attempts.
- `blocker-tests.txt`: final 64 passed in 3.83s; all 15 final blocker cases plus all 49 prior Stage1 acceptance cases. Covers the final successful-refresh clock-reversal fence added after the broad run. Zero network and production-store attempts.
- `authority-world-tests.txt`: 94 passed in 265.21s; Stage1 entry authority, universal strategy authority and Stage7 replay/World closure. Zero network and production-store attempts.
- Five new negative controls detect scan timestamp rewriting, the original preserve-alts/restamp membership leak, dropping both input constraints, substituting event time and dropping the spot constraint. Assertions fail for receipt identity or actual earlier-cut membership/basis leakage. Prior four Stage1 PIT mutants also remain passing in the acceptance campaign.
- `preliminary-regression.txt` is superseded: eight fixture-compatibility failures caused by a missing-universe Kernel fixture in the first Attention hook implementation. The hook was corrected and the complete broad suite rerun successfully. It is not accepted closure evidence.
- `caller-audit.json` records the narrow universe/Kernel/Attention/basis/WorldModel/Opportunity/replay/backtest inspection. No remaining live-capable PIT bypass found in that scope.
- `graphify-update.txt`: required code-only AST update; no semantic extraction/API cost.
- `files-changed.json`: exact package-local file manifest, including generated graph artifacts; excludes unrelated pre-existing workspace changes.

Prior quality, revisions, general PIT, HTF/partial/aggregate bars, latest/as-of separation, feature lineage, persistence and authority tests were preserved. Tests were not weakened. Control files select independent R2 re-review, with no recovery/monitoring advancement. Evidence is offline implementation safety, not independent closure or live venue/runtime readiness.
