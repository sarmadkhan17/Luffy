# Gate-2 studentized calibration — new offline protocol

Frozen before this experiment's simulations. This is a revision experiment,
not an amendment to the original failed run or a runtime specification.
Independent review was completed first in
`../reports/2026-09-16-gate2-independent-review.md`. The original protocol,
harness, calibration artifacts, thesis v1/v2 and /tmp dossiers are preserved.
Candidate `55243573515adc3b`, thesis SHA256
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`,
remains `well_formed_untested` / `waiting_for_evidence` as recorded in the
handoff; no current ledger read is used to verify or change that status.

## Revision and hypothesis

`scripts/gate2_calibrate_studentized.py` evaluates paired calendar-cluster
bootstrap-t. The motivation is sensitivity of the unstudentized mean contrast
to estimated scale and skewness. This is a testable proposed improvement;
studentization does not repair dependence between calendar blocks and is not
expected to supply a universal guarantee for regime switching or long memory.
No block-length selection, effect trimming, arm reweighting, new evidence floor,
changed outcome population or retry rule accompanies it.

For each prediction and original block k, retain count n_ak and sum S_ak in
arm a. Let N_a = sum_k n_ak and mu_a = sum_k S_ak / N_a. The reported signed
contrast stays delta = mu_1 - mu_0, the untrimmed trade-weighted mean-R
contrast. With K calendar blocks, including empty blocks:

```
u_k = (S_1k - mu_1*n_1k)/N_1 - (S_0k - mu_0*n_0k)/N_0
SE^2 = K/(K-1) * sum_k u_k^2
T = delta / SE
```

Draw K entire blocks with replacement on the shared calendar. Recompute
mu_b, delta_b, u_b and SE_b on the drawn multiset, counting each duplicated
block separately. The null-centered draw is `(delta_b-delta)/SE_b`.

```
p = (1 + count(invalid_draw OR (delta_b-delta)/SE_b >= T)) / (B+1)
```

SE <= 1e-14, nonfinite statistics, fewer than two blocks, empty arms or the
original pooled-variance degeneracy all refuse. The SE floor is numerical,
not a statistical guarantee. Nonzero contrast with zero SE also refuses.
Invalid draws increase the numerator; more than 20% invalid draws sets p=1.
The sign check remains strictly positive. Required slices combine maximum
p and all-positive signs, refusing an empty required set. Synthetic partition
labels, complete-trade filtering and unknown counts retain the original
contract. Actual DSL/engine partition correctness remains out of scope.

## Fixed experiment and precision

Seed **20260917**, fresh independent per-cell SeedSequence namespaces. Seed
20260916 and every original output remain untouched. The plan and hashes of
script, pure LORD++ arithmetic, new tests and this protocol are written before
simulation. Output requires a new directory and exclusive file creation;
per-cell files survive interruption, but completion requires results and
manifest. No automatic resume, overwrite or outcome-dependent retuning.

Charged block length is **30 days**. Eligibility remains **12 calendar blocks
with both arms, 60 known complete trades, 20 complete trades per arm**. General
null fixtures have 24 blocks and 20 trades/block; the heavy-tail-floor fixture
has 12 blocks and 5 trades/block, exactly 20/40 arms. Whole entry-block
assignment, empty blocks, shared H1/H2 draws and costs remain unchanged.

The two synthetic source-LORD++ histories are rows 2/3 after rejection [1]
and rows 11/12 after rejection [10]. Levels are respectively
[.003046424052297288, .001007637299531772] and
[.0026695373430517732, .0006986996187705586]. Both prediction levels freeze
against the same prior history; H1 cannot finance H2. No current ledger or
configuration is read. Experiment alpha=.10 and w0=.05 remain fixed.

**B=1431 for fixed-history null, power and diagnostic cells**, **N=14170
independent populations per null cell and independent streams per stream
model**. Selected stream families freeze their two levels first, then use
B=ceil(1/min(levels))-1; a family defers if that exceeds draw_cap=10000. This retains the minimum finite-B resolution and
zero-failure precision budget from the original experiment. Each one-sided
Clopper-Pearson endpoint uses error=.05/1000. There are fewer than 1000 lower
and upper endpoints including power, streams, FDR and diagnostics; a union
bound gives at least 95% simultaneous coverage within this new experiment.
This does not promise acceptance or precision when failures occur, and is not
a simultaneous claim across unlimited future revisions. No arbitrary-B or
infinite-bootstrap-tail claim is supported. Retain the exact full-B lower-bound
shortcut only when every H decision is irreversibly a nonrejection. Outer N
never stops early and no rejection uses a censored draw count.

Retain all original null generators unchanged: iid, shared AR factor,
4096-day truncated fractional-memory process, regime switching, heavy tail,
heavy tail at the arm floor, missingness, and holding-period/30-day ratios
.1/.5/1/2. Independent label and outcome innovations define null mean contrasts.
Eligibility and mean group counts are reported: unconditional refusal is not
conditional-on-eligibility calibration. Missingness is not an arbitrary
outcome-dependent mechanism and fractional memory is a finite approximation.

Power retains 1000 independent replicates per coefficient [0,.25,.5,1,2,4]
and each of the 12/24 blocks x 5/20 trades/block settings, at the second
history's levels. Correlated labels imply true marginal contrast approximately
1.7048 times the planted per-label coefficient. MDE is the smallest tested
coefficient with joint-power lower bound >=80%, iid only, no interpolation.
Power cannot compensate for inflated size. The separate 48-block long-memory
30/60/90-day diagnostic retains 1000 replicates; it cannot replace the charged
30-day rule even if a result looks preferable.

## Selection, family target and interpretation

Retain five-candidate shared-population streams and all-null/H1-only/H2-only
models. Dependent partial-null labels have correlation .6 and the residualized
active effect preserves the other marginal null. Each independent stream is
the binomial unit. The selector is exactly the **maximum of three one-sided
Gaussian tail probabilities**, equivalently the tail of the minimum score.
It is a Gaussian coupling proxy, not production gate 1. Record unconditional
and selection-conditioned false admission, false rows, deferrals, alpha range
and stream FDP/FDR estimate with Hoeffding upper bound. Rare selection can
leave the conditional bound uninformative.

No approved family-error target exists. This experiment does not invent an
approval or turn the stream probabilities into acceptance results. Fixed-history
marginal nulls compare against their own source levels; all-null conjunction
compares against the smaller marginal level, never a product. Stream estimates
remain descriptive. Under dependent reuse, source LORD++ arithmetic alone is
not a theorem of sequential error control. Exact synthetic production gate-1
integration and an explicit family target still need separate design work and
human approval before any admission-related use.

Outcomes are `inflated`, `bounded_in_fixture` or `inconclusive`, using the
original exact-binomial rule. Any inflated fixture remains a blocker. A
favorable fixture does not approve the test; no attempt will follow a failure
in this run by quietly changing blocks, floors, seed, tails or targets.

## Boundaries

Generated in-memory populations only. No network, held-out outcomes, database,
config, broad log, current ledger or operational state reads. No runtime,
trading, referee/handoff setting, reservation/settlement, admission or
`reason_passed` changes. No wiring, evidence access or prospective clock is
started. The existing dependence-corrected gate stop remains in force.
Calibration and explicit human approval remain prerequisites for further use.
