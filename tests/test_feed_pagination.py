"""DataFeed pagination + persistent candle store.

Exchanges cap one klines request at 1000 bars; deep gauntlet fetches
(2900×15m ≈ 30 days) must transparently page. The sqlite store
(data/candles.db) survives restarts so warm history is served without
re-downloading — only the missing tail is fetched incrementally.
"""

import time

import pandas as pd

from trader.data.feed import DataFeed


class FakeEx:
    """Returns 1000 bars per call, advancing with `since`."""

    def __init__(self, total: int = 4000, tf_ms: int = 900_000):
        self.total = total
        self.tf_ms = tf_ms
        self.calls: list[tuple] = []
        # candles live on an absolute UTC grid (like Binance): anchor to
        # the aligned boundary so independent instances agree on timestamps
        now_ms = int(time.time() * 1000)
        aligned = now_ms - (now_ms % tf_ms)
        self.first_ts = aligned - total * tf_ms

    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        self.calls.append((symbol, tf, since, limit))
        n = limit or 1000
        if since is None:
            start_idx = max(0, self.total - n)   # ccxt: newest `limit` bars
        else:
            start_idx = int((since - self.first_ts) // self.tf_ms)
        batch = []
        i = max(0, start_idx)
        while i < self.total and len(batch) < n:
            ts = self.first_ts + i * self.tf_ms
            batch.append([ts, 1, 2, 0.5, 1.5, 10])
            i += 1
        return batch


def _df_of(feed, n):
    return feed.fetch_ohlcv("BTC/USDT", "15m", limit=n, force=True)


def test_small_fetch_single_call(tmp_path):
    ex = FakeEx()
    feed = DataFeed(exchange=ex, db_path=tmp_path / "c.db")
    df = _df_of(feed, 400)
    assert len(df) == 400
    assert len(ex.calls) == 1


def test_deep_fetch_pages(tmp_path):
    ex = FakeEx(total=4000)
    feed = DataFeed(exchange=ex, db_path=tmp_path / "c.db")
    df = _df_of(feed, 2900)
    assert len(df) == 2900
    assert len(ex.calls) >= 3          # 1000-cap → multiple pages
    step = pd.Timedelta(minutes=15)    # resolution-independent check
    assert (df["ts"].diff().dropna() == step).all()   # contiguous bars


def test_deep_fetch_beyond_history(tmp_path):
    ex = FakeEx(total=1500)
    feed = DataFeed(exchange=ex, db_path=tmp_path / "c.db")
    df = _df_of(feed, 2900)
    assert len(df) == 1500             # all available bars, no crash


def test_store_serves_cold_instance(tmp_path):
    """Second process (empty memory cache) reads history from sqlite
    without a single exchange call while data is fresh."""
    ex1 = FakeEx()
    p = tmp_path / "c.db"
    wide = {"15m": 86_400}             # immune to candle-boundary crossings
    DataFeed(exchange=ex1, db_path=p,
             ttl_by_tf=dict(wide)).fetch_ohlcv("BTC/USDT", "15m",
                                               limit=400, force=True)
    ex2 = FakeEx()                     # fresh instance, same UTC grid
    feed2 = DataFeed(exchange=ex2, db_path=p, ttl_by_tf=dict(wide))
    df2 = feed2.fetch_ohlcv("BTC/USDT", "15m", limit=400)
    assert len(df2) == 400
    assert ex2.calls == []             # zero REST calls on the warm path


def test_incremental_tail_fetch(tmp_path):
    """When only the tail is stale, exactly one small request extends the
    stored series instead of re-downloading full history."""
    ex1 = FakeEx()
    p = tmp_path / "c.db"
    f1 = DataFeed(exchange=ex1, db_path=p, ttl_by_tf={"15m": 10_000})
    f1.fetch_ohlcv("BTC/USDT", "15m", limit=400, force=True)

    # simulate the feed having stopped 12 bars (~3h) ago
    import sqlite3
    con = sqlite3.connect(p)
    con.execute(
        "DELETE FROM candles WHERE symbol='BTC/USDT' AND tf='15m' AND ts IN "
        "(SELECT ts FROM candles WHERE symbol='BTC/USDT' AND tf='15m' "
        "ORDER BY ts DESC LIMIT 12)")
    con.commit()

    ex2 = FakeEx()
    f2 = DataFeed(exchange=ex2, db_path=p, ttl_by_tf={"15m": 10_000})
    df2 = f2.fetch_ohlcv("BTC/USDT", "15m", limit=400)
    assert len(ex2.calls) == 1                      # one window-filling call
    assert ex2.calls[0][2] is not None              # targeted `since`
    assert len(df2) == 400                          # restored, deduped
    step = pd.Timedelta(minutes=15)
    assert (df2["ts"].diff().dropna() == step).all()


def test_store_persists_across_deep_fetches(tmp_path):
    """A second cold instance needing MORE bars than stored pages only the
    missing window instead of failing."""
    p = tmp_path / "c.db"
    DataFeed(exchange=FakeEx(), db_path=p).fetch_ohlcv(
        "SOL/USDT", "15m", limit=400, force=True)
    ex2 = FakeEx(total=4000)
    f2 = DataFeed(exchange=ex2, db_path=p)
    df2 = f2.fetch_ohlcv("SOL/USDT", "15m", limit=2900)
    assert len(df2) == 2900
    step = pd.Timedelta(minutes=15)
    assert (df2["ts"].diff().dropna() == step).all()
