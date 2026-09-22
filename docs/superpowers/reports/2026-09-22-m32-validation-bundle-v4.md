# M3.2 Scheme-D validation bundle v4

Authority: [September 20 pre-RNG correction](../specs/2026-09-20-m32-scheme-d-actual-statistic-validation-protocol-pre-rng-correction.md).

The v4 implementation supersedes v3 without editing any frozen v1/v2/v3
artifact or the frozen pre-RNG truth package. This is an implementation and
deterministic verification deliverable, not validation evidence or execution
authorization. All changes remain uncommitted.

## Preparation exception: owner review required

The preparation session did **not** remain entirely RNG-free. A delegated draft
test, `test_run_cell_dispatch_spy_all_ten_p1_p3_cells`, mistakenly wrapped the real
`SeedRegistry.generator` instead of replacing it with a stub. Its passing pytest
entry proves that the test initialized 40 `Generator` objects, 40 `PCG64` objects,
and 40 `SeedSequence` objects: four streams for one world index in each of the
ten P1/P3 cells. The registry used the frozen phase-20 seed coordinates.

The generation, injection, and receipt callbacks were deterministic spies. They
did not request random variates from these generators. No stochastic validation
worlds or validation results were produced; no cloud execution was launched.
This does not excuse the RNG-construction boundary violation.

The parent replaced the registry with an inert `RegistryStub`, added constructor
guards to test subprocesses, and verified both the corrected dispatch test and a
negative guard test. The draft-test hash, log hash, offending spy, observed test
result, exact coordinates, scope, and remediation are preserved in the manifested
[`preparation_audit.json`](../../../scripts/m32_scheme_d_validation_bundle_v4_20260922/artifacts/preparation_audit.json).

The manifest explicitly sets
`no_rng_no_seed_generation_no_worlds_no_search_no_gate2=false` and
`preparation_audit.owner_review_required=true`. Its status is
`PRE_RUN_BUNDLE_REQUIRES_PREPARATION_REVIEW_NOT_AUTHORIZED_TO_RUN`.
Passing final checks must not be interpreted as erasing this exception.

## Implemented correction

- P1/P3 cells have `kind="perturbed_null"`; effect-table role is `calibration`.
  Generation still uses N1 and scenarios 101/103 with the existing phase-20
  world/stream coordinates. `run_cell()` dispatches injection by the presence
  of a scenario, independently of acceptance role.
- `POWER_RULES` and `FDR_SCENARIOS` contain only 102, 104, 105, and 106.
- P1/P3 use non-refused worlds for `P(any BH rejection)`: point estimate at most
  0.05 **and** two-sided Wilson 95% upper bound at most 0.06. Their result fields
  `fdp_count`, `fdp_rate`, `type_one_rate`, `mean_fdp`, and `fdp_wilson_upper`
  describe the same false-positive event. `fdp_count` counts worlds with any
  rejection, not the number of rejected hypotheses. World FDP is `1{R > 0}`.
  There is no power, mixed-null bootstrap-FDR, or marginal-calibration gate.
- P2/P4/P5/P6 retain the original power and mixed-null FDR gates; the config now
  explicitly records P2's retained Wilson-lower threshold of 0.80.
- N0–N3 retain Type-I and marginal calibration; P7 remains descriptive with the
  original refusal blocker. Refused worlds never receive non-rejection credit.
- The design remains 55 cells and 75,000 worlds. All 260,000 phase-10/20 world
  stream identities remain unchanged. Removing P1/P3 bootstrap gates leaves 20
  used phase-30 bootstrap tuples instead of 30, for 260,020 total used tuples.
  No retained tuple is renumbered or reseeded.

The generator, injection implementation, FastMetric, truth tables, RNG
construction, BH, refusal logic, and effect sizes are unchanged. A structural
comparison verified 15 protected runner function/class definitions against v3,
excluding docstrings. A before/after SHA-256 inventory confirmed all 149 tracked
files across v1/v2/v3 and both pre-RNG truth directories were unchanged.

## Verification

- Final preflight: **PASS, 35/35 checks**, zero RNG-construction attempts, zero
  worlds generated from RNG, zero seed tuples materialized by preflight.
- Final regression suite: **38 passed in 197.10 seconds** (25 v4 tests and 13
  frozen-bundle/truth regressions).
- Independent v4 verifier: **PASS**, including an identical fresh preflight
  replay, 147,456 deterministic equivalence comparisons across 96 fixtures with
  zero mismatches, all 16 manifested bundle files and 14 pinned inputs, and the
  frozen truth-package binding. Its `whole_preparation_session_rng_free=false`
  records the exception separately from the current verifier's RNG-free run.
- The full historical FastMetric equivalence suite was not replayed: it uses
  randomized fixtures. Its frozen hashes/receipts remain pinned; the current
  preflight and independent verifier use deterministic live equivalence probes.
- Graphify was refreshed using `graphify update .` (AST only, no LLM/API calls).
  Its generated graph, report, HTML, labels, caches, manifest and dated backup
  changed. Community names were retained or derived from hubs; no semantic
  relabeling was requested.

Commands:

```bash
OPENBLAS_NUM_THREADS=1 ./venv/bin/python scripts/m32_scheme_d_validation_bundle_v4_20260922/runner/preflight.py --out scripts/m32_scheme_d_validation_bundle_v4_20260922/artifacts/preflight_report.json
OPENBLAS_NUM_THREADS=1 ./venv/bin/python scripts/m32_scheme_d_validation_bundle_v4_20260922/build_bundle.py --manifest
OPENBLAS_NUM_THREADS=1 ./venv/bin/python -m pytest -q tests/test_m32_validation_bundle_v4.py tests/test_m32_validation_bundle.py tests/test_m32_validation_bundle_v2.py tests/test_m32_validation_bundle_v3.py tests/test_m32_pre_rng_freeze.py
OPENBLAS_NUM_THREADS=1 ./venv/bin/python scripts/m32_scheme_d_validation_bundle_v4_20260922/verify_bundle.py
```

Tests cover role/gate membership, all ten P1/P3 dispatch paths using inert
registry stubs and fixed seed-coordinate assertions, false-positive/FDP events,
zero/excessive rejections, the Wilson boundary, empty/all-refused cells, unchanged
ordinary-null and mixed-null gates, P7 refusal blocking, frozen artifact hashes,
fresh RNG-free preflight replay, constructor guards, corruption, missing required
v4 checks, and authorization mismatch. Test replays write to temporary paths;
they do not rewrite the canonical preflight artifact.

## Files and hashes

New bundle: `scripts/m32_scheme_d_validation_bundle_v4_20260922/`:

- `runner/{scheme_d_runner,acceptance,preflight}.py`
- `build_bundle.py`, `verify_bundle.py`, `requirements.lock`
- `artifacts/{config,membership_table,effect_assignment_table,loadings,covariance_psd,environment_lock,equivalence_receipt,compute_projection,preflight_report,preparation_audit,verification_result}.json`
- `BUNDLE_MANIFEST.json`

Also added `tests/test_m32_validation_bundle_v4.py` and this report; updated
`docs/NEXT_SESSION.md` and the generated Graphify outputs.

Bundle SHA-256 is the SHA-256 of `BUNDLE_MANIFEST.json`:

```text
a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023
```

Superseded v3 SHA-256 (unchanged):
`eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01`.
Frozen truth-package SHA-256 (unchanged):
`a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb`.

## Remaining blockers

The preparation exception requires owner review. V4 has no execution
authorization and has not been committed/frozen for a run. The existing shard
layer, qualification, benchmark and pilot artifacts remain bound to v3. The
shard layer also dispatches injection using `kind == "power"`; a separate v4
integration must correct that dispatch before distributed execution. These
downstream artifacts and authorizations were not changed or reused for v4.

No search, Gate 2, referee/handoff, risk, demo, or live-system changes were made.
