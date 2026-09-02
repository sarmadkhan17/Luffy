"""Funding is signed and per-symbol, or it is the conservative flat charge.

The engine charged abs(flat rate) to both sides, billing a short for carry it
would actually receive. Measured on BTC 4h over 11,000 bars, the flat model
charges 10.95%/yr to both sides where the venue's own mean settlement is
6.96%/yr paid by longs — over-charging a long by half and mis-signing a short
entirely. On a spec that can hold 83 days that is not a rounding error.

The flat charge stays wherever the real series is unknown: a bar with no
settlement behind it gets NaN, and NaN falls back to the pessimistic number
rather than to a fabricated zero.
"""
import numpy as np
import pandas as pd
import pytest

from trader.strategy import spec_evidence as se
from trader.strategy.vector_backtest import funding_for


def _bars(ts):
    return pd.DataFrame({"ts": pd.to_datetime(ts, utc=True),
                         "open": 1.0, "high": 1.0, "low": 1.0,
                         "close": 1.0, "volume": 1.0})


class _Feed:
    def __init__(self, rows):
        self._rows = rows

    def load(self, symbol, series):
        if series != "funding" or not self._rows:
            return None
        ts, v = zip(*self._rows)
        return pd.DataFrame({"ts": pd.to_datetime(list(ts), utc=True),
                             "value": list(v)})


# ── point-in-time alignment ────────────────────────────────────────────
def test_a_bar_carries_the_last_settlement_that_had_already_happened():
    bars = _bars(["2026-01-01T08:00", "2026-01-01T12:00", "2026-01-01T16:00"])
    feed = _Feed([("2026-01-01T08:00", 0.0001), ("2026-01-01T16:00", 0.0003)])
    got = se.funding_series("X/USDT", bars, feed=feed)
    # a settlement AT the bar's own timestamp has happened by its close
    assert got == pytest.approx([0.0001, 0.0001, 0.0003])


def test_a_bar_before_the_first_settlement_is_nan_not_zero():
    bars = _bars(["2026-01-01T00:00", "2026-01-01T08:00"])
    feed = _Feed([("2026-01-01T08:00", 0.0002)])
    got = se.funding_series("X/USDT", bars, feed=feed)
    assert np.isnan(got[0]) and got[1] == pytest.approx(0.0002)


def test_no_stored_series_returns_none_and_keeps_the_flat_path():
    assert se.funding_series("X/USDT", _bars(["2026-01-01T00:00"]),
                             feed=_Feed([])) is None


def test_coverage_reports_the_fraction_actually_known():
    assert se.funding_coverage(np.array([np.nan, 1.0, 2.0, np.nan])) == 0.5
    assert se.funding_coverage(None) == 0.0


# ── the switch ─────────────────────────────────────────────────────────
def test_the_synthetic_equivalence_symbol_never_gets_a_series():
    """backtest_equivalence must keep comparing like with like."""
    assert funding_for("BT", _bars(["2026-01-01T00:00"]), {}) is None


def test_it_can_be_turned_off():
    assert funding_for("BTC/USDT", _bars(["2026-01-01T00:00"]),
                       {"real_funding": False}) is None


# ── the arithmetic ─────────────────────────────────────────────────────
def _run(side_long, funding, n=40):
    """One trade held to the time stop on a flat price, so P&L is pure cost."""
    from trader.strategy.spec import ExitSpec
    from trader.strategy.vector_backtest import WARMUP, simulate
    n = WARMUP + n
    df = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=n, freq="4h",
                                           tz="UTC"),
                       "open": 100.0, "high": 100.05, "low": 99.95,
                       "close": 100.0, "volume": 1.0})
    sig = np.zeros(n, dtype=bool)
    sig[WARMUP + 1] = True
    other = np.zeros(n, dtype=bool)
    ex = ExitSpec(stop={"kind": "pct", "v": 0.05}, target={"kind": "none"},
                  trail={"kind": "none"}, time={"max_bars": 6})
    risk = {"taker_fee_pct": 0.0, "slippage_atr_frac": 0.0,
            "risk_per_trade_pct": 1.0, "funding_rate_8h": 0.0001,
            "bar_minutes": 240}
    lo, sh = (sig, other) if side_long else (other, sig)
    return simulate(lo, sh, df, ex, risk, symbol="T",
                    funding=None if funding is None else np.full(n, funding))


def test_without_a_series_both_sides_pay_the_same_flat_charge():
    assert _run(True, None).pnl_usdt == pytest.approx(_run(False, None).pnl_usdt)
    assert _run(True, None).pnl_usdt < 0


def test_with_a_positive_rate_the_long_pays_and_the_short_is_paid():
    long_pnl = _run(True, 0.0001).pnl_usdt
    short_pnl = _run(False, 0.0001).pnl_usdt
    assert long_pnl < 0 < short_pnl
    assert long_pnl == pytest.approx(-short_pnl, rel=1e-6)


def test_a_negative_rate_reverses_who_pays():
    assert _run(True, -0.0001).pnl_usdt > 0
    assert _run(False, -0.0001).pnl_usdt < 0


def test_an_unknown_bar_falls_back_to_the_conservative_flat_charge():
    """NaN is not zero. A short must not be handed free carry for a bar the
    venue never told us about."""
    assert _run(False, float("nan")).pnl_usdt == \
        pytest.approx(_run(False, None).pnl_usdt)


def test_a_misaligned_series_is_ignored_rather_than_trusted():
    from trader.strategy.spec import ExitSpec
    from trader.strategy.vector_backtest import WARMUP, simulate
    n = WARMUP + 40
    df = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=n, freq="4h",
                                           tz="UTC"),
                       "open": 100.0, "high": 100.05, "low": 99.95,
                       "close": 100.0, "volume": 1.0})
    sig = np.zeros(n, dtype=bool); sig[WARMUP + 1] = True
    ex = ExitSpec(stop={"kind": "pct", "v": 0.05}, target={"kind": "none"},
                  trail={"kind": "none"}, time={"max_bars": 6})
    risk = {"taker_fee_pct": 0.0, "slippage_atr_frac": 0.0,
            "risk_per_trade_pct": 1.0, "funding_rate_8h": 0.0001,
            "bar_minutes": 240}
    short = simulate(sig, np.zeros(n, dtype=bool), df, ex, risk, symbol="T",
                     funding=np.full(n // 2, -0.01))
    assert short.pnl_usdt == pytest.approx(_run(True, None).pnl_usdt)
