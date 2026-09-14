"""The common-rotation null: one offset for every symbol, one p per book."""
import numpy as np
import pandas as pd
import pytest

from trader.research import portfolio_null as pn
from trader.strategy.geometries import GEOS

RISK = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
        "slippage_atr_frac": 0.015, "bar_minutes": 240, "real_funding": False}
N = 1400


def _frame(close):
    ts = pd.date_range("2021-01-01", periods=len(close), freq="4h", tz="UTC")
    prev = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": ts, "open": prev,
                         "high": np.maximum(prev, close) * 1.004,
                         "low": np.minimum(prev, close) * 0.996,
                         "close": close, "volume": 1.0})


def _market(seed, k=6):
    """k symbols that share one random-walk market factor, no persistence."""
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.01, N)
    closes = [100 * np.exp(np.cumsum(m + rng.normal(0, 0.006, N)))
              for _ in range(k)]
    return m, closes


def _legs_momentum(seed):
    """Long when the SHARED factor's 48-bar return is up, short when down —
    the same entries on every symbol at once, and no edge: the factor is a
    random walk, so its past says nothing about its future."""
    m, closes = _market(seed)
    past = pd.Series(np.cumsum(m)).diff(48).to_numpy()
    fire = np.zeros(N, bool)
    fire[::24] = True                      # re-arm every 4 days
    lo = fire & (past > 0.02)
    sh = fire & (past < -0.02)
    return [pn.Leg(f"S{i}", lo, sh, _frame(c), None)
            for i, c in enumerate(closes)]


def _legs_planted(seed):
    """Entries that know the next 30 bars — a real, per-bar timing edge."""
    _m, closes = _market(seed)
    legs = []
    for i, c in enumerate(closes):
        fwd = np.r_[c[30:] / c[:-30] - 1, np.zeros(30)]
        fire = np.zeros(N, bool)
        fire[::12] = True
        legs.append(pn.Leg(f"S{i}", fire & (fwd > 0.03), fire & (fwd < -0.03),
                           _frame(c), None))
    return legs


def _run(legs, draws=39, seed=1):
    return pn.common_rotation(legs, GEOS["trail"], RISK, "4h", 2000.0, 0.5,
                              8, draws=draws, seed=seed)


def test_a_shared_regime_with_no_edge_is_not_a_discovery():
    rejected = sum(1 for s in range(12)
                   if (_run(_legs_momentum(s))["p"] or 1.0) <= 0.05)
    # 12 draws of a valid p at 0.05: expect ~0.6; 3 or more is P < 2%
    assert rejected <= 2


def test_a_planted_timing_edge_is_detected():
    hits = sum(1 for s in range(4) if (_run(_legs_planted(s))["p"] or 1.0)
               <= 0.05)
    assert hits >= 3


def test_the_table_walk_reproduces_the_engine_exactly():
    """The rotation null re-uses precomputed trades; if the walk and
    `simulate` ever disagree, every null draw measures a different engine."""
    from trader.strategy.vector_backtest import simulate
    rng = np.random.default_rng(5)
    for leg in _legs_planted(2)[:3] + _legs_momentum(3)[:3]:
        fund = rng.normal(0.0001, 0.0003, N)
        fund[rng.uniform(size=N) < 0.2] = np.nan
        leg.funding = fund
        for geo in ("trail", "fixed"):
            mine = []
            simulate(leg.long, leg.short, leg.df, GEOS[geo], RISK,
                     symbol=leg.symbol, funding=fund, fills_out=mine)
            from trader.strategy.vector_backtest import trade_table, walk_table
            got = walk_table(trade_table(leg.df, GEOS[geo], RISK, fund),
                             leg.long, leg.short, RISK)
            assert [(f.entry_i, f.exit_i) for f in mine] == \
                [(i, e) for i, e, _ in got]
            assert [f.r_multiple for f in mine] == \
                pytest.approx([r for _, _, r in got], rel=1e-9, abs=1e-12)


def test_draws_follow_the_level_and_defer_past_the_cap():
    assert pn.draws_for(0.05, 20000) == 199            # floor
    assert pn.draws_for(0.0025, 20000) == 1599         # 4 x 400 - 1
    assert pn.draws_for(0.0001, 20000) == 20000        # capped, still reachable
    assert pn.draws_for(0.00001, 20000) is None        # needs 99999: wait
    assert pn.draws_for(0.0, 20000) is None


def test_no_trade_opens_inside_a_legs_context_bars():
    legs = _legs_planted(0)
    cut = 700
    for l in legs:
        l.first_bar = cut
    fills, _ = pn.fills_for(legs, GEOS["trail"], RISK, "4h")
    assert fills and min(f.entry_i for f in fills) >= cut
