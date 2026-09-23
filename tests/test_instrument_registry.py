"""Capability observations must not become inferred trading permission."""
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from trader.core.instrument_registry import (
    AccountTrading, AssetClass, Eligibility, EligibilityBasis,
    EligibilityBasisKind, InstrumentId, Presence, RegistrySnapshot,
)
from trader.core.types import MarketType
from trader.data.binance_usdm_registry import from_binance_usdm_responses


def _symbol(name="BTCUSDT", status="TRADING"):
    return {
        "symbol": name, "status": status, "contractType": "PERPETUAL",
        "baseAsset": name[:-4], "quoteAsset": "USDT", "marginAsset": "USDT",
        "onboardDate": 1700000000000, "pricePrecision": 2, "quantityPrecision": 0,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "1.000", "minQty": "2"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.00"},
        ],
    }


def _snapshot(symbols=None, **kwargs):
    return from_binance_usdm_responses(
        exchange_info={"symbols": symbols if symbols is not None else [_symbol()]},
        as_of_ms=1800000000000, account_scope="demo-account", **kwargs,
    )


def test_immutable_records_and_snapshot_and_caller_input():
    symbols = [_symbol()]
    snapshot = _snapshot(symbols)
    symbols[0]["status"] = "BREAK"
    symbols.append(_symbol("ETHUSDT"))
    assert len(snapshot.records) == 1
    assert snapshot.records[0].venue_status == "TRADING"
    with pytest.raises(FrozenInstanceError):
        snapshot.records[0].venue_status = "BREAK"
    with pytest.raises(FrozenInstanceError):
        snapshot.as_of_ms = 0


def test_deterministic_serialization_identity_and_order():
    a = _snapshot([_symbol("ETHUSDT"), _symbol("BTCUSDT")],
                  symbol_configs=[{"symbol": "ETHUSDT"}, {"symbol": "BTCUSDT"}])
    b = _snapshot([_symbol("BTCUSDT"), _symbol("ETHUSDT")],
                  symbol_configs=[{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}])
    assert a.canonical_json() == b.canonical_json()
    assert a.snapshot_id == b.snapshot_id
    assert json.loads(a.canonical_json())["as_of_ms"] == 1800000000000
    assert [r.instrument_id.venue_symbol for r in a.all_instruments()] == ["BTCUSDT", "ETHUSDT"]


def test_duplicate_canonical_identity_refused():
    with pytest.raises(ValueError, match="duplicate"):
        _snapshot([_symbol(), _symbol()])


def test_metadata_presence_and_absence_never_imply_eligibility():
    snapshot = _snapshot([_symbol("BTCUSDT"), _symbol("ETHUSDT")],
                         account={"canTrade": True},
                         symbol_configs=[{"symbol": "BTCUSDT"}],
                         leverage_brackets=[{"symbol": "BTCUSDT"}])
    assert snapshot.account_trading is AccountTrading.ENABLED
    btc, eth = snapshot.records
    assert btc.venue_listing is Presence.PRESENT
    assert btc.symbol_config is Presence.PRESENT
    assert btc.leverage_bracket is Presence.PRESENT
    assert eth.symbol_config is Presence.ABSENT
    assert eth.leverage_bracket is Presence.ABSENT
    assert all(r.account_eligibility is Eligibility.UNKNOWN for r in snapshot.records)
    assert snapshot.by_account_eligibility(Eligibility.ELIGIBLE) == ()


@pytest.mark.parametrize("field", ["symbol_configs", "leverage_brackets"])
def test_each_account_metadata_population_alone_means_unknown(field):
    snapshot = _snapshot(**{field: [{"symbol": "BTCUSDT"}]})
    assert snapshot.records[0].account_eligibility is Eligibility.UNKNOWN


def test_nontrading_status_and_global_denial_are_distinct():
    snapshot = _snapshot([_symbol(status="BREAK")], account={"canTrade": False})
    assert snapshot.venue_active() == ()
    assert snapshot.account_trading is AccountTrading.DISABLED
    assert snapshot.records[0].account_eligibility is Eligibility.UNKNOWN
    record = snapshot.records[0]
    basis = EligibilityBasis(EligibilityBasisKind.ACCOUNT_SYMBOL_PERMISSION,
                             record.instrument_id, "authoritative-permission-record-1")
    with pytest.raises(ValueError, match="disabled account"):
        RegistrySnapshot(as_of_ms=snapshot.as_of_ms, account_scope=snapshot.account_scope,
                         account_trading=snapshot.account_trading, source=snapshot.source,
                         records=(replace(record, account_eligibility=Eligibility.ELIGIBLE,
                                          eligibility_basis=basis),))


def test_asset_class_is_unknown_without_authoritative_classification():
    tradfi = _symbol("GOOGLEUSDT")
    tradfi["underlyingType"] = "INDEX"
    snapshot = _snapshot([_symbol(), tradfi])
    assert all(record.asset_class is AssetClass.UNKNOWN for record in snapshot.records)
    google = snapshot.get(InstrumentId("binance_usdm", MarketType.FUTURES, "GOOGLEUSDT"))
    assert google.venue_underlying_type == "INDEX"
    assert snapshot.get(InstrumentId("binance_usdm", MarketType.FUTURES,
                                     "BTCUSDT")).venue_underlying_type is None


@pytest.mark.parametrize("field", ["symbol_configs", "leverage_brackets"])
@pytest.mark.parametrize("bad", [{}, {"symbol": ""}, {"symbol": "   "},
                                      {"symbol": None}, {"symbol": 123}, "BTCUSDT"])
def test_malformed_population_fails_closed(field, bad):
    with pytest.raises(ValueError, match="population row"):
        _snapshot(**{field: [{"symbol": "BTCUSDT"}, bad]})


def test_supplied_empty_population_is_absent_and_unsupplied_is_unknown():
    assert _snapshot(symbol_configs=[]).records[0].symbol_config is Presence.ABSENT
    assert _snapshot().records[0].symbol_config is Presence.UNKNOWN


def test_eligibility_requires_matching_typed_per_symbol_basis():
    record = _snapshot().records[0]
    # Generic metadata provenance cannot stand in for the typed basis.
    for state in (Eligibility.ELIGIBLE, Eligibility.INELIGIBLE):
        with pytest.raises(ValueError, match="basis required"):
            replace(record, account_eligibility=state,
                    evidence=("exchangeInfo", "symbolConfig", "leverageBracket",
                              "data", "static_config", "account:canTrade=true"))
    with pytest.raises(ValueError, match="evidence reference"):
        EligibilityBasis(EligibilityBasisKind.ACCOUNT_SYMBOL_PERMISSION,
                         record.instrument_id, " ")
    wrong_symbol = InstrumentId("binance_usdm", MarketType.FUTURES, "ETHUSDT")
    with pytest.raises(ValueError, match="per-symbol"):
        replace(record, account_eligibility=Eligibility.ELIGIBLE,
                eligibility_basis=EligibilityBasis(EligibilityBasisKind.ACCOUNT_SYMBOL_PERMISSION,
                                                   wrong_symbol, "permission-1"))
    positive = EligibilityBasis(EligibilityBasisKind.ACCOUNT_SYMBOL_PERMISSION,
                                record.instrument_id, "permission-1")
    negative = EligibilityBasis(EligibilityBasisKind.ACCOUNT_SYMBOL_REFUSAL,
                                record.instrument_id, "account-rejection-1")
    with pytest.raises(ValueError, match="does not match"):
        replace(record, account_eligibility=Eligibility.INELIGIBLE,
                eligibility_basis=positive)
    assert replace(record, account_eligibility=Eligibility.ELIGIBLE,
                   eligibility_basis=positive).eligibility_basis == positive
    assert replace(record, account_eligibility=Eligibility.INELIGIBLE,
                   eligibility_basis=negative).eligibility_basis == negative
    with pytest.raises(ValueError, match="UNKNOWN"):
        replace(record, eligibility_basis=positive)


def test_supplied_precision_minimums_and_provenance_retained():
    snapshot = _snapshot(data_symbols={"BTCUSDT"})
    record = snapshot.records[0]
    assert record.instrument_id == InstrumentId("binance_usdm", MarketType.FUTURES, "BTCUSDT")
    assert record.constraints.price_tick == "0.1"
    assert record.constraints.quantity_step == "1"
    assert record.constraints.minimum_quantity == "2"
    assert record.constraints.minimum_notional == "5"
    assert record.constraints.amount_precision == 0
    assert record.data_availability is Presence.PRESENT
    assert record.source == snapshot.source == "binance-usdm:exchangeInfo"
    assert record.observed_at_ms == snapshot.as_of_ms
    assert record.evidence == ("data:observed_symbols", "exchangeInfo")


def test_unknown_when_endpoint_not_supplied_and_queries_are_exact():
    snapshot = _snapshot()
    record = snapshot.get(InstrumentId("binance_usdm", MarketType.FUTURES, "BTCUSDT"))
    assert record is not None
    assert record.symbol_config is Presence.UNKNOWN
    assert record.leverage_bracket is Presence.UNKNOWN
    assert record.data_availability is Presence.UNKNOWN
    assert snapshot.by_market_type(MarketType.FUTURES) == (record,)
    assert snapshot.get(InstrumentId("binance_usdm", MarketType.FUTURES, "ETHUSDT")) is None
    assert not hasattr(snapshot, "eligible_symbols")


def test_loaded_ccxt_contract_metadata_retained_only_when_supplied():
    symbol = _symbol()
    symbol.pop("pricePrecision")
    symbol.pop("quantityPrecision")
    symbol["filters"] = []
    snapshot = _snapshot([symbol], ccxt_markets={"BTC/USDT:USDT": {
        "id": "BTCUSDT", "contractSize": 0.001,
        "precision": {"price": 0.001, "amount": 0.1},
        "limits": {"amount": {"min": 1}, "cost": {"min": 10}},
    }})
    record = snapshot.records[0]
    assert record.contract_multiplier == "0.001"
    assert record.constraints.amount_precision is None
    assert record.constraints.price_precision is None
    assert record.constraints.minimum_quantity == "1"
    assert record.constraints.minimum_notional == "10"
    assert "ccxt:loaded_market" in record.evidence


def test_duplicate_market_filter_refused_instead_of_order_dependent_result():
    symbol = _symbol()
    symbol["filters"].append({"filterType": "LOT_SIZE", "minQty": "3"})
    with pytest.raises(ValueError, match="duplicate exchangeInfo filter"):
        _snapshot([symbol])


def test_probe_population_shapes_do_not_establish_permission():
    # The real probe found V2 positions matching config, while V3 contained
    # only five active/open-order symbols. This small fixture has the same
    # asymmetric shape; neither population is a per-symbol permission source.
    symbols = [_symbol("BTCUSDT"), _symbol("ETHUSDT"), _symbol("ENSOUSDT", "BREAK")]
    snapshot = _snapshot(
        symbols,
        symbol_configs=[{"symbol": s["symbol"]} for s in symbols] +
                       [{"symbol": "EXTRAFUTURE"}],
        leverage_brackets=[{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"},
                           {"symbol": "BRACKETONLY"}],
        account={"canTrade": True, "positions": [{"symbol": s["symbol"]} for s in symbols]},
        account_v3={"positions": [{"symbol": "BTCUSDT"}]},
        account_config={"canTrade": True, "multiAssetsMargin": False},
    )
    assert snapshot.account_trading is AccountTrading.ENABLED
    assert len(snapshot.venue_active()) == 2
    assert all(r.account_eligibility is Eligibility.UNKNOWN for r in snapshot.records)
    assert snapshot.get(InstrumentId("binance_usdm", MarketType.FUTURES,
                                     "ENSOUSDT")).leverage_bracket is Presence.ABSENT
    assert all("account:v3:position_population_nonpermission" in r.evidence
               for r in snapshot.records)


def test_account_config_can_supply_global_denial_and_conflict_refuses():
    snapshot = _snapshot(account_config={"canTrade": False})
    assert snapshot.account_trading is AccountTrading.DISABLED
    assert snapshot.records[0].account_eligibility is Eligibility.UNKNOWN
    with pytest.raises(ValueError, match="conflicting"):
        _snapshot(account={"canTrade": True}, account_config={"canTrade": False})


def test_core_domain_has_no_network_or_order_dependency():
    import inspect
    import trader.core.instrument_registry as domain

    source = inspect.getsource(domain)
    assert "import ccxt" not in source
    assert "requests" not in source
    assert "create_order" not in source
    assert "fetch_" not in source
