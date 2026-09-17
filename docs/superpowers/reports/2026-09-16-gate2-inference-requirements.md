# Gate-2 inference requirements after the stationary experiment

Design assessment only. This does not revise a frozen protocol, authorize a
new experiment or change any gate. The completed stationary/HAC experiment
demonstrates marginal inflation even under iid outcomes. Its report,
`2026-09-16-gate2-stationary-calibration.md`, is the authority for final counts.

## What the evidence establishes

The first experiment failed with independent 30-day block resampling.
Studentization did not resolve persistence failures. The stationary/HAC
experiment additionally preserves runs of adjacent blocks, but its completed
iid cells already fail. Independent implementation review has identified no
discrepancy against the registered formulas. These observations reject these
particular finite-sample procedures at their implemented rare-error levels.
They do not establish which variance estimator, resampling distribution or
small-sample correction caused each failure.

Do not respond by multiplying standard errors until the same synthetic grid
passes, promoting a favorable diagnostic block length, or treating a fresh
seed as a new statistical justification. Any new rule needs a mathematical
scope, a separately frozen experiment and validation outside the conditions
that motivated its design. Passing a finite simulator family remains evidence
about that family, not production admission approval.

## Assumptions that a useful mean-contrast test needs

The untrimmed mean-R contrast and dependent calendar population are fixed
requirements. Their inferential assumptions must be stated separately from
the estimand and the sample-size floors.

A useful, uniformly valid finite-sample test of a mean contrast cannot be
obtained over all unbounded outcome distributions merely by requiring that
the mean or variance exists. A simple construction explains the obstacle:
start with a distribution having a positive subset contrast, then place an
arbitrarily small probability epsilon on an arbitrarily large negative subset
outcome. Its population contrast can become zero or negative, while a sample
of n independent trades avoids the added component with probability
(1-epsilon)^n, arbitrarily close to one. The modified distribution can still
have finite variance. Thus observationally near-identical finite samples can
have opposite population mean contrasts when no quantitative tail control is
specified. Arbitrary dependence creates a separate obstacle: many observed
trades need not provide many independent pieces of information.

This does not mean that mean tests are impossible under useful assumptions.
It means the next design should explicitly supply and justify quantitative
conditions, for example a justified moment/tail bound and a dependence model
or independently generated sampling units. Estimating such limits from the
same short cohort does not itself make them known. A fitted mixing length or
an apparent autocorrelation cutoff is not proof that long dependence is absent.
No such production assumption is certified by the synthetic fixtures here.

Changing to trimmed/clipped outcomes would change the registered estimand.
Permuting labels would require an exchangeability/randomization null stronger
than equal marginal mean contrasts. An oracle that knows the simulator's law
would require information unavailable in production. None is an automatic
replacement for the existing test contract. Returning p=1 always controls
false passes but provides no useful test or calibrated evidence of an edge.

## Separate the family question from the marginal-test question

A future family design can be discussed symbolically without approving an
error target. For a fixed family requiring every prediction, a valid
conjunction p-value is the maximum of valid constituent p-values: whenever at
least one constituent null is true, the event that the maximum is <=a is a
subset of that true null's rejection event. This argument does not require
independence between constituents. Required slices can be included in the same
maximum if validity applies to each required constituent/slice null.

Across a predeclared sequence of valid family tests, fixed spending levels
beta*w_j with nonnegative weights summing to <=1 would give an any-false-family
upper bound beta by a union bound, without dependence-based reward assumptions.
This is a conditional design statement, not a selected numerical target or a
proposal to replace the existing LORD++ ledger now. It requires valid family
p-values for the actual selection/evaluation protocol and a correctly defined
family null. Adaptive candidate generation, outcome-dependent selection and
reused data do not automatically meet those premises. The current Gaussian
selection proxy cannot establish them for production gate 1.

The practical dependency order is therefore: justify the marginal inferential
model and sampling units; validate the actual selection/partition protocol;
then approve the family-error objective and accounting rule. Synthetic stream
rates alone cannot close all three. Exact production gate-1 simulation can be
useful separately, but cannot make invalid marginal p-values valid.

## Next bounded work and existing limits

The next useful statistical work is an assumptions-first design: state the
permitted outcome-tail/dependence class and the source of each quantitative
bound, derive the guarantee within that class, then preregister power and
violating-assumption challenges. The frozen thesis and untrimmed mean metric
must remain unchanged unless explicitly revised in a separately authorized
contract. This note selects no fourth bootstrap variant.

No held-out outcomes, database, configuration, current ledger, broad logs,
network or operational state were read for this assessment. No runtime,
trading, referee/handoff, reservation/settlement, reason_passed or admission
change is made. All previous artifacts remain intact and the recorded gate
stop stands. Production assumptions, a family target, evidence access and any
wiring still require the existing explicit approvals and prerequisites.
