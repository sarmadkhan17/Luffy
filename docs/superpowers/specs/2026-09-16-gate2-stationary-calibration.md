# Gate-2 stationary bootstrap/HAC calibration — frozen offline experiment

A third separately frozen experiment after two recorded failures. It does not
modify or replace those runs. Design review is recorded in the new
`../reports/2026-09-16-gate2-stationary-review.md`. Prior protocols, reports,
source/tests, thesis v1/v2 artifacts and both /tmp dossiers remain unchanged.
Candidate `55243573515adc3b`, thesis
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`,
retains the handoff status `well_formed_untested` / `waiting_for_evidence`.
No ledger observation or update is made.

## Prespecified revision

Use `scripts/gate2_calibrate_stationary.py`. Keep the untrimmed trade-weighted
signed mean-R contrast, original 30-day calendar summaries, entire entry-block
assignment, unknown/incomplete exclusions, strict positive-effect check,
required-slice fail-closed aggregation and 12/60/20 evidence floors.

Instead of independent block draws, draw K ordered blocks using a stationary
bootstrap. Start uniformly among the K original calendar blocks. At each next
position, restart uniformly with probability 1/L; otherwise continue at the
next original block modulo K. At the charged 30-day resolution, **L=4**, so
geometric runs average 120 days. Empty and duplicated blocks stay in the sample;
H1/H2 use the same indices. Circular continuation gives uniform endpoint
weights but introduces an artificial end/start seam. Restarts are not evidence
of actual independent observations.

The run length is an experimental choice informed by earlier failures, not an
untouched choice or a parameter proved valid. No 60/90-day diagnostic result
is promoted. Four original blocks retain some adjacent dependence; they do not
preserve arbitrary regime persistence or long memory. K/L is only about three
at the floor and six in default null cohorts. It is not an effective-sample-size
theorem and provides little support for asymptotic tail approximations.

For each arm a, sum counts N_a and outcomes S_a, and set mu_a=S_a/N_a.
For each block k define the paired ratio influence:

```
delta = mu_1 - mu_0
u_k = (S_1k - mu_1*n_1k)/N_1 - (S_0k - mu_0*n_0k)/N_0
Q = sum_k u_k^2 + 2*sum_{1 <= h < L} (1-h/L)*sum_{k=h}^{K-1} u_k*u_{k-h}
SE^2 = K/(K-1)*Q
T = delta/SE
```

This is a Bartlett HAC variance with unadjusted lag-product sums. No K/(K-h)
lag correction is applied: the Bartlett quadratic form is positive semidefinite
in exact arithmetic. Negative estimates, even from rounding, refuse; none are
clipped to a positive floor. Nonfinite or SE<=1e-14 estimates, K<2, empty arms
or the prior pooled-variance degeneracy also refuse. The numerical floor is not
a guarantee of calibration.

Recompute arm means, influences and HAC SE on each ordered resampled multiset.
There are no special resets at sampler restart boundaries in that sequence.
Compare `(delta_b-delta)/SE_b >= delta/SE`; invalid draws add to the numerator.
The p-value is `(1+extreme)/(B+1)`, with denominator fixed. More than 20% invalid
draws forces p=1; an invalid observed statistic also forces p=1. Keep the exact
irreversible-nonrejection shortcut with the full-B denominator and censor flag;
no rejection uses a shortcut, and outer replicate counts never stop early.
These are approximate bootstrap p-values, not exact randomization p-values.

## Frozen grid, budget and provenance

Fresh seed **20260918**; deterministic per-cell RNG namespaces. Freeze plan,
NumPy version and script/FDR/test/protocol hashes before draws. New output
directory only, exclusive writes, no automatic overwrite or retry. Complete
runs have per-cell outputs, aggregate results and a manifest. Do not change
source, seeds, thresholds, scenarios or the rule after any cell result.

Retain all eleven original generators with identical output under equal seeds:
iid, shared factor, finite fractional memory, 120-day regime switching, heavy
tail, heavy tail at the 20/40 arm floor, missingness and holding-period ratios
.1/.5/1/2. Add three prespecified persistence challenges:

| Scenario | Mean geometric outcome-state dwell | Independent label AR phi | Calendar span |
|---|---:|---:|---:|
| regime_240 | 240 days | .995^(1/2) | 24 x 30 days |
| regime_480 | 480 days | .995^(1/4) | 24 x 30 days |
| regime_480_long | 480 days | .995^(1/4) | 48 x 30 days |

All outcome-state and label innovations remain independent under the null;
initial state is symmetric. Dwell lengths are stochastic, not fixed durations.
The extended horizon helps measure whether the shorter span alone explains
behavior; it cannot replace an unfavorable shorter-span fixture. Existing
nulls retain 24 blocks/20 trades per block; heavy_tail_floor alone uses 12/5.
Report arm, unknown, incomplete and eligibility counts. Size is unconditional
including legitimate refusals, not a conditional-on-eligibility theorem.

Both synthetic source-LORD++ histories remain unchanged: rows 2/3 after [1]
with levels [.003046424052297288,.001007637299531772], and rows 11/12 after
[10] with [.0026695373430517732,.0006986996187705586]. Freeze both levels
before each family; H1 cannot finance H2. Experiment alpha=.10,w0=.05 are
fixed synthetic inputs, not a configuration or ledger read.

**N=14170 per null fixture and per independent stream model. B=1431 for
fixed-history null/power/diagnostic cells.** Selected stream families compute
B=ceil(1/min(levels))-1 and defer above the 10000-draw cap. Each one-sided exact
binomial endpoint has error=.05/1000, covering fewer than 1000 endpoints with
at least 95% simultaneous coverage within this experiment. This minimum
zero-failure precision budget does not promise precision or acceptance at a
nonzero failure count. The measured decision is finite-B, with no transfer to
arbitrary B or an infinite-bootstrap tail. Repeated experiments do not inherit
a single global confidence guarantee or justify selecting a favorable run.

Keep the iid power grid: 1000 replications, coefficients [0,.25,.5,1,2,4] on
both correlated labels, 12/24 blocks x 5/20 trades. True marginal contrasts are
about 1.7048 times the per-label coefficient. MDE is the smallest tested
coefficient with joint-power lower bound >=80%, no interpolation. Power does
not compensate for size failure.

Keep 1000 long-memory diagnostic populations spanning 48 original blocks.
At aggregation widths 30/60/90 days, **mean run length stays 120 days**, so
L=4, 2, and 4/3 respectively. Use the Bartlett
weights 1-h/L for integer h<L, including noninteger L. These remain diagnostics
only; neither aggregation nor run length may be selected from their results.

## Streams and decision scope

Keep five-candidate, same-population streams, all-null and dependent H1-only/
H2-only models. Partial-null residualized effects retain a zero marginal mean
contrast for the other prediction. Independent whole streams are the binomial
units. Selection is the maximum of three one-sided Gaussian tail probabilities,
a coupling proxy, not production gate 1. Report false rows, false admissions,
selection-conditioned bounds, deferrals, alpha ranges and FDP/FDR estimates
with Hoeffding upper bounds. Source LORD++ arithmetic alone does not establish
error control under reuse/dependence.

No human-approved family-error target exists. Stream probabilities remain
descriptive. Marginal nulls compare with their own levels; fixed-history
all-null conjunctions compare with the smaller level, never their product.
Classification is unchanged: lower bound above target means `inflated`, upper
bound at/below target means `bounded_in_fixture`, otherwise `inconclusive`.
No fixture pass is gate approval; any inflated fixture is a blocker. Do not
silently adjust run length, HAC bandwidth, floors, seed or family rule after
seeing results. Exact synthetic production gate 1 integration, DSL/engine
partition validation, evidence access and family approval remain separate.

## Boundaries

Generated in-memory populations only. No network, held-out outcomes, database,
config, current ledger, broad logs or operational runtime state reads. No
runtime/trading, referee/handoff, reservation/settlement, reason_passed or
admission changes. No prospective clock starts and no wiring is authorized.
The dependence-corrected gate stop stands. Calibration and explicit human
approval remain prerequisites for admission-related use.
