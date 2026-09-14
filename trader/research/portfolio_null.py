"""Does the whole book of entries beat itself fired at one random offset?

`null_baseline.consistency_p` reads per-symbol rotation percentiles as
independent votes. A market-wide condition breaks that: "the alt index is
trending" is true or false for every alt at once, a per-symbol rotation moves
each symbol's entries out of that regime, and so every symbol beats its
rotation for ONE reason, which the binomial then counts nineteen times. On
2026-09-14 the search's top survivors (`r_alts_z96>p90`, all 19 discovery
symbols members of the alt index) read p=4.2e-15 that way.

Here every symbol's entries are shifted by the SAME number of bars, so the
cross-symbol clustering the condition creates survives into the null, and
the statistic is the one the book is judged on: compounded return over one
account at the live concurrency cap. One candidate, one exact empirical
p-value — `(1 + #null >= actual) / (draws + 1)`.

Resolution is the constraint that shapes this module. LORD++ holds its first
test to 0.0025 and its second, with no rejection, to about 0.0005, so a
useful null needs thousands of draws. Each bar's trade is therefore computed
ONCE (`vector_backtest.trade_table`, the same `_trade` the engine runs) and a
draw is only a walk over rotated entries. Extrapolating the tail from a few
hundred draws was tried and refused: a normal fit on log growth reads
P(p <= 1e-4) = 0.0036 under no edge when the null is heavy-tailed — 36x the
nominal rate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from ..strategy.portfolio_evidence import Fill, portfolio_curve
from ..strategy.vector_backtest import WARMUP, trade_table, walk_table
from .evaluate import _TF_SECONDS, _clock

log = logging.getLogger(__name__)

#: fewest draws a common rotation is ever run with
MIN_DRAWS = 199


@dataclass
class Leg:
    """One symbol's entries and the frame they were computed on."""
    symbol: str
    long: np.ndarray
    short: np.ndarray
    df: object
    funding: np.ndarray | None
    # bars before this index are warmup context only — no trade may open
    # there. 0 on the discovery slice; the cut's index on held-out B.
    first_bar: int = 0
    table: dict | None = field(default=None, repr=False)


def _mask(a, first_bar: int) -> np.ndarray:
    a = np.asarray(a, dtype=bool).copy()
    if first_bar > 0:
        a[:first_bar] = False
    return a


def _prepare(legs, exit_spec, risk: dict, tf: str, t0: int | None = None):
    """Build each leg's trade table and bar grid once — a draw re-uses them.
    The grid is relative to `t0` (default: the earliest leg), so it is
    rebuilt only when the set of legs or the origin changes. `tf` sets the
    grid's bar length, which need not be the legs' own timeframe."""
    key = (tf, t0, tuple(id(l) for l in legs))
    if all(getattr(l, "_prep_key", None) == key for l in legs):
        return
    step = _TF_SECONDS.get(tf, 900)
    clocks = {id(l): _clock(l.df) for l in legs}
    if t0 is None:
        t0 = min(int(c[0]) for c in clocks.values())
    for l in legs:
        if l.table is None:
            l.table = trade_table(l.df, exit_spec, risk, funding=l.funding,
                                  first_bar=l.first_bar)
        l._grid = ((clocks[id(l)] - t0) // step).astype(np.int64)
        l._long = np.asarray(l.long, dtype=bool)
        l._short = np.asarray(l.short, dtype=bool)
        l._prep_key = key


def fills_for(legs, exit_spec, risk: dict, tf: str, offset: int = 0,
              t0: int | None = None) -> tuple[list, int]:
    """Every leg's fills on one bar grid, entries rolled by `offset` bars."""
    _prepare(legs, exit_spec, risk, tf, t0)
    fills, trades = [], 0
    for l in legs:
        lo = _mask(np.roll(l._long, offset), l.first_bar)
        sh = _mask(np.roll(l._short, offset), l.first_bar)
        for i, e, r in walk_table(l.table, lo, sh, risk):
            fills.append(Fill(entry_i=int(l._grid[i]),
                              exit_i=int(l._grid[e]), r_multiple=r,
                              symbol=l.symbol))
            trades += 1
    return fills, trades


def draws_for(alpha: float, cap: int) -> int | None:
    """Draws that let a p-value reach `alpha` — p >= 1/(draws+1) — with
    room for the actual to sit below a handful of draws; None when the cap
    cannot reach it and the look must wait."""
    if alpha <= 0:
        return None
    need = int(np.ceil(1.0 / alpha)) - 1
    if need > cap:
        return None
    return int(min(cap, max(MIN_DRAWS, 4 * (need + 1) - 1)))


def common_rotation(legs, exit_spec, risk: dict, tf: str, equity: float,
                    risk_pct: float, max_open: int, draws: int = MIN_DRAWS,
                    seed: int = 0) -> dict:
    """{actual_total_pct, null_median, percentile, p, draws, trades}."""
    legs = [l for l in legs if l.df is not None and len(l.df) > WARMUP + 2]
    out = {"actual_total_pct": 0.0, "null_median": None, "percentile": None,
           "p": None, "draws": 0, "trades": 0}
    if not legs:
        return out
    fills, trades = fills_for(legs, exit_spec, risk, tf)
    out["trades"] = trades
    if not fills:
        return out
    actual = portfolio_curve(fills, equity, risk_pct, max_open)["total_pct"]
    out["actual_total_pct"] = round(float(actual), 3)

    # the offset must move every leg clear of its own warmup at both ends;
    # the shortest leg bounds it
    n = min(len(l.df) - l.first_bar for l in legs)
    lo_off, hi_off = WARMUP + 1, n - WARMUP - 1
    if hi_off <= lo_off:
        out["reason"] = f"slice too short to rotate ({n} bars)"
        return out
    rng = np.random.default_rng(seed)
    null = np.empty(int(draws))
    for k, off in enumerate(rng.integers(lo_off, hi_off, size=int(draws))):
        f, _ = fills_for(legs, exit_spec, risk, tf, offset=int(off))
        null[k] = portfolio_curve(f, equity, risk_pct, max_open)[
            "total_pct"] if f else 0.0
    beaten_by = int((null >= actual).sum())
    out.update(null_median=round(float(np.median(null)), 3),
               percentile=round(float((null < actual).mean()), 4),
               p=(1 + beaten_by) / (len(null) + 1), draws=len(null))
    return out


def legs_for(compiled, bundle) -> list:
    """Entries for every symbol in an `evaluate.Bundle`, computed once."""
    from ..strategy.vector_backtest import funding_for
    legs = []
    for sym, df in bundle.frames.items():
        sf = bundle.sym_frames.get(sym)
        try:
            lo, sh = compiled.entries(
                sf, btc=bundle.btc, derivs=bundle.derivs.get(sym),
                universe=bundle.universe, market=bundle.market, symbol=sym)
        except Exception as e:                          # noqa: BLE001
            log.debug(f"portfolio null entries {sym}: {e}")
            continue
        legs.append(Leg(sym, np.asarray(lo, bool), np.asarray(sh, bool), df,
                        funding_for(sym, df, bundle.risk),
                        int(bundle.first_bars.get(sym, 0))))
    return legs
