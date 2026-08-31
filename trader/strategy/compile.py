"""Compile a StrategySpec into the three things the company needs from it:
vectorized entry arrays (Analyst), a live evaluator (Trader), and a readable
card (Librarian). One artifact, three consumers, no drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.types import Action, StrategySignal
from . import dsl
from .features import FeatureCtx
from .spec import StrategySpec


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
                derivs: dict | None = None):
        ctx = self._ctx(frames, btc, derivs)
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

    def exit_signal(self, frames: dict, btc=None, derivs=None):
        if self._exit is None:
            return None
        return dsl.evaluate_bool(self._exit, self._ctx(frames, btc, derivs))

    def _ctx(self, frames, btc, derivs) -> FeatureCtx:
        tf = self.spec.timeframe
        if tf not in frames:
            raise dsl.SpecError(f"spec timeframe '{tf}' not in frames "
                                f"{sorted(frames)}")
        return FeatureCtx(frames=frames, tf=tf, btc=btc, derivs=derivs)

    # ── live path (Trader) ───────────────────────────────────────────────
    def to_evaluator(self):
        """A callable with the EXACT signature library.evaluate() dispatches
        to: (genome_like, Snapshot) -> StrategySignal | None.

        This is why the orchestrator needs no change at cutover — registering
        this under f"spec:{id}" makes library.evaluate()'s family dispatch
        (library.py:293) find it like any other evaluator.
        """
        def _evaluate(_genome, snap):
            frames = {k: v for k, v in snap.dfs.items()
                      if v is not None and len(v)}
            if self.spec.timeframe not in frames:
                return None
            btc = ({"15m": snap.dfs["BTC_1h"]}
                   if snap.dfs.get("BTC_1h") is not None else None)
            try:
                lo, sh = self.entries(frames, btc=btc)
            except Exception:
                return None
            if not len(lo):
                return None
            if lo[-1]:
                action, why = Action.BUY, self.spec.entry_long
            elif sh[-1]:
                action, why = Action.SELL, self.spec.entry_short
            else:
                return None
            return StrategySignal(
                strategy_id=self.spec.id, strategy_name=self.spec.name,
                symbol=snap.symbol, action=action,
                confidence=0.6,
                rationale=f"{self.spec.name}: {why}",
                params={"spec_id": self.spec.id})
        return _evaluate

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
