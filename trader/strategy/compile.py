"""Compile a StrategySpec into the three things the company needs from it:
vectorized entry arrays (Analyst), a live evaluator (Trader), and a readable
card (Librarian). One artifact, three consumers, no drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import logging
import time

import numpy as np

from ..core.types import TF_MS, Action, StrategySignal, closed_bars
from . import dsl
from .features import FeatureCtx
from .spec import StrategySpec

log = logging.getLogger(__name__)

#: context frames the snapshot keys by name rather than by timeframe
_CTX_TF = {"BTC_1h": "1h"}


@dataclass
class CompiledStrategy:
    spec: StrategySpec
    _long: object | None = None
    _short: object | None = None
    _filters: list = field(default_factory=list)
    _exit: object | None = None
    data_requires: tuple = ("ohlcv",)

    # ── vectorized path (Analyst) ────────────────────────────────────────
    def entries(self, frames: dict, btc: dict | None = None,
                derivs: dict | None = None, universe: dict | None = None,
                market: dict | None = None, symbol: str | None = None):
        ctx = self._ctx(frames, btc, derivs, universe, market, symbol)
        n = len(ctx.index)
        keep = np.ones(n, dtype=bool)
        for f in self._filters:
            keep &= dsl.evaluate_bool(f, ctx)
        lo = (dsl.evaluate_bool(self._long, ctx) & keep) \
            if self._long is not None else np.zeros(n, dtype=bool)
        sh = (dsl.evaluate_bool(self._short, ctx) & keep) \
            if self._short is not None else np.zeros(n, dtype=bool)
        # a bar cannot be both; a direction conflict resolves to no trade
        both = lo & sh
        return lo & ~both, sh & ~both

    def exit_signal(self, frames: dict, btc=None, derivs=None,
                    universe=None, market=None, symbol: str | None = None):
        if self._exit is None:
            return None
        return dsl.evaluate_bool(
            self._exit, self._ctx(frames, btc, derivs, universe, market,
                                  symbol))

    def _ctx(self, frames, btc, derivs, universe=None,
             market=None, symbol=None) -> FeatureCtx:
        tf = self.spec.timeframe
        if tf not in frames:
            raise dsl.SpecError(f"spec timeframe '{tf}' not in frames "
                                f"{sorted(frames)}")
        return FeatureCtx(frames=frames, tf=tf, btc=btc, derivs=derivs,
                          universe=universe, market=market, symbol=symbol)

    # ── live path (Trader) ───────────────────────────────────────────────
    def to_evaluator(self):
        """A callable with the EXACT signature library.evaluate() dispatches
        to: (genome_like, Snapshot) -> StrategySignal | None.

        This is why the orchestrator needs no change at cutover — registering
        this under f"spec:{id}" makes library.evaluate()'s family dispatch
        (library.py:293) find it like any other evaluator.
        """
        def _evaluate(_genome, snap):
            # Judge CLOSED bars only. The live frame's last row is the bar
            # currently forming, whose `close` is just the last trade — but
            # the statistics that admitted this spec came from
            # vector_backtest, which signals on bar i and fills at
            # closes[i]. Reading the forming bar made the live rule "price
            # is beyond the level right now" where the validated rule is
            # "the bar CLOSED beyond it", so every intrabar poke that
            # retraced was an entry the backtest never took. On 2026-09-02
            # UNI/USDT 4h traded to 6.373 above its channel and closed at
            # 5.742 below it.
            frames = {}
            for k, v in snap.dfs.items():
                if v is None or not len(v):
                    continue
                # "BTC_1h" is a context frame keyed by name, not timeframe
                tf = k if k in TF_MS else _CTX_TF.get(k)
                v = closed_bars(v, tf) if tf else v
                if len(v):
                    frames[k] = v
            if self.spec.timeframe not in frames:
                return None
            btc = ({"15m": frames["BTC_1h"]}
                   if frames.get("BTC_1h") is not None else None)
            try:
                lo, sh = self.entries(frames, btc=btc,
                                      derivs=getattr(snap, "derivs", None),
                                      universe=getattr(snap, "universe", None),
                                      symbol=snap.symbol)
            except Exception as e:
                # Silence here cost three authored specs their entire live
                # career: they raised on every bar and read as "no signal".
                log.warning("spec %s failed to evaluate on %s: %s",
                            self.spec.id, snap.symbol, e)
                return None
            if not len(lo):
                return None
            if lo[-1]:
                action, why = Action.BUY, self.spec.entry_long
            elif sh[-1]:
                action, why = Action.SELL, self.spec.entry_short
            else:
                return None
            # How stale is the bar this fired on? The backtest fills at the
            # signal bar's own close (vector_backtest.py:115), so a live fill
            # far into the bar is a different trade at a different price.
            # Recorded, not gated: the right cutoff is a measurement nobody
            # has taken yet, and guessing one risks suppressing real entries.
            bar_age_min = None
            try:
                sig_tf = frames[self.spec.timeframe]
                closed_at = (sig_tf["ts"].iloc[-1].timestamp() * 1000
                             + TF_MS.get(self.spec.timeframe, 0))
                bar_age_min = round(
                    (time.time() * 1000 - closed_at) / 60_000, 1)
            except Exception:
                pass
            return StrategySignal(
                strategy_id=self.spec.id, strategy_name=self.spec.name,
                symbol=snap.symbol, action=action,
                confidence=0.6,
                rationale=f"{self.spec.name}: {why}",
                params={"spec_id": self.spec.id,
                        "signal_bar_age_min": bar_age_min})
        return _evaluate

    # ── tradingview path ─────────────────────────────────────────────────
    def to_pine(self, fee_pct: float = 0.05) -> tuple[str, bool, list]:
        """(pine_source, tv_testable, reasons).

        Honest degradation: a spec whose features have no Pine analogue
        (funding, open interest, flow) returns tv_testable=False with the
        reason, rather than a substituted proxy. Substituting a proxy is what
        made FAMILY_TV score every candidate of a family identically.
        """
        from .pine_spec import to_pine as _to_pine
        return _to_pine(self.spec, fee_pct=fee_pct)

    # ── librarian path ───────────────────────────────────────────────────
    def to_markdown(self) -> str:
        s = self.spec
        ex = s.exit
        prov = s.provenance or {}
        src = prov.get("source_kind", "unknown")
        if prov.get("source_url"):
            src += f" — {prov['source_url']}"
        lines = [
            f"# {s.name}", "",
            f"**Thesis** — {s.thesis}", "",
            f"**Invalidation** — {s.invalidation}", "",
            f"- Timeframe: `{s.timeframe}`  ·  Direction: `{s.direction}`",
            f"- Regimes: {', '.join(s.regime_filter) or 'any'}",
            f"- Data: {', '.join(self.data_requires)}",
            f"- Provenance: {src}",
            "", "## Logic", "```",
        ]
        if s.entry_long:
            lines.append(f"long:   {s.entry_long}")
        if s.entry_short:
            lines.append(f"short:  {s.entry_short}")
        for f in s.filters:
            lines.append(f"filter: {f}")
        lines += [
            f"stop:   {ex.stop}",
            f"target: {ex.target}",
            f"trail:  {ex.trail}",
            f"time:   max {ex.time.get('max_bars')} bars",
        ]
        if ex.signal_exit:
            lines.append(f"exit:   {ex.signal_exit}")
        lines += ["```", ""]
        return "\n".join(lines)


def compile_spec(spec: StrategySpec) -> CompiledStrategy:
    """Parse every expression up front. A spec that cannot compile must never
    reach the gauntlet, let alone the book."""
    errs = StrategySpec.validate(spec)
    if errs:
        raise dsl.SpecError(f"invalid spec '{spec.id}': {errs}")
    long_t = dsl.parse(spec.entry_long) if (spec.entry_long or "").strip() \
        else None
    short_t = dsl.parse(spec.entry_short) if (spec.entry_short or "").strip() \
        else None
    filters = [dsl.parse(f) for f in (spec.filters or []) if (f or "").strip()]
    exit_t = dsl.parse(spec.exit.signal_exit) \
        if (spec.exit.signal_exit or "").strip() else None
    trees = [t for t in (long_t, short_t, exit_t, *filters) if t is not None]
    req = dsl.data_requires(*trees)
    spec.data_requires = list(req)
    return CompiledStrategy(spec=spec, _long=long_t, _short=short_t,
                            _filters=filters, _exit=exit_t, data_requires=req)
