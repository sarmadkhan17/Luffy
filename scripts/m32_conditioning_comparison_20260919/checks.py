"""Lightweight independent checks (not broad tests). Writes checks.json (exclusive)."""
import itertools
import json
import os

import numpy as np

import sim

HERE = os.path.dirname(os.path.abspath(__file__))
out = {}

# 1. uniform enumeration of within-strata permutations: strata sizes 3,2 -> |G|=12
sid = np.array([0, 1, 0, 1, 0])
rng = np.random.default_rng(1)
order0, od = sim.perm_orders(sid, 120000, rng)
maps = np.empty_like(od)
maps[:, order0] = od  # block order0[i] receives source od[:, i]
keys, counts = np.unique(maps, axis=0, return_counts=True)
valid = all(all(sid[r[i]] == sid[i] for i in range(5)) for r in keys)
exp = 120000 / 12
chi2 = float(((counts - exp) ** 2 / exp).sum())
out["uniform_enumeration"] = {"distinct_perms": int(len(keys)), "expected_G": 12, "all_within_strata": valid,
                              "chi2_df11": chi2, "chi2_crit_05_df11": 19.675,
                              "pass": bool(len(keys) == 12 and valid and chi2 < 19.675)}

# 2. identical mask treatment: permute whole tensors vs summaries
sc = {s["name"]: s for s in sim.PS["scenarios"]}["mcar30"]
Yf, Mf, x, reg = sim.gen_world(sc, 0)
sidD = sim.strata_ids("D", Mf, reg)
rng = np.random.default_rng(2)
o0, od = sim.perm_orders(sidD, 5, rng)
ybar, cnt = sim.block_means(Yf, Mf)
ok = True
for r in od:
    src = np.empty(sim.NB, int); src[o0] = r
    Yp, Mp = Yf[src], Mf[src]                      # whole tensors travel together
    yb2, c2 = sim.block_means(Yp, Mp)
    ok &= np.allclose(yb2, ybar[src]) and np.array_equal(c2, cnt[src])
    ok &= np.array_equal(Mp[:, :, 5], Mp[:, :, 4])  # linked copy mask stays attached
    ok &= np.array_equal(sim.strata_ids("D", Mp, reg), sidD) or np.array_equal(
        sim.mask_class(Mp[:, :, :5]), sim.mask_class(Mf[:, :, :5]))
    ok &= np.array_equal(sim.mask_class(Mp[:, :, :5]), sim.mask_class(Mf[:, :, :5]))  # class preserved per slot
out["mask_synchronization"] = {"pass": bool(ok)}

# 3. guards
base = {s["name"]: s for s in sim.PS["scenarios"]}["baseline"]
g = {}
for name, s in {s["name"]: s for s in sim.PS["scenarios"]}.items():
    g[name] = {k: sim.metadata_guards(s, k) for k in sim.SCHEMES}
expect_all_refuse = [n for n, s in {s["name"]: s for s in sim.PS["scenarios"]}.items() if s["kind"] == "null_invalid"]
out["guards"] = {"per_scenario": g,
                 "invalid_all_refuse": all(all(g[n][k] for k in sim.SCHEMES) for n in expect_all_refuse),
                 "baseline_passes": all(not g["baseline"][k] for k in sim.SCHEMES),
                 "coverage_only_C_refuses": [k for k in sim.SCHEMES if g["coverage_class_confounding"][k]] == ["C"],
                 "nuisance_none_refuse": all(not g["quarter_regime_nuisance"][k] for k in sim.SCHEMES)}
# boundary edges: 400h passes, 401 refuses; maturity 24.99 passes, 25.0 refuses
def with_clock(**kw):
    s = json.loads(json.dumps(base)); s["declared_clocks_links"].update(kw); return sim.metadata_guards(s, "C")
out["guards"]["edges"] = {"lookback400": with_clock(feature_lookback_h=400), "lookback401": with_clock(feature_lookback_h=401),
                          "maturity24.99": with_clock(outcome_maturity_day_max=24.99), "maturity25": with_clock(outcome_maturity_day_max=25.0),
                          "horizon25h": with_clock(outcome_horizon_h=25)}
# degenerate: constant outcome -> refusal, never p=1
r = sim.test_world(np.ones(sim.NB), np.full(sim.NB, 80), np.arange(sim.NB, dtype=float),
                   sim.strata_ids("C", Mf, reg), 99, np.random.default_rng(3))
out["degenerate_refuses"] = r["status"] == "refused" and "p" not in r
out["all_pass"] = bool(out["uniform_enumeration"]["pass"] and out["mask_synchronization"]["pass"]
                       and out["guards"]["invalid_all_refuse"] and out["guards"]["baseline_passes"]
                       and out["guards"]["coverage_only_C_refuses"] and out["guards"]["nuisance_none_refuse"]
                       and out["degenerate_refuses"] and not out["guards"]["edges"]["lookback400"]
                       and out["guards"]["edges"]["lookback401"] and not out["guards"]["edges"]["maturity24.99"]
                       and out["guards"]["edges"]["maturity25"] and out["guards"]["edges"]["horizon25h"])
with open(os.path.join(HERE, "checks.json"), "x") as fh:
    json.dump(out, fh, indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "guards"}, indent=1))
print("guards summary", {k: v for k, v in out["guards"].items() if k != "per_scenario"})
