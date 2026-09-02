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
from ..strategy import rolling, spec_evidence
from ..strategy.compile import compile_spec
from ..strategy.spec import StrategySpec
from ..strategy.vector_backtest import vector_walk_forward

log = logging.getLogger(__name__)

TIMEFRAMES = ("15m", "1h", "4h")

#: two strategies firing on the same bars in the same direction are one
#: strategy wearing two names, however well each backtests alone
MAX_SIGNAL_OVERLAP = 0.6


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

    # ── data ─────────────────────────────────────────────────────────────
    def frames(self, tf: str) -> dict:
        if tf not in self._frames:
            self._frames[tf] = spec_evidence.load_frames(self.cfg, tf,
                                                         feed=self.feed)
        return self._frames[tf]

    def _ctx(self, tf: str, spec: StrategySpec):
        frames = self.frames(tf)
        btc = frames.get("_btc_1h")
        return (frames,
                {"15m": btc} if btc is not None else None,
                lambda s: spec_evidence.load_derivs(s, spec.data_requires),
                spec_evidence.risk_for(self.cfg["risk"], tf))

    def _scfg(self) -> dict:
        return self.cfg.get("strategies", {}) or {}

    # ── selection ────────────────────────────────────────────────────────
    def evaluate(self, spec: StrategySpec,
                 timeframes=TIMEFRAMES) -> tuple[bool, dict]:
        """Judge a candidate on recent evidence, best timeframe wins.

        Timeframe is a gene, and it is a consequential one: a round trip
        costs ~18.5% of the risk staked at 15m against ~7.8% at 4h, because
        ATR/price grows with horizon while the fee does not.
        """
        s = self._scfg()
        recent_days = float(s.get("select_recent_days", 90))
        min_trades = int(s.get("select_min_trades", 20))
        min_pf = float(s.get("select_min_pf", 1.15))

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
                compiled = compile_spec(probe)
            except Exception as e:
                results[tf] = {"error": str(e)}
                continue
            ok, ev = rolling.recent_verdict(
                compiled, frames, risk, tf, recent_days=recent_days,
                min_trades=min_trades, min_pf=min_pf, btc=btc,
                derivs_for=derivs_for)
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
            compiled = compile_spec(probe)
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
            compiled = compile_spec(probe)
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
    def redundancy(self, spec: StrategySpec, tf: str,
                   book: list) -> tuple[float, str]:
        """Highest signal correlation against anything already trading.

        A profit factor of 1.05 that fires when nothing else does is worth
        more to a book than 1.30 that duplicates an existing member.
        """
        frames, btc, derivs_for, _risk = self._ctx(tf, spec)
        syms = [k for k in frames if not k.startswith("_")]
        if not syms:
            return 0.0, ""
        sym = syms[0]
        universe = {s: {tf: frames[s]} for s in syms
                    if frames[s] is not None}
        try:
            cand = compile_spec(
                StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf}))
            a_lo, a_sh = cand.entries({tf: frames[sym]}, btc=btc,
                                      derivs=derivs_for(sym),
                                      universe=universe, symbol=sym)
        except Exception:
            return 0.0, ""
        worst, who = 0.0, ""
        for other in book:
            if other.id == spec.id:
                continue
            try:
                oc = compile_spec(
                    StrategySpec.from_dict({**other.to_dict(),
                                            "timeframe": tf}))
                b_lo, b_sh = oc.entries(
                    {tf: frames[sym]}, btc=btc,
                    derivs=spec_evidence.load_derivs(sym, other.data_requires),
                    universe=universe, symbol=sym)
            except Exception:
                continue
            r = signal_overlap(a_lo, a_sh, b_lo, b_sh)
            if r > worst:
                worst, who = r, other.id
        return worst, who

    @staticmethod
    def _with_null_evidence(ev: dict, per_symbol_pct: dict) -> dict:
        """Attach the rotation-null result to an admission record.

        Admission gates on a 90-day pooled profit factor and signal overlap —
        the same arithmetic that scores ALWAYS-LONG at PF 1.28 on drift, so
        it cannot separate a mechanism from a market direction.

        Recorded, not gated. With 60 draws a single symbol's percentile is
        noisy, and the admitted strategy's per-symbol figures (0.70-0.88) do
        not separate cleanly from a rejected one's (0.60-1.00); a threshold
        chosen to split those two would be fitted to them. Putting the number
        in the record lets the 6h review and the operator see it.
        """
        import statistics as _st
        vals = [v for v in per_symbol_pct.values() if v is not None]
        return {**ev,
                "null_median_percentile": (round(_st.median(vals), 3)
                                           if vals else None),
                "null_beats_90pct_on":
                    f"{sum(1 for v in vals if v >= 0.90)}/{len(vals)}"}

    def admit(self, spec: StrategySpec, book: list) -> tuple[bool, dict]:
        """Full admission decision: works now, and adds something new."""
        ok, ev = self.evaluate(spec)
        if not ok:
            return False, ev
        tf = ev["chosen_timeframe"]
        overlap, twin = self.redundancy(spec, tf, book)
        ev["redundancy"] = {"max_overlap": round(overlap, 3), "twin": twin}
        ev["persistence"] = self.persistence(spec, tf)
        if overlap > MAX_SIGNAL_OVERLAP:
            return False, {**ev, "reason": f"signal overlap {overlap:.2f} with "
                                           f"{twin} (> {MAX_SIGNAL_OVERLAP})"}
        return True, self._with_null_evidence(ev, self._null_percentiles(spec, tf))

    def _null_percentiles(self, spec: StrategySpec, tf: str) -> dict:
        """{symbol: share of its own rotations this spec beats}, best effort."""
        from ..strategy import null_baseline
        out: dict = {}
        try:
            probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": tf})
            compiled = compile_spec(probe)
            frames, btc, derivs_for, risk = self._ctx(tf, probe)
            for sym in [k for k in frames if not k.startswith("_")]:
                sf = spec_evidence.frames_for(frames[sym], tf)
                r = vector_walk_forward(compiled, sf, risk, btc=btc,
                                        derivs=derivs_for(sym), symbol=sym)
                if r["test"].trades < 2:
                    continue
                a = null_baseline.assess(
                    compiled, sf, risk, r["test"].profit_factor, btc=btc,
                    derivs=derivs_for(sym), symbol=sym, draws=40,
                    split=0.7, part="test")
                if a.get("percentile") is not None:
                    out[sym] = a["percentile"]
        except Exception as e:
            log.warning(f"null baseline unavailable for {spec.id}: {e}")
        return out

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
        tv_pf = res.get("profit_factor")
        if tv_pf is not None:
            out["tv_pf"] = tv_pf
            out["agree"] = bool(float(tv_pf) >= 1.0)
        self.journal.log_brain_event("tv_confirmation", spec.id, out)
        return out

    # ── the active set, for the regime we are actually in ────────────────
    def active_set(self, specs: list, tf: str = "1h") -> dict:
        """Which strategies trade now, and how loudly each one speaks.

        Deliberately not a single pick. The orchestrator combines every
        eligible signal, and the governing premise is that no edge lasts —
        collapsing the book to one mechanism means dying with it. So this
        reports the SET, with the same weights the orchestrator will apply,
        rather than inventing a second ordering that nothing consumes.
        """
        from ..strategy.blend import strategy_weights
        reg = self.current_regime(tf) or {}
        now = reg.get("regime") or reg.get("label") or ""
        weights = strategy_weights(self.journal, specs, now)
        members = []
        for spec in specs:
            filt = list(getattr(spec, "regime_filter", []) or [])
            members.append({
                "spec": spec.id, "name": spec.name,
                "regime_filter": filt,
                # the orchestrator gates on exactly this test
                "trades_now": (not filt) or (now in filt),
                "weight": weights.get(spec.id, 1.0)})
        members.sort(key=lambda m: (not m["trades_now"], -m["weight"]))
        active = [m for m in members if m["trades_now"]]
        return {"regime": now, "active": len(active),
                "members": members,
                "combined_weight": round(sum(m["weight"] for m in active), 3)}

    # ── retirement ───────────────────────────────────────────────────────
    def review_deployed(self, specs: list) -> list:
        """Sweep live specs for decay. Returns the retirement actions."""
        s = self._scfg()
        days = float(s.get("decay_recent_days", 30))
        floor = float(s.get("decay_floor_pf", 0.85))
        min_trades = int(s.get("decay_min_trades", 10))
        actions = []
        for spec in specs:
            tf = spec.timeframe
            frames, btc, derivs_for, risk = self._ctx(tf, spec)
            try:
                compiled = compile_spec(spec)
            except Exception as e:
                log.warning(f"analyst: {spec.id} will not compile: {e}")
                continue
            dead, ev = rolling.has_decayed(
                compiled, frames, risk, tf, recent_days=days,
                min_trades=min_trades, floor_pf=floor, btc=btc,
                derivs_for=derivs_for)
            if dead:
                actions.append({"spec": spec.id, "name": spec.name,
                                "action": "retire", "evidence": ev})
                self.journal.log_brain_event(
                    "spec_decayed", spec.id,
                    {"name": spec.name, "evidence": ev,
                     "ts": datetime.now(timezone.utc).isoformat()})
                log.warning(f"ANALYST: {spec.name} decayed — {ev['verdict']}")
        if actions and self.notifier:
            self.notifier.send("📉 Retiring decayed strategies:\n" + "\n".join(
                f"{a['name']}: {a['evidence']['verdict']}" for a in actions))
        return actions
