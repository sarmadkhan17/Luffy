"""Market data must come from the production public API, never the demo venue.

Binance Demo Trading simulates order flow. Sampled against production for the
same 4h bars, the stored candle history matched the DEMO endpoint EXACTLY
(375,667.5) while production futures reported 24,921.4 — a 15x inflation
running all the way back to 2022. Closes track production closely, so
price-only features were fine; every VOLUME-derived feature (VWAP, flow,
aggressor imbalance, volume z-scores) was computed on invented numbers.

The system's governing premise is "is this strategy working NOW", so "now"
must be the real market. `Universe` already routes scans to production public
data for exactly this reason; DataFeed was handed the kernel's demo TRADING
exchange instead.

Orders still go to the demo venue — the Executor holds that exchange
directly (engine/executor.py) and never needed DataFeed to carry it.
"""
from unittest.mock import patch

from trader.data.feed import DataFeed


def test_datafeed_defaults_to_production_public_data_on_demo():
    with patch("trader.data.feed.make_exchange") as mk:
        mk.return_value = "PRODUCTION"
        with patch("trader.core.config.Env.get", return_value="true"):
            feed = DataFeed()
            assert feed.ex == "PRODUCTION"
    kwargs = mk.call_args.kwargs
    assert kwargs.get("demo") is False, "data path must force demo=False"
    assert kwargs.get("with_keys") is False, "public data needs no keys"


def test_an_explicitly_injected_exchange_is_honoured():
    """Injection is how backtests and tests supply their own data source;
    silently swapping it for a live one would make them untestable."""
    fake = object()
    with patch("trader.core.config.Env.get", return_value="true"):
        assert DataFeed(exchange=fake).ex is fake


def test_kernel_does_not_hand_its_demo_trading_venue_to_the_feed():
    """The regression that poisoned the candle store: kernel.py built one
    demo exchange and passed it to DataFeed, so every candle came from the
    simulator."""
    code = [ln.split("#")[0] for ln in open("trader/kernel.py")]
    assert not any("DataFeed(self.exchange)" in ln for ln in code), \
        "the feed must not be constructed from the demo trading exchange"
