"""Cross-sectional features need peers at the SPEC's timeframe.

The cycle built `universe_frames[sym] = {exec_tf: df}` — 15m only — whatever
timeframe the strategies traded. `FeatureCtx.for_symbol` requires the spec's
own timeframe in the peer's frames and returns None otherwise, so for a 4h
spec every peer resolved to None, `xs_rank` produced an all-NaN column, every
comparison against it was False, and the spec silently never fired.

That reads as "this mechanism has no edge" when it was never evaluated at
all — the same failure that made funding specs look worthless before their
derivatives were passed through.

No spec in the book uses xs features today, which is exactly why this needs
a test: the first one that does would be silently broken.
"""
from trader.kernel import Kernel


class _Feed:
    def __init__(self):
        self.asked = []
    def fetch_ohlcv(self, sym, tf, **kw):
        self.asked.append((sym, tf))
        import pandas as pd
        n = 50
        c = [100.0 + i for i in range(n)]
        return pd.DataFrame({
            "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": c, "high": c, "low": c, "close": c, "volume": [1.0] * n})


def _kernel(tfs):
    k = object.__new__(Kernel)
    k.feed = _Feed()
    k.cfg = {"timeframes": {"execution": "15m", "context": ["1h", "4h"]}}
    k._scan_timeframes = tfs
    return k


def test_peers_are_built_at_every_timeframe_the_book_trades():
    k = _kernel(("4h",))
    frames = k._universe_frames(["BTC/USDT", "ETH/USDT"])
    assert "4h" in frames["BTC/USDT"], \
        "a 4h spec must find its peers at 4h, not only at the execution tf"


def test_the_execution_timeframe_is_always_present():
    """Analysts and the orchestrator still read the execution frame."""
    k = _kernel(("4h",))
    assert "15m" in k._universe_frames(["BTC/USDT"])["BTC/USDT"]


def test_several_strategy_timeframes_are_all_covered():
    k = _kernel(("1h", "4h"))
    got = k._universe_frames(["BTC/USDT"])["BTC/USDT"]
    assert {"15m", "1h", "4h"} <= set(got)


def test_no_strategy_timeframes_still_yields_the_execution_frame():
    k = _kernel(())
    assert list(k._universe_frames(["BTC/USDT"])["BTC/USDT"]) == ["15m"]


def test_a_symbol_with_no_data_is_omitted_rather_than_half_built():
    k = _kernel(("4h",))
    k.feed.fetch_ohlcv = lambda sym, tf, **kw: None
    assert k._universe_frames(["BTC/USDT"]) == {}


def test_each_timeframe_is_fetched_once_per_symbol():
    k = _kernel(("4h", "4h"))
    k._universe_frames(["BTC/USDT"])
    assert len(k.feed.asked) == len(set(k.feed.asked))
