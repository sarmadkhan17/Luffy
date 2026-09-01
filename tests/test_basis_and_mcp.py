"""Basis, and the kernel's own MCP client.

`basis` was registered as a feature from day one with no fetcher behind it,
so every basis spec reported UNTESTED forever — a dead feature the Strategist
was still allowed to write against. And `.mcp.json` configures MCP servers
for the coding agent, not for the kernel, so the daemon could never reach one.
These cover both, without touching the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.data import derivatives as dv
from trader.data.mcp_client import MCPClient, from_config


# ── basis ────────────────────────────────────────────────────────────────
def _kline(close_ms: int, close: float):
    """Binance kline row — index 6 is closeTime, index 4 is close."""
    return [0, "0", "0", "0", f"{close}", "0", close_ms, "0", 0, "0", "0", "0"]


def _feed(perp: dict, spot: dict):
    f = dv.DerivFeed(db_path=":memory:")

    def fake_get(path, params, base=dv.FAPI):
        src = perp if base == dv.FAPI else spot
        return [_kline(ts, px) for ts, px in sorted(src.items())]

    f._get = fake_get
    return f


def test_basis_is_the_perp_premium_as_a_fraction():
    """perp 101 against spot 100 is a +1% basis."""
    df = _feed({1_000: 101.0}, {1_000: 100.0}).basis("BTC/USDT")
    assert len(df) == 1
    assert df["value"].iloc[0] == pytest.approx(0.01)


def test_a_perp_discount_is_negative():
    df = _feed({1_000: 99.0}, {1_000: 100.0}).basis("BTC/USDT")
    assert df["value"].iloc[0] == pytest.approx(-0.01)


def test_bars_missing_on_either_venue_produce_no_basis():
    """An inner join: half a basis is not a basis."""
    df = _feed({1_000: 101.0, 2_000: 102.0}, {1_000: 100.0}).basis("BTC/USDT")
    assert len(df) == 1


def test_a_zero_spot_price_is_dropped_not_divided_by():
    df = _feed({1_000: 101.0}, {1_000: 0.0}).basis("BTC/USDT")
    assert df.empty


def test_basis_rows_are_sorted_and_timestamped_utc():
    df = _feed({2_000: 102.0, 1_000: 101.0},
               {2_000: 100.0, 1_000: 100.0}).basis("BTC/USDT")
    assert list(df["ts"]) == sorted(df["ts"])
    assert str(df["ts"].dt.tz) == "UTC"


def test_basis_is_registered_as_a_recorded_series():
    """It was in SERIES but not in _SERIES_FETCHERS, which is exactly why
    nothing ever fetched it."""
    assert "basis" in dv.SERIES
    assert "basis" in dict(dv.DerivFeed._SERIES_FETCHERS)


# ── MCP client ───────────────────────────────────────────────────────────
def _sse(payload: dict) -> str:
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


class _Resp:
    def __init__(self, text, status=200, headers=None):
        self.text, self.status_code = text, status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_client_parses_a_server_sent_event_frame(monkeypatch):
    body = _sse({"jsonrpc": "2.0", "id": 1,
                 "result": {"serverInfo": {"name": "x"}}})
    monkeypatch.setattr("trader.data.mcp_client.requests.post",
                        lambda *a, **kw: _Resp(body, headers={
                            "mcp-session-id": "sid-1"}))
    c = MCPClient("http://server", name="x")
    assert c.connect()["serverInfo"]["name"] == "x"
    assert c._session_id == "sid-1"


def test_client_accepts_a_plain_json_reply_too(monkeypatch):
    """Not every server wraps a single reply in SSE."""
    monkeypatch.setattr(
        "trader.data.mcp_client.requests.post",
        lambda *a, **kw: _Resp(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}})))
    assert MCPClient("http://server").connect() == {"serverInfo": {}}


def test_a_json_rpc_error_is_raised_not_returned(monkeypatch):
    from trader.data.mcp_client import MCPError
    calls = {"n": 0}

    def post(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(_sse({"jsonrpc": "2.0", "id": 1,
                               "result": {"serverInfo": {}}}),
                         headers={"mcp-session-id": "s"})
        return _Resp(_sse({"jsonrpc": "2.0", "id": 2,
                           "error": {"code": -32601, "message": "no such tool"}}))

    monkeypatch.setattr("trader.data.mcp_client.requests.post", post)
    with pytest.raises(MCPError):
        MCPClient("http://server").call("nope")


def test_text_of_concatenates_text_content_blocks():
    assert MCPClient.text_of(
        {"content": [{"type": "text", "text": "a"},
                     {"type": "image"},
                     {"type": "text", "text": "b"}]}) == "a\nb"


def test_from_config_skips_a_server_with_no_url():
    got = from_config({"mcp": {"servers": {"bad": {}, "ok": {"url": "http://h"}}}})
    assert list(got) == ["ok"]


def test_from_config_on_an_empty_config_is_empty():
    assert from_config({}) == {}
