# Gate-2 studentized calibration handoff — 2026-09-16

Independent review, revision and the full offline calibration are complete.
The studentized revision fails calibration; gate 2 remains blocked. Original failure evidence is unchanged.
Read `2026-09-16-gate2-studentized-calibration.md` for measured results and
`2026-09-16-gate2-independent-review.md` for the separate review record.

Candidate `55243573515adc3b` retains the recorded handoff status
`well_formed_untested` / `waiting_for_evidence`; no ledger read or status write
occurred. Thesis SHA256:
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`.

New files: `scripts/gate2_calibrate_studentized.py`,
`tests/test_gate2_calibrate_studentized.py`, protocol
`../specs/2026-09-16-gate2-studentized-calibration.md`, and artifact directory
`../artifacts/2026-09-16-gate2-studentized-calibration/`.

The fresh seed 20260917, source hashes, unchanged scenario grid, 30-day blocks,
12/60/20 floors, B=1431 fixed-history draws and N=14170 null/stream replicates
were frozen before simulation. Selected stream families use their own frozen
levels and finite draw cap. All planned cells completed, with no recovery,
post-result revisions or diagnostic rule selection. The primary agent
implemented and tested; a fresh independent agent reviewed before revision,
before calibration and during result verification. No Luna/Claude attribution
is claimed.

Validation: **124 tests passed**. Verified 21 new manifest entries,
4 frozen source hashes, all per-cell/aggregate matches,
and 45 preservation hashes covering original artifacts, original
calibration sources/docs, thesis v1/v2 and both /tmp dossier directories.

Remaining blockers: validity under persistent calendar dependence, an explicit
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
length. The next statistical design must address dependence, rather than
assuming that studentization resolves it.

Durable integrity and preservation evidence:
`2026-09-16-gate2-studentized-verification.json`.
