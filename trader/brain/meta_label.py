"""Meta-labeling — the secondary model that decides whether the primary
ensemble's signal deserves capital (López de Prado, AFML ch.3/16).

Primary model = the orchestrator's vote+signal ensemble (direction).
Secondary model = a logistic P(win | decision features) trained on the
journal's own resolved outcomes (incl. shadow labels from near-threshold
HOLDs). LIVE deployment with hard bounds (operator choice):

  veto   : P < META_FLOOR (0.45) → entry skipped, still outcome-graded
  sizing : size_mult = clamp((P − 0.35)/0.45, 0.25, 1.0) — shrink-only
  kill   : ≥20 live meta-judged directional outcomes with win-rate < 0.40
           → auto-disable until the next refit proves recovery

Features are reconstructible identically at train time (journal joins) and
decision time (live context): score_ratio, agreement frac, regime one-hot,
|htf|, adx, hour-of-day (sin/cos), strategy count + agreement, news state.
Model + live stats persist in data/meta_model.json.
"""
from __future__ import annotations

import json
import logging
import math
import time

from ..core.config import ROOT

log = logging.getLogger(__name__)

PATH = ROOT / "data" / "meta_model.json"
META_FLOOR = 0.45           # veto below this
META_MIN_TRAIN = 40         # outcomes before the model may act
META_LIVE_MIN_WR = 0.40     # auto-disable below this over >=20 live calls
REGIMES = ("RANGING", "TRENDING_UP", "TRENDING_DOWN", "VOLATILE")

FEATS = ["score_ratio", "frac", *("reg_" + r for r in REGIMES),
         "abs_htf", "adx_n", "hour_sin", "hour_cos",
         "n_sigs_n", "sig_agree", "news", "bias"]


# ── feature builders ─────────────────────────────────────────────────────
def _hour_feats(ts: str) -> tuple[float, float]:
    try:
        h = float(ts[11:13]) + float(ts[14:16]) / 60.0
    except Exception:
        h = 0.0
    ang = h / 24.0 * 2.0 * math.pi
    return math.sin(ang), math.cos(ang)


def _regime_feats(regime: str) -> list[float]:
    return [1.0 if regime == r else 0.0 for r in REGIMES]


def features(score: float, threshold: float, frac: float, regime: str,
             htf: float, adx: float, ts: str, n_sigs: int,
             sig_agree: float, news_active: bool) -> list[float]:
    hs, hc = _hour_feats(ts)
    return [
        min(abs(score) / max(threshold, 1e-9), 3.0),
        max(0.0, min(1.0, frac)),
        *_regime_feats(regime),
        min(abs(htf), 1.0),
        min(adx / 50.0, 1.5),
        hs, hc,
        min(n_sigs, 5) / 5.0,
        sig_agree,                      # 1 agree / 0 oppose / 0.5 none
        1.0 if news_active else 0.0,
        1.0,                            # bias
    ]


def _frac_from_votes(vote_rows, action: str, n_sigs: int,
                     sig_agree_sign: int) -> float:
    """Recompute the orchestrator's agreement frac from persisted votes."""
    directional = [v for v in vote_rows if abs(float(v["conviction"])) > 0.05]
    n = len(directional) + n_sigs
    if n == 0:
        return 0.5
    agree = 0
    for v in directional:
        if (float(v["conviction"]) > 0) == (action == "BUY"):
            agree += 1
    # signals always counted as agreeing with the net direction
    if n_sigs and sig_agree_sign > 0:
        agree += n_sigs
    return agree / n


# ── model io ─────────────────────────────────────────────────────────────
def load() -> dict:
    if PATH.exists():
        try:
            return json.loads(PATH.read_text())
        except Exception:
            pass
    return {"w": None, "n": 0, "updated": 0.0, "disabled": False,
            "live": {"judged": 0, "wins": 0}}


def _save(state: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(state, indent=2))


def _sigmoid(z: float) -> float:
    if z > 35:
        return 1.0
    if z < -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _fit(X: list[list[float]], y: list[float], iters: int = 600,
         lr: float = 0.35, l2: float = 1e-3) -> list[float]:
    import numpy as np
    Xm = np.array(X, dtype=float)
    ym = np.array(y, dtype=float)
    n, d = Xm.shape
    w = np.zeros(d)
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Xm @ w)))
        grad = Xm.T @ (p - ym) / n + l2 * w
        w -= lr * grad
    return [round(float(x), 5) for x in w]


def _brier(w: list[float], X: list[list[float]], y: list[float]) -> float:
    if not X:
        return 1.0
    s = 0.0
    for x, t in zip(X, y):
        p = _sigmoid(sum(a * b for a, b in zip(w, x)))
        s += (p - t) ** 2
    return round(s / len(X), 4)


# ── training ─────────────────────────────────────────────────────────────
def refit(journal) -> dict:
    """Train on all resolved outcomes (directional + shadow leans),
    time-ordered 70/30 split, and refresh the live kill-switch stats."""
    rows = journal.query("""
        SELECT d.id, d.cycle_id, d.ts, d.action, d.score, d.threshold,
               d.signals_json, c.regime, c.adx,
               o.action AS lean, o.correct_4h AS ok
        FROM outcomes o
        JOIN decisions d ON d.id = o.decision_id
        JOIN cycles c ON c.id = d.cycle_id
        WHERE o.resolved_at IS NOT NULL AND o.correct_4h IS NOT NULL
        ORDER BY d.ts""")
    if len(rows) < META_MIN_TRAIN:
        state = load()
        state["n"] = len(rows)
        state["updated"] = time.time()
        _save(state)
        return state

    votes_by_cycle: dict[str, list] = {}
    for v in journal.query(
            "SELECT cycle_id, agent, conviction, meta FROM votes"):
        votes_by_cycle.setdefault(v["cycle_id"], []).append(v)

    X, y = [], []
    for r in rows:
        vs = votes_by_cycle.get(r["cycle_id"], [])
        try:
            sigs = json.loads(r["signals_json"] or "[]")
        except Exception:
            sigs = []
        n_sigs = len(sigs)
        lean_buy = (r["lean"] or r["action"]) == "BUY"
        sig_agree = (sum(1 for s in sigs
                         if (s.get("action") == "BUY") == lean_buy) / n_sigs
                     if n_sigs else 0.5)
        htf = 0.0
        news = False
        hts = []
        for v in vs:
            try:
                m = json.loads(v["meta"] or "{}")
            except Exception:
                m = {}
            if m.get("htf") is not None:
                hts.append(float(m["htf"]))
            news = news or bool(m.get("news_blackout"))
        if hts:
            htf = sum(hts) / len(hts)
        lean_action = r["lean"] or r["action"]
        frac = _frac_from_votes(vs, lean_action, n_sigs,
                                1 if lean_buy else -1)
        X.append(features(r["score"], r["threshold"], frac,
                          r["regime"] or "RANGING", htf, float(r["adx"] or 0),
                          r["ts"], n_sigs, sig_agree, news))
        y.append(float(r["ok"]))

    cut = int(len(X) * 0.7)
    w = _fit(X[:cut], y[:cut])
    btr = _brier(w, X[:cut], y[:cut])
    bte = _brier(w, X[cut:], y[cut:]) if len(X) - cut >= 8 else btr

    # live kill-switch: win-rate of meta-JUDGED (non-vetoed) live calls
    judged = journal.query(
        "SELECT COUNT(*) n, AVG(o.correct_4h) wr FROM decisions d "
        "JOIN outcomes o ON o.decision_id = d.id "
        "WHERE d.meta_p IS NOT NULL AND d.meta_p >= ? "
        "  AND o.resolved_at IS NOT NULL AND o.correct_4h IS NOT NULL",
        (META_FLOOR,))
    live = {"judged": judged[0]["n"] or 0,
            "wins": round((judged[0]["wr"] or 0) * (judged[0]["n"] or 0))}
    disabled = bool(live["judged"] >= 20 and (judged[0]["wr"] or 0)
                    < META_LIVE_MIN_WR)
    if disabled and not load().get("disabled"):
        log.warning(f"meta-model AUTO-DISABLED: live WR "
                    f"{judged[0]['wr']:.2f} over {live['judged']} calls")

    state = {"w": w, "n": len(X), "brier_train": btr, "brier_test": bte,
             "disabled": disabled, "live": live, "updated": time.time()}
    _save(state)
    log.info(f"meta-model refit: n={len(X)} brier {btr}/{bte} "
             f"live {live} disabled={disabled}")
    return state


def maybe_refit(journal, hours: float = 6.0) -> None:
    state = load()
    if time.time() - float(state.get("updated", 0)) < hours * 3600:
        return
    try:
        refit(journal)
    except Exception as e:
        log.warning(f"meta refit failed: {e}")


# ── live judgment ────────────────────────────────────────────────────────
_cache: dict = {"m": None, "ts": 0.0}


def _model() -> list[float] | None:
    now = time.time()
    if now - _cache["ts"] > 300:
        _cache["m"] = load().get("w")
        _cache["ts"] = now
    return _cache["m"]


def judge(feat: list[float]) -> float | None:
    """P(win) for a directional decision, or None when the model is not
    ready / auto-disabled (passthrough — primary ensemble stands alone)."""
    state = load()
    if state.get("disabled"):
        return None
    w = _model()
    if not w or len(w) != len(feat):
        return None
    return round(_sigmoid(sum(a * b for a, b in zip(w, feat))), 3)


def size_mult(p: float) -> float:
    """Shrink-only position sizing from P(win): [0.25, 1.0]× base."""
    return round(max(0.25, min(1.0, (p - 0.35) / 0.45)), 2)
