"""Reference markets: what `ref(key, expr)` can read, and where it lives.

A reference is a market-wide series the book does not trade but reads —
the S&P, the dollar, gold, the 10y yield, VIX, oil, BTC dominance, an
equal-weight alt index, total stablecoin supply, CoinGecko's own dominance
figures. Each declares:

- `tf`            its native bar ("1h", "4h", "1d", or "snap" for a point
                  snapshot);
- `close_after_ms` how long after its stamp the value is FINAL and known.
                  Yahoo stamps an S&P daily bar at the 13:30 UTC open; the
                  close exists at 20:00. Aligning on the stamp would let every
                  S&P signal see 6.5 hours ahead, so every daily bar here is
                  known one full day after its stamp — conservative, and it
                  can never peek;
- `max_stale_ms`  how long after its last known bar a silent series still
                  counts as live. Staleness catches a DEAD FEED, not a closed
                  market: the S&P's Friday close was known all weekend.

Stored in their own `refs` table of data/candles.db. Not `candles`:
`norm_symbol` splits on ':' (`ref:spx` would collapse to `ref`), and every
tool that walks candle symbols would try to audit the S&P against Binance.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core.config import ROOT
from ..core.types import TF_MS, closed_bars

HOUR = 3_600_000
DAY = 86_400_000


@dataclass(frozen=True)
class Ref:
    key: str
    source: str            # yahoo | binance | defillama | coingecko | computed
    tf: str                # native bar: 1h | 4h | 1d | snap
    symbol: str            # the source's own identifier (ticker, pair, field)
    close_after_ms: int    # stamp -> the moment the value is final and known
    max_stale_ms: int      # silent longer than this after known -> NaN
    close_only: bool = False


def _yahoo(key: str, ticker: str) -> list:
    return [Ref(key, "yahoo", "1d", ticker, DAY, 100 * HOUR),
            Ref(f"{key}_1h", "yahoo", "1h", ticker, HOUR, 80 * HOUR)]


REFS: dict[str, Ref] = {r.key: r for r in [
    *_yahoo("spx", "^GSPC"), *_yahoo("dxy", "DX-Y.NYB"),
    *_yahoo("gold", "GC=F"), *_yahoo("us10y", "^TNX"),
    *_yahoo("vix", "^VIX"), *_yahoo("oil", "CL=F"),
    Ref("btcdom", "binance", "4h", "BTCDOM/USDT:USDT", 4 * HOUR, 12 * HOUR),
    Ref("alts", "computed", "4h", "", 4 * HOUR, 12 * HOUR, close_only=True),
    Ref("stables", "defillama", "1d", "", DAY, 72 * HOUR, close_only=True),
    Ref("cg_btc_d", "coingecko", "snap", "btc", 0, 12 * HOUR, close_only=True),
    Ref("cg_usdt_d", "coingecko", "snap", "usdt", 0, 12 * HOUR,
        close_only=True),
    Ref("cg_total", "coingecko", "snap", "total", 0, 12 * HOUR,
        close_only=True),
    Ref("cg_total2", "coingecko", "snap", "total2", 0, 12 * HOUR,
        close_only=True),
]}


def to_ms(ts) -> np.ndarray:
    """Epoch milliseconds of a timestamp Series, whatever its resolution."""
    t = pd.to_datetime(ts, utc=True).dt.tz_localize(None)
    return np.asarray(t, dtype="datetime64[ms]").astype("int64")


def _num(v):
    return None if v is None or v != v else float(v)


class RefStore:
    """Closed reference bars, one row per (key, ts)."""

    def __init__(self, db_path=None):
        self._db_path = str(db_path) if db_path else \
            str(ROOT / "data" / "candles.db")
        self._local = threading.local()

    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS refs (key TEXT NOT NULL, "
                "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, "
                "close REAL, volume REAL, PRIMARY KEY (key, ts))")
            conn.commit()
            self._local.conn = conn
        return conn

    def save(self, key: str, df, now_ms: int | None = None) -> int:
        """Persist CLOSED bars with a measured close. Returns rows written."""
        ref = REFS[key]
        if df is None or not len(df):
            return 0
        if ref.tf in TF_MS:
            df = closed_bars(df, ref.tf, now_ms)
            if df is None or not len(df):
                return 0
        n = len(df)

        def col(c):
            return df[c].tolist() if c in df else [None] * n

        rows = [(key, int(t), _num(o), _num(h), _num(lo), _num(c), _num(v))
                for t, o, h, lo, c, v in zip(
                    to_ms(df["ts"]), col("open"), col("high"), col("low"),
                    col("close"), col("volume"))]
        rows = [r for r in rows if r[5] is not None]  # no close, no bar
        if rows:
            self.db.executemany(
                "INSERT OR REPLACE INTO refs VALUES (?,?,?,?,?,?,?)", rows)
            self.db.commit()
        return len(rows)

    def load(self, key: str, since_ms: int = 0):
        rows = self.db.execute(
            "SELECT ts, open, high, low, close, volume FROM refs "
            "WHERE key=? AND ts>=? ORDER BY ts", (key, int(since_ms))).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low",
                                         "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.astype({c: float for c in
                          ("open", "high", "low", "close", "volume")})

    def last_ts(self, key: str) -> int | None:
        r = self.db.execute("SELECT MAX(ts) FROM refs WHERE key=?",
                            (key,)).fetchone()
        return int(r[0]) if r and r[0] is not None else None
