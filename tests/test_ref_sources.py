"""Each reference source, parsed from a recorded payload shape — no network."""
import pytest

from trader.data import ref_sources as S


def test_yahoo_parses_bars_and_drops_an_unmeasured_close():
    seen = {}

    def get(url, params=None):
        seen.update(url=url, params=params)
        return {"chart": {"result": [{
            "timestamp": [1_788_000_000, 1_788_003_600, 1_788_007_200],
            "indicators": {"quote": [{
                "open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5],
                "low": [0.5, 1.5, 2.5], "close": [1.2, None, 3.2],
                "volume": [10, 20, 30]}]}}]}}

    df = S.yahoo_frame("^GSPC", "1h", "5d", get)
    assert list(df.columns) == S.COLS
    assert df["close"].tolist() == [1.2, 3.2]
    assert "%5EGSPC" in seen["url"]
    assert seen["params"] == {"range": "5d", "interval": "1h"}


def test_yahoo_with_no_result_is_an_empty_frame_not_an_error():
    df = S.yahoo_frame("^GSPC", "1d", "1mo",
                       lambda url, params=None: {"chart": {"result": None}})
    assert df.empty and list(df.columns) == S.COLS


def test_defillama_stables_is_close_only_and_skips_bad_rows():
    rows = [{"date": "1511913600", "totalCirculatingUSD": {"peggedUSD": 1.5e9}},
            {"date": "1512000000", "totalCirculatingUSD": {}},
            {"date": "1512086400", "totalCirculatingUSD": {"peggedUSD": 1.6e9}}]
    df = S.defillama_stables(lambda url, params=None: rows)
    assert df["close"].tolist() == [1.5e9, 1.6e9]
    assert df["high"].isna().all() and df["open"].isna().all()


def test_coingecko_global_derives_total2_from_btc_dominance():
    payload = {"data": {"total_market_cap": {"usd": 3.0e12},
                        "market_cap_percentage": {"btc": 60.0, "usdt": 5.0}}}
    snap = S.coingecko_global(lambda url, params=None: payload)
    assert snap["cg_btc_d"] == 60.0 and snap["cg_usdt_d"] == 5.0
    assert snap["cg_total"] == 3.0e12
    assert snap["cg_total2"] == pytest.approx(1.2e12)


def test_binance_klines_pages_forward_until_the_venue_runs_dry():
    step = 4 * 3_600_000
    calls = []

    def fetch(symbol, tf, since=None, limit=None):
        calls.append(since)
        if since >= 4 * step:
            return []
        return [[since + i * step, 1.0, 2.0, 0.5, 1.5, 9.0]
                for i in range(2)]

    df = S.binance_klines(fetch, "BTCDOM/USDT:USDT", "4h", 0, 10 ** 13, page=2)
    assert len(df) == 4 and df["close"].tolist() == [1.5] * 4
    assert calls == [0, 2 * step, 4 * step]
