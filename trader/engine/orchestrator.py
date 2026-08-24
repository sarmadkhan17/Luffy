"""Orchestrator — aggregates votes + strategy signals into one Decision.

No vetoes. Regime-fit scales each analyst's conviction; measured agent
accuracy (from journal) modulates weight. Strategy signals contribute
their confidence as conviction in their direction.

    net = Σ(conviction × |conviction|·confidence · w_agent · fit) / Σw

Dynamic threshold: agreement tightens it (unanimous 0.18), conflict loosens
it (0.32). Output is always a Decision — executed or skipped with reason.
"""
from __future__ import annotations

import logging
import threading
import time

from ..agents.base import Analyst
from ..agents.regime import classify, fit_multiplier
from ..core.journal import Journal
from ..core.types import (Action, Decision, Side, Snapshot, StrategySignal,
                          Vote, new_id)
from ..strategy import library as strat_lib

log = logging.getLogger(__name__)

BASE_WEIGHTS = {"structure": 0.28, "flow": 0.22, "momentum": 0.20,
                "value": 0.15, "rotation": 0.15}
STRATEGY_VOTE_WEIGHT = 0.45      # strategies speak louder than any analyst


class Orchestrator:
    def __init__(self, analysts: list[Analyst], journal: Journal,
                 base_threshold: float = 0.24):
        self.analysts = {a.name: a for a in analysts}
        self.journal = journal
        self.base_threshold = base_threshold
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

        # ── collect votes ────────────────────────────────────────────────
        votes: list[Vote] = []
        acc_mults = self._accuracy_multipliers()
        for name, analyst in self.analysts.items():
            try:
                v = analyst.evaluate(snap)
            except Exception as e:
                log.warning(f"analyst {name} failed on {snap.symbol}: {e}")
                continue
            if v is None:
                continue
            v.meta["regime_fit"] = fit_multiplier(analyst.regime_affinity, snap.regime)
            v.meta["acc_mult"] = round(acc_mults.get(name, 1.0), 2)
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
            w = BASE_WEIGHTS.get(v.agent, 0.15) * v.meta.get("acc_mult", 1.0)
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

        if net > threshold:
            action = Action.BUY
        elif net < -threshold:
            action = Action.SELL
        else:
            action = Action.HOLD

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
        if action != Action.HOLD and not entry_allowed:
            d.skip_reason = blocked_reason or "entries not allowed"
        return d

    def journalize(self, snap: Snapshot, decision: Decision,
                   market_type: str, mode: str) -> None:
        self.journal.log_cycle(snap, decision.cycle_id, mode)
        votes = [Vote(**{**v, "side": Side(v["side"])})
                 for v in decision.votes]
        self.journal.log_votes(decision.cycle_id, decision.symbol, votes)
        self.journal.log_decision(decision)
