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

from ..brain.llm import BrainLLM
from ..brain.tv import FAMILY_TV, TVClient
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
                 notifier=None, llm: BrainLLM | None = None,
                 tv: TVClient | None = None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        self.rng = random.Random()
        self.llm = llm
        self.tv = tv or TVClient(
            interval="1h",
            period="6mo",
            min_oos_trades=int(cfg["strategies"].get("tv_min_oos_trades", 5)),
            require_positive_oos=bool(
                cfg["strategies"].get("tv_require_positive_oos", True)))

    def _proposals_today(self) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind IN "
            "('proposal_accepted','proposal_rejected') AND ts LIKE ?",
            (f"{day}%",))[0]["n"]
        return n

    def _family_gaps(self) -> list[str]:
        """Families absent from the TRADE-ELIGIBLE population.
        Demoted/retired strategies cover nothing — their families are
        exactly the ones needing new candidates."""
        rows = self.journal.list_strategies(["paper", "active"])
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        max_active = int(self.cfg["strategies"]["max_active"])
        gaps = []
        for fam in FAMILY_GENE_SPECS:
            if fam not in counts:
                gaps.append(fam)
        if rows and len(rows) < min(MIN_POPULATION, max_active) and not gaps:
            # room left — the whole pipeline may try every family and the
            # best dual-gauntlet survivor joins the population
            return list(FAMILY_GENE_SPECS.keys())
        return gaps

    def _llm_genome(self, family: str, tv_context: dict) -> Genome | None:
        """DeepSeek composes a genome informed by TV's read of the market."""
        if not (self.llm and self.llm.available):
            return None
        spec = FAMILY_GENE_SPECS[family]
        genes_doc = {k: f"{v[1]}..{v[2]} (default {v[3]})"
                     for k, v in spec.items()}
        prompt = (
            f"Compose ONE trading strategy of family '{family}'.\n"
            f"Gene schema (name: range): {json.dumps(genes_doc)}\n"
            f"TradingView walk-forward read of the current market:\n"
            f"{json.dumps(tv_context)[:800]}\n\n"
            "Choose genes suited to that read. Output JSON:\n"
            '{"params":{...},"hypothesis":"one sentence citing the market '
            'condition","name_hint":"3 words"}')
        raw = self.llm.chat_json(prompt, deep=False)
        if not raw or not isinstance(raw.get("params"), dict):
            return None
        g = Genome(strategy_id=f"prop_{family}_{self.rng.randint(1000,9999)}",
                   family=family, hypothesis=raw.get("hypothesis") or
                   f"{family} composed for current TV regime.",
                   invalidation="Demote on PF<0.85/20 trades or 6 straight losses.",
                   regime_filter=frozenset(FAMILY_REGIMES.get(family, ["RANGING"])),
                   markets=frozenset({"futures"}), params=raw["params"])
        if Genome.validate(g):
            return None
        return g

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
        for _st, g in build_seed_population():
            if g.family == family:
                return g.hypothesis
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

        # TV context first: how does every family read the current market?
        tv_context = {}
        try:
            tv_context = self.tv.compare_families("BTC/USDT")
        except Exception as e:
            log.warning(f"tv context failed: {e}")

        best = None
        for family in gaps:                  # sweep families — first pass wins
            candidates = []
            llm_g = self._llm_genome(family, tv_context)
            if llm_g:
                candidates.append(("llm", llm_g))
            for _ in range(2):
                candidates.append(("random", self._random_genome(family)))

            for origin, g in candidates:
                if Genome.validate(g):
                    continue
                # ── gauntlet 1: TRADINGVIEW (primary judge) ──────────
                # 1y hourly walk-forward, OOS profitability + quality +
                # fold consistency. 15m/30d internal windows are too short
                # to validate anything — TV is the statistical authority.
                tv = self.tv.walk_forward("BTC/USDT", family)
                if not tv.get("valid"):
                    self.journal.log_brain_event(
                        "proposal_rejected", family,
                        {"origin": origin, "stage": "tradingview",
                         "params": g.params,
                         "tv": {k: tv.get(k) for k in
                                ("verdict", "checks", "oos_return_pct",
                                 "oos_trades", "oos_sharpe",
                                 "positive_folds")}})
                    continue

                # ── gauntlet 2: internal sanity (loose disaster check) ──
                # only rejects catastrophes on our own data; strict
                # statistical judgment already happened above.
                results = []
                for sym in ("BTC/USDT", "SOL/USDT"):
                    df = self.feed.fetch_ohlcv(sym, "15m", limit=2900)
                    if df is None or len(df) < 400:
                        continue
                    results.append(walk_forward(g, df, self.cfg["risk"]))
                test_pfs = [r["test"].profit_factor for r in results
                            if r["test"].trades >= 2]
                if test_pfs and min(test_pfs) < 0.5:
                    self.journal.log_brain_event(
                        "proposal_rejected", family,
                        {"origin": origin, "stage": "internal_sanity",
                         "params": g.params, "test_pfs": test_pfs})
                    continue

                score = ((1 if tv.get("beats_buy_hold") else 0),
                         tv.get("oos_return_pct") or 0)
                cand = (score, g, results, tv, origin, family)
                if best is None or score > best[0]:
                    best = cand

        if best is None:
            return {"proposed": 0,
                    "reason": "dual gauntlet (internal+TV) rejected all — "
                              "see proposal_rejected events"}

        score, g, results, tv, origin, family = best
        detail = {
            "family": family, "origin": origin, "params": g.params,
            "internal": {"robust_variants": f"{score[0]}/3",
                         "train_pfs": [round(r["train"].profit_factor, 2)
                                       for r in results],
                         "test_pfs": [round(r["test"].profit_factor, 2)
                                      for r in results]},
            "tradingview": {k: tv.get(k) for k in
                            ("tv_strategy", "verdict", "robustness",
                             "oos_return_pct", "oos_trades", "oos_win_rate",
                             "oos_sharpe", "beats_buy_hold")},
        }

        from ..core.types import Strategy, StrategyState, new_id
        st = Strategy(id=new_id("strat"), name=f"{family} variant "
                      f"({g.params.get('z_entry') or g.params.get('adx_min') or 'v2'})",
                      kind=family, params=g.params,
                      state=StrategyState.PAPER,
                      description="brain proposal, dual-gauntlet passed",
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
