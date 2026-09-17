# Gate-2 stationary calibration — 2026-09-16

**The revised test fails calibration; gate 2 remains blocked.**
The independent review is complete and the revised, prespecified offline
experiment is complete. Candidate `55243573515adc3b` retains the handoff status
`well_formed_untested` / `waiting_for_evidence`; no current ledger was read.
This measures generated populations, not the candidate or its actual DSL.

## Revision and review

The new stationary bootstrap resamples ordered runs of original 30-day blocks,
with geometric mean run length 120 days and circular continuation. A paired
Bartlett HAC standard error includes neighboring-block lag products and is
recomputed within every draw. Untrimmed means, 12/60/20 floors and all eleven
original generators remain unchanged. Three new challenges use 240/480-day
mean regime dwell, more persistent independent labels, and a 48-block long
horizon. Invalid standard errors refuse.

The 120-day choice was informed by earlier failures, not selected from this
run. Finite runs, circular seams, truncated covariance estimation and short
histories remain limitations. This is an approximate bootstrap, not an exact
randomization test or a guarantee under arbitrary dependence.

A fresh independent agent completed design review and a pre-run
implementation review. It did not impersonate the pending Luna reviewer or
attribute implementation to Claude. Its report is
`2026-09-16-gate2-stationary-review.md`. The diagnostic run-length wording was clarified before source freezing.
The full combined suite passed 160 tests before the run.

Protocol: `../specs/2026-09-16-gate2-stationary-calibration.md`.
Harness: `scripts/gate2_calibrate_stationary.py`.
Tests: `tests/test_gate2_calibrate_stationary.py`.
Artifacts: `../artifacts/2026-09-16-gate2-stationary-calibration/`.

## Measured size

Fresh seed **20260918**, **14170 independent populations per null fixture**.
Fixed-history B=1431; selected stream families resolve their own frozen levels
up to the 10000-draw cap. Every one-sided bound uses error=.05/1000, giving
at least 95% simultaneous coverage across this experiment's fewer than 1000
endpoints. This covers the implemented finite-B decision, not an arbitrary
bootstrap draw budget, all possible generators or unlimited revisions.

The table shows the second synthetic history (rows 11/12, earlier rejection
[10]): H1 alpha=.002669537343 and H2 alpha=.000698699619. The all-null joint
comparison is the smaller marginal level, never their product. Both histories,
all marginal bounds and eligibility counts are in the artifact.

| Null fixture | H1 failures | H2 failures | Joint failures | Joint lower–upper | Joint assessment |
|---|---:|---:|---:|---|---|
| iid | 117 | 65 | 10 | 0.000142–0.002032 | inconclusive |
| shared_factor | 136 | 56 | 24 | 0.000666–0.003477 | inconclusive |
| long_memory | 187 | 81 | 38 | 0.001311–0.004803 | inflated |
| regime_switching | 271 | 123 | 77 | 0.003353–0.008257 | inflated |
| heavy_tail | 147 | 87 | 32 | 0.001025–0.004243 | inflated |
| heavy_tail_floor | 199 | 94 | 94 | 0.004302–0.009704 | inflated |
| missingness | 130 | 64 | 32 | 0.001025–0.004243 | inflated |
| spillover_0.1 | 144 | 68 | 37 | 0.001262–0.004710 | inflated |
| spillover_0.5 | 146 | 65 | 22 | 0.000581–0.003280 | inconclusive |
| spillover_1 | 130 | 64 | 21 | 0.000539–0.003181 | inconclusive |
| spillover_2 | 129 | 69 | 24 | 0.000666–0.003477 | inconclusive |
| regime_240 | 296 | 161 | 106 | 0.004986–0.010712 | inflated |
| regime_480 | 313 | 168 | 93 | 0.004245–0.009619 | inflated |
| regime_480_long | 305 | 155 | 128 | 0.006262–0.012538 | inflated |

Across both fixed histories, **74 of 84** marginal/joint fixture comparisons have a lower bound above their target. These are overlapping diagnostics, not independent failures. Inconclusive results are not passes.

Even the iid marginal tests inflate: at the second history H1's lower bound
is .00562045 > .00266954 and H2's is .00269973 > .00069870. Persistent dependence
alone therefore cannot explain this run's failure. The longer 48-block,
480-day-regime challenge still has 128 joint false passes and lower bound
.00626182 > .00069870. The experiment does not isolate the cause; finite-K
variance estimation and resampling/studentization interactions remain
hypotheses rather than measured attribution.

These are unconditional decisions including legitimate eligibility refusals.
The heavy-tail floor uses exactly 20/40 arms; missingness and long holds retain
24-block spans. Synthetic labels do not implement the frozen predictions.
Finite fractional memory and the restricted missingness mechanism retain the
original limitations. No observed result was used to revise this run.

## Power and diagnostics

Each power point has 1000 independent replicates. The planted coefficient is
per label; correlated labels imply marginal contrast about 1.7048 times that
coefficient. MDE is the smallest tested coefficient whose joint-power lower
bound reaches 80%, iid only, with no interpolation. Power cannot repair size.

| Blocks / trades per block | Demonstrated grid coefficient R | Joint power | Lower bound |
|---|---:|---:|---:|
| 12 / 5 | not demonstrated through 4 | — | — |
| 12 / 20 | 1 | 0.911 | 0.871 |
| 24 / 5 | 1 | 0.937 | 0.902 |
| 24 / 20 | 0.5 | 0.958 | 0.928 |

The separate 48-block long-memory diagnostics are not replacement rules.
Mean run length remains 120 days at each aggregation (L=4, 2, 4/3), with
matching Bartlett weights:

| Block days | Joint failures / 1000 | Joint assessment |
|---|---:|---|
| 30 | 1 | inconclusive |
| 60 | 1 | inconclusive |
| 90 | 1 | inconclusive |

## Shared streams and unresolved selection

The selector is a Gaussian coupling proxy, not production gate 1. Five looks
reuse each stream's population; independent complete streams are the binomial
units. Dependent partial-null models preserve one zero population contrast.
No approved family-error target exists, so these probabilities are descriptive.

| Stream model | False admissions / 14170 | Lower–upper | Selected streams | Conditional upper |
|---|---:|---|---:|---:|
| all_null | 0 | 0.000000–0.000699 | 3 | 0.963160 |
| H1_only | 413 | 0.023958–0.035033 | 14168 | 0.035038 |
| H2_only | 516 | 0.030598–0.042919 | 14167 | 0.042929 |

The artifact also reports any false row, FDR estimates with Hoeffding upper
bounds, selected candidate counts, deferrals and alpha ranges. Rare all-null
selection does not establish conditional validity. Source LORD++ arithmetic
is not a dependency guarantee. Production gate-1 integration, family-error
approval, actual DSL/engine partition validation and evidence access remain
unresolved.

## Verification and preserved boundaries

The full focused suite passed **160 tests** before source freezing: stationary,
studentized and original synthetic tests, frozen thesis validation and candidate
dossier tests. Tests include literal ordered HAC calculations, dense Bartlett
quadratic-form/PSD checks, forced continuation/restarts and circular wrap,
uniform sampler marginals, L=1 reduction, zero/nonfinite SE refusal, conservative
invalid draws, shortcut equivalence and exact original-generator preservation.

Verified **24 new manifest entries**, all **4 frozen source hashes**, aggregate/per-cell equality and **75 preservation hashes**, including all original artifacts and both /tmp dossiers. The run completed without recovery or retuning.

No held-out data, database, configuration, current ledger, broad log, network
or operational runtime state was read. No trading, runtime, referee/handoff
setting, reservation/settlement, admission or `reason_passed` was changed.
Both earlier failed calibrations, thesis v1/v2, source files and prior workspace
changes were preserved. The existing dependence-corrected gate stop stands.
No wiring, evidence-access approval or prospective clock has started.

Further inference revision needs another separately frozen offline protocol.
No diagnostic block length is promoted. Explicit human approval and closure
of existing blockers remain prerequisites for any admission-related use.

results.json SHA256: `ec508e2f6ded72bd46a213bcf24be46869a8e4dd8ed2ef64f5187ed78cfc5ffb`.

manifest.json SHA256: `0ec083a1d1f61839bb04caf4bd2f8f7de83e5ee070d3b0c216ca286573c089df`.

The independently reviewed next-design assessment is
`2026-09-16-gate2-inference-requirements.md`. Durable integrity evidence is
`2026-09-16-gate2-stationary-verification.json`.
