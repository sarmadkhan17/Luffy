"""The portfolio cut is detached from the existing Kernel position read."""
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from trader.core.instrument_registry import InstrumentId
from trader.core.types import MarketType
from trader.data.feed import make_exchange
from trader.kernel import Kernel
from trader.observability.portfolio_observation import observe_positions, trading_source

DEMO = ("demo", "https://demo-fapi.binance.com")
PRODUCTION = ("production", "https://fapi.binance.com")


def row(symbol="BTCUSDT", contracts=2.5, side="long"):
    return {"symbol": f"{symbol[:-4]}/USDT:USDT", "info": {"symbol": symbol},
            "contracts": contracts, "side": side}


def observe(rows, start=1000, received=1010, exchange_id="binanceusdm",
            provenance=DEMO):
    return observe_positions(rows, exchange_id=exchange_id,
                             market_type=MarketType.FUTURES,
                             environment=provenance[0], source_ref=provenance[1],
                             request_start_ms=start,
                             response_received_ms=received)


def kernel(rows, provenance=DEMO):
    k = object.__new__(Kernel)
    k.exchange = Mock(id="binanceusdm")
    host = provenance[1]
    k.exchange.urls = {"api": {"fapiPrivateV3": f"{host}/fapi/v3"}}
    k.exchange.fetch_positions.return_value = rows
    k.market_type = MarketType.FUTURES
    k.position_observation = None
    k.journal = Mock()
    k.journal.open_trades.return_value = []
    return k


def test_complete_immutable_detached_canonical_observation():
    rows = [row(), row("ETHUSDT", 0, None)]
    k = kernel(rows)
    assert k._detect_exchange_exits("BTC/USDT") == 0
    k.exchange.fetch_positions.assert_called_once_with()
    o = k.position_observation
    assert o.schema == "portfolio.observation.v1"
    assert o.complete and o.venue == "binance_usdm"
    assert (o.environment, o.source_ref) == DEMO
    assert o.market_type == MarketType.FUTURES
    assert o.request_start_ms <= o.response_received_ms
    assert o.as_of_ms == o.response_received_ms
    btc = o.get(InstrumentId("binance_usdm", MarketType.FUTURES, "BTCUSDT"))
    eth = o.get(InstrumentId("binance_usdm", MarketType.FUTURES, "ETHUSDT"))
    assert (btc.position_present, btc.side, btc.absolute_quantity) == (True, "long", 2.5)
    assert (eth.position_present, eth.side, eth.absolute_quantity) == (False, None, 0)
    rows[0]["contracts"] = 999
    rows[0]["info"]["symbol"] = "XUSDT"
    rows.clear()
    assert btc.absolute_quantity == 2.5 and btc.instrument_id.venue_symbol == "BTCUSDT"
    assert len(o.positions) == 2
    with pytest.raises(FrozenInstanceError):
        o.as_of_ms = 5000
    with pytest.raises(FrozenInstanceError):
        btc.absolute_quantity = 999


def test_cut_and_response_order_determine_id():
    a = observe([row(), row("ETHUSDT", 0, None)])
    b = observe([row("ETHUSDT", 0, None), row()])
    assert a.observation_id == b.observation_id
    assert a.positions == b.positions
    assert observe([row()], start=1001).observation_id != observe([row()]).observation_id
    assert observe([row()], received=1011).observation_id != observe([row()]).observation_id


def test_resolved_trading_venue_provenance_and_identity():
    assert trading_source(make_exchange("futures", demo=True, with_keys=False)) == DEMO
    assert trading_source(make_exchange("futures", demo=False, with_keys=False)) == PRODUCTION
    demo = observe([row()], provenance=DEMO)
    production = observe([row()], provenance=PRODUCTION)
    assert (demo.environment, demo.source_ref) == DEMO
    assert (production.environment, production.source_ref) == PRODUCTION
    assert demo.positions == production.positions
    assert demo.observation_id != production.observation_id


def test_response_rows_cannot_choose_environment():
    misleading = row()
    misleading["info"]["environment"] = "demo"
    k = kernel([misleading], provenance=PRODUCTION)
    assert k._detect_exchange_exits("BTC/USDT") == 0
    assert (k.position_observation.environment, k.position_observation.source_ref) == PRODUCTION
    k.exchange.fetch_positions.assert_called_once_with()


@pytest.mark.parametrize("provenance", [
    ("demo", PRODUCTION[1]), ("production", DEMO[1]),
    ("testnet", DEMO[1]), ("demo", "https://unknown.example"),
    (None, None),
])
def test_unsupported_or_ambiguous_provenance_fails_closed(provenance):
    with pytest.raises(ValueError, match="trading REST venue"):
        observe([row()], provenance=provenance)


def test_missing_or_unknown_resolved_venue_preserves_prior_observation():
    k = kernel([row()])
    k._detect_exchange_exits("BTC/USDT")
    good = k.position_observation
    k.exchange.urls = {"api": {"fapiPrivateV3": "https://unknown.example/fapi/v3"}}
    k.exchange.fetch_positions.return_value = [row("ETHUSDT")]
    assert k._detect_exchange_exits("BTC/USDT") == 0
    assert k.position_observation is good
    k.exchange.urls = {}
    assert k._detect_exchange_exits("BTC/USDT") == 0
    assert k.position_observation is good
    assert k.exchange.fetch_positions.call_count == 3


def test_kernel_retains_local_call_timing(monkeypatch):
    times = iter([1_000_000_000, 1_015_000_000])
    monkeypatch.setattr("trader.kernel.time.time_ns", lambda: next(times))
    k = kernel([row()])
    k._detect_exchange_exits("BTC/USDT")
    assert (k.position_observation.request_start_ms,
            k.position_observation.response_received_ms,
            k.position_observation.as_of_ms) == (1000, 1015, 1015)
    k.exchange.fetch_positions.assert_called_once_with()


@pytest.mark.parametrize("bad_rows", [
    None, {}, (row(),), [None], [{"symbol": "BTC/USDT:USDT", "contracts": 1}],
    [row("BTCUSDT", -1)], [row("BTCUSDT", float("nan"))],
    [row("BTCUSDT", float("inf"))], [row("BTCUSDT", "1")],
    [row("BTCUSDT", True)], [row("BTCUSDT", 1, None)],
    [row("BTCUSDT", 1, "both")], [row("BTCUSDT", 0, "both")],
    [row(), row()], [row(), row("BTCUSDT", 3, "short")],
    [row("BTC/USDT")], [dict(row(), symbol="ETH/USDT:USDT")],
    [dict(row(), info={"symbol": "btcUSDT"})],
])
def test_invalid_or_partial_response_fails_closed(bad_rows):
    with pytest.raises(ValueError):
        observe(bad_rows)


@pytest.mark.parametrize("exchange_id,market_type", [
    ("binance", MarketType.FUTURES), ("binanceusdm", MarketType.SPOT),
])
def test_unsupported_source_fails_closed(exchange_id, market_type):
    with pytest.raises(ValueError):
        observe_positions([row()], exchange_id=exchange_id, market_type=market_type,
                          environment=DEMO[0], source_ref=DEMO[1],
                          request_start_ms=1000, response_received_ms=1010)


def test_failed_and_invalid_later_reads_preserve_exact_good_object():
    k = kernel([row()])
    k._detect_exchange_exits("BTC/USDT")
    good = k.position_observation
    k.exchange.fetch_positions.side_effect = RuntimeError("network")
    assert k._detect_exchange_exits("BTC/USDT") == 0
    assert k.position_observation is good
    k.exchange.fetch_positions.side_effect = None
    k.exchange.fetch_positions.return_value = [row("BTCUSDT", float("nan"))]
    assert k._detect_exchange_exits("BTC/USDT") == 0
    assert k.position_observation is good
    assert (k.position_observation.observation_id, k.position_observation.as_of_ms) == (
        good.observation_id, good.as_of_ms)
    assert k.exchange.fetch_positions.call_count == 3


def test_failed_read_never_creates_flat_book():
    k = kernel([])
    k.exchange.fetch_positions.side_effect = RuntimeError("network")
    k._detect_exchange_exits("BTC/USDT")
    assert k.position_observation is None
    k.exchange.fetch_positions.side_effect = None
    k.exchange.fetch_positions.return_value = None
    k._detect_exchange_exits("BTC/USDT")
    assert k.position_observation is None
    assert observe([]).complete and observe([]).positions == ()
