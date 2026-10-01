"""Public order-book depth observations for capacity evidence (shadow only).

Validates and stores the body of one unauthenticated GET of
``/fapi/v1/depth?symbol=S&limit=N``; the request itself is made by the
shadow runner (scripts/capacity_depth_shadow.py) with the public fetcher (no
auth headers, no proxies, no redirects, bounded bytes). No credentials,
account endpoint, order API, CCXT, Kernel, Risk or journal.

Each accepted observation keeps the raw levels exactly as the venue sent
them (price and quantity strings), the venue's event / transaction times,
local request and receipt times, the endpoint, the configured depth and the
SHA-256 of the response body. A malformed, crossed, oversize or stale
response is rejected and recorded as telemetry only — never as depth.

Nothing here computes a safe size, participation rate, impact or capacity.
Storage is the caller's bounded shadow database, never a production DB.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

SCHEMA = "depth-observation.v1"
DEPTH_PATH = "/fapi/v1/depth"
VENUE = "binance_usdm"
# the limits /fapi/v1/depth accepts
ALLOWED_LIMITS = (5, 10, 20, 50, 100, 500, 1000)
MAX_BODY_BYTES = 512 * 1024


class DepthRejected(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def request_url(target, symbol: str, limit: int) -> str:
    """`target` has `.base_url` (scheme://host) and `.environment`."""
    if limit not in ALLOWED_LIMITS:
        raise ValueError("depth limit not accepted by the venue")
    if not symbol.isalnum() or not symbol.isupper():
        raise ValueError("venue symbol must be upper-case alphanumeric")
    return f"{target.base_url}{DEPTH_PATH}?" + urlencode({"symbol": symbol, "limit": limit})


def _level(row) -> tuple[str, str, Decimal, Decimal]:
    if not isinstance(row, list) or len(row) != 2 or \
            not all(isinstance(x, str) for x in row):
        raise DepthRejected("malformed_level")
    try:
        p, q = Decimal(row[0]), Decimal(row[1])
    except InvalidOperation:
        raise DepthRejected("malformed_level") from None
    if not (p.is_finite() and q.is_finite()) or p <= 0 or q <= 0:
        raise DepthRejected("nonpositive_level")
    return row[0], row[1], p, q


def _side(rows, limit: int, descending: bool) -> list[list[str]]:
    if not isinstance(rows, list) or not rows:
        raise DepthRejected("empty_side")
    if len(rows) > limit:
        raise DepthRejected("more_levels_than_requested")
    parsed = [_level(r) for r in rows]
    for a, b in zip(parsed, parsed[1:]):
        if (a[2] <= b[2]) if descending else (a[2] >= b[2]):
            raise DepthRejected("levels_not_strictly_ordered")
    return [[p, q] for p, q, _, _ in parsed]


def observe(body, *, target, symbol: str, limit: int,
            request_start_ms: int, received_ms: int, max_age_ms: int) -> dict:
    """An accepted `depth-observation.v1`, or DepthRejected(reason)."""
    if not isinstance(body, bytes):
        raise DepthRejected("no_body")
    if len(body) > MAX_BODY_BYTES:
        raise DepthRejected("oversized")
    if type(request_start_ms) is not int or type(received_ms) is not int \
            or received_ms < request_start_ms:
        raise DepthRejected("receipt_time_invalid")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise DepthRejected("malformed_json") from None
    if not isinstance(payload, dict):
        raise DepthRejected("malformed_depth")
    uid, e, t = payload.get("lastUpdateId"), payload.get("E"), payload.get("T")
    if not all(type(x) is int and x > 0 for x in (uid, e, t)):
        raise DepthRejected("missing_update_id_or_venue_times")
    bids = _side(payload.get("bids"), limit, descending=True)
    asks = _side(payload.get("asks"), limit, descending=False)
    if Decimal(bids[0][0]) >= Decimal(asks[0][0]):
        raise DepthRejected("crossed_book")
    if abs(received_ms - e) > max_age_ms:
        raise DepthRejected("stale_or_clock_skewed")
    return {"schema": SCHEMA, "venue": VENUE, "market_type": "futures",
            "environment": target.environment, "symbol": symbol,
            "instrument_id": f"{VENUE}:futures:{symbol}",
            "source_endpoint": request_url(target, symbol, limit),
            "configured_depth": limit, "bid_levels": len(bids), "ask_levels": len(asks),
            "bids": bids, "asks": asks, "last_update_id": uid,
            "venue_event_ms": e, "venue_transaction_ms": t,
            "request_start_ms": request_start_ms, "received_ms": received_ms,
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "response_bytes": len(body)}


DDL = """
CREATE TABLE IF NOT EXISTS depth_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    environment TEXT NOT NULL,
    configured_depth INTEGER NOT NULL,
    received_ms INTEGER NOT NULL,
    venue_event_ms INTEGER NOT NULL,
    venue_transaction_ms INTEGER NOT NULL,
    request_start_ms INTEGER NOT NULL,
    last_update_id INTEGER NOT NULL,
    source_endpoint TEXT NOT NULL,
    response_sha256 TEXT NOT NULL,
    response_bytes INTEGER NOT NULL,
    bids_json TEXT NOT NULL,             -- [[price, qty], ...] exactly as sent
    asks_json TEXT NOT NULL,
    UNIQUE(symbol, response_sha256)
);
CREATE INDEX IF NOT EXISTS depth_observations_time ON depth_observations(symbol, received_ms);
CREATE TABLE IF NOT EXISTS depth_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    reason TEXT NOT NULL,
    request_start_ms INTEGER,
    received_ms INTEGER,
    http_status INTEGER,
    response_sha256 TEXT,
    response_bytes INTEGER
);
CREATE TRIGGER IF NOT EXISTS depth_observations_no_update BEFORE UPDATE ON depth_observations
BEGIN SELECT RAISE(ABORT, 'depth_observations is immutable'); END;
CREATE TRIGGER IF NOT EXISTS depth_observations_no_delete BEFORE DELETE ON depth_observations
BEGIN SELECT RAISE(ABORT, 'depth_observations is immutable'); END;
"""


def connect(path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=10)
    db.executescript(DDL)
    return db


def store(db: sqlite3.Connection, obs: dict) -> str:
    cur = db.execute(
        "INSERT OR IGNORE INTO depth_observations(symbol,environment,configured_depth,"
        "received_ms,venue_event_ms,venue_transaction_ms,request_start_ms,last_update_id,"
        "source_endpoint,response_sha256,response_bytes,bids_json,asks_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (obs["symbol"], obs["environment"], obs["configured_depth"], obs["received_ms"],
         obs["venue_event_ms"], obs["venue_transaction_ms"], obs["request_start_ms"],
         obs["last_update_id"], obs["source_endpoint"], obs["response_sha256"],
         obs["response_bytes"], json.dumps(obs["bids"], separators=(",", ":")),
         json.dumps(obs["asks"], separators=(",", ":"))))
    db.commit()
    return "inserted" if cur.rowcount else "duplicate"


def reject(db: sqlite3.Connection, symbol: str, reason: str, *, request_start_ms=None,
           received_ms=None, http_status=None, body=None) -> None:
    db.execute("INSERT INTO depth_rejections(symbol,reason,request_start_ms,received_ms,"
               "http_status,response_sha256,response_bytes) VALUES (?,?,?,?,?,?,?)",
               (symbol, reason, request_start_ms, received_ms, http_status,
                hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else None,
                len(body) if isinstance(body, bytes) else None))
    db.commit()


def storage(db: sqlite3.Connection, db_bytes: int, first_ms: int | None,
            last_ms: int | None) -> dict:
    """Measured growth only: no retention limit is set here."""
    n = db.execute("SELECT COUNT(*) FROM depth_observations").fetchone()[0]
    payload = db.execute("SELECT COALESCE(SUM(LENGTH(bids_json)+LENGTH(asks_json)+"
                         "LENGTH(source_endpoint)+LENGTH(response_sha256)+LENGTH(symbol)"
                         "+LENGTH(environment)),0) FROM depth_observations").fetchone()[0]
    rejected = db.execute("SELECT COUNT(*) FROM depth_rejections").fetchone()[0]
    span_h = ((last_ms - first_ms) / 3_600_000) if first_ms and last_ms and \
        last_ms > first_ms else None
    per_h = n / span_h if span_h else None
    per_snap = db_bytes / n if n else None
    return {"snapshots": n, "rejections": rejected, "db_bytes": db_bytes,
            "payload_bytes": payload,
            "bytes_per_snapshot_on_disk": per_snap,
            "payload_bytes_per_snapshot": payload / n if n else None,
            "observed_span_hours": span_h,
            "snapshots_per_hour": per_h,
            "snapshots_per_day": per_h * 24 if per_h else None,
            "projected_30_day_bytes": per_snap * per_h * 24 * 30
            if per_snap and per_h else None,
            "basis": "measured from this shadow database; a projection at the "
                     "observed rate, not a retention policy"}
