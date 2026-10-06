# QNT-03 / QNT-04 / QNT-05 canonical reconciliation

Control-plane only; no implementation changed (trader/ and tests/ clean vs HEAD). Each row's exact closing condition was checked against the engineering commit and re-run tests (83 passed via luffy-pytest; root free 8.2G before/after).

- **QNT-03 CLOSED** (a801cfd, 26a26d3): dependence-corrected + common-rotation admission, contributor lineage, WRLD-07 exact-cut context. Source hashes match the engineering validation.json pins.
- **QNT-04 CLOSED** (093187b): frozen held-out cut, spent-look guard, replay-safe split.
- **QNT-05 CLOSED** (ceba1bd): registered persistent LORD++ budget, caller-alpha validation, restart-safe sequence.

All three are architecture/offline closure on synthetic fixtures; no real-data calibration or held-out look is claimed.

Preserved limits / baselines not caused by these changes:
- tests/test_research_job.py: 2 failures reproduce with QNT-04 stashed and at HEAD.
- test_analyst.py / test_analyst_tv_and_vault.py: 5 failures reproduce on pinned 45e825d.
- QNT-05: crashing looks below referee_max_attempts (3) retry uncharged — existing design.

Status counts: CLOSED 52, EVIDENCE_TO_MAP 84, DEFERRED 7, AWAITING_EVIDENCE 9, AWAITING_OWNER 14, BLOCKED 8, OPEN 4 (=178). The manifest's previous counts were stale against the YAML (47/90/7 vs 49/87/8) and are now aligned.

QNT-08: dependencies QNT-03, QNT-05, QNT-06 all CLOSED, so it is dependency-eligible. Row unchanged and unselected. Active RES-05 work_package/current_active_item untouched. Details in reconciliation.json.
