# Gate-2 studentized calibration — 2026-09-16

**The revised test fails calibration; gate 2 remains blocked.**
The independent review is complete and the revised, prespecified offline
experiment is complete. Candidate `55243573515adc3b` retains the handoff status
`well_formed_untested` / `waiting_for_evidence`; no current ledger was read.
This measures generated populations, not the candidate or its actual DSL.

## Revision and review

The new paired calendar-cluster bootstrap-t recomputes a standard error for
both the observed trade-weighted contrast and every resampled multiset. The
30-day blocks, untrimmed means, 12/60/20 floors, generators and scenario grid
are unchanged. Zero or nonfinite standard errors refuse. Studentization does
not make persistent blocks independent.

A fresh independent agent completed the outstanding review and a pre-run
implementation review. It did not impersonate the pending Luna reviewer or
attribute implementation to Claude. Its report is
`2026-09-16-gate2-independent-review.md`. The two requested pre-freeze wording
clarifications were applied: the proxy takes the maximum of three Gaussian
tail probabilities, and selected stream families have variable draw budgets.

Protocol: `../specs/2026-09-16-gate2-studentized-calibration.md`.
Harness: `scripts/gate2_calibrate_studentized.py`.
Tests: `tests/test_gate2_calibrate_studentized.py`.
Artifacts: `../artifacts/2026-09-16-gate2-studentized-calibration/`.

## Measured size

Fresh seed **20260917**, **14170 independent populations per null fixture**.
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
| iid | 27 | 10 | 1 | 0.000000–0.000882 | inconclusive |
| shared_factor | 49 | 14 | 1 | 0.000000–0.000882 | inconclusive |
| long_memory | 74 | 33 | 19 | 0.000459–0.002980 | inconclusive |
| regime_switching | 473 | 214 | 180 | 0.009362–0.016770 | inflated |
| heavy_tail | 43 | 15 | 7 | 0.000065–0.001688 | inconclusive |
| heavy_tail_floor | 52 | 21 | 21 | 0.000539–0.003181 | inconclusive |
| missingness | 35 | 15 | 7 | 0.000065–0.001688 | inconclusive |
| spillover_0.1 | 53 | 21 | 11 | 0.000172–0.002142 | inconclusive |
| spillover_0.5 | 48 | 18 | 8 | 0.000088–0.001805 | inconclusive |
| spillover_1 | 56 | 22 | 5 | 0.000027–0.001444 | inconclusive |
| spillover_2 | 34 | 16 | 4 | 0.000014–0.001316 | inconclusive |

Across both fixed histories, **11 of 66** marginal/joint fixture comparisons have a lower bound above their target. These are overlapping diagnostics, not independent failures. Inconclusive results are not passes.

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
| 12 / 20 | 1 | 0.985 | 0.964 |
| 24 / 5 | 1 | 0.995 | 0.980 |
| 24 / 20 | 0.5 | 1.000 | 0.990 |

The separate 48-block long-memory diagnostics are not replacement rules:

| Block days | Joint failures / 1000 | Joint assessment |
|---|---:|---|
| 30 | 3 | inconclusive |
| 60 | 1 | inconclusive |
| 90 | 0 | inconclusive |

## Shared streams and unresolved selection

The selector is a Gaussian coupling proxy, not production gate 1. Five looks
reuse each stream's population; independent complete streams are the binomial
units. Dependent partial-null models preserve one zero population contrast.
No approved family-error target exists, so these probabilities are descriptive.

| Stream model | False admissions / 14170 | Lower–upper | Selected streams | Conditional upper |
|---|---:|---|---:|---:|
| all_null | 0 | 0.000000–0.000699 | 9 | 0.667258 |
| H1_only | 120 | 0.005795–0.011877 | 14168 | 0.011878 |
| H2_only | 181 | 0.009423–0.016850 | 14168 | 0.016853 |

The artifact also reports any false row, FDR estimates with Hoeffding upper
bounds, selected candidate counts, deferrals and alpha ranges. Rare all-null
selection does not establish conditional validity. Source LORD++ arithmetic
is not a dependency guarantee. Production gate-1 integration, family-error
approval, actual DSL/engine partition validation and evidence access remain
unresolved.

## Verification and preserved boundaries

The full focused suite passed **124 tests** before source freezing: the new
studentized and original synthetic tests, frozen thesis validation and candidate
dossier tests. Tests include literal repeated-block standard errors, empty
calendar blocks, nonzero contrast with zero SE, invalid draws counted against
rejection and shortcut/full-draw decision equivalence.

Verified **21 new manifest entries**, all **4 frozen source hashes**, aggregate/per-cell equality and **45 preservation hashes**, including all original artifacts and both /tmp dossiers. The run completed without recovery or retuning.

No held-out data, database, configuration, current ledger, broad log, network
or operational runtime state was read. No trading, runtime, referee/handoff
setting, reservation/settlement, admission or `reason_passed` was changed.
The original failed calibration, thesis v1/v2, source files and prior workspace
changes were preserved. The existing dependence-corrected gate stop stands.
No wiring, evidence-access approval or prospective clock has started.

Further inference revision needs another separately frozen offline protocol.
No diagnostic block length is promoted. Explicit human approval and closure
of existing blockers remain prerequisites for any admission-related use.

results.json SHA256: `3243afac4b3c0321faaec44a29d73f108670ab0e04e16702868a563c7cf6da24`.

manifest.json SHA256: `f44b04324370d61b42266c90f001b0588d78404e619e7be09af1dc2bfd4f9730`.

Durable integrity and preservation evidence:
`2026-09-16-gate2-studentized-verification.json`.
