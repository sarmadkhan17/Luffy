"""M3.2 conditioning-scheme synthetic comparison. SYNTHETIC ONLY. Self-contained
(numpy + stdlib), reads prespec.json from this directory, writes results.json with
exclusive create.  Statistic is a SURROGATE, not the historical M3.2 statistic.
"""
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PS = json.load(open(os.path.join(HERE, "prespec.json")))
SEED = PS["master_seed"]
NB = PS["design"]["n_blocks"]
NS = PS["design"]["n_symbols_fixed_roster"]
NK = PS["design"]["unique_outcome_keys_per_symbol"]
SD = PS["dgp_baseline"]["SD_UNIT_analytic"]
LAM = np.array(PS["dgp_baseline"]["lambda_s"])
SIG = np.array(PS["dgp_baseline"]["sigma_s"])
BLOCKS = PS["design"]["blocks"]
QUARTER = np.array([b["quarter"] for b in BLOCKS])
YM = [b["year_month"] for b in BLOCKS]
WD = [b["weekday"] for b in BLOCKS]
HR = [b["hour"] for b in BLOCKS]
REG = {"main": np.array([1 if k in PS["design"]["regime_patterns"]["main_regime1_blocks"] else 0 for k in range(NB)]),
       "rare": np.array([1 if k in PS["design"]["regime_patterns"]["rare_regime1_blocks"] else 0 for k in range(NB)])}
SCHEMES = ["A", "B", "C", "D"]
COVERS = {s: set(PS["schemes"][s]["covers"]) for s in SCHEMES}
CLASS_EDGES = np.array([0.2, 0.4, 0.6])
TEMPLATE = np.array([[(s * NK + t) % 10 < 3 for t in range(NK)] for s in range(NS)])  # True = missing
NUIS = PS["dgp_baseline"]["nuisance_quarter_regime"]
RHO_F, RHO_E = 0.5, 0.3
ALPHA = 0.05


# ---------------------------------------------------------------- data generation
def _ar(innov, rho):
    out = np.empty_like(innov)
    out[..., 0] = innov[..., 0]
    c = math.sqrt(1 - rho * rho)
    for t in range(1, innov.shape[-1]):
        out[..., t] = rho * out[..., t - 1] + c * innov[..., t]
    return out


def gen_world(sc, world, effect=0.0):
    """Returns full tensors Y (NB,NS,6), M (NB,NS,6) (True=observed), x (NB,), regime (NB,).
    Fixed draw order so worlds are identical across schemes and effects (CRN)."""
    d = sc["dgp"]
    rng = np.random.default_rng(np.random.SeedSequence([SEED, sc["code"], world]))
    z = rng.standard_normal(NB)
    g = rng.standard_normal((NB, NK))
    t3 = rng.standard_t(3, (NB, NK)) / math.sqrt(3.0)
    e = rng.standard_normal((NB, NS, NK))
    jump_on = rng.random(NB) < 0.10
    jump = 2.0 * rng.standard_t(3, NB) / math.sqrt(3.0)
    U = rng.random((NB, NS, NK))
    U2 = rng.random((NB, NS, NK))
    latent = rng.integers(0, 4, NB)
    lag = np.minimum(rng.geometric(0.5, (NB, NS)) - 1, NK)
    arx = rng.standard_normal(NB)
    ary = rng.standard_normal(NB)

    regime = REG[d["regime"]]
    f = _ar(t3 if d["factor"] == "t3" else g, RHO_F)
    idio = _ar(e, RHO_E) * SIG[None, :, None]
    noise = LAM[None, :, None] * f[:, None, :] + idio
    if d["jumps"]:
        noise = noise + np.where(jump_on, jump, 0.0)[:, None, None]
    x = z.copy()
    if d["drift_var"]:
        scale = np.linspace(1.0, d["drift_var"], NB)
        noise = noise * scale[:, None, None]
        x = x * scale
    Y = noise + effect * SD * z[:, None, None]
    if d["nuisance"] == "quarter_regime":
        x = x + np.array(NUIS["x_quarter"])[QUARTER - 1] + np.array(NUIS["x_regime"])[regime]
        Y = Y + SD * (np.array(NUIS["y_quarter_sdunits"])[QUARTER - 1] + np.array(NUIS["y_regime_sdunits"])[regime])[:, None, None]
    if d["between_block_ar"]:
        phi = d["between_block_ar"]
        x = _ar(arx[None, :], phi)[0]
        Y = Y + SD * _ar(ary[None, :], phi)[0][:, None, None]
    if d["drift_mean"]:
        tr = np.linspace(-d["drift_mean"], d["drift_mean"], NB)
        x = x + tr
        Y = Y + SD * tr[:, None, None]

    m = d["mask"]
    if m == "complete":
        miss = np.zeros((NB, NS, NK), bool)
    elif m == "mcar":
        rate = d["mcar_rate"] * np.linspace(0.5, 1.5, NS)
        miss = U < rate[None, :, None]
    elif m == "template":
        miss = np.broadcast_to(TEMPLATE, (NB, NS, NK)).copy()
    elif m == "coverage_class":
        rate = np.array([0.08, 0.30, 0.50, 0.72])[latent]
        miss = U < rate[:, None, None]
    elif m == "publication_lag":
        miss = np.arange(NK)[None, None, :] >= (NK - lag)[:, :, None]
    elif m == "informative":
        miss = (Y < 0) & (np.arange(NK)[None, None, :] > 0) & (U2 < 0.6)
    else:
        raise ValueError(m)
    if d["coverage_confounding"]:
        cls = mask_class(~miss)
        x = x + np.array([-0.9, -0.3, 0.3, 0.9])[cls]
        Y = Y + SD * np.array([-1.0, -0.3, 0.4, 1.1])[cls][:, None, None]
    # linked/repeated slot 5 = canonical key 4 (same value, same mask)
    Yf = np.concatenate([Y, Y[:, :, 4:5]], axis=2)
    Mf = np.concatenate([~miss, ~miss[:, :, 4:5]], axis=2)
    return Yf, Mf, x, regime


def unique_view(Yf, Mf):
    return Yf[:, :, :NK], Mf[:, :, :NK]  # canonical de-duplication of linked copies


def block_means(Yf, Mf):
    Y, M = unique_view(Yf, Mf)
    cnt = M.sum(axis=(1, 2))
    s = np.where(M, Y, 0.0).sum(axis=(1, 2))
    with np.errstate(invalid="ignore", divide="ignore"):
        return s / cnt, cnt


def mask_class(Munique):
    frac_missing = 1.0 - Munique.reshape(Munique.shape[0], -1).mean(axis=1)
    return np.searchsorted(CLASS_EDGES, frac_missing, side="right")


# ---------------------------------------------------------------- strata & guards
def strata_ids(scheme, Mf, regime):
    Mu = Mf[:, :, :NK]
    if scheme == "A":
        lab = [(YM[b], WD[b], HR[b], int(regime[b]), Mu[b].tobytes()) for b in range(NB)]
    elif scheme == "B":
        lab = [(int(QUARTER[b]), int(regime[b]), Mu[b].tobytes()) for b in range(NB)]
    elif scheme == "C":
        lab = [(int(QUARTER[b]), int(regime[b])) for b in range(NB)]
    else:
        cls = mask_class(Mu)
        lab = [(int(QUARTER[b]), int(regime[b]), int(cls[b])) for b in range(NB)]
    idx = {}
    return np.array([idx.setdefault(l, len(idx)) for l in lab])


def metadata_guards(sc, scheme):
    o, c = sc["oracle_provenance"], sc["declared_clocks_links"]
    r = []
    if not o["certified_exchangeable"]:
        r.append("no_exchangeability_certificate")
    for k in ["between_block_dependence", "unconditioned_drift_mean", "unconditioned_drift_variance",
              "outcome_dependent_missingness"]:
        if o[k]:
            r.append(k)
    for conf in o["confounders"]:
        if conf not in COVERS[scheme]:
            r.append("omitted_confounder:" + conf)
    if c["feature_lookback_h"] > 400 or c["feature_start_offset_h"] < 0 or c["feature_available_by_day"] > 17:
        r.append("feature_footprint_outside_block")
    if c["outcome_horizon_h"] > 24 or c["outcome_maturity_day_max"] >= 25:
        r.append("outcome_horizon_or_maturity_violation")
    if c["sequence_links_cross_boundary"]:
        r.append("cross_boundary_link")
    return r


def center(v, sid):
    ns = sid.max() + 1
    sums = np.bincount(sid, weights=v, minlength=ns)
    n = np.bincount(sid, minlength=ns)
    return v - (sums / n)[sid], n


def availability(sid):
    n = np.bincount(sid)
    mov = n[n >= 2]
    log10G = float(sum(math.lgamma(k + 1) for k in mov) / math.log(10))
    if len(mov) and np.all(mov == 2):
        floor, kind = 2.0 / 2.0 ** len(mov), "exact_two_sided_all_pairs"
    elif len(mov):
        floor, kind = 10.0 ** (-log10G), "lower_bound_1_over_G"
    else:
        floor, kind = None, "none"
    return {"n_strata": int(len(n)), "movable_strata": int(len(mov)), "movable_blocks": int(mov.sum()),
            "log10_G": log10G, "floor": floor, "floor_kind": kind}


def perm_orders(sid, B, rng):
    order0 = np.argsort(sid, kind="stable")
    keys = sid[None, :] + rng.random((B, len(sid)))
    return order0, np.argsort(keys, axis=1)


def test_world(ybar, cnt, x, sid, B, rng, skip_support=False):
    """Returns dict with status tested/refused. Support guards always apply."""
    av = availability(sid)
    if np.any(cnt < 8):
        return {"status": "refused", "reasons": ["eight_label_floor_unmet"], "avail": av}
    if av["movable_blocks"] == 0:
        return {"status": "refused", "reasons": ["no_nonidentity_exchangeable_blocks"], "avail": av}
    if av["movable_blocks"] < 3:
        return {"status": "refused", "reasons": ["three_group_floor_unmet"], "avail": av}
    xc, _ = center(x, sid)
    yc, _ = center(ybar, sid)
    if np.linalg.norm(xc) < 1e-12 or np.linalg.norm(yc) < 1e-12:
        return {"status": "refused", "reasons": ["degenerate_score_no_support"], "avail": av}
    order0, od = perm_orders(sid, B, rng)
    T = np.abs(yc[od] @ xc[order0])
    tobs = abs(float(xc @ yc))
    tol = 1e-12 * max(1.0, tobs)
    scale = max(tobs, 1e-300)
    distinct = int(np.unique(np.round(T / scale, 9)).size)
    if distinct == 1 and abs(T[0] - tobs) <= tol:
        return {"status": "refused", "reasons": ["degenerate_orbit_no_support"], "avail": av}
    p = (1 + int(np.sum(T >= tobs - tol))) / (B + 1)
    return {"status": "tested", "p": p, "avail": av, "distinct": distinct, "draws": B}


def run_world(sc, world, scheme, effect, B, forced=False, tag=7):
    Yf, Mf, x, regime = gen_world(sc, world, effect)
    ybar, cnt = block_means(Yf, Mf)
    if not forced:
        r = metadata_guards(sc, scheme)
        if r:
            return {"status": "refused", "reasons": r, "avail": None, "draws": 0}
    sid = strata_ids(scheme, Mf, regime)
    rng = np.random.default_rng(np.random.SeedSequence([SEED, sc["code"], world, SCHEMES.index(scheme), tag]))
    return test_world(ybar, cnt, x, sid, B, rng)


# ---------------------------------------------------------------- summaries
def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return None
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return [c - h, c + h]


def summarize(results, alpha=ALPHA):
    tested = [r for r in results if r["status"] == "tested"]
    refused = [r for r in results if r["status"] == "refused"]
    reasons = {}
    for r in refused:
        for q in r["reasons"]:
            reasons[q] = reasons.get(q, 0) + 1
    out = {"worlds": len(results), "tested": len(tested), "refused": len(refused), "refusal_reasons": reasons}
    avs = [r["avail"] for r in results if r.get("avail")]
    if avs:
        mb = np.array([a["movable_blocks"] for a in avs])
        out["availability"] = {
            "n_strata_median": float(np.median([a["n_strata"] for a in avs])),
            "movable_blocks_min_median_max": [int(mb.min()), float(np.median(mb)), int(mb.max())],
            "log10_G_min_median_max": [float(np.min([a["log10_G"] for a in avs])),
                                        float(np.median([a["log10_G"] for a in avs])),
                                        float(np.max([a["log10_G"] for a in avs]))],
            "zero_orbit_refusals": int(sum(a["movable_blocks"] == 0 for a in avs)),
        }
    if tested:
        p = np.array([r["p"] for r in tested])
        k = int(np.sum(p <= alpha))
        fl = [r["avail"]["floor"] for r in tested]
        out.update({
            "reject": k, "rate": k / len(tested), "wilson95": wilson(k, len(tested)),
            "p_cdf": {str(c): float(np.mean(p <= c)) for c in PS["reporting"]["p_cdf_points"]},
            "p_quantiles": {str(q): float(np.quantile(p, q)) for q in PS["reporting"]["p_quantiles"]},
            "successful_draws_per_tested_world": int(tested[0]["draws"]),
            "distinct_drawn_stats_min_median": [int(min(r["distinct"] for r in tested)),
                                                float(np.median([r["distinct"] for r in tested]))],
            "worlds_orbit_smaller_than_draws": int(sum(r["avail"]["log10_G"] < math.log10(tested[0]["draws"] + 1) for r in tested)),
            "worlds_floor_exceeds_alpha": int(sum(f is not None and f > alpha for f in fl)),
            "floor_kinds": sorted({r["avail"]["floor_kind"] for r in tested}),
            "floor_max": float(max(fl)),
        })
        out["size_control_rejected_wilson_lower_gt_05"] = bool(out["wilson95"][0] > 0.05)
    return out


def paired(res_a, res_b, alpha=ALPHA):
    both = [(a["p"] <= alpha, b["p"] <= alpha) for a, b in zip(res_a, res_b)
            if a["status"] == "tested" and b["status"] == "tested"]
    return {"both_tested": len(both), "first_only_reject": sum(1 for a, b in both if a and not b),
            "second_only_reject": sum(1 for a, b in both if b and not a),
            "both_reject": sum(1 for a, b in both if a and b)}


def bh_reject(p, q):
    m = len(p)
    o = np.argsort(p)
    ok = np.nonzero(p[o] <= q * np.arange(1, m + 1) / m)[0]
    rej = np.zeros(m, bool)
    if ok.size:
        rej[o[: ok.max() + 1]] = True
    return rej


def family_world(sc, world, B, m=384, effect=0.5):
    Yf, Mf, x0, regime = gen_world(dict(sc, code=500 + sc["code"]), world, 0.0)
    rng_f = np.random.default_rng(np.random.SeedSequence([SEED, 500 + sc["code"], world, 3]))
    X = rng_f.standard_normal((NB, m))
    X[:, 0] = x0  # feature 0 = world feature z
    Yf2, Mf2, _, _ = gen_world(dict(sc, code=500 + sc["code"]), world, effect)
    ybar, cnt = block_means(Yf2, Mf2)
    if metadata_guards(sc, "D"):
        return {"status": "refused"}
    sid = strata_ids("D", Mf2, regime)
    av = availability(sid)
    if np.any(cnt < 8) or av["movable_blocks"] < 3:
        return {"status": "refused"}
    ns = sid.max() + 1
    n = np.bincount(sid, minlength=ns)
    Xc = X - (np.stack([np.bincount(sid, weights=X[:, j], minlength=ns) for j in range(m)], 1) / n[:, None])[sid]
    yc, _ = center(ybar, sid)
    rng = np.random.default_rng(np.random.SeedSequence([SEED, 500 + sc["code"], world, 3, 11]))
    order0, od = perm_orders(sid, B, rng)
    tobs = np.abs(yc @ Xc)
    T = np.abs(yc[od] @ Xc[order0])
    p = (1 + np.sum(T >= tobs[None, :] - 1e-12 * np.maximum(1, tobs)[None, :], axis=0)) / (B + 1)
    rej = bh_reject(p, 0.05)
    return {"status": "tested", "true_p": float(p[0]), "true_bh": bool(rej[0]), "false_rej": int(rej[1:].sum()),
            "true_unadj": bool(p[0] <= 0.05), "min_p": float(p.min()), "n_rej": int(rej.sum())}


# ---------------------------------------------------------------- main
def main():
    out_path = os.path.join(HERE, "results.json")
    if os.path.exists(out_path):
        sys.exit("results.json exists; refusing to overwrite")
    ss = PS["sample_sizes"]
    scs = {s["name"]: s for s in PS["scenarios"]} if "scenarios" in PS else None
    res = {"prespec_sha256": None, "null": {}, "power": {}, "forced_diagnostic_noninferential": {},
           "paired": {}, "family": {}}
    import hashlib
    res["prespec_sha256"] = hashlib.sha256(open(os.path.join(HERE, "prespec.json"), "rb").read()).hexdigest()
    t0 = time.time()
    for sc in PS["scenarios"]:
        per = {}
        for sch in SCHEMES:
            per[sch] = [run_world(sc, w, sch, 0.0, ss["null_B"]) for w in range(ss["null_worlds"])]
            res["null"].setdefault(sc["name"], {})[sch] = summarize(per[sch])
        res["paired"][sc["name"]] = {"C_vs_D": paired(per["C"], per["D"]), "B_vs_D": paired(per["B"], per["D"])}
        print(f"[{time.time()-t0:7.0f}s] null {sc['name']}: " + "; ".join(
            f"{k} t={v['tested']} r={v.get('rate', float('nan')):.4f}" for k, v in res["null"][sc["name"]].items()), flush=True)
        if sc["kind"] == "null_invalid" or sc["name"] == "coverage_class_confounding":
            fd = {}
            for sch in SCHEMES:
                fr = [run_world(sc, w, sch, 0.0, ss["forced_diagnostic_B"], forced=True, tag=99)
                      for w in range(ss["forced_diagnostic_worlds"])]
                fd[sch] = summarize(fr)
            res["forced_diagnostic_noninferential"][sc["name"]] = fd
    byname = {s["name"]: s for s in PS["scenarios"]}
    for name in ss["power_scenarios"]:
        for eff in ss["power_effects"]:
            per = {}
            for sch in SCHEMES:
                per[sch] = [run_world(byname[name], w, sch, eff, ss["power_B"]) for w in range(ss["power_worlds"])]
                res["power"].setdefault(name, {}).setdefault(str(eff), {})[sch] = summarize(per[sch])
            res["paired"][f"{name}@{eff}"] = {"C_vs_D": paired(per["C"], per["D"])}
            print(f"[{time.time()-t0:7.0f}s] power {name} {eff}: " + "; ".join(
                f"{k} t={v['tested']} r={v.get('rate', float('nan')):.3f}" for k, v in res["power"][name][str(eff)].items()), flush=True)
    fam = ss["family_check"]
    for name in fam["scenarios"]:
        fw = [family_world(byname[name], w, fam["B"], fam["m"], fam["true_feature_effect"]) for w in range(fam["worlds"])]
        t = [r for r in fw if r["status"] == "tested"]
        n = len(t)
        k1 = sum(r["true_bh"] for r in t)
        kf = sum(r["false_rej"] > 0 for r in t)
        ku = sum(r["true_unadj"] for r in t)
        fdp = [r["false_rej"] / r["n_rej"] if r["n_rej"] else 0.0 for r in t]
        res["family"][name] = {"tested": n, "refused": len(fw) - n, "B": fam["B"],
                               "true_feature_BH_rejected": k1, "wilson95_true_BH": wilson(k1, n),
                               "true_feature_unadjusted_p_le_05": ku, "wilson95_unadj": wilson(ku, n),
                               "any_false_BH_rejection": kf, "wilson95_any_false": wilson(kf, n),
                               "mean_FDP": float(np.mean(fdp)) if n else None,
                               "true_p_quantiles": {q: float(np.quantile([r["true_p"] for r in t], q)) for q in (0.05, 0.5, 0.95)} if n else None,
                               "worlds_min_p_at_draw_floor": sum(r["min_p"] <= 1.0 / (fam["B"] + 1) + 1e-15 for r in t)}
        print(f"[{time.time()-t0:7.0f}s] family {name}: {res['family'][name]}", flush=True)
    res["runtime_s"] = time.time() - t0
    with open(out_path, "x") as fh:
        json.dump(res, fh, indent=1)
    print("wrote", out_path, flush=True)


if __name__ == "__main__":
    main()
