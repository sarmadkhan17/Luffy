"""Learned conviction calibration — replaces hand-tuned magnitudes.

Each analyst's raw vote encodes guessed constants (+0.45 spring, +0.35
BOS...). The journal already records every vote and resolves it against
the 4h forward return (outcomes.correct_4h). We fit, per agent, a
logistic map  P(correct) = σ(w0 + w1·z)  where z = conv·|conv|·conf is
exactly the magnitude term the orchestrator aggregates.

The recalibrated vote keeps the scout's DIRECTION but re-scales its
magnitude to an empirical probability: conv' = ±(2P−1). A spring signal
that historically wins 62% of the time becomes ±0.24 regardless of how
excited the hand-tuned constant was.

Shrinkage: model output is blended toward 0.5 with pseudo-count k=25 and
below min_samples the vote passes through untouched. Refit is cheap
(pure numpy gradient descent) and runs every refit_hours from the kernel.
"""
from __future__ import annotations

import json
import logging
import math
import time

from ..core.config import ROOT

log = logging.getLogger(__name__)

PATH = ROOT / "data" / "agent_calibration.json"
SHRINK_K = 25.0


def _fit_logistic(zs: list[float], ys: list[float],
                  iters: int = 800, lr: float = 1.0,
                  l2: float = 1e-3) -> tuple[float, float]:
    w0, w1 = 0.0, 0.0
    n = len(zs)
    for _ in range(iters):
        g0 = g1 = 0.0
        for z, y in zip(zs, ys):
            p = 1.0 / (1.0 + math.exp(-(w0 + w1 * z)))
            e = p - y
            g0 += e
            g1 += e * z
        w0 -= lr * (g0 / n + l2 * w0)
        w1 -= lr * (g1 / n + l2 * w1)
    return w0, w1


def collect_training_data(journal) -> dict[str, tuple[list[float], list[float]]]:
    """agent -> ([z...], [correct...]) from resolved 4h outcomes."""
    rows = journal.query("""
        SELECT v.agent AS agent, v.conviction AS c, v.confidence AS cf,
               o.correct_4h AS y
        FROM votes v
        JOIN outcomes o ON o.symbol=v.symbol AND o.cycle_id=v.cycle_id
         AND o.resolved_at IS NOT NULL
        WHERE v.conviction NOT IN (0) AND o.correct_4h IS NOT NULL
          AND ABS(v.conviction) > 0.05
    """)
    data: dict[str, tuple[list[float], list[float]]] = {}
    zs: dict[str, list[float]] = {}
    ys: dict[str, list[float]] = {}
    for r in rows:
        z = abs(float(r["c"]) * abs(float(r["c"])) * float(r["cf"]))
        zs.setdefault(r["agent"], []).append(min(z, 1.0))
        ys.setdefault(r["agent"], []).append(float(r["y"]))
    for a in zs:
        data[a] = (zs[a], ys[a])
    return data


def refit(journal, min_samples: int = 60) -> dict:
    """Fit calibrations for every agent with enough evidence."""
    state = load()
    data = collect_training_data(journal)
    changed = []
    for agent, (zs, ys) in data.items():
        n = len(zs)
        if n < min_samples:
            state.pop(agent, None)
            continue
        w0, w1 = _fit_logistic(zs, ys)
        prev = state.get(agent, {})
        if prev and abs(prev.get("w0", 9) - w0) < 0.01 \
                and abs(prev.get("w1", 9) - w1) < 0.01:
            continue
        state[agent] = {"w0": round(w0, 4), "w1": round(w1, 4),
                        "n": n,
                        "acc": round(sum(ys) / n, 3),
                        "updated": time.time()}
        changed.append(agent)
    state["_meta"] = {"updated": time.time(), "changed": changed}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(state, indent=2))
    if changed:
        log.info(f"calibration refit: {changed}")
    return state


def maybe_refit(journal, min_samples: int = 60, refit_hours: float = 6.0) -> None:
    state = load()
    last = (state.get("_meta") or {}).get("updated", 0)
    if time.time() - last < refit_hours * 3600:
        return
    try:
        refit(journal, min_samples=min_samples)
    except Exception as e:
        log.warning(f"calibration refit failed: {e}")


def load() -> dict:
    if PATH.exists():
        try:
            return json.loads(PATH.read_text())
        except Exception:
            return {}
    return {}


def apply(state: dict, agent: str, conviction: float,
          confidence: float, min_samples: int = 60) -> tuple[float, bool]:
    """Return (calibrated_conviction, was_calibrated).

    Direction preserved; magnitude becomes shrunk empirical probability.
    """
    c = conviction
    if abs(c) <= 0.05:
        return c, False
    fit = state.get(agent)
    if not fit or fit.get("n", 0) < min_samples:
        return c, False
    z = min(abs(c) * abs(c) * confidence, 1.0)
    try:
        p = 1.0 / (1.0 + math.exp(-(fit["w0"] + fit["w1"] * z)))
    except OverflowError:
        p = 0.0 if fit["w0"] + fit["w1"] * z < 0 else 1.0
    n, k = fit["n"], SHRINK_K
    p_final = (n * p + k * 0.5) / (n + k)
    mag = max(0.05, min(0.85, 2.0 * p_final - 1.0))
    return math.copysign(mag, c), True
