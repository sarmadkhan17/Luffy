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
import time
from . import market_provenance as mp
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core.config import ROOT
from ..core.types import TF_MS, closed_bars
from .sqlite_tx import BUSY_TIMEOUT_S, close_quietly, write_tx, insert_rows

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
    """Append-only receipt/revisions plus the legacy one-row inventory projection."""

    #: seconds a statement waits on a lock before "database is locked"
    BUSY_TIMEOUT_S = BUSY_TIMEOUT_S

    def __init__(self, db_path=None, clock_ms=None):
        self._db_path = str(db_path) if db_path else \
            str(ROOT / "data" / "candles.db")
        self._local = threading.local()
        self._clock_ms = clock_ms or (lambda: int(time.time()*1000))

    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, timeout=self.BUSY_TIMEOUT_S)
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS refs (key TEXT NOT NULL, "
                    "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, "
                    "close REAL, volume REAL, PRIMARY KEY (key, ts))")
                mp.init(conn)
                conn.commit()
            except BaseException:
                close_quietly(conn, "init")  # setup error wins; not cached
                raise
            self._local.conn = conn
        return conn

    def save(self, key: str, df, now_ms: int | None = None) -> int:
        """Persist CLOSED bars with a measured close. Returns rows written."""
        ref = REFS[key]
        raw = df.attrs.get('raw_source_record') if df is not None else None
        if df is None or not len(df):
            return 0
        if ref.tf in TF_MS:
            df = closed_bars(df, ref.tf, now_ms)
            if df is None or not len(df):
                return 0
        df = df.copy()
        for column in ('open','high','low','volume'):
            if column not in df:
                df[column] = np.nan
        n = len(df)
        # A single immutable acquisition is shared by all its bars. Hash it
        # once; repeated whole-response hashing made reference refresh O(n²).
        raw_hash = mp.digest(raw) if raw is not None else None
        raw_text = mp.encode(raw) if raw is not None else None
        received = self._clock_ms() if now_ms is None else mp.cut(now_ms)
        if not all(k in df for k in mp.META):
            # The recorder's source receipt is required. Arbitrary imported
            # projections and old table rows do not establish historical truth.
            raw = df.attrs.get('raw_source_record')
            df = mp.annotate(df, instrument_id='ref:'+key if raw is not None else None,
                             source=df.attrs.get('source') if raw is not None else None,
                             kind='reference', received_ms=received,
                             request_started_ms=df.attrs.get('request_started_ms'),
                             timeframe=ref.tf if ref.tf in TF_MS else None,
                             raw=[{'raw_source_hash':raw_hash, 'event_ms':int(t)} for t in to_ms(df['ts'])] if raw is not None else None)
        known = to_ms(df['ts']) + ref.close_after_ms
        if not ref.close_only:
            numeric=df[['open','high','low','close','volume']]
            finite=np.isfinite(numeric).all(axis=1)
            geometry=(df['volume'].ge(0) & df['low'].le(df[['open','close']].min(axis=1)) &
                      df['high'].ge(df[['open','close']].max(axis=1)))
            df.loc[df['quality'].eq('VALID') & ~finite,'quality']='INCOMPLETE'
            df.loc[df['quality'].eq('VALID') & ~geometry,'quality']='INVALID'
        df['available_at_ms'] = np.maximum(df['available_at_ms'], known)
        early = df['available_at_ms'] > received
        df.loc[early, 'quality'] = 'INCOMPLETE'
        df.loc[early, 'bar_state'] = 'PARTIAL'

        def col(c):
            return df[c].tolist() if c in df else [None] * n

        rows = [(key, int(t), _num(o), _num(h), _num(lo), _num(c), _num(v))
                for t, o, h, lo, c, v in zip(
                    to_ms(df["ts"]), col("open"), col("high"), col("low"),
                    col("close"), col("volume"))]
        rows = [r for r in rows if r[5] is not None]  # no close, no bar
        if rows:
            prepared = mp.prepare(df)
            # a failure raises to the caller with nothing left open or
            # half-committed on this thread's connection — see sqlite_tx
            with write_tx(self._local, self.db, f"ref {key}") as conn:
                if raw is not None:
                    conn.execute('INSERT OR IGNORE INTO market_raw_sources VALUES (?,?,?)',
                                 (raw_hash, df['source'].iloc[0], raw_text))
                mp.append(conn, "reference:"+key, prepared=prepared)
                insert_rows(conn, "INSERT OR REPLACE INTO refs", rows, 7)
        return len(rows)

    def load(self, key: str, since_ms: int = 0, *, as_of_ms=None):
        at = self._clock_ms() if as_of_ms is None else mp.cut(as_of_ms)
        df = mp.load(self.db, 'reference:'+key, as_of_ms=at, revision_stream=as_of_ms is None)
        df = mp.eligible_frame(df, None, at)
        if as_of_ms is not None and df is not None and len(df):
            ref = REFS[key]
            if at-int(df.event_time_ms.iloc[-1])-ref.close_after_ms > ref.max_stale_ms:
                df['quality']='STALE'
                df.loc[:,[k for k in mp.VALUES if k in df]]=np.nan
        return df.loc[to_ms(df['ts']) >= since_ms].copy() if df is not None else None

    def last_ts(self, key: str) -> int | None:
        r = self.db.execute("SELECT MAX(ts) FROM refs WHERE key=?",
                            (key,)).fetchone()
        return int(r[0]) if r and r[0] is not None else None
