import pandas as pd
import pytest

from trader.data.derivatives import SERIES, DerivFeed, to_binance


@pytest.fixture
def feed(tmp_path):
    return DerivFeed(db_path=tmp_path / "derivs.db")


def _df(start="2026-08-01", n=10, freq="15min", val=None):
    return pd.DataFrame({
        "ts": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
        "value": list(range(n)) if val is None else [val] * n})


def test_symbol_conversion():
    assert to_binance("BTC/USDT") == "BTCUSDT"
    assert to_binance("BTC/USDT:USDT") == "BTCUSDT"


def test_known_series_names():
    assert SERIES == ("funding", "oi", "taker_ratio", "ls_ratio", "basis")


def test_store_roundtrip(feed):
    feed.save("BTC/USDT", "funding", _df())
    out = feed.load("BTC/USDT", "funding")
    assert len(out) == 10 and out["value"].iloc[-1] == 9


def test_store_is_idempotent(feed):
    df = _df(n=5, val=1.0)
    feed.save("BTC/USDT", "oi", df)
    feed.save("BTC/USDT", "oi", df)
    assert len(feed.load("BTC/USDT", "oi")) == 5


def test_store_merges_new_observations(feed):
    feed.save("BTC/USDT", "oi", _df(n=5, val=1.0))
    feed.save("BTC/USDT", "oi", _df("2026-08-01 01:00", n=5, val=2.0))
    assert len(feed.load("BTC/USDT", "oi")) == 9      # one overlapping bar


def test_series_are_isolated_per_symbol_and_kind(feed):
    feed.save("BTC/USDT", "oi", _df(n=5))
    feed.save("ETH/USDT", "oi", _df(n=3))
    feed.save("BTC/USDT", "funding", _df(n=2))
    assert len(feed.load("BTC/USDT", "oi")) == 5
    assert len(feed.load("ETH/USDT", "oi")) == 3
    assert len(feed.load("BTC/USDT", "funding")) == 2


def test_missing_series_returns_none(feed):
    assert feed.load("BTC/USDT", "funding") is None


def test_coverage_reports_what_accumulated(feed):
    feed.save("BTC/USDT", "oi", _df(n=5))
    cov = feed.coverage()
    assert cov[("BTC/USDT", "oi")][0] == 5


def test_parse_funding_payload(feed):
    raw = [{"symbol": "BTCUSDT", "fundingTime": 1756000000000,
            "fundingRate": "0.0001"}]
    df = feed._parse_funding(raw)
    assert list(df.columns) == ["ts", "value"]
    assert df["value"].iloc[0] == pytest.approx(0.0001)


def test_parse_open_interest_payload(feed):
    raw = [{"symbol": "BTCUSDT", "sumOpenInterest": "12345.6",
            "sumOpenInterestValue": "1.0", "timestamp": 1756000000000}]
    assert feed._parse_oi(raw)["value"].iloc[0] == pytest.approx(12345.6)


def test_parse_taker_ratio_payload(feed):
    raw = [{"buySellRatio": "1.42", "timestamp": 1756000000000}]
    assert feed._parse_ratio(raw, "buySellRatio")["value"].iloc[0] == \
        pytest.approx(1.42)


def test_record_all_is_safe_when_every_endpoint_fails(feed, monkeypatch):
    """The recorder runs in the live kernel. A dead endpoint must never
    raise into the daemon thread."""
    monkeypatch.setattr(feed, "_get", lambda *a, **k: [])
    assert feed.record_all(["BTC/USDT"], delay=0.0) == {}


def test_record_all_survives_a_raising_endpoint(feed, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(feed, "funding", boom)
    monkeypatch.setattr(feed, "open_interest", lambda *a, **k: _df(n=3))
    monkeypatch.setattr(feed, "taker_ratio", lambda *a, **k: _df(n=3))
    monkeypatch.setattr(feed, "ls_ratio", lambda *a, **k: _df(n=3))
    counts = feed.record_all(["BTC/USDT"], delay=0.0)
    assert "funding" not in counts and counts["oi"] == 3


def test_record_all_writes_each_series(feed, monkeypatch):
    for name in ("funding", "open_interest", "taker_ratio", "ls_ratio"):
        monkeypatch.setattr(feed, name, lambda *a, **k: _df(n=3))
    counts = feed.record_all(["BTC/USDT"], delay=0.0)
    assert set(counts) == {"funding", "oi", "taker_ratio", "ls_ratio"}
    assert feed.load("BTC/USDT", "funding") is not None


def test_backfill_pulls_coarser_periods_and_skips_funding(feed, monkeypatch):
    """Funding already has years at its native cadence; the 30-day-window
    series are the ones that need a coarse pull."""
    seen = []

    def rec(name):
        def fn(sym, period="15m", **k):
            seen.append((name, period))
            return _df(n=3)
        return fn
    for m in ("open_interest", "taker_ratio", "ls_ratio"):
        monkeypatch.setattr(feed, m, rec(m))
    monkeypatch.setattr(feed, "funding", rec("funding"))
    counts = feed.backfill(["BTC/USDT"], delay=0.0)
    assert not any(n == "funding" for n, _ in seen)
    assert {p for _, p in seen} == set(DerivFeed.BACKFILL_PERIODS)
    assert "oi@4h" in counts and "oi@1h" in counts


def test_mixed_granularity_coexists_in_the_store(feed):
    """Coarse history and fine recent data must merge, not collide."""
    feed.save("BTC/USDT", "oi", _df("2026-07-01", n=10, freq="4h", val=1.0))
    feed.save("BTC/USDT", "oi", _df("2026-08-01", n=10, freq="15min", val=2.0))
    out = feed.load("BTC/USDT", "oi")
    assert len(out) == 20 and out["ts"].is_monotonic_increasing
