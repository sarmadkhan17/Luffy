"""Online expert aggregation — EWA (exponentially weighted average).

Replaces static analyst weights with theory-backed online weights
(Littlestone–Warmuth / Vovk exponential weights, as applied to finance in
the Bernstein Online Aggregation literature): after every resolved
outcome, each voting agent's weight is multiplied by exp(-η·loss) —
correct votes keep their weight, wrong votes decay. Cumulative-loss
guarantees track the best expert under non-stationarity, which weekly
batch validation cannot.

Shrinkage: the EWA estimate is blended with the static prior by evidence
(pseudo-count `prior`), so the first few dozen outcomes cannot swing
weights wildly — same philosophy as calibration's k=25.

State: data/ewa_state.json — {agents: {name: {w, n}}, processed: [...],
updated}. Updated on the hourly brain tick; consumed by the orchestrator's
reload_weights() (hot-reloaded, no restart needed).
"""
from __future__ import annotations

import json
import logging
import math
import time

from ..core.config import ROOT

log = logging.getLogger(__name__)

PATH = ROOT / "data" / "ewa_state.json"

#: ceiling on one expert's raw EWA score before it is turned into a share —
#: a runaway multiplier must not be able to take the whole book
W_CAP = 4.0


def _load() -> dict:
    if PATH.exists():
        try:
            return json.loads(PATH.read_text())
        except Exception:
            pass
    return {"agents": {}, "processed": [], "updated": 0.0}


def _save(state: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(state, indent=2))


def update(journal, eta: float = 0.15, mix: float = 0.02) -> dict:
    """Fold every newly-resolved outcome into the expert weights.

    Cursor = last processed resolved_at (monotonic: the resolver processes
    oldest-first), so rows are folded exactly once no matter how late they
    resolve.

    Discounted EWA with mixing (Herbster–Warmuth 'tracking the best
    expert'): after each fold every weight is pulled a fraction `mix`
    back toward 1.0. Pure multiplicative decay can never recover — one
    bad month pins an expert at ~0 forever; mixing gives a recovery
    half-life of ~1/mix outcomes, matching non-stationary markets."""
    state = _load()
    agents: dict = state.get("agents", {})
    cursor = state.get("cursor", "")

    rows = journal.query(
        "SELECT o.decision_id AS did, o.cycle_id AS cid, o.action AS act, "
        "       o.correct_4h AS ok, o.resolved_at AS rat "
        "FROM outcomes o "
        "WHERE o.resolved_at IS NOT NULL AND o.correct_4h IS NOT NULL "
        "  AND o.resolved_at > ? "
        "ORDER BY o.resolved_at", (cursor,))
    if not rows:
        return state
    votes = {}
    for v in journal.query(
            "SELECT cycle_id, agent, conviction FROM votes "
            "WHERE ABS(conviction) > 0.05"):
        votes.setdefault(v["cycle_id"], []).append(
            (v["agent"], float(v["conviction"])))

    folded = 0
    for r in rows:
        ok = int(r["ok"])
        for agent, conv in votes.get(r["cid"], []):
            agrees = (conv > 0) == (r["act"] == "BUY")
            label = ok if agrees else 1 - ok     # per-VOTE truth
            rec = agents.setdefault(agent, {"w": 1.0, "n": 0})
            w = rec["w"] * math.exp(-eta * (1.0 - label))
            rec["w"] = (1.0 - mix) * w + mix * 1.0   # recovery mixing
            rec["n"] += 1
        folded += 1
        cursor = max(cursor, r["rat"])

    state = {"agents": agents, "cursor": cursor, "updated": time.time()}
    _save(state)
    top = sorted(agents.items(), key=lambda kv: -kv[1]["w"])[:3]
    log.info(f"ewa folded {folded} outcomes — top: "
             f"{[(a, round(x['w'], 3)) for a, x in top]}")
    return state


def blended_weights(prior: dict[str, float],
                    prior_k: float = 30.0) -> dict[str, float]:
    """EWA weights shrunk toward the static prior, normalized to Σ=1.

    prior: the merged default/measured weights (already Σ≈1). An agent
    with n resolved votes gets weight (n·ewa + prior_k·prior)/(n+prior_k);
    an unseen agent keeps its prior.

    `rec["w"]` is an UNNORMALIZED multiplicative score — it starts at 1.0
    and decays toward 0 — while the prior is a share of a distribution over
    every agent (~0.14 each at seven of them). Blending the two raw put the
    EWA term on roughly seven times the prior's scale, so `prior_k` shrank
    nothing and identical evidence flattened the prior toward uniform. The
    score is an adjustment TO the prior, not a replacement for it, so it
    enters the way exponential-weights aggregation says it should:

        posterior[a] ∝ prior[a] × w[a]

    renormalized over the agents that have evidence, so that block keeps
    the prior mass it already held and unseen agents are untouched. Equal
    scores then leave the prior exactly where it was, and `prior_k` shrinks
    a real difference by how much evidence stands behind it.
    """
    state = _load()
    agents: dict = state.get("agents", {})
    seen = {a: max(0.0, min(float(agents[a]["w"]), W_CAP)) for a in prior
            if (agents.get(a) or {}).get("n", 0) > 0}
    mass = sum(prior[a] for a in seen)
    tilted = {a: prior[a] * w for a, w in seen.items()}
    total = sum(tilted.values())
    share = ({a: v / total * mass for a, v in tilted.items()}
             if total > 0 else {a: prior[a] for a in seen})
    out = {}
    for a, p in prior.items():
        if a in share:
            n = float(agents[a]["n"])
            out[a] = (n * share[a] + prior_k * p) / (n + prior_k)
        else:
            out[a] = p
    tot = sum(out.values()) or 1.0
    return {a: w / tot for a, w in out.items()}


def maybe_update(journal, eta: float = 0.15,
                 min_interval_min: float = 45.0) -> None:
    state = _load()
    if time.time() - float(state.get("updated", 0)) < min_interval_min * 60:
        return
    try:
        update(journal, eta=eta)
    except Exception as e:
        log.warning(f"ewa update failed: {e}")
