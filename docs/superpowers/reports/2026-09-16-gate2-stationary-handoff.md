# Gate-2 stationary calibration handoff — 2026-09-16

Independent design and implementation review, revision and the full offline
calibration are complete. The stationary/HAC revision fails calibration; gate 2 remains blocked. Original failure evidence is unchanged.
Read `2026-09-16-gate2-stationary-calibration.md` for measured results and
`2026-09-16-gate2-stationary-review.md` for the separate review record.

Candidate `55243573515adc3b` retains the recorded handoff status
`well_formed_untested` / `waiting_for_evidence`; no ledger read or status write
occurred. Thesis SHA256:
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`.

New files: `scripts/gate2_calibrate_stationary.py`,
`tests/test_gate2_calibrate_stationary.py`, protocol
`../specs/2026-09-16-gate2-stationary-calibration.md`, and artifact directory
`../artifacts/2026-09-16-gate2-stationary-calibration/`.

The fresh seed 20260918, source hashes, fourteen-scenario grid, original 30-day blocks,
12/60/20 floors, B=1431 fixed-history draws and N=14170 null/stream replicates
were frozen before simulation. Selected stream families use their own frozen
levels and finite draw cap. All planned cells completed, with no recovery,
post-result revisions or diagnostic rule selection. The primary agent
implemented and tested; a fresh independent agent reviewed before revision,
before calibration and during result verification. No Luna/Claude attribution
is claimed.

Validation: **160 tests passed**. Verified 24 new manifest entries,
4 frozen source hashes, all per-cell/aggregate matches,
and 75 preservation hashes covering original artifacts, original
calibration sources/docs, thesis v1/v2 and both /tmp dossier directories.

Remaining blockers: finite-sample marginal validity even under iid outcomes,
validity under persistent calendar dependence, an explicit
human-approved family-error target, exact production gate-1 selection
integration, actual DSL/engine partition validation and evidence access.
Gaussian-proxy stream measurements are descriptive. Calibration failure is
not repaired by iid power or an inconclusive diagnostic result.

No held-out outcomes, database, config, current ledger, broad logs, network or
operational runtime state were read. No trading, runtime, referee/handoff,
reservation/settlement, reason_passed or admission changes. All pre-existing
boundaries and artifacts remain intact; the dependence-corrected gate stop
stands. No prospective clock was started and no admission approval was sought
or granted.

Any further inference revision requires another separately frozen offline
protocol. Calibration and explicit human approval remain prerequisites to
wiring or evidence access. Do not retry this run or promote a diagnostic block
length. The next statistical design must address the measured dependence and
short-history limitations; do not assume finite resampling runs resolve them.

Read `2026-09-16-gate2-inference-requirements.md` before another revision.
It records independently reviewed assumptions and conditional family-error
arguments; it selects no new numerical target or production rule. Integrity
evidence: `2026-09-16-gate2-stationary-verification.json`.
