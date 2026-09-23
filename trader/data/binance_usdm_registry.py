"""Read-only translation of already-fetched Binance USD-M capability responses.

No endpoint call is made here. Metadata coverage is not account permission.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from ..core.instrument_registry import (
    AccountTrading, AssetClass, Capability, Eligibility, InstrumentId, InstrumentRecord,
    OrderConstraints, Presence, RegistrySnapshot,
)
from ..core.types import MarketType


def _decimal(value: object) -> str | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid numeric market metadata") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("invalid numeric market metadata")
    return format(number.normalize(), "f")


def _population(response: Sequence[Mapping] | None, *, key: str = "symbol") -> set[str] | None:
    if response is None:
        return None
    if not isinstance(response, Sequence) or isinstance(response, (str, bytes)):
        raise ValueError("population response must be a sequence of rows")
    population = set()
    for index, row in enumerate(response):
        if not isinstance(row, Mapping):
            raise ValueError(f"population row {index} must be a mapping")
        symbol = row.get(key)
        if not isinstance(symbol, str) or not symbol or symbol != symbol.strip():
            raise ValueError(f"population row {index} needs a non-empty symbol")
        population.add(symbol)
    return population


def _presence(population: set[str] | None, symbol: str) -> Presence:
    if population is None:
        return Presence.UNKNOWN
    return Presence.PRESENT if symbol in population else Presence.ABSENT


def _integer(value: object) -> int | None:
    if value is None:
        return None
    result = int(value)
    return result if result > 0 else None


def _precision(value: object) -> int | None:
    if value is None:
        return None
    result = int(value)
    if result < 0:
        raise ValueError("negative precision")
    return result


def from_binance_usdm_responses(
    *, exchange_info: Mapping, as_of_ms: int, account_scope: str,
    account: Mapping | None = None,
    account_v3: Mapping | None = None,
    account_config: Mapping | None = None,
    symbol_configs: Sequence[Mapping] | None = None,
    leverage_brackets: Sequence[Mapping] | None = None,
    ccxt_markets: Mapping[str, Mapping] | Sequence[Mapping] | None = None,
    data_symbols: set[str] | None = None,
    source: str = "binance-usdm:exchangeInfo",
) -> RegistrySnapshot:
    """Build a capability observation; all response arguments are already fetched.

    ``account`` is the V2 account response. Its positions and the V3 account
    response have no per-symbol permission semantics. ``account_config`` and
    V2 ``canTrade`` are account-global only. ``data_symbols`` is observed data
    coverage only. Missing population members never imply ineligibility.
    """
    if not isinstance(exchange_info, Mapping) or "symbols" not in exchange_info:
        raise ValueError("exchangeInfo symbols are required")
    markets = ccxt_markets.values() if isinstance(ccxt_markets, Mapping) else (ccxt_markets or ())
    by_venue_id = {}
    for market in markets:
        venue_id = market.get("id")
        if venue_id:
            if venue_id in by_venue_id:
                raise ValueError("duplicate loaded market id")
            by_venue_id[venue_id] = market
    configs = _population(symbol_configs)
    brackets = _population(leverage_brackets)
    global_flags = [response["canTrade"] for response in (account, account_config)
                    if response is not None and isinstance(response.get("canTrade"), bool)]
    if len(set(global_flags)) > 1:
        raise ValueError("conflicting account-global canTrade responses")
    global_state = (AccountTrading.ENABLED if global_flags[0] else AccountTrading.DISABLED) \
        if global_flags else AccountTrading.UNKNOWN

    records = []
    for symbol in exchange_info["symbols"]:
        venue_symbol = str(symbol["symbol"])
        market = by_venue_id.get(venue_symbol, {})
        filters = {}
        for row in symbol.get("filters", ()):
            kind = row.get("filterType")
            if kind in filters:
                raise ValueError("duplicate exchangeInfo filter type")
            filters[kind] = row
        lot = filters.get("LOT_SIZE", {})
        price = filters.get("PRICE_FILTER", {})
        minimum = filters.get("MIN_NOTIONAL", {})
        ccxt_limits = market.get("limits") or {}
        amount_limits = ccxt_limits.get("amount") or {}
        cost_limits = ccxt_limits.get("cost") or {}
        constraints = OrderConstraints(
            price_tick=_decimal(price.get("tickSize")),
            quantity_step=_decimal(lot.get("stepSize")),
            minimum_quantity=_decimal(lot.get("minQty", amount_limits.get("min"))),
            minimum_notional=_decimal(minimum.get("notional", minimum.get("minNotional", cost_limits.get("min")))),
            price_precision=_precision(symbol.get("pricePrecision")),
            amount_precision=_precision(symbol.get("quantityPrecision")),
        )
        evidence = ["exchangeInfo"]
        if market:
            evidence.append("ccxt:loaded_market")
        if account is not None:
            evidence.append("account:v2:global_and_positions")
        if account_v3 is not None:
            evidence.append("account:v3:position_population_nonpermission")
        if account_config is not None:
            evidence.append("accountConfig:global")
        if symbol_configs is not None:
            evidence.append("symbolConfig:population")
        if leverage_brackets is not None:
            evidence.append("leverageBracket:population")
        if data_symbols is not None:
            evidence.append("data:observed_symbols")
        records.append(InstrumentRecord(
            instrument_id=InstrumentId("binance_usdm", MarketType.FUTURES, venue_symbol),
            asset_class=AssetClass.UNKNOWN, contract_type=symbol.get("contractType"),
            base_asset=str(symbol["baseAsset"]), quote_asset=str(symbol["quoteAsset"]),
            settlement_asset=symbol.get("marginAsset"),
            contract_multiplier=_decimal(market.get("contractSize")),
            delivery_ms=(None if symbol.get("contractType") == "PERPETUAL"
                         else _integer(symbol.get("deliveryDate"))),
            venue_listing=Presence.PRESENT,
            venue_status=str(symbol.get("status", "UNKNOWN")),
            onboard_ms=_integer(symbol.get("onboardDate")), constraints=constraints,
            shortability=Capability.UNKNOWN, account_eligibility=Eligibility.UNKNOWN,
            account_state_ref=account_scope, symbol_config=_presence(configs, venue_symbol),
            leverage_bracket=_presence(brackets, venue_symbol),
            data_availability=_presence(data_symbols, venue_symbol),
            observed_at_ms=as_of_ms, source=source, evidence=tuple(evidence),
            venue_underlying_type=symbol.get("underlyingType"),
        ))
    return RegistrySnapshot(as_of_ms=as_of_ms, account_scope=account_scope,
                            account_trading=global_state, records=tuple(records), source=source)
