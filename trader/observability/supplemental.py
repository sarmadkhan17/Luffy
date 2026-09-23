"""One observation-only Attention symbol outside the strategy scan.

A data path, not an eligibility path: the symbol is fetched by one
`IsolatedSource` request (a killable child process, never the shared
DataFeed, its cache or its candle store), bounded to the bars the Attention
candle evaluator reads, and handed to Attention capture only. It never enters the
trading Universe (so the Universe's listing-age rule does not apply), strategy
frames, the Orchestrator, Risk or Execution, and says nothing about whether
the account may trade or short it.

Statuses reuse the evaluator's candle-window vocabulary (`stale`, `warmup`,
`gap`, `missing`) and add `usable` and `error`. Nothing is filled: a window
that is not complete is reported, never repaired.
"""
from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from trader.cognition.attention import CognitionConfig
from trader.cognition.contracts import TF_MS
from trader.core.types import closed_bars

USABLE, WARMUP, MISSING, STALE, GAP, ERROR = (
    "usable", "warmup", "missing", "stale", "gap", "error")
STATUSES = (USABLE, WARMUP, MISSING, STALE, GAP, ERROR)
SOURCE = "attention.supplemental"
#: Small fixed floor for the closed Attention window plus a forming bar.
MIN_REQUEST_BARS = 30
LIMITATIONS = (
    "Observation only: no trade, strategy, shortability or account eligibility is implied.",
    "account_eligibility remains UNKNOWN.",
    "Bars are closed by the cut; venue availability before the fetch is not proven.",
)
#: production public market data, the venue DataFeed reads bars from
KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
CHILD = Path(__file__).with_name("_supplemental_child.py")
DEFAULT_TIMEOUT_S = 2.0
MAX_TIMEOUT_S = 30.0
#: after SIGTERM, how long the child gets before SIGKILL
TERM_S = 0.2
#: after SIGKILL, how long the cycle waits to reap the child
REAP_S = 1.0
MAX_BYTES = 1_000_000
#: room for the IPC envelope around a MAX_BYTES body
ENVELOPE_BYTES = 4096
#: tolerance when checking the child's wall-clock request bounds
CLOCK_SLACK_MS = 1000
#: Binance kline row width
KLINE_WIDTH = 12
_META = ("symbol", "interval", "limit", "request_start_ms", "request_end_ms")
_ERROR_TOKEN = re.compile(r"[a-z0-9_]{1,64}")
#: process-wide single flight: one fetch at a time, and a child whose kill
#: could not be verified blocks every later fetch from every source
_GATE = threading.Lock()
#: the child inherits only what an HTTPS request needs; never credentials
_CHILD_ENV = ("PATH", "SSL_CERT_FILE", "SSL_CERT_DIR")


def required_bars(cfg: CognitionConfig | None = None) -> int:
    """Consecutive closed bars the Attention candle evaluator reads."""
    cfg = cfg or CognitionConfig()
    return cfg.window + cfg.short + 1


def one_symbol(raw) -> str | None:
    """None, one symbol string, or a one-item list. More than one is refused:
    this slice has no rotation or fairness policy to choose between them."""
    if raw is None:
        return None
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise ValueError("supplemental attention symbol must be a string")
    if not items:
        return None
    if len(items) > 1:
        raise ValueError("at most one supplemental attention symbol is supported")
    sym = items[0]
    if not isinstance(sym, str) or not sym.strip():
        raise ValueError("supplemental attention symbol must be a non-empty string")
    return sym.strip()


@dataclass(frozen=True)
class SupplementalResult:
    symbol: str
    timeframe: str
    as_of_ms: int
    status: str
    reason: str
    need: int
    requested_limit: int
    bars_present: int
    first_open_ms: int
    anchor_open_ms: int
    #: detached copy of exactly the `need` closed bars; only when usable
    frame: object = field(default=None, repr=False, compare=False)
    role: str = "observation_only"
    account_eligibility: str = "UNKNOWN"
    source: str = SOURCE
    limitations: tuple = LIMITATIONS

    @property
    def usable(self) -> bool:
        return self.status == USABLE

    def summary(self) -> dict:
        """Primitive, frame-free description for telemetry."""
        return {"symbol": self.symbol, "timeframe": self.timeframe,
                "as_of_ms": self.as_of_ms, "status": self.status,
                "reason": self.reason, "need": self.need,
                "requested_limit": self.requested_limit,
                "bars_present": self.bars_present,
                "first_open_ms": self.first_open_ms,
                "anchor_open_ms": self.anchor_open_ms, "role": self.role,
                "account_eligibility": self.account_eligibility,
                "source": self.source, "limitations": list(self.limitations)}


def _open_ms(df):
    ts = df["ts"]
    unit = getattr(ts.dt, "unit", None) or "ms"
    return (ts.astype("int64") // {"ns": 10 ** 6, "us": 10 ** 3}.get(unit, 1)).tolist()


def fetch(source, symbol, timeframe: str, as_of_ms: int,
          need: int | None = None) -> SupplementalResult:
    """One bounded attempt for one symbol, cut at `as_of_ms`.

    The kernel passes an IsolatedSource. The source returns only after its
    isolated child has exited and the parent has validated the primitive rows.
    """
    if not isinstance(source, IsolatedSource):
        raise TypeError("supplemental acquisition requires IsolatedSource")
    symbol = one_symbol(symbol)
    if symbol is None:
        raise ValueError("a supplemental attention symbol is required")
    if timeframe not in TF_MS:
        raise ValueError("unsupported attention timeframe")
    if isinstance(as_of_ms, bool) or not isinstance(as_of_ms, int):
        raise ValueError("as_of_ms must be an explicit integer cut")
    need = required_bars() if need is None else int(need)
    if need < 1 or need + 2 > 1000:
        raise ValueError("supplemental window exceeds one Binance request")
    tf_ms = TF_MS[timeframe]
    limit = max(need + 2, MIN_REQUEST_BARS)
    # Same window the evaluator reads: `need` opens ending at the newest
    # bar closed by the cut.
    anchor = (as_of_ms // tf_ms) * tf_ms - tf_ms
    first = anchor - (need - 1) * tf_ms

    def result(status, reason, present=0, frame=None):
        return SupplementalResult(symbol, timeframe, as_of_ms, status, reason,
                                  need, limit, present, first, anchor, frame)

    try:
        # A young symbol's short history is reported as warmup.
        df = source.acquire(symbol, timeframe, limit=limit)
    except SupplementalFetchError as exc:
        return result(ERROR, exc.reason)
    except Exception as exc:
        return result(ERROR, f"fetch_failed:{type(exc).__name__}")
    if df is None or not len(df):
        return result(MISSING, "no_data")
    try:
        closed = closed_bars(df, timeframe, as_of_ms)
        opens = _open_ms(closed) if len(closed) else []
        if any(b <= a for a, b in zip(opens, opens[1:])):
            return result(ERROR, "unordered_frame")
        wanted = set(range(first, anchor + 1, tf_ms))
        present = [i for i, ms in enumerate(opens) if ms in wanted]
        have = {opens[i] for i in present}
        if anchor not in have:
            return result(STALE if have else MISSING,
                          "anchor_bar_absent" if have else "no_bar_in_window",
                          len(have))
        if len(have) < need:
            lo = min(have)
            contiguous = all(t in have for t in range(lo, anchor + 1, tf_ms))
            return (result(WARMUP, "insufficient_history", len(have)) if contiguous
                    else result(GAP, "hole_in_window", len(have)))
        window = closed.iloc[present].copy(deep=True).reset_index(drop=True)
        return result(USABLE, "complete_window", len(have), window)
    except Exception as exc:
        return result(ERROR, f"invalid_frame:{type(exc).__name__}")


class SupplementalFetchError(Exception):
    """A bounded acquisition failure with an explicit telemetry reason."""
    reason = "fetch_failed"

    def __init__(self, reason: str | None = None):
        super().__init__(reason or self.reason)
        if reason:
            self.reason = reason


class SupplementalTimeout(SupplementalFetchError):
    reason = "fetch_timeout"


class SupplementalInFlight(SupplementalFetchError):
    reason = "fetch_in_flight"


def _venue_symbol(symbol: str) -> str:
    """Validate a linear USDT symbol without consulting CCXT markets."""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{1,24}/USDT(?::USDT)?", symbol):
        raise ValueError("unsupported supplemental venue symbol")
    return symbol.split(":")[0].replace("/", "")


def _is_int(v) -> bool:
    return type(v) is int


def _decimal(v) -> float:
    """Binance sends prices/volumes as decimal strings; anything else is invalid."""
    if not isinstance(v, str):
        raise ValueError
    x = float(v)
    if not math.isfinite(x):
        raise ValueError
    return x


def _parse(out: bytes, *, symbol: str, interval: str, limit: int,
           started_ms: int, ended_ms: int) -> pd.DataFrame:
    """Validated child message -> ts/OHLCV frame. Any deviation fails the whole
    response (`invalid_response:*`); nothing is dropped, coerced or filled."""
    def bad(why):
        return SupplementalFetchError(f"invalid_response:{why}")

    if len(out) > MAX_BYTES + ENVELOPE_BYTES:
        raise bad("too_large")
    try:
        msg = json.loads(out, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise bad("not_json") from None
    if not isinstance(msg, dict):
        raise bad("not_object")
    extra = set(msg) - set(_META)
    if not set(_META) <= set(msg) or extra not in ({"rows"}, {"error"}):
        raise bad("keys")
    if not (msg["symbol"] == symbol and msg["interval"] == interval
            and _is_int(msg["limit"]) and msg["limit"] == limit):
        raise bad("identity")
    t0, t1 = msg["request_start_ms"], msg["request_end_ms"]
    if not (_is_int(t0) and _is_int(t1)
            and started_ms - CLOCK_SLACK_MS <= t0 <= t1 <= ended_ms + CLOCK_SLACK_MS):
        raise bad("request_time")
    if "error" in msg:
        err = msg["error"]
        if not isinstance(err, str) or not _ERROR_TOKEN.fullmatch(err):
            raise bad("error_token")
        raise SupplementalFetchError(f"fetch_failed:{err}")
    rows, tf_ms = msg["rows"], TF_MS[interval]
    if not isinstance(rows, list) or len(rows) > limit:
        raise bad("row_count")
    parsed, prev = [], None
    for r in rows:
        if not isinstance(r, list) or len(r) != KLINE_WIDTH:
            raise bad("row_schema")
        ts, close_ts = r[0], r[6]
        if not (_is_int(ts) and _is_int(close_ts) and ts % tf_ms == 0
                and close_ts == ts + tf_ms - 1 and ts <= t1
                and (prev is None or ts > prev)):
            raise bad("row_time")
        try:
            o, h, lo, c, v = (_decimal(x) for x in r[1:6])
            quote_v, taker_v, taker_quote, ignored = (
                _decimal(x) for x in (r[7], r[9], r[10], r[11]))
        except ValueError:
            raise bad("row_value") from None
        trades = r[8]
        if not (min(o, h, lo, c) > 0 and v >= 0
                and h >= max(o, c, lo) and lo <= min(o, c)
                and quote_v >= 0 and _is_int(trades) and trades >= 0
                and 0 <= taker_v <= v and 0 <= taker_quote <= quote_v
                and ignored == 0):
            raise bad("row_value")
        parsed.append((ts, o, h, lo, c, v))
        prev = ts
    df = pd.DataFrame(parsed, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    return df


class IsolatedSource:
    """One public klines request in a child process killed at a hard deadline.

    The deadline starts before spawn and covers the child through IPC. Normal
    termination cleanup has a short extra allowance. The child shares no memory with the kernel and
    is given no DataFeed, cache, store path or credentials; output arriving
    after the deadline is never read. At most one child exists: if a killed
    child has not yet exited, another call refuses (`fetch_in_flight`).
    """

    def __init__(self, timeout_s=DEFAULT_TIMEOUT_S, url: str = KLINES_URL,
                 child: Path = CHILD):
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not 0 < timeout_s <= MAX_TIMEOUT_S):
            raise ValueError(f"supplemental timeout must be in (0, {MAX_TIMEOUT_S}] seconds")
        self.timeout_s = float(timeout_s)
        self.url = url
        self.child = Path(child)
        self._proc = None

    @property
    def bound_s(self) -> float:
        """Normal cleanup budget; OS reaping can exceed it in an unkillable state."""
        return self.timeout_s + TERM_S + REAP_S

    def _stop_and_join(self, proc) -> None:
        """Terminate, then kill if needed; never return with a live child."""
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            if proc.poll() is None:
                proc.terminate()
        try:
            proc.wait(timeout=TERM_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                if proc.poll() is None:
                    proc.kill()
            # A killed child must be reaped before control returns to trading.
            # A kernel blocked in an uninterruptible OS state cannot satisfy
            # both a finite cleanup bound and a no-live-child guarantee.
            try:
                proc.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                proc.wait()
        if proc.poll() is None:
            raise RuntimeError("supplemental child was not reaped")
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    def acquire(self, symbol, tf, limit):
        if not _GATE.acquire(blocking=False):
            raise SupplementalInFlight()
        proc = None
        try:
            if tf not in TF_MS or not isinstance(limit, int) or not 1 <= limit <= 1000:
                raise SupplementalFetchError("invalid_request")
            venue_symbol = _venue_symbol(symbol)
            deadline = time.monotonic() + self.timeout_s
            started_ms = time.time_ns() // 1_000_000
            env = {k: os.environ[k] for k in _CHILD_ENV if k in os.environ}
            proc = subprocess.Popen(
                [sys.executable, "-I", str(self.child), self.url, venue_symbol,
                 tf, str(limit), str(self.timeout_s), str(MAX_BYTES)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env, close_fds=True,
                start_new_session=True)
            self._proc = proc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SupplementalTimeout()
            try:
                out, _ = proc.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise SupplementalTimeout() from None
            if time.monotonic() >= deadline:
                raise SupplementalTimeout()
            ended_ms = time.time_ns() // 1_000_000
            if proc.returncode != 0:
                raise SupplementalFetchError(f"fetch_failed:child_exit_{proc.returncode}")
            return _parse(out, symbol=venue_symbol, interval=tf, limit=limit,
                          started_ms=started_ms, ended_ms=ended_ms)
        finally:
            if proc is not None:
                if proc.poll() is None:
                    self._stop_and_join(proc)
                else:
                    proc.wait()
                    for stream in (proc.stdout, proc.stderr):
                        if stream is not None:
                            stream.close()
                self._proc = None
            _GATE.release()
