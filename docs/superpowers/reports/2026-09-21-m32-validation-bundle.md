# M3.2 Scheme D validation bundle (RNG-free), September 21

Bundle: `scripts/m32_scheme_d_validation_bundle_20260921/`; manifest
`BUNDLE_MANIFEST.json` (bundle SHA-256 = SHA-256 of that file, recorded in
`artifacts/verification_result.json`). Consumes the frozen truth package
(`7279ce0`, package SHA-256 `a100a19c…13eb`) unchanged. No RNG, no seed tuples,
no phase-90 benchmark, no validation world, no search, no Gate 2, no protocol
or truth change.

## Contents (13 manifest-bound files)

`artifacts/`: `config.json`, `membership_table.json` (384 rows),
`effect_assignment_table.json` (P1-P7), `loadings.json`, `covariance_psd.json`,
`environment_lock.json`, `preflight_report.json`; `runner/`: `scheme_d_runner.py`,
`acceptance.py`, `preflight.py`; `requirements.lock`, `build_bundle.py`,
`verify_bundle.py`. The manifest also pins the two protocol documents and the four
hash-pinned sources (`m32_search.py`, `m32_protocol.py`, `m32_correction.py`,
`m32_scheme_d_validation.py`); the two previously untracked section 2 files
(`m32_protocol.py`, `m32_correction.py`) are committed unchanged.

## Results

- **PSD:** C0-C4 factor forms `Sigma = d I + sum w_k s_k s_k^T` (w >= 0, d > 0)
  reproduce all 384 x 384 exact entries and unit variance; exact
  `lambda_min = d` (1, 7/10, 3/10, 3/10, 2/5); numeric eigenvalues and Cholesky
  agree; C4 float loadings match to 1.1e-16.
- **Preflight:** 22/22 checks PASS with all numpy/`random` generator constructors
  patched to raise (0 attempts): metric known answers and bit-identity against the
  pinned harness, exact BH with refusals in m = 384, p-value/tie rule,
  with-replacement draws incl. identity, N0 orbit `(8!)^4 (4!)^4`, 15 invalid
  cases refused 100%, hypothesis-level refusals, seed-map gates and analytic
  tuple counts (260,030), exclusive create, import isolation, static RNG/import
  scan, acceptance evaluators (Wilson, exact Clopper-Pearson, power, blockers),
  world generation/injection logic through a fixed-pattern stub, receipt pipeline.
  Mutation checks show the suite rejects a changed BH constant or floor.
- **Independent verifier:** PASS (re-derives tables, matrices, PSD, null MAD, config
  numbers, environment, git ancestry/tracking, isolation, RNG scan, and reruns
  preflight to an identical report). 12 tamper cases were rejected.

## Owner-review items (config `interpretations_flagged_for_owner_review`)

The protocol leaves these undefined; the bundle fixes them explicitly:
I1 episode = unique row, dependence group = block; I2 floor/worst-case reading;
I3 block < 8 observed labels refuses all 384; I4 degeneracy = constant outcome
column only (zero lift is not refused); I5 order inside the data stream;
I6 marginal calibration counts a refused h(w) as not <= t; I7 exact integer BH;
I8 reference statistic only.

## Limits stated plainly

- **Full run is infeasible as built.** The statistic is the imported production
  `_metric` (no unproven rewrite). A fixture evaluation of all 384 hypotheses
  takes about 7.2 s, i.e. roughly 13,000 CPU-years for 57.6e9 maps (fixture
  timing, not a phase-90 benchmark). Validation mode therefore refuses without a
  measured runtime estimate in the authorization, and an optimized statistic would
  need the section 3 exact-equivalence proof first.
- The RNG paths (`SeedRegistry.generator`, world generation) are exercised only
  through a deterministic pattern stub; real generator behaviour is untested by design.
- `scripts/bench_m32_scheme_d_phase90.py` (untracked) targets the pre-RNG harness,
  which refuses RNG, so it cannot run; the runner's `benchmark` mode replaces it.
- `m32_correction.py` imports the still-untracked `m32_evidence.py`; the runner
  never imports it.

## Authorization record (owner-created, not created here)

`{"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "benchmark",
"bundle_sha256": "<BUNDLE_MANIFEST.json SHA-256>", "authorized_by": "owner"}`;
`validation` additionally needs numeric `runtime_estimate_cpu_hours`.
