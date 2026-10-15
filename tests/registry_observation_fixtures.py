"""Offline fixtures for registry metadata contracts."""
import json

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

def _body(symbols=None, server_time=1_800_000_000_123):
    info = {"timezone": "UTC", "symbols": symbols or [_symbol(), _symbol("ETHUSDT")]}
    if server_time is not None:
        info["serverTime"] = server_time
    return json.dumps(info).encode()
