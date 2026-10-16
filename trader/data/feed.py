"""Market data: ccxt OHLCV fetcher with TTL cache + validation.

Binance demo keys from .env; sandbox mode when BINANCE_DEMO is truthy.
"""
from __future__ import annotations

from copy import deepcopy

import logging
import threading
from collections import OrderedDict
from contextlib import nullcontext

import numpy as np
import time

from ..core.types import TF_MS as _TF_MS_SHARED, closed_bars, norm_symbol
from .sqlite_tx import BUSY_TIMEOUT_S, close_quietly, write_tx
from . import market_provenance as mp
from ..core.instrument_registry import canonical_venue
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
    ex.entry_account_scope = execution_account_scope(ex)
    return ex


def execution_account_scope(exchange):
    """Opaque identity of actual venue/credential context; never permission."""
    import hashlib
    from ..observability.portfolio_observation import trading_source
    key = getattr(exchange, 'apiKey', None)
    if getattr(exchange, 'id', None) != 'binanceusdm' or not isinstance(key, str) or not key:
        return None
    try:
        environment, source = trading_source(exchange)
    except (ValueError, TypeError, AttributeError):
        return None
    return 'execution-account.v1:' + hashlib.sha256(
        (environment + '\0' + source + '\0' + key).encode()).hexdigest()


class DataFeed:
    """Fetch + cache OHLCV per (symbol, timeframe). Thread-safe via GIL ops.

    Three layers: in-memory TTL cache → local sqlite candle store
    (data/candles.db, survives restarts, incrementally appended) →
    exchange REST calls (paged past the 1000-bar/request cap).
    """

    BACKGROUND_QUEUE_CAPACITY = 128
    # One existing queue. Reserve space and service for current trading data.
    HISTORY_QUEUE_RESERVE = 32
    HISTORY_BATCH = 16
    HISTORY_REQUEST_BARS = 154  # existing 153-bar broad-cache budget plus a forming bar
    HISTORY_RETRY_MS = 300_000
    MAX_CACHE_SERIES = 128

    def __init__(self, exchange=None, ttl_by_tf: dict | None = None,
                 db_path=None, clock_ms=None, retrospective=None):
        self.retrospective = deepcopy(retrospective) if retrospective is not None else None
        if self.retrospective is not None:
            if not db_path or not isinstance(self.retrospective.get('dataset_id'), str) or not self.retrospective['dataset_id'].strip():
                raise ValueError('retrospective requires explicit database and dataset_id')
            mp.cut(self.retrospective.get('cut_ms'))
            for key in ('start_ms', 'end_ms'):
                if self.retrospective.get(key) is not None:
                    mp.cut(self.retrospective[key])
            if self.retrospective.get('start_ms', 0) > self.retrospective['cut_ms']:
                raise ValueError('retrospective start exceeds historical cut')
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
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
            if (on_demo and exchange is None and self.retrospective is None) else None
        # cache freshness: 15m data ~2min old max; 1h ~10min; 4h ~30min
        self.ttl = ttl_by_tf or {"5m": 120, "15m": 180, "1h": 600, "4h": 1800, "1d": 7200}
        self._cache: dict[tuple, tuple[float, pd.DataFrame]] = OrderedDict()
        self._instruments = {}
        self._db_path = str(db_path) if db_path else None   # resolved lazily
        self._local = threading.local()                     # per-thread conns
        #: symbol -> unix ts it was last seen as non-existent on the exchange
        self._dead: dict[str, float] = {}
        self.background_acquisition = False
        self._acquisition_lock = threading.Lock()
        self._acquisition_queue_lock = threading.Lock()
        self._acquisition_queue = OrderedDict()
        self._acquisition_stopping = False
        self._acquisition_inflight = None
        self._acquisition_outcomes = OrderedDict()
        self._trading_burst = 0
        self._acquisition_queue_counts = {
            'accepted': 0, 'coalesced': 0, 'completed': 0,
            'skipped_fresh': 0, 'failed': 0, 'refused_full': 0,
            'refused_shutdown': 0, 'cancelled': 0,
        }
        self._acquisition_queue_last_outcome = None

    @property
    def ex(self):
        """Exchange used for market data (production public when on demo)."""
        if self.retrospective is not None:
            raise RuntimeError('retrospective provider access forbidden')
        if self.data_ex is not None:
            return self.data_ex
        if self._ex is None:
            self._ex = make_exchange()
        return self._ex

    def initialize_markets(self):
        """Boot initializes this feed's own public exchange, independently of execution.

        Failure leaves identity UNKNOWN; observation retries and protection/exit
        work can continue without fabricating a tradable identity.
        """
        try:
            with self._acquisition_lock if self.background_acquisition else nullcontext():
                self.ex.load_markets()
            self._start_background_worker()
            return True
        except Exception as exc:
            log.warning("market-data initialization unavailable: %s", exc)
            self._start_background_worker()
            return False

    def background_acquisition_health(self):
        """Bounded status for the optional single-worker candle queue."""
        with self._acquisition_queue_lock:
            return {
                'capacity': self.BACKGROUND_QUEUE_CAPACITY,
                'pending': len(self._acquisition_queue),
                'worker_busy': self._acquisition_lock.locked(),
                'inflight': self._acquisition_inflight[0] if self._acquisition_inflight else None,
                'outcomes_retained': len(self._acquisition_outcomes),
                'outcomes': [dict(symbol=k[0], timeframe=k[1], **v)
                             for k,v in self._acquisition_outcomes.items()],
                'stopping': self._acquisition_stopping,
                **self._acquisition_queue_counts,
                'last_outcome': self._acquisition_queue_last_outcome,
            }

    def cancel_background_acquisition(self):
        """Cancel queued work during shutdown; in-flight provider calls cannot be interrupted."""
        with self._acquisition_queue_lock:
            self._acquisition_stopping = True
            cancelled = len(self._acquisition_queue)
            for key in self._acquisition_queue:
                self._acquisition_outcomes[key] = dict(at_ms=self._clock_ms(), outcome='cancelled_shutdown')
                self._acquisition_outcomes.move_to_end(key)
            while len(self._acquisition_outcomes) > self.BACKGROUND_QUEUE_CAPACITY:
                self._acquisition_outcomes.popitem(last=False)
            self._acquisition_queue.clear()
            self._acquisition_queue_counts['cancelled'] += cancelled
            self._acquisition_queue_last_outcome = 'shutdown_cancelled'
            return cancelled

    def _start_background_worker(self):
        if not self.background_acquisition:
            return False
        with self._acquisition_queue_lock:
            if self._acquisition_stopping or not self._acquisition_queue:
                return False
            if not self._acquisition_lock.acquire(False):
                return False
        try:
            threading.Thread(target=self._drain_background_queue,
                             name='candle-acquisition', daemon=True).start()
            return True
        except Exception as exc:
            with self._acquisition_queue_lock:
                # Retain bounded pending demand for the next start attempt.
                self._acquisition_queue_counts['failed'] += 1
                self._acquisition_queue_last_outcome = (
                    'worker_start_failed:' + type(exc).__name__)
                self._acquisition_lock.release()
            log.warning('background candle worker unavailable: %s', type(exc).__name__)
            return False

    def _queue_background_request(self, symbol, tf, limit, force, min_bars, *, history=False):
        """Coalesce pending AND in-flight work; history cannot fill trading reserve."""
        key = (norm_symbol(symbol), tf)
        limit = min(int(limit), 20000)
        with self._acquisition_queue_lock:
            if self._acquisition_stopping:
                self._acquisition_queue_counts['refused_shutdown'] += 1
                self._acquisition_queue_last_outcome = 'refused_shutdown'
                return False
            inflight = self._acquisition_inflight
            if inflight and inflight[0] == key:
                active = inflight[1]
                if limit <= active['limit'] and (not force or active['force']) and min_bars <= active['min_bars']:
                    self._acquisition_queue_counts['coalesced'] += 1
                    self._acquisition_queue_last_outcome = 'coalesced_inflight'
                    return True
            pending = self._acquisition_queue.get(key)
            if pending is not None:
                pending['limit'] = max(pending['limit'], min(int(limit), 20000))
                pending['force'] = pending['force'] or bool(force)
                pending['min_bars'] = max(pending['min_bars'], int(min_bars))
                if not history:
                    pending.pop('history', None)  # promotion retains one request
                self._acquisition_queue_counts['coalesced'] += 1
                self._acquisition_queue_last_outcome = 'coalesced'
            elif len(self._acquisition_queue) >= (self.BACKGROUND_QUEUE_CAPACITY -
                    min(self.HISTORY_QUEUE_RESERVE, self.BACKGROUND_QUEUE_CAPACITY // 4) if history
                    else self.BACKGROUND_QUEUE_CAPACITY):
                self._acquisition_queue_counts['refused_full'] += 1
                self._acquisition_queue_last_outcome = 'refused_queue_full'
                log.warning('background candle request refused: queue full (%s %s)',
                            symbol, tf)
                return False
            else:
                self._acquisition_queue[key] = {
                    'symbol': symbol, 'tf': tf,
                    'limit': min(int(limit), 20000),
                    'force': bool(force), 'min_bars': int(min_bars),
                }
                if history:
                    self._acquisition_queue[key]['history'] = True
                self._acquisition_queue_counts['accepted'] += 1
                self._acquisition_queue_last_outcome = 'queued'
        self._start_background_worker()
        return True

    def _background_needs_data(self, request, current=None, *, current_loaded=False):
        if not current_loaded:
            current = self.latest_ohlcv(request['symbol'], request['tf'], request['limit'])
            current = mp.usable_current(current, request['tf'], self._clock_ms(),
                                        self.ttl.get(request['tf'], 300))
        return (request['force'] or current is None or
                (len(current) < min(request['limit'], 20000) and
                 not self._at_floor(request['symbol'], request['tf'], current)) or
                ('quality' in current and not current['quality'].eq('VALID').all()) or
                not self._current_tail(current, request['tf'], min(request['min_bars'], request['limit'])))

    def _drain_background_queue(self):
        try:
            while True:
                with self._acquisition_queue_lock:
                    if self._acquisition_stopping or not self._acquisition_queue:
                        return
                    # At most eight trading jobs between history jobs; a trading
                    # arrival waits behind at most one non-preemptible history job.
                    trading = [k for k,r in self._acquisition_queue.items() if not r.get('history')]
                    history = [k for k,r in self._acquisition_queue.items() if r.get('history')]
                    _key = (trading[0] if trading and (self._trading_burst < 8 or not history)
                            else history[0])
                    request = self._acquisition_queue.pop(_key)
                    self._trading_burst = 0 if request.get('history') else self._trading_burst + 1
                    self._acquisition_inflight = (_key, dict(request))
                outcome = 'failed'
                try:
                    if not self._background_needs_data(request):
                        outcome = 'skipped_fresh'
                        with self._acquisition_queue_lock:
                            self._acquisition_queue_counts['skipped_fresh'] += 1
                            self._acquisition_queue_last_outcome = 'skipped_fresh'
                        continue
                    with self._acquisition_queue_lock:
                        stopping = self._acquisition_stopping
                        if stopping:
                            self._acquisition_queue_counts['cancelled'] += 1
                            self._acquisition_queue_last_outcome = 'shutdown_cancelled'
                            outcome = 'shutdown_cancelled'
                    if stopping:
                        continue
                    self._local.acquiring = True
                    self._local.acquisition_short = False
                    result = self.fetch_ohlcv(request['symbol'], request['tf'],
                                              request['limit'], request['force'],
                                              request['min_bars'])
                    with self._acquisition_queue_lock:
                        outcome = ('completed' if not self._local.acquisition_failure and
                                   (result is not None or self._local.acquisition_short) else 'failed')
                        self._acquisition_queue_counts[outcome] += 1
                        if getattr(self._local, 'persistence_failure', None):
                            outcome = 'persistence_unavailable:' + self._local.persistence_failure
                        elif getattr(self._local, 'acquisition_failure', None):
                            outcome = 'source_unavailable:' + self._local.acquisition_failure
                        qualified = (result is not None and len(result) >= request['min_bars'] and
                                     result['quality'].eq('VALID').all() and
                                     self._current_tail(result, request['tf'], request['min_bars']))
                        if outcome == 'completed' and not qualified:
                            outcome = 'completed_unqualified_history'
                        self._acquisition_queue_last_outcome = outcome
                except Exception as exc:
                    outcome = 'failed:' + type(exc).__name__
                    with self._acquisition_queue_lock:
                        self._acquisition_queue_counts['failed'] += 1
                        self._acquisition_queue_last_outcome = (
                            'failed:' + type(exc).__name__)
                    log.warning('background candles unavailable: %s', type(exc).__name__)
                finally:
                    with self._acquisition_queue_lock:
                        # Bounded diagnostic/cooldown cache, never candle authority.
                        self._acquisition_outcomes[_key] = dict(
                            at_ms=self._clock_ms(), outcome=outcome)
                        self._acquisition_outcomes.move_to_end(_key)
                        while len(self._acquisition_outcomes) > self.BACKGROUND_QUEUE_CAPACITY:
                            self._acquisition_outcomes.popitem(last=False)
                        self._acquisition_inflight = None
                    self._local.acquiring = False
                    conn = getattr(self._local, 'conn', None)
                    if conn is not None:
                        close_quietly(conn, 'background acquisition')
                        del self._local.conn
        finally:
            with self._acquisition_queue_lock:
                self._acquisition_lock.release()
                restart = bool(self._acquisition_queue) and not self._acquisition_stopping
            if restart:
                self._start_background_worker()

    @staticmethod
    def _contiguous_tail(frame, tf, need):
        if frame is None or len(frame) < need:
            return False
        opens = mp.ms(frame['ts'])[-need:]
        return bool((np.diff(opens) == _TF_MS_SHARED[tf]).all())

    def _current_tail(self, frame, tf, need):
        return (self._contiguous_tail(frame, tf, need) and
                self._last_ms(frame) == (self._clock_ms() // self._TF_MS[tf] - 1) * self._TF_MS[tf])

    def prepare_history(self, symbol, tf='4h'):
        """Bounded submission to DA-02; returns an explicit scheduling outcome.

        The caller supplies a rotating cohort, not another backlog. Restart
        reconstructs demand from the candle store and its durable scan cursor.
        """
        key = (norm_symbol(symbol), tf)
        with self._acquisition_queue_lock:
            previous = self._acquisition_outcomes.get(key)
            if previous and 0 <= self._clock_ms() - previous['at_ms'] < self.HISTORY_RETRY_MS:
                return 'retry_cooldown'
        if not self.background_acquisition:
            return 'refused_background_disabled'
        if self.is_dead(key[0]):
            return 'refused_dead_symbol'
        accepted = self._queue_background_request(key[0], tf, self.HISTORY_REQUEST_BARS,
                                                   False, 26, history=True)
        return 'queued_or_coalesced' if accepted else ('refused_shutdown' if self._acquisition_stopping
                                                      else 'deferred_queue_capacity')

    # ── persistent candle store ─────────────────────────────────────────
    #: seconds a statement waits on a lock before "database is locked"
    BUSY_TIMEOUT_S = BUSY_TIMEOUT_S

    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            import sqlite3
            if self._db_path is None:
                from ..core.config import ROOT
                self._db_path = str(ROOT / "data" / "candles.db")
            if self.retrospective is not None:
                from pathlib import Path
                uri = Path(self._db_path).resolve().as_uri() + '?mode=ro'
                conn = sqlite3.connect(uri, uri=True, timeout=self.BUSY_TIMEOUT_S)
                conn.execute('PRAGMA query_only=ON')
                self._local.conn = conn
                return conn
            conn = sqlite3.connect(self._db_path, timeout=self.BUSY_TIMEOUT_S)
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS candles ("
                    "symbol TEXT NOT NULL, tf TEXT NOT NULL, "
                    "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, "
                    "close REAL, volume REAL, taker_buy REAL, "
                    "PRIMARY KEY (symbol, tf, ts))")
                # where the venue's history for a (symbol, tf) begins: once a
                # walk has reached it, a store shorter than the requested
                # limit is complete, not a reason to re-walk from the
                # listing date
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS candle_floor ("
                    "symbol TEXT NOT NULL, tf TEXT NOT NULL, first_ts INTEGER "
                    "NOT NULL, window_version TEXT, PRIMARY KEY (symbol, tf))")
                # Older floor hints may describe a deficit-sized overlapping
                # query, not the source's history boundary. Preserve those
                # rows but recheck through the ordinary bounded acquisition.
                if 'window_version' not in {r[1] for r in conn.execute('PRAGMA table_info(candle_floor)')}:
                    with write_tx(self._local, conn, 'floor schema'):
                        conn.execute('BEGIN IMMEDIATE')
                        # Another opener may have completed this migration
                        # while this connection waited for the writer.
                        if 'window_version' not in {r[1] for r in conn.execute('PRAGMA table_info(candle_floor)')}:
                            conn.execute('ALTER TABLE candle_floor ADD COLUMN window_version TEXT')
                mp.init(conn)
                conn.commit()
            except BaseException:
                close_quietly(conn, "init")  # setup error wins; not cached
                raise
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
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True, errors='coerce')
        for col in COLUMNS[1:]:
            df[col] = pd.to_numeric(df[col].map(lambda x: np.nan if isinstance(x,(bool,np.bool_)) else x), errors="coerce")
        if width > self._TAKER_BUY_IDX:
            df["taker_buy"] = pd.to_numeric(
                [r[self._TAKER_BUY_IDX] for r in rows], errors="coerce")
        else:
            df["taker_buy"] = np.nan
        # taker_buy is allowed to be absent; an incomplete OHLCV bar is not
        df = df.dropna(subset=["ts"]).reset_index(drop=True)
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
            bound=self._instruments.get(norm_symbol(symbol))
            params = {"symbol": bound.venue_symbol if bound is not None and bound.venue==canonical_venue(getattr(ex,"id",None)) else self._venue_symbol(ex, symbol),
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

    #: bar length by timeframe — shared with the live evaluator, which must
    #: agree with the store about where a bar ends
    _TF_MS = _TF_MS_SHARED

    #: how much of the stored tail an incremental refresh re-reads. A bar
    #: frozen while forming used to be the one bar `since` skipped forever,
    #: so damage accumulated one bar per fetch rather than staying at the
    #: tail — live it ran four 4h bars deep. Re-reading a few costs nothing
    #: (same single request) and repairs an accumulated freeze in one pass.
    STORE_OVERLAP_BARS = 4

    def _store_save(self, symbol: str, tf: str, df: pd.DataFrame,
                    now_ms: int | None = None) -> bool:
        """Persist CLOSED bars only. A forming bar is not a bar.

        A partially-formed candle has the right open and a high/low/close
        truncated to whatever has traded so far. Writing it is writing a
        measurement that was never taken — the same fabrication the DSL
        forbids with "missing information is NaN, never a default", one
        layer down. Once written it read as final to every backtest,
        feature and live signal above it.
        """
        self._local.persistence_failure = None
        symbol = norm_symbol(symbol)
        if now_ms is None:
            now_ms = self._clock_ms()
        original = df
        df = closed_bars(df, tf, now_ms)
        try:
            prepared = mp.prepare(original)
            if not len(df):
                with write_tx(self._local, self.db, "partial receipt") as conn:
                    mp.append(conn, self._series_key(symbol, tf), prepared=prepared)
                return True
            # .value/.astype(int64) are ns-based only for datetime64[ns];
            # this repo's pandas keeps ms resolution → convert explicitly
            unit = getattr(df["ts"].dt, "unit", None) or (
                "ns" if str(df["ts"].dtype).startswith("datetime64[ns]") else "ms")
            ms = df["ts"].astype("int64") // {"ns": 10 ** 6, "us": 10 ** 3}.get(unit, 1)
            rows = [(symbol, tf, int(t), float(o), float(h), float(l),
                     float(c), float(v),
                     None if b is None or b != b else float(b))
                    for t, o, h, l, c, v, b in zip(
                        ms, df["open"], df["high"], df["low"],
                        df["close"], df["volume"],
                        df["taker_buy"] if "taker_buy" in df
                        else [None] * len(df))]
            # a failed commit must not leave this thread's connection holding
            # the lock (and the batch) open — see sqlite_tx
            with write_tx(self._local, self.db, "write") as conn:
                mp.append(conn, self._series_key(symbol, tf), prepared=prepared)
                from .sqlite_tx import save_projection
                save_projection(conn, 'candles', ('symbol','tf','ts','open','high','low','close','volume','taker_buy'), ('symbol','tf','ts'), rows)
            return True
        except Exception as e:
            import sqlite3
            self._local.persistence_failure = ('sqlite_busy' if isinstance(e, sqlite3.OperationalError)
                and getattr(e, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                else 'write_failure')
            self._local.acquisition_failure = 'persistence_write_failure'
            log.warning(f"candle store write {symbol} {tf}: {e}")
            return False

    def _merge_save(self, symbol: str, tf: str,
                    stored: Optional[pd.DataFrame],
                    fresh: pd.DataFrame) -> pd.DataFrame:
        # Only the retained read supplies decision frames. Count timestamps,
        # rather than duplicating every value and receipt column before it.
        size = len(fresh) if stored is None else len(pd.Index(stored['ts']).union(fresh['ts']))
        if not self._store_save(symbol, tf, fresh):
            # Never publish an uncommitted receipt or reread after a failed
            # write. Existing qualified observations can still be used.
            return mp.eligible_frame(stored, tf, self._clock_ms(), final=False)
        retained = self.ohlcv_asof(symbol, tf, as_of_ms=self._clock_ms(), limit=size, include_partial=True)
        return retained

    @staticmethod
    def _first_ms(df: pd.DataFrame) -> int:
        return int(pd.to_datetime(df["ts"], utc=True).iloc[0].timestamp()
                   * 1000)

    def _floor(self, symbol: str, tf: str) -> int | None:
        """Where the venue's history for this series begins, once known."""
        try:
            r = self.db.execute(
                "SELECT first_ts FROM candle_floor WHERE symbol=? AND tf=? "
                "AND window_version='full-window.v1'",
                (norm_symbol(symbol), tf)).fetchone()
        except Exception:
            return None
        return int(r[0]) if r else None

    def _at_floor(self, symbol: str, tf: str, stored) -> bool:
        floor = self._floor(symbol, tf)
        return floor is not None and self._first_ms(stored) <= floor

    def _note_floor(self, symbol: str, tf: str, limit: int, df):
        """A walk that came back shorter than `limit` reached the start of the
        venue's history. Remember where, so the next call does not re-walk a
        young symbol from its listing date just because it is younger than
        the limit asked for."""
        if getattr(self._local, 'persistence_failure', None):
            return df  # no second write after the receipt transaction failed
        if df is not None and len(df) and len(df) < limit:
            try:
                with write_tx(self._local, self.db, "floor") as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO candle_floor "
                        "(symbol,tf,first_ts,window_version) VALUES (?,?,?,'full-window.v1')",
                        (norm_symbol(symbol), tf, self._first_ms(df)))
            except Exception as e:
                log.warning(f"candle floor {symbol} {tf}: {e}")
        return df

    def _fetch_paged(self, symbol: str, tf: str, limit: int) -> list:
        """Exchanges cap a single klines request (1000 on binanceusdm);
        walk backwards in pages until `limit` bars are assembled."""
        tf_ms = self._TF_MS[tf]
        now_ms = self._clock_ms()
        cursor = now_ms - limit * tf_ms
        rows: list = []
        for _ in range((limit + 999) // 1000 + 1):
            if cursor >= now_ms:
                break
            batch = self._klines(symbol, tf, since=cursor, limit=1000)
            if not batch:
                break
            rows.extend(batch)
            next_cursor = batch[-1][0] + tf_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(batch) < 1000:
                break
        return rows[-limit:]

    def bind_instrument(self, symbol, instrument_id):
        """Use the existing Registry's typed identity; grants no trade capability."""
        from ..core.instrument_registry import InstrumentId
        if not isinstance(instrument_id,InstrumentId):
            raise TypeError('Registry InstrumentId required')
        self._instruments[norm_symbol(symbol)] = instrument_id

    def _identity(self,symbol):
        iid,source=mp.venue_identity(self.ex,symbol)
        if iid:
            return iid,source
        binding=self._instruments.get(norm_symbol(symbol))
        if binding is not None and binding.venue == canonical_venue(getattr(self.ex,'id',None)):
            return binding.value,mp.venue_source(self.ex)
        return None,None

    @staticmethod
    def _series_key(symbol, tf):
        return "candle:" + norm_symbol(symbol) + ":" + tf

    def _received_frame(self, raw, symbol, tf):
        # One conservative local receipt for the completed retrieval, including
        # pagination. Raw provider rows remain exact and publication is unknown.
        df = self._frame(raw)
        iid, source = self._identity(symbol)
        valid_raw = [r for r in raw if len(r) >= 6 and
                     not pd.isna(pd.to_numeric(r[0], errors='coerce'))]
        return mp.annotate(df, instrument_id=iid, source=source, kind='candle',
                           received_ms=self._clock_ms(), timeframe=tf,
                           request_started_ms=getattr(self._local,'acquisition_started_ms',None),
                           raw=valid_raw if len(valid_raw) == len(df) else None)

    def ohlcv_asof(self, symbol, tf='15m', *, as_of_ms, limit=20000,
                   include_partial=False):
        """Offline historical read; never routes through latest or the network."""
        iid,source=self._identity(symbol)
        df = mp.load(self.db, self._series_key(symbol, tf), as_of_ms=as_of_ms,
                     limit=limit, include_partial=include_partial, instrument_id=iid, source=source)
        if df is not None:
            df.attrs['timeframe'] = tf
        return mp.eligible_frame(df, tf, as_of_ms, final=not include_partial)

    def latest_ohlcv(self, symbol, tf='15m', limit=20000):
        """Explicit current offline view; unsuitable for historical decisions."""
        return self.ohlcv_asof(symbol, tf, as_of_ms=self._clock_ms(), limit=limit)

    def replay_ohlcv(self, symbol, tf='15m', limit=20000):
        """Bar-close replay from revisions actually retained by each bar cut.

        Late backfills and unqualified pre-upgrade rows cannot prove what
        LUFFY knew at an earlier close and are deliberately unavailable.
        """
        at = self._clock_ms()
        df = mp.load(self.db, self._series_key(symbol, tf),
                     as_of_ms=at, limit=limit, replay_tf=tf)
        return mp.eligible_frame(df,tf,at)

    def _retrospective_query(self, symbol, tf, *, start_ms=None, end_ms=None,
                             closed_before_ms=None, metadata_only=False):
        if self.retrospective is None:
            raise ValueError('explicit retrospective dataset required')
        if tf not in self._TF_MS:
            raise ValueError('unsupported retrospective timeframe')
        contract = self.retrospective
        start = contract.get('start_ms', 0) if start_ms is None else mp.cut(start_ms)
        cut = contract['cut_ms'] if closed_before_ms is None else mp.cut(closed_before_ms)
        end = contract.get('end_ms') if end_ms is None else mp.cut(end_ms)
        # Only metadata counting can inspect coverage beyond the discovery cut.
        if not metadata_only:
            cut = min(cut, contract['cut_ms'])
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        args = [norm_symbol(symbol), tf, start, cut - self._TF_MS[tf]]
        predicate = 'symbol=? AND tf=? AND ts>=? AND ts<=?'
        if end is not None:
            predicate += ' AND ts<?'
            args.append(end)
        # The projection is the audited stored observation, never evidence of
        # what was available at the old bar close. Validate without reading
        # post-cut prices. Legacy receipt/source clocks remain unknown.
        if not metadata_only:
            predicate += " AND low>0 AND volume>=0 AND low<=open AND low<=close AND high>=open AND high>=close"
            predicate += " AND abs(open)<1e308 AND abs(high)<1e308 AND abs(low)<1e308 AND abs(close)<1e308 AND abs(volume)<1e308"
        if 'candles' not in tables:
            raise ValueError('retrospective candle projection missing')
        return predicate, args, tables

    def retrospective_coverage(self, symbol, tf='15m', *, start_ms=None,
                                end_ms=None, closed_before_ms=None, metadata_only=False):
        """Coverage through the same bounded projection, with no price loading.

        metadata_only allows protected-era counts but selects only timestamps.
        It is inventory, not evidence that a held-out evaluation is eligible.
        """
        predicate, args, _ = self._retrospective_query(symbol, tf, start_ms=start_ms,
            end_ms=end_ms, closed_before_ms=closed_before_ms, metadata_only=metadata_only)
        n, first, last = self.db.execute(
            'SELECT COUNT(*),MIN(ts),MAX(ts) FROM candles WHERE '+predicate, args).fetchone()
        return {'bars': int(n), 'first_ms': first, 'last_ms': last,
                'dataset_id': self.retrospective['dataset_id'],
                'evidence_scope': 'coverage_metadata' if metadata_only else 'retrospective_observation'}

    def retrospective_ohlcv(self, symbol, tf='15m', limit=200000):
        """Read audited stored OHLCV closed by the explicit historical cut.

        Acquisition clocks are preserved if retained; missing provenance is
        UNKNOWN. This never manufactures historically available evidence.
        """
        if type(limit) is not int or limit <= 0:
            raise ValueError('positive retrospective limit required')
        predicate, args, tables = self._retrospective_query(symbol, tf)
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(candles)')}
        taker = 'taker_buy' if 'taker_buy' in columns else 'NULL AS taker_buy'
        rows = self.db.execute('SELECT ts,open,high,low,close,volume,'+taker+
            ' FROM candles WHERE '+predicate+' ORDER BY ts DESC LIMIT ?', args+[limit]).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows[::-1], columns=COLUMNS+['taker_buy'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        stored_taker = pd.to_numeric(df['taker_buy'], errors='coerce')
        # Old projections may contain an unverified flow proxy. It cannot
        # become a measured aggressor feature without a matching source receipt.
        df['taker_buy'] = np.nan
        for key in mp.META:
            df[key] = None
        df['quality'] = 'UNKNOWN'
        df['observation_quality'] = 'VALID'
        df['bar_state'] = 'FINAL'
        df['event_time_ms'] = mp.ms(df['ts'])
        # Retain only receipts exactly matching this stored projection. A
        # receipt for a different revision must never authenticate these bytes.
        if 'market_revisions' in tables:
            receipts = mp.retrospective_load(self.db, self._series_key(symbol, tf),
                cut_ms=self.retrospective['cut_ms'], tf=tf,
                start_ms=self.retrospective.get('start_ms',0),
                end_ms=self.retrospective.get('end_ms'), limit=limit)
            if receipts is not None:
                # Vectorized exact projection match avoids one pandas mutation
                # per receipt field in multi-year universes.
                matched = receipts.set_index('event_time_ms', drop=False).reindex(df['event_time_ms'])
                matched.index = df.index
                agrees = pd.Series(True, index=df.index)
                for key in COLUMNS[1:]:
                    agrees &= matched[key].eq(df[key])
                for key in mp.META:
                    df.loc[agrees,key] = matched.loc[agrees,key].to_numpy()
                if 'taker_buy' in matched:
                    trustworthy = (agrees & matched['quality'].eq('VALID') &
                        matched['source'].notna() & matched['instrument_id'].notna() &
                        matched['taker_buy'].eq(stored_taker) & stored_taker.ge(0) &
                        stored_taker.le(df['volume']) & np.isfinite(stored_taker))
                    df.loc[trustworthy,'taker_buy'] = stored_taker.loc[trustworthy]
        identity = mp.digest({'dataset_id':self.retrospective['dataset_id'],
            'symbol':norm_symbol(symbol),'tf':tf,'contract':self.retrospective,
            'rows':df.to_json(orient='split',date_unit='ms',date_format='iso')})
        df.attrs.update(retrospective=deepcopy(self.retrospective),
            dataset_id=self.retrospective['dataset_id'], symbol=norm_symbol(symbol), historical_cut_ms=self.retrospective['cut_ms'],
            timeframe=tf,timestamp_semantics='interval_open',read_mode='retrospective',
            evidence_scope='retrospective_observation',input_identity=identity,
            source_limitation='Stored observations; unknown provenance remains unknown; no historical availability claim')
        return df

    def cached_ohlcv(self, symbol, tf='15m', limit=20000, *, as_of_ms=None):
        """Compatibility historical API: replay by default, explicit cut otherwise."""
        if self.retrospective is not None:
            if as_of_ms is not None:
                raise ValueError('retrospective observation cannot establish as-of evidence')
            return self.retrospective_ohlcv(symbol, tf, limit=limit)
        if as_of_ms is not None:
            return self.ohlcv_asof(symbol, tf, as_of_ms=as_of_ms, limit=limit)
        return self.replay_ohlcv(symbol, tf, limit)

    def fetch_ohlcv(self, symbol: str, tf: str = "15m",
                    limit: int = 400, force: bool = False,
                    min_bars: int = 30, *, as_of_ms=None) -> Optional[pd.DataFrame]:
        """Acquire through the existing path and retain one bounded diagnostic.

        Diagnostics contain no frames, exception text or authority. A store
        read failure refuses this series, leaving later symbols serviceable.
        """
        import sqlite3
        # Caller contract errors retain their public API behavior.
        if as_of_ms is not None:
            mp.cut(as_of_ms)
        elif self.retrospective is not None:
            raise RuntimeError('retrospective acquisition forbidden; use cached_ohlcv')
        self._local.acquisition_failure = None
        self._local.persistence_failure = None
        self._local.acquisition_short = False
        self._local.frame_refusal = None
        reason = None
        try:
            result = self._fetch_ohlcv(symbol, tf, limit, force, min_bars, as_of_ms=as_of_ms)
        except (sqlite3.Error, OSError):
            result, reason = None, 'read_failure'
        except (ValueError, KeyError, TypeError):
            result, reason = None, 'invalid'
        if reason is None:
            if result is None or result.empty:
                reason = ('persistence_failure' if self._local.persistence_failure else
                          self._local.frame_refusal if self._local.frame_refusal else
                          'insufficient_history' if self._local.acquisition_short else
                          'source_unavailable' if self._local.acquisition_failure else 'missing')
            elif result['quality'].eq('STALE').any():
                reason = 'stale'
            elif not result['quality'].eq('VALID').all():
                reason = 'invalid'
            else:
                reason = 'valid'
        self._local.fetch_diagnostic = dict(symbol=norm_symbol(symbol), timeframe=tf,
            reason=reason, persistence=self._local.persistence_failure,
            acquisition_failed=bool(self._local.acquisition_failure))
        return result

    def fetch_diagnostics(self):
        """Latest fetch_multi call on this thread; no additional store reads."""
        return deepcopy(getattr(self._local, 'multi_diagnostics', {}))

    def _fetch_ohlcv(self, symbol: str, tf: str = "15m",
                    limit: int = 400, force: bool = False,
                    min_bars: int = 30, *, as_of_ms=None) -> Optional[pd.DataFrame]:
        if as_of_ms is not None:
            return self.ohlcv_asof(symbol, tf, as_of_ms=as_of_ms, limit=limit)
        symbol = norm_symbol(symbol)
        if self.retrospective is not None:
            raise RuntimeError('retrospective acquisition forbidden; use cached_ohlcv')
        if self.background_acquisition and not getattr(self._local, 'acquiring', False):
            current = self.latest_ohlcv(symbol, tf, limit)
            now_ms = self._clock_ms()
            current = mp.usable_current(current, tf, now_ms, self.ttl.get(tf, 300))
            needs_data = (current is None or
                          (len(current) < min(limit, 20000) and
                           not self._at_floor(symbol, tf, current)) or force or
                          ('quality' in current and not current['quality'].eq('VALID').all()) or
                          not self._current_tail(current, tf, min(min_bars, limit)))
            if needs_data:
                self._queue_background_request(symbol, tf, limit, force, min_bars)
            if current is not None and len(current) < min_bars:
                self._local.acquisition_short = True
                self._local.frame_refusal = ('stale' if current['quality'].eq('STALE').any() else
                    'invalid' if not current['quality'].eq('VALID').all() else 'insufficient_history')
            return current if current is not None and len(current) >= min_bars else None
        ck = (symbol, tf)
        tf_ms = self._TF_MS.get(tf, 900_000)
        now_ms = self._clock_ms()

        if self.is_dead(symbol):
            # delisted / never-listed symbol: serve whatever is stored and do
            # not touch the exchange. Three such symbols (DRAM, BZ, MRVL) were
            # producing 31,566 of 32,569 log warnings — 97% of all noise — by
            # being refetched every single cycle forever.
            stored = self._store_load(symbol, tf, limit)
            if stored is not None and len(stored) >= min_bars:
                return mp.usable_current(self.latest_ohlcv(symbol, tf, limit), tf, now_ms, self.ttl.get(tf, 300))
            return None

        def finish(df: pd.DataFrame) -> pd.DataFrame:
            at = self._clock_ms()
            safe = mp.usable_current(df, tf, at, self.ttl.get(tf, 300))
            self._cache[ck] = (at / 1000, safe)
            self._cache.move_to_end(ck)
            while len(self._cache) > self.MAX_CACHE_SERIES:
                self._cache.popitem(last=False)
            self._local.acquisition_short = safe is not None and len(safe) < min_bars
            return None if self._local.acquisition_short else safe

        hit = self._cache.get(ck)
        iid,source=self._identity(symbol)
        if hit and hit[1] is not None and ('instrument_id' not in hit[1] or
                not hit[1]['instrument_id'].eq(iid).all() or not hit[1]['source'].eq(source).all()):
            hit=None
        if (hit and hit[1] is not None and len(hit[1]) >= min_bars and not force
                and 0 <= now_ms / 1000 - hit[0] < self.ttl.get(tf, 300)
                and (len(hit[1]) >= limit or self._at_floor(symbol, tf, hit[1]))):
            current = mp.usable_current(hit[1], tf, now_ms, self.ttl.get(tf, 300))
            if current is not None and current['quality'].eq('VALID').all():
                return current

        stored = self.latest_ohlcv(symbol, tf, limit)
        qualified = stored
        if stored is None:
            stored = self._store_load(symbol, tf, limit)  # inventory only, never qualified
        # "full" = the window asked for is stored, OR the store already
        # reaches back to where the venue's history begins (a symbol younger
        # than `limit` can never hold `limit` bars, and must not be re-walked
        # from its listing date on every call for want of them)
        have_full = stored is not None and len(stored) > 0 and (
            len(stored) >= limit or self._at_floor(symbol, tf, stored))
        fresh_tail = (stored is not None and
                      now_ms - (self._last_ms(stored) + tf_ms) <=
                      tf_ms + self.ttl.get(tf, 300) * 1000)
        if (have_full and fresh_tail and qualified is not None and not force
                and self._current_tail(qualified, tf, min(min_bars, limit))
                and mp.usable_current(qualified, tf, now_ms, self.ttl.get(tf, 300))['quality'].eq('VALID').all()):
            return finish(stored)

        try:
            # force means "refresh now", not "forget the store": history
            self._local.acquisition_started_ms = self._clock_ms()
            self._local.acquisition_failure = None
            # already on disk is extended from its tail, never re-downloaded
            # Legacy projection rows are inventory only. Reacquire the bounded
            # requested window when its retained provenance is incomplete or
            # has holes; refreshing a tail cannot qualify an interior hole.
            if (stored is not None and len(stored) > 0 and
                    (qualified is None or not qualified['quality'].eq('VALID').all() or
                     not self._contiguous_tail(qualified, tf, min(min_bars, limit)))):
                raw = (self._fetch_paged(symbol, tf, limit) if limit > 1000
                       else self._klines(symbol, tf,
                                         since=(now_ms // tf_ms - limit) * tf_ms,
                                         limit=limit))
                new = self._received_frame(raw or [], symbol, tf)
                return finish(self._merge_save(symbol, tf, stored, new)) if len(new) else finish(qualified)
            if stored is not None and len(stored) > 0:
                # extend/refresh whatever is stored instead of redownloading
                last = self._last_ms(stored)
                if not have_full:
                    raw = (self._fetch_paged(symbol, tf, limit)
                           if limit > 1000 else
                           self._klines(
                               symbol, tf,
                               # Include the current requested window. A
                               # deficit-sized interval overlaps the
                               # stored tail and can falsely set a floor.
                               since=(now_ms // tf_ms - limit) * tf_ms,
                               limit=1000))
                else:   # full window stored → only the tail can be stale
                    # Start ON the stored tail, not past it. `last + tf_ms`
                    # meant the newest stored bar was the one bar never
                    # requested again, so a bar written while forming stayed
                    # frozen at its partial values for good.
                    resume = int(last) - self.STORE_OVERLAP_BARS * tf_ms
                    missing = (now_ms - resume) // tf_ms
                    raw = (self._klines(symbol, tf, since=resume, limit=1000)
                           if missing < 950 else
                           self._fetch_paged(symbol, tf, limit))
                new = self._received_frame(raw or [], symbol, tf)
                if new.empty:
                    return finish(stored)
                return finish(self._note_floor(symbol, tf, limit,
                                           self._merge_save(symbol, tf,
                                                            stored, new)))
            # cold symbol or force refresh
            raw = (self._fetch_paged(symbol, tf, limit) if limit > 1000
                   else self._klines(symbol, tf, limit=limit))
            if not raw:
                self._local.acquisition_failure = 'empty_provider_response'
                return finish(stored) if stored is not None else None
            new = self._received_frame(raw, symbol, tf)
            return finish(self._note_floor(symbol, tf, limit,
                                           self._merge_save(symbol, tf,
                                                            stored, new)))
        except Exception as e:
            self._local.acquisition_failure = type(e).__name__
            if self._note_dead(symbol, e):
                log.warning(f"ohlcv {symbol} {tf}: {e} — symbol marked dead, "
                            f"suppressing further fetches")
            else:
                log.warning(f"ohlcv {symbol} {tf}: {e}")
            if stored is not None and len(stored) >= min_bars:
                return finish(stored)
            # A retained short history stays evidence, but a failed refresh
            # cannot bypass this caller's minimum-bar requirement.
            return finish(hit[1]) if hit and hit[0] * 1000 <= now_ms else None

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
        diagnostics = {}
        for tf in dict.fromkeys(tfs):
            df = self.fetch_ohlcv(symbol, tf, limit)
            last = getattr(self._local, 'fetch_diagnostic', {})
            if last.get('symbol') == norm_symbol(symbol) and last.get('timeframe') == tf:
                diagnostics[tf] = dict(last)
            if df is not None:
                out[tf] = df
        self._local.multi_diagnostics = diagnostics
        return out

    def price(self, symbol: str) -> float | None:
        """Explicit latest valuation, validated by the ticker receipt contract."""
        return self.ticker_quote(symbol)['price']

    def ticker_quote(self, symbol: str) -> dict:
        """The same ticker valuation as price() (last, else close), with its
        identity and times. Not an exchange mark price.

        {symbol, price, field: "last"|"close"|None, source_ms (the ticker's own
        timestamp, None if the venue sent none), attempt_started_at (epoch s,
        taken immediately before the request), received_at (epoch s, taken
        immediately after a successful return; None when nothing was received),
        error}. A receipt earlier than its attempt (local clock stepped back)
        is impossible: the quote fails closed with no price."""
        attempt = time.time()
        try:
            t = self.ex.fetch_ticker(symbol)
        except Exception as e:
            log.warning(f"ticker {symbol}: {e}")
            return {"symbol": symbol, "price": None, "field": None, "source_ms": None,
                    "attempt_started_at": attempt, "received_at": None,
                    "error": f"ticker_fetch_failed:{type(e).__name__}"}
        received = time.time()
        t = t if isinstance(t, dict) else {}
        field = "last" if t.get("last") else "close" if t.get("close") else None
        try:
            px = float(t[field]) if field else None
        except (TypeError, ValueError):
            px = None
        ts = t.get("timestamp")
        error = None if px and np.isfinite(px) and px > 0 else 'ticker_price_missing'
        if error:
            px = None
        if ts is not None and (type(ts) not in (int,float) or not np.isfinite(ts) or ts > received*1000):
            px,error = None,'ticker_time_invalid'
        iid,source=mp.venue_identity(self.ex,symbol)
        if not iid or not source:
            px,error=None,'ticker_identity_unknown'
        if received < attempt:
            px, error = None, "quote_receipt_before_attempt"
        return {"symbol": symbol, "price": px, "field": field,
                'instrument_id':iid,'source':source,'available_at_ms':int(received*1000),
                'quality':'VALID' if error is None else 'INVALID',
                'content_hash':mp.digest(t), 'raw':t,
                "source_ms": ts if isinstance(ts, (int, float))
                and not isinstance(ts, bool) else None,
                "attempt_started_at": attempt, "received_at": received,
                "error": error}


#: symbols proven untradeable at runtime; shared process-wide so a rejection
#: in the executor immediately removes the symbol from the next scan.
_RUNTIME_BLACKLIST: set[str] = set()

#: exchange errors that mean "this account may never trade this symbol"
_UNTRADEABLE_MARKERS = ("-4411", "tradfi-perps", "tradfi perps")


#: set by Universe.attach_journal: durable record of venue refusals, so a
#: restart does not forget them. A failing sink never blocks the exclusion.
_UNTRADEABLE_SINK = None


def mark_untradeable(symbol: str, reason: str = "") -> bool:
    """Blacklist a symbol (durably once a journal is attached). True if newly added."""
    first = symbol not in _RUNTIME_BLACKLIST
    _RUNTIME_BLACKLIST.add(symbol)
    if first:
        log.warning(f"universe: {symbol} marked untradeable ({reason[:120]})")
    sink = _UNTRADEABLE_SINK
    if sink is not None:
        try:
            sink(symbol, reason)
        except Exception as e:                            # noqa: BLE001
            log.warning(f"universe: untradeable record failed for {symbol}: {type(e).__name__}")
    return first


def is_untradeable_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _UNTRADEABLE_MARKERS)


class Universe:
    """Majors always included + top-N alts by quote volume, rescanned periodically."""

    def __init__(self, cfg: dict, exchange=None):
        self.cfg = cfg["universe"]
        self.majors = list(self.cfg["majors"])
        # This process first observed the configuration now. A declaration
        # loaded on restart cannot establish membership at an earlier cut.
        configured_at = int(time.time()*1000)
        self._configured_membership = {
            sym: dict(source='config.universe.majors', symbol=sym, quality='VALID',
                      observed_at_ms=configured_at, available_at_ms=configured_at)
            for sym in self.majors}
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
        self._selection_receipt = None
        self._listing_cache: dict[str, dict] = {}
        #: symbol -> last seen 24h quote volume, for strategy liquidity floors
        self._volume_receipts = {}
        self._volumes: dict[str, float] = {}

    @property
    def environment(self) -> str:
        """The trading environment this universe's account refusals belong to."""
        from ..core.config import Env
        return "demo" if Env.get("BINANCE_DEMO", "true").lower() in ("1", "true", "yes") else "production"

    def attach_journal(self, journal) -> None:
        """Make universe history durable: restore this environment's recorded
        venue refusals, and record future refusals and every selection revision.
        Observation only; nothing here grants or widens trading authority."""
        global _UNTRADEABLE_SINK
        from . import registry_store as store
        self._journal = journal
        env = self.environment
        known = store.load_untradeable(journal, env)
        _RUNTIME_BLACKLIST.update(known)
        self.blacklist |= set(known)
        _UNTRADEABLE_SINK = lambda sym, reason: store.record_untradeable(  # noqa: E731
            journal, env, sym, reason)

    def _persist_revision(self, receipt: dict) -> None:
        journal = getattr(self, "_journal", None)
        if journal is None:
            return
        try:
            from . import registry_store as store
            store.record_universe_revision(journal, receipt, self.environment)
        except Exception as e:                            # noqa: BLE001
            log.warning(f"universe: revision record failed: {type(e).__name__}")

    @property
    def ex(self):
        """Exchange used for market-data scans (production public on demo)."""
        if self.data_ex is not None:
            return self.data_ex
        if self._ex is None:
            self._ex = make_exchange()
        return self._ex

    def symbols(self, *, as_of_ms=None) -> list[str]:
        at = int(time.time()*1000) if as_of_ms is None else mp.cut(as_of_ms)
        if self.enabled and (at < self._last_scan*1000 or at/1000 - self._last_scan > self.rescan_hours * 3600):
            self._rescan()
            if as_of_ms is None:
                # The current call acquired this selection. Its receipts were
                # not yet available at the pre-request cut; never backdate them.
                at = int(time.time()*1000)
        receipt = self.membership_receipts(as_of_ms=at)
        return [s for s in self.majors if s in receipt] + [s for s in self._alts if s not in self.majors and s in receipt]

    def membership_receipts(self, *, as_of_ms):
        """Configuration members and the exact eligible scan result, never backdated."""
        import copy
        at = mp.cut(as_of_ms)
        out = {sym: copy.deepcopy(r) for sym, r in getattr(self, '_configured_membership', {}).items()
               if sym in self.majors and r['available_at_ms'] <= at and r['observed_at_ms'] <= at}
        r = self._selection_receipt
        if (r and r['quality'] == 'VALID' and r['available_at_ms'] <= at
                and r['observed_at_ms'] <= at and self._last_scan*1000 <= at):
            out.update({sym: copy.deepcopy(r) for sym in self._alts if sym in r['members']})
        return out

    def volumes(self, *, as_of_ms=None) -> dict:
        """{symbol: 24h quote volume} as of the last scan.

        A spec's `min_volume_usdt` needs a reading to test against; a symbol
        absent here has no reading and must not pass a liquidity floor.
        """
        if not self._volumes and self.enabled:
            self._rescan()
        now_ms=int(time.time()*1000) if as_of_ms is None else mp.cut(as_of_ms)
        return {sym:value for sym,value in self._volumes.items()
                if (r := self._volume_receipts.get(sym)) and r['quality']=='VALID'
                and 0 <= now_ms-r['available_at_ms'] <= self.rescan_hours*3600*1000}

    def _rescan(self) -> None:
        import uuid
        started = int(time.time()*1000)
        try:
            tickers = self.ex.fetch_tickers()
            if not isinstance(tickers, dict) or not tickers:
                return  # Missing response is not proof of an empty universe.
            if int(time.time()*1000) < started:
                return  # Refuse before publishing any component receipts.
        except Exception as e:
            log.warning(f"universe rescan failed: {e}")
            return
        self.blacklist |= set(_RUNTIME_BLACKLIST)
        scored = []
        volume_receipts = {}
        #: symbol -> reason code, for every USDT ticker the scan did not select
        exclusions = {}
        for sym_raw, t in tickers.items():
            sym = norm_symbol(sym_raw)
            if not sym.endswith("/USDT"):
                continue
            if sym in self.blacklist:
                exclusions[sym] = ("runtime_untradeable" if sym in _RUNTIME_BLACKLIST
                                   else "blacklisted")
                continue
            iid, source = mp.venue_identity(self.ex, sym_raw)
            quote_vol = pd.to_numeric(t.get('quoteVolume'),errors='coerce')
            last = pd.to_numeric(t.get('last'),errors='coerce')
            received=int(time.time()*1000)
            event=t.get('timestamp')
            if (received < started or not iid or not source or not np.isfinite(quote_vol) or not np.isfinite(last)
                    or quote_vol < 0 or last <= 0 or event is not None and
                    (type(event) not in (int,float) or not np.isfinite(event) or event > received)):
                exclusions[sym] = "ticker_invalid_or_identity_unverified"
                continue
            self._volumes[sym] = float(quote_vol)
            self._volume_receipts[sym] = dict(instrument_id=iid,source=source,
                event_time_ms=event,observed_at_ms=received,available_at_ms=received,
                request_started_ms=started,content_hash=mp.digest(t),raw=t,quality='VALID')
            volume_receipts[sym] = self._volume_receipts[sym]
            if sym in self.majors:
                continue
            if quote_vol < self.min_vol:
                exclusions[sym] = "below_min_volume"
                continue
            if last < self.min_price:
                exclusions[sym] = "below_min_price"
                continue
            if not self._old_enough(sym):
                exclusions[sym] = "listing_too_new_or_unverified"
                continue
            scored.append((quote_vol, sym))
            self._volumes[sym] = quote_vol
        scored.sort(reverse=True)
        received = int(time.time()*1000)
        if received < started or not mp.venue_source(self.ex):
            return
        if self._selection_receipt and received < self._selection_receipt['available_at_ms']:
            return  # An older cut must not replace a valid later result.
        members = [s for _, s in scored[:self.top_n]]
        for _, sym in scored[self.top_n:]:
            exclusions[sym] = "below_top_n_rank"
        required_receipts = list(volume_receipts.values()) + [self._listing_cache[sym] for sym in members]
        if any(received < max(r['available_at_ms'], r['observed_at_ms']) for r in required_receipts):
            return  # A reversed local clock cannot backdate required selection inputs.
        body = dict(source=mp.venue_source(self.ex), members=members,
                    request_id=uuid.uuid4().hex, request_started_ms=started,
                    observed_at_ms=received, available_at_ms=received,
                    content_hash=mp.digest(tickers), raw=tickers, quality='VALID',
                    selection_version='universe.volume-age.v1',
                    selection_config=dict(top_n=self.top_n, min_vol=self.min_vol,
                        min_price=self.min_price, min_age_days=self.min_age_days,
                        blacklist=sorted(self.blacklist)),
                    volume_receipts=volume_receipts,
                    exclusions=dict(sorted(exclusions.items())),
                    # configured majors stay observable, but absence from the venue
                    # scan is recorded and never makes anything tradable
                    majors_unobserved=sorted(m for m in self.majors
                                             if m not in {norm_symbol(k) for k in tickers}),
                    listing_receipts={sym:self._listing_cache[sym] for sym in members})
        body['revision_id'] = mp.digest(body)
        self._selection_receipt = body
        self._persist_revision(body)
        self._alts = members
        self._last_scan = received/1000
        log.info(f"universe: {len(self.majors) + len(self._alts)} symbols "
                 f"(majors {len(self.majors)} + alts {len(self._alts)})")

    def _old_enough(self, symbol: str) -> bool:
        """Listing age ≥ min_age_days via first 1d candle (cached)."""
        now = int(time.time()*1000)
        iid, source = mp.venue_identity(self.ex, symbol)
        receipt = self._listing_cache.get(symbol)
        if (not receipt or receipt['available_at_ms'] > now or
                receipt['instrument_id'] != iid or receipt['source'] != source):
            try:
                raw = self.ex.fetch_ohlcv(symbol, "1d", since=0, limit=1)
                received = int(time.time()*1000)
                first_ms = raw[0][0] if raw else None
                if (not iid or not source or received < now or
                        type(first_ms) not in (int,float) or not np.isfinite(first_ms)
                        or first_ms < 0 or first_ms > received):
                    return False
                receipt = dict(instrument_id=iid, source=source, available_at_ms=received,
                               observed_at_ms=received, event_time_ms=first_ms,
                               raw=raw, content_hash=mp.digest(raw), quality='VALID')
            except Exception:
                return False
            self._listing_cache[symbol] = receipt
        first_ms = receipt['event_time_ms']
        now = int(time.time()*1000)
        if receipt['available_at_ms'] > now or not first_ms or first_ms > now - 86_400_000 * 5:
            # no history, or "first" candle is recent (since=0 unsupported)
            return False
        age_days = (now - first_ms) / 86_400_000
        return age_days >= self.min_age_days
