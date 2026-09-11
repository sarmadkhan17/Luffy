"""Reference markets live in their own table, closed bars only.

Not the `candles` table: norm_symbol() splits on ':' so `ref:spx` would
collapse to `ref`, and repair_partial_bars.py walks every candle symbol and
would try to audit the S&P against Binance.
"""
import sqlite3

import numpy as np
import pandas as pd

from trader.data.references import DAY, HOUR, REFS, RefStore


def _frame(start_ms, n, step_ms, close0=100.0, ohlc=True):
    ts = pd.to_datetime([start_ms + i * step_ms for i in range(n)],
                        unit="ms", utc=True)
    c = [close0 + i for i in range(n)]
    d = {"ts": ts, "close": c}
    if ohlc:
        d.update(open=c, high=[x + 1 for x in c], low=[x - 1 for x in c],
                 volume=[10.0] * n)
    return pd.DataFrame(d)


def test_the_registry_names_every_source():
    for k in ("spx", "spx_1h", "dxy", "dxy_1h", "gold", "us10y", "vix",
              "oil", "btcdom", "alts", "stables", "cg_btc_d", "cg_usdt_d",
              "cg_total", "cg_total2"):
        assert k in REFS
    assert REFS["spx"].close_after_ms == DAY
    assert REFS["spx_1h"].close_after_ms == HOUR
    assert REFS["stables"].close_only and REFS["alts"].close_only


def test_a_forming_bar_is_not_stored(tmp_path):
    s = RefStore(tmp_path / "c.db")
    now = 1_789_000_000_000 - (1_789_000_000_000 % (4 * HOUR))
    df = _frame(now - 3 * 4 * HOUR, 4, 4 * HOUR)   # last bar opens at `now`
    assert s.save("btcdom", df, now_ms=now + 1) == 3
    assert len(s.load("btcdom")) == 3
    assert s.last_ts("btcdom") == now - 4 * HOUR


def test_references_do_not_touch_the_candles_table(tmp_path):
    s = RefStore(tmp_path / "c.db")
    s.save("btcdom", _frame(0, 2, 4 * HOUR), now_ms=10 ** 13)
    con = sqlite3.connect(tmp_path / "c.db")
    tabs = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    assert "refs" in tabs and "candles" not in tabs


def test_a_close_only_series_reads_nan_high_and_low(tmp_path):
    s = RefStore(tmp_path / "c.db")
    s.save("stables", _frame(0, 3, DAY, ohlc=False), now_ms=10 ** 13)
    df = s.load("stables")
    assert df["high"].isna().all() and df["low"].isna().all()
    assert df["close"].tolist() == [100.0, 101.0, 102.0]


def test_an_unmeasured_close_is_not_stored(tmp_path):
    s = RefStore(tmp_path / "c.db")
    df = _frame(0, 3, DAY)
    df.loc[1, "close"] = np.nan
    assert s.save("spx", df, now_ms=10 ** 13) == 2


def test_an_empty_store_reads_none(tmp_path):
    s = RefStore(tmp_path / "c.db")
    assert s.load("spx") is None and s.last_ts("spx") is None
