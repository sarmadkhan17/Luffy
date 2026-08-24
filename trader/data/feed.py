"""Market data: ccxt OHLCV fetcher with TTL cache + validation.

Binance demo keys from .env; sandbox mode when BINANCE_DEMO is truthy.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def make_exchange(market_type: str = "futures"):
    import ccxt
    from ..core.config import Env

    key, secret = Env.binance_keys()
    klass = ccxt.binanceusdm if market_type == "futures" else ccxt.binance
    ex = klass({
        "apiKey": key, "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future" if market_type == "futures" else "spot"},
    })
    if Env.get("BINANCE_DEMO", "true").lower() in ("1", "true", "yes"):
        ex.set_sandbox_mode(True)
    return ex


class DataFeed:
    """Fetch + cache OHLCV per (symbol, timeframe). Thread-safe via GIL ops."""

    def __init__(self, exchange=None, ttl_by_tf: dict | None = None):
        self._ex = exchange
        # cache freshness: 15m data ~2min old max; 1h ~10min; 4h ~30min
        self.ttl = ttl_by_tf or {"5m": 120, "15m": 180, "1h": 600, "4h": 1800, "1d": 7200}
        self._cache: dict[tuple, tuple[float, pd.DataFrame]] = {}

    @property
    def ex(self):
        if self._ex is None:
            self._ex = make_exchange()
        return self._ex

    def fetch_ohlcv(self, symbol: str, tf: str = "15m",
                    limit: int = 400, force: bool = False) -> Optional[pd.DataFrame]:
        ck = (symbol, tf)
        hit = self._cache.get(ck)
        if hit and not force and time.time() - hit[0] < self.ttl.get(tf, 300):
            return hit[1]
        try:
            raw = self.ex.fetch_ohlcv(symbol, tf, limit=limit)
        except Exception as e:
            log.warning(f"ohlcv {symbol} {tf}: {e}")
            return hit[1] if hit else None
        if not raw or len(raw) < 30:
            return hit[1] if hit else None
        df = pd.DataFrame(raw, columns=COLUMNS)
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        for col in COLUMNS[1:]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        self._cache[ck] = (time.time(), df)
        return df

    def fetch_multi(self, symbol: str, tfs: list[str], limit: int = 400) -> dict[str, pd.DataFrame]:
        out = {}
        for tf in tfs:
            df = self.fetch_ohlcv(symbol, tf, limit)
            if df is not None:
                out[tf] = df
        return out

    def price(self, symbol: str) -> float | None:
        try:
            t = self.ex.fetch_ticker(symbol)
            return float(t.get("last") or t.get("close") or 0) or None
        except Exception as e:
            log.warning(f"price {symbol}: {e}")
            return None


class Universe:
    """Majors always included + top-N alts by quote volume, rescanned periodically."""

    def __init__(self, cfg: dict, exchange=None):
        self.cfg = cfg["universe"]
        self.majors = list(self.cfg["majors"])
        scan = self.cfg.get("auto_scan", {})
        self.enabled = bool(scan.get("enabled", True))
        self.top_n = int(scan.get("top_n", 12))
        self.min_vol = float(scan.get("min_volume_usdt", 1e8))
        self.min_price = float(scan.get("min_price", 0.5))
        self.rescan_hours = float(scan.get("rescan_hours", 4))
        self.blacklist = set(scan.get("blacklist", [])) | {
            "USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "BUSD/USDT"}
        self._ex = exchange
        self._last_scan = 0.0
        self._alts: list[str] = []

    @property
    def ex(self):
        if self._ex is None:
            self._ex = make_exchange()
        return self._ex

    def symbols(self) -> list[str]:
        if self.enabled and time.time() - self._last_scan > self.rescan_hours * 3600:
            self._rescan()
        return self.majors + [s for s in self._alts if s not in self.majors]

    def _rescan(self) -> None:
        try:
            tickers = self.ex.fetch_tickers() or {}
        except Exception as e:
            log.warning(f"universe rescan failed: {e}")
            self._last_scan = time.time()
            return
        scored = []
        for sym, t in tickers.items():
            if not sym.endswith("/USDT") or ":" in sym or sym in self.blacklist:
                continue
            if sym in self.majors:
                continue
            quote_vol = float(t.get("quoteVolume") or 0)
            last = float(t.get("last") or 0)
            if quote_vol >= self.min_vol and last >= self.min_price:
                scored.append((quote_vol, sym))
        scored.sort(reverse=True)
        self._alts = [s for _, s in scored[:self.top_n]]
        self._last_scan = time.time()
        log.info(f"universe: {len(self.symbols())} symbols "
                 f"(majors {len(self.majors)} + alts {len(self._alts)})")
