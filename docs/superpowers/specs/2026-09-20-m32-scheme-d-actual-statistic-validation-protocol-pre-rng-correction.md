# M3.2 Scheme D actual-statistic validation — pre-RNG correction

> **STATUS: DRAFT PRE-RNG CORRECTION ONLY — NOT AUTHORIZED TO RUN.**
>
> This correction is mathematical prespecification. No RNG, simulation,
> optimization, benchmark, validation world, historical search, Gate 2 look,
> referee action, handoff, or demo/live action is authorized. It corrects the
> protocol dated 2026-09-19 before any validation RNG has been spent.

- Date: 2026-09-20
- Corrected protocol:
  `docs/superpowers/specs/2026-09-19-m32-scheme-d-actual-statistic-validation-protocol.md`
- Scope of amendment: P1/P3 classification and gates; admissible pre-RNG
  truth certification; consequent cell counts and overall decision rule.

## 1. Authority and unchanged boundaries

This document supersedes only the conflicting provisions of §§2, 8, 9.3,
11.1–11.3, 12.2, 12.4–12.5, 14 and 17 of the corrected protocol. Every other
provision remains unchanged, including:

- the actual M3.2 statistic and fixed 384-hypothesis family;
- BH step-up at `q = 0.05` with `m = 384` always, including refused
  hypotheses retained in the denominator as explicit `null` p-values;
- Scheme D conditioning, synchronized whole-block permutation, draw budget,
  p-value construction, support floors, refusal rules, integrity stops,
  fixed sampling, complete-all rule and separation of evidence;
- all DGP geometry, correlations C0–C4, effects, scenario index sets,
  magnitudes, seed tuples and frozen environment requirements;
- `research.referee=false` and `research.handoff=false`; and
- every historical-search, Gate 2, held-out, admission, demo and live-system
  boundary.

The labels `P1` and `P3` are retained only for stable scenario and seed-map
identity. They no longer denote power alternatives.

## 2. Corrected scenario roles and cell counts

| Scenario | Corrected role | Cells | Acceptance treatment |
|---|---|---:|---|
| P1 | true-null perturbed calibration | C0–C4 (5) | Type-I/false-positive gates only |
| P2 | single-target persistence power | C0–C4 (5) | existing power and FDR gates |
| P3 | true-null perturbed calibration | C0–C4 (5) | Type-I/false-positive gates only |
| P4 | sparse mixed-family power | C0–C4 (5) | existing power and FDR gates |
| P5 | clustered mixed-family power | C0–C4 (5) | existing power and FDR gates |
| P6 | distributed mixed-family power | C0–C4 (5) | existing power and FDR gates |
| P7 | dense weak-effect sensitivity | C0–C4 (5) | descriptive only, unchanged |

The generated worlds and injection mechanisms are unchanged. The acceptance
partition is corrected to:

- 20 ordinary null cells: N0–N3 × C0–C4;
- 10 perturbed true-null calibration cells: P1/P3 × C0–C4;
- 20 mixed-null acceptance-power/FDR cells: P2/P4/P5/P6 × C0–C4; and
- 5 descriptive sensitivity cells: P7 × C0–C4.

The total remains 55 cells and 75,000 worlds. This arithmetic statement does
not authorize their generation.

## 3. Exact P1 and P3 correction

### 3.1 Truth classification

For every correlation structure C0–C4, all 384 population signed metrics in
P1 and P3 are true nulls.

- P1's OR=3 perturbation of the rows matched by hypothesis 0 does not change
  the categorical statistic's population majority decision and therefore
  does not create a nonzero macro-F1 lift. Persistence outcomes are untouched
  and remain independent of categorical perturbation.
- P3's OR=3 perturbation of the rows matched by hypothesis 320 has the same
  majority-invariance consequence for the categorical statistic.
  Persistence outcomes are again untouched.
- Correlated memberships may propagate the categorical distributional
  perturbation to other memberships, but they do not turn the population
  macro-F1 lift into a nonzero estimand while the required majority-invariance
  proof holds.

The immutable truth tables must consequently mark every P1/P3 hypothesis
`true_null`, with no sign. The proof reference must identify the exact
majority-invariance and outcome-column-independence arguments; an injected-set
label is not a truth classification.

### 3.2 Removed impossible gates

Delete every P1 and P3 requirement from the original §12.4 power table. In
particular, there is no gate on the probability that hypothesis 0 or 320 is a
"true discovery", because neither is a population non-null. A P1/P3 rejection
is never power and never a true discovery.

P2 keeps the original single-target criterion: in each C0–C4 cell, the Wilson
95% lower bound on the probability that injected target 128 is a correct-sign
true discovery must be at least 0.80.

P4–P6 keep their original per-cell power criteria and P7 remains descriptive
only. Their injected sets, effect sizes and intent are unchanged, subject to
the completed truth proofs required by §6 below.

### 3.3 P1/P3 false-positive gates

P1 and P3 are preserved as false-positive/Type-I stress checks under a
structured perturbation whose tested population estimands remain null. For
each of their ten C0–C4 cells, using non-refused worlds:

- the point estimate of `P(any BH rejection)` must be at most 0.05; and
- its two-sided 95% Wilson upper bound must be at most 0.06.

Every BH rejection in P1/P3 is a false discovery, so world FDP is 1 when
there is any rejection and 0 otherwise. These cells are governed by the
Type-I rule above, not by the mixed-null bootstrap-FDR gate. Their per-cell
mean FDP and rejection counts must still be reported as the same underlying
false-positive event.

The existing refusal accounting remains unchanged: refused worlds are
excluded from Type-I estimation, never earn non-rejection credit, and remain
subject to the 1% blocker. Hypothesis-level refusals remain in BH's fixed
`m = 384` denominator.

## 4. Certified numerical interval rule

### 4.1 Narrow permission

Exact symbolic, algebraic, independence, symmetry and monotonicity proofs
remain the default. A rigorously certified numerical interval may be used
only where classification depends on multivariate Gaussian threshold-event
probabilities induced by C1–C4 membership latents (orthant probabilities).
It may not be used to estimate truth from generated worlds, random or
quasi-random integration, simulation, permutation, bootstrap, optimization,
sample statistics or empirical tolerances.

The orthant-probability enclosure may be propagated through exact mixture
weights, exact rational class probabilities, deterministic macro-F1 formulas,
and deterministic monotone median/root brackets. Every operation must use
outward-rounded interval arithmetic. The resulting enclosure must contain the
population signed metric for the frozen generator, including input-rounding
error in thresholds, loadings, covariance entries and effect constants.

### 4.2 Required tolerance and classification rule

For each hypothesis requiring numerical certification, emit a final signed-
metric enclosure `[L, U]` satisfying both:

- certified enclosure radius `(U - L)/2 <= 1e-12`; and
- all intermediate orthant bounds and propagated operations are rigorous
  enough to establish that final radius.

Let the zero-exclusion margin be `delta_truth = 1e-10` in signed-metric units.

- Classify `analytic_non_null`, sign `+1`, only if `L > delta_truth`.
- Classify `analytic_non_null`, sign `-1`, only if `U < -delta_truth`.
- Classify `true_null` only by an exact proof that the estimand equals zero.
  A numerical interval, however narrow, cannot prove a true null.

Thus `delta_truth` is not a numerical-null region and does not convert a
small effect into a null. If a nonzero sign cannot be separated from zero by
the stated margin, the record is unclassifiable.

### 4.3 Certificate artifact

Each interval-classified row must include the exact frozen inputs, reduction
to orthant events, interval method and implementation hash, directed-rounding
mode, working precision, all primitive probability enclosures, the propagated
`[L,U]`, its radius, the zero-exclusion comparison and a replayable
deterministic proof transcript. The check suite must independently verify the
certificate without constructing any RNG.

### 4.4 Fail-closed behavior

A row is `unclassifiable` if any input is not exact or enclosed, any bound is
non-rigorous, the final radius exceeds `1e-12`, the interval intersects
`[-1e-10, 1e-10]`, an asserted exact-zero proof is incomplete, or independent
certificate replay disagrees. One unclassifiable row refuses the entire cell
before RNG. An unclassifiable P2/P4/P5/P6 acceptance cell means this corrected
protocol cannot run as frozen. No fallback point estimate, midpoint, widened
scientific interpretation or nearby-hypothesis substitution is allowed.

## 5. Corrected acceptance accounting

The mixed-null FDR gate applies to P2 and P4–P6 only: 20 cells. Its original
definition, threshold and 10,000-resample world bootstrap are unchanged.
P7 FDR remains descriptive only.

The acceptance-power gate applies to P2 and P4–P6 only: 20 cells. The original
P2, P4, P5 and P6 thresholds are unchanged. P1/P3 have no power gate.

The corrected protocol passes only if:

- all 20 N0–N3 null cells pass the original global-null and marginal-p
  calibration gates;
- all 10 P1/P3 perturbed true-null cells pass §3.3;
- all 20 P2/P4–P6 mixed-null cells pass the original FDR gates;
- all 20 P2/P4–P6 acceptance-power cells pass their unchanged power gates;
- no refusal blocker, pre-RNG truth-table refusal or integrity stop occurs.

C4 remains blocking and has the same weight as every other cell.

## 6. Remaining pre-RNG proof obligations

No P4–P7/C1–C4 cell is eligible for generation until all 384 rows in its
truth table are certified. At minimum, the freeze must discharge:

1. **Categorical rows, P4–P7 × C1–C4.** Reduce correlated threshold
   memberships, sequential overlaps and target-class switches to exact
   formulas whose only numerical primitives are certified Gaussian orthant
   probabilities. Prove the conditional majority decisions, enclose each
   population macro-F1 lift, and classify exact zeros or zero-excluded signed
   non-nulls. This includes non-injected spillovers and C4's negative
   cross-sector dependence.
2. **Persistence rows, P4–P7 × C1–C3.** Supply complete monotone-association
   proofs for every target and spillover, including the sign and strict
   nonzero claim after overlapping shifts and the all-population median term.
   A slogan such as `positive_association_monotone_shift` is not a proof.
3. **Persistence rows, P4–P6 × C4.** Prove the asserted positive same-sector
   and negative opposite-sector signs, strictness, and absence of cancellation
   for every hypothesis under each scenario's target layout.
4. **Persistence rows, P7 × C4.** Resolve the opposed-sector effects and all
   possible cancellations. Certified orthant-derived interval propagation is
   permitted under §4; any row not separated from zero fails closed.
5. **C1–C4 foundations.** Certify the exact loading/covariance construction,
   PSD property, threshold-event reductions, sector/cluster index mapping,
   sequential-injection composition and frozen-input rounding enclosures used
   by every proof.
6. **Cross-checks.** Recheck P4–P7 C0's exact-rational classifications and
   independently replay all C1–C4 proof certificates. Confirm that truth-table
   labels drive `V`, attribution error and correct-sign power exactly as in
   the corrected protocol.

P2 also requires a complete frozen proof table across C0–C4, but its scenario
role and gates are unchanged. P1/P3 require exact majority-invariance proofs;
their classification may not rest on numerical intervals.

## 7. Freeze consequence and single next task

The 2026-09-19 truth-table schema/output is not freeze-ready while it encodes
P1/P3 as power scenarios or leaves an orthant-dependent row unclassified.
The corrected scenario-role table, truth tables, interval certificates,
deterministic verifier and amended acceptance accounting must be committed and
SHA-256 manifested before any RNG is constructed. All other original §14
freeze items remain required.

**Single next task:** without constructing RNG, produce and independently
verify the complete 384-row truth tables and replayable proof certificates for
P1–P7 × C0–C4 under this correction, failing closed on every unresolved
P4–P7/C1–C4 row; then report whether the pre-RNG freeze is possible.

Until that task is complete and separately reviewed, the protocol is
internally coherent as a fail-closed prespecification but is not freeze-ready
and remains **NOT AUTHORIZED TO RUN**.
