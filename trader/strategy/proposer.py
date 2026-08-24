"""Genome proposals — how the population grows beyond its seeds.

Flow (REQUIREMENTS §5/§6): brain composes a genome for an
under-represented family → walk-forward gauntlet on live candles →
survivors enter PAPER probation, rejects are journaled with reasons.

Caps: ≤3 proposals/day (independent of mutation cap).
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone

from ..core.journal import Journal
from ..strategy.backtest import walk_forward
from ..strategy.genome import FAMILY_GENE_SPECS, Genome

log = logging.getLogger(__name__)

MAX_PROPOSALS_PER_DAY = 3
MIN_POPULATION = 6
FAMILY_REGIMES = {
    "ema_trend": ["TRENDING_UP", "TRENDING_DOWN"],
    "vwap_fade": ["RANGING"],
    "breakout_retest": ["TRENDING_UP", "TRENDING_DOWN", "RANGING"],
    "sweep_reversal": ["RANGING", "VOLATILE"],
    "rotation_momo": ["TRENDING_UP"],
}


class Proposer:
    def __init__(self, journal: Journal, cfg: dict, feed=None,
                 notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        self.rng = random.Random()

    def _proposals_today(self) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind IN "
            "('proposal_accepted','proposal_rejected') AND ts LIKE ?",
            (f"{day}%",))[0]["n"]
        return n

    def _family_gaps(self) -> list[str]:
        """Families either absent from the live population or under-covered."""
        rows = self.journal.list_strategies(["paper", "active", "demoted"])
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        max_active = int(self.cfg["strategies"]["max_active"])
        gaps = []
        for fam in FAMILY_GENE_SPECS:
            if fam not in counts:
                gaps.append(fam)
        if rows and len(rows) < min(MIN_POPULATION, max_active):
            # still room — add a second variant of the best-behaved family
            for fam, n in sorted(counts.items(), key=lambda x: x[1]):
                if n == min(counts.values()) and fam not in gaps:
                    gaps.append(fam)
        return gaps

    def _random_genome(self, family: str) -> Genome:
        spec = FAMILY_GENE_SPECS[family]
        params = {}
        for k, (typ, lo, hi, dflt) in spec.items():
            if typ is str:
                params[k] = dflt
            elif typ is int:
                params[k] = self.rng.randint(int(lo), int(hi))
            else:
                params[k] = round(self.rng.uniform(lo, hi), 4)
        regimes = FAMILY_REGIMES.get(family, ["RANGING"])
        return Genome(
            strategy_id=f"prop_{family}_{self.rng.randint(1000, 9999)}",
            family=family,
            hypothesis=self._hypothesis(family, params),
            invalidation="Demote on PF<0.85 over 20 trades or 6 straight losses.",
            regime_filter=frozenset(regimes),
            markets=frozenset({"futures"}),
            params=params)

    def _hypothesis(self, family: str, params: dict) -> str:
        from ..strategy.library import build_seed_population
        for st, _g in build_seed_population():
            if st.kind == family:
                return st.hypothesis
        return (f"{family} variant exploiting its documented inefficiency "
                f"with alternative parameters {params}.")

    def propose(self) -> dict:
        if self._proposals_today() >= MAX_PROPOSALS_PER_DAY:
            return {"proposed": 0, "reason": "daily proposal cap reached"}
        gaps = self._family_gaps()
        if not gaps:
            return {"proposed": 0, "reason": "population well-covered"}
        if self.feed is None:
            return {"proposed": 0, "reason": "no data feed for gauntlet"}

        family = gaps[0]
        best = None
        attempts = 0
        while attempts < 4:                      # try a few param draws
            attempts += 1
            g = self._random_genome(family)
            errs = Genome.validate(g)
            if errs:
                continue
            # gauntlet on the three majors (most liquid, longest history)
            results = []
            for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
                df = self.feed.fetch_ohlcv(sym, "15m", limit=900)
                if df is None or len(df) < 400:
                    continue
                results.append(walk_forward(g, df, self.cfg["risk"]))
            if not results:
                continue
            robust_n = sum(1 for r in results if r["robust"])
            total_test_trades = sum(r["test"].trades for r in results)
            score = (robust_n, total_test_trades)
            if best is None or score > best[0]:
                best = (score, g, results)

        if best is None:
            self.journal.log_brain_event("proposal_rejected", family,
                                         "no valid genome drawn")
            return {"proposed": 0, "reason": "gauntlet rejected all draws"}

        score, g, results = best
        accepted = score[0] >= 1 and score[1] >= 6
        detail = {
            "family": family, "params": g.params,
            "robust_variants": f"{score[0]}/3",
            "test_trades": score[1],
            "train_pfs": [round(r["train"].profit_factor, 2) for r in results],
            "test_pfs": [round(r["test"].profit_factor, 2) for r in results],
        }
        if not accepted:
            self.journal.log_brain_event("proposal_rejected", family, detail)
            return {"proposed": 0, "reason": f"gauntlet: {detail}"}

        from ..core.types import Strategy, StrategyState, new_id
        st = Strategy(id=new_id("strat"), name=f"{family} variant "
                      f"({g.params.get('z_entry') or g.params.get('adx_min') or 'v2'})",
                      kind=family, params=g.params,
                      state=StrategyState.PAPER,
                      description="brain proposal, gauntlet-passed",
                      origin="brain")
        st.hypothesis, st.invalidation = g.hypothesis, g.invalidation
        st.regime_filter, st.markets = set(g.regime_filter), set(g.markets)
        st.generation, st.parent_id = 1, "proposer"
        self.journal.upsert_strategy(st)
        self.journal.log_brain_event("proposal_accepted", st.id, detail)
        if self.notifier:
            self.notifier.send(f"🧬 New strategy proposed: {st.name} "
                               f"({family}) → paper probation")
        return {"proposed": 1, "strategy": st.as_dict(), "gauntlet": detail}
