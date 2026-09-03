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
SAPI = "https://api.binance.com"      # spot — the other half of the basis
#: `ls_ratio` is Binance's topLongShortPositionRatio (what the recorder
#: polls); `ls_account_ratio` is globalLongShortAccountRatio, which only
#: Coinalyze serves deeply. They are anti-correlated (-0.64 measured), so
#: they are two series and never one.
SERIES = ("funding", "oi", "taker_ratio", "ls_ratio", "ls_account_ratio",
          "basis")

#: how many 1000-row klines pages `basis_history` may walk. At 1h that is
#: 41.6 days a page, so 96 pages reach back just under 11 years — deeper
#: than any perpetual listed on the venue.
_MAX_BASIS_PAGES = 96

#: milliseconds per kline interval, for paging basis
_PERIOD_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000,
              "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
              "1d": 86_400_000}
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
    def _get(self, path: str, params: dict, base: str = FAPI) -> list:
        try:
            r = requests.get(f"{base}{path}", params=params, headers=UA,
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

    def funding(self, symbol: str, limit: int = 1000,
                period: str | None = None) -> pd.DataFrame:
        """Most recent funding settlements. `period` is accepted and ignored
        so the recorder can call every fetcher uniformly."""
        return self._parse_funding(self._get(
            "/fapi/v1/fundingRate",
            {"symbol": to_binance(symbol), "limit": min(limit, 1000)}))

    def funding_history(self, symbol: str, years: float = 4.0,
                        delay: float = 0.25,
                        since_ms: int | None = None) -> pd.DataFrame:
        """Page funding all the way back.

        A single call caps at 1000 rows, which at an 8-hour settlement is
        under a year. The candle store now holds 5 years at 4h, so without
        paging every funding spec is refused for coverage — the data has to
        reach as far as the frame it is tested against, not the other way
        round.

        `since_ms` is that frame's own floor, supplied per symbol by the
        caller that knows how far the candles go. `years` is only the
        fallback for a caller that does not: it was 4.0 against a 5-year 4h
        frame, so the oldest fifth of every backtest paid the flat
        conservative rate — a cost assumption wearing a measurement's
        clothes.
        """
        import time as _t
        now_ms = int(_t.time() * 1000)
        floor_ms = (int(since_ms) if since_ms
                    else now_ms - int(years * 365.25 * 86400 * 1000))
        sym = to_binance(symbol)
        # Page FORWARD. Given startTime/endTime Binance returns the EARLIEST
        # rows in the window, so walking endTime backwards just re-reads the
        # oldest page and stops one call in.
        chunks, cursor = [], floor_ms
        for _ in range(40):                  # hard bound; 40k rows is ~36 years
            raw = self._get("/fapi/v1/fundingRate",
                            {"symbol": sym, "limit": 1000,
                             "startTime": cursor})
            if not raw:
                break
            df = self._parse_funding(raw)
            if df.empty:
                break
            chunks.append(df)
            newest = int(pd.to_datetime(df["ts"], utc=True).max().timestamp() * 1000)
            if newest <= cursor:             # no progress — stop, do not spin
                break
            cursor = newest + 1
            if len(raw) < 1000 or cursor >= now_ms:
                break
            _t.sleep(delay)
        if not chunks:
            return self._frame([])
        out = pd.concat(chunks).drop_duplicates(subset="ts") \
                .sort_values("ts").reset_index(drop=True)
        return out

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

    # ── basis ────────────────────────────────────────────────────────────
    #: Binance caps a klines page at 1500 rows on both venues.
    _KLINE_PAGE = 1000

    def _klines(self, base: str, path: str, symbol: str, interval: str,
                start_ms: int, limit: int) -> list:
        """[(close_ms, close_price)] for one page."""
        raw = self._get(path, {"symbol": symbol, "interval": interval,
                               "startTime": start_ms, "limit": limit},
                        base=base)
        out = []
        for k in raw:
            try:
                out.append((int(k[6]), float(k[4])))   # closeTime, close
            except (IndexError, TypeError, ValueError):
                continue
        return out

    def basis(self, symbol: str, period: str = "1h",
              limit: int = 500) -> pd.DataFrame:
        """Recent spot-perp basis: (perp - spot) / spot, per closed bar.

        Unlike OI and the ratio series, nothing here is capped at 30 days —
        both klines endpoints serve years. `basis` was registered as a
        feature from the start and no fetcher ever existed, so every basis
        spec reported UNTESTED forever. This is that fetcher.
        """
        import time as _t
        span_ms = _PERIOD_MS.get(period, 3600_000) * int(limit)
        start = int(_t.time() * 1000) - span_ms
        return self._basis_between(symbol, period, start)

    def _basis_between(self, symbol: str, period: str,
                       start_ms: int) -> pd.DataFrame:
        sym = to_binance(symbol)
        perp = dict(self._klines(FAPI, "/fapi/v1/klines", sym, period,
                                 start_ms, self._KLINE_PAGE))
        spot = dict(self._klines(SAPI, "/api/v3/klines", sym, period,
                                 start_ms, self._KLINE_PAGE))
        # inner join on close time: a bar missing on either venue has no basis
        pairs = [(ts, (perp[ts] - spot[ts]) / spot[ts])
                 for ts in sorted(perp.keys() & spot.keys())
                 if spot[ts] > 0]
        return self._frame(pairs)

    def basis_history(self, symbol: str, years: float = 2.0,
                      period: str = "1h", delay: float = 0.25,
                      since_ms: int | None = None) -> pd.DataFrame:
        """Page basis back to `since_ms`, or `years` when nobody says.

        Both venues serve deep klines, so this is the one 30-day-capped
        series that is not actually capped — the only thing that ever
        limited it was the default.
        """
        import time as _t
        now_ms = int(_t.time() * 1000)
        step = _PERIOD_MS.get(period, 3600_000) * self._KLINE_PAGE
        cursor = (int(since_ms) if since_ms
                  else now_ms - int(years * 365.25 * 86400 * 1000))
        chunks = []
        for _ in range(_MAX_BASIS_PAGES):
            df = self._basis_between(symbol, period, cursor)
            if len(df):
                chunks.append(df)
            cursor += step
            if cursor >= now_ms:
                break
            if delay:
                _t.sleep(delay)
        if not chunks:
            return self._frame([])
        return pd.concat(chunks).drop_duplicates(subset="ts") \
                 .sort_values("ts").reset_index(drop=True)

    # ── recorder ─────────────────────────────────────────────────────────
    _SERIES_FETCHERS = (("funding", "funding"), ("oi", "open_interest"),
                        ("taker_ratio", "taker_ratio"),
                        ("ls_ratio", "ls_ratio"), ("basis", "basis"))

    #: coarser periods pull deeper history. Measured against the live API on
    #: 2026-08-31: at 15m the 500-row cap reaches back only 5 days, at 1h it
    #: reaches 20, and at 4h/1d it hits Binance's hard 30-day retention wall.
    #: So a backfill pass makes OI/taker/long-short usable over ~30 days
    #: IMMEDIATELY instead of after two months of forward recording.
    BACKFILL_PERIODS = ("4h", "1h")

    def backfill(self, symbols: list, delay: float = 0.3,
                 since: dict | None = None) -> dict:
        """Seed deeper history for the 30-day-window series.

        Mixed granularity in one series is fine and intended: features align
        by forward-filling the last observation before each bar, so the store
        ends up coarse in the past and fine near the present.

        `since` is {symbol: epoch_ms} — the earliest bar the candle store
        holds for that market. One floor per symbol, because a coin listed in
        2024 has no 2021 frame to reach and asking for one only burns pages
        against an empty window.
        """
        counts: dict = {}
        since = since or {}
        for sym in symbols:
            floor = since.get(sym)
            try:
                fh = self.funding_history(sym, delay=delay, since_ms=floor)
                if fh is not None and len(fh):
                    self.save(sym, "funding", fh)
                    counts["funding@history"] = \
                        counts.get("funding@history", 0) + len(fh)
            except Exception as e:
                log.warning(f"derivs backfill {sym}/funding history: {e}")
            try:
                bh = self.basis_history(sym, delay=delay, since_ms=floor)
                if bh is not None and len(bh):
                    self.save(sym, "basis", bh)
                    counts["basis@history"] = \
                        counts.get("basis@history", 0) + len(bh)
            except Exception as e:
                log.warning(f"derivs backfill {sym}/basis history: {e}")
            for series, method in self._SERIES_FETCHERS:
                if series in ("funding", "basis"):
                    continue            # both have real history above
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
