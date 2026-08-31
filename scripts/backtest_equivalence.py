"""Equivalence harness — the gate on the vectorized engine.

Two independent claims, tested separately:

  engine_equivalence  — identical signals + identical exit geometry must
                        produce identical trades in both engines. Isolates
                        the fill and accounting model.
  signal_equivalence  — a ported spec's DSL entry array must match the
                        legacy evaluator's bar-by-bar signals. Isolates the
                        translation. Deltas here are expected for families
                        whose Python logic is not expressible one-to-one;
                        they are REPORTED, not asserted away.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.types import Action                        # noqa: E402
from trader.strategy import library as strat_lib            # noqa: E402
from trader.strategy.backtest import _snap, context_frames  # noqa: E402
from trader.strategy.spec import ExitSpec                   # noqa: E402
from trader.strategy.vector_backtest import WARMUP, simulate  # noqa: E402


def legacy_signals(genome, df: pd.DataFrame, ctx: dict | None = None):
    """Replay the legacy evaluator bar-by-bar and record its raw signals.

    This deliberately does NOT skip bars while a position is open — the
    unconditioned signal array is what lets both engines be fed the same
    input. Position management is the engines' job, and it is what we are
    trying to compare.
    """
    n = len(df)
    lo = np.zeros(n, dtype=bool)
    sh = np.zeros(n, dtype=bool)
    ts_vals = (pd.to_datetime(df["ts"], utc=True).values
               if "ts" in df.columns else None)
    ctx_idx = {}
    if ctx and ts_vals is not None:
        for k, cdf in ctx.items():
            if cdf is not None and len(cdf) and "ts" in cdf.columns:
                ctx_idx[k] = (cdf, pd.to_datetime(cdf["ts"], utc=True).values)

    def ctx_at(i):
        if not ctx_idx:
            return {}
        now = ts_vals[i]
        out = {}
        for k, (cdf, cts) in ctx_idx.items():
            j = int(np.searchsorted(cts, now, side="right"))
            if j > 0:
                out[k] = cdf.iloc[max(0, j - 400):j]
        return out

    for i in range(WARMUP, n - 1):
        window = df.iloc[max(0, i - 400):i + 1]
        try:
            sig = strat_lib.evaluate(
                genome, _snap(window, float(df["close"].iloc[i]), ctx_at(i)))
        except Exception:
            sig = None
        if sig is None:
            continue
        if sig.action == Action.BUY:
            lo[i] = True
        elif sig.action == Action.SELL:
            sh[i] = True
    return lo, sh


def config_equivalent_exit(risk_cfg: dict, max_hold: int = 32) -> ExitSpec:
    """The exit geometry the OLD engine hardcoded for every strategy."""
    return ExitSpec(
        stop={"kind": "atr", "mult": float(risk_cfg["stop_loss_atr_mult"])},
        target={"kind": "atr", "mult": float(risk_cfg["take_profit_atr_mult"])},
        trail={"kind": "none"},
        time={"max_bars": max_hold},
        signal_exit="")


def engine_equivalence(genome, df: pd.DataFrame, risk_cfg: dict,
                       ctx: dict | None = None) -> dict:
    from trader.strategy.backtest import backtest as legacy_backtest

    lo, sh = legacy_signals(genome, df, ctx)
    old = legacy_backtest(genome, df, risk_cfg, ctx=ctx)
    max_hold = int(genome.params.get("max_hold_bars", 32))
    new = simulate(lo, sh, df, config_equivalent_exit(risk_cfg, max_hold),
                   risk_cfg, genome_id=genome.strategy_id)
    match = (old.trades == new.trades and old.wins == new.wins
             and abs(old.pnl_usdt - new.pnl_usdt)
             <= max(0.01, abs(old.pnl_usdt) * 0.005))
    return {"family": genome.family, "signals": int(lo.sum() + sh.sum()),
            "old": {"trades": old.trades, "wins": old.wins,
                    "pnl": round(old.pnl_usdt, 4)},
            "new": {"trades": new.trades, "wins": new.wins,
                    "pnl": round(new.pnl_usdt, 4)},
            "match": bool(match)}


def signal_equivalence(compiled, genome, frames: dict, btc=None) -> dict:
    tf = compiled.spec.timeframe
    df = frames[tf]
    ctx = context_frames(df, btc)
    old_lo, old_sh = legacy_signals(genome, df, ctx)
    new_lo, new_sh = compiled.entries(
        frames, btc={"15m": btc} if btc is not None else None)
    agree = int((old_lo == new_lo).sum() + (old_sh == new_sh).sum())
    total = len(old_lo) * 2
    return {"spec": compiled.spec.id, "family": genome.family,
            "legacy_signals": int(old_lo.sum() + old_sh.sum()),
            "spec_signals": int(new_lo.sum() + new_sh.sum()),
            "bar_agreement": round(agree / total, 4)}


if __name__ == "__main__":
    import json

    from trader.core.config import load_config
    from trader.data.feed import DataFeed
    from trader.strategy import evidence
    from trader.strategy.library import build_seed_population

    cfg = load_config()
    frames = evidence.load_frames(DataFeed(), cfg)
    syms = [k for k in frames if not k.startswith("_")]
    if not syms:
        print("no candle data — cannot run equivalence")
        sys.exit(1)
    sym = syms[0]
    df = frames[sym]
    btc = frames.get("_btc_1h")
    ctx = context_frames(df, btc)
    print(f"engine equivalence on {sym} ({len(df)} bars)\n")
    ok = True
    for _st, g in build_seed_population():
        rep = engine_equivalence(g, df, cfg["risk"], ctx=ctx)
        ok &= rep["match"] or rep["signals"] == 0
        print(json.dumps(rep))
    print("\nENGINE EQUIVALENCE:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
