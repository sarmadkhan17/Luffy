"""QNT-02: mechanism-appropriate nulls and reproducible positive / no-edge controls."""
import numpy as np
import pandas as pd
import pytest

from trader.research import portfolio_null as pn
from trader.strategy import null_baseline as nb
from trader.strategy.geometries import GEOS
from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import simulate

RISK = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04, "slippage_atr_frac": 0.015,
        "bar_minutes": 240, "real_funding": False}
N = 1400


def _frame(close, freq="4h"):
    ts = pd.date_range("2021-01-01", periods=len(close), freq=freq, tz="UTC")
    prev = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": ts, "open": prev, "high": np.maximum(prev, close) * 1.004,
                         "low": np.minimum(prev, close) * 0.996, "close": close, "volume": 1.0})


def _walk(seed, n=N):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(0, 0.008, n)))


def _symbol_percentile(close, lo, draws=60, seed=0):
    df = _frame(close)
    sh = np.zeros(len(close), bool)
    pf = simulate(lo, sh, df, GEOS["trail"], RISK).profit_factor
    null = nb.null_pfs(lo, sh, df, GEOS["trail"], RISK, draws=draws, seed=seed)
    return nb.edge_percentile(pf, null)


def _no_edge_entries(seed):
    rng = np.random.default_rng(seed + 1000)
    fire = np.zeros(N, bool)
    fire[300::20] = rng.random(len(fire[300::20])) < 0.6
    return fire


def _planted_entries(close):
    fwd = np.r_[close[30:] / close[:-30] - 1, np.zeros(30)]
    fire = np.zeros(N, bool)
    fire[::12] = True
    return fire & (fwd > 0.03)


def test_per_symbol_no_edge_control_does_not_fabricate_significance():
    pcts = [_symbol_percentile(_walk(s), _no_edge_entries(s)) for s in range(30)]
    pcts = [p for p in pcts if p is not None]
    assert len(pcts) >= 25
    # under no edge a percentile is ~uniform: a >=0.95 reading is ~5%; 6+/30 is P<1%
    assert sum(p >= 0.95 for p in pcts) <= 5
    assert 0.25 < np.mean(pcts) < 0.7


def test_per_symbol_positive_control_detects_planted_timing():
    pcts = [_symbol_percentile(_walk(s), _planted_entries(_walk(s))) for s in range(6)]
    assert sum((p or 0) >= 0.95 for p in pcts) >= 5


def test_rotation_null_is_reproducible_and_seed_sensitive():
    c = _walk(3)
    lo = _no_edge_entries(3)
    a = _symbol_percentile(c, lo, seed=5)
    assert a == _symbol_percentile(c, lo, seed=5)
    df = _frame(c)
    sh = np.zeros(N, bool)
    x = nb.null_pfs(lo, sh, df, GEOS["trail"], RISK, draws=30, seed=1)
    y = nb.null_pfs(lo, sh, df, GEOS["trail"], RISK, draws=30, seed=2)
    assert x != y


def test_rotation_preserves_count_runs_and_spacing_but_breaks_alignment():
    lo = _no_edge_entries(1)
    r = nb.rotate_entries(lo, 417)
    assert r.sum() == lo.sum()
    gaps = lambda a: sorted(np.diff(np.flatnonzero(a)))
    # circular spacing multiset is invariant up to the one wrap-around gap
    assert sorted(gaps(r))[len(gaps(r)) // 2] == sorted(gaps(lo))[len(gaps(lo)) // 2]
    assert not np.array_equal(r, lo)


def _shared_market_legs(seed, k=6):
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.01, N)
    closes = [100 * np.exp(np.cumsum(m + rng.normal(0, 0.006, N))) for _ in range(k)]
    past = pd.Series(np.cumsum(m)).diff(48).to_numpy()
    fire = np.zeros(N, bool)
    fire[::24] = True
    lo, sh = fire & (past > 0.02), fire & (past < -0.02)
    return [pn.Leg(f"S{i}", lo, sh, _frame(c), None) for i, c in enumerate(closes)]


def _book(legs, draws=99, seed=1):
    return pn.common_rotation(legs, GEOS["trail"], RISK, "4h", 2000.0, 0.5, 8, draws=draws, seed=seed)


def test_common_rotation_p_is_reproducible_exact_and_on_the_finite_grid():
    legs = _shared_market_legs(2)
    a, b = _book(legs), _book(_shared_market_legs(2))
    assert a == b
    p, d = a["p"], a["draws"]
    assert d == 99 and 1 / (d + 1) <= p <= 1.0
    assert (p * (d + 1)) == pytest.approx(round(p * (d + 1)))      # (1+k)/(draws+1)
    assert _book(legs, seed=2)["p"] != pytest.approx(-1)            # different seed still valid


def test_one_offset_moves_every_symbol_so_cross_symbol_alignment_survives():
    legs = _shared_market_legs(4)
    pn._prepare(legs, GEOS["trail"], RISK, "4h")
    off = 333
    rolled = [np.roll(l._long, off) for l in legs]
    base = [l._long for l in legs]
    # identical entries on every leg stay identical (the shared-regime structure) after rotation
    assert all(np.array_equal(rolled[0], r) for r in rolled)
    assert all(np.array_equal(base[0], b) for b in base)


def test_independent_percentile_votes_overstate_a_shared_market_but_common_rotation_does_not():
    """The mechanism is market-wide, so the matching control is the common rotation."""
    rej_common = rej_indep = 0
    S = 10
    for s in range(S):
        legs = _shared_market_legs(s)
        rej_common += (_book(legs, draws=39)["p"] or 1.0) <= 0.05
        pcts = []
        for l in legs:
            df = l.df
            pf = simulate(l.long, l.short, df, GEOS["trail"], RISK).profit_factor
            pcts.append(nb.edge_percentile(pf, nb.null_pfs(l.long, l.short, df, GEOS["trail"], RISK,
                                                          draws=40, seed=s)))
        p = nb.consistency_p(pcts)
        rej_indep += (p is not None and p <= 0.05)
    assert rej_common <= 2
    assert rej_indep >= rej_common     # independence is never the stricter control on shared structure


def test_dependence_deflation_is_monotone_in_rho():
    pcts = [0.97, 0.96, 0.99, 0.95, 0.98, 0.97, 0.96, 0.99]
    ps = [nb.consistency_p_dependent(pcts, r) for r in (0.0, 0.3, 0.6, 0.9)]
    assert ps == sorted(ps) and ps[0] == pytest.approx(nb.consistency_p(pcts))


def test_consistency_p_no_edge_false_positive_rate_and_ties_are_not_significant():
    rng = np.random.default_rng(0)
    fp = np.mean([nb.consistency_p(rng.uniform(size=10)) <= 0.05 for _ in range(2000)])
    assert fp <= 0.05                                              # valid at its nominal level
    assert nb.consistency_p([0.5] * 10) > 0.5                      # sitting ON the median is no edge


def test_empty_or_short_null_is_untested_not_a_score():
    c = _walk(0, 500)
    lo = np.zeros(500, bool); lo[300::20] = True
    assert nb.null_pfs(lo, np.zeros(500, bool), _frame(c), GEOS["trail"], RISK, draws=500) == []
    assert nb.edge_percentile(1.5, []) is None
    assert nb.consistency_p([0.99, 0.99, 0.99]) is None
