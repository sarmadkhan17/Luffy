"""Deterministic (no RNG) prespecification writer for the M3.2 conditioning-scheme
synthetic comparison.  Writes prespec.json with exclusive create BEFORE any random
simulation is executed.  Self-contained: numpy + stdlib only, no repository imports.
"""
import datetime as dt
import json
import math
import os

import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prespec.json")

N_BLOCKS = 48
N_SYM = 16
N_KEYS = 5          # unique outcome keys (episodes) per symbol per block
N_SLOTS = 6         # slot 5 is a linked/repeated copy of key 4 (same canonical key)
START = dt.datetime(2020, 1, 6, 0, 0, tzinfo=dt.timezone.utc)  # Monday
RHO_F, RHO_E = 0.5, 0.3
LAMBDA = np.linspace(0.6, 1.4, N_SYM)
SIGMA = np.linspace(0.5, 1.5, N_SYM)

blocks = []
for k in range(N_BLOCKS):
    t = START + dt.timedelta(days=28 * k)
    blocks.append({"k": k, "start_utc": t.isoformat(), "year_month": t.strftime("%Y-%m"),
                   "weekday": t.strftime("%a"), "hour": t.hour,
                   "quarter": (t.month - 1) // 3 + 1})

# Fixed regime patterns (block-slot properties; persistent runs, imbalanced).
MAIN_R1 = [2, 3] + list(range(5, 11)) + list(range(19, 23)) + list(range(33, 37)) + list(range(43, 46))
RARE_R1 = [3, 17, 30, 44]
main_regime = [1 if k in MAIN_R1 else 0 for k in range(N_BLOCKS)]
rare_regime = [1 if k in RARE_R1 else 0 for k in range(N_BLOCKS)]


def cells(reg):
    c = {}
    for b, r in zip(blocks, reg):
        key = f"Q{b['quarter']}_R{r}"
        c[key] = c.get(key, 0) + 1
    return dict(sorted(c.items()))


def ym_cells(reg):
    c = {}
    for b, r in zip(blocks, reg):
        key = f"{b['year_month']}_R{r}"
        c[key] = c.get(key, 0) + 1
    return {k: v for k, v in sorted(c.items()) if v > 1}


main_cells = cells(main_regime)
assert len(main_cells) == 8 and all(v >= 2 for v in main_cells.values()), main_cells

# Analytic baseline complete-block noise SD (Gaussian factor, no nuisance, all 80 keys).
Rf = RHO_F ** np.abs(np.subtract.outer(np.arange(N_KEYS), np.arange(N_KEYS)))
Re = RHO_E ** np.abs(np.subtract.outer(np.arange(N_KEYS), np.arange(N_KEYS)))
var_fbar = Rf.sum() / N_KEYS ** 2
var_idio = (SIGMA ** 2).sum() * Re.sum() / (N_SYM * N_KEYS) ** 2
SD_UNIT = math.sqrt(LAMBDA.mean() ** 2 * var_fbar + var_idio)

M_BH, Q_BH = 384, 0.05
thr = Q_BH / M_BH
bh = {
    "m": M_BH, "q": Q_BH, "first_threshold_q_over_m": thr,
    "min_B_rank_one_attainable": math.ceil(M_BH / Q_BH) - 1,
    "B_rel_MCSE_10pct_at_threshold": math.ceil((1 - thr) / (thr * 0.10 ** 2)),
    "B_rel_MCSE_20pct_at_threshold": math.ceil((1 - thr) / (thr * 0.20 ** 2)),
    "B_zero_exceedance_one_sided95_upper_le_threshold": math.ceil(math.log(0.05) / math.log1p(-thr)),
    "B_zero_exceedance_clopper_pearson_two_sided95_upper_le_threshold": math.ceil(math.log(0.025) / math.log1p(-thr)),
    "note": ("Computed analytically. Attainability (1/(B+1) <= q/m) is necessary, not sufficient; "
             "precision needs far larger B. Discrete orbit floor (1/|G|, 2/|G| under two-sided sign "
             "symmetry) is separate from the Monte Carlo draw floor 1/(B+1). The frozen draw budget, "
             "family size and alpha are NOT changed by this document."),
}

ORACLE_OK = {"certified_exchangeable": True, "between_block_dependence": False,
             "unconditioned_drift_mean": False, "unconditioned_drift_variance": False,
             "outcome_dependent_missingness": False, "confounders": []}
CLOCK_OK = {"feature_lookback_h": 400, "feature_start_offset_h": 0, "feature_available_by_day": 17,
            "outcome_horizon_h": 24, "outcome_maturity_day_max": 24.99, "sequence_links_cross_boundary": False}


def scen(name, kind, dgp, oracle=None, clock=None, **kw):
    o = dict(ORACLE_OK); o.update(oracle or {})
    c = dict(CLOCK_OK); c.update(clock or {})
    d = {"name": name, "kind": kind, "dgp": dgp, "oracle_provenance": o, "declared_clocks_links": c}
    d.update(kw)
    return d


BASE = {"factor": "gaussian", "jumps": False, "mask": "complete", "regime": "main",
        "nuisance": None, "coverage_confounding": False, "between_block_ar": 0.0,
        "drift_mean": 0.0, "drift_var": 0.0, "informative_missing": False}


def dg(**kw):
    d = dict(BASE); d.update(kw); return d


scenarios = [
    scen("baseline", "null_valid", dg(), code=1),
    scen("shocks", "null_valid", dg(factor="t3", jumps=True), code=2),
    scen("mcar10", "null_valid", dg(mask="mcar", mcar_rate=0.10), code=3),
    scen("mcar30", "null_valid", dg(mask="mcar", mcar_rate=0.30), code=4),
    scen("mcar50", "null_valid", dg(mask="mcar", mcar_rate=0.50), code=5),
    scen("template30", "null_valid", dg(mask="template", template_rate=0.30), code=6),
    scen("quarter_regime_nuisance", "null_valid", dg(nuisance="quarter_regime"), code=7,
         oracle={"confounders": ["quarter", "regime"]}),
    scen("coverage_class_confounding", "null_valid", dg(mask="coverage_class", coverage_confounding=True), code=8,
         oracle={"confounders": ["coverage_class"]}),
    scen("publication_lag_censoring", "null_valid", dg(mask="publication_lag"), code=9),
    scen("rare_regime_small_strata", "null_valid", dg(regime="rare"), code=10),
    # invalid: must refuse before randomization in ALL schemes
    scen("between_block_ar_invalid", "null_invalid", dg(between_block_ar=0.8), code=21,
         oracle={"between_block_dependence": True}),
    scen("drift_mean_invalid", "null_invalid", dg(drift_mean=1.0), code=22,
         oracle={"unconditioned_drift_mean": True}),
    scen("drift_variance_invalid", "null_invalid", dg(drift_var=3.0), code=23,
         oracle={"unconditioned_drift_variance": True}),
    scen("informative_missingness_invalid", "null_invalid", dg(mask="informative", informative_missing=True), code=24,
         oracle={"outcome_dependent_missingness": True}),
    scen("missing_certificate_invalid", "null_invalid", dg(), code=25,
         oracle={"certified_exchangeable": False}),
    scen("cross_boundary_feature_invalid", "null_invalid", dg(), code=26,
         clock={"feature_lookback_h": 500, "feature_start_offset_h": -100}),
    scen("cross_boundary_horizon_invalid", "null_invalid", dg(), code=27,
         clock={"outcome_horizon_h": 200, "outcome_maturity_day_max": 25.9}),
    scen("cross_boundary_sequence_invalid", "null_invalid", dg(), code=28,
         clock={"sequence_links_cross_boundary": True}),
]

prespec = {
    "title": "M3.2 conditioning-scheme synthetic comparison (SYNTHETIC ONLY, exploratory, no freeze)",
    "written_utc_note": "written before any random simulation; do not tune after results. v2: v1 (sha in prespec_v1_discarded.sha256) replaced BEFORE any random run because its main regime pattern left Q1xR1 empty. v3: v2 (prespec_v2_discarded.sha256) replaced BEFORE any random run because it omitted the scenario list",
    "master_seed": 20260919,
    "seeding": {
        "world_data": "np.random.default_rng(SeedSequence([20260919, scenario_code, world_index])); "
                      "identical world data across schemes (paired) and across effects 0/.2/.5 (common random numbers; "
                      "power world i = null world i of the same base scenario plus effect)",
        "permutations": "np.random.default_rng(SeedSequence([20260919, scenario_code, world_index, scheme_index, 7])) "
                        "scheme_index A=0,B=1,C=2,D=3; forced diagnostics add tag 99",
        "family": "SeedSequence([20260919, 500 + scenario_code, world_index]) for data; tag 11 for permutations",
    },
    "design": {
        "n_blocks": N_BLOCKS, "block_spacing_days": 28, "first_block_start_utc": START.isoformat(),
        "blocks": blocks, "n_symbols_fixed_roster": N_SYM, "unique_outcome_keys_per_symbol": N_KEYS,
        "slots_per_symbol": N_SLOTS,
        "linked_topology": "slot 5 is a repeated/linked copy of key 4 (same canonical key, same mask); "
                           "identical in every block; block means de-duplicate by canonical key",
        "regime_patterns": {"main_regime1_blocks": MAIN_R1, "main_quarter_regime_cells": main_cells,
                            "main_yearmonth_regime_cells_size_gt1": ym_cells(main_regime),
                            "rare_regime1_blocks": RARE_R1, "rare_quarter_regime_cells": cells(rare_regime)},
    },
    "dgp_baseline": {
        "cell": "Y[b,s,t] = nuis_y(b) + delta*SD_UNIT*x_b + lambda_s*f[b,t] + e[b,s,t] (+ jump_b in shocks)",
        "feature": "x_b = nuis_x(b) + N(0,1)",
        "lambda_s": LAMBDA.tolist(), "sigma_s": SIGMA.tolist(),
        "factor": "f[b,.] AR(1) rho=0.5 over the 5 keys, unit stationary variance; gaussian innovations "
                  "(shocks: student-t df=3 innovations scaled to unit variance, plus common block jump "
                  "w.p. 0.10 of size 2*t3/sqrt(3) added to every cell)",
        "idiosyncratic": "e[b,s,.] AR(1) rho=0.3, stationary sd sigma_s",
        "SD_UNIT_analytic": SD_UNIT,
        "SD_UNIT_formula": "sqrt(mean(lambda)^2 * 1'R_f1/25 + sum(sigma^2) * 1'R_e1/6400)",
        "effects": [0.0, 0.2, 0.5],
        "effect_unit": "baseline complete-block (80 unique keys) observed-mean noise SD, analytic (no simulation outcomes)",
        "masks": {
            "mcar": "independent per block and key; symbol rate = rate*linspace(0.5,1.5,16); non-identical across blocks",
            "template": "one deterministic common mask (every symbol's keys with (s*5+t) % 10 < 3 missing -> 30%) identical in every block",
            "coverage_class": "latent class c_b ~ Cat(.25,.25,.25,.25); per-key MCAR rate {0.08,0.30,0.50,0.72}[c_b]; "
                              "REALIZED missing-fraction class (bins [0,.2),[.2,.4),[.4,.6),[.6,1]) shifts "
                              "x by [-0.9,-0.3,0.3,0.9] and outcome cells by [-1.0,-0.3,0.4,1.1]*SD_UNIT (exogenous; "
                              "missingness independent of outcomes given class)",
            "publication_lag": "per block and symbol lag L=min(Geometric(0.5)-1,5); the last L keys (latest maturity) "
                               "censored; independent of outcomes; does not extend the frozen cut",
            "informative": "key missing w.p. 0.6 if its outcome < 0 and it is not the first key (outcome-dependent)",
        },
        "nuisance_quarter_regime": {"x_quarter": [-0.8, -0.2, 0.4, 0.9], "x_regime": [0.0, 1.2],
                                    "y_quarter_sdunits": [0.9, -0.5, 0.3, -1.0], "y_regime_sdunits": [0.0, 1.5]},
        "invalid_dgps": {
            "between_block_ar": "block-level AR(1) phi=0.8 in BOTH x and a common outcome block shift (1*SD_UNIT innovations)",
            "drift_mean": "linear trend over block index from -1 to +1 in x and from -1 to +1 SD_UNIT in outcomes",
            "drift_variance": "outcome noise scale multiplied by linspace(1,3,48) (and x variance likewise)",
        },
    },
    "schemes": {
        "A": {"strata": "UTC year-month x weekday x hour (literal) x regime x exact mask", "tensor_moves_with_mask": True,
              "covers": ["year_month", "quarter", "regime", "coverage_class", "exact_mask"]},
        "B": {"strata": "recurring calendar quarter (Q1..Q4) x regime x exact mask", "tensor_moves_with_mask": True,
              "covers": ["quarter", "regime", "coverage_class", "exact_mask"]},
        "C": {"strata": "recurring quarter x regime; whole outcome+mask tensors travel together; no mask conditioning",
              "tensor_moves_with_mask": True, "covers": ["quarter", "regime"]},
        "D": {"strata": "recurring quarter x regime x predeclared missing-fraction class [0,.2),[.2,.4),[.4,.6),[.6,1]",
              "tensor_moves_with_mask": True, "covers": ["quarter", "regime", "coverage_class"]},
        "rule": "No independent symbol/row transforms; one synchronized permutation of whole block tensors "
                "(outcomes, masks, dispositions, linked copies) within strata; features stay with block slots.",
    },
    "scenarios": scenarios,
    "statistic": {
        "name": "SURROGATE within-stratum centered covariance (NOT the historical M3.2 statistic)",
        "definition": "ybar_b = raw mean of observed unique-key outcomes in block b; xc,yc centered within scheme "
                      "strata; T = |sum_b xc_b*yc_b| (two-sided)",
        "p_value": "(1 + #{T_perm >= T_obs - 1e-12*max(1,|T_obs|)}) / (1 + B); B draws uniform with replacement "
                   "from the full within-strata permutation group (identity included)",
    },
    "refusal_guards_pre_randomization": [
        "oracle: certified_exchangeable false -> no_exchangeability_certificate",
        "oracle: between_block_dependence -> between_block_dependence",
        "oracle: unconditioned_drift_mean -> unconditioned_drift_mean",
        "oracle: unconditioned_drift_variance -> unconditioned_drift_variance",
        "oracle: outcome_dependent_missingness -> outcome_dependent_missingness",
        "oracle: confounder not covered by scheme -> omitted_confounder:<name>",
        "clock: feature_lookback_h>400 or feature_start_offset_h<0 or availability after day 17 -> feature_footprint_outside_block",
        "clock: outcome_horizon_h>24 or maturity >= day 25 -> outcome_horizon_or_maturity_violation",
        "link: sequence_links_cross_boundary -> cross_boundary_link",
        "floor: any block with <8 observed unique keys -> eight_label_floor_unmet",
        "support: movable blocks == 0 -> no_nonidentity_exchangeable_blocks (zero orbit)",
        "support: movable blocks < 3 -> three_group_floor_unmet",
        "support: zero norm of centered x or y over movable blocks -> degenerate_score_no_support",
        "post-draw: all drawn statistics identical -> degenerate_orbit_no_support (never p=1)",
    ],
    "oracle_caveat": "Oracle guards read synthetic DGP provenance; they do NOT detect empirical exchangeability. "
                     "Real data cannot supply this metadata; inability to establish robust invalid refusal without "
                     "the oracle remains a limitation.",
    "valid_null_assumptions": "Under the null, whole-block joint outcome+mask tensors are exchangeable within the "
                              "scheme strata and independent of the block feature given strata; missingness is "
                              "independent of outcomes (given declared class where applicable).",
    "expected_validity": {
        "quarter_regime_nuisance": "all schemes condition on quarter (A via year-month) and regime -> valid",
        "coverage_class_confounding": "A,B (exact mask) and D valid; C omits coverage_class -> oracle refusal; "
                                      "forced C reported only as a noninferential diagnostic",
        "mcar/publication_lag": "C and D valid; A/B valid but expected untestable (non-identical masks)",
    },
    "sample_sizes": {
        "null_worlds": 2000, "null_B": 1999,
        "power_worlds": 1000, "power_B": 1999, "power_scenarios": ["baseline", "shocks", "mcar30", "mcar50"],
        "power_effects": [0.2, 0.5],
        "forced_diagnostic_worlds": 1000, "forced_diagnostic_B": 999,
        "family_check": {"worlds": 100, "m": 384, "B": 9999, "scheme": "D", "scenarios": ["baseline", "mcar30"],
                         "true_feature_effect": 0.5, "features": "384 independent N(0,1) block features; feature 0 carries the effect"},
    },
    "reporting": {
        "size_power": "Wilson 95% on tested worlds; tested and refused counts separate; refusal reasons tabulated",
        "p_cdf_points": [0.01, 0.05, 0.1, 0.5, 0.9], "p_quantiles": [0.05, 0.25, 0.5, 0.75, 0.95],
        "availability": "n strata, movable blocks, log10 prod n_k!, theoretical two-sided exact floor where derivable "
                        "(2/|G| when all movable strata have size 2; else lower bound 1/|G|), successful draws, "
                        "distinct drawn statistics per world, zero-orbit refusals, floor>alpha count",
        "paired": "per scenario, C vs D and B vs D discordant rejection counts on commonly tested worlds",
    },
    "decision_rules": {
        "size": "size control REJECTED for (scheme, scenario) if Wilson 95% lower bound > 0.05 (exploratory; no post-hoc tuning)",
        "invalid": "every invalid scenario must refuse 100% before randomization in all schemes",
        "untestable": "tested fraction 0 means UNTESTABLE, not size control",
        "freeze": "no scheme is recommended for freeze by this exploratory suite; at most a conditional candidate",
    },
    "bh_resolution_analytic": bh,
    "checks": ["uniform enumeration of within-strata permutations (strata sizes 3,2; |G|=12; 120000 draws; chi-square df=11)",
               "identical mask treatment: permuting whole tensors then recomputing means/masks/classes == permuting "
               "block summaries with the same permutation; masks stay attached",
               "guard unit cases: each oracle/clock/link guard fires alone; valid metadata passes; degenerate refuses"],
}

with open(OUT, "x") as fh:
    json.dump(prespec, fh, indent=1)
print("wrote", OUT, "SD_UNIT", SD_UNIT)
print("main cells", main_cells)
print("ym pairs", ym_cells(main_regime))
print("rare cells", cells(rare_regime))
print(json.dumps(bh, indent=1))
