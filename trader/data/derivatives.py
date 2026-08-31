"""Derivatives and flow data — the inputs that make non-obvious strategies
possible.

OHLCV can only yield strategies that have been invented a million times. The
crypto-native mechanisms — funding exhaustion, open-interest divergence, basis
dislocation, aggressor imbalance — need the data below, none of which the
DataFeed ingests today.

Retention is asymmetric and it drives the design:

  funding   /fapi/v1/fundingRate                    years  -> backtestable now
  basis     spot vs perp klines                     years  -> backtestable now
  oi        /futures/data/openInterestHist          ~30 d  -> record forward
  taker     /futures/data/takerlongshortRatio       ~30 d  -> record forward
  ls_ratio  /futures/data/topLongShortPositionRatio ~30 d  -> record forward

Because the ~30-day series cannot be backfilled, the recorder runs from day
one — every day without it is a day of history permanently lost.
"""
from __future__ import annotations

import logging
import threading
import time

import pandas as pd
import requests

log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
SERIES = ("funding", "oi", "taker_ratio", "ls_ratio", "basis")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}


def to_binance(symbol: str) -> str:
    """'BTC/USDT' or 'BTC/USDT:USDT' -> 'BTCUSDT'."""
    return symbol.split(":")[0].replace("/", "")


class DerivFeed:
    def __init__(self, db_path=None, timeout: int = 15):
        self._db_path = str(db_path) if db_path else None
        self._local = threading.local()
        self.timeout = timeout

    # ── store ────────────────────────────────────────────────────────────
    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            import sqlite3
            if self._db_path is None:
                from ..core.config import ROOT
                self._db_path = str(ROOT / "data" / "derivs.db")
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS derivs ("
                "symbol TEXT NOT NULL, series TEXT NOT NULL, "
                "ts INTEGER NOT NULL, value REAL, "
                "PRIMARY KEY (symbol, series, ts))")
            conn.commit()
            self._local.conn = conn
        return conn

    @staticmethod
    def _to_ms(ts: pd.Series) -> pd.Series:
        """datetime64 -> epoch milliseconds, resolution-aware.

        This repo's pandas yields datetime64[ms] here, so a blind
        `.astype("int64") // 10**6` divides milliseconds by a million and
        lands every observation in 1970 — after which align() forward-fills a
        single constant across the whole frame and every derivative feature
        silently returns garbage. feed.py:_store_save documents the same trap.
        """
        ts = pd.to_datetime(ts, utc=True)
        unit = getattr(ts.dt, "unit", None) or (
            "ns" if str(ts.dtype).startswith("datetime64[ns]") else "ms")
        return ts.astype("int64") // {"ns": 10 ** 6, "us": 10 ** 3}.get(unit, 1)

    def save(self, symbol: str, series: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        ms = self._to_ms(df["ts"])
        try:
            self.db.executemany(
                "INSERT OR REPLACE INTO derivs VALUES (?,?,?,?)",
                [(symbol, series, int(t), float(v))
                 for t, v in zip(ms, df["value"])])
            self.db.commit()
        except Exception as e:
            log.warning(f"derivs write {symbol} {series}: {e}")

    def load(self, symbol: str, series: str,
             limit: int = 200000) -> pd.DataFrame | None:
        try:
            rows = self.db.execute(
                "SELECT ts, value FROM derivs WHERE symbol=? AND series=? "
                "ORDER BY ts DESC LIMIT ?", (symbol, series, limit)).fetchall()
        except Exception as e:
            log.warning(f"derivs read {symbol} {series}: {e}")
            return None
        if not rows:
            return None
        df = pd.DataFrame(rows[::-1], columns=["ts", "value"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df

    def coverage(self) -> dict:
        """{(symbol, series): (rows, first_ts, last_ts)} — how much history
        has actually accumulated. The recorder's whole purpose."""
        out = {}
        for sym, ser, n, lo, hi in self.db.execute(
                "SELECT symbol, series, COUNT(*), MIN(ts), MAX(ts) "
                "FROM derivs GROUP BY symbol, series"):
            out[(sym, ser)] = (n, lo, hi)
        return out

    # ── fetch ────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict) -> list:
        try:
            r = requests.get(f"{FAPI}{path}", params=params, headers=UA,
                             timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            log.warning(f"derivs fetch {path} {params.get('symbol')}: {e}")
            return []

    @staticmethod
    def _frame(pairs: list) -> pd.DataFrame:
        df = pd.DataFrame(pairs, columns=["ts", "value"])
        if df.empty:
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            return df
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.dropna().sort_values("ts").reset_index(drop=True)

    def _parse_funding(self, raw: list) -> pd.DataFrame:
        return self._frame([(int(r["fundingTime"]), float(r["fundingRate"]))
                            for r in raw])

    def _parse_oi(self, raw: list) -> pd.DataFrame:
        return self._frame([(int(r["timestamp"]), float(r["sumOpenInterest"]))
                            for r in raw])

    def _parse_ratio(self, raw: list, key: str) -> pd.DataFrame:
        return self._frame([(int(r["timestamp"]), float(r[key])) for r in raw])

    def funding(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
        return self._parse_funding(self._get(
            "/fapi/v1/fundingRate",
            {"symbol": to_binance(symbol), "limit": min(limit, 1000)}))

    def open_interest(self, symbol: str, period: str = "15m",
                      limit: int = 500) -> pd.DataFrame:
        return self._parse_oi(self._get(
            "/futures/data/openInterestHist",
            {"symbol": to_binance(symbol), "period": period,
             "limit": min(limit, 500)}))

    def taker_ratio(self, symbol: str, period: str = "15m",
                    limit: int = 500) -> pd.DataFrame:
        return self._parse_ratio(self._get(
            "/futures/data/takerlongshortRatio",
            {"symbol": to_binance(symbol), "period": period,
             "limit": min(limit, 500)}), "buySellRatio")

    def ls_ratio(self, symbol: str, period: str = "15m", limit: int = 500,
                 kind: str = "top") -> pd.DataFrame:
        path = ("/futures/data/topLongShortPositionRatio" if kind == "top"
                else "/futures/data/globalLongShortAccountRatio")
        return self._parse_ratio(self._get(
            path, {"symbol": to_binance(symbol), "period": period,
                   "limit": min(limit, 500)}), "longShortRatio")

    # ── recorder ─────────────────────────────────────────────────────────
    _SERIES_FETCHERS = (("funding", "funding"), ("oi", "open_interest"),
                        ("taker_ratio", "taker_ratio"),
                        ("ls_ratio", "ls_ratio"))

    #: coarser periods pull deeper history. Measured against the live API on
    #: 2026-08-31: at 15m the 500-row cap reaches back only 5 days, at 1h it
    #: reaches 20, and at 4h/1d it hits Binance's hard 30-day retention wall.
    #: So a backfill pass makes OI/taker/long-short usable over ~30 days
    #: IMMEDIATELY instead of after two months of forward recording.
    BACKFILL_PERIODS = ("4h", "1h")

    def backfill(self, symbols: list, delay: float = 0.3) -> dict:
        """Seed deeper history for the 30-day-window series.

        Mixed granularity in one series is fine and intended: features align
        by forward-filling the last observation before each bar, so the store
        ends up coarse in the past and fine near the present.
        """
        counts: dict = {}
        for sym in symbols:
            for series, method in self._SERIES_FETCHERS:
                if series == "funding":
                    continue            # already has years at native cadence
                for period in self.BACKFILL_PERIODS:
                    try:
                        df = getattr(self, method)(sym, period=period)
                    except Exception as e:
                        log.warning(f"derivs backfill {sym}/{series}"
                                    f"@{period}: {e}")
                        continue
                    if df is not None and len(df):
                        self.save(sym, series, df)
                        counts[f"{series}@{period}"] = \
                            counts.get(f"{series}@{period}", 0) + len(df)
                    if delay:
                        time.sleep(delay)
        return counts

    def record_all(self, symbols: list, delay: float = 0.3) -> dict:
        """One pass over every symbol and series. Rows already stored are
        replaced, so this is safe to run at any cadence and safe to retry."""
        counts: dict = {}
        for sym in symbols:
            for series, method in self._SERIES_FETCHERS:
                try:
                    df = getattr(self, method)(sym)
                except Exception as e:
                    log.warning(f"derivs record {sym}/{series}: {e}")
                    continue
                if df is not None and len(df):
                    self.save(sym, series, df)
                    counts[series] = counts.get(series, 0) + len(df)
                if delay:
                    time.sleep(delay)          # public endpoints are rate-limited
        return counts
