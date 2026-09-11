"""ref(key, expr): any indicator, on any reference market, point-in-time.

A base bar is decided at its own close, so it may read a reference value
only if that value was KNOWN by then — the reference bar's stamp plus its
declared close_after. Yahoo stamps an S&P daily bar at the 13:30 open; the
close is not known until later, so reading it at the stamp would peek.
"""
import numpy as np
import pandas as pd
import pytest

from trader.data.references import DAY, HOUR
from trader.strategy import dsl
from trader.strategy.features import FeatureCtx

T0 = 1_788_000_000_000 - (1_788_000_000_000 % DAY)   # a UTC midnight


def _bars(start_ms, n, step_ms, close0=100.0):
    c = [close0 + i for i in range(n)]
    return pd.DataFrame({
        "ts": pd.to_datetime([start_ms + i * step_ms for i in range(n)],
                             unit="ms", utc=True),
        "open": c, "high": c, "low": c, "close": c, "volume": [1.0] * n})


def _eval(expr, base, market):
    ctx = FeatureCtx(frames={"4h": base}, tf="4h", market=market)
    return dsl.evaluate(dsl.parse(expr), ctx)


def test_a_same_frame_reference_is_read_bar_for_bar():
    base = _bars(T0, 6, 4 * HOUR)
    ref = _bars(T0, 6, 4 * HOUR, close0=1000.0)
    out = _eval('ref("btcdom", close)', base, {"btcdom": ref})
    assert out.tolist() == [1000.0, 1001.0, 1002.0, 1003.0, 1004.0, 1005.0]


def test_a_daily_close_is_invisible_until_it_is_known():
    """S&P bar stamped D0 13:30 is known at D1 13:30 — not before."""
    spx = _bars(T0 + 13 * HOUR + 30 * 60_000, 2, DAY, close0=5000.0)
    base = _bars(T0 + DAY, 6, 4 * HOUR)          # D1 00:00 .. D1 20:00
    out = _eval('ref("spx", close)', base, {"spx": spx})
    # base bars close at D1 04,08,12,16,20,24h
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[2])  # 12:00 < 13:30
    assert out.iloc[3] == 5000.0                  # 16:00: D0's close known
    assert 5001.0 not in out.tolist()             # D1's close not yet known


def test_a_dead_feed_reads_nan():
    base = _bars(T0 + 10 * DAY, 3, 4 * HOUR)
    ref = _bars(T0, 3, 4 * HOUR)                  # ten days silent
    out = _eval('ref("btcdom", close)', base, {"btcdom": ref})
    assert out.isna().all()


def test_no_reference_frame_reads_nan():
    base = _bars(T0, 3, 4 * HOUR)
    assert _eval('ref("btcdom", close)', base, None).isna().all()


def test_indicators_run_on_the_reference_not_the_base():
    base = _bars(T0, 30, 4 * HOUR, close0=1.0)
    ref = _bars(T0, 30, 4 * HOUR, close0=1000.0)
    out = _eval('ref("btcdom", ema(5))', base, {"btcdom": ref})
    assert out.iloc[-1] > 1000.0


def test_a_close_only_reference_has_no_high():
    base = _bars(T0 + DAY, 3, 4 * HOUR)
    st = _bars(T0, 1, DAY)
    st[["open", "high", "low"]] = np.nan
    out = _eval('ref("stables", high)', base, {"stables": st})
    assert out.isna().all()


def test_the_requirement_is_derived():
    tree = dsl.parse('ref("spx", close > ema(50)) and ref("dxy", ret(5)) < 0')
    assert set(dsl.data_requires(tree)) >= {"ref:spx", "ref:dxy"}


@pytest.mark.parametrize("expr", [
    'ref("nope", close)',
    'ref("spx", ref("dxy", close))',
    'ref("spx", funding)',
    'ref("spx", btc_ret(4))',
])
def test_what_cannot_mean_anything_on_a_reference_is_refused(expr):
    with pytest.raises(dsl.SpecError):
        dsl.parse(expr)


def test_a_ref_spec_is_not_translatable_to_pine():
    from trader.strategy.pine_spec import PineUnsupported, _emit
    with pytest.raises(PineUnsupported):
        _emit(dsl.parse('ref("spx", close)').body, set())
