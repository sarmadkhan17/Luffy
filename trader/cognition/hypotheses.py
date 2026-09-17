"""Competing hypotheses for a selected episode, and their resolution.

These are ILLUSTRATIVE deterministic research templates. They are not
discovered strategies, edges or causal facts, and `probability` stays None
until something calibrates it. No template names a trade direction.

For a selected asset at decision time, with anchor close A and deadline
D = A + horizon * tf:

- market_continuation — the asset moved because the market moved. Predicts:
  the cohort median forward log return over (A, D] keeps the sign of the
  cohort's current median R_S, and the asset's |rel_forward_z| < persist_z.
- asset_divergence — something specific to the asset. Predicts:
  |rel_forward_z| >= persist_z.
- unknown — neither explanation. Predicts: neither competitor is confirmed.

    rel_forward_z = (fwd_asset - median_cohort(fwd)) / (sigma_long * sqrt(horizon))

The cohort is the eligible set at decision time, frozen then, and relative and
hypothesis outcomes need the deadline bar of EVERY member; until then they stay
unresolved. The asset's raw forward return (`baseline_raw`) needs only its own
bar. Measurement is exact-horizon: the bar that closes at D, as available at
resolution time.
Tolerance is 0 — a later bar is never substituted. Evidence cited on each
side is by observation ID; contradicting evidence is kept, not netted away.
"""
from __future__ import annotations

import math
import statistics

from trader.cognition.contracts import Hypothesis, Outcome, rnd, stable_id

TOLERANCE_MS = 0


def frame_episode(decision_id: str, sym: str, att: dict, cfg, deadline_ms: int):
    """Return (episode dict, [Hypothesis]) for one selected asset."""
    idx, market, f = att["index"], att["market"], att["features"][sym]
    breadth = idx[("market_breadth", "*")]
    div = idx[("relative_return_divergence", sym)]
    parts = idx[("participation", sym)]
    episode_id = stable_id("ep", decision_id, sym)

    missing = [o.obs_id for o in (breadth, div) if o.status != "ok"]
    broad = market["broad"] is True
    divergent = div.status == "ok" and abs(f["div_z"]) >= cfg.divergence_z
    if missing:
        framing, why = "unknown", "insufficient_required_evidence"
    elif broad and not divergent:
        framing, why = "market_wide", "broad_move_without_divergence"
    elif divergent and not broad:
        framing, why = "asset_specific", "divergence_without_broad_move"
    elif broad and divergent:
        framing, why = "contested", "broad_move_and_divergence"
    else:
        framing, why = "unknown", "no_discriminating_evidence"

    med = market["median_ret_short"]
    sign = None if med is None or med == 0 else (1 if med > 0 else -1)

    def ids(*pairs):
        return [o.obs_id for o, keep in pairs if keep]

    market_hyp = Hypothesis(
        stable_id("hyp", episode_id, "market_continuation"), episode_id,
        "market_continuation",
        f"{sym}'s salience reflects a market-wide move that continues to the deadline.",
        ids((breadth, broad)),
        ids((div, divergent), (breadth, market["status"] == "ok" and not broad)),
        {"cohort_median_forward_sign": sign,
         "asset_abs_rel_forward_z_below": cfg.persist_z},
        deadline_ms,
        "Falsified if the cohort median forward return at the deadline does not "
        "share the expected sign, or |rel_forward_z| >= persist_z.")
    moving = [(p, True) for p in parts            # each series judged on its own change
              if p.status == "ok" and abs(p.value) >= cfg.participation_change]
    div_hyp = Hypothesis(
        stable_id("hyp", episode_id, "asset_divergence"), episode_id,
        "asset_divergence",
        f"{sym}'s salience is asset-specific and its divergence from the cohort persists.",
        ids((div, divergent), *moving),
        ids((breadth, broad), (div, div.status == "ok" and not divergent)),
        {"asset_abs_rel_forward_z_at_least": cfg.persist_z},
        deadline_ms,
        "Falsified if |rel_forward_z| < persist_z at the deadline.")
    unknown_hyp = Hypothesis(
        stable_id("hyp", episode_id, "unknown"), episode_id, "unknown",
        f"Neither template explains {sym}; the cause is unknown.",
        list(missing), [],
        {"competitors_confirmed": 0},
        deadline_ms,
        "Falsified if either competing template is confirmed.")
    episode = {"episode_id": episode_id, "symbol": sym, "framing": framing,
               "framing_reason": why, "salience_obs": att["rows"][sym]["evidence"],
               "contradictions_present": bool(market_hyp.contradicting_ids
                                              or div_hyp.contradicting_ids),
               "deadline_ms": deadline_ms}
    return episode, [market_hyp, div_hyp, unknown_hyp]


def forward_measure(ds, decision: dict, eval_ms: int):
    """Exact-horizon forward returns for the decision's frozen cohort, as known
    at `eval_ms`. None before the deadline. The cohort median exists only when
    EVERY cohort member's deadline bar is available — a subset is a different
    cohort and is never graded silently."""
    deadline = decision["deadline_ms"]
    if eval_ms < deadline:
        return None
    per = {}
    for sym, c in decision["cohort"].items():
        bar = ds.bar_asof(sym, deadline - ds.tf_ms, eval_ms)
        if bar is not None:
            per[sym] = {"fwd": math.log(bar.close / c["anchor_close"]),
                        "bar_close_ms": bar.close_ms,
                        "bar_available_ms": bar.available_ms}
    missing = sorted(set(decision["cohort"]) - set(per))
    complete = decision["cohort_ok"] and not missing
    median = statistics.median(v["fwd"] for v in per.values()) if complete else None
    return {"per": per, "median": median, "missing": missing, "complete": complete}


def pending_reason(kind: str, sym: str, fwd) -> dict:
    """Why an outcome is still unresolved at the final evaluation time."""
    if fwd is None:
        return {"reason": "deadline_not_reached"}
    if sym not in fwd["per"]:
        return {"reason": "deadline_bar_unavailable"}
    return {"reason": "cohort_incomplete", "missing_cohort": fwd["missing"]}


def _scale(decision, sym, cfg):
    s = decision["cohort"][sym]["sigma"]
    scale = s * math.sqrt(cfg.horizon) if isinstance(s, float) else None
    return scale if scale is not None and math.isfinite(scale) and scale > 0 else None


def _raw(sym, decision, fwd, cfg):
    p, scale = fwd["per"][sym], _scale(decision, sym, cfg)
    return {"symbol": sym, "forward_log_return": rnd(p["fwd"]),
            "abs_forward_z": rnd(abs(p["fwd"]) / scale) if scale else None,
            "scale": scale, "scale_status": "ok" if scale else "unusable",
            "bar_close_ms": p["bar_close_ms"], "bar_available_ms": p["bar_available_ms"],
            "tolerance_ms": TOLERANCE_MS}


def _relative(sym, decision, fwd, cfg):
    m = _raw(sym, decision, fwd, cfg)
    rel = (fwd["per"][sym]["fwd"] - fwd["median"]) / m["scale"] if m["scale"] else None
    return dict(m, cohort_median_forward=rnd(fwd["median"]), rel_forward_z=rnd(rel),
                cohort_bars={s: [v["bar_close_ms"], v["bar_available_ms"]]
                             for s, v in sorted(fwd["per"].items())})


def _not_testable(subject_id, kind, deadline, eval_ms, sym, reason):
    return Outcome(stable_id("out", subject_id), subject_id, kind, deadline, eval_ms,
                   "not_testable", {"symbol": sym, "reason": reason,
                                    "tolerance_ms": TOLERANCE_MS})


def resolve_episode(episode: dict, hyps: list, decision: dict, fwd, eval_ms: int, cfg):
    """Outcomes for the three hypotheses, or None while evidence is absent."""
    sym = episode["symbol"]
    if fwd is None:
        return None
    if not decision["cohort_ok"]:
        return [_not_testable(h["hyp_id"], "hypothesis", h["deadline_ms"], eval_ms, sym,
                              "cohort_below_min_at_decision") for h in hyps]
    if not fwd["complete"]:
        return None
    if _scale(decision, sym, cfg) is None:
        return [_not_testable(h["hyp_id"], "hypothesis", h["deadline_ms"], eval_ms, sym,
                              "unusable_scale") for h in hyps]
    m = _relative(sym, decision, fwd, cfg)
    by = {h["template"]: h for h in hyps}
    rel = abs(m["rel_forward_z"])
    exp_sign = by["market_continuation"]["prediction"]["cohort_median_forward_sign"]
    med = fwd["median"]
    got_sign = 0 if med == 0 else (1 if med > 0 else -1)
    status = {
        "market_continuation": "not_testable" if exp_sign is None else
        "confirmed" if got_sign == exp_sign and rel < cfg.persist_z else "falsified",
        "asset_divergence": "confirmed" if rel >= cfg.persist_z else "falsified",
    }
    status["unknown"] = ("falsified" if "confirmed" in status.values() else "confirmed")
    return [Outcome(stable_id("out", by[t]["hyp_id"]), by[t]["hyp_id"], "hypothesis",
                    by[t]["deadline_ms"], eval_ms, status[t],
                    dict(m, observed_median_sign=got_sign))
            for t in ("market_continuation", "asset_divergence", "unknown")]


def resolve_baseline_raw(sample: dict, decision: dict, fwd, eval_ms: int, cfg):
    """The asset's own forward return; needs only its own deadline bar."""
    sym = sample["symbol"]
    if fwd is None or sym not in fwd["per"]:
        return None
    sid = f"{sample['sample_id']}:raw"
    return Outcome(stable_id("out", sid), sid, "baseline_raw", decision["deadline_ms"],
                   eval_ms, "measured",
                   dict(_raw(sym, decision, fwd, cfg), selected=sample["selected"]))


def resolve_baseline_relative(sample: dict, decision: dict, fwd, eval_ms: int, cfg):
    """Forward return relative to the full frozen cohort."""
    sym = sample["symbol"]
    sid = f"{sample['sample_id']}:relative"
    if fwd is None:
        return None
    if not decision["cohort_ok"]:
        return _not_testable(sid, "baseline_relative", decision["deadline_ms"], eval_ms,
                             sym, "cohort_below_min_at_decision")
    if not fwd["complete"]:
        return None
    return Outcome(stable_id("out", sid), sid, "baseline_relative", decision["deadline_ms"],
                   eval_ms, "measured",
                   dict(_relative(sym, decision, fwd, cfg), selected=sample["selected"]))
