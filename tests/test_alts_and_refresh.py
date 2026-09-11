"""The alt index, and one refresh of every reference.

A source that fails must cost only itself: Yahoo's API is unofficial, and a
refresh that died on its first error would leave every other series stale.
"""
import types

import pandas as pd
import pytest

from trader.data import ref_sources as S
from trader.data.references import HOUR, RefStore

STEP = 4 * HOUR


def _px(closes, start=0):
    return pd.DataFrame({
        "ts": pd.to_datetime([start + i * STEP for i in range(len(closes))],
                             unit="ms", utc=True),
        "close": closes})


def test_the_index_is_the_equal_weight_mean_return():
    idx = S.alts_index({"A": _px([100, 110]), "B": _px([100, 120])},
                       min_members=2)
    assert idx["close"].tolist() == pytest.approx([115.0])
    assert idx["high"].isna().all()


def test_opposite_moves_cancel():
    idx = S.alts_index({"A": _px([100, 110, 121]), "B": _px([100, 90, 81])},
                       min_members=2)
    assert idx["close"].tolist() == pytest.approx([100.0, 100.0])


def test_too_few_members_is_nan_not_a_level():
    idx = S.alts_index({"A": _px([100, 110, 121])}, min_members=2)
    assert idx.empty


def test_a_member_missing_a_bar_does_not_fake_its_return():
    a = _px([100, 110, 121])
    b = _px([100, 120, 144]).drop(index=1)       # B has no middle bar
    c = _px([100, 100, 100])
    idx = S.alts_index({"A": a, "B": b, "C": c}, min_members=2)
    # bar 1: A +10%, C 0%, B unknown -> +5%; bar 2: A +10%, C 0%, B's return
    # spans the gap and is unknown -> +5% again
    assert idx["close"].tolist() == pytest.approx([105.0, 110.25])


class _Ex:
    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        # one bar per call: a short page ends the paging loop
        return [[since, 1.0, 2.0, 0.5, 1.5, 9.0]]


def _feed():
    frames = {s: _px([100.0 + i for i in range(3)])
              for s in ("A", "B", "C", "D", "E")}
    return types.SimpleNamespace(
        ex=_Ex(), cached_ohlcv=lambda s, tf, limit=None: frames.get(s))


def _get(fail_yahoo=False):
    def get(url, params=None):
        if "yahoo" in url:
            if fail_yahoo:
                raise ConnectionError("yahoo down")
            return {"chart": {"result": [{"timestamp": [1_600_000_000],
                    "indicators": {"quote": [{"open": [1.0], "high": [1.0],
                                              "low": [1.0], "close": [1.0],
                                              "volume": [1.0]}]}}]}}
        if "llama" in url:
            return [{"date": "1600000000",
                     "totalCirculatingUSD": {"peggedUSD": 2.0e11}}]
        if "coingecko" in url:
            return {"data": {"total_market_cap": {"usd": 3e12},
                             "market_cap_percentage": {"btc": 55.0,
                                                       "usdt": 5.0}}}
        raise AssertionError(url)
    return get


def test_one_refresh_writes_every_source(tmp_path):
    store = RefStore(tmp_path / "c.db")
    out = S.refresh_all(_feed(), ["A", "B", "C", "D", "E"], store,
                        _get(), now_ms=10 ** 13)
    assert out["spx"] == 1 and out["vix_1h"] == 1
    assert out["btcdom"] >= 1 and out["stables"] == 1
    assert out["alts"] == 2
    assert out["cg_btc_d"] == 1 and store.load("cg_total2") is not None


def test_a_failing_source_costs_only_itself(tmp_path):
    store = RefStore(tmp_path / "c.db")
    out = S.refresh_all(_feed(), ["A", "B", "C", "D", "E"], store,
                        _get(fail_yahoo=True), now_ms=10 ** 13)
    assert str(out["spx"]).startswith("error:")
    assert out["stables"] == 1 and out["cg_btc_d"] == 1


def test_coingecko_is_snapshotted_at_most_every_four_hours(tmp_path):
    store = RefStore(tmp_path / "c.db")
    S.refresh_all(_feed(), [], store, _get(), now_ms=10 ** 13)
    out = S.refresh_all(_feed(), [], store, _get(), now_ms=10 ** 13 + HOUR)
    assert "cg_btc_d" not in out
