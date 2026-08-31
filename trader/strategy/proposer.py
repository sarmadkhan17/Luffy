"""Genome proposals — how the population grows beyond its seeds.

Flow (REQUIREMENTS §5/§6): brain composes a genome for an
under-represented family → walk-forward gauntlet on live candles →
survivors enter PAPER probation, rejects are journaled with reasons.

Caps: ≤3 proposals/day (independent of mutation cap).
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone

from ..brain.llm import BrainLLM
from ..brain.tv import FAMILY_TV, TVClient
from ..core.journal import Journal
from ..strategy import evidence
from ..strategy.genome import FAMILY_GENE_SPECS, Genome, register_family

log = logging.getLogger(__name__)

MAX_PROPOSALS_PER_DAY = 3
MAX_INVENTIONS_PER_DAY = 1
INVENTED_FAMILIES_PATH = "data/invented_families.json"
MIN_POPULATION = 6
FAMILY_REGIMES = {
    "ema_trend": ["TRENDING_UP", "TRENDING_DOWN"],
    "vwap_fade": ["RANGING"],
    "breakout_retest": ["TRENDING_UP", "TRENDING_DOWN", "RANGING"],
    "sweep_reversal": ["RANGING", "VOLATILE"],
    "rotation_momo": ["TRENDING_UP"],
}


def _probe_frames() -> dict:
    """Synthetic but realistically shaped OHLCV for the invention smoke test.

    Random-walk close with a real high/low/volume envelope, 400 bars, on every
    timeframe key an evaluator might reach for — enough for indicator warmups
    (EMA200 needs 200) so the evaluator's body actually executes.
    """
    import numpy as _np
    import pandas as _pd

    def _frame(n: int, freq: str) -> _pd.DataFrame:
        rng = _np.random.default_rng(7)
        close = 50000 * _np.exp(_np.cumsum(rng.normal(0, 0.002, n)))
        spread = close * 0.001
        return _pd.DataFrame({
            "ts": _pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"),
            "open": close, "high": close + spread, "low": close - spread,
            "close": close,
            "volume": rng.uniform(100, 1000, n),
        })

    f15 = _frame(400, "15min")
    return {"5m": _frame(400, "5min"), "15m": f15,
            "1h": _frame(400, "1h"), "4h": _frame(400, "4h"),
            "BTC_1h": _frame(400, "1h")}


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
        """Count only ACCEPTED proposals against the daily cap.

        Rejections used to count too — and a single propose() call logs one
        rejection per candidate per family (up to 24), so the cap of 3 was
        exhausted the first time the gauntlet did its job.
        """
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind='proposal_accepted' "
            "AND ts LIKE ?", (f"{day}%",))[0]["n"]
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
        gaps = [fam for fam in FAMILY_GENE_SPECS if fam not in counts]
        if gaps:
            return gaps
        # No empty family, but still headroom under max_active: sweep
        # everything and let the walk-forward pick the best survivor.
        # The old guard also required len(rows) < MIN_POPULATION (6) which,
        # with max_active also 6, almost never held — so the proposer
        # short-circuited on "population well-covered" and proposed nothing.
        if len(rows) < max_active:
            return list(FAMILY_GENE_SPECS.keys())
        return []

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

    # ── invention helpers ────────────────────────────────────────────────

    def _inventions_today(self) -> int:
        """Both outcomes count here: an invention costs a deep reasoner call
        plus a full multi-symbol gauntlet whether or not it survives."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind IN "
            "('invention_accepted','invention_rejected') AND ts LIKE ?",
            (f"{day}%",))[0]["n"]
        return n

    def _sandbox_evaluator(self, code_str: str, family_name: str):
        """Exec LLM-generated evaluator in restricted namespace. Returns callable or None."""
        from ..strategy.library import SAFE_NS
        ns = dict(SAFE_NS)
        try:
            exec(compile(code_str, "<invented>", "exec"), ns)
        except SyntaxError as e:
            log.warning(f"invention sandbox syntax error ({family_name}): {e}")
            return None
        fn = ns.get(f"eval_{family_name}")
        if not callable(fn):
            log.warning(f"invention: eval_{family_name} not found after exec")
            return None
        # Smoke-test against BOTH an empty snapshot (guard clauses) and a
        # realistically shaped one. The empty case alone proved nothing: every
        # well-guarded evaluator returns None on `dfs={}` without ever
        # executing its body.
        from ..core.types import Snapshot as _Snapshot, StrategySignal
        from .genome import PARAM_DEFAULTS
        probe_genome = Genome(
            strategy_id="test", family=family_name,
            hypothesis="test", invalidation="test",
            regime_filter=frozenset(), markets=frozenset({"futures"}),
            params=dict(PARAM_DEFAULTS.get(family_name, {})))
        for label, dfs in (("empty", {}), ("shaped", _probe_frames())):
            try:
                result = fn(probe_genome,
                            _Snapshot(symbol="BTC/USDT",
                                      ts="2026-01-01T00:00:00",
                                      price=50000.0, dfs=dfs,
                                      market_type="futures"))
            except Exception as e:
                log.warning(f"invention smoke-test ({family_name}/{label}) "
                            f"raised: {e}")
                return None
            if result is not None and not isinstance(result, StrategySignal):
                log.warning(f"invention: eval_{family_name} returned "
                            f"{type(result).__name__}, not StrategySignal")
                return None
        return fn

    def _parse_gene_spec(self, raw: dict) -> dict:
        """Convert LLM gene_spec {name: [type_str, min, max, default]} to FAMILY_GENE_SPECS format."""
        out = {}
        type_map = {"float": float, "int": int, "str": str}
        for k, v in raw.items():
            if not isinstance(v, list) or len(v) != 4:
                continue
            typ = type_map.get(str(v[0]).lower())
            if typ is None:
                continue
            try:
                lo = typ(v[1]) if typ is not str else None
                hi = typ(v[2]) if typ is not str else None
                dflt = typ(v[3]) if typ is not str else v[3]
            except (TypeError, ValueError):
                continue
            out[k] = (typ, lo, hi, dflt)
        return out

    def _invent_family(self, tv_context: dict):
        """Ask DeepSeek to invent a brand-new strategy family with code."""
        if not (self.llm and self.llm.available):
            return None
        existing = ", ".join(sorted(FAMILY_GENE_SPECS.keys()))
        prompt = (
            "You are inventing a NEW crypto futures trading strategy family for an "
            "automated system.\n\n"
            f"Current market context:\n{json.dumps(tv_context)[:600]}\n\n"
            "Available indicator functions (pandas-based):\n"
            "  ema(series, period) -> Series\n"
            "  rsi(series, period) -> Series   (values 0-100)\n"
            "  adx(df) -> float\n"
            "  atr(df) -> float\n"
            "  anchored_vwap(df, bars) -> float\n"
            "  zscore(series, window) -> float\n\n"
            "The evaluator receives:\n"
            "  g.params['param_name'] for each gene\n"
            "  snap.df('15m') -> DataFrame|None  (cols: open,high,low,close,volume)\n"
            "  snap.dfs -> dict of DataFrames keyed by 'SYMBOL_TF'\n\n"
            f"Do NOT reuse existing families: {existing}.\n"
            "Invent something genuinely different — e.g. funding-rate exhaustion, "
            "open-interest divergence, volatility regime flip, candle-pattern fade, "
            "session-open gap fill, cross-asset correlation break, etc.\n\n"
            "Output JSON only:\n"
            "{\n"
            '  "family_name": "snake_case_name",\n'
            '  "gene_spec": {"param": ["float"|"int", min, max, default], ...},\n'
            '  "param_defaults": {"param": value, ...},\n'
            '  "regimes": ["TRENDING_UP"|"TRENDING_DOWN"|"RANGING"|"VOLATILE"],\n'
            '  "hypothesis": "≥60 chars naming the inefficiency",\n'
            '  "invalidation": "≥20 chars — how this strategy dies",\n'
            '  "evaluator_code": "def eval_{family_name}(g, snap):\\n    ..."\n'
            "  // Must return StrategySignal(...) or None\n"
            "  // gene_spec: 2-4 params; use Action.BUY or Action.SELL\n"
            "  // guard: if df is None or len(df) < N: return None\n"
            "}"
        )
        raw = self.llm.chat_json(prompt, deep=True)
        if not isinstance(raw, dict):
            return None
        family_name = (raw.get("family_name") or "").strip().lower().replace(" ", "_")
        if not family_name or family_name in FAMILY_GENE_SPECS:
            log.info(f"invention: skipped — name empty or collision ({family_name})")
            return None
        gene_spec_raw = raw.get("gene_spec")
        code_str = raw.get("evaluator_code", "")
        param_defaults = raw.get("param_defaults") or {}
        regimes = raw.get("regimes") or ["RANGING"]
        hypothesis = raw.get("hypothesis", "")
        invalidation = raw.get("invalidation", "Demote on PF<0.85/20 trades or 6 straight losses.")
        if not isinstance(gene_spec_raw, dict) or not code_str:
            return None
        gene_spec = self._parse_gene_spec(gene_spec_raw)
        if not gene_spec:
            log.warning(f"invention: gene_spec parse failed for {family_name}")
            return None
        # fill defaults from spec if missing
        for k, (typ, lo, hi, dflt) in gene_spec.items():
            if k not in param_defaults:
                param_defaults[k] = dflt
        # sandbox the code
        evaluator_fn = self._sandbox_evaluator(code_str, family_name)
        if evaluator_fn is None:
            return None
        # temporarily register so Genome.validate() passes
        register_family(family_name, gene_spec, param_defaults)
        g = Genome(
            strategy_id=f"inv_{family_name}_{self.rng.randint(1000, 9999)}",
            family=family_name,
            hypothesis=hypothesis,
            invalidation=invalidation,
            regime_filter=frozenset(regimes),
            markets=frozenset({"futures"}),
            params=param_defaults)
        errs = Genome.validate(g)
        if errs:
            log.warning(f"invention genome invalid ({family_name}): {errs}")
            return None
        return g, evaluator_fn, gene_spec, param_defaults, regimes, code_str

    def _persist_invented(self, family_name: str, gene_spec: dict,
                          param_defaults: dict, regimes: list, code_str: str) -> None:
        path = INVENTED_FAMILIES_PATH
        existing = {}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    existing = json.load(f)
            except Exception:
                pass
        # serialise gene_spec: (typ, lo, hi, dflt) → [type_name, lo, hi, dflt]
        serialised_spec = {
            k: [v[0].__name__, v[1], v[2], v[3]]
            for k, v in gene_spec.items()
        }
        existing[family_name] = {
            "gene_spec": serialised_spec,
            "param_defaults": param_defaults,
            "regimes": regimes,
            "code": code_str,
        }
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)

    def _dual_gauntlet_invented(self, g: Genome, evaluator_fn) -> tuple[bool, dict]:
        """Internal-only gauntlet for invented families.

        Invented families have no Pine template, so the TradingView tester
        can never see them — the internal walk-forward is the ONLY judge and
        therefore carries a stricter PF bar than the templated families.
        """
        from ..strategy.library import register_evaluator
        register_evaluator(g.family, evaluator_fn)
        return evidence.run_gauntlet(g, self.feed, self.cfg, min_pf=1.3)

    def _budget(self) -> tuple[float, int]:
        """(wall-clock seconds, max full gauntlet runs) for one sweep.

        propose() runs inside the hourly brain tick, so it must be bounded
        no matter how many families are open. <= 0 disables all gauntlet
        work — including the LLM and TradingView calls that feed it.
        """
        scfg = self.cfg.get("strategies", {})
        return (float(scfg.get("gauntlet_time_budget_s", 240)),
                int(scfg.get("gauntlet_max_full_runs", 4)))

    def propose(self) -> dict:
        budget, max_full = self._budget()
        if budget <= 0:
            return {"proposed": 0, "reason": "gauntlet time budget disabled"}
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

        # Load candles ONCE for the whole sweep instead of per candidate.
        frames = evidence.load_frames(self.feed, self.cfg)
        if not [k for k in frames if not k.startswith("_")]:
            return {"proposed": 0, "reason": "no candle data for gauntlet"}

        # ── stage 1: build the candidate pool, prescreen it cheaply ──────
        # A full multi-symbol walk-forward costs ~2 ms/bar/symbol, so
        # screening 24 candidates the expensive way would take over an hour
        # — far longer than the brain tick that calls this.
        deadline = time.monotonic() + budget
        pool = []
        for family in gaps:
            # check BEFORE composing candidates: _llm_genome is a paid API
            # call, so an exhausted budget must not keep spending tokens on
            # genomes that will never be screened.
            if time.monotonic() > deadline:
                log.info("proposer: prescreen budget spent — "
                         f"{len(pool)} survivor(s) from families before "
                         f"'{family}'")
                break
            candidates = []
            llm_g = self._llm_genome(family, tv_context)
            if llm_g:
                candidates.append(("llm", llm_g))
            for _ in range(2):
                candidates.append(("random", self._random_genome(family)))
            for origin, g in candidates:
                if Genome.validate(g):
                    continue
                if time.monotonic() > deadline:
                    break
                ok, score, ev = evidence.prescreen(g, self.feed, self.cfg,
                                                   frames=frames)
                if not ok:
                    self.journal.log_brain_event(
                        "proposal_rejected", family,
                        {"origin": origin, "params": g.params,
                         "evidence": ev})
                    continue
                pool.append((score, origin, family, g))

        # ── stage 2: full gauntlet on the most promising survivors ───────
        # The TradingView family-proxy used to gate here. It maps a family to
        # a stock TV strategy (ema_trend→ema_cross, …) and never sees
        # g.params, so every candidate of a family got an identical verdict
        # and ranking was meaningless. It is advisory context only now.
        pool.sort(key=lambda t: t[0], reverse=True)
        best = None
        for score, origin, family, g in pool[:max_full]:
            if time.monotonic() > deadline:
                log.info("proposer: gauntlet budget spent")
                break
            ok, ev = evidence.run_gauntlet(g, self.feed, self.cfg,
                                           frames=frames)
            if not ok:
                self.journal.log_brain_event(
                    "proposal_rejected", family,
                    {"origin": origin, "params": g.params, "evidence": ev})
                continue
            full_score = tuple(ev["score"])
            if best is None or full_score > best[0]:
                best = (full_score, g, ev, origin, family)

        if best is None:
            return {"proposed": 0,
                    "reason": f"walk-forward gauntlet rejected all "
                              f"{len(pool)} prescreen survivors — "
                              f"see proposal_rejected events"}

        score, g, ev, origin, family = best
        tv = (tv_context or {}).get(family, {})
        detail = {
            "family": family, "origin": origin, "params": g.params,
            "internal": ev,
            "tradingview_advisory": tv,
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

    def propose_invention(self, tv_context: dict | None = None) -> dict:
        """Invent a brand-new strategy FAMILY (with its own evaluator code).

        This used to live at the tail of propose(), behind four early returns
        (daily cap, 'population well-covered', no feed, and 'best is None').
        It therefore only ran on a day when a normal proposal had already
        succeeded — and the journal confirms it never once executed. It is
        now an independent entry point.
        """
        if self._budget()[0] <= 0:
            return {"invented": 0, "reason": "gauntlet time budget disabled"}
        if not self.feed:
            return {"invented": 0, "reason": "no data feed for gauntlet"}
        if self._inventions_today() >= MAX_INVENTIONS_PER_DAY:
            return {"invented": 0, "reason": "daily invention cap reached"}
        if not (self.llm and self.llm.available):
            return {"invented": 0, "reason": "no LLM available"}

        if tv_context is None:
            try:
                tv_context = self.tv.compare_families("BTC/USDT")
            except Exception as e:
                log.warning(f"tv context failed: {e}")
                tv_context = {}

        try:
            inv_result = self._invent_family(tv_context)
        except Exception as e:
            log.warning(f"invention attempt failed: {e}")
            return {"invented": 0, "reason": f"invention error: {e}"}
        if not inv_result:
            return {"invented": 0, "reason": "LLM produced no valid family"}

        inv_g, evaluator_fn, gene_spec, param_defaults, regimes, code_str = inv_result
        ok, ev = self._dual_gauntlet_invented(inv_g, evaluator_fn)
        if not ok:
            self.journal.log_brain_event("invention_rejected", inv_g.family,
                                         {"gauntlet": ev,
                                          "hypothesis": inv_g.hypothesis})
            log.info(f"invented family {inv_g.family} rejected: {ev}")
            return {"invented": 0, "reason": ev.get("reason", "gauntlet failed"),
                    "family": inv_g.family, "gauntlet": ev}

        from ..strategy.library import register_evaluator
        register_evaluator(inv_g.family, evaluator_fn)
        self._persist_invented(inv_g.family, gene_spec,
                               param_defaults, regimes, code_str)
        from ..core.types import Strategy, StrategyState, new_id
        inv_st = Strategy(
            id=new_id("inv"), name=f"{inv_g.family} (invented)",
            kind=inv_g.family, params=inv_g.params,
            state=StrategyState.PAPER,
            description="invented by brain — internal gauntlet passed",
            origin="invented")
        inv_st.hypothesis = inv_g.hypothesis
        inv_st.invalidation = inv_g.invalidation
        inv_st.regime_filter = set(inv_g.regime_filter)
        inv_st.markets = set(inv_g.markets)
        inv_st.generation, inv_st.parent_id = 1, "inventor"
        self.journal.upsert_strategy(inv_st)
        self.journal.log_brain_event("invention_accepted", inv_st.id,
                                     {"family": inv_g.family,
                                      "gauntlet": ev,
                                      "hypothesis": inv_g.hypothesis})
        if self.notifier:
            self.notifier.send(
                f"🔬 New family invented: {inv_g.family}\n"
                f"{inv_g.hypothesis[:120]}\n"
                f"evidence: {ev.get('per_symbol')} → paper probation")
        return {"invented": 1, "strategy": inv_st.as_dict(), "gauntlet": ev}


def _parse_gene_spec_static(raw: dict) -> dict:
    """Module-level helper used by load_invented_families()."""
    type_map = {"float": float, "int": int, "str": str}
    out = {}
    for k, v in raw.items():
        if not isinstance(v, list) or len(v) != 4:
            continue
        typ = type_map.get(str(v[0]).lower())
        if typ is None:
            continue
        try:
            lo = typ(v[1]) if typ is not str else None
            hi = typ(v[2]) if typ is not str else None
            dflt = typ(v[3]) if typ is not str else v[3]
        except (TypeError, ValueError):
            continue
        out[k] = (typ, lo, hi, dflt)
    return out


def load_invented_families() -> None:
    """Reload invented families from disk into runtime dicts on startup."""
    from ..strategy.genome import register_family
    from ..strategy.library import SAFE_NS, register_evaluator
    if not os.path.exists(INVENTED_FAMILIES_PATH):
        return
    try:
        with open(INVENTED_FAMILIES_PATH) as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f"load_invented_families: failed to read {INVENTED_FAMILIES_PATH}: {e}")
        return
    for name, entry in data.items():
        try:
            gene_spec = _parse_gene_spec_static(entry.get("gene_spec", {}))
            param_defaults = entry.get("param_defaults", {})
            code_str = entry.get("code", "")
            if not gene_spec or not code_str:
                continue
            register_family(name, gene_spec, param_defaults)
            ns = dict(SAFE_NS)
            exec(compile(code_str, f"<invented:{name}>", "exec"), ns)
            fn = ns.get(f"eval_{name}")
            if callable(fn):
                register_evaluator(name, fn)
                log.info(f"load_invented_families: restored {name}")
            else:
                log.warning(f"load_invented_families: eval_{name} not found in code")
        except Exception as e:
            log.warning(f"load_invented_families: failed to restore {name}: {e}")


load_invented_families()
