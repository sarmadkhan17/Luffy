"""DataFeed pagination: exchanges cap one klines request at 1000 bars;
deep gauntlet fetches (2900×15m ≈ 30 days) must transparently page."""

import time

import pandas as pd

from trader.data.feed import DataFeed


class FakeEx:
    """Returns 1000 bars per call, advancing with `since`."""

    def __init__(self, total: int = 4000, tf_ms: int = 900_000):
        self.total = total
        self.tf_ms = tf_ms
        self.calls: list[tuple] = []
        now_ms = int(time.time() * 1000)     # anchor to the real clock
        self.first_ts = now_ms - total * tf_ms

    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        self.calls.append((symbol, tf, since, limit))
        start_idx = 0
        if since is not None:
            start_idx = int((since - self.first_ts) // self.tf_ms)
        batch = []
        i = max(0, start_idx)
        while i < self.total and len(batch) < (limit or 1000):
            ts = self.first_ts + i * self.tf_ms
            batch.append([ts, 1, 2, 0.5, 1.5, 10])
            i += 1
        return batch


def _df_of(feed, n):
    return feed.fetch_ohlcv("BTC/USDT", "15m", limit=n, force=True)


def test_small_fetch_single_call():
    ex = FakeEx()
    feed = DataFeed(exchange=ex)
    df = _df_of(feed, 400)
    assert len(df) == 400
    assert len(ex.calls) == 1


def test_deep_fetch_pages():
    ex = FakeEx(total=4000)
    feed = DataFeed(exchange=ex)
    df = _df_of(feed, 2900)
    assert len(df) == 2900
    assert len(ex.calls) >= 3          # 1000-cap → multiple pages
    step = pd.Timedelta(minutes=15)    # resolution-independent check
    assert (df["ts"].diff().dropna() == step).all()   # contiguous bars


def test_deep_fetch_beyond_history():
    ex = FakeEx(total=1500)
    feed = DataFeed(exchange=ex)
    df = _df_of(feed, 2900)
    assert len(df) == 1500             # all available bars, no crash
