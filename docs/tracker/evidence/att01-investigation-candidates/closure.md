# ATT-01 — closure (architecture/offline scope)

Engineering commits (reused, not re-run for implementation changes): `c4c08c1`, `84b3080`.
Dependencies: DATA-06 CLOSED (`c4bcf05`), WRLD-03 CLOSED (`1a2424b`).

## Closing-condition proof
"A supported normal event creates ranked investigation candidates with instrument, reasons, urgency/cost and
source identity."
- `trader/attention_admission.py` `candidates(receipt)`: pure projection from a verified ATT-04 admission receipt;
  each candidate carries symbol, rank, reasons, urgency, bounded cost, source identity and `INVESTIGATE_ONLY`
  authority. ATT-04 receipt format and replay unchanged.
- `trader/kernel.py` `_candidate_snapshot_for`: the normal deep-scan path adds the candidate as
  `admission_context['investigation']` (additive key; legacy keys unchanged), cached per receipt id.
- End-to-end: `test_normal_cycle_delivers_same_candidates_to_downstream_consumer` — cycle -> persisted/replayed
  receipt -> candidates -> orchestrator consumer sees identical identity/rank/reasons/urgency/cost/source.

## Failure/regression proof
Zero eligible candidates is a valid current result distinct from refused/incomplete capture; no receipt is
unavailable (not zero); incomplete observation refuses; warm-up is not negative salience; tampered receipt yields
no candidates; candidates grant no trade or size authority; deterministic replay reproduces selection and reasons.

## Tests (run at reconciliation, 2026-10-06)
- `tests/test_att01_candidates.py`: 9 passed.
- 143 passed across test_att01_candidates, test_attention_kernel_wiring, test_attention_selection_persistence,
  test_cognition_attention.

## Limits
Offline engineering evidence only: no kernel/dashboard launch, provider call or live venue read; no runtime or
calibration claim. Ranking weights are not newly calibrated (that is ATT-03). Scope is ATT-01 only; ATT-02
(capture completion at scale) and ATT-03 (warm-up/calibrated ranking) remain EVIDENCE_TO_MAP.
