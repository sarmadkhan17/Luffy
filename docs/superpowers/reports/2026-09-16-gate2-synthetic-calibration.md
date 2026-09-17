# Gate-2 synthetic calibration — 2026-09-16

**The proposed 30-day bootstrap fails calibration. Keep gate 2 blocked.**
This is evidence about an experimental statistic on generated populations,
not evidence about candidate `55243573515adc3b`. Its frozen thesis remains
`well_formed_untested` / `waiting_for_evidence`.

## Deliverables and verification

- Harness: `scripts/gate2_calibrate_synthetic.py`.
- Tests: `tests/test_gate2_calibrate_synthetic.py`.
- Protocol: `docs/superpowers/specs/2026-09-16-gate2-synthetic-calibration.md`.
- Frozen plan, source hashes, per-cell counts/bounds and manifest:
  `docs/superpowers/artifacts/2026-09-16-gate2-synthetic-calibration/`.
- Command: `./venv/bin/python scripts/gate2_calibrate_synthetic.py --output-dir NEW_DIRECTORY`.
- Verification: **96 passed**, running the new test module plus
  `test_thesis_validate_frozen.py` and `test_candidate_dossier.py`.

No held-out data, database, broad logs, network, current ledger or config were
read. No runtime, trading behavior, referee/handoff settings, ledger rows,
`reason_passed` or admission were changed. Prior workspace edits, v1/v2 thesis
artifacts and both /tmp dossier directories were preserved.

## Measured false-pass behavior

Each null cell uses **14,170 independent populations**, B=1431, seed 20260916
with separate deterministic RNG namespaces. The budget comes from the exact
zero-failure upper-bound requirement, not a fixed small smoke count. All
reported bounds use error=.05/1000 per endpoint, conservatively providing
at least 95% simultaneous coverage for the experiment's reported endpoints.

The table uses the second declared history: H1 at row 11, H2 at row 12, earlier
rejection [10]. Source LORD++ levels are **.0026695373 / .0006986996**. Both
are frozen before the family. These are simulated histories, not current
ledger levels. The artifact also reports history [1] at rows 2/3.

| Null fixture | H1 failures | H2 failures | Joint failures | Joint lower–upper bound | Joint assessment |
|---|---:|---:|---:|---|---|
| iid | 57 | 29 | 5 | .000027–.001444 | inconclusive |
| shared factor | 69 | 31 | 12 | .000204–.002251 | inconclusive |
| fractional-memory stress | 131 | 57 | 30 | .000933–.004054 | inflated |
| regime switching | 868 | 548 | 490 | .028915–.040936 | inflated |
| heavy tail | 66 | 25 | 12 | .000204–.002251 | inconclusive |
| heavy tail, 20/40 arms | 101 | 55 | 55 | .002170–.006339 | inflated |
| missingness | 71 | 33 | 13 | .000237–.002359 | inconclusive |
| hold/block=.1 | 90 | 38 | 14 | .000271–.002465 | inconclusive |
| hold/block=.5 | 101 | 57 | 27 | .000797–.003767 | inflated |
| hold/block=1 | 104 | 45 | 21 | .000539–.003181 | inconclusive |
| hold/block=2 | 88 | 42 | 18 | .000419–.002879 | inconclusive |

Every H2 fixture has a lower bound above its alpha; H1 does too except iid.
For example, shared-factor H1's lower bound is .002916 > .002670, and H2's
is .000979 > .000699. “Inconclusive” joint results are not successes.
The all-null joint comparison uses the smaller marginal level, never the
product of levels. Regime switching gives 490/14170 = **3.458%** joint false
passes, versus a .06987% comparison level.

General null fixtures span 24 blocks; the explicit arm-floor fixture spans
12. Missingness and long-duration fixtures were designed with enough calendar
span to avoid measuring only automatic refusal. Per-prediction eligibility
and arm/unknown/incomplete counts are included in each cell. These are
unconditional decisions including legitimate refusals; they do not establish
a conditional-on-eligibility theorem.

## Power and minimum detectable effects

Each point uses 1000 repetitions at the second history's levels. Both synthetic
labels receive the stated coefficient; correlation makes each true marginal
mean contrast approximately **1.7048 × coefficient**. Thus a coefficient of
.5 R corresponds to about .8524 R contrast in this iid model. This is not an
effect estimate for the frozen thesis.

| Blocks / trades per block | Smallest tested coefficient with joint lower power ≥80% | Joint power at that coefficient | Lower bound |
|---|---|---|---|
| 12 / 5 | not demonstrated through 4 R | — | — |
| 12 / 20 | .5 R | .994 | .978 |
| 24 / 5 | .5 R | .849 | .801 |
| 24 / 20 | .5 R | 1.000 | .990 |

At 24/20 and .25 R, observed joint power is .847 but its lower bound is .799,
so that point does not meet the predeclared criterion. At 12/5 even 4 R reaches
only .297 joint power; random arm/block eligibility constrains this sparse
case. MDE is a tested-grid bound, not interpolation. High power from an
inflated test does not make the design acceptable.

## Selection, family error and limits

Five-candidate simulated streams reuse the same population, freeze both H
levels before evaluation, and allow rewards only for later families. All-null
and dependent H1-only/H2-only populations are included. Stream replicates,
not dependent rows or selected candidates, are the binomial sampling units.

The gate-1 selector is a **Gaussian max-of-three coupling proxy**. It does not
execute production gate-1 consistency or common rotation. In 14,170 all-null
streams, only **five** selected a family; none falsely admitted. The
unconditional upper bound is .0006987, but the selection-conditioned upper
bound is **.8620**. This does not close the selection blocker.

The H1-only stream produced **339/14170** false admissions (2.392%), bounded
by **1.924%–2.931%**. Those are descriptive probabilities; no approved stream
family-error target exists. Full stream results include FDR estimates and
Hoeffding upper bounds, without treating LORD++ as a dependency guarantee.
The H2-only stream produced **441/14170** false admissions (3.112%), bounded
by **2.576%–3.718%**.

Separate 30/60/90-day long-memory diagnostics are recorded in `diagnostics.json`.
They do not replace the charged 30-day rule or authorize choosing a favorable
block length after seeing results. Joint failures were 2/1000, 2/1000 and
5/1000 respectively; all bounds are inconclusive. Four and eight repetitions
disagreed with the 30-day decisions. These diagnostics establish no remedy.

The synthetic labels are not the frozen DSL predicates; the generated R values
are not engine trades. Fractional memory uses a finite 4096-day approximation.
The statistic is calibrated at its finite Monte Carlo grid, not at an exact
permutation distribution or arbitrary B. Missingness preserves the generated
null and does not cover every outcome-dependent missing-data mechanism.
Production selection, actual engine/DSL partition correctness, prospective
evidence access, and a human-approved family-error target remain unresolved.

## Review and handoff

Astra's review identified structurally ineligible initial stress fixtures;
the frozen run uses longer spans. It also prompted dependent partial-null
families, within-stream population reuse and explicit long-memory approximation.
The primary agent performed implementation/testing; Claude was not available
as a callable model. Luna was assigned independent progress assessment but
remained pending initialization after the environment transition. No completed
independent Luna assessment is claimed; that review remains outstanding.

A permissions/environment transition terminated the initial process after
null/power cells completed. Missing cells were computed with unchanged frozen
seeds and hash-verified source; completed outputs were not overwritten.
`recovery.json` records that interruption. This was synthetic computation,
not a retry of a held-out test. All **22 artifact hashes**, frozen source
hashes, and the existing v2 thesis artifact manifest verified after completion.
Results SHA256:
`e90293def4fb4665df24739936606332f251715b6d00cc218004bacdf5d39609`.
Manifest SHA256:
`dede8bfc3e17a9343e553b72ea3410ace95963f434440952172958bcc104dda3`.

Read `2026-09-16-gate2-calibration-handoff.md` for continuation. Any revised
inference needs a new frozen offline protocol and retained failure evidence.
**No wiring, held-out read, reservation, settlement or admission is approved.**
