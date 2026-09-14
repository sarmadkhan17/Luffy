"""consistency_p corrected for cross-symbol dependence."""
import numpy as np
import pandas as pd
import pytest

from trader.research import portfolio_null as pn
from trader.research import referee as rf
from trader.strategy import null_baseline as nb
from trader.strategy.geometries import GEOS

RISK = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
        "slippage_atr_frac": 0.015, "bar_minutes": 240, "real_funding": False}


def test_the_continuous_tail_is_the_binomial_at_integers():
    for n in range(4, 20):
        for k in range(n + 1):
            for q in (0.5, 0.25, 0.1):
                assert nb.tail_p_real(k, n, q) == pytest.approx(
                    nb._tail_p(k, n, q), abs=1e-12)


def test_no_dependence_reproduces_consistency_p():
    rng = np.random.default_rng(0)
    for _ in range(50):
        p = rng.uniform(size=int(rng.integers(4, 20)))
        assert nb.consistency_p_dependent(p, 0.0) == pytest.approx(
            nb.consistency_p(p), rel=1e-9)


def test_dependence_only_ever_weakens_the_evidence():
    p = [0.95] * 13 + [0.3, 0.2]
    readings = [nb.consistency_p_dependent(p, r)
                for r in (0.0, 0.05, 0.2, 0.5, 1.0)]
    assert readings == sorted(readings)
    assert readings[0] < 1e-3
    # fully dependent: fifteen symbols are one market, and one market at the
    # 95th percentile is p ~ 3 x 0.10, not a discovery
    assert readings[-1] > 0.1


def test_effective_n_clips_rho():
    assert nb.effective_n(15, 0.0) == 15
    assert nb.effective_n(15, -0.3) == 15
    assert nb.effective_n(15, 1.0) == pytest.approx(1.0)
    assert nb.effective_n(15, 2.0) == pytest.approx(1.0)


def test_untestable_stays_none():
    assert nb.consistency_p_dependent([0.9, 0.9, 0.9], 0.0) is None
    assert nb.consistency_p_dependent([0.9] * 10, None) is None
    assert nb.consistency_p_dependent([0.9] * 10, float("nan")) is None


N = 1400


def _frame(close):
    ts = pd.date_range("2021-01-01", periods=len(close), freq="4h", tz="UTC")
    prev = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": ts, "open": prev,
                         "high": np.maximum(prev, close) * 1.004,
                         "low": np.minimum(prev, close) * 0.996,
                         "close": close, "volume": 1.0})


def _legs(seed, beta, k=8):
    """Identical entries on every symbol from a shared factor's past."""
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.01, N)
    closes = [100 * np.exp(np.cumsum(beta * m + rng.normal(0, 0.006, N)))
              for _ in range(k)]
    past = pd.Series(np.cumsum(m)).diff(48).to_numpy()
    fire = np.zeros(N, bool)
    fire[::24] = True
    lo, sh = fire & (past > 0.02), fire & (past < -0.02)
    return [pn.Leg(f"S{i}", lo, sh, _frame(c), None)
            for i, c in enumerate(closes)]


def test_the_dependence_estimate_sees_a_shared_market():
    shared = rf.consistency(_legs(1, 1.0), GEOS["trail"], RISK, "4h",
                            draws=40, dependence_draws=60)
    alone = rf.consistency(_legs(1, 0.0), GEOS["trail"], RISK, "4h",
                           draws=40, dependence_draws=60)
    assert shared["rho_bar"] > 0.5
    assert abs(alone["rho_bar"]) < 0.2
    assert shared["n_eff"] < 3
    assert shared["consistency_p_dep"] >= shared["consistency_p"]
