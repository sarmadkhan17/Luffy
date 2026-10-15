"""Live cache-only peer reads cover every strategy timeframe at the admission cut."""
from trader.kernel import Kernel


class _Feed:
    def __init__(self):
        self.asked = []
    def cached_ohlcv(self, sym, tf, **kw):
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
    from types import SimpleNamespace
    k.journal = SimpleNamespace(kv_get=lambda *args: "null")
    k.cfg = {"timeframes": {"execution": "15m", "context": ["1h", "4h"]}}
    k._admission_receipt = {"source_cut_ms": 1800000000000}
    k._scan_timeframes = tfs
    return k


def test_peers_are_built_at_every_timeframe_the_book_trades():
    k = _kernel(("4h",))
    frames = k._peer_frames(["BTC/USDT", "ETH/USDT"])
    assert "4h" in frames["BTC/USDT"], \
        "a 4h spec must find its peers at 4h, not only at the execution tf"


def test_the_execution_timeframe_is_always_present():
    """Analysts and the orchestrator still read the execution frame."""
    k = _kernel(("4h",))
    assert "15m" in k._peer_frames(["BTC/USDT"])["BTC/USDT"]


def test_several_strategy_timeframes_are_all_covered():
    k = _kernel(("1h", "4h"))
    got = k._peer_frames(["BTC/USDT"])["BTC/USDT"]
    assert {"15m", "1h", "4h"} <= set(got)


def test_no_strategy_timeframes_still_yields_the_execution_frame():
    k = _kernel(())
    assert list(k._peer_frames(["BTC/USDT"])["BTC/USDT"]) == ["15m"]


def test_unavailable_peer_stays_explicit_without_fabricated_data():
    k = _kernel(("4h",))
    k.feed.cached_ohlcv = lambda sym, tf, **kw: None
    assert k._peer_frames(["BTC/USDT"]) == {"BTC/USDT": {}}


def test_each_timeframe_is_fetched_once_per_symbol():
    k = _kernel(("4h", "4h"))
    k._peer_frames(["BTC/USDT"])
    assert len(k.feed.asked) == len(set(k.feed.asked))
