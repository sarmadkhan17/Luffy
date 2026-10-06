# [SOL-2][WRLD-06] Historical learned revisions

**CLOSED — architecture/offline engineering.** WRLD-04 engineering closure
`91ae9f4` and DATA-05 CLOSED are the satisfied prerequisites. Work is limited
to WRLD-06 evidence mapping and fresh offline verification. No demonstrated
implementation gap, code change, new test, clock model, or migration.
Tracker, STATE, NEXT and manifest are untouched.

## Existing selection contract

`WorldModel.effective_claims` passes the immutable model's `as_of_ms` through
`_overlay` to `targets.observation/read`; no wall-clock latest read is used by
that historical query. `consumers.world_claims` routes the normal descriptive
claim consumer through the same API. Context identity is exact; no nearby
asset/horizon/evidence/dimension fallback is allowed.

`targets.read` joins immutable target revisions to their application receipts,
filters APPLIED receipts with integer `applied_at <= as_of_ms`, and selects the
highest eligible revision. `temporal_state` verifies state and application
hashes, matching context/target/resulting state, APPLIED status and the
application time against that cut. A later outcome's earlier event timestamp
cannot make a later applied learning revision eligible.

`WorldQueryReader` captures the selected full state/application proof, and
frozen replay validates it with the same `temporal_state` rather than reading
mutable latest learning. `attention.evaluate_snapshot` and cognition Attention
use this reader. Missing eligible learning leaves the immutable base confidence.

This reuses the existing DATA-05 semantics: explicit UTC epoch-millisecond
cut, inclusive availability boundary, later revisions retained without
rewriting earlier truth, and no latest fallback for historical evidence. Market
receipts keep their established availability/observation gates; learning keeps
its already-established application availability (`applied_at`). No second
clock model or new availability field is introduced.

## Closing-condition proof

| Requirement | Verified existing test and result |
| --- | --- |
| Exact cut selects correct applied revision | `test_exact_cut_multiple_revisions_capture_replay_and_reopen`: applications at T-1 and T select confidence .8 / revision 2 at T. |
| Later learning cannot affect earlier as_of | `test_future_applied_revision_cannot_change_historical_world`: application at T+86400000 is visible to latest, but WorldModel at T stays at base .6 before and after Journal reopen and normal Attention query. |
| Multiple revisions select correctly | The exact-cut test adds revision 3 at T+1; queries at T-1, T and T+1 select .7, .8 and .9 respectively. |
| Restart/replay reproduces selection | The exact-cut test reopens Journal after revision 3 and still selects .8 at T. Frozen governed-state replay produces the same Attention rows as the original capture, despite newer learning. WRLD-04 archive tests separately reopen exact WorldModelRecord/WorldHistory bytes and IDs. |
| Immutable base observations unchanged | `test_learned_confidence_appends_linked_history_without_rewriting_claim` (both parameters): two real applications and Journal reopen leave the entire model JSON, base claim value/quality, evidence refs and embedded observations unchanged. UNKNOWN/null also remains UNKNOWN/null under a .9 overlay. `test_confidence_and_validity_versions_keep_old_contradictions_and_facts` retains identical observations across archived versions. |
| Future revision refused | The exact-cut test injects genuinely applied T+1 governed state into replay at T and expects `ValueError` matching future/temporal. Ordinary historical reads exclude that revision; replay refuses supplied future evidence. |
| DATA-05 semantics reused | All 33 `test_data05_revision_truth.py` cases pass, including inclusive boundaries, predecessor cuts, multiple retained revisions, restart, future/index rebasing refusal and historical lineage negative controls. No DATA-05 implementation changed. |

## Fresh validation

```text
./venv/bin/python -m pytest tests/test_final_audit_learning_time.py tests/test_wrld04_claim_history.py tests/test_data05_revision_truth.py -q
43 passed in 12.38s
```

Counts: 2 WorldModel temporal tests, 8 WRLD-04 immutable/history cases and
33 DATA-05 revision cases. All use isolated offline fixtures. Governed learning
rules and claim emission in these tests are test-only; no production emission,
provider/venue call, deployment or execution-realism proof is inferred.
The prior WRLD-04 broader passing suite is not counted again.
`git diff --check` passes for this report.

The inspected source and test bytes all match dependency commit `91ae9f4`.
SHA-256 pins for this mapping:

| File | SHA-256 |
| --- | --- |
| `trader/learning/targets.py` | `c0c59f471b3601a4cdf3c101880eebb022ab0a5ea1b3db38832c8904b9d313f1` |
| `trader/learning/consumers.py` | `2b4e1bb2a373120eba0d24773a997d5417845a397e58d5033f2e3b61ef3b254b` |
| `trader/world/model.py` | `8163c0ec6467c44eab25590b0a1aaf3d9d95ed00a4e8ddd96faa041033408574` |
| `trader/world/replay.py` | `57ec8b2649564cfd41d4d5c9896f7a26c7705bf4d0e3c2661b032c85227a4588` |
| `trader/observability/world_producer.py` | `63a2febf04b3a732146618ea2667f5931f95da61975d74c8fa092d7cdcd97aae` |
| `trader/observability/attention.py` | `212aa27db4dce3d4257fce28474df24133c34c6e6f39576616d1129a1c5395e3` |
| `trader/cognition/attention.py` | `4b7245c48e0765fdb46f561120a36fd2d5c4fab85abdcfed457fc3acaa1f7edd` |
| `trader/data/market_provenance.py` | `4bca12b4d16a57b7a582fcd27c48254fc59c1e23a0ab0848554b5d4118d3e584` |
| `tests/test_final_audit_learning_time.py` | `d2626bf24c233125bdf26dc7fbbb9adc2b9fc8ac9d8eb9a4f6d27e24872fd361` |
| `tests/test_wrld04_claim_history.py` | `01f9f9368fab998c6b81cc9502d4c092b5e930ef3a078134c14b5c624f69044c` |
| `tests/test_data05_revision_truth.py` | `4ae7452393436b1fab1abbc4572816b9dfd4f55868dadad025b64c1e90714776` |

No source changed, so no code graph refresh or semantic extraction is needed.

## OUT-03 dependency impact (read-only)

OUT-03 is **CLOSER, NOT ELIGIBLE**. WRLD-06 now satisfies its engineering
prerequisite, and the current canonical DATA-03 row is CLOSED. The current
OUT-01 row remains BLOCKED on DEC-04 and full normal execution delivery
mapping. That dependency is not bypassed or closed here. OUT-03 retains its
own exact replay/identity acceptance proof and tracker reconciliation remains
separate; its older summary listing DATA-03 as unmet is not authoritative over
the current DATA-03 row.
