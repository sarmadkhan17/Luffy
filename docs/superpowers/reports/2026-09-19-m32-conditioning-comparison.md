# M3.2 conditioning-scheme comparison — SYNTHETIC ONLY (2026-09-19)

Exploratory suite. No repository file, config, flag, DB, market data or network touched.
No freeze claim. **Statistic = SURROGATE** (|within-stratum centred covariance of block feature
and raw observed block mean|), **not the historical M3.2 statistic**. Frozen family (384),
alpha, support floors and draw budget are unchanged; the draw counts used here apply only
to these unadjusted comparisons.

## Reproduction

```
cd /tmp/m32-conditioning-20260919
/home/sarmad/trader/venv/bin/python -B make_prespec.py              # deterministic, no RNG
OPENBLAS_NUM_THREADS=1 /home/sarmad/trader/venv/bin/python -B checks.py
OPENBLAS_NUM_THREADS=1 /home/sarmad/trader/venv/bin/python -B sim.py > run.log 2>&1   # 454 s
```

| File | sha256 |
|---|---|
| prespec.json (v3) | 9baeddbd402b91986d10fe4c7d976d682e0e9400df03a69358f27971291c11bb |
| sim.py | 600e85bcc0e63652ac97ec665f11b53a6b1c38f214db4ddd83f99d8b5a927aad |
| checks.py | c9e3f374fcadd0de07a819945c0c2a5373b270fdff58d5ea7ae490b04c2f7204 |
| make_prespec.py | 1615974f8da8fe8c0ee92e91bace11c53224c50c1619fd574aaf6dcca8133109 |
| results.json | d2093bc8f83ed648247afeabcf397ab385ca8c7e7c4f6ed4179a97e61c33a67c |
| checks.json | 3a05670915b8e3431d4ea3855b9726644099a79121f690c2d2199602c28f2515 |

Seeds: master 20260919. World data `SeedSequence([20260919, scenario_code, world])`
(the same worlds for every scheme, and for effects 0/.2/.5 via common random numbers);
permutations `[20260919, code, world, scheme_idx(A0 B1 C2 D3), 7]` (forced diagnostics tag 99);
family `[20260919, 500+code, world, …]`. Scenario codes are listed in prespec.json.

**Prespec history (disclosed):** v1 left Q1×regime-1 empty and v2 left out the scenario
list. Both were replaced **before any random simulation ran**; their hashes are in
`prespec_v*_discarded.sha256`. One non-statistical bug was fixed before the run, also
before any random simulation: `perm_orders` hard-coded vector length 48, which broke the
5-block check. **scipy is not installed** in the venv, so the Wilson intervals and the
χ² critical value use numpy/math only.

## Design (as prespecified)

- 48 global blocks, 28 days apart, starting Monday 2020-01-06 UTC. Fixed 16-symbol roster.
  There are 5 unique outcome keys per symbol, plus a linked repeat slot (a copy of key 4:
  same canonical key, same mask). The topology is identical in every block, and means
  de-duplicate the linked copy.
- Cell = λ_s·f(b,t) + e(b,s,t) + nuisance + δ·SD_UNIT·z_b. Here f is a common factor,
  AR(1) with ρ=.5 within the block (shocks: t3 innovations plus a common t3 jump in 10% of
  blocks); e is idiosyncratic AR(1) with ρ=.3 and heterogeneous σ_s and λ_s. Scores are
  **not** standardized into IID Gaussian scores.
- SD_UNIT = 0.68342 is the analytic SD of the complete-block mean under the baseline DGP.
  It was computed before any simulation.
- Regimes: main pattern has 19 regime-1 blocks out of 48, with all 8 quarter×regime cells ≥2
  (sizes 14/2/6/6/2/9/7/2). The rare pattern has 4 regime-1 blocks, which leaves one singleton.
- Schemes: A = literal year-month×weekday×hour×regime×exact mask. B = quarter×regime×exact mask.
  C = quarter×regime, with whole outcome+mask tensors moving together. D = C × missing-fraction
  class [0,.2),[.2,.4),[.4,.6),[.6,1]. In every scheme there is one synchronized whole-tensor
  permutation, with no symbol or row transforms.
- Null assumption: outcome+mask tensors are exchangeable within strata and independent of
  the feature given the strata. Missingness is independent of outcomes (given the declared
  class for D).
- p = (1+#{T* ≥ T})/(1+B), with B=1999 draws uniform with replacement from the within-strata
  group (identity included). Wilson 95% intervals are computed over tested worlds only.
  Refusals are counted separately and never reported as p=1.

## Checks (checks.json, all pass)

- Uniform enumeration: strata of sizes 3 and 2 give all 12 of 12 permutations,
  χ²(11)=17.73 < 19.675, all within strata.
- Mask synchronization: permuting whole tensors equals permuting summaries. The linked-copy
  mask stays attached, and each slot keeps its D class.
- Each oracle, clock and link guard fires alone. Edge cases: lookback 400 passes, 401 refuses;
  maturity 24.99 d passes, 25.0 refuses; a 25 h horizon refuses. Baseline passes. Under
  coverage confounding, only C refuses. A constant score is refused, not given p=1.

## Null size — tested worlds / 2000 (rate, Wilson 95%)

| Scenario | A | B | C | D |
|---|---|---|---|---|
| baseline | 2000: 0 [0,.0019]† | .0475 [.0390,.0577] | .0485 [.0399,.0588] | .0450 [.0368,.0550] |
| shocks (t3 + jumps) | 2000: 0† | .0455 [.0372,.0555] | .0445 [.0363,.0544] | .0460 [.0377,.0561] |
| MCAR 10% | refused 2000 (zero orbit) | refused 2000 | .0495 [.0408,.0599] | .0500 [.0413,.0604] |
| MCAR 30% | refused 2000 | refused 2000 | .0445 [.0363,.0544] | .0450 [.0368,.0550] |
| MCAR 50% | refused 2000 | refused 2000 | .0455 [.0372,.0555] | .0505 [.0417,.0610] |
| template 30% | 2000: 0† | .0475 [.0390,.0577] | .0455 [.0372,.0555] | .0475 [.0390,.0577] |
| quarter/regime nuisance | 2000: 0† | .0550 [.0458,.0659] | .0565 [.0472,.0675] | .0540 [.0449,.0648] |
| coverage-class confounding | refused 2000 (zero orbit) | refused 2000 (zero orbit) | **refused 2000 (oracle: omitted_confounder)** | **.0600 [.0504,.0713] — SIZE CONTROL REJECTED by rule** |
| publication-lag censoring | refused 2000 | refused 2000 | .0465 [.0381,.0566] | .0430 [.0350,.0528] |
| rare regime / small strata | 2000: 0‡ | .0410 [.0332,.0506] | .0415 [.0336,.0512] | .0425 [.0345,.0523] |

† A has 4 year-month pairs, so 8 movable blocks and |G|=16 (log10 1.2). Its exact two-sided
floor is 2/16=.125, which is above α in every world. A can never reject, and its p-values are
not calibrated (CDF at .5 = .44, q95 = 1.0). ‡ In the rare scenario A has 3 pairs, a floor of
.25, and 4 distinct statistics.

p CDF at .01/.05/.1/.5/.9 for B/C/D in the tested cells:
- .0055–.014 / .041–.060 / .093–.106 / .485–.504 / .8785–.906
- quantiles q05 .042–.062, q50 .495–.516, q95 .946–.965

Exact per-cell values are in results.json.

D has 120/2000 rejections in the coverage-confounding scenario. Its Wilson lower bound of
.0504 exceeds .05, so by the prespecified rule **size control is rejected for D here**. The
DGP is valid for D by construction: it conditions exactly on the realized class. Multiplicity
across 24 tested B/C/D scheme×scenario cells (about 2σ excess) is a possible explanation, but it is
**not established**. The forced diagnostic (.051) reuses the first 1000 of these same worlds with different
permutation draws (B=999), so it is not independent and is not used to overturn the rule. All other tested B/C/D cells have lower bounds ≤ .05.
The quarter/regime nuisance cells run at about .055 for B, C and D alike (not rejected).

### Availability

| Scheme / condition | Strata | Movable blocks (min / median / max) | log10\|G\| | Two-sided floor | Distinct statistics drawn |
|---|---|---|---|---|---|
| B, C, D under complete masks | 8 | 48 | 26.8 | 1/\|G\| lower bound ≈1.5e-27, not binding | 1998–1999 |
| D, MCAR 30/50% | median 10 | 39 / 46 / 48 | 18.3–26.8 | — | — |
| D, publication lag | 14 | 39–48 (median 44) | 15.3–22.3 | — | — |
| D, coverage confounding | 23 | 32 / 38 / 46 | 8.3–14.3 | — | — |
| Rare regime | 6 | 47 (1 singleton) | 31.6 | — | — |

- Successful draws were 1999 per tested world; there were no failed draws.
- A and B have zero orbit under every non-identical mask (MCAR, publication lag, coverage):
  48 singleton strata, 0 movable blocks, refused as `no_nonidentity_exchangeable_blocks`.
- The surrogate's 3-movable-block and 8-observed-key floors were never binding. They
  **cannot certify** the real three-group/eight-label support floors.

## Invalid designs — refused before randomization, 2000/2000 in all four schemes, 0 draws

| Scenario | Reason | Basis |
|---|---|---|
| between-block AR (φ=.8) | between_block_dependence | oracle |
| within-stratum mean drift | unconditioned_drift_mean | oracle |
| variance drift | unconditioned_drift_variance | oracle |
| informative missingness | outcome_dependent_missingness | oracle |
| missing certificate | no_exchangeability_certificate | oracle |
| feature lookback 500 h / start −100 h | feature_footprint_outside_block | declared clocks |
| horizon 200 h / maturity 25.9 d | outcome_horizon_or_maturity_violation | declared clocks |
| cross-boundary sequence link | cross_boundary_link | declared links |

The oracle guards read synthetic DGP provenance. **They do not detect empirical
exchangeability**, and real data cannot supply this metadata. That limitation stands.

**Forced diagnostic (noninferential; 1000 worlds, B=999, guards bypassed), rejection rate A/B/C/D:**

| Scenario | A | B | C | D |
|---|---|---|---|---|
| coverage confounding | refused | refused | **.635** | .051 |
| between-block AR | 0 | .186 | .189 | .189 |
| mean drift | 0 | .362 | .362 | .360 |
| variance drift | 0 | .073 | .070 | .073 |
| informative missingness | refused | refused | .049 | .045 |
| missing certificate / clock / link scenarios (baseline data) | 0 | .04–.053 | .04–.053 | .04–.053 |

- Under this DGP, informative missingness did not inflate size. That shows invalidity is not
  always visible in rejection rates.
- The clock and link scenarios violate metadata only; their synthetic data carry no leakage.

## Power (1000 worlds each, unadjusted p≤.05, Wilson 95%)

| Scenario | δ | A | B | C | D |
|---|---|---|---|---|---|
| baseline | .2 | 0 (floor) | .223 [.198,.250] | .225 [.200,.252] | .224 [.199,.251] |
| baseline | .5 | 0 | .850 [.827,.871] | .850 [.827,.871] | .849 [.825,.870] |
| shocks | .2 | 0 | .204 | .209 [.185,.235] | .201 [.177,.227] |
| shocks | .5 | 0 | .690 | .684 [.655,.712] | .689 [.660,.717] |
| MCAR 30% | .2 | refused | refused | .256 [.230,.284] | .244 [.218,.272] |
| MCAR 30% | .5 | refused | refused | .882 [.861,.901] | .858 [.835,.878] |
| MCAR 50% | .2 | refused | refused | .278 [.251,.307] | .261 [.235,.289] |
| MCAR 50% | .5 | refused | refused | .898 [.878,.915] | .887 [.866,.905] |

- Paired C vs D, with C-only / D-only rejection counts:
  - MCAR 30% at δ=.5: 36 / 12 (McNemar χ²≈12). D conditioning costs measurable power there.
  - MCAR 50% at δ=.5: 28 / 17.
  - Baseline and shocks: roughly balanced.
- Different scenarios use different world sets. Differences *between* scenarios (for example
  MCAR 50% vs baseline) are therefore not paired and not interpretable as missingness helping.
  The mask preferentially removes higher-index symbols, which also have higher factor loadings
  and idiosyncratic noise. It can therefore reduce block-mean noise; these power results are
  specific to this DGP and do not establish that missingness has no power cost generally.

## BH m=384, q=.05 resolution (analytic)

| Quantity | Value |
|---|---|
| First-rank threshold q/m | 1.302e-4 |
| B for rank-one **attainability**: ceil(m/q)−1 | **7679** (necessary, not sufficient) |
| B for 10% relative Monte Carlo SE at the threshold | 767,900 |
| B for 20% relative Monte Carlo SE at the threshold | 191,975 |
| B for zero-exceedance 95% upper bound ≤ threshold, one-sided | 23,006 |
| B for zero-exceedance 95% upper bound ≤ threshold, Clopper–Pearson two-sided | 28,329 |

- **Discrete orbit floor vs draw floor.**
  - The orbit floor is ≥1/|G|, and exactly 2/|G| when every movable stratum is a pair
    (global sign symmetry of |T|).
  - The Monte Carlo draw floor is 1/(B+1).
  - For B, C and D the orbit is not binding (|G| ≈ 1e27; about 1e8 at worst for D under
    coverage confounding).
  - For A the orbit alone makes even α=.05 unattainable.
- **Supplemental family check** (scheme D, B=9999, 100 worlds, 384 *independent* synthetic
  features, feature 0 at δ=.5):
  - Baseline: the true feature was BH-rejected in 16/100 worlds [.101,.244]. Unadjusted it was
    rejected in 91/100. Any false BH rejection occurred in 11/100 worlds; mean FDP .095.
  - MCAR 30%: 11/100 BH-rejected [.063,.186]; 87/100 unadjusted; any false rejection 5/100;
    mean FDP .035.
  - The FDP estimates come from 100 worlds and are noisy. They are **not an FDR claim**.
  - The real family is correlated and uses the actual statistic, and was **not measured**.

## Conclusion and recommendation

**No scheme is acceptable for freeze.**

- **A:** the literal calendar design cannot reject at any α < .125. It is untestable under
  non-identical masks.
- **B:** under complete or identical masks it behaves the same as C. It is untestable under
  any non-identical mask pattern, which includes realistic MCAR and publication lag.
- **C:** calibrated in every tested cell. However, it omits an observable coverage confounder,
  and that inflates size badly (forced .635). The only thing that refused it here is the oracle.
- **D (conditional candidate only):** testable under non-identical masks, conditions on an
  observable coverage class, and was near-nominal in 9 of 10 valid cells. Its conditions:
  - It failed the prespecified size rule in the coverage-confounding cell (.060, lower
    bound .0504). That must be resolved by independent replication, not by tuning.
  - It costs some power relative to C under MCAR.
  - It inherits the oracle-only invalid refusals.
  - It uses the surrogate statistic.
  - Its BH384 power is low and was measured only on an unrealistic independent family.

## Single next task

A prespecified, independent-seed replication (e.g. 10,000 worlds, B=1999) of scheme D in the
coverage-class-confounding cell, with C and D in the quarter/regime-nuisance cell as paired
controls. Declare the decision rule and seeds before running. The replication resolves whether
D's size excursion is Monte Carlo noise or a defect of realized-class conditioning. No tuning,
no freeze, no real data.
