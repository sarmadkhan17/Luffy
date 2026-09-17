# Gate-2 synthetic calibration protocol

Offline experiment only, 2026-09-16. Implements the calibration exception in
§12 of `2026-09-16-gate2-prediction-test.md`; does not implement a runtime gate.
Candidate `55243573515adc3b`, thesis
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`
remains `well_formed_untested` / `waiting_for_evidence`.

## Frozen experiment

The executable is `scripts/gate2_calibrate_synthetic.py`. Its only project
import is the pure `trader.research.fdr.alpha_at`. It generates all trades and
labels in memory. There is no external data-input option, runtime evaluator,
ledger reservation/settlement, configuration loader or admission operation.
Output requires a new directory and uses exclusive file creation.

`plan.json` is written before simulation. It records the seed, independent
per-cell RNG namespaces, source hashes, NumPy version, repetition counts,
bootstrap draws, histories, effects, floors, scenario names and restrictions.
Workers run independent cells; completion order does not change results.
Per-cell JSON survives interruption, but an incomplete directory has no final
manifest and is not a complete run. There is no automatic resume or overwrite.

Two synthetic histories exercise the actual source LORD++ arithmetic with
alpha=.10 and w0=.05 (these are experiment parameters, not a config read):

| Next H1 row | Earlier rejections | H1 alpha | H2 alpha |
|---|---|---|---|
| 2 | [1] | .003046424052297288 | .001007637299531772 |
| 11 | [10] | .0026695373430517732 | .0006986996187705586 |

Both H levels are frozen from the same history. H1 cannot finance H2.
Both are evaluated; later families can receive both rejection rewards.
These histories do not describe the current ledger.

## Statistic and precision

Whole 30-day calendar blocks are resampled with replacement, on one shared
clock across all generated trades. Entry day determines block membership;
empty calendar blocks are retained. Counts, sums and sums of squares reproduce
trade-weighted means without resampling either arm separately. H1/H2 use the
same resampling draws. Unknown labels and incomplete trades enter neither arm.

The statistic follows the corrected spec: signed mean difference; null-centered
`(1 + count(delta_b - delta_hat >= delta_hat))/(B+1)`; zero pooled variance or
empty arms are degenerate; degenerate draws increase the numerator; over 20%
degenerate draws forces p=1. Eligibility requires 12 blocks carrying both arms,
60 known complete trades and 20 per arm. The implementation conservatively
counts known complete trades for the 60 floor. A separate sign check requires
positive effects. Required-slice aggregation fails closed; production slice
loading/partitioning is outside this harness.

B=1431 is the minimum resolving the smallest level. This is a coarse finite
Monte Carlo grid, not a precise approximation to an infinite-draw p-value.
The actual rejection thresholds are integer multiples of 1/1432; calibration
measures this implemented finite-B decision, not a tail extrapolation. It is
not valid to transfer these measurements to an arbitrary draw count.

A computational shortcut stops generating bootstrap draws only when every
prediction is irreversibly a nonrejection: the full-B denominator and already
observed extreme count give a p lower bound above the largest tested alpha,
or the sign/eligibility already fails. The returned field marks censored p
lower bounds. No rejection can use the shortcut. Outer repetition counts are
fixed; this is not optional stopping of a calibration experiment.

Each reported binomial endpoint uses error=.05/1000. Fewer than 1000 endpoints
are reported across null, power, streams and diagnostics, so their simultaneous
coverage is at least 95% by a union bound. No independence between cells is
needed for that union bound. Counts within each binomial experiment are from
independent generated populations or independent complete streams.

N=ceil(log(error)/log(1-smallest_alpha))=14,170 is the minimum budget whose
zero-failure upper bound can meet that level. It is a feasibility floor, not a
promise of precision or acceptance when failures occur. Bounds are exact
one-sided Clopper-Pearson inversions, computed in log space without SciPy.
The decision labels are `inflated` (lower bound above target),
`bounded_in_fixture` (upper bound no greater than target), or `inconclusive`.
None is an admission approval.

## Generators and known answers

All null generators use independent outcome and label innovations, preserving
zero population mean contrast. H1/H2 labels are correlated synthetic partitions,
not evaluations of the frozen thesis's DSL expressions. These experiments
assess the proposed inferential statistic, not the thesis itself.

| Fixture | Mechanism |
|---|---|
| iid | Independent Gaussian outcomes; correlated binary labels |
| shared_factor | Common AR(.8) factor plus per-trade noise; independent persistent label process |
| long_memory | Truncated ARFIMA(0,.4,0), 4096-day filter with presample innovations, for independently generated outcome and label processes |
| regime_switching | Outcome state flips with probability 1/120 per day; persistent independent labels |
| heavy_tail | Centered, variance-scaled Pareto shape 2.5 innovations, plus common factor |
| heavy_tail_floor | 12 blocks, 60 trades, exactly 20/40 arms, identical H labels, Pareto innovations |
| missingness | Unknown warmup and probabilities depending on label and independent latent volatility; never signed future outcome |
| spillover | Holding durations 0.1, 0.5, 1 and 2 times 30 days; outcomes average the common factor across holding intervals |

General null fixtures use 24 blocks and 20 trades/block. Missingness and long
holds therefore leave enough potential eligible blocks; 12-block versions
would test structural refusal instead. The floor fixture is separate.
Eligibility and mean arm/unknown/incomplete counts are reported, so refusal
must not be interpreted as calibrated inference in an eligible population.
The long-memory approximation has a finite truncation; it is not a theorem
about arbitrary infinite-memory processes. Spillover cohorts use a synthetic
symbol count sufficient to permit nonoverlapping per-symbol holds; they are
not a reproduction of the candidate's trail exits or universe.

## Power and diagnostics

Fixed coefficients [0,.25,.5,1,2,4] R are added to both correlated binary labels
in iid cohorts, for 12/24 blocks and 5/20 trades per block. 1000 repetitions
per cell report H1, H2 and joint power at the second history's alpha levels.
The planted coefficient is not the marginal contrast: the correlated other
label also contributes. With the iid Gaussian sign labels, each true marginal
contrast is `coefficient * (1 + (2/pi)*asin(.9/sqrt(.9**2+.45**2)))`, about
1.7048 times the coefficient. Observed mean contrasts are reported separately.

The grid MDE is the smallest tested coefficient whose simultaneous joint-power
lower bound reaches 80%. Null means not demonstrated within the tested grid;
there is no interpolation or extrapolation. This is iid power, not a power
claim for every stress generator.

A separate 48-block long-memory experiment uses 1000 repetitions to compare
30/60/90-day decisions. The 30-day rule remains the charged design; larger
blocks are diagnostic only. Their repetition budget need not establish a
rare-error bound; insufficient precision is explicitly reported.

## Selection and shared stream

Each independent stream starts at t=1 and processes five candidate looks,
reusing the same synthetic population across candidates. Gate-1 failure
consumes its simulated row and prevents the H family. Gate-1 success triggers
a fixed two-level family; an unresolvable family defers before evaluation.
No database row is reserved, settled or written.

The gate-1 selector is a **proxy**: maximum of three Gaussian tail scores,
coupled to the cohort mean. It reflects the max-of-three shape but does not
execute production consistency, common rotation, trade walking or portfolio
compounding. Selection rates and conditional-on-any-selection error are
reported. Selected candidates within a stream are not treated as independent
binomial trials. Rare selection can leave conditional results uninformative.

All-null and H1-only/H2-only models are included. Partial-null streams retain
correlated binary labels (correlation .6); adding `2*(active-.6*other)` leaves
the other population marginal contrast zero. This tests false conjunction
admission with one true constituent, not only the easier global null.

Independent streams provide binomial bounds for any false row, any false
admission and false admission given any selection. Mean per-stream FDP gives
an FDR estimate with a bounded-variable Hoeffding upper bound. A conjunction
is compared with the smaller marginal level only in all-null fixed-history
fixtures, never with their product. No human-approved stream family-error
target currently exists; stream probabilities are descriptive, not passes.

## Approval boundary

Calibration can falsify the proposed rule within a fixture. Favorable results
cannot establish universal validity, production gate-1-selection behavior,
actual DSL/engine partition correctness, prospective evidence availability or
an approved family-error guarantee. Those and human review remain blockers.
No gate wiring, runtime/config/trading/referee/handoff change, ledger state,
`reason_passed`, held-out read or admission is authorized by this protocol.
