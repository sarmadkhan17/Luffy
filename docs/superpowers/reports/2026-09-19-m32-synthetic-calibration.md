# M3.2 synthetic block-null calibration — September 19

Status: SYNTHETIC-ONLY calibration of the UNFROZEN
[block-sampling proposal](../specs/2026-09-19-m32-block-sampling-proposal.md).
No database, market data, real search, collection/runtime activation, Gate 2,
config/research flag, risk, exit or venue/network call. No freeze or acceptance
claim. `m32_search.py`, `m32_protocol.py` and earlier artifacts are untouched.

## Scope and caveats (read first)

- **Two separate designs, never pooled.** `strict` = the literal proposal
  (UTC year-month/weekday/hour calendar stratum per block).
  `laboratory_repeated_strata` = a synthetic device that declares all blocks
  calendar-equivalent so the permutation machinery can be measured. Lab results
  say nothing about the literal design.
- **DGP provenance is an oracle.** Refusals for between-block autocorrelation,
  unconditioned regimes, drifting mean/variance and missing certification come
  from scenario metadata, not from a finite-data detector. The harness cannot
  detect real-data non-exchangeability.
- **Statistic is a covariance surrogate** (|feature · standardized observed block
  mean|, both centered within strata), not the historical M3.2 search metric or
  a trading-performance estimate.
- In every valid scenario the standardized block score is IID Gaussian across
  blocks by construction, so within-block structure (common shocks, AR, overlaps,
  sequences) collapses to one scalar per block. Near-nominal size here is
  evidence about permutation behaviour under oracle exchangeability only, not
  robustness, and not per-scenario size control (see market_shocks below).

## Run

```
OPENBLAS_NUM_THREADS=1 ./venv/bin/python -B -m scripts.calibrate_m32_blocks \
  --worlds 1000 --family-worlds 250 --draws 2000 --blocks 48 --seed 20260919 \
  --output docs/superpowers/artifacts/m32-search/2026-09-19-synthetic-block-calibration.json
```

- Artifact (new, written with exclusive create): sha256
  `b65bf9b9ff0b94bf65ba12c0e995c22971f7ee4dd28f4d0413d397b736d4ffa4`.
- Code manifest: `m32_block_calibration.py` `5c993a3d…4a70e`,
  `calibrate_m32_blocks.py` `6f0f6717…10175`.
- Review found no substantive defect; no code change. Focused tests: 31 passed.
- alpha = .05; Monte Carlo p = (1+exceedances)/(1+2000); intervals Wilson 95%.

## Type-I (lab, family 1, effect 0, 1000 worlds each, 48 blocks)

| Scenario | Rate | Wilson 95% | P(p≤.01) | P(p≤.1) | P(p≤.5) |
|---|---|---|---|---|---|
| iid | .047 | [.036, .062] | .010 | .094 | .490 |
| market_shocks | .064 (64/1000) | [.050436, .080901] | .019 | .103 | .502 |
| cross_symbol | .045 | [.034, .060] | .015 | .086 | .502 |
| within_block_ar | .044 | [.033, .059] | .009 | .092 | .507 |
| persistent_regimes (conditioned) | .050 | [.038, .065] | .008 | .095 | .490 |
| overlapping_lookbacks (400h) | .049 | [.037, .064] | .011 | .104 | .510 |
| overlapping_outcomes (24h) | .051 | [.039, .066] | .010 | .099 | .485 |
| missing_10 (template) | .059 | [.046, .075] | .012 | .109 | .510 |
| missing_30 (template) | .051 | [.039, .066] | .011 | .103 | .521 |
| missing_50 (template) | .041 | [.030, .055] | .008 | .082 | .503 |
| uneven_symbols (2 strata ×24) | .043 | [.032, .057] | .011 | .082 | .524 |
| episode_sequences | .057 | [.044, .073] | .014 | .113 | .509 |
| **Pooled 12 scenarios (descriptive)** | **601/12000 = .0501** | **[.0463, .0541]** | | | |

The pooled row is a descriptive average over heterogeneous scenarios; it does
not show that every scenario controls size at .05.

p calibration: CDF at .01/.1/.5 is near uniform in most tested nulls;
p quantiles q05 .036–.063, q50 .479–.518, q95 .934–.960. **market_shocks is an
unresolved per-scenario calibration excursion:** 64/1000 = .064, Wilson 95%
[.050436, .080901], which excludes .05. By construction its standardized score
should match iid, so Monte Carlo variation across 12 scenarios is a possible
explanation, but it is not established; neither sampling variation nor size
control for this scenario is asserted here. It needs a separate
prespecified replication before any size claim.

Groups: dependence components 48/48 in every tested world; movable blocks 48.
Strata: 1 (lab), 2 × 24 (uneven, regimes). cross_block_sequence_invalid
correctly merges to 47 components.

## Strict literal calendar design

iid effect 0 and effect .5: **0/1000 tested, 1000/1000 refused each**
(`no_nonidentity_exchangeable_blocks`; 48 singleton strata, 0 movable blocks).
The literal proposal has **no valid permutation** and is UNTESTABLE as written.

## Power by effect (lab, family 1, unadjusted p≤.05, 1000 worlds)

Effect unit: additive association per complete-observation block-mean noise SD.

| Scenario | .1 | .2 | .35 | .5 |
|---|---|---|---|---|
| iid | .100 [.083,.120] | .281 [.254,.310] | .666 [.636,.695] | .910 [.891,.926] |
| market_shocks | .118 [.099,.139] | .293 [.266,.322] | .668 [.638,.696] | .909 [.890,.925] |
| missing_10 | .087 [.071,.106] | .245 [.219,.273] | .602 [.571,.632] | .868 [.846,.888] |
| missing_30 | .086 [.070,.105] | .208 [.184,.234] | .505 [.474,.536] | .785 [.758,.809] |
| missing_50 | .061 [.048,.078] | .144 [.124,.167] | .396 [.366,.427] | .656 [.626,.685] |

Only effect ≥.5 with ≤10% missingness reaches ~.87–.91 unadjusted power at 48
blocks (~3.7 years of 28-day blocks). Effects ≤.2 have low measured
unadjusted power (≤.293).

## Missingness

- Outcome-independent missingness with an identical mask in every block
  (template, mean missing .098/.300/.500) keeps size (above) and costs power.
- **Random MCAR topology (30%, independent per block): 1000/1000 refused**,
  `no_nonidentity_exchangeable_blocks`, 0 movable blocks. Exact-mask strata
  make the proposal untestable under non-identical per-block masks as tested.
- Informative (outcome-dependent) missingness: 1000/1000 refused. The
  outcome-dependence refusal relies on known synthetic DGP provenance
  (oracle metadata); it is not detected from the masks. The co-occurring
  mask-topology refusal only reflects non-identical masks and says nothing
  about outcome dependence.

## BH over the 384-member family (250 worlds, 2000 draws)

| Scenario / effect | Primary unadjusted p≤.05 | Primary BH rejected | Any BH rejection | Any false BH rejection |
|---|---|---|---|---|
| iid / 0 | .036 [.019,.067] | 0/250 | 0/250 | 0/250 [0, .015] |
| iid / .5 | .892 [.847,.925] | **0/250** | 0/250 | 0/250 |
| market_shocks / 0 | .076 [.049,.116] | 0/250 | 0/250 | 0/250 |
| market_shocks / .5 | .884 [.838,.918] | **0/250** | 0/250 | 0/250 |

Structural cause: minimum Monte Carlo p = 1/2001 ≈ 5.0e-4 exceeds the BH
first-rank threshold .05/384 ≈ 1.30e-4, so no rank-one rejection is possible.
A true signal can still be rejected if enough other small p-values support a
BH rank ≥4 (threshold 4 × .05/384 ≈ 5.2e-4 ≥ the floor). Measured adjusted
power was 0/250 in both tested scenarios; that is the only power claim.
≥7,679 draws is merely the necessary resolution for a rank-one rejection at
.05, not sufficient for useful power. Family FWER/FDR 0 here is
resolution-induced conservatism, not calibration.

## Invalid-design refusals (1000/1000 each, 0 null draws)

| Scenario | Reason(s) | Basis |
|---|---|---|
| between_block_ar_invalid | between_block_autocorrelation | oracle |
| unconditioned_regimes_invalid | unconditioned_persistent_regime | oracle |
| drifting_mean_invalid | nonstationary_conditional_mean | oracle |
| drifting_variance_invalid | nonexchangeable_conditional_variance | oracle |
| uncertified_exchangeability_invalid | no_synthetic_exchangeability_certificate | oracle |
| informative_missingness_invalid | outcome_dependent_missingness + no_nonidentity_exchangeable_blocks | oracle + topology |
| cross_block_lookback_invalid (500h) | feature_footprint_outside_block_or_manifest | declared clocks |
| cross_block_horizon_invalid (200h) | outcome_horizon_or_maturity_violation | declared clocks |
| cross_block_sequence_invalid | cross_block_sequence_dependency + dependent_blocks_merge_required | declared links |
| random_missing_topology (valid DGP) | no_nonidentity_exchangeable_blocks | topology (UNTESTABLE) |

Refusals are reported as refusals, never as p=1 or zero Type-I error.

## Statistical conclusion

Under oracle-certified whole-block exchangeability, the whole-block permutation
shows near-nominal size in pooled descriptive terms (.0501 [.0463, .0541]) and
near-uniform p-values in most scenarios; market_shocks (.064, Wilson lower
bound .050436) remains an unresolved per-scenario excursion. That is the only
positive result, and it is conditional on oracle exchangeability. The proposal
**does not meet the freeze gate**: the literal calendar design has no
permutations; independent per-block missingness masks are untestable under
exact-mask strata; the 384 family had 0/250 measured BH-adjusted power at 2000
draws; and 48 blocks give weak single-test power below effect .35. Six of the
nine invalid-DGP refusals (including informative missingness) rely on DGP
metadata that real data cannot supply.

## Changes required before any freeze

1. Replace the literal UTC-month calendar stratum with a predeclared, coarser
   stratum that admits non-identity permutations, justified before results
   (cannot be relaxed after observation).
2. A missingness-robust design (e.g. mask-invariant statistic or registered
   mask classes) calibrated under non-identical MCAR, with informative
   missingness still refusing.
3. Resolve BH384 resolution: a draw budget ≥7,679 is only the necessary
   rank-one resolution, not sufficient power; alternatives include a smaller
   registered family or an exact/analytic tail — an explicit protocol decision
   not yet made. The existing family, support and error budget are unchanged.
4. Calibrate the actual historical search metric and family structure, not
   this covariance surrogate.
5. Add the missing spec stressors: publication lag, rare types and
   regime/calendar imbalance. Finite-data diagnostics may flag some violations
   but cannot prove exchangeability; exchangeability and missingness
   ignorability remain declared, justified design assumptions, not detected
   properties.
6. Declare minimum worthwhile effect and block count/stopping rule from the
   resulting power curve; owner review of assumptions and collection cost.

## Single next bounded task

Address the blocking exact calendar/missingness strata first: a synthetic-only
design comparison of prespecified conditioning schemes (candidate coarser
calendar strata and missingness-mask treatments, all declared before any run)
under plausible non-exchangeability stressors (e.g. calendar/regime imbalance,
drift, non-identical and informative missingness). Report per-scenario size,
refusals and power for each scheme; preserve this report's results, artifact
and limits unchanged. Proposal stays unfrozen; the existing family, support and
error budget are unchanged. No protocol freeze, collection, real data or search.

Subsequent unresolved items, not authorized by this report: any draw-budget
change for BH384 and calibration of the actual M3.2 search metric.
