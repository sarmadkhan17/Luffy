# Gate-2 calibration handoff — 2026-09-16

Candidate `55243573515adc3b` remains `well_formed_untested` /
`waiting_for_evidence`. Thesis SHA256:
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`.
Certification of its existing artifact is context integrity only.

Implemented only `scripts/gate2_calibrate_synthetic.py`, with tests in
`tests/test_gate2_calibrate_synthetic.py`. Protocol:
`docs/superpowers/specs/2026-09-16-gate2-synthetic-calibration.md`.
Results: `docs/superpowers/artifacts/2026-09-16-gate2-synthetic-calibration/`.
Read the accompanying calibration report before planning further work.

The existing 30-day unstudentized bootstrap is **not approved**: completed
synthetic fixtures demonstrate false-pass inflation at source-derived LORD++
levels, including joint inflation with regime switching, fractional-memory
stress, and the heavy-tail 20-trade-arm floor. Power does not cure invalid
size. Do not silently select a diagnostic block length or retune this run.

The selector is explicitly a Gaussian coupling proxy, not production gate 1.
All-null selection was rare: only five independent streams selected anything,
so zero conditional false admissions is inconclusive (upper bound about .862).
Dependent partial-null streams and same-population candidate reuse are included.
There is no approved stream family-error target and no universal validity claim.

Focused verification: 96 tests passed across synthetic harness,
`test_thesis_validate_frozen.py`, and `test_candidate_dossier.py`.

Roles: Astra reviewed architecture and statistical correctness. The primary
agent implemented and tested; no Claude agent was available in the tool roster.
Luna was assigned independent progress assessment. Do not attribute primary-agent
implementation to Claude. Luna remained pending initialization after the
environment transition; its independent assessment remains outstanding.

An execution-environment permissions change interrupted the run after its null
and power cells finished. Completed cells were preserved; missing cells were
computed from the same frozen plan and per-cell seeds after verifying source
hashes. The artifact records recovery separately. No data snapshot was involved.
The run is complete: all 22 artifact hashes, frozen source hashes, and the
original v2 thesis artifact manifest verified.

Preserved all prior workspace changes, original v1/v2 thesis artifacts and both
/tmp dossier directories. No held-out data, database table, broad log, network,
current ledger, config or operational runtime state was read. No runtime,
trading, referee/handoff, ledger, reason_passed or admission change was made.

Next design work, only if requested: revise inference offline with a new frozen
protocol, retaining this failed calibration unchanged; define the family-error
target and plan exact synthetic production gate-1 integration separately.
Calibration and explicit human approval remain prerequisites to any wiring,
held-out read, reservation/settlement or admission.
