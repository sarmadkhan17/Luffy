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
    src = _df()
    feed.save("BTC/USDT", "funding", src)
    out = feed.load("BTC/USDT", "funding")
    assert len(out) == 10 and out["value"].iloc[-1] == 9
    # TIMESTAMPS MUST ROUND-TRIP. A resolution-blind ms conversion divides
    # milliseconds by a million and lands everything in 1970; align() then
    # forward-fills one constant across the frame and every derivative
    # feature silently returns garbage instead of failing.
    assert list(out["ts"]) == list(src["ts"])


def test_stored_timestamps_are_epoch_millis(feed):
    feed.save("BTC/USDT", "funding", _df(n=1))
    ts = feed.db.execute("SELECT ts FROM derivs").fetchone()[0]
    assert ts > 1_500_000_000_000, f"{ts} is not epoch ms — resolution bug"


def test_roundtrip_survives_a_nanosecond_resolution_frame(feed):
    """Guard both branches of the unit conversion."""
    src = _df(n=5)
    src["ts"] = src["ts"].astype("datetime64[ns, UTC]")
    feed.save("ETH/USDT", "oi", src)
    assert list(feed.load("ETH/USDT", "oi")["ts"]) == list(src["ts"])


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
    for name in ("funding", "open_interest", "taker_ratio", "ls_ratio",
                 "basis"):
        monkeypatch.setattr(feed, name, lambda *a, **k: _df(n=3))
    counts = feed.record_all(["BTC/USDT"], delay=0.0)
    assert set(counts) == {"funding", "oi", "taker_ratio", "ls_ratio",
                           "basis"}
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


def test_funding_history_pages_forward(feed, monkeypatch):
    """One call caps at 1000 rows (<1 year at 8h settlement) while the candle
    store holds 5 years, so funding must page or every funding spec is
    refused for coverage. Binance returns the EARLIEST rows in a window, so
    paging must walk startTime forward — walking endTime backwards re-reads
    the oldest page and stops after one call."""
    step = 8 * 3600 * 1000
    calls = []

    def fake_get(path, params):
        start = params["startTime"]
        calls.append(start)
        if len(calls) > 3:
            return []
        return [{"fundingTime": start + i * step, "fundingRate": "0.0001"}
                for i in range(1000)]
    monkeypatch.setattr(feed, "_get", fake_get)
    out = feed.funding_history("BTC/USDT", years=4.0, delay=0.0)
    assert len(out) > 1000, "must page past the single-call cap"
    assert out["ts"].is_monotonic_increasing
    assert calls == sorted(calls) and len(calls) > 1, "must advance forward"


def test_funding_history_stops_when_no_progress(feed, monkeypatch):
    """A server returning the same window forever must not spin."""
    import time as _t
    now = int(_t.time() * 1000)
    monkeypatch.setattr(feed, "_get", lambda p, q: [
        {"fundingTime": now, "fundingRate": "0.0001"}] * 1000)
    out = feed.funding_history("BTC/USDT", years=4.0, delay=0.0)
    assert len(out) >= 1


def test_funding_history_empty_response_is_safe(feed, monkeypatch):
    monkeypatch.setattr(feed, "_get", lambda p, q: [])
    assert feed.funding_history("BTC/USDT", years=1.0, delay=0.0).empty


def test_funding_history_honours_an_explicit_floor(feed, monkeypatch):
    """The backfill floor is the frame the spec is scored over, not a fixed
    number of years. A store holding 5 years of 4h bars and 4 years of
    funding leaves the oldest fifth of every frame paying the flat
    conservative rate — a cost model, not a measurement."""
    step = 8 * 3600 * 1000
    floor = 1_600_000_000_000          # 2020-09-13
    calls = []

    def fake_get(path, params):
        calls.append(params["startTime"])
        if len(calls) > 2:
            return []
        return [{"fundingTime": params["startTime"] + i * step,
                 "fundingRate": "0.0001"} for i in range(1000)]

    monkeypatch.setattr(feed, "_get", fake_get)
    out = feed.funding_history("BTC/USDT", years=1.0, since_ms=floor, delay=0.0)
    assert calls[0] == floor, "since_ms must override the years default"
    assert not out.empty


def test_basis_history_honours_an_explicit_floor(feed, monkeypatch):
    floor = 1_600_000_000_000
    starts = []

    def fake_between(symbol, period, start_ms):
        starts.append(start_ms)
        return feed._frame([(start_ms + 3_600_000, 0.001)])

    monkeypatch.setattr(feed, "_basis_between", fake_between)
    feed.basis_history("BTC/USDT", years=1.0, since_ms=floor, delay=0.0)
    assert starts[0] == floor


def test_backfill_passes_the_per_symbol_floor_down(feed, monkeypatch):
    """One floor per symbol: a coin listed in 2024 has no 2021 frame to
    reach, and asking for one just burns pages against an empty window."""
    seen = {}

    def fake_funding(sym, years=4.0, delay=0.25, since_ms=None):
        seen[("funding", sym)] = since_ms
        return feed._frame([])

    def fake_basis(sym, years=2.0, period="1h", delay=0.25, since_ms=None):
        seen[("basis", sym)] = since_ms
        return feed._frame([])

    monkeypatch.setattr(feed, "funding_history", fake_funding)
    monkeypatch.setattr(feed, "basis_history", fake_basis)
    monkeypatch.setattr(feed, "open_interest", lambda *a, **k: feed._frame([]))
    monkeypatch.setattr(feed, "taker_ratio", lambda *a, **k: feed._frame([]))
    monkeypatch.setattr(feed, "ls_ratio", lambda *a, **k: feed._frame([]))

    feed.backfill(["BTC/USDT", "TAO/USDT"], delay=0.0,
                  since={"BTC/USDT": 111, "TAO/USDT": 222})
    assert seen[("funding", "BTC/USDT")] == 111
    assert seen[("basis", "TAO/USDT")] == 222


def test_backfill_without_a_floor_keeps_the_years_default(feed, monkeypatch):
    seen = {}

    def fake_funding(sym, years=4.0, delay=0.25, since_ms=None):
        seen[sym] = since_ms
        return feed._frame([])

    monkeypatch.setattr(feed, "funding_history", fake_funding)
    monkeypatch.setattr(feed, "basis_history",
                        lambda *a, **k: feed._frame([]))
    monkeypatch.setattr(feed, "open_interest", lambda *a, **k: feed._frame([]))
    monkeypatch.setattr(feed, "taker_ratio", lambda *a, **k: feed._frame([]))
    monkeypatch.setattr(feed, "ls_ratio", lambda *a, **k: feed._frame([]))
    feed.backfill(["BTC/USDT"], delay=0.0)
    assert seen["BTC/USDT"] is None
