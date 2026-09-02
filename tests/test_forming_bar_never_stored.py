"""The candle store holds CLOSED bars. A forming bar is not a bar.

`fetch_ohlcv` extended the store with `since = last + tf_ms`, one bar past
the newest row it held. But `_merge_save` had already written that newest
row while the bar was still forming, so the very bar that needed correcting
was the one bar every subsequent request skipped. Written once at partial
values, never revisited, frozen for good.

Measured live on 2026-09-02: 104 of 123 (symbol, timeframe) tails in
data/candles.db disagreed with the venue, up to four consecutive 4h bars
deep. Each frozen bar carried 4-16% of the venue's volume for that bar with
the open exact and high/low/close truncated to whatever had traded by the
snapshot — the signature of a partial candle.

    UNI/USDT 4h      stored o/h/l/c                venue o/h/l/c
    2026-09-02 08:00 6.298/6.307/6.26/6.304        6.298/6.373/5.692/5.742

That fabricated close read as a Donchian breakout (100-bar high 6.222) on
the one strategy in the book. The real bar closed at 5.742 and broke
nothing. The rolling high was wrong too, being a max over truncated highs.

This is the same rule the DSL already lives by — missing information is NaN,
never a fabricated default — applied to the store that feeds it.
"""
import time

import pandas as pd
import pytest

from trader.data.feed import DataFeed

TF_MS = 14_400_000          # 4h


def _now_ms() -> int:
    return int(time.time() * 1000)


def _last_closed(now_ms: int) -> int:
    """Open time of the newest 4h bar that has already closed."""
    return now_ms - now_ms % TF_MS - TF_MS


def _seed(feed, symbol: str, tf: str, rows: list) -> None:
    """Put rows on disk exactly as the old code left them.

    Deliberately not via `_store_save`: these tests must describe the state
    the live store was actually in, and must fail on the assertion rather
    than on a missing keyword argument.
    """
    feed.db.executemany(
        "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
        [(symbol, tf, int(r[0]), float(r[1]), float(r[2]), float(r[3]),
          float(r[4]), float(r[5]), float(r[9])) for r in rows])
    feed.db.commit()


def _rows(start_ms: int, n: int, *, close=100.0, volume=1000.0):
    """Raw 12-wide binance kline rows, the shape `_klines` returns."""
    out = []
    for i in range(n):
        ts = start_ms + i * TF_MS
        out.append([ts, close, close * 1.02, close * 0.98, close,
                    volume, ts + TF_MS - 1, 0, 0, volume * 0.5, 0, 0])
    return out


class FakeFeed(DataFeed):
    """A DataFeed whose only exchange contact is a scripted kline source."""

    def __init__(self, db_path, now_ms):
        super().__init__(exchange=object(), db_path=db_path)
        self.data_ex = None
        self.now_ms = now_ms
        self.calls: list[int | None] = []
        self.script: list = []

    def _klines(self, symbol, tf, since=None, limit=1000):
        self.calls.append(since)
        rows = self.script
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return rows[:limit]


def test_store_save_drops_a_bar_that_has_not_closed(tmp_path):
    """The forming bar must not reach disk at all."""
    now = _now_ms()
    closed_ts = _last_closed(now)                # fully closed
    forming_ts = now - now % TF_MS               # still open right now
    df = pd.DataFrame({
        "ts": pd.to_datetime([closed_ts, forming_ts], unit="ms", utc=True),
        "open": [10.0, 11.0], "high": [12.0, 11.1], "low": [9.0, 11.0],
        "close": [11.0, 11.05], "volume": [500.0, 20.0],
        "taker_buy": [250.0, 10.0]})

    feed = DataFeed(db_path=tmp_path / "c.db")
    feed._store_save("BTC/USDT", "4h", df, now_ms=now)

    got = feed._store_load("BTC/USDT", "4h", 100)
    assert got is not None, "the closed bar must still be stored"
    stored_ms = [int(t.timestamp() * 1000) for t in got["ts"]]
    assert stored_ms == [closed_ts], (
        "only the closed bar belongs on disk; the forming bar is a snapshot "
        f"of an incomplete candle, got {stored_ms}")


def test_a_frozen_partial_bar_is_corrected_by_a_later_fetch(tmp_path):
    """The live failure, end to end: a truncated bar must not survive.

    Fetch once while the bar is forming, then again after it closes. The
    store must end up holding the venue's real high/low/close, not the
    partial values seen the first time.
    """
    now = _now_ms()
    bar_ts = _last_closed(now)
    feed = FakeFeed(tmp_path / "c.db", now_ms=now)

    # on disk: this bar, frozen at the values it showed an hour in. The bar
    # has since closed, so the freeze is now permanent under the old `since`.
    partial = [[bar_ts, 6.298, 6.307, 6.260, 6.304, 1000.0,
                bar_ts + TF_MS - 1, 0, 0, 500.0, 0, 0]]
    _seed(feed, "UNI/USDT", "4h", partial)

    # the venue reports the whole range for that same closed bar
    feed.script = [[bar_ts, 6.298, 6.373, 5.692, 5.742, 24840243.0,
                    bar_ts + TF_MS - 1, 0, 0, 12000000.0, 0, 0]]
    feed._cache.clear()                       # force a real refresh
    feed.fetch_ohlcv("UNI/USDT", "4h", limit=1, min_bars=1)

    got = feed._store_load("UNI/USDT", "4h", 10)
    assert got is not None and len(got) == 1
    row = got.iloc[-1]
    assert row["close"] == pytest.approx(5.742), (
        f"stored close {row['close']} is the partial snapshot; the bar "
        "closed at 5.742")
    assert row["low"] == pytest.approx(5.692), (
        f"stored low {row['low']} truncates the real low of 5.692 — a "
        "Donchian channel built on this is wrong in both directions")


def test_incremental_fetch_re_reads_the_stored_tail(tmp_path):
    """`since` must cover the newest stored bar, not start past it.

    Starting at `last + tf_ms` is what made the freeze permanent: the one
    bar that could be wrong was the one bar never requested again.
    """
    now = _now_ms()
    bar_ts = _last_closed(now)
    feed = FakeFeed(tmp_path / "c.db", now_ms=now)
    _seed(feed, "BTC/USDT", "4h", _rows(bar_ts - 9 * TF_MS, 10))

    stored = feed._store_load("BTC/USDT", "4h", 100)
    last_ms = int(stored["ts"].iloc[-1].timestamp() * 1000)

    feed.script = _rows(bar_ts - 9 * TF_MS, 10)
    feed.calls.clear()
    feed._cache.clear()
    feed.fetch_ohlcv("BTC/USDT", "4h", limit=10, min_bars=1)

    assert feed.calls, "an incremental refresh must actually hit the venue"
    since = feed.calls[-1]
    assert since is not None and since <= last_ms, (
        f"since={since} starts after the newest stored bar {last_ms}, so "
        "that bar can never be corrected once written")


def test_a_tail_several_bars_deep_heals_itself(tmp_path):
    """Live corruption ran four bars deep, so a one-bar overlap is not enough.

    Every fetch that landed while a bar was forming froze that bar, so the
    damage accumulated one bar per fetch rather than staying at the tail.
    Re-reading only the newest stored bar would take four cycles to repair
    what a small overlap repairs in one.
    """
    now = _now_ms()
    first = _last_closed(now) - 9 * TF_MS
    feed = FakeFeed(tmp_path / "c.db", now_ms=now)

    # ten bars on disk, the last four frozen at a fraction of their range
    truth = _rows(first, 10)
    partial = [list(r) for r in truth]
    for r in partial[-4:]:
        r[2], r[3], r[4], r[5] = r[1] * 1.001, r[1] * 0.999, r[1], r[5] * 0.05
    _seed(feed, "UNI/USDT", "4h", partial)

    feed.script = truth
    feed._cache.clear()
    feed.fetch_ohlcv("UNI/USDT", "4h", limit=10, min_bars=1)

    got = feed._store_load("UNI/USDT", "4h", 50)
    highs = [round(float(h), 6) for h in got["high"].tail(4)]
    want = [round(float(r[2]), 6) for r in truth[-4:]]
    assert highs == want, (
        f"stale tail not repaired: {highs} != {want} — a single fetch must "
        "re-read enough of the tail to correct an accumulated freeze")
