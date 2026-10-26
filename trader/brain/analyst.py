"""The Analyst — decides which strategies the company trades, and when to
stop trading them.

The company's flow is Researcher -> Strategist -> Analyst -> Trader, with the
Librarian keeping the record. This is the Analyst: it compiles a spec, judges
it on recent evidence, checks it adds something the book does not already
have, and retires what has stopped working.

Its governing assumption is that no edge lasts. Selection therefore asks "is
this working now" rather than "did this survive five years", and a separate
sweep asks "has this stopped" — which is what makes the population rotate
instead of ossify. `rolling.py` documents the measurements behind that.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np

from ..core.journal import Journal
from ..strategy import health_observation, rolling, spec_evidence
from ..strategy.compile import compile_spec
from ..strategy.spec import StrategySpec
from ..strategy.vector_backtest import funding_for

log = logging.getLogger(__name__)

TIMEFRAMES = ("15m", "1h", "4h")

#: owned by the qualification record, which re-derives the verdict from it
from ..strategy.qualification import MAX_SIGNAL_OVERLAP  # noqa: E402


def signal_overlap(a_long, a_short, b_long, b_short) -> float:
    """Correlation of two specs' directional signal series, in [0, 1].

    Cheaper and more honest than correlating equity curves: it measures
    whether they are making the same call at the same moment, which is what
    redundancy actually means for a book.
    """
    n = min(len(a_long), len(b_long))
    if n == 0:
        return 0.0
    a = a_long[:n].astype(float) - a_short[:n].astype(float)
    b = b_long[:n].astype(float) - b_short[:n].astype(float)
    if a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return float(abs(np.corrcoef(a, b)[0, 1]))


class Analyst:
    def __init__(self, journal: Journal, cfg: dict, feed=None, notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.feed = feed
        self.notifier = notifier
        self._frames: dict = {}
        self._health_sweeps: list = []
        #: how improbable a spec's cross-symbol null result must be before
        #: it is admitted. 0.01 is conventional, not fitted: the measured
        #: false-positive rate on uniform percentiles is 0.1-0.4%.
        self.null_max_p = float(
            (cfg.get("strategies") or {}).get("select_null_max_p", 0.01))

    # ── data ─────────────────────────────────────────────────────────────
    def frames(self, tf: str, extra: tuple = ()) -> dict:
        key = (tf, extra)
        if key not in self._frames:
            base = list(spec_evidence.load_frames(
                self.cfg, tf, feed=self.feed))
            syms = [k for k in base if not k.startswith("_")]
            syms += [x for x in extra if x not in syms]
            self._frames[key] = spec_evidence.load_frames(
                self.cfg, tf, feed=self.feed, symbols=syms)
        return self._frames[key]

    @staticmethod
    def _declared(spec: StrategySpec) -> tuple:
        """The markets the spec itself says it trades."""
        try:
            return tuple((spec.universe or {}).get("include") or ())
        except Exception:
            return ()

    def _ctx(self, tf: str, spec: StrategySpec):
        """Evidence context for one spec, over ITS universe.

        The backtest symbol list in config is five markets. A spec that
        declares sixteen was judged — pooled profit factor, rotation null,
        redundancy — on five of them, and the cross-symbol null test then had
        five percentiles to work with, which is barely more than the minimum
        that makes it computable. Donchian Breakout Trail reads p=0.094 on
        those five and p=3.5e-04 on the sixteen it actually trades.
        """
        frames = self.frames(tf, self._declared(spec))
        # reference markets travel the way _btc_1h does: as an underscore
        # key every symbol loop already skips. Copied, never written into
        # the cached dict, because the references differ per spec.
        # Derived from the expressions alone. compile_spec() validates the
        # whole spec first, and a spec that failed validation fell back to
        # its stored data_requires — ["ohlcv"] unless compiled before — so
        # its references silently never loaded.
        from ..strategy import dsl
        try:
            exprs = [spec.entry_long, spec.entry_short, *(spec.filters or []),
                     getattr(spec.exit, "signal_exit", "")]
            req = dsl.data_requires(*[dsl.parse(e) for e in exprs
                                      if (e or "").strip()])
        except Exception:
            req = tuple(spec.data_requires or ())
        market = spec_evidence.load_refs(req)
        if market:
            frames = {**frames, "_market": market}
        btc = frames.get("_btc_1h")
        return (frames,
                {tf: btc} if btc is not None else None,
                lambda s: spec_evidence.load_derivs(s, spec.data_requires),
                spec_evidence.risk_for(self.cfg["risk"], tf))

    def _scfg(self) -> dict:
        return self.cfg.get("strategies", {}) or {}

    def _compile(self, probe: StrategySpec):
        """Compile for evidence. Under `qualify` the spec is measured with the
        Factory exit semantics the paper/live engine will actually run, so the
        backtest describes the exact strategy that is later installed. Every
        other caller keeps the legacy simulator."""
        bound = getattr(self, "_exit_semantics_id", None)
        return compile_spec(probe) if bound is None else compile_spec(
            probe, exit_semantics_id=bound)

    # ── selection ────────────────────────────────────────────────────────
    def evaluate(self, spec: StrategySpec,
                 timeframes=TIMEFRAMES) -> tuple[bool, dict]:
        """Judge a candidate on recent evidence, best timeframe wins.

        Timeframe is a gene, and it is a consequential one: a round trip
        costs ~18.5% of the risk staked at 15m against ~7.8% at 4h, because
        ATR/price grows with horizon while the fee does not.
        """
        if getattr(self, '_fixed_proposal_timeframe', None):
            timeframes = (self._fixed_proposal_timeframe,)
        s = self._scfg()
        recent_days = float(s.get("select_recent_days", 90))
        min_trades = int(s.get("select_min_trades", 20))
        min_pf = float(s.get("select_min_pf", 1.15))
        max_days = float(s.get("select_max_days", 365))

        best, results = None, {}
        for tf in timeframes:
            probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf})
            frames, btc, derivs_for, risk = self._ctx(tf, probe)
            syms = [k for k in frames if not k.startswith("_")]
            gaps = spec_evidence.missing_data(probe, syms, frames=frames)
            if gaps:
                results[tf] = {"untested": True, "reason": f"missing: {gaps}"}
                continue
            try:
                compiled = self._compile(probe)
            except Exception as e:
                results[tf] = {"error": str(e)}
                continue
            ok, ev = rolling.recent_verdict(
                compiled, frames, risk, tf, recent_days=recent_days,
                min_trades=min_trades, min_pf=min_pf, btc=btc,
                derivs_for=derivs_for, max_days=max_days)
            results[tf] = ev
            if best is None or ev["pooled_pf"] > best[1]["pooled_pf"]:
                best = (tf, ev, ok)

        if best is None:
            return False, {"spec": spec.id, "by_timeframe": results,
                           "reason": "no testable timeframe", "untested": True}
        tf, ev, ok = best
        out = {"spec": spec.id, "chosen_timeframe": tf, "recent": ev,
               "by_timeframe": results,
               "reason": ev.get("reason", "passed recent gate")}
        # regime context: what is true now, and where this has actually paid
        now = self.current_regime(tf)
        rf = self.regime_fitness(spec, tf)
        out["regime"] = {"current": now["overall"],
                         "per_symbol": now["per_symbol"],
                         "fit": rf.get("fit", []),
                         "declared": rf.get("declared", []),
                         "by_regime": rf.get("by_regime", {}),
                         "fit_for_now": now["overall"] in (rf.get("fit") or [])}
        return ok, out

    def persistence(self, spec: StrategySpec, tf: str | None = None) -> dict:
        """How often this mechanism has paid across history.

        Context, never a gate. A spec profitable in 74% of past windows that
        is dead today is still dead today; a spec working today with a poor
        history is still working today. This informs how hard to watch it.
        """
        tf = tf or spec.timeframe
        probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf})
        frames, btc, derivs_for, risk = self._ctx(tf, probe)
        try:
            compiled = self._compile(probe)
        except Exception as e:
            return {"error": str(e)}
        st = rolling.rolling_windows(
            compiled, frames, risk, tf,
            window_days=float(self._scfg().get("persistence_window_days", 60)),
            step_days=float(self._scfg().get("persistence_step_days", 15)),
            btc=btc, derivs_for=derivs_for)
        return st.as_dict()

    # ── regime ───────────────────────────────────────────────────────────
    def current_regime(self, tf: str = "1h") -> dict:
        """What regime is the market in right now, per symbol and overall.

        The overall label is the modal one across the book's symbols, which is
        what a portfolio-level rotation decision needs; per-symbol labels are
        what the orchestrator gates individual signals on.
        """
        from collections import Counter

        from ..agents.regime import classify
        frames = self.frames(tf)
        per_symbol = {}
        for sym, df in frames.items():
            if sym.startswith("_") or df is None or len(df) < 60:
                continue
            per_symbol[sym] = classify(df, None)["regime"]
        counts = Counter(per_symbol.values())
        overall = counts.most_common(1)[0][0] if counts else "UNKNOWN"
        return {"overall": overall, "per_symbol": per_symbol,
                "counts": dict(counts)}

    def regime_fitness(self, spec: StrategySpec,
                       tf: str | None = None) -> dict:
        """Which regimes this mechanism has actually paid in.

        spec.regime_filter as written is the author's guess, and the
        orchestrator gates live signals on it (orchestrator.py:207) — so a
        wrong guess either silences a working strategy or runs it where it
        loses. This replaces the guess with measurement.
        """
        tf = tf or spec.timeframe
        probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf})
        frames, btc, derivs_for, risk = self._ctx(tf, probe)
        try:
            compiled = self._compile(probe)
        except Exception as e:
            return {"error": str(e), "fit": [], "declared": spec.regime_filter}
        s = self._scfg()
        by = rolling.regime_windows(
            compiled, frames, risk, tf,
            window_days=float(s.get("regime_window_days", 30)),
            step_days=float(s.get("regime_step_days", 7)),
            btc=btc, derivs_for=derivs_for)
        fit = rolling.fit_regimes(
            by,
            min_windows=int(s.get("regime_min_windows", 5)),
            min_hit_rate=float(s.get("regime_min_hit_rate", 0.5)),
            min_median_pf=float(s.get("regime_min_median_pf", 1.0)))
        return {"fit": fit, "declared": list(spec.regime_filter),
                "by_regime": {r: st.as_dict() for r, st in by.items()}}

    def set_measured_regimes(self, spec: StrategySpec,
                             tf: str | None = None) -> dict:
        """Overwrite spec.regime_filter with what the evidence supports.

        If nothing is supported the declared filter is kept and the spec is
        flagged: an empty filter would silence it entirely, which is a
        stronger claim than the evidence makes.
        """
        rf = self.regime_fitness(spec, tf)
        # Persist what was measured. The vault card and the orchestrator's
        # strategy blender both read provenance["regime_evidence"]; until
        # this line nothing wrote it, so a measurement made here died here.
        if rf.get("by_regime"):
            prov = dict(spec.provenance or {})
            prov["regime_evidence"] = rf["by_regime"]
            spec.provenance = prov
        if rf.get("fit"):
            measured = sorted(rf["fit"])          # deterministic ordering
            rf["changed"] = measured != sorted(spec.regime_filter)
            spec.regime_filter = measured
        else:
            rf["changed"] = False
            rf["note"] = ("no regime met the evidence bar — declared filter "
                          "kept, strategy is unproven rather than disproven")
        return rf

    # ── portfolio ────────────────────────────────────────────────────────
    def redundancy(self, spec: StrategySpec, tf: str, book: list) -> dict:
        """Compare every shared market on its actual symbol and timeframe.

        Cross-timeframe alignment has no registered contract here. Refuse that
        comparison rather than rewriting an installed strategy's timeframe.
        Missing inputs/calendars are unavailable evidence, never diversification.
        """
        from ..core.types import norm_symbol
        from ..strategy.vector_backtest import WARMUP

        result = {"status": "NOT_APPLICABLE", "max_overlap": 0.0,
                  "twin": "", "comparisons": []}
        peers = [other for other in book if other.id != spec.id]
        if not peers:
            return result
        try:
            probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf})
            cand = self._compile(probe)
            frames, btc, derivs_for, _risk = self._ctx(tf, probe)

            def symbols(strategy, available):
                u = strategy.universe or {}
                include = {norm_symbol(s) for s in u.get("include") or ()}
                exclude = {norm_symbol(s) for s in u.get("exclude") or ()}
                return (include or {norm_symbol(s) for s in available
                                    if not s.startswith("_")}) - exclude

            def signals(compiled, context, symbol):
                fs, leader, derivatives, _ = context
                df = fs.get(symbol)
                if df is None or len(df) <= WARMUP + 1:
                    raise ValueError(f"insufficient_history:{symbol}")
                if (not df["ts"].is_monotonic_increasing
                        or df["ts"].duplicated().any()):
                    raise ValueError(f"invalid_calendar:{symbol}")
                universe = {s: {tf: f} for s, f in fs.items()
                            if not s.startswith("_") and f is not None}
                lo, sh, diag = compiled.entries_detail(
                    spec_evidence.frames_for(df, tf), btc=leader,
                    derivs=derivatives(symbol), universe=universe,
                    market=fs.get("_market"), symbol=symbol)
                if diag["missing"] or not np.all(diag["quality_ok"][WARMUP:]):
                    raise ValueError(f"missing_signal_context:{symbol}:{diag['missing']}")
                world = diag.get("world_ok")
                if world is not None and not np.all(world[WARMUP:]):
                    raise ValueError(f"missing_world_context:{symbol}")
                if len(lo) != len(df) or len(sh) != len(df):
                    raise ValueError(f"signal_calendar_mismatch:{symbol}")
                return lo[WARMUP:], sh[WARMUP:]

            context = (frames, btc, derivs_for, _risk)
            candidate_symbols = symbols(probe, frames)
            if not candidate_symbols:
                raise ValueError("candidate_comparison_universe_unavailable")
            for other in peers:
                # Compile the actual installed declaration, never a tf-refitted copy.
                oc = compile_spec(StrategySpec.from_dict(other.to_dict()))
                other_ctx = self._ctx(other.timeframe, oc.spec)
                other_symbols = symbols(other, other_ctx[0])
                if not other_symbols:
                    raise ValueError(f"comparison_universe_unavailable:{other.id}")
                common = candidate_symbols & other_symbols
                if not common:
                    continue  # explicitly disjoint declared/evaluated markets
                if other.timeframe != tf:
                    raise ValueError(f"cross_timeframe_comparison_unavailable:{other.id}:"
                                     f"{tf}:{other.timeframe}")
                for symbol in sorted(common):
                    left, right = frames.get(symbol), other_ctx[0].get(symbol)
                    if (left is None or right is None
                            or not left["ts"].equals(right["ts"])):
                        raise ValueError(f"comparison_calendar_unavailable:{other.id}:{symbol}")
                    overlap = signal_overlap(*signals(cand, context, symbol),
                                             *signals(oc, other_ctx, symbol))
                    if not np.isfinite(overlap):
                        raise ValueError(f"nonfinite_overlap:{other.id}:{symbol}")
                    result["comparisons"].append(
                        {"spec": other.id, "symbol": symbol, "timeframe": tf,
                         "max_overlap": overlap})
                    if overlap > result["max_overlap"]:
                        result.update(max_overlap=overlap, twin=other.id)
            if result["comparisons"]:
                result["status"] = "MEASURED"
            return result
        except Exception as exc:
            return {**result, "status": "UNAVAILABLE", "max_overlap": None,
                    "reason": str(exc)}

    @staticmethod
    def _with_null_evidence(ev: dict, per_symbol_pct: dict) -> dict:
        """Keep percentile summaries as diagnostics, never admission authority."""
        import statistics as _st
        from ..strategy import null_baseline
        vals = [v for v in per_symbol_pct.values() if v is not None]
        return {**ev,
                "null_median_percentile": (round(_st.median(vals), 3)
                                           if vals else None),
                "null_beats_90pct_on":
                    f"{sum(1 for v in vals if v >= 0.90)}/{len(vals)}",
                "null_consistency_p_raw": null_baseline.consistency_p(vals),
                "null_consistency_p": None,
                "null_percentiles": dict(per_symbol_pct),
                "null_symbols": len(vals)}

    def admit(self, spec: StrategySpec, book: list) -> tuple[bool, dict]:
        """Full admission decision: works now, and adds something new."""
        ok, ev = self.evaluate(spec)
        if not ok:
            return False, ev
        tf = ev["chosen_timeframe"]
        redundancy = self.redundancy(spec, tf, book)
        ev["redundancy"] = redundancy
        if redundancy["status"] == "UNAVAILABLE":
            return False, {**ev, "untested": True, "reason":
                           "redundancy_unavailable:" + redundancy["reason"]}
        overlap, twin = redundancy["max_overlap"], redundancy["twin"]
        ev["persistence"] = self.persistence(spec, tf)
        if overlap > MAX_SIGNAL_OVERLAP:
            return False, {**ev, "reason": f"signal overlap {overlap:.2f} with "
                                           f"{twin} (> {MAX_SIGNAL_OVERLAP})"}
        joint = self._null_evidence(spec, tf)
        ev = self._with_null_evidence(ev, joint.get("percentiles", {}))
        ev["null_dependence"] = joint
        ev["null_consistency_p"] = joint.get("consistency_p_dep")
        ps = (ev["null_consistency_p"], joint.get("common_rotation", {}).get("p"))
        if any(p is None or not np.isfinite(p) for p in ps):
            return False, {**ev, "untestable": True, "reason":
                           "untestable: dependence-aware rotation evidence unavailable: "
                           + joint.get("reason", "insufficient joint null evidence")}
        p = max(ps)
        if p > self.null_max_p:
            return False, {**ev, "reason":
                           f"dependence-aware rotation fails across {ev['null_symbols']} "
                           f"symbols (p={p:.2g} > {self.null_max_p})"}
        return True, ev

    def qualify(self, spec: StrategySpec,
                book: list, *, fixed_timeframe=False) -> tuple[bool, dict, dict | None]:
        """The Backtest PASS for a created strategy.

        This is `admit` — the same recent-profitability gate, dependence-aware
        rotation null and redundancy check — run once under the Factory exit
        semantics the paper/live engine will execute, so the evidence describes
        the strategy that is later installed rather than the legacy simulator's
        reading of it. A pass is returned as a canonical qualification record
        (`strategy.qualification`); a refusal carries `admit`'s own reason.
        Nothing is installed or written here."""
        from ..strategy import exit_policy, factory_handoff as fh, qualification
        bad = exit_policy.unsupported(spec.exit)
        if bad:
            return False, {"spec": spec.id, "untested": True,
                           "reason": f"unsupported_versioned_exit:{bad}"}, None
        self._exit_semantics_id = exit_policy.EXIT_SEMANTICS_ID
        self._fixed_proposal_timeframe = spec.timeframe if fixed_timeframe else None
        try:
            ok, ev = self.admit(spec, book)
            if not ok:
                return False, ev, None
            tf = ev["chosen_timeframe"]
            probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf})
            frames, _btc, _derivs, risk = self._ctx(tf, probe)
            evaluated = fh._frozen_spec(probe)[1]
            th = qualification.thresholds(self.cfg)
            if th["null_max_p"] != self.null_max_p:
                return False, {"spec": spec.id, "untested": True,
                               "reason": "qualification_threshold_mismatch"}, None
            run = spec_evidence.quant_contract.run_identity(
                probe, frames, risk)
            rec = qualification.build(
                spec_id=spec.id, timeframe=tf, evaluated_spec_sha256=evaluated,
                declared_universe=probe.universe or {},
                declared_regime_filter=list(probe.regime_filter or []),
                exit_semantics_id=exit_policy.EXIT_SEMANTICS_ID,
                ev=ev, th=th, run=run, risk_cfg=risk)
            return True, ev, rec
        except qualification.QualificationRefused as e:
            return False, {"spec": spec.id, "reason": e.code,
                           "untested": True}, None
        finally:
            self._exit_semantics_id = None
            self._fixed_proposal_timeframe = None

    def _null_evidence(self, spec: StrategySpec, tf: str) -> dict:
        """One timing hypothesis, one common-calendar joint control receipt."""
        from ..research import portfolio_null as pn
        from ..research.dependence import assess_joint
        from ..research.evaluate import _clock
        from ..strategy.vector_backtest import warm_window
        from ..world.context import load_context
        try:
            probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf})
            compiled = self._compile(probe)
            frames, btc, derivs_for, risk = self._ctx(tf, probe)
            universe = {s: {tf: f} for s, f in frames.items()
                        if not s.startswith("_") and f is not None}
            if not universe:
                return {"reason": "no symbol frames"}
            clocks = [_clock(v[tf]) for v in universe.values()]
            if any(not np.array_equal(c, clocks[0]) for c in clocks):
                return {"reason": "unaligned symbol calendars; common offsets unavailable"}
            cut = int(len(clocks[0]) * 0.7)
            sl, first = warm_window(cut, len(clocks[0]))
            world = load_context(as_of_ms=int(clocks[0][-1]) * 1000)
            legs = []
            sources = {"btc": btc, "market": frames.get("_market"),
                       "universe": universe, "derivatives": {}}
            for sym in sorted(universe):
                sf = spec_evidence.frames_for(frames[sym], tf)
                derivs = derivs_for(sym)
                sources["derivatives"][sym] = derivs
                lo, sh = compiled.entries(sf, btc=btc, derivs=derivs,
                    universe=universe, market=frames.get("_market"), symbol=sym,
                    world=world)
                fund = funding_for(sym, sf[tf], risk)
                legs.append(pn.Leg(sym, lo[sl], sh[sl],
                    sf[tf].iloc[sl].reset_index(drop=True),
                    None if fund is None else np.asarray(fund)[sl], first))
            return assess_joint(legs, compiled.spec.exit, risk, tf,
                hypothesis=probe.to_dict(), world=world, sources=sources,
                equity=2000.0, risk_pct=float(risk.get("risk_per_trade_pct", .5)),
                max_open=int(risk.get("max_open_trades", 8)),
                rotation_draws=pn.draws_for(self.null_max_p, 10000) or 0)
        except Exception as e:
            log.warning(f"joint null unavailable for {spec.id}: {e}")
            return {"reason": "joint null unavailable: " + str(e)}

    # ── tradingview confirmation ─────────────────────────────────────────
    #: TradingView symbol for a pair, e.g. BTC/USDT -> BINANCE:BTCUSDT
    @staticmethod
    def tv_symbol(pair: str = "BTC/USDT") -> str:
        return "BINANCE:" + pair.split(":")[0].replace("/", "")

    def confirm_on_tv(self, spec: StrategySpec, tf: str | None = None,
                      symbol: str = "BTC/USDT") -> dict:
        """Second opinion from TradingView's Strategy Tester. ADVISORY.

        This never gates admission, by deliberate choice. TV runs on TV's
        data with TV's fee model, so it will disagree with our own backtest;
        the disagreement is evidence about the mechanism, not a verdict on
        it. Letting a TV proxy decide is what `luffy-gauntlet-repair` records
        going wrong before. And only about half of the book can compile to
        Pine at all — funding, open interest and order flow do not exist on
        a Pine chart — so a TV gate would silently kill every derivatives
        mechanism while looking like a quality bar.

        Returns {tv_testable, ran, agree, ...}. `agree` is None when TV could
        not be consulted, which is different from TV disagreeing.
        """
        out = {"spec": spec.id, "tv_testable": False, "ran": False,
               "agree": None}
        try:
            compiled = compile_spec(spec)
            code, testable, reasons = compiled.to_pine()
        except Exception as e:
            return {**out, "reason": f"compile failed: {e}"}
        out["tv_testable"] = bool(testable)
        if not testable:
            out["reason"] = "; ".join(str(r) for r in reasons[:3])
            return out
        try:
            from .tv_harness import TVHarness
            h = TVHarness(self.journal, self.cfg)
            if h.health().get("state") == "down" and h.runs_today() == 0:
                pass                    # never consulted yet; still try once
            res = h.backtest(code, symbol=self.tv_symbol(symbol),
                             tf=tf or spec.timeframe)
        except Exception as e:
            return {**out, "reason": f"harness unavailable: {e}"}
        out["ran"] = bool(res.get("ok"))
        out["tv"] = res
        if not out["ran"]:
            out["reason"] = res.get("skipped") or "tester run failed"
            return out
        tv_pf = (res.get("metrics") or {}).get("profit_factor")
        if tv_pf is not None:
            out["tv_pf"] = tv_pf
            out["agree"] = bool(float(tv_pf) >= 1.0)
        self.journal.log_brain_event("tv_confirmation", spec.id, out)
        return out

    # ── retirement ───────────────────────────────────────────────────────
    def review_deployed(self, specs: list) -> list:
        """Sweep live specs for decay. Returns the retirement actions."""
        s = self._scfg()
        days = float(s.get("decay_recent_days", 30))
        floor = float(s.get("decay_floor_pf", 0.85))
        min_trades = int(s.get("decay_min_trades", 10))
        actions = []
        # health observation is telemetry: open_sweep never raises, the
        # loop only appends raw facts to memory, and records are written by
        # flush_health_observations() once the caller has applied retirement
        sweep = health_observation.open_sweep(
            self.journal, specs, {"decay_recent_days": days,
                                  "decay_min_trades": min_trades,
                                  "decay_floor_pf": floor})
        try:
            self.__dict__.setdefault("_health_sweeps", []).append(sweep)
        except Exception:
            pass
        for spec in specs:
            sweep.attempt(spec)
            tf = spec.timeframe
            try:
                frames, btc, derivs_for, risk = self._ctx(tf, spec)
            except Exception as e:
                sweep.evaluation_raised(spec, "context", e)
                self.flush_health_observations()
                raise
            try:
                compiled = compile_spec(spec)
            except Exception as e:
                log.warning(f"analyst: {spec.id} will not compile: {e}")
                sweep.compile_failed(spec, e)
                continue
            try:
                from ..learning.runtime import recent_decay
                from ..strategy.factory_handoff import _jsha
                versions = self.journal.query(
                    'SELECT version_id FROM strategy_versions WHERE strategy_id=? AND spec_hash=?',
                    (spec.id, _jsha(compiled.spec.to_dict())))
                if len(versions) == 1:
                    cutoff = int(datetime.now(timezone.utc).timestamp()*1000)
                    recent_decay(self.journal, self.cfg, versions[0]['version_id'], frames,
                                 cutoff_ms=cutoff)
            except (ValueError, KeyError, TypeError, __import__('sqlite3').OperationalError) as exc:
                log.info('registered recent-decay producer unavailable: %s', exc)
            diag = {}
            try:
                dead, ev = rolling.has_decayed(
                    compiled, frames, risk, tf, recent_days=days,
                    min_trades=min_trades, floor_pf=floor, btc=btc,
                    derivs_for=derivs_for, diagnostics=diag)
            except Exception as e:
                sweep.evaluation_raised(spec, "evaluate", e)
                self.flush_health_observations()
                raise
            if dead:
                actions.append({"spec": spec.id, "name": spec.name,
                                "action": "retire", "evidence": ev})
                self.journal.log_brain_event(
                    "spec_decayed", spec.id,
                    {"name": spec.name, "evidence": ev,
                     "ts": datetime.now(timezone.utc).isoformat()})
                log.warning(f"ANALYST: {spec.name} decayed — {ev['verdict']}")
            sweep.observed(spec, dead, ev, diag, risk)
        sweep.finish()
        if actions and self.notifier:
            self.notifier.send("📉 Decay diagnostics (no lifecycle change):\n" + "\n".join(
                f"{a['name']}: {a['evidence']['verdict']}" for a in actions))
        return actions

    def flush_health_observations(self) -> bool:
        """Write buffered health observations. Telemetry: never raises.

        Called by the kernel after it has applied the retirements
        review_deployed selected. The journal's `_tx` rolls back on failure,
        so nothing is written while this thread's connection holds an
        uncommitted transaction (the kernel's retirement UPDATEs): the
        sweeps stay pending for a later call. Returns True when nothing is
        left pending."""
        try:
            conn = getattr(self.journal, "_conn", None)
            if conn is not None and conn().in_transaction:
                return False
            pending = self.__dict__.get("_health_sweeps") or []
            self._health_sweeps = []
            for sw in pending:
                try:
                    sw.flush()
                except Exception:
                    pass
            return True
        except Exception as e:
            try:
                log.warning(f"analyst: health observation flush failed: {e}")
            except Exception:
                pass
            return False
