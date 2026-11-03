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

Provider retention above describes retrievable content, not historical LUFFY
availability. Only retained receipt/revision clocks can establish PIT replay.

Because the ~30-day series cannot be backfilled, the recorder runs from day
one — every day without it is a day of history permanently lost.
"""
from __future__ import annotations

from copy import deepcopy

import logging
import threading
import time
import uuid

import pandas as pd
import requests

from . import derivative_tail as tail
from . import market_provenance as mp
from ..core.types import MarketType
from ..core.instrument_registry import InstrumentId

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
_CLOCK_FIELDS = frozenset(("event_time_ms", "observed_at_ms", "available_at_ms",
                           "max_age_ms", "request_started_ms"))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}

SQLITE_BUSY_MS = 1000
SQLITE_CACHE_KIB = 8192


def _configure_connection(conn, *, read_only=False):
    """Connection-local bounds apply on every open, including versioned stores."""
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute(f'PRAGMA busy_timeout={SQLITE_BUSY_MS}')
    conn.execute('PRAGMA synchronous=FULL')
    conn.execute('PRAGMA temp_store=MEMORY')
    conn.execute(f'PRAGMA cache_size=-{SQLITE_CACHE_KIB}')
    if read_only:
        conn.execute('PRAGMA query_only=ON')
    elif conn.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
        raise RuntimeError('derivative_wal_unavailable')


def to_binance(symbol: str) -> str:
    """'BTC/USDT' or 'BTC/USDT:USDT' -> 'BTCUSDT'."""
    return symbol.split(":")[0].replace("/", "")


class DerivFeed:
    def __init__(self, db_path=None, timeout: int = 15, clock_ms=None, *, retrospective=None, read_only=False,
                 incremental=True):
        #: record_all asks for a bounded tail only from a proven cursor
        self.incremental = bool(incremental)
        self.last_pass: list = []
        self._db_path = str(db_path) if db_path else None
        self.read_only = bool(read_only)
        self.retrospective = deepcopy(retrospective) if retrospective is not None else None
        if self.retrospective is not None:
            if not db_path or not self.retrospective.get('dataset_id'):
                raise ValueError('retrospective dependency requires explicit dataset_id and db_path')
            mp.cut(self.retrospective['cut_ms'])
        self._local = threading.local()
        self.timeout = timeout
        self._clock_ms = clock_ms or (lambda: int(time.time()*1000))
        self._local.receipts = []

    # ── store ────────────────────────────────────────────────────────────
    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if self.read_only or self.retrospective is not None:
                import sqlite3
                from pathlib import Path
                if self._db_path is None:
                    from ..core.config import ROOT
                    self._db_path = str(ROOT / "data" / "derivs.db")
                conn = sqlite3.connect(Path(self._db_path).resolve().as_uri() + '?mode=ro', uri=True)
                _configure_connection(conn, read_only=True)
                self._local.conn = conn
                return conn
            import sqlite3
            if self._db_path is None:
                from ..core.config import ROOT
                self._db_path = str(ROOT / "data" / "derivs.db")
            conn = sqlite3.connect(self._db_path)
            try:
                _configure_connection(conn)
            except Exception:
                conn.close()
                raise
            if mp.initialised(conn, 'derivs', 'deriv_acquisition_cursor'):
                self._local.conn = conn      # versioned: no schema or receipt writes
                return conn
            # Measured recorder transactions outlive bounded current readers.
            # WAL retains atomic revisions while readers see committed source
            # snapshots without waiting for an unrelated writer to commit.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS derivs ("
                "symbol TEXT NOT NULL, series TEXT NOT NULL, "
                "ts INTEGER NOT NULL, value REAL, "
                "PRIMARY KEY (symbol, series, ts))")
            mp.init(conn)
            tail.init(conn)
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

    def save(self, symbol: str, series: str, df: pd.DataFrame, *, acquisition=None) -> None:
        if self.read_only or self.retrospective is not None:
            raise ValueError('retrospective dependency is read-only')
        if df is None or df.empty:
            return
        # Imported plain values are inventory, never historical receipt proof.
        if not all(k in df for k in mp.META):
            df = mp.annotate(df, instrument_id=None, source=None, kind='derivative',
                             received_ms=self._clock_ms())
        # Receipt sealing and projection conversion do not own SQLite's
        # writer lock. Exact ancestry selection remains atomic in append().
        prepared = mp.prepare(df)
        projection = [(symbol, series, int(t), float(v))
                      for t, v in zip(mp.ms(df['ts']), df['value'])]
        from .sqlite_tx import write_tx, save_projection
        claim = self._coverage_claim(symbol, series, df, acquisition)
        with write_tx(self._local, self.db, 'derivative receipt') as conn:
            # Reserve the writer before ancestry/cursor reads. A deferred WAL
            # read snapshot cannot upgrade after another producer commits.
            if not conn.in_transaction:
                conn.execute('BEGIN IMMEDIATE')
            if acquisition is not None and acquisition['mode'] == 'required_window':
                # Keep proved unchanged history; append only gaps, corrections
                # and two freshly observed tip events. Selection and writes
                # share this writer transaction, so a concurrent correction
                # cannot be lost between the comparison and the append.
                expected = self.current_source(series)
                instrument = InstrumentId('binanceusdm', MarketType.FUTURES, to_binance(symbol)).value
                old = mp.load_current_candles(conn, 'derivative:'+symbol+':'+series,
                    as_of_ms=self._clock_ms(), limit=len(df), instrument_id=instrument,
                    source=expected, min_event_ms=int(df['event_time_ms'].min()))
                prior = {} if old is None else {int(r.event_time_ms): r
                    for r in old.itertuples() if r.quality == 'VALID'}
                tip = sorted(set(int(t) for t in df['event_time_ms']))[-2:]
                keep = [i for i, r in enumerate(df.itertuples())
                    if int(r.event_time_ms) in tip or r.quality != 'VALID'
                    or int(r.event_time_ms) not in prior
                    or prior[int(r.event_time_ms)].value != r.value]
                prepared = [prepared[i] for i in keep]
                projection = [projection[i] for i in keep]
            mp.append(conn, 'derivative:'+symbol+':'+series, prepared=prepared)
            save_projection(conn, 'derivs', ('symbol','series','ts','value'), ('symbol','series','ts'), projection)
            if claim is not None:
                # Same transaction as the receipts: a failed save never moves it.
                if not conn.in_transaction:
                    conn.execute('BEGIN IMMEDIATE')   # exact retry wrote nothing; still serialize the read
                state = tail.advance(tail.read(conn, claim['key']), claim['spec'], claim['mode'],
                                     claim['events'], claim['now_ms'], claim['identity'],
                                     claim['start_ms'])
                if state is not None:
                    tail.write(conn, state)
        # Fetch freshness means COMMITTED receipts, never a successful HTTP call.
        if (acquisition is not None and acquisition.get('complete', True)
                and set(df['quality']) == {'VALID'}):
            self._fetched_at[self._need_key(symbol, series)] = acquisition['now_ms']
        mp.refresh_entries(self.db, 'derivative:'+symbol+':'+series, deferred=True)

    def _coverage_claim(self, symbol, series, df, acquisition):
        """What a retrieval may claim: only a fully VALID response from exactly
        the planned source and instrument. Anything else leaves the cursor alone."""
        if not self.incremental or acquisition is None or acquisition['mode'] == 'required_window' or series not in tail.SPECS:
            return None
        spec = tail.SPECS[series]
        instrument = InstrumentId('binanceusdm', MarketType.FUTURES, to_binance(symbol)).value
        if (set(df['quality']) != {'VALID'} or set(df['instrument_id']) != {instrument}
                or set(df['source']) != {spec.source}):
            return None
        key = tail.cursor_key('derivative:'+symbol+':'+series, spec.source, instrument, spec.period)
        return dict(key=key, spec=spec, mode=acquisition['mode'], events=mp.ms(df['ts']).tolist(),
                    now_ms=acquisition['now_ms'], start_ms=acquisition.get('start_ms'),
                    identity=dict(cursor_key=key, series_key='derivative:'+symbol+':'+series,
                                  source=spec.source, instrument_id=instrument, period=spec.period))

    def load(self, symbol: str, series: str, limit: int = 200000, *, as_of_ms=None, window_start_ms=None, instrument_id=None):
        if self.retrospective is not None:
            d = self.retrospective
            if as_of_ms is not None:
                raise ValueError('retrospective observations cannot serve historical as-of evidence')
            if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_revisions'").fetchone():
                raise ValueError('retrospective dependency requires trustworthy retained provenance: market_revisions')
            identities = self.db.execute(
                "SELECT DISTINCT json_extract(record_json,'$.instrument_id') "
                "FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=? "
                "AND (? IS NULL OR event_ms<?)",
                ('derivative:'+symbol+':'+series, d.get('start_ms',0), d['cut_ms']-0,
                 d.get('end_ms'), d.get('end_ms'))).fetchall()
            if len({row[0] for row in identities if row[0] is not None}) > 1:
                raise ValueError('retrospective dependency ambiguous instrument identity: ' + 'derivative:'+symbol+':'+series)
            df = mp.retrospective_load(self.db, 'derivative:'+symbol+':'+series, cut_ms=d['cut_ms'],
                                      start_ms=d.get('start_ms', 0), end_ms=d.get('end_ms'),
                                      limit=limit)
            if df is not None:
                import numpy as np
                df = df.loc[df['quality'].eq('VALID') & df['source'].notna() & df['instrument_id'].notna()
                            & np.isfinite(df['value'])].copy()
            if df is None or not len(df):
                raise ValueError('retrospective dependency missing trustworthy retained source: ' + 'derivative:'+symbol+':'+series)
            df['observation_quality'] = 'VALID'
            df.attrs.update(input_identity=mp.digest(df['revision_id'].tolist()), retrospective=deepcopy(d), dataset_id=d['dataset_id'],
                            historical_cut_ms=d['cut_ms'], read_mode='retrospective',
                            evidence_scope='retrospective_observation')
            return df
        at = self._clock_ms() if as_of_ms is None else mp.cut(as_of_ms)
        key = 'derivative:'+symbol+':'+series
        if as_of_ms is not None and window_start_ms is not None and instrument_id is not None:
            # Current typed consumer: bounded index-ranked window, cached across
            # scans by committed witness. Missing proof is a reason, not a scan.
            from ..scheduling.timing import stage_scope
            reasons = self.__dict__.setdefault('input_reasons', {})
            start = window_start_ms
            def loader():
                frame, reason = mp.load_window_typed(self.db, key, as_of_ms=at, window_start_ms=start,
                                                     instrument_id=instrument_id, limit=limit)
                reasons[key] = reason
                return frame
            with stage_scope('input.derivative_read'):
                df = mp.cached_current(self.db, key, as_of_ms=at, limit=limit, instrument_id=instrument_id,
                                       window_start_ms=start, loader=loader,
                                       refill=lambda: self.load(symbol, series, limit, as_of_ms=self._clock_ms(),
                                                                window_start_ms=window_start_ms,
                                                                instrument_id=instrument_id))
            if df is None and mp.last_reason():
                reasons[key] = mp.last_reason()
            expected = self.current_source(series)
            if df is not None and expected is not None and not df['source'].eq(expected).all():
                # A current typed input must come from its specified source; never
                # silently accept another provider's rows for this series.
                reasons[key] = 'source_mismatch'
                return None
            df = mp.eligible_frame(df, None, at)
            if df is not None and len(df):
                tail = df.iloc[-1]
                age = tail.get('max_age_ms')
                if age is not None and pd.notna(age) and at-int(tail.event_time_ms) > age:
                    df['quality'] = 'STALE'
                    df['value'] = float('nan')
            return df
        df = mp.load(self.db, key, as_of_ms=at, limit=limit, revision_stream=as_of_ms is None, window_start_ms=window_start_ms)
        df = mp.eligible_frame(df, None, at)
        if as_of_ms is not None and df is not None and len(df):
            tail=df.iloc[-1]
            age=tail.get('max_age_ms')
            if age is not None and pd.notna(age) and at-int(tail.event_time_ms)>age:
                df['quality']='STALE'
                df['value']=float('nan')
        return df

    #: the exact provider endpoint each CURRENT series is specified to come from.
    #: ``ls_account_ratio`` has no adapter here (Coinalyze only): unspecified, explicit.
    FUNDING_SOURCE = FAPI + "/fapi/v1/fundingRate"

    @classmethod
    def current_source(cls, series):
        if series == 'funding':
            return cls.FUNDING_SOURCE
        spec = tail.SPECS.get(series)
        return spec.source if spec is not None else None

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
        if self.read_only or self.retrospective is not None:
            raise ValueError('retrospective dependency forbids provider calls')
        import uuid
        self._local.receipts = []
        started = self._clock_ms()
        try:
            r = requests.get(f"{base}{path}", params=params, headers=UA,
                             timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            self._local.receipts = [dict(source=base+path, params=dict(params),
                                         received_ms=self._clock_ms(), request_started_ms=started,
                                         request_id=uuid.uuid4().hex, raw=data)]
            return data if isinstance(data, list) else []
        except Exception as e:
            log.warning(f"derivs fetch {path} {params.get('symbol')}: {e}")
            return []

    def _frame(self, pairs: list) -> pd.DataFrame:
        df = pd.DataFrame(pairs, columns=["ts", "value"])
        if df.empty:
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            return df
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.sort_values("ts").reset_index(drop=True)
        receipts = getattr(self._local, 'receipts', [])
        identity = None
        source = None
        if receipts:
            symbol = receipts[-1]['params'].get('symbol')
            if symbol:
                identity = InstrumentId('binanceusdm', MarketType.FUTURES, symbol).value
                source = receipts[-1]['source']
        # Preserve receipt order and duplicate source rows, indexing once
        # instead of rescanning every acquisition for every observation.
        indexed = []
        for receipt in receipts:
            events = {}
            for record in receipt['raw']:
                event = (record.get('timestamp', record.get('fundingTime')) if isinstance(record, dict)
                         else record[6] if isinstance(record, (list,tuple)) and len(record)>6 else None)
                try:
                    events.setdefault(event, []).append(record)
                except TypeError:
                    pass  # Unhashable source stamps never matched an integer.
            indexed.append(events)
        raw = [{'receipts': [{**{k:v for k,v in r.items() if k!='raw'},
                              'raw': matches.get(int(event), [])}
                             for r,matches in zip(receipts,indexed)],
                'event_ms': int(event), 'value': float(value)}
               for event,value in zip(mp.ms(df['ts']),df['value'])]
        valid_receipts = bool(receipts) and all(r['received_ms'] >= r['request_started_ms'] for r in receipts)
        return mp.annotate(df, instrument_id=identity if valid_receipts else None,
                           source=source if valid_receipts else None, kind='derivative',
                           received_ms=self._clock_ms(), raw=raw,
                           request_started_ms=min((r['request_started_ms'] for r in receipts),default=None),
                           max_age_ms=(8*3_600_000+1_800_000 if source and 'fundingRate' in source
                                       else _PERIOD_MS.get(receipts[-1]['params'].get('period',receipts[-1]['params'].get('interval','1h')),3_600_000)+1_800_000
                                       if receipts else None))


    def _parse_funding(self, raw: list) -> pd.DataFrame:
        return self._frame([(int(r["fundingTime"]), float(r["fundingRate"]))
                            for r in raw])

    def _parse_oi(self, raw: list) -> pd.DataFrame:
        return self._frame([(int(r["timestamp"]), float(r["sumOpenInterest"]))
                            for r in raw])

    def _parse_ratio(self, raw: list, key: str) -> pd.DataFrame:
        return self._frame([(int(r["timestamp"]), float(r[key])) for r in raw])

    @staticmethod
    def _range(params: dict, start_ms, end_ms) -> dict:
        """Optional documented startTime/endTime window (Binance USD-M market
        data). Callers must keep the range within one page's rows."""
        if start_ms is not None:
            params["startTime"] = int(start_ms)
        if end_ms is not None:
            params["endTime"] = int(end_ms)
        return params

    def funding(self, symbol: str, limit: int = 1000,
                period: str | None = None, *, start_ms=None,
                end_ms=None) -> pd.DataFrame:
        """Most recent funding settlements. `period` is accepted and ignored
        so the recorder can call every fetcher uniformly."""
        return self._parse_funding(self._get(
            "/fapi/v1/fundingRate",
            self._range({"symbol": to_binance(symbol), "limit": min(limit, 1000)},
                        start_ms, end_ms)))

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
            self._check_cancelled()
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
                      limit: int = 500, *, start_ms=None,
                      end_ms=None) -> pd.DataFrame:
        return self._parse_oi(self._get(
            "/futures/data/openInterestHist",
            self._range({"symbol": to_binance(symbol), "period": period,
                         "limit": min(limit, 500)}, start_ms, end_ms)))

    def taker_ratio(self, symbol: str, period: str = "15m",
                    limit: int = 500, *, start_ms=None,
                    end_ms=None) -> pd.DataFrame:
        return self._parse_ratio(self._get(
            "/futures/data/takerlongshortRatio",
            self._range({"symbol": to_binance(symbol), "period": period,
                         "limit": min(limit, 500)}, start_ms, end_ms)), "buySellRatio")

    def ls_ratio(self, symbol: str, period: str = "15m", limit: int = 500,
                 kind: str = "top", *, start_ms=None,
                 end_ms=None) -> pd.DataFrame:
        path = ("/futures/data/topLongShortPositionRatio" if kind == "top"
                else "/futures/data/globalLongShortAccountRatio")
        return self._parse_ratio(self._get(
            path, self._range({"symbol": to_binance(symbol), "period": period,
                               "limit": min(limit, 500)}, start_ms, end_ms)),
            "longShortRatio")

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
              limit: int = 500, *, start_ms=None) -> pd.DataFrame:
        """Recent spot-perp basis: (perp - spot) / spot, per closed bar.

        Unlike OI and the ratio series, nothing here is capped at 30 days —
        both klines endpoints serve years. `basis` was registered as a
        feature from the start and no fetcher ever existed, so every basis
        spec reported UNTESTED forever. This is that fetcher.
        """
        span_ms = _PERIOD_MS.get(period, 3600_000) * int(limit)
        start = self._clock_ms() - span_ms if start_ms is None else int(start_ms)
        return self._basis_between(symbol, period, start)

    def _check_cancelled(self):
        if getattr(self, 'cancelled', lambda: False)():
            raise InterruptedError('derivative_acquisition_stopped')
        # Batch boundary of a cold/history pass: let current demand go first.
        hook = getattr(self, 'history_yield', None)
        if hook is not None:
            hook()

    def _basis_between(self, symbol: str, period: str,
                       start_ms: int) -> pd.DataFrame:
        self._check_cancelled()
        sym = to_binance(symbol)
        perp = dict(self._klines(FAPI, "/fapi/v1/klines", sym, period,
                                 start_ms, self._KLINE_PAGE))
        perp_receipts = list(getattr(self._local, 'receipts', []))
        spot = dict(self._klines(SAPI, "/api/v3/klines", sym, period,
                                 start_ms, self._KLINE_PAGE))
        # inner join on close time: a bar missing on either venue has no basis
        pairs = [(ts, (perp[ts] - spot[ts]) / spot[ts])
                 for ts in sorted(perp.keys() & spot.keys())
                 if spot[ts] > 0]
        spot_receipts = list(getattr(self._local, 'receipts', []))
        self._local.receipts = perp_receipts + spot_receipts
        if not pairs:
            return self._frame([])
        legs = ((perp_receipts, perp, 'binanceusdm', MarketType.FUTURES),
                (spot_receipts, spot, 'binance', MarketType.SPOT))
        # One indexed pass per raw receipt, not one scan per event. The bucket
        # keeps raw order, so records[-1] is still the last match WITHIN a
        # receipt; every receipt (and its multiplicity) keeps its own component.
        batches = []
        for receipts, closes, venue, market in legs:
            identity = InstrumentId(venue, market, sym).value
            for r in receipts:
                by_event = {}
                for k in r['raw']:
                    if isinstance(k, (list, tuple)) and len(k) > 6:
                        try:
                            by_event.setdefault(k[6], []).append(k)
                        except TypeError:
                            pass       # an unhashable stamp never equalled an integer
                matched = {}
                for position, (event, _) in enumerate(pairs):
                    price = closes[event]
                    records = [k for k in by_event.get(event, ()) if float(k[4]) == price]
                    if records:
                        matched[position] = records[-1]
                batches.append((r, identity, closes, matched))
        # Components are annotated once per receipt for all of its events.
        # A receipt without its own request_id must mint one per component
        # (annotate's behaviour), so it stays on the per-component path.
        built = []
        for r, identity, closes, matched in batches:
            rows = None
            if matched and r.get('request_id'):
                rows = dict(zip(matched, self._component_rows(
                    r, identity, [pairs[p][0] for p in matched],
                    [closes[pairs[p][0]] for p in matched], list(matched.values()))))
            built.append(rows)
        specs = []
        for position, (event, value) in enumerate(pairs):
            self._check_cancelled()
            components = []
            for (r, identity, closes, matched), rows in zip(batches, built):
                if position not in matched:
                    continue
                components.append(rows[position] if rows is not None else self._component_rows(
                    r, identity, [event], [closes[event]], [matched[position]])[0])
            valid = len(components) == 2 and all(c['quality'] == 'VALID' for c in components)
            # A derived receipt cannot precede either required input receipt.
            component_times = [c['available_at_ms'] for c in components if c['available_at_ms'] is not None]
            component_times += [c['observed_at_ms'] for c in components]
            available = max([self._clock_ms()] + component_times)
            raw = dict(components=components, derivation='basis.spot-perp.v1',
                       event_ms=event, value=value)
            specs.append((position, valid, available, event, value, raw, uuid.uuid4().hex))
        groups = {}
        for spec in specs:
            groups.setdefault((spec[1], spec[2], spec[3] <= spec[2]), []).append(spec)
        frames = []
        for (valid, available, _), group in groups.items():
            frame = mp.annotate(pd.DataFrame(dict(
                ts=pd.to_datetime([g[3] for g in group], unit='ms', utc=True),
                value=[g[4] for g in group])),
                instrument_id=InstrumentId('binanceusdm', MarketType.FUTURES, sym).value if valid else None,
                source='binance:spot+usdm:basis' if valid else None, kind='derivative',
                received_ms=available, raw=[g[5] for g in group],
                request_id=group[0][6], transform_version='basis.spot-perp.v1',
                max_age_ms=_PERIOD_MS.get(period, 3_600_000)+1_800_000)
            if len(group) > 1:
                # Each derived receipt keeps its own request identity.
                frame['request_id'] = [g[6] for g in group]
                frame = mp.seal(frame)
            frame['_position'] = [g[0] for g in group]
            frames.append(frame)
        out = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        if len(frames) > 1:
            out = out.sort_values('_position', kind='stable')
        return out.drop(columns='_position').reset_index(drop=True)

    @staticmethod
    def _component_rows(receipt, identity, events, prices, records) -> list:
        """Receipt metadata dicts for one receipt's matched events, in one batch."""
        frame = mp.annotate(pd.DataFrame(dict(
            ts=pd.to_datetime(events, unit='ms', utc=True), value=prices)),
            instrument_id=identity, source=receipt['source'], kind='derivative',
            received_ms=receipt['received_ms'], request_id=receipt.get('request_id'),
            request_started_ms=receipt['request_started_ms'], raw=records)
        columns = []
        for key in mp.META:
            values = frame[key].tolist()
            if key in _CLOCK_FIELDS:
                values = [None if v is None or v != v else int(v) for v in values]
            columns.append(values)
        return [dict(zip(mp.META, row)) for row in zip(*columns)]

    def basis_history(self, symbol: str, years: float = 2.0,
                      period: str = "1h", delay: float = 0.25,
                      since_ms: int | None = None) -> pd.DataFrame:
        """Page basis back to `since_ms`, or `years` when nobody says.

        Both venues serve deep klines, so this is the one 30-day-capped
        series that is not actually capped — the only thing that ever
        limited it was the default.
        """
        import time as _t
        now_ms = self._clock_ms()
        step = _PERIOD_MS.get(period, 3600_000) * self._KLINE_PAGE
        cursor = (int(since_ms) if since_ms
                  else now_ms - int(years * 365.25 * 86400 * 1000))
        chunks = []
        for _ in range(_MAX_BASIS_PAGES):
            self._check_cancelled()
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
        if self.read_only or self.retrospective is not None:
            raise ValueError('retrospective dependency acquisition forbidden')
        counts: dict = {}
        since = since or {}
        # Resumable slices: stop at a symbol boundary once the budget is spent and
        # leave the next index in ``history_resume`` (None = complete).
        started = time.monotonic()
        start = int(getattr(self, 'backfill_start', 0) or 0)
        budget = getattr(self, 'backfill_budget_s', None)
        self.history_resume = None
        for index, sym in enumerate(symbols):
            if index < start:
                continue
            if budget is not None and index > start and time.monotonic() - started >= budget:
                self.history_resume = index
                break
            self._check_cancelled()
            floor = since.get(sym)
            try:
                fh = self.funding_history(sym, delay=delay, since_ms=floor)
                if fh is not None and len(fh):
                    self.save(sym, "funding", fh)
                    counts["funding@history"] = \
                        counts.get("funding@history", 0) + len(fh)
            except InterruptedError:
                raise
            except Exception as e:
                log.warning(f"derivs backfill {sym}/funding history: {e}")
            try:
                bh = self.basis_history(sym, delay=delay, since_ms=floor)
                if bh is not None and len(bh):
                    self.save(sym, "basis", bh)
                    counts["basis@history"] = \
                        counts.get("basis@history", 0) + len(bh)
            except InterruptedError:
                raise
            except Exception as e:
                log.warning(f"derivs backfill {sym}/basis history: {e}")
            for series, method in self._SERIES_FETCHERS:
                self._check_cancelled()
                if series in ("funding", "basis"):
                    continue            # both have real history above
                for period in self.BACKFILL_PERIODS:
                    try:
                        df = getattr(self, method)(sym, period=period)
                    except InterruptedError:
                        raise
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

    def _acquire_current(self, sym: str, series: str, method: str):
        """One series for one symbol: a bounded tail from a proven cursor,
        otherwise the existing full request. -> (frame, acquisition | None)."""
        if not self.incremental or series not in tail.SPECS:
            df = getattr(self, method)(sym)
            self.last_pass.append(dict(symbol=sym, series=series, mode=tail.FULL,
                                       reason=tail.FULL_ONLY.get(series, 'incremental_disabled'),
                                       start_ms=None, rows=0 if df is None else len(df)))
            return df, dict(mode=tail.FULL, now_ms=self._clock_ms(), start_ms=None)
        spec = tail.SPECS[series]
        now = self._clock_ms()
        instrument = InstrumentId('binanceusdm', MarketType.FUTURES, to_binance(sym)).value
        state = tail.read(self.db, tail.cursor_key('derivative:'+sym+':'+series, spec.source,
                                                   instrument, spec.period))
        mode, start, reason = tail.plan(state, spec, now)
        if mode == tail.TAIL:
            kwargs = {'start_ms': start} if series == 'basis' else {'start_ms': start, 'end_ms': now}
            df = getattr(self, method)(sym, **kwargs)
        else:
            df = getattr(self, method)(sym)
        self.last_pass.append(dict(symbol=sym, series=series, mode=mode, reason=reason,
                                   start_ms=start, rows=0 if df is None else len(df)))
        return df, dict(mode=mode, now_ms=now, start_ms=start)

    #: refresh cadence of a REQUIRED series: the finest source period (15m) is the
    #: sampling floor for oi/taker/ls; funding settles every 8h but is re-read at the
    #: recorder's own cadence so a correction is observed; basis is hourly.
    NEED_INTERVAL_MS = {'funding': 900_000, 'oi': 900_000, 'taker_ratio': 900_000,
                        'ls_ratio': 900_000, 'basis': 3_600_000}
    #: a boundary's value is requested only once the source has had this long to publish it
    NEED_PUBLISH_LAG_MS = 20_000
    _fetched_at: dict = {}      # (physical store, symbol, series) -> committed acquisition

    def _need_key(self, symbol, series):
        # Never let a fixture, research store or another database suppress production.
        return (mp._db_id(self.db), symbol, series)

    def _required_window(self, symbol, series, span):
        """Fetch only a strategy's recent window, at the declared source period.

        No multi-year fallback or coarsening. Page only within that exact window;
        a bounded unfinished window remains visible for the next pass.
        """
        now = self._clock_ms()
        period = self.NEED_INTERVAL_MS[series]
        start = max(0, now - max(int(span), period) - (86_400_000 if series == 'funding' else period))
        method = dict(self._SERIES_FETCHERS)[series]
        chunks = []
        cursor = start
        for _ in range(8):
            self._check_cancelled()
            size = 1000 if series == 'funding' else 500
            kwargs = dict(start_ms=cursor, end_ms=now, limit=size)
            if series == 'basis':
                kwargs.pop('end_ms')
            if series != 'funding':
                kwargs['period'] = '1h' if series == 'basis' else '15m'
            frame = getattr(self, method)(symbol, **kwargs)
            if frame is None or frame.empty:
                break
            chunks.append(frame)
            newest = int(mp.ms(frame['ts']).max())
            if newest < cursor or len(frame) < size or newest >= now:
                break
            cursor = newest + 1
        frame = pd.concat(chunks, ignore_index=True) if chunks else self._frame([])
        complete = False
        if len(frame):
            latest = frame.loc[frame['event_time_ms'].idxmax()]
            complete = (latest['quality'] == 'VALID' and
                        now - int(latest['event_time_ms']) <= int(latest['max_age_ms']))
        return frame, dict(mode='required_window', now_ms=now, start_ms=start, complete=complete)

    def acquire_needs(self, needs, *, max_requests: int = 20, delay: float = 0.3, max_deepen: int = 4, held=()) -> dict:
        """Acquire exactly the (symbol, series) pairs current strategies require.

        Reuses the recorder's own bounded request (tail from a proven cursor,
        otherwise the standard one-page window) and its receipt/cursor writes.
        With a declared span, requests only that recent window at source resolution.
        Never a multi-year backfill. Series already acquired within their
        cadence (by this or the broad recorder) are skipped; unsupported ones
        are reported, not guessed. ``needs`` is priority-ordered.
        """
        if self.read_only or self.retrospective is not None:
            raise ValueError('retrospective dependency acquisition forbidden')
        methods = dict(self._SERIES_FETCHERS)
        out = dict(requested=0, fetched=0, skipped_fresh=0, unsupported=[], failed=0, deferred=0,
                   deepened=0, deepen_deferred=0)
        now = self._clock_ms()
        spans = {}
        order = []
        for need in needs:
            symbol, series = need[0], need[1]
            if (symbol, series) not in spans:
                order.append((symbol, series))
            spans[(symbol, series)] = max(spans.get((symbol, series), 0), need[2] if len(need) > 2 else 0)
        attempted = self.__dict__.setdefault('_need_attempted', {})
        # Oldest attempted inputs first; stable shortlist order breaks ties.
        order.sort(key=lambda pair: (pair[0] not in held, attempted.get(pair, -1)))
        for symbol, series in order:
            self._check_cancelled()
            if series not in methods:
                out['unsupported'].append(f'{symbol}:{series}')
                continue
            period = self.NEED_INTERVAL_MS[series]
            boundary = (now - self.NEED_PUBLISH_LAG_MS) // period * period
            if self._fetched_at.get(self._need_key(symbol, series), -10**18) >= boundary + self.NEED_PUBLISH_LAG_MS:
                out['skipped_fresh'] += 1
            elif out['requested'] >= max_requests:
                out['deferred'] += 1
            else:
                out['requested'] += 1
                attempted[(symbol, series)] = now
                try:
                    df, acquisition = (self._required_window(symbol, series, spans[(symbol, series)])
                                       if spans[(symbol, series)] else
                                       self._acquire_current(symbol, series, methods[series]))
                    if df is not None and len(df):
                        self.save(symbol, series, df, acquisition=acquisition)
                        out['fetched'] += 1
                    else:
                        out['failed'] += 1
                except InterruptedError:
                    raise
                except Exception as exc:
                    out['failed'] += 1
                    log.warning(f'derivs need {symbol}/{series}: {exc}')
                if delay:
                    time.sleep(delay)
            # Required-window acquisition already supplies the requested recent
            # lookback. Research/history backfills have their own worker.
        return out

    DEEPEN_RETRY_MS = 6 * 3_600_000
    _deepened_at: dict = {}

    def _deepen_to(self, symbol, series, start_ms, out, max_deepen, delay):
        """Bounded required-lookback paging: reach ``start_ms`` for one (symbol, series).

        Uses the existing adapters only (funding/basis history from ``start_ms``;
        coarser periods for the 30-day-retention series, as ``backfill`` does) and
        at most one attempt per DEEPEN_RETRY_MS, so a provider that cannot serve
        that far back is not hammered. Coverage still short afterwards stays
        visible as missing history; nothing is fabricated.
        """
        lo = self.db.execute('SELECT MIN(ts) FROM derivs WHERE symbol=? AND series=?', (symbol, series)).fetchone()[0]
        period_ms = self.NEED_INTERVAL_MS[series]
        if lo is not None and lo <= start_ms + 2 * period_ms:
            return
        now = self._clock_ms()
        if now - self._deepened_at.get((symbol, series), -10**18) < self.DEEPEN_RETRY_MS:
            return
        if out['deepened'] >= max_deepen:
            out['deepen_deferred'] += 1
            return
        self._deepened_at[(symbol, series)] = now
        out['deepened'] += 1
        try:
            if series == 'funding':
                df = self.funding_history(symbol, delay=delay, since_ms=int(start_ms))
            elif series == 'basis':
                df = self.basis_history(symbol, since_ms=int(start_ms), delay=delay)
            else:
                rows_15m = (now - start_ms) / 900_000
                period = '15m' if rows_15m <= 500 else '1h' if rows_15m <= 2000 else '4h'
                df = getattr(self, dict(self._SERIES_FETCHERS)[series])(symbol, period=period)
            if df is not None and len(df):
                self.save(symbol, series, df)
        except InterruptedError:
            raise
        except Exception as exc:
            out['failed'] += 1
            log.warning(f'derivs deepen {symbol}/{series}: {exc}')

    def record_all(self, symbols: list, delay: float = 0.3, *, exclude=()) -> dict:
        """One pass over every symbol and series. Rows already stored are
        replaced, so this is safe to run at any cadence and safe to retry.

        With a proven per-series cursor (see derivative_tail) a pass asks for
        a bounded tail; cold, gapped, unproven or reconcile-due series use the
        full request exactly as before. Every fetched row gets a new receipt
        either way. `last_pass` reports mode/reason/rows per series."""
        if self.read_only or self.retrospective is not None:
            raise ValueError('retrospective dependency acquisition forbidden')
        counts: dict = {}
        self.last_pass = []
        # A duplicated universe member is the same acquisition in this pass.
        # A later pass still retrieves and records a legitimate new receipt.
        for sym in dict.fromkeys(symbols):
            self._check_cancelled()
            for series, method in self._SERIES_FETCHERS:
                self._check_cancelled()
                if (sym, series) in exclude:
                    continue
                try:
                    df, acquisition = self._acquire_current(sym, series, method)
                except InterruptedError:
                    raise
                except Exception as e:
                    log.warning(f"derivs record {sym}/{series}: {e}")
                    continue
                if df is not None and len(df):
                    self.save(sym, series, df, acquisition=acquisition)
                    counts[series] = counts.get(series, 0) + len(df)
                if delay:
                    time.sleep(delay)          # public endpoints are rate-limited
        if self.last_pass:
            modes: dict = {}
            for item in self.last_pass:
                bucket = modes.setdefault(item['series'] + ':' + item['mode'], [0, 0])
                bucket[0] += 1
                bucket[1] += item['rows']
            log.info('derivs record_all (requests, rows): %s', modes)
        return counts
