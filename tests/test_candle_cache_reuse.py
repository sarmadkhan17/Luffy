"""History already in candles.db is never downloaded again.

Two paths re-paged the exchange for bars the store already held:
  * force=True skipped the store entirely — agents/validate.py re-walked
    1,920 fifteen-minute bars per symbol on every run;
  * a symbol younger than the requested limit could never be "full", so
    every call re-walked its entire history from the listing date.
And the dashboard's chart endpoint built its feed on the DEMO venue, so a
chart view merged simulated candles into the production store.
"""
import inspect

from tests.test_feed_pagination import FakeEx
from trader.data.feed import DataFeed


def test_force_refreshes_the_tail_not_the_history(tmp_path):
    p = tmp_path / "c.db"
    DataFeed(exchange=FakeEx(total=4000), db_path=p).fetch_ohlcv(
        "BTC/USDT", "15m", limit=2900)
    ex = FakeEx(total=4000)
    df = DataFeed(exchange=ex, db_path=p).fetch_ohlcv(
        "BTC/USDT", "15m", limit=2900, force=True)
    assert len(df) == 2900
    assert len(ex.calls) == 1, "force must re-read the tail, not re-page"


def test_a_young_symbol_is_not_repaged_every_call(tmp_path):
    p = tmp_path / "c.db"
    first = FakeEx(total=1500)                 # listed 1,500 bars ago
    DataFeed(exchange=first, db_path=p).fetch_ohlcv(
        "NEW/USDT", "15m", limit=4000)
    assert len(first.calls) >= 2               # the first walk pages
    again = FakeEx(total=1500)
    df = DataFeed(exchange=again, db_path=p).fetch_ohlcv(
        "NEW/USDT", "15m", limit=4000)
    assert len(df) == 1500
    assert len(again.calls) == 1, "the start of history is remembered"


def test_the_dashboard_charts_read_production_not_the_demo_venue():
    """The chart endpoint's fetch_ohlcv WRITES the shared store, so it must
    read production. The marks/price feeds only call .price() on the demo
    venue — the right mark for demo positions, and nothing is stored."""
    from trader.dashboard import server
    assert "_feed_cache.append(DataFeed())" in inspect.getsource(server)
