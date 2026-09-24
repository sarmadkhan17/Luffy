"""Bounded read-only capture of recorded derivatives positioning for Attention.

Runs inside the disposable Attention worker child, never on the trading-thread
producer. Reads only the local `derivs` table written by the kernel
derivatives recorder; no network, no trader.data import, no writes.

The capture reads the values stored at read time with `ts <= as_of`. The
recorder can overwrite a stored (symbol, series, ts) value, so nothing here
claims those values were stored at `as_of`: the exact values and timestamps
read are frozen into the scan input, and replay consumes that input only.
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path

from trader.cognition.attention import POSITIONING_REFERENCE
from trader.cognition.contracts import POSITIONING_SERIES

CAPTURE_SCHEMA = "attention-positioning-capture.v1"
#: rows read per (symbol, series): the anchor plus its reference window
LIMIT = POSITIONING_REFERENCE + 1
#: whole-read deadline; well inside the worker child's timeout
DEADLINE_SECONDS = 1.0
MAX_SYMBOLS = 128
_QUERY = ("SELECT ts, value FROM derivs WHERE symbol=? AND series=? AND ts<=? "
          "ORDER BY ts DESC LIMIT ?")


def _wall_ms() -> int:
    return int(time.time() * 1000)


def _value(v):
    """Stored value, or None when it is not a finite number (the contract
    rejects None as non_finite, which marks the series unusable)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def read(path, symbols, as_of_ms, *, deadline_seconds=DEADLINE_SECONDS,
         connect=sqlite3.connect):
    """Return (records, provenance). Any fault returns (None, provenance)
    with status `positioning_unavailable`; it never raises."""
    started = _wall_ms()
    symbols = sorted(set(symbols))[:MAX_SYMBOLS]
    prov = {"schema": CAPTURE_SCHEMA, "source": "derivs", "series": list(POSITIONING_SERIES),
            "as_of_ms": as_of_ms, "eligibility": "ts <= as_of_ms",
            "limit_per_series": LIMIT, "symbols": symbols,
            "read_started_ms": started, "read_finished_ms": None,
            "status": "ok", "reason": None,
            "pit": "values as stored at read time; no claim they were stored at as_of_ms"}
    db = None
    try:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(p.name)
        db = connect(p.resolve().as_uri() + "?mode=ro", uri=True,
                     timeout=deadline_seconds)
        stop = time.monotonic() + deadline_seconds
        db.set_progress_handler(lambda: time.monotonic() > stop, 1000)
        records = []
        for sym in symbols:
            for series in POSITIONING_SERIES:
                rows = db.execute(_QUERY, (sym, series, as_of_ms, LIMIT)).fetchall()
                if time.monotonic() > stop:
                    raise TimeoutError("positioning_deadline")
                for ts, value in reversed(rows):
                    records.append({"symbol": sym, "series": series, "ts": ts,
                                    "value": _value(value)})
        prov["read_finished_ms"] = _wall_ms()
        return records, prov
    except Exception as exc:              # never kill the scan
        prov.update(status="positioning_unavailable", reason=type(exc).__name__,
                    read_finished_ms=_wall_ms())
        return None, prov
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def attach(event, path, **kw):
    """Freeze captured positioning into a scan event in place. On failure the
    input gets no positioning key (evaluation unchanged) and an issue is
    recorded."""
    if event.get("kind") != "scan":
        return event
    symbols = [m["symbol"] for m in event["input"]["membership"]]
    records, prov = read(path, symbols, event["as_of_ms"], **kw)
    event["positioning_capture"] = prov
    if records is None:
        event["issues"] = list(event.get("issues", [])) + [
            {"symbol": "*", "reason": "positioning_unavailable", "detail": prov["reason"]}]
    else:
        event["input"]["positioning"] = records
    return event
