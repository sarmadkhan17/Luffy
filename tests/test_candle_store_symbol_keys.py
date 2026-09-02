"""One market, one key in the candle store.

ccxt names a linear perp both ways: 'XAU/USDT' and 'XAU/USDT:USDT'. The store
keyed rows by whatever string the caller happened to pass, so the same market
accumulated two separate histories:

    XAU/USDT         4h    400 bars   2026-06-27 -> 2026-09-02
    XAU/USDT:USDT    4h   1427 bars   2026-01-07 -> 2026-09-02

A backtest reading the plain form saw 400 bars where 1427 existed, which at
five folds and a 210-bar warmup left too few trades per window to score at
all — gold and silver silently contributed nothing to a validation that
claimed to include them.

`norm_symbol` already existed for exactly this; the store just never used it.
"""
import pandas as pd

from trader.data.feed import DataFeed


def _frame(n=50, start="2026-01-01"):
    c = [100.0 + i for i in range(n)]
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=n, freq="4h", tz="UTC"),
        "open": c, "high": [x * 1.01 for x in c], "low": [x * 0.99 for x in c],
        "close": c, "volume": [10.0] * n, "taker_buy": [5.0] * n})


def test_both_symbol_forms_write_to_the_same_rows(tmp_path):
    feed = DataFeed(db_path=tmp_path / "c.db")
    feed._store_save("XAU/USDT:USDT", "4h", _frame(50))
    got = feed._store_load("XAU/USDT", "4h", 500)
    assert got is not None and len(got) == 50, \
        "history written under the perp form must be readable by the plain one"


def test_the_reverse_direction_also_resolves(tmp_path):
    feed = DataFeed(db_path=tmp_path / "c.db")
    feed._store_save("XAU/USDT", "4h", _frame(30))
    got = feed._store_load("XAU/USDT:USDT", "4h", 500)
    assert got is not None and len(got) == 30


def test_the_two_forms_merge_rather_than_fragment(tmp_path):
    """The bug's real cost: 400 bars visible where 1427 existed."""
    feed = DataFeed(db_path=tmp_path / "c.db")
    feed._store_save("XAU/USDT:USDT", "4h", _frame(40, "2026-01-01"))
    feed._store_save("XAU/USDT", "4h", _frame(40, "2026-03-01"))
    got = feed._store_load("XAU/USDT", "4h", 500)
    assert len(got) == 80, "both writes must land in one continuous history"


def test_cached_ohlcv_reads_through_the_same_normalisation(tmp_path):
    feed = DataFeed(db_path=tmp_path / "c.db")
    feed._store_save("XAU/USDT:USDT", "4h", _frame(60))
    assert len(feed.cached_ohlcv("XAU/USDT", "4h", limit=500)) == 60


def test_distinct_markets_are_still_distinct(tmp_path):
    feed = DataFeed(db_path=tmp_path / "c.db")
    feed._store_save("BTC/USDT", "4h", _frame(20))
    feed._store_save("ETH/USDT", "4h", _frame(35))
    assert len(feed._store_load("BTC/USDT", "4h", 500)) == 20
    assert len(feed._store_load("ETH/USDT", "4h", 500)) == 35
