"""Market data: ccxt OHLCV fetcher with TTL cache + validation.

Binance demo keys from .env; sandbox mode when BINANCE_DEMO is truthy.
"""
from __future__ import annotations

import logging

import numpy as np
import time

from ..core.types import norm_symbol
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


def make_exchange(market_type: str = "futures", demo: bool | None = None,
                  with_keys: bool = True):
    """demo=None → follow .env · demo=True/False forces mode.
    with_keys=False → clean public instance (universe scans use production
    public data even when trading on demo: demo volumes are simulated)."""
    import ccxt
    from ..core.config import Env

    key, secret = Env.binance_keys() if with_keys else ("", "")
    klass = ccxt.binanceusdm if market_type == "futures" else ccxt.binance
    ex = klass({
        "apiKey": key, "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future" if market_type == "futures" else "spot"},
    })
    use_demo = (Env.get("BINANCE_DEMO", "true").lower() in ("1", "true", "yes")
                if demo is None else demo)
    if use_demo:
        # Binance Demo Trading platform (successor of the deprecated testnet).
        # Futures: demo-fapi.binance.com · Spot: demo-api.binance.com
        if hasattr(ex, "enable_demo_trading"):
            ex.enable_demo_trading(True)
        else:   # legacy ccxt
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
                    limit: int = 400, force: bool = False,
                    min_bars: int = 30) -> Optional[pd.DataFrame]:
        ck = (symbol, tf)
        hit = self._cache.get(ck)
        if hit and not force and time.time() - hit[0] < self.ttl.get(tf, 300):
            return hit[1]
        try:
            raw = self.ex.fetch_ohlcv(symbol, tf, limit=limit)
        except Exception as e:
            log.warning(f"ohlcv {symbol} {tf}: {e}")
            return hit[1] if hit else None
        if not raw or len(raw) < min_bars:
            return hit[1] if hit else None
        df = pd.DataFrame(raw, columns=COLUMNS)
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        for col in COLUMNS[1:]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        # estimated aggressor buy volume (candle-position proxy; ccxt omits col 9)
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        df["taker_buy"] = (df["volume"] * (df["close"] - df["low"]) / rng).fillna(
            df["volume"] * 0.5)
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
        self.min_age_days = float(scan.get("min_age_days", 90))
        self.rescan_hours = float(scan.get("rescan_hours", 4))
        self.blacklist = set(scan.get("blacklist", [])) | {
            "USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "BUSD/USDT",
            "USDE/USDT", "BFUSD/USDT"}
        self._ex = exchange
        # market-data truth lives on production public API even when the
        # trading venue is demo (demo volumes/listings are simulated)
        from ..core.config import Env
        on_demo = Env.get("BINANCE_DEMO", "true").lower() in ("1", "true", "yes")
        self.data_ex = make_exchange(
            "futures", demo=False, with_keys=False) if on_demo else (exchange or None)
        self._last_scan = 0.0
        self._alts: list[str] = []
        self._listing_cache: dict[str, float] = {}   # symbol -> first-candle ts (ms)

    @property
    def ex(self):
        """Exchange used for market-data scans (production public on demo)."""
        if self.data_ex is not None:
            return self.data_ex
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
        for sym_raw, t in tickers.items():
            sym = norm_symbol(sym_raw)
            if not sym.endswith("/USDT") or sym in self.blacklist:
                continue
            if sym in self.majors:
                continue
            quote_vol = float(t.get("quoteVolume") or 0)
            last = float(t.get("last") or 0)
            if quote_vol < self.min_vol or last < self.min_price:
                continue
            if not self._old_enough(sym):
                continue
            scored.append((quote_vol, sym))
        scored.sort(reverse=True)
        self._alts = [s for _, s in scored[:self.top_n]]
        self._last_scan = time.time()
        log.info(f"universe: {len(self.symbols())} symbols "
                 f"(majors {len(self.majors)} + alts {len(self._alts)})")

    def _old_enough(self, symbol: str) -> bool:
        """Listing age ≥ min_age_days via first 1d candle (cached)."""
        if symbol in self._listing_cache:
            first_ms = self._listing_cache[symbol]
        else:
            try:
                raw = self.ex.fetch_ohlcv(symbol, "1d", since=0, limit=1)
                first_ms = raw[0][0] if raw else 0
            except Exception:
                first_ms = 0
            self._listing_cache[symbol] = first_ms
        if not first_ms or first_ms > time.time() * 1000 - 86_400_000 * 5:
            # no history, or "first" candle is recent (since=0 unsupported)
            return False
        age_days = (time.time() * 1000 - first_ms) / 86_400_000
        return age_days >= self.min_age_days
