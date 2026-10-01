"""Capacity evidence: durable records of what existing reads already observe.

Two records, both made from responses the Kernel already receives — no new
request, endpoint or credential:

- ``account-margin-observation.v1`` — the venue's ``availableBalance`` from
  the SAME ``/fapi/v3/account`` response `Kernel._fetch_balance_fresh` reads
  Risk's equity (``totalMarginBalance``) from. Bound to that response by its
  SHA-256 and by the equity field carried beside it. A response without the
  field is UNAVAILABLE / NOT_PRESENT_IN_EXISTING_AUTHORIZED_ACCOUNT_EVIDENCE;
  nothing is derived from wallet balance, equity or margin arithmetic.
- ``venue-position-snapshot.v1`` — the verified `portfolio.observation.v1`
  reconciliation already builds from ``fetch_positions()``, unchanged (its
  observation_id still verifies), plus the entry price each present row
  carried. A venue reading, never the journal.

Freshness is judged by the reader at use time against pre-existing bounds
(``current_truth.ACCOUNT_STALE_S``, ``protection_snapshot.STALE_AFTER_S``).

Storage: the latest of each in ``state_kv`` (overwritten, like
``account_observation``); snapshots additionally append to the immutable
``venue_position_snapshots`` table whenever the observed book differs from
the last row stored, so every distinct venue book is kept once.

Observation only: nothing here places an order, changes Risk, leverage,
reconciliation or control state, and a failure is contained by the caller.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, InvalidOperation

MARGIN_SCHEMA = "account-margin-observation.v1"
MARGIN_KV = "account_margin_observation"
SNAPSHOT_SCHEMA = "venue-position-snapshot.v1"
SNAPSHOT_KV = "venue_position_snapshot"
SNAPSHOT_TABLE = "venue_position_snapshots"

AVAILABLE, UNAVAILABLE = "AVAILABLE", "UNAVAILABLE"
NOT_PRESENT = "NOT_PRESENT_IN_EXISTING_AUTHORIZED_ACCOUNT_EVIDENCE"
NO_RESPONSE = "NO_AUTHORIZED_ACCOUNT_RESPONSE_THIS_CYCLE"

VENUE = "binance_usdm"
ACCOUNT_PATH = "/fapi/v3/account"
MARGIN_FIELD = "availableBalance"
EQUITY_FIELD = "totalMarginBalance"
# the only hosts the Kernel's account read resolves to, and their environment
ACCOUNT_HOSTS = {"https://demo-fapi.binance.com": "demo",
                 "https://fapi.binance.com": "production"}
DENOMINATION = ("USDT: the denomination the Kernel's account_observation "
                "assigns to totalMarginBalance from this same response "
                "(Binance reports account-level fields as USD value in "
                "multi-assets mode; this response does not state the mode)")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode()).hexdigest()


def _venue_decimal(value) -> str | None:
    """The venue's numeric string, exactly, if it is a finite number."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return str(value) if d.is_finite() else None


def _is_ms(v) -> bool:
    return type(v) is int and v >= 0


# ── available margin, from the existing account response ────────────────
def margin_observation(body, *, request_url: str | None, request_start_ms,
                       received_ms) -> dict:
    """One `account-margin-observation.v1` from the raw bytes of the account
    response the Kernel already read. `body=None` means no response exists
    this cycle. Never raises on venue content: absence or malformation is a
    reason, not a guess."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(request_url or "")
        env = ACCOUNT_HOSTS.get(f"{u.scheme}://{u.netloc}") \
            if u.path == ACCOUNT_PATH else None
    except ValueError:
        env = None
    rec = {"schema": MARGIN_SCHEMA, "venue": VENUE, "market_type": "futures",
           "environment": env, "endpoint": ACCOUNT_PATH, "field": MARGIN_FIELD,
           "asset": "USDT", "denomination": DENOMINATION,
           "request_start_ms": request_start_ms if _is_ms(request_start_ms) else None,
           "received_at_ms": received_ms if _is_ms(received_ms) else None,
           "observed_at_ms": None, "observed_at_basis":
               "local receipt of the complete response (the response carries "
               "no account-level server time)",
           "source": "Kernel._fetch_balance_fresh GET /fapi/v3/account — the "
                     "response Risk's equity is read from; no additional request",
           "response_sha256": None, "response_bytes": None,
           "equity_field": EQUITY_FIELD, "equity_value_text": None,
           "value_text": None, "value": None, "status": UNAVAILABLE, "reason": None}
    if not isinstance(body, (bytes, bytearray)):
        rec["reason"] = NO_RESPONSE
    else:
        body = bytes(body)
        rec.update(response_sha256=_sha(body), response_bytes=len(body))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = None
        if not isinstance(payload, dict):
            rec["reason"] = "ACCOUNT_RESPONSE_MALFORMED"
        elif env is None:
            rec["reason"] = "ACCOUNT_REQUEST_URL_UNRECOGNISED"
        elif not (_is_ms(rec["request_start_ms"]) and _is_ms(rec["received_at_ms"])
                  and rec["received_at_ms"] >= rec["request_start_ms"]):
            rec["reason"] = "ACCOUNT_RESPONSE_TIME_INVALID"
        else:
            rec["equity_value_text"] = _venue_decimal(payload.get(EQUITY_FIELD))
            rec["observed_at_ms"] = rec["received_at_ms"]
            if MARGIN_FIELD not in payload:
                rec["reason"] = NOT_PRESENT
            else:
                text = _venue_decimal(payload.get(MARGIN_FIELD))
                if text is None:
                    rec["reason"] = "AVAILABLE_BALANCE_MALFORMED"
                else:
                    rec.update(value_text=text, value=float(Decimal(text)),
                               status=AVAILABLE)
    rec["observation_id"] = _sha(canonical(rec))
    return rec


def verify_margin(rec) -> dict | None:
    """The record if its identity re-verifies, else None."""
    if not isinstance(rec, dict) or rec.get("schema") != MARGIN_SCHEMA:
        return None
    body = {k: v for k, v in rec.items() if k != "observation_id"}
    try:
        return rec if _sha(canonical(body)) == rec.get("observation_id") else None
    except (TypeError, ValueError):
        return None


# ── venue position snapshot, from the reconciliation read ───────────────
def _observation_dict(obs) -> dict:
    """`portfolio.observation.v1` as plain values, id unchanged."""
    return {"schema": obs.schema, "venue": obs.venue, "source": obs.source,
            "environment": obs.environment, "source_ref": obs.source_ref,
            "market_type": getattr(obs.market_type, "value", obs.market_type),
            "request_start_ms": obs.request_start_ms,
            "response_received_ms": obs.response_received_ms,
            "as_of_ms": obs.as_of_ms, "complete": obs.complete,
            "positions": [[p.instrument_id.value, p.position_present, p.side,
                           p.absolute_quantity] for p in obs.positions],
            "observation_id": obs.observation_id}


def _observation_payload(o: dict) -> dict:
    return {k: o[k] for k in ("schema", "venue", "source", "environment",
                              "source_ref", "market_type", "request_start_ms",
                              "response_received_ms", "as_of_ms", "complete",
                              "positions")}


def position_snapshot(observation, rows) -> dict:
    """A `venue-position-snapshot.v1` from a verified PortfolioObservation and
    the exact rows it was built from. Entry price is the venue's
    ``info.entryPrice`` string for present positions (None if absent)."""
    obs = _observation_dict(observation)
    if _sha(canonical(_observation_payload(obs))) != obs["observation_id"]:
        raise ValueError("observation identity does not verify")
    entry = {}
    for row in rows if isinstance(rows, list) else ():
        info = row.get("info") if isinstance(row, dict) else None
        if isinstance(info, dict) and isinstance(info.get("symbol"), str):
            entry[f"{VENUE}:futures:{info['symbol']}"] = _venue_decimal(info.get("entryPrice"))
    positions = [{"instrument_id": iid, "symbol": iid.split(":")[-1], "side": side,
                  "quantity": qty, "entry_price_text": entry.get(iid),
                  "entry_price_basis": "venue positionRisk entryPrice (via ccxt info)"
                  if entry.get(iid) is not None else "not present in the response row"}
                 for iid, present, side, qty in obs["positions"] if present]
    body = {"schema": SNAPSHOT_SCHEMA, "venue": obs["venue"],
            "market_type": obs["market_type"], "environment": obs["environment"],
            "observed_at_ms": obs["as_of_ms"],
            "received_at_ms": obs["response_received_ms"],
            "request_start_ms": obs["request_start_ms"],
            "completeness": "COMPLETE" if obs["complete"] is True else "INCOMPLETE",
            "source_identity": {"source": obs["source"], "source_ref": obs["source_ref"],
                                "observation_id": obs["observation_id"],
                                "read_by": "Kernel._detect_exchange_exits (reconciliation)"},
            "basis": "venue position response; not the journal",
            "positions": positions, "observation": obs}
    return {**body, "snapshot_id": _sha(canonical(body))}


def verify_snapshot(snap) -> dict | None:
    """The snapshot if its id and its embedded observation id both verify."""
    if not isinstance(snap, dict) or snap.get("schema") != SNAPSHOT_SCHEMA:
        return None
    try:
        body = {k: v for k, v in snap.items() if k != "snapshot_id"}
        obs = snap["observation"]
        if _sha(canonical(body)) != snap.get("snapshot_id") or \
                _sha(canonical(_observation_payload(obs))) != obs.get("observation_id"):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return snap


def _book_key(snap: dict) -> str:
    """What makes two snapshots the same venue book (times excluded)."""
    return canonical([snap["environment"], snap["completeness"], snap["positions"]])


SNAPSHOT_DDL = f"""
CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
    snapshot_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    market_type TEXT NOT NULL,
    environment TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL,
    book_sha256 TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS {SNAPSHOT_TABLE}_time ON {SNAPSHOT_TABLE}(observed_at_ms);
CREATE TRIGGER IF NOT EXISTS {SNAPSHOT_TABLE}_no_update BEFORE UPDATE ON {SNAPSHOT_TABLE}
BEGIN SELECT RAISE(ABORT, '{SNAPSHOT_TABLE} is immutable'); END;
CREATE TRIGGER IF NOT EXISTS {SNAPSHOT_TABLE}_no_delete BEFORE DELETE ON {SNAPSHOT_TABLE}
BEGIN SELECT RAISE(ABORT, '{SNAPSHOT_TABLE} is immutable'); END;
"""


def record_margin(journal, rec: dict) -> None:
    journal.kv_set(MARGIN_KV, canonical(rec))


def record_snapshot(journal, snap: dict, *, at_ms: int) -> str:
    """Latest -> state_kv; appended to the immutable table when the venue
    book differs from the last stored row. Returns 'inserted' / 'unchanged'."""
    if verify_snapshot(snap) is None:
        raise ValueError("snapshot identity does not verify")
    text = canonical(snap)
    book = _sha(_book_key(snap))
    out = "unchanged"
    with journal._tx() as c:
        c.executescript(SNAPSHOT_DDL)
        last = c.execute(f"SELECT book_sha256 FROM {SNAPSHOT_TABLE} ORDER BY "
                         "observed_at_ms DESC, rowid DESC LIMIT 1").fetchone()
        if last is None or last[0] != book:
            c.execute(f"INSERT OR IGNORE INTO {SNAPSHOT_TABLE} VALUES (?,?,?,?,?,?,?,?)",
                      (snap["snapshot_id"], snap["venue"], snap["market_type"],
                       snap["environment"], snap["observed_at_ms"], book, text,
                       int(at_ms)))
            out = "inserted"
        c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                  (SNAPSHOT_KV, text))
    return out


def snapshot_history(journal) -> list[dict]:
    """Every stored distinct venue book, oldest first, each re-verified."""
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                         (SNAPSHOT_TABLE,)):
        return []
    out = []
    for r in journal.query(f"SELECT canonical_json FROM {SNAPSHOT_TABLE} "
                           "ORDER BY observed_at_ms, rowid"):
        snap = verify_snapshot(json.loads(r["canonical_json"]))
        if snap is None:
            raise ValueError("stored venue position snapshot does not verify")
        out.append(snap)
    return out


def load_json(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def finite(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None
