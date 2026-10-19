"""D6 — where a closed-bar signal may legitimately fill.

Three facts stay separate: candle close, receipt availability, and the fill.
A signal on bar k is decided no earlier than max(close_k, availability of every
bar in its lookback): a decision cannot precede its inputs. It fills at the
open of the first retained bar starting at or after that instant, never at the
signal candle's close.

Validity: StrategySpec declares no entry validity, so the existing freshness
budget applies (interval + TTL after the close, `market_provenance.freshness_ms`
-- the same age past which the feed and kernel treat a closed bar as STALE),
measured at the fill. No new parameter. Missing or unknown inputs refuse.

Frames with no receipt columns, and explicitly retrospective datasets, decide
at close under their own declared "no historical availability claim" evidence
scope (never factory-validated); in a replay frame, unknown availability
refuses.
"""
from __future__ import annotations

import ast
from functools import lru_cache

import numpy as np
import pandas as pd

from ..core.types import TF_MS
from ..data import market_provenance as mp

POLICY_ID = "next-executable-open.v2"
#: engine-intrinsic minimum: ATR(14) sizing plus the previous bar
MIN_LOOKBACK = 15
#: exponentially smoothed features carry memory well beyond their period
_SMOOTHED = {"ema": 3, "rsi": 3, "atr": 3, "adx": 3}
#: the existing engine warm-up (vector_backtest.WARMUP): the longest history
#: any supported strategy is already required to have; never exceeded here
_CAP = 210


def _tf(df) -> str:
    tf = df.attrs.get("timeframe")
    if tf in TF_MS:
        return tf
    step = int(np.median(np.diff(mp.ms(df["ts"])))) if len(df) > 1 else 0
    for name, size in TF_MS.items():
        if size == step:
            return name
    raise ValueError("fill_timing_timeframe_unknown")


def _span(node) -> int:
    """Upper bound of base-frame bars an expression looks back over."""
    from .features import FEATURES, SERIES_ARG
    if isinstance(node, ast.Call):
        name = node.func.id
        if name in ("htf", "ref"):          # evaluated on another frame
            return 0
        period, inner = 0, 0
        for arg, aspec in zip(node.args, FEATURES[name].arg_specs):
            if aspec is SERIES_ARG:
                inner = max(inner, _span(arg))
            elif aspec[0] is int and isinstance(arg, ast.Constant):
                period = max(period, int(arg.value))
        return period * _SMOOTHED.get(name, 1) + inner
    return max((_span(child) for child in ast.iter_child_nodes(node)), default=0)


@lru_cache(maxsize=256)
def _lookback(expressions: tuple, swing: int) -> int:
    from .dsl import parse
    deepest = max((_span(parse(e)) for e in expressions), default=0)
    return min(max(MIN_LOOKBACK, deepest + 1, swing + 1), _CAP)


def lookback_bars(spec) -> int:
    """Contiguous history a spec's own expressions and stop actually consume."""
    stop = spec.exit.stop or {}
    swing = int(stop.get("lookback", 20)) if stop.get("kind") == "swing" else 0
    expressions = tuple(e for e in (spec.entry_long, spec.entry_short, *spec.filters,
                                    spec.exit.signal_exit) if e)
    return _lookback(expressions, swing)


def fills(df):
    """(fill_i, reason) per signal bar: first executable bar index, else -1 and why.

    The history a signal needs is `df.attrs['signal_lookback_bars']` (bound by
    the compiled spec), else MIN_LOOKBACK. Reasons: availability_unknown (a
    lookback input has no recorded availability), signal_stale (the first
    executable open is past interval + TTL after the close), no_executable_bar,
    unexecutable_price (non-finite open) and, for receipt-bearing frames only,
    history_gap (a bar missing from the lookback)."""
    n = len(df)
    fill = np.full(n, -1, dtype=np.int64)
    why = np.full(n, "", dtype=object)
    if n == 0:
        return fill, why
    tf = _tf(df)
    step = TF_MS[tf]
    window = int(df.attrs.get("signal_lookback_bars") or MIN_LOOKBACK)
    ts = mp.ms(df["ts"])
    receipts = "available_at_ms" in df and df.attrs.get("read_mode") != "retrospective"
    close = (ts + step).astype(float)
    decision = close.copy()
    unknown = np.zeros(n, dtype=bool)
    gapped = np.zeros(n, dtype=bool)
    if receipts:
        available = pd.Series(pd.to_numeric(df["available_at_ms"], errors="coerce").to_numpy(float))
        unknown = available.isna().rolling(window, min_periods=1).max().to_numpy(bool)
        decision = np.maximum(close, available.rolling(window, min_periods=1).max().to_numpy())
        hole = np.r_[False, np.diff(ts) != step]
        gapped = pd.Series(hole).rolling(window, min_periods=1).max().to_numpy(bool)
    first = np.searchsorted(ts, np.where(unknown, 0.0, decision), side="left")
    clipped = np.minimum(first, n - 1)
    opens = df["open"].to_numpy(float)
    for reason, refused in (
            ("history_gap", gapped),
            ("unexecutable_price", ~np.isfinite(opens[clipped])),
            ("signal_stale", ts[clipped] - close > mp.freshness_ms(tf)),
            ("no_executable_bar", first >= n),
            ("availability_unknown", unknown)):
        why[refused] = reason          # later entries take precedence
    fill[why == ""] = first[why == ""]
    return fill, why


def summary(df) -> dict:
    """Counts of executable signal bars and refusals by reason, for evidence."""
    fill, why = fills(df)
    out = {"policy": POLICY_ID, "executable": int((fill >= 0).sum())}
    for reason in sorted(set(why[fill < 0])):
        out[reason] = int((why == reason).sum())
    return out
