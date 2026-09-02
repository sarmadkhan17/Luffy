"""Does this strategy beat its own signals fired at a random time?

The gauntlet asked "is profit factor >= 1.15", which is a question about
arithmetic, not about edge. Exit geometry alone sets a win rate: with TP 4.5
ATR and SL 2.5 ATR a coin-flip entry wins 2.5/(2.5+4.5) = 35.7% of the time,
and after costs that lands near PF 0.76. Measured on production candles, an
ALWAYS-LONG rule scored exactly that — median PF 0.76, win rate 38% — while
the book's specs scored 0.46-0.81. They were at or below the no-edge line and
the gate could not see it, because 0.81 and 0.76 both just read as "fail".

The null here is a circular rotation of the entry array. Signal count, run
lengths and spacing survive untouched; only the alignment between signal and
market state is destroyed. So the comparison isolates the one thing a
strategy claims to have: timing.

A spec that cannot beat its own rotation is not a weak edge. It is no edge,
and no amount of parameter tuning or cost reduction will make it one.
"""
from __future__ import annotations

import numpy as np

from .vector_backtest import WARMUP, simulate

#: draws below this make the percentile too coarse to act on
MIN_DRAWS = 20


def rotate_entries(a: np.ndarray, offset: int) -> np.ndarray:
    """Circularly shift a boolean entry array.

    np.roll and not a reshuffle: shuffling would break up clustered signals
    and hand the null a different trade-spacing profile than the strategy,
    which makes the comparison meaningless.
    """
    return np.roll(np.asarray(a, dtype=bool), int(offset))


def null_pfs(long: np.ndarray, short: np.ndarray, df, exit_spec,
             risk_cfg: dict, draws: int = 100, seed: int = 0,
             symbol: str = "BT") -> list[float]:
    """Profit factors from `draws` rotations of the same signals."""
    n = len(df)
    if n <= WARMUP + 2:
        return []
    rng = np.random.default_rng(seed)
    # keep the rotation clear of the warmup edge at both ends
    lo_off, hi_off = WARMUP + 1, max(WARMUP + 2, n - WARMUP - 1)
    if hi_off <= lo_off:
        return []
    out: list[float] = []
    for off in rng.integers(lo_off, hi_off, size=int(draws)):
        r = simulate(rotate_entries(long, off), rotate_entries(short, off),
                     df, exit_spec, risk_cfg, symbol=symbol)
        if r.trades > 0:
            out.append(float(r.profit_factor))
    return out


def edge_percentile(actual_pf: float, null: list[float]) -> float | None:
    """Share of null draws the strategy beats. None when the null is empty.

    0.95 means the strategy beat 95% of its own rotations — the timing is
    carrying information. 0.50 means it is indistinguishable from firing the
    same signals at an arbitrary moment.
    """
    if not null:
        return None
    return sum(1 for p in null if actual_pf > p) / len(null)


def assess(compiled, frames: dict, risk_cfg: dict, actual_pf: float,
           btc=None, derivs=None, universe=None, market=None,
           symbol: str = "BT", draws: int = 100, seed: int = 0,
           split: float | None = None, part: str = "test") -> dict:
    """{null_median, null_p90, percentile, draws, bars} for one spec/symbol.

    `split`/`part` select the same slice the actual profit factor came from.
    Measuring a TEST-half PF against a full-frame null is not a comparison:
    over a test half that rose 20-54% it inflates every percentile, and an
    always-long rule — whose rotation is itself — read as 100%.
    """
    tf = compiled.spec.timeframe
    df = frames[tf]
    lo, sh = compiled.entries(frames, btc=btc, derivs=derivs,
                              universe=universe, market=market, symbol=symbol)
    if split is not None:
        cut = int(len(df) * float(split))
        sl = slice(cut, len(df)) if part == "test" else slice(0, cut)
        df = df.iloc[sl].reset_index(drop=True)
        lo, sh = lo[sl], sh[sl]
    pfs = null_pfs(lo, sh, df, compiled.spec.exit, risk_cfg,
                   draws=draws, seed=seed, symbol=symbol)
    if len(pfs) < MIN_DRAWS:
        return {"draws": len(pfs), "bars": len(df), "percentile": None,
                "reason": f"only {len(pfs)} usable null draws"}
    arr = np.array(pfs, dtype=float)
    return {"draws": len(pfs), "bars": len(df),
            "null_median": round(float(np.median(arr)), 3),
            "null_p90": round(float(np.percentile(arr, 90)), 3),
            "percentile": edge_percentile(actual_pf, pfs)}
