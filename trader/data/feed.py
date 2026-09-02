"""Market data: ccxt OHLCV fetcher with TTL cache + validation.

Binance demo keys from .env; sandbox mode when BINANCE_DEMO is truthy.
"""
from __future__ import annotations

import logging
import threading

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
    """Fetch + cache OHLCV per (symbol, timeframe). Thread-safe via GIL ops.

    Three layers: in-memory TTL cache → local sqlite candle store
    (data/candles.db, survives restarts, incrementally appended) →
    exchange REST calls (paged past the 1000-bar/request cap).
    """

    def __init__(self, exchange=None, ttl_by_tf: dict | None = None,
                 db_path=None):
        self._ex = exchange
        #: the venue orders are actually sent to (demo, when demo is on)
        self.trade_ex = exchange
        # Market-data truth lives on the production public API even when the
        # trading venue is demo: demo klines carry simulated volume (1-4 vs a
        # production 153-997 on the same 15m bar) and closes that drift by
        # hundreds of dollars. Every volume feature, regime call and backtest
        # reads these bars, so sourcing them from demo makes "is this working
        # NOW" a question about a simulation. Universe already does this.
        # An explicitly injected exchange always wins: that is how backtests
        # and tests supply their own source.
        from ..core.config import Env
        on_demo = Env.get("BINANCE_DEMO", "true").lower() in ("1", "true", "yes")
        self.data_ex = make_exchange("futures", demo=False, with_keys=False) \
            if (on_demo and exchange is None) else None
        # cache freshness: 15m data ~2min old max; 1h ~10min; 4h ~30min
        self.ttl = ttl_by_tf or {"5m": 120, "15m": 180, "1h": 600, "4h": 1800, "1d": 7200}
        self._cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
        self._db_path = str(db_path) if db_path else None   # resolved lazily
        self._local = threading.local()                     # per-thread conns
        #: symbol -> unix ts it was last seen as non-existent on the exchange
        self._dead: dict[str, float] = {}

    @property
    def ex(self):
        """Exchange used for market data (production public when on demo)."""
        if self.data_ex is not None:
            return self.data_ex
        if self._ex is None:
            self._ex = make_exchange()
        return self._ex

    # ── persistent candle store ─────────────────────────────────────────
    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            import sqlite3
            if self._db_path is None:
                from ..core.config import ROOT
                self._db_path = str(ROOT / "data" / "candles.db")
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS candles ("
                "symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL,"
                "open REAL, high REAL, low REAL, close REAL, volume REAL,"
                "taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
            conn.commit()
            self._local.conn = conn
        return conn

    #: index of takerBuyBaseAssetVolume in a raw Binance kline row
    _TAKER_BUY_IDX = 9

    def _frame(self, raw: list) -> pd.DataFrame:
        """kline rows → validated DataFrame carrying real aggressor volume.

        Rows may arrive 6-wide (ccxt's unified fetch_ohlcv, which drops the
        column) or 12-wide (the venue's raw klines endpoint, which publishes
        it at index 9). `taker_buy` is the measured aggressor-buy base volume
        when the venue gives it and NaN when it does not.

        It used to be synthesised as volume*(close-low)/(high-low) — a pure
        function of OHLCV with no order-flow content. Against Binance's
        published figure over 3000 bars that proxy correlated +0.42, had 3x
        the dispersion, and got the DIRECTION of the imbalance wrong 33% of
        the time, while the flow analyst and the aggressor specs read it as
        if it reported who paid the spread.
        """
        if not len(raw):
            df = pd.DataFrame(columns=COLUMNS + ["taker_buy"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            return df
        width = max(len(r) for r in raw)
        rows = [list(r) + [None] * (width - len(r)) for r in raw]
        df = pd.DataFrame([r[:6] for r in rows], columns=COLUMNS)
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        for col in COLUMNS[1:]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if width > self._TAKER_BUY_IDX:
            df["taker_buy"] = pd.to_numeric(
                [r[self._TAKER_BUY_IDX] for r in rows], errors="coerce")
        else:
            df["taker_buy"] = np.nan
        # taker_buy is allowed to be absent; an incomplete OHLCV bar is not
        df = df.dropna(subset=COLUMNS).reset_index(drop=True)
        return df

    @staticmethod
    def _venue_symbol(ex, symbol: str) -> str:
        """Venue id for a unified symbol, without forcing a markets load.

        ccxt's `market()` raises "markets not loaded" on a fresh instance,
        which would send every raw-klines call down the fallback path and
        silently restore the synthetic taker_buy.
        """
        try:
            return ex.market(symbol)["id"]
        except Exception:
            return symbol.split(":")[0].replace("/", "")

    def _klines(self, symbol: str, tf: str, since: int | None = None,
                limit: int = 1000) -> list:
        """Raw kline rows, preferring the endpoint that publishes aggressor
        volume. Falls back to ccxt's unified 6-wide rows on any venue or
        error where the raw call is unavailable — taker_buy then reads NaN,
        which is the honest answer.
        """
        try:
            ex = self.ex
            params = {"symbol": self._venue_symbol(ex, symbol),
                      "interval": tf, "limit": int(limit)}
            if since is not None:
                params["startTime"] = int(since)
            getter = getattr(ex, "fapiPublicGetKlines", None) or \
                getattr(ex, "publicGetKlines", None)
            if getter is not None:
                rows = getter(params)
                if rows:
                    return rows
        except Exception as e:
            log.debug(f"raw klines unavailable for {symbol} {tf}: {e}")
        return self.ex.fetch_ohlcv(symbol, tf, since=since, limit=limit) or []

    def _store_load(self, symbol: str, tf: str, limit: int,
                    min_ts: int = 0) -> Optional[pd.DataFrame]:
        # One market, one key. ccxt names a linear perp both 'XAU/USDT' and
        # 'XAU/USDT:USDT', and keying by whatever the caller passed split the
        # same market's history in two — a backtest reading the plain form saw
        # 400 bars where 1427 existed.
        symbol = norm_symbol(symbol)
        try:
            rows = self.db.execute(
                "SELECT ts, open, high, low, close, volume, taker_buy "
                "FROM candles WHERE symbol=? AND tf=? AND ts>=? "
                "ORDER BY ts DESC LIMIT ?",
                (symbol, tf, min_ts, limit)).fetchall()
        except Exception as e:
            log.warning(f"candle store read {symbol} {tf}: {e}")
            return None
        if not rows:
            return None
        df = pd.DataFrame(rows[::-1], columns=COLUMNS + ["taker_buy"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df

    _TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000,
              "4h": 14_400_000, "1d": 86_400_000}

    def _store_save(self, symbol: str, tf: str, df: pd.DataFrame) -> None:
        symbol = norm_symbol(symbol)
        try:
            # .value/.astype(int64) are ns-based only for datetime64[ns];
            # this repo's pandas keeps ms resolution → convert explicitly
            unit = getattr(df["ts"].dt, "unit", None) or (
                "ns" if str(df["ts"].dtype).startswith("datetime64[ns]") else "ms")
            ms = df["ts"].astype("int64") // {"ns": 10 ** 6, "us": 10 ** 3}.get(unit, 1)
            self.db.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                [(symbol, tf, int(t), float(o), float(h), float(l),
                  float(c), float(v),
                  None if b is None or b != b else float(b))
                 for t, o, h, l, c, v, b in zip(
                     ms, df["open"], df["high"], df["low"],
                     df["close"], df["volume"],
                     df["taker_buy"] if "taker_buy" in df
                     else [None] * len(df))])
            self.db.commit()
        except Exception as e:
            log.warning(f"candle store write {symbol} {tf}: {e}")

    def _merge_save(self, symbol: str, tf: str,
                    stored: Optional[pd.DataFrame],
                    fresh: pd.DataFrame) -> pd.DataFrame:
        df = fresh if stored is None else pd.concat([stored, fresh]) \
            .drop_duplicates(subset="ts", keep="last") \
            .sort_values("ts").reset_index(drop=True)
        self._store_save(symbol, tf, df)
        return df

    def _fetch_paged(self, symbol: str, tf: str, limit: int) -> list:
        """Exchanges cap a single klines request (1000 on binanceusdm);
        walk backwards in pages until `limit` bars are assembled."""
        tf_ms = self._TF_MS[tf]
        now_ms = int(time.time() * 1000)
        cursor = now_ms - limit * tf_ms
        rows: list = []
        while cursor < now_ms:
            batch = self._klines(symbol, tf, since=cursor, limit=1000)
            if not batch:
                break
            rows.extend(batch)
            cursor = batch[-1][0] + tf_ms
            if len(batch) < 1000:
                break
        return rows[-limit:]

    def cached_ohlcv(self, symbol: str, tf: str = "15m",
                     limit: int = 20000) -> Optional[pd.DataFrame]:
        """Read the local candle store ONLY — never touches the exchange.

        Backtests want every bar already on disk, which is a very different
        request from 'give me fresh candles'. Routing them through
        fetch_ohlcv(limit=20000) triggers _fetch_paged and walks the REST API
        backwards for every symbol, turning an offline gauntlet into minutes
        of network I/O.
        """
        return self._store_load(symbol, tf, limit)

    def fetch_ohlcv(self, symbol: str, tf: str = "15m",
                    limit: int = 400, force: bool = False,
                    min_bars: int = 30) -> Optional[pd.DataFrame]:
        ck = (symbol, tf)
        tf_ms = self._TF_MS.get(tf, 900_000)
        now_ms = int(time.time() * 1000)

        if self.is_dead(symbol):
            # delisted / never-listed symbol: serve whatever is stored and do
            # not touch the exchange. Three such symbols (DRAM, BZ, MRVL) were
            # producing 31,566 of 32,569 log warnings — 97% of all noise — by
            # being refetched every single cycle forever.
            stored = self._store_load(symbol, tf, limit)
            if stored is not None and len(stored) >= min_bars:
                return stored
            return None

        def finish(df: pd.DataFrame) -> pd.DataFrame:
            self._cache[ck] = (time.time(), df)
            return df

        hit = self._cache.get(ck)
        if hit and not force and time.time() - hit[0] < self.ttl.get(tf, 300):
            return hit[1]

        stored = self._store_load(symbol, tf, limit)
        have_full = stored is not None and len(stored) >= limit
        fresh_tail = (stored is not None and
                      now_ms - self._last_ms(stored) <=
                      self.ttl.get(tf, 300) * 1000)
        if have_full and fresh_tail and not force:
            return finish(stored)

        try:
            if not force and stored is not None and len(stored) > 0:
                # extend/refresh whatever is stored instead of redownloading
                last = self._last_ms(stored)
                if len(stored) < limit:
                    raw = (self._fetch_paged(symbol, tf, limit)
                           if limit > 1000 else
                           self._klines(
                               symbol, tf,
                               since=int(last) + tf_ms -
                               (limit - len(stored)) * tf_ms, limit=1000))
                else:   # full window stored → only the tail can be stale
                    missing = (now_ms - last) // tf_ms
                    raw = (self._klines(symbol, tf,
                                        since=int(last) + tf_ms,
                                        limit=1000)
                           if missing < 950 else
                           self._fetch_paged(symbol, tf, limit))
                new = self._frame(raw or [])
                if new.empty:
                    return finish(stored)
                return finish(self._merge_save(symbol, tf, stored, new))
            # cold symbol or force refresh
            raw = (self._fetch_paged(symbol, tf, limit) if limit > 1000
                   else self._klines(symbol, tf, limit=limit))
            if not raw or len(raw) < min_bars:
                return hit[1] if hit else (stored if stored is not None
                                           and len(stored) >= min_bars else None)
            new = self._frame(raw)
            return finish(self._merge_save(symbol, tf, stored, new))
        except Exception as e:
            if self._note_dead(symbol, e):
                log.warning(f"ohlcv {symbol} {tf}: {e} — symbol marked dead, "
                            f"suppressing further fetches")
            else:
                log.warning(f"ohlcv {symbol} {tf}: {e}")
            if stored is not None and len(stored) >= min_bars:
                return finish(stored)
            return hit[1] if hit else None

    # ── dead-symbol negative cache ──────────────────────────────────────
    #: exchange messages that mean "this market will never exist"
    _DEAD_PATTERNS = ("does not have market symbol", "symbol not found",
                      "invalid symbol", "unknown symbol")
    #: retry a dead symbol only after this long (a relisting is possible)
    DEAD_TTL_S = 6 * 3600

    def is_dead(self, symbol: str) -> bool:
        ts = self._dead.get(symbol)
        if ts is None:
            return False
        if time.time() - ts > self.DEAD_TTL_S:
            self._dead.pop(symbol, None)     # give it another chance
            return False
        return True

    def _note_dead(self, symbol: str, exc: Exception) -> bool:
        """Record a permanent-looking symbol failure. True if newly marked."""
        msg = str(exc).lower()
        if not any(p in msg for p in self._DEAD_PATTERNS):
            return False
        first = symbol not in self._dead
        self._dead[symbol] = time.time()
        return first

    def dead_symbols(self) -> list[str]:
        return sorted(s for s in self._dead if self.is_dead(s))

    @staticmethod
    def _last_ms(df: pd.DataFrame) -> int:
        v = df["ts"].iloc[-1]
        return int(v.value // 1_000_000) if hasattr(v, "value") else int(v)

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


#: symbols proven untradeable at runtime; shared process-wide so a rejection
#: in the executor immediately removes the symbol from the next scan.
_RUNTIME_BLACKLIST: set[str] = set()

#: exchange errors that mean "this account may never trade this symbol"
_UNTRADEABLE_MARKERS = ("-4411", "tradfi-perps", "tradfi perps")


def mark_untradeable(symbol: str, reason: str = "") -> bool:
    """Blacklist a symbol for the life of the process. True if newly added."""
    first = symbol not in _RUNTIME_BLACKLIST
    _RUNTIME_BLACKLIST.add(symbol)
    if first:
        log.warning(f"universe: {symbol} marked untradeable ({reason[:120]})")
    return first


def is_untradeable_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _UNTRADEABLE_MARKERS)


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
        # symbols the venue lists but refuses to trade for this account
        # (e.g. tokenized equities: "-4411 Please sign TradFi-Perps
        # agreement"). Learned at runtime instead of hand-edited into
        # config.yaml after each rejection.
        self.blacklist |= set(_RUNTIME_BLACKLIST)
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
        #: symbol -> last seen 24h quote volume, for strategy liquidity floors
        self._volumes: dict[str, float] = {}

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

    def volumes(self) -> dict:
        """{symbol: 24h quote volume} as of the last scan.

        A spec's `min_volume_usdt` needs a reading to test against; a symbol
        absent here has no reading and must not pass a liquidity floor.
        """
        if not self._volumes and self.enabled:
            self._rescan()
        out = dict(self._volumes)
        for m in self.majors:                # majors are scanned regardless
            out.setdefault(m, float("inf"))
        return out

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
            self._volumes[sym] = quote_vol
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
