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

from math import comb

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
             symbol: str = "BT", funding: np.ndarray | None = None
             ) -> list[float]:
    """Profit factors from `draws` rotations of the same signals.

    `funding` is the same signed per-bar series the actual result was charged.
    It must be passed, because a rotation moves the ENTRIES and leaves the
    market where it is: the carry on a given bar is a property of that bar,
    so it stays aligned. Omitting it charged every null draw the flat
    `abs(funding_8h)` to both sides while the actual paid the venue's real
    signed rate — a control made more expensive than the thing it controls,
    which inflates the percentile of every strategy measured against it.
    """
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
                     df, exit_spec, risk_cfg, symbol=symbol, funding=funding)
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
           split: float | None = None, part: str = "test",
           funding: np.ndarray | None = None) -> dict:
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
        if funding is not None:
            funding = np.asarray(funding, dtype=float)[sl]
    pfs = null_pfs(lo, sh, df, compiled.spec.exit, risk_cfg,
                   draws=draws, seed=seed, symbol=symbol, funding=funding)
    if len(pfs) < MIN_DRAWS:
        return {"draws": len(pfs), "bars": len(df), "percentile": None,
                "reason": f"only {len(pfs)} usable null draws"}
    arr = np.array(pfs, dtype=float)
    return {"draws": len(pfs), "bars": len(df),
            "null_median": round(float(np.median(arr)), 3),
            "null_p90": round(float(np.percentile(arr, 90)), 3),
            "percentile": edge_percentile(actual_pf, pfs)}


#: cuts the consistency test looks at, and how often a no-edge mechanism
#: clears each. Percentiles are uniform under the null, so the cut IS the
#: rate. Three looks, Bonferroni-corrected, because a single cut is a guess
#: about where the evidence sits.
_CUTS = (0.50, 0.75, 0.90)
#: fewer symbols than this and the test has no power worth reporting
MIN_SYMBOLS = 4


def _tail_p(k: int, n: int, q: float) -> float:
    """P(at least k of n symbols clear a cut a no-edge spec clears q of."""
    return sum(comb(n, i) * q ** i * (1 - q) ** (n - i) for i in range(k, n + 1))


def _clears(v: float, cut: float) -> bool:
    """STRICTLY above the cut.

    Percentiles come off a finite number of null draws, so they land on a
    grid and ties at a cut are common. `>=` counts a spec sitting exactly ON
    the no-edge median as beating it: ten symbols all at 0.50 — the
    definition of no edge — scored p=0.003. Strict is conservative in the
    direction an admission gate should be conservative in.
    """
    return v > cut


def consistency_p(percentiles) -> float | None:
    """How improbable this spread of per-symbol null percentiles is if the
    mechanism has no edge at all.

    A single symbol's percentile is noisy and a median throws away most of
    what the sample says. What separates a mechanism from a lucky symbol is
    the SHAPE of the distribution across independent markets: under no edge
    the percentiles are uniform, so counting how many clear each cut and
    reading the binomial tail is a test, not a fitted threshold.

    Measured false-positive rate on uniform inputs: 0.4% at 15 symbols,
    0.1% at 8 — the Bonferroni is conservative, and so is the strict
    comparison at each cut.

    Calibration, on real specs:
      Donchian Breakout Trail, 15 symbols          p = 3.5e-04
        its discovery half (8) / held-out half (7)     1.2e-02 / 3.9e-02
      momo_persist, discovery universe (8)         p = 1.1e-03
        the same rule on 9 symbols it never saw        p = 2.7e-01

    Returns None when too few symbols carry a percentile to say anything.
    """
    vals = [float(v) for v in percentiles if v is not None]
    n = len(vals)
    if n < MIN_SYMBOLS:
        return None
    best = min(_tail_p(sum(1 for v in vals if _clears(v, q)), n, 1.0 - q)
               for q in _CUTS)
    return min(1.0, best * len(_CUTS))
