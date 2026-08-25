"""Orchestrator — aggregates votes + strategy signals into one Decision.

No vetoes — except two explicit, evidence-based ones:
  1. higher-timeframe veto: strong 4h trend refuses counter-trend entries
     (soft bump at |s|>0.35, hard refusal at |s|≥htf_hard_veto)
  2. news blackout: while macro headlines churn, threshold rises

    net = Σ(conv' × |conv'|·confidence · w_agent · fit) / Σw
    where conv' is the journal-calibrated conviction (calibration.py).

Regime-fit scales each analyst's conviction; measured agent accuracy
(from validation replay + live outcomes) modulates weight.

Dynamic threshold: agreement tightens it (unanimous 0.18), conflict
loosens it (0.32). Output is always a Decision — executed or skipped
with reason.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from ..agents import calibration
from ..agents.base import Analyst
from ..agents.regime import classify, fit_multiplier
from ..core.journal import Journal
from ..core.types import (Action, Decision, Side, Snapshot, StrategySignal,
                          Vote, new_id)
from ..strategy import library as strat_lib

log = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = {"structure": 0.24, "flow": 0.15, "momentum": 0.17,
                    "value": 0.11, "rotation": 0.13,
                    "positioning": 0.11, "depth": 0.09}
STRATEGY_VOTE_WEIGHT = 0.45      # strategies speak louder than any analyst


def _measured_weights() -> tuple[dict, dict]:
    """Evidence-based weights from the validation harness, if available."""
    try:
        from ..agents.validate import load_weights
        w = load_weights()
        if w and w.get("base_weights"):
            fit = {}
            for agent, by_regime in (w.get("agents") or {}).items():
                fit[agent] = {r: (round(2 * (v["acc_1h"] - 0.5) + 0.6, 2)
                                  if v.get("acc_1h") is not None else 0.55)
                              for r, v in (by_regime.get("by_regime")
                                           or {}).items()}
            return w["base_weights"], fit
    except Exception:
        pass
    return {}, {}


def htf_trend_score(df_4h) -> float:
    """4h trend strength s ∈ [-1,+1]: EMA50 side × ADX-normalized slope."""
    import numpy as np
    if df_4h is None or len(df_4h) < 60:
        return 0.0
    c = df_4h["close"]
    ema50 = c.ewm(span=50, adjust=False).mean()
    dist = (float(c.iloc[-1]) - float(ema50.iloc[-1])) / (float(ema50.iloc[-1]) + 1e-12)
    slope = float(ema50.iloc[-1] - ema50.iloc[-13]) / (float(ema50.iloc[-1]) + 1e-12)
    atr = float((c.diff().abs().rolling(14).mean().iloc[-1]))
    norm = max(atr * 3 / float(c.iloc[-1]), 1e-6)      # ~how far is "far" in 4h terms
    s = np.tanh((0.7 * dist + 0.3 * slope) / norm)
    return float(max(-1.0, min(1.0, s)))


class Orchestrator:
    def __init__(self, analysts: list[Analyst], journal: Journal,
                 base_threshold: float = 0.24,
                 news_guard=None, cfg: dict | None = None):
        self.analysts = {a.name: a for a in analysts}
        self.journal = journal
        self.base_threshold = base_threshold
        self.news_guard = news_guard
        sc = ((cfg or {}).get("scouts") or {})
        self.htf_soft_bump = float(sc.get("htf_soft_bump", 0.07))
        self.htf_hard_veto = float(sc.get("htf_hard_veto", 0.80))
        self.calib_cfg = (sc.get("calibration") or {})
        self.base_weights = {**_DEFAULT_WEIGHTS, **_measured_weights()[0]}
        self.measured_fit = _measured_weights()[1]
        if any(a != d for a, d in zip(sorted(self.base_weights),
                                      sorted(_DEFAULT_WEIGHTS))):
            log.info(f"orchestrator weights: {self.base_weights}")
        self._acc_cache: tuple[float, dict] = (0.0, {})   # ts, {agent: mult}
        self._lock = threading.Lock()

    # ── accuracy → weight multiplier (bounded 0.6..1.4) ─────────────────
    def _accuracy_multipliers(self, ttl: float = 1800.0) -> dict[str, float]:
        now = time.time()
        with self._lock:
            ts, cache = self._acc_cache
            if now - ts < ttl:
                return cache
        try:
            rows = self.journal.agent_accuracy(since_hours=336)
            fresh = {}
            for r in rows:
                if r["n"] >= 8 and r["accuracy"] is not None:
                    fresh[r["agent"]] = max(0.6, min(1.4, 2.0 * r["accuracy"]))
        except Exception:
            fresh = {}
        with self._lock:
            self._acc_cache = (now, fresh)
        return fresh

    def decide(self, snap: Snapshot, population: list[tuple],
               entry_allowed: bool = True,
               blocked_reason: str = "") -> Decision:
        cycle_id = new_id("cyc")
        regime_info = classify(snap.df("15m"), snap.df("1h"))
        snap.regime = regime_info["regime"]
        snap.adx = regime_info["adx"]

        # ── guards ───────────────────────────────────────────────────────
        news = self.news_guard.check() if self.news_guard else \
            {"active": False, "why": ""}
        htf = htf_trend_score(snap.df("4h"))

        # ── collect votes ────────────────────────────────────────────────
        votes: list[Vote] = []
        acc_mults = self._accuracy_multipliers()
        cal_state = calibration.load() if \
            self.calib_cfg.get("enabled", True) else {}
        for name, analyst in self.analysts.items():
            try:
                v = analyst.evaluate(snap)
            except Exception as e:
                log.warning(f"analyst {name} failed on {snap.symbol}: {e}")
                continue
            if v is None:
                continue
            measured = (self.measured_fit.get(v.agent) or {}).get(snap.regime)
            v.meta["regime_fit"] = measured if measured is not None else \
                fit_multiplier(analyst.regime_affinity, snap.regime)
            v.meta["acc_mult"] = round(acc_mults.get(name, 1.0), 2)
            raw_c = v.conviction
            new_c, did = calibration.apply(
                cal_state, v.agent, v.conviction, v.confidence,
                int(self.calib_cfg.get("min_samples", 60)))
            if did:
                v.conviction = new_c
                v.meta["raw_conviction"] = round(raw_c, 3)
                v.meta["calibrated"] = True
            votes.append(v)

        sigs: list[StrategySignal] = []
        for st, genome in population:
            if not st.is_trade_eligible:
                continue
            if snap.market_type not in genome.markets:
                continue
            if snap.regime not in genome.regime_filter:
                continue
            try:
                sig = strat_lib.evaluate(genome, snap)
            except Exception as e:
                log.warning(f"strategy {st.id} failed on {snap.symbol}: {e}")
                continue
            if sig is not None:
                sigs.append(sig)

        # ── aggregate ────────────────────────────────────────────────────
        num = den = 0.0
        for v in votes:
            w = self.base_weights.get(v.agent, 0.10) * v.meta.get("acc_mult", 1.0)
            eff = v.conviction * abs(v.conviction) * v.confidence \
                * v.meta.get("regime_fit", 1.0)
            num += eff * w
            den += w
        for s in sigs:
            dirn = 1.0 if s.action == Action.BUY else -1.0
            num += dirn * s.confidence * STRATEGY_VOTE_WEIGHT
            den += STRATEGY_VOTE_WEIGHT
        net = num / den if den > 0 else 0.0

        # dynamic threshold via directional agreement
        directional = [v for v in votes if abs(v.conviction) > 0.05] + \
                      [None] * len(sigs)
        if len(directional) >= 2:
            agree = sum(
                1 for v in directional
                if (v.conviction if v else net) * (1 if net >= 0 else -1) > 0)
            frac = agree / len(directional)
        else:
            frac = 0.5
        threshold = self.base_threshold * (1.45 - 0.55 * frac)   # 0.18..0.32

        if news.get("active"):
            threshold += 0.08
            net *= 0.75
        veto_reason = ""
        if abs(htf) > 0.35 and snap.regime != "VOLATILE":
            opposing = (net < 0 < htf) or (net > 0 > htf)
            if opposing:
                if abs(htf) >= self.htf_hard_veto:
                    veto_reason = (f"4h trend {'UP' if htf > 0 else 'DOWN'} "
                                   f"s={htf:+.2f} — hard veto")
                    net = 0.0
                else:
                    threshold += self.htf_soft_bump

        if net > threshold:
            action = Action.BUY
        elif net < -threshold:
            action = Action.SELL
        else:
            action = Action.HOLD

        # ── signal cooldown: one decision per setup, not per minute ─────
        # A persisting condition would re-fire identical signals every
        # cycle, flooding the journal with pseudo-replicated outcomes.
        # Re-arm only after 45 min, an opposite signal, or a HOLD break.
        if action != Action.HOLD:
            try:
                last = self.journal.query(
                    "SELECT action, ts FROM decisions WHERE symbol=? "
                    "AND action!='HOLD' ORDER BY ts DESC LIMIT 1",
                    (snap.symbol,))
                if last and last[0]["action"] == action.value:
                    age_min = (datetime.now(timezone.utc)
                               - datetime.fromisoformat(last[0]["ts"])
                               ).total_seconds() / 60
                    if age_min < 45:
                        action = Action.HOLD
                        net = _clean(net)  # keep score for transparency
                        self.journal.log_control_event(
                            "signal_cooldown", "orchestrator",
                            detail={"symbol": snap.symbol,
                                    "since": last[0]["ts"][:19],
                                    "action": action.value})
            except Exception as e:
                log.debug(f"cooldown check failed: {e}")

        # NaN from indicator edge cases must never reach SQLite (it becomes
        # NULL → NOT NULL violation → crash-loop → duplicate entries)
        def _clean(x: float, default: float = 0.0) -> float:
            x = float(x)
            return default if (x != x or x in (float("inf"), float("-inf"))) \
                else round(x, 6)

        net = _clean(net)
        threshold = max(_clean(threshold, 0.24), 0.05)
        confidence = min(0.95, max(0.30, _clean(
            abs(net) / max(threshold, 1e-9) * 0.62 * (0.7 + 0.3 * frac), 0.3)))
        d = Decision(
            id=new_id("dec"), cycle_id=cycle_id, symbol=snap.symbol,
            action=action, score=_clean(net), threshold=threshold,
            confidence=confidence,
            votes=[v.as_dict() for v in votes],
            strategy_signals=[vars(s) | {"action": s.action.value}
                              for s in sigs])
        d.executed = False
        skip_bits = []
        if veto_reason:
            skip_bits.append(veto_reason)
        if action != Action.HOLD and not entry_allowed:
            skip_bits.append(blocked_reason or "entries not allowed")
        d.skip_reason = "; ".join(skip_bits)
        return d

    def journalize(self, snap: Snapshot, decision: Decision,
                   market_type: str, mode: str) -> None:
        self.journal.log_cycle(snap, decision.cycle_id, mode)
        votes = [Vote(**{**v, "side": Side(v["side"])})
                 for v in decision.votes]
        self.journal.log_votes(decision.cycle_id, decision.symbol, votes)
        self.journal.log_decision(decision)
        # every directional decision — taken OR skipped — becomes a
        # falsifiable prediction with resolved outcomes. Skipped setups are
        # the majority of evidence; without them the learning loop starves.
        if decision.action != Action.HOLD:
            self.journal.schedule_outcome(
                decision.id, decision.cycle_id, decision.symbol,
                decision.ts, decision.action.value, snap.price)
