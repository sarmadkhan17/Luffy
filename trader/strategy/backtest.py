"""Vectorized-enough backtester — replays a genome bar-by-bar through its own
evaluator, simulating fills pessimistically (SL before TP when both hit
intrabar), netting taker fees both sides.

This is the gauntlet every proposed strategy must survive before paper
probation, and the tool the brain uses to test mutations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..agents.indicators import atr as _atr
from ..core.types import Action, Snapshot
from . import library as strat_lib
from .genome import Genome

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    genome_id: str
    symbol: str
    bars: int
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl_usdt: float = 0.0
    gross_win: float = 0.0
    gross_loss: float = 0.0
    max_dd_pct: float = 0.0
    errors: list = field(default_factory=list)
    exit_semantics_id: str | None = None
    exit_reasons: dict = field(default_factory=dict)
    ambiguous_exit_count: int = 0
    censored_position_count: int = 0

    @property
    def profit_factor(self) -> float:
        return self.gross_win / self.gross_loss if self.gross_loss > 0 else \
            (99.0 if self.gross_win > 0 else 0.0)

    @property
    def winrate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    def passes(self, min_trades=20, min_pf=1.15, max_dd=12.0,
               min_winrate=0.40) -> tuple[bool, list[str]]:
        fails = []
        if self.trades < min_trades:
            fails.append(f"trades {self.trades}<{min_trades}")
        if self.trades and self.profit_factor < min_pf:
            fails.append(f"PF {self.profit_factor:.2f}<{min_pf}")
        if self.trades and self.winrate < min_winrate:
            fails.append(f"winrate {self.winrate:.0%}<{min_winrate:.0%}")
        if self.max_dd_pct > max_dd:
            fails.append(f"DD {self.max_dd_pct:.1f}%>{max_dd}%")
        return (not fails), fails


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate OPEN-labelled candles, retaining exact constituent ancestry.

    The interval is [open, close); final values cannot precede either its
    close or any constituent's availability. Incomplete buckets stay partial.
    """
    from ..data import market_provenance as mp
    from ..core.types import TF_MS
    if df is None or df.empty or 'ts' not in df:
        return df
    duration = TF_MS[rule]
    opens = mp.ms(df['ts'])
    own = df.attrs.get('timeframe')
    qualified = all(k in df for k in mp.META)
    if qualified and own not in TF_MS:
        raise ValueError('qualified aggregation requires source timeframe')
    step = TF_MS.get(own) or (int(np.median(np.diff(opens))) if len(opens) > 1 else duration)
    if step > duration or duration % step:
        raise ValueError('cannot aggregate a finer interval from coarse bars')
    records = []
    for bucket, group in df.assign(_bucket=opens//duration*duration).groupby('_bucket'):
        stamps = mp.ms(group['ts'])
        complete = np.array_equal(stamps, np.arange(bucket, bucket+duration, step))
        value = dict(ts=pd.to_datetime(bucket, unit='ms', utc=True),
                     open=group['open'].iloc[0], high=group['high'].max(skipna=False),
                     low=group['low'].min(skipna=False), close=group['close'].iloc[-1],
                     volume=group['volume'].sum(min_count=len(group)))
        if qualified:
            complete = complete and group['bar_state'].eq('FINAL').all()
            receipts = group['observed_at_ms'].astype('int64')
            available = group['available_at_ms']
            known = int(available.max()) if available.notna().all() else None
            received = int(receipts.max())
            ids = group['revision_id'].tolist()
            quality=next((state for state in ('INVALID','UNKNOWN','MISSING','STALE','SUSPECT','REPAIRED','UNSUPPORTED','INCOMPLETE')
                          if group['quality'].eq(state).any()),'VALID')
            if group['instrument_id'].nunique(dropna=False)!=1 or group['source'].nunique(dropna=False)!=1:
                quality='INVALID'
            elif quality=='VALID' and (not complete or known is None):
                quality='INCOMPLETE'
            body = {k: group[k].iloc[0] for k in mp.META}
            body.update(event_time_ms=int(bucket), observed_at_ms=received,
                        available_at_ms=max(int(bucket)+duration, known) if known is not None and complete else known,
                        request_id=mp.digest(ids), content_hash=mp.digest(ids),
                        revision_id=mp.digest(dict(tf=rule, open=int(bucket), sources=ids,
                                                  derivation='ohlcv.aggregate.v1')),
                        supersedes=None, raw_json=mp.encode(dict(constituents=ids, timeframe=rule,
                                                open_ms=int(bucket), close_ms=int(bucket)+duration)),
                        transform_version='ohlcv.aggregate.v1',
                        bar_state='FINAL' if complete else 'PARTIAL',
                        quality=quality)
            value.update(body)
        records.append(value)
    out = pd.DataFrame(records)
    out.attrs.update(df.attrs)
    out.attrs.update(timeframe=rule, timestamp_semantics='interval_open',
                     transform_version='ohlcv.aggregate.v1')
    return out


def _snap(df: pd.DataFrame, price: float,
          ctx: dict[str, pd.DataFrame] | None = None) -> Snapshot:
    """Build the evaluator's view of the market at this bar.

    `ctx` carries higher-timeframe and cross-asset frames ALREADY sliced to
    the current bar by the caller. Without it, families that read 1h/4h or
    'BTC_1h' (e.g. rotation_momo) return None on every bar and silently
    score zero trades in every backtest.
    """
    dfs: dict[str, pd.DataFrame] = {"15m": df}
    if ctx:
        dfs.update({k: v for k, v in ctx.items() if v is not None and len(v)})
    return Snapshot(symbol="BT", ts="", price=price, dfs=dfs,
                    market_type="futures")


def backtest(genome: Genome, df: pd.DataFrame, risk_cfg: dict,
             equity: float = 2000.0,
             ctx: dict[str, pd.DataFrame] | None = None) -> BacktestResult:
    if df.attrs.get('schema_version') == 'market.receipt.v1' and df.attrs.get('read_mode') != 'replay':
        raise ValueError('historical backtest requires replay revisions, not latest/as-of snapshot')
    if 'quality' in df and not df['quality'].eq('VALID').all():
        raise ValueError('historical backtest requires valid market evidence')
    res = BacktestResult(genome_id=genome.strategy_id, symbol="BT",
                         bars=len(df))
    sl_mult = float(risk_cfg["stop_loss_atr_mult"])
    tp_mult = float(risk_cfg["take_profit_atr_mult"])
    fee = float(risk_cfg.get("taker_fee_pct", 0.05)) / 100.0
    slip_frac = float(risk_cfg.get("slippage_atr_frac", 0.06))
    risk_frac = float(risk_cfg["risk_per_trade_pct"]) / 100.0
    # perp carry: charged every 8h a position is held. Absent from the old
    # model entirely, which flattered every strategy holding overnight.
    funding_8h = float(risk_cfg.get("funding_rate_8h", 0.0001))
    bar_minutes = float(risk_cfg.get("bar_minutes", 15))

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    warmup = 210
    step = 1                       # evaluate EVERY bar; step=2 hid half the setups

    # point-in-time index for context frames (HTF / cross-asset)
    ts_vals = (pd.to_datetime(df["ts"], utc=True).values
               if "ts" in df.columns else None)
    ctx_idx = {}
    if ctx and ts_vals is not None:
        for k, cdf in ctx.items():
            if cdf is not None and len(cdf) and "ts" in cdf.columns:
                from ..data.market_provenance import ms
                from ..core.types import TF_MS
                frame_tf = k.removeprefix('BTC_')
                known = ms(cdf['ts']) + TF_MS.get(frame_tf, 0)
                if 'available_at_ms' in cdf:
                    known = np.maximum(known, cdf['available_at_ms'].fillna(np.iinfo(np.int64).max).to_numpy(dtype='int64'))
                ctx_idx[k] = (cdf, np.maximum.accumulate(known))

    def ctx_at(i: int) -> dict:
        """Context frames truncated to bars that had CLOSED by bar i."""
        if not ctx_idx:
            return {}
        from ..data.market_provenance import ms
        from ..core.types import TF_MS
        now = ms(df['ts'])[i] + TF_MS[df.attrs.get('timeframe') or '15m']
        out = {}
        for k, (cdf, cts) in ctx_idx.items():
            j = int(np.searchsorted(cts, now, side="right"))
            if j > 0:
                out[k] = cdf.iloc[max(0, j - 400):j]
        return out

    pos = None                     # dict(side, entry, amount, sl, tp, entry_i)
    peak, trough = 0.0, 0.0
    equity_curve = [equity]

    def close_pos(exit_px: float, i: int):
        nonlocal pos, res, peak, trough
        direction = 1.0 if pos["side"] == "long" else -1.0
        # exits pay the spread too — previously they filled exactly at sl/tp,
        # which quietly handed every trade a free tick.
        exit_px = exit_px - direction * pos["slip"]
        gross = (exit_px - pos["entry"]) * direction * pos["amount"]
        fees = fee * (pos["entry"] + exit_px) * pos["amount"]
        hours_held = (i - pos["entry_i"]) * bar_minutes / 60.0
        funding = (abs(funding_8h) * (hours_held / 8.0)
                   * exit_px * pos["amount"])
        pnl = gross - fees - funding
        res.trades += 1
        res.pnl_usdt += pnl
        if pnl > 0:
            res.wins += 1
            res.gross_win += pnl
        else:
            res.losses += 1
            res.gross_loss += abs(pnl)
        equity_curve.append(equity_curve[-1] + pnl)
        cur = equity_curve[-1]
        peak = max(peak, cur)
        trough = min(trough, cur)
        dd = (peak - cur) / peak * 100 if peak > 0 else 0.0
        res.max_dd_pct = max(res.max_dd_pct, dd)
        pos = None

    i = warmup
    while i < n - 1:
        window = df.iloc[max(0, i - 400):i + 1]
        px = float(closes[i])

        if pos is not None:
            # ── manage open position: pessimistic SL-first ──────────────
            hit_sl = (lows[i] <= pos["sl"]) if pos["side"] == "long" \
                else (highs[i] >= pos["sl"])
            hit_tp = (highs[i] >= pos["tp"]) if pos["side"] == "long" \
                else (lows[i] <= pos["tp"])
            if hit_sl:
                close_pos(pos["sl"], i)
            elif hit_tp:
                close_pos(pos["tp"], i)
            elif i - pos["entry_i"] >= int(
                    genome.params.get("max_hold_bars", 32)):
                close_pos(px, i)
            i += 1
            continue

        try:
            sig = strat_lib.evaluate(genome, _snap(window, px, ctx_at(i)))
        except Exception as e:
            res.errors.append(str(e)[:80])
            sig = None
        if sig is None or sig.action not in (Action.BUY, Action.SELL):
            i += step
            continue

        a = _atr(window)
        side = "long" if sig.action == Action.BUY else "short"
        sl_dist = max(a * sl_mult, px * 0.004)
        slip = a * slip_frac
        entry = px + (slip if side == "long" else -slip)   # pay spread
        sl = entry - sl_dist if side == "long" else entry + sl_dist
        tp = entry + a * tp_mult if side == "long" else entry - a * tp_mult
        amount = (equity_curve[-1] * risk_frac) / sl_dist
        if amount * px < 10:                                # dust guard
            i += step
            continue
        pos = {"side": side, "entry": entry, "amount": amount,
               "sl": sl, "tp": tp, "entry_i": i, "slip": slip}
        i += 1

    if pos is not None:
        close_pos(float(closes[-1]), n - 1)
    return res


def context_frames(df: pd.DataFrame,
                   btc_1h: pd.DataFrame | None = None) -> dict:
    """Standard context bundle for a 15m frame: its own 1h/4h resamples plus
    the BTC 1h leader. Pass to backtest()/walk_forward() as `ctx`."""
    ctx = {"1h": resample(df, "1h"), "4h": resample(df, "4h")}
    if btc_1h is not None and len(btc_1h):
        ctx["BTC_1h"] = btc_1h
    return ctx


def walk_forward(genome: Genome, df: pd.DataFrame, risk_cfg: dict,
                 split: float = 0.7,
                 ctx: dict[str, pd.DataFrame] | None = None) -> dict:
    """Train/test discipline: fit nothing (genes fixed), but require BOTH
    halves to behave — catches luck masquerading as edge."""
    cut = int(len(df) * split)
    train = backtest(genome, df.iloc[:cut], risk_cfg, ctx=ctx)
    test = backtest(genome, df.iloc[cut:], risk_cfg, ctx=ctx)
    ok_train, f_train = train.passes()
    ok_test, f_test = test.passes(min_trades=max(3, int(train.trades * 0.2)),
                                  min_pf=1.0)
    return {"train": train, "test": test,
            "robust": ok_train and ok_test,
            "train_fails": f_train, "test_fails": f_test}


def score_results(results: list[dict]) -> tuple:
    """Rank key for a candidate genome from its OWN walk-forward evidence.

    Ordered so that plain tuple comparison prefers: robust on every symbol >
    worst-case out-of-sample profit factor > out-of-sample activity. This
    replaces the family-level TradingView score, which was identical for
    every candidate of a family and therefore ranked nothing.
    """
    if not results:
        return (0, 0.0, 0)
    all_robust = int(all(r["robust"] for r in results))
    test_pfs = [r["test"].profit_factor for r in results
                if r["test"].trades >= 2]
    worst_pf = min(test_pfs) if test_pfs else 0.0
    trades = sum(r["test"].trades for r in results)
    return (all_robust, round(worst_pf, 4), trades)
