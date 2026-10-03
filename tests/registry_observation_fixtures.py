"""Offline fixtures for current production registry observation contracts."""
import time
import json
import numpy as np
import pandas as pd
from trader.observability import supplemental as S
TF=14_400_000

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

class FakeEx:
    """Newest-first venue on the absolute 4h grid, with a forming bar."""

    def __init__(self, total=60, lag=0, holes=(), forming=True, fail=None):
        self.calls = []
        self.fail = fail
        now = int(time.time() * 1000)
        newest = now // TF * TF - lag * TF          # open of the forming bar
        opens = [newest - i * TF for i in range(total)][::-1]
        if not forming:
            opens = opens[:-1]
        self.rows = [[t, 100 + i % 5, 101 + i % 5, 99 + i % 5, 100 + i % 5 + 0.5 * np.sin(i), 10 + i % 7]
                     for i, t in enumerate(opens) if t not in set(holes)]
        self.newest = newest

    def fetch_ohlcv(self, symbol, tf, limit=None, min_bars=1):
        self.calls.append((symbol, tf, None, limit))
        if self.fail:
            raise self.fail
        rows = self.rows[-(limit or 1000):]
        return pd.DataFrame({"ts": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True),
                             "open": [r[1] for r in rows], "high": [r[2] for r in rows],
                             "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                             "volume": [r[5] for r in rows]})

def scan_frames(symbols, now=None):
    now = now or int(time.time()*1000)
    anchor = now // TF * TF
    out = {}
    for j, sym in enumerate(symbols):
        c = 100 * np.exp(np.cumsum(np.sin(np.arange(30) + j) * .01))
        out[sym] = {'4h': pd.DataFrame({
            'ts': pd.to_datetime([anchor - (30 - i) * TF for i in range(30)], unit='ms', utc=True),
            'open': c, 'high': c * 1.01, 'low': c * .99, 'close': c,
            'volume': 100 + np.arange(30) % 7})}
    return out
