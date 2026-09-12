"""Where the search is allowed to look.

One CALENDAR cut per horizon, at 70% of the universe's own span. The
alternative — each symbol's own 70% — puts a young market's discovery slice
inside an old market's held-out era, so the "unseen era" would already have
been searched somewhere else. A calendar cut makes held-out B genuinely
later for every symbol in it.

`WARMUP` is a BAR COUNT, not a duration: `simulate` skips the first 210 bars
of every slice it is handed. A slice shorter than that plus room to trade
carries no information, so it is dropped rather than scored on noise.
"""
from __future__ import annotations

import pandas as pd

from ..strategy.vector_backtest import WARMUP

#: the share of the universe's span the search may see
DISCOVERY_FRAC = 0.7
#: a slice shorter than this cannot survive WARMUP and still trade
MIN_SLICE_BARS = WARMUP + 210


def _ms(df) -> pd.Series:
    """Convert timestamp column to milliseconds since epoch.

    Handles both datetime64[ms] and datetime64[ns] resolutions.
    Returns milliseconds since epoch as a Series.
    """
    ts = df["ts"]
    # Get the underlying integer values in the native unit
    # datetime64[ms] -> int64 in milliseconds
    # datetime64[ns] -> int64 in nanoseconds
    int_vals = ts._values.view("int64")

    # Determine the resolution and convert to milliseconds
    dtype_str = str(ts.dtype)
    if "ms" in dtype_str:
        # Already in milliseconds
        return pd.Series(int_vals, index=ts.index)
    elif "ns" in dtype_str:
        # Convert from nanoseconds to milliseconds
        return pd.Series(int_vals // 10 ** 6, index=ts.index)
    else:
        # Unknown resolution, assume nanoseconds (the brief's default)
        return pd.Series(int_vals // 10 ** 6, index=ts.index)


def cut_ms(frames: dict, frac: float = DISCOVERY_FRAC) -> int | None:
    """The one timestamp that separates discovery from held-out B."""
    lo, hi = None, None
    for df in (frames or {}).values():
        if df is None or not len(df):
            continue
        m = _ms(df)
        lo = int(m.iloc[0]) if lo is None else min(lo, int(m.iloc[0]))
        hi = int(m.iloc[-1]) if hi is None else max(hi, int(m.iloc[-1]))
    if lo is None or hi is None or hi <= lo:
        return None
    return lo + int((hi - lo) * float(frac))


def before(df, cut: int):
    if df is None or not len(df):
        return df
    return df[_ms(df) < int(cut)].reset_index(drop=True)


def after(df, cut: int):
    if df is None or not len(df):
        return df
    return df[_ms(df) >= int(cut)].reset_index(drop=True)


def _slice_all(frames: dict, cut: int, fn, min_bars: int) -> dict:
    out = {}
    for sym, df in (frames or {}).items():
        part = fn(df, cut)
        if part is not None and len(part) >= min_bars:
            out[sym] = part
    return out


def discovery(frames: dict, cut: int, min_bars: int = MIN_SLICE_BARS) -> dict:
    """Every bar that had closed before the cut."""
    return _slice_all(frames, cut, before, min_bars)


def heldout_b(frames: dict, cut: int, min_bars: int = MIN_SLICE_BARS) -> dict:
    """The later era. Phase 2 counts its BARS and never reads its prices."""
    return _slice_all(frames, cut, after, min_bars)


def bar_counts(frames: dict) -> dict:
    return {s: int(len(df)) for s, df in (frames or {}).items()
            if df is not None}


def usable_bars(n: int) -> int:
    """Bars a slice can actually trade on, after the engine's warmup."""
    return max(0, int(n) - WARMUP)
