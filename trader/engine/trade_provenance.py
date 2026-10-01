"""Prospective trade provenance: strategy version → decision → order → fill →
booking, joined only by identifiers the system itself recorded.

Observation only. Nothing here decides whether, when or how Luffy trades;
every write happens inside a booking transaction the executor was already
making, and a failure here is contained so it can never fail that booking.

Three records, all in the journal:

- ``trades.entry_identity_json`` — the exact strategy version the entry was
  taken on, frozen at insert. A trigger refuses any later change, including
  filling in a NULL, so historical trades cannot be "backfilled" with a spec
  that did not exist when they opened; they stay UNKNOWN.
- ``trade_legs`` — one row per booking receipt: which trade it reduced or
  opened, whether Luffy sent an order for it (and the venue's order id exactly
  as returned) or merely observed the venue change, and the reference price
  captured before submission.
- ``trade_fills`` — venue fills, one row per (market type, symbol, venue fill
  id). A fill is ATTRIBUTED to a trade only when its venue order id equals an
  order Luffy recorded, in the same market type and symbol, for exactly one
  trade. Symbol, side and time proximity alone never attribute a fill; such
  fills stay UNATTRIBUTED or AMBIGUOUS. The market type always comes from the
  booking receipt's trade; unknown scope records nothing and attributes nothing.

A closed trade's provenance is VERIFIED only through the leg whose receipt
moved it open -> closed (``terminal_close``), and only when that leg is a
Luffy order with an exact order link and complete fills.

Funding is not attributed per trade (account-level funding is never split).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from decimal import Decimal, InvalidOperation

from ..core.types import norm_symbol

log = logging.getLogger(__name__)

IDENTITY_VERSION = "trade-entry-identity.v1"
READ_VERSION = "trade-provenance-read.v2"

ATTRIBUTED, AMBIGUOUS, UNATTRIBUTED = "ATTRIBUTED", "AMBIGUOUS", "UNATTRIBUTED"
VERIFIED, UNAVAILABLE, UNKNOWN, ESTIMATED, PARTIAL = (
    "VERIFIED", "UNAVAILABLE", "UNKNOWN", "ESTIMATED", "PARTIAL")

#: legs Luffy submitted an order for. Anything else is a venue change the
#: journal booked without an order of its own (native stop, ghost, alignment).
LUFFY_ORDER_PURPOSES = {"entry", "recovered_entry", "partial_exit", "final_exit",
                        "final_exit_partial_fill", "panic_exit"}
ENTRY_PURPOSES = {"entry", "recovered_entry"}
NON_EXIT_PURPOSES = ENTRY_PURPOSES | {"adopted", "entry_unrecorded"}

#: venue scopes a fill or order id is unique within. A receipt whose trade
#: carries anything else has an unknown scope, and nothing is keyed or
#: attributed on it: spot and futures ids are independent sequences.
MARKET_TYPES = {"spot", "futures"}
EXACT_LINK = "EXACT_LUFFY_ORDER_LINK"
#: the R1 draft's word for the same rule, before order ids were scoped by
#: market type; read conservatively, never as an exact link
LEGACY_EXACT = "EXACT"

TABLES = """
CREATE TABLE IF NOT EXISTS trade_legs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL,
    booking_id INTEGER NOT NULL,         -- trade_accounting_bookings.id
    kind TEXT NOT NULL,                  -- the booking kind (entry / close:* / align_*)
    purpose TEXT NOT NULL,               -- entry / partial_exit / final_exit / native_exit / ...
    origin TEXT NOT NULL,                -- luffy_order | venue_observed | adopted
    symbol TEXT NOT NULL,
    side TEXT,                           -- order side (buy/sell) when known
    venue_order_id TEXT,                 -- exactly as the venue returned; NULL if unknown
    client_order_id TEXT,
    protective_algo_id TEXT,
    order_identity TEXT NOT NULL,        -- VERIFIED | UNAVAILABLE | NOT_APPLICABLE
    requested_qty REAL,
    booked_qty REAL,
    booked_price REAL,
    reference_json TEXT,                 -- slippage basis captured before submission
    exit_attribution TEXT NOT NULL,      -- EXACT_LUFFY_ORDER_LINK | AMBIGUOUS | VENUE_EVENT_UNVERIFIED | UNKNOWN_HISTORICAL | NOT_APPLICABLE
    exit_attribution_json TEXT,
    fee_basis TEXT,                      -- evidence basis of the booked P&L/fee
    source TEXT NOT NULL,                -- live_booking | backfill_receipt
    recorded_ms INTEGER NOT NULL,
    market_type TEXT,                    -- the trade's, from the receipt; NULL = scope unknown
    status_transition TEXT,              -- '<before>-><after>' trade status in the receipt
    terminal_close INTEGER               -- 1 iff this receipt moved the trade open -> closed; NULL = not recorded (legacy)
);

CREATE TABLE IF NOT EXISTS trade_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    fill_key TEXT NOT NULL,              -- '<market_type>|id:<venue fill id>' (or '|digest:<sha256>'); legacy rows unscoped
    venue_fill_id TEXT,                  -- exactly as the venue returned
    venue_order_id TEXT,
    trade_id TEXT,                       -- set only when attribution = ATTRIBUTED
    leg_id INTEGER,                      -- trade_legs.id of the attributed order
    leg TEXT,                            -- entry | exit | unknown
    side TEXT,
    qty REAL,
    price REAL,
    commission TEXT,                     -- venue string, exact
    commission_asset TEXT,
    realized_pnl TEXT,                   -- venue string, exact
    venue_ts_ms INTEGER,
    observed_ms INTEGER NOT NULL,        -- when Luffy received it
    attribution TEXT NOT NULL,           -- ATTRIBUTED | AMBIGUOUS | UNATTRIBUTED
    attribution_reason TEXT NOT NULL,
    source TEXT NOT NULL,                -- which evidence carried it
    observation_json TEXT,               -- where it was observed; never attribution
    fill_json TEXT NOT NULL,
    market_type TEXT,                    -- NULL only on legacy unscoped rows
    UNIQUE(symbol, fill_key)
);
"""

#: columns added to tables an earlier draft of this module created
COLUMNS = {"trade_legs": (("market_type", "TEXT"), ("status_transition", "TEXT"),
                          ("terminal_close", "INTEGER")),
           "trade_fills": (("market_type", "TEXT"),)}

INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS trade_legs_booking ON trade_legs(booking_id);
CREATE INDEX IF NOT EXISTS trade_legs_trade ON trade_legs(trade_id, id);
CREATE INDEX IF NOT EXISTS trade_legs_order ON trade_legs(symbol, venue_order_id);
CREATE INDEX IF NOT EXISTS trade_legs_scoped_order
    ON trade_legs(market_type, symbol, venue_order_id);
CREATE INDEX IF NOT EXISTS trade_fills_trade ON trade_fills(trade_id);
CREATE INDEX IF NOT EXISTS trade_fills_order ON trade_fills(symbol, venue_order_id);
CREATE UNIQUE INDEX IF NOT EXISTS trade_fills_scoped
    ON trade_fills(market_type, symbol, fill_key);
CREATE INDEX IF NOT EXISTS trade_fills_scoped_order
    ON trade_fills(market_type, symbol, venue_order_id);
"""

#: the entry identity is frozen at insert: no update may change it, and a
#: historical NULL may not be filled in later with a current spec
IDENTITY_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trades_entry_identity_immutable
BEFORE UPDATE OF entry_identity_json ON trades
WHEN OLD.entry_identity_json IS NOT NEW.entry_identity_json
BEGIN SELECT RAISE(ABORT, 'entry_identity_immutable'); END;
"""


def migrate(c) -> None:
    """Additive only: create missing tables, add missing columns, add indexes
    and the identity trigger. Existing rows and the original UNIQUE(symbol,
    fill_key) are untouched; legacy rows keep a NULL market_type (scope
    unknown) and are never re-keyed or re-scoped by guess."""
    c.executescript(TABLES)
    for table, columns in COLUMNS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in columns:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    c.executescript(INDEXES)
    c.executescript(IDENTITY_TRIGGER)


def _encode(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(value) -> str:
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def clean(value):
    """JSON-safe copy, or None. Identity must never be able to fail an entry."""
    try:
        return json.loads(_encode(value))
    except (TypeError, ValueError):
        return None


# ── strategy identity at entry ──────────────────────────────────────────────
def spec_version(spec) -> dict:
    """The exact compiled spec, and its hash. Computed from the object the
    evaluator runs, never from the registry row (which may change later)."""
    body = clean(spec.to_dict())
    return {"spec": body, "spec_sha256": sha256(body) if body is not None else None}


def population_identity(strategy_id: str, *, kind: str, family: str, name: str,
                        state: str, generation, parent_id, loaded_at: str,
                        spec=None, params=None, created_at=None) -> dict:
    """What the running population holds for one strategy, at load time."""
    out = {"strategy_id": strategy_id, "strategy_name": name, "kind": kind,
           "evaluator_family": family, "registry_state_at_load": state,
           "registry_created_at": created_at, "generation": generation,
           "parent_id": parent_id or None, "population_loaded_at": loaded_at}
    if spec is not None:
        out.update(spec_version(spec), version_basis="compiled spec held by the running population")
    else:
        body = clean(params)
        out.update(params=body, params_sha256=sha256(body) if body is not None else None,
                   version_basis="genome params held by the running population")
    return out


def entry_identity(strategy_id: str, loaded: dict | None, decision, signal: dict | None,
                   captured_at: str) -> dict:
    """The immutable identity persisted on a new trade.

    VERIFIED only when the signal that proposed the trade carries the spec hash
    of the exact evaluator that produced it and that hash matches the loaded
    spec. Otherwise the reason is recorded; nothing is inferred from the
    registry.
    """
    dec = {"decision_id": getattr(decision, "id", None),
           "cycle_id": getattr(decision, "cycle_id", None),
           "scan_id": getattr(decision, "scan_id", None),
           "action": getattr(getattr(decision, "action", None), "value", None),
           "score": getattr(decision, "score", None),
           "threshold": getattr(decision, "threshold", None),
           "confidence": getattr(decision, "confidence", None),
           "meta_p": getattr(decision, "meta_p", None),
           "decided_at": getattr(decision, "ts", None)}
    out = {"schema_version": IDENTITY_VERSION, "captured_at": captured_at,
           "strategy_id": strategy_id or None, "decision": dec,
           "signal": signal}
    if not strategy_id or strategy_id == "orchestrator":
        out.update(status=UNKNOWN, reason="no_strategy_signal_proposed_this_direction")
    elif loaded is None:
        out.update(status=UNKNOWN, reason="strategy_not_in_running_population")
    else:
        out.update(loaded)
        stamped = ((signal or {}).get("params") or {}).get("spec_sha256")
        if loaded.get("kind") == "spec":
            if stamped and stamped == loaded.get("spec_sha256"):
                out.update(status=VERIFIED, reason="signal_spec_hash_matches_loaded_spec")
            elif stamped:
                # the evaluator that fired and the population snapshot differ
                # (a reload between signal and entry): the signal's hash is the
                # version that decided; the stored spec body is not that version
                out.update(status=AMBIGUOUS, reason="signal_spec_hash_differs_from_loaded_spec",
                           signal_spec_sha256=stamped, spec=None)
            else:
                out.update(status=UNKNOWN, reason="signal_carries_no_spec_hash")
        else:
            out.update(status=VERIFIED if loaded.get("params_sha256") else UNKNOWN,
                       reason="genome_params_held_by_running_population")
    safe = clean(out)
    if safe is None:
        return {"schema_version": IDENTITY_VERSION, "status": UNKNOWN,
                "reason": "identity_not_serializable", "strategy_id": strategy_id or None,
                "captured_at": captured_at}
    return safe


# ── recording, inside the booking transaction ───────────────────────────────
def _purpose(kind: str, evidence: dict) -> str:
    if evidence.get("purpose"):
        return str(evidence["purpose"])
    basis = evidence.get("basis")
    if kind == "entry":
        return {"entry_order_confirmation": "entry",
                "recovered_entry_order_confirmation": "recovered_entry",
                "reconcile_adoption": "adopted"}.get(basis, "entry_unrecorded")
    if kind.startswith("close:"):
        reason = kind.split(":", 1)[1]
        if reason in ("sl_fill", "tp_fill"):
            return "native_exit"
        if reason == "reconciled_ghost":
            return "reconcile_ghost"
        if reason == "panic":
            return "panic_exit"
        return "final_exit" if evidence.get("order_id") else "unrecorded_exit"
    if kind.startswith("align"):
        return "partial_exit" if evidence.get("order_id") else "reconcile_align"
    return "unknown"


def _num(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _oid(value):
    """A venue identifier exactly as the venue returned it. None or an empty
    string is unknown; anything else is kept verbatim (no trimming, no
    numeric normalization)."""
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else str(value)


def market_scope(*snapshots) -> str | None:
    """The trade's market type from the receipt's own trade snapshots, or
    None when absent, conflicting or not a known venue scope. Never guessed
    from the symbol or the executor's configuration."""
    seen = {str(s.get("market_type") or "").strip().lower() for s in snapshots if s}
    seen.discard("")
    return seen.pop() if len(seen) == 1 and seen <= MARKET_TYPES else None


def record_booking(db, trade_id: str, kind: str, before, after, evidence: dict,
                   booking_id: int, *, source: str = "live_booking",
                   recorded_ms: int | None = None) -> int | None:
    """Record one booking's leg and any fills its evidence carries. Idempotent
    per booking receipt. Returns the leg id (None if already recorded)."""
    if db.execute("SELECT 1 FROM trade_legs WHERE booking_id=?", (booking_id,)).fetchone():
        return None
    evidence = evidence if isinstance(evidence, dict) else {}
    after = after or {}
    before = before or {}
    purpose = _purpose(kind, evidence)
    symbol = after.get("symbol") or before.get("symbol")
    market = market_scope(before, after)
    # the terminal marker is the receipt's own trade transition, not the
    # purpose label: only the booking that moved the trade open -> closed
    was, now = before.get("status"), after.get("status")
    transition = f"{was or 'none'}->{now or 'none'}"
    terminal = 1 if (was == "open" and now == "closed") else 0
    oid = _oid(evidence.get("order_id"))
    origin = ("luffy_order" if purpose in LUFFY_ORDER_PURPOSES
              else "adopted" if purpose == "adopted"
              else "unrecorded" if purpose == "entry_unrecorded" else "venue_observed")
    order_identity = (VERIFIED if oid else UNAVAILABLE) if origin == "luffy_order" \
        else "NOT_APPLICABLE"
    side = evidence.get("side")
    if not side and purpose in ENTRY_PURPOSES:
        side = "buy" if after.get("side") == "long" else "sell"
    qty = _num(evidence.get("quantity", evidence.get("confirmed_quantity")))
    if qty is None and before and after:
        # the journal's own booked reduction, when no order quantity exists
        a, b = _num(after.get("amount")), _num(before.get("amount"))
        if terminal:
            qty = b
        elif a is not None and b is not None:
            qty = abs(b - a) or None
    elif qty is None and purpose in ("adopted", "entry_unrecorded"):
        qty = _num(after.get("amount"))
    price = _num(evidence.get("confirmed_price"))
    if price is None:
        price = _num(after.get("exit_price") if kind.startswith("close:") else None)
    exit_attr, exit_detail = "NOT_APPLICABLE", None
    if purpose not in NON_EXIT_PURPOSES:
        exit_attr, exit_detail = _exit_attribution(db, trade_id, symbol, market, origin,
                                                   oid, source)
    cur = db.execute(
        "INSERT INTO trade_legs(trade_id,booking_id,kind,purpose,origin,symbol,side,"
        "venue_order_id,client_order_id,protective_algo_id,order_identity,requested_qty,"
        "booked_qty,booked_price,reference_json,exit_attribution,exit_attribution_json,"
        "fee_basis,source,recorded_ms,market_type,status_transition,terminal_close) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (trade_id, booking_id, kind, purpose, origin, symbol, side, oid,
         _oid(evidence.get("client_order_id")), _oid(evidence.get("protective_algo_id")),
         order_identity, _num(evidence.get("requested_quantity")), qty, price,
         _encode(evidence["reference"]) if clean(evidence.get("reference")) else None,
         exit_attr, _encode(exit_detail) if exit_detail else None,
         evidence.get("booked_pnl_basis") or evidence.get("basis"),
         source, recorded_ms or int(time.time() * 1000), market, transition, terminal))
    leg_id = cur.lastrowid
    if oid and origin == "luffy_order" and market:
        _upgrade_unattributed(db, market, symbol, oid)
    observed = int(evidence.get("observed_ms") or recorded_ms or time.time() * 1000)
    ctx = {"booking_id": booking_id, "trade_id": trade_id, "kind": kind,
           "market_type": market}
    for fill in evidence.get("fills") or []:
        record_fill(db, fill, source=f"{evidence.get('basis')}", observed_ms=observed,
                    context=ctx)
    window = evidence.get("venue_window_observation")
    if isinstance(window, dict):
        wobs = int(window.get("observed_ms") or observed)
        for fill in window.get("fills") or []:
            record_fill(db, fill, source="venue_window_observation", observed_ms=wobs,
                        context=ctx)
    return leg_id


#: what a journal sibling check can and cannot establish
SIBLING_SCOPE = "journal_only"
VENUE_EXCLUSIVITY = "NOT_CLAIMED"


def _exit_attribution(db, trade_id, symbol, market, origin, oid, source):
    """EXACT_LUFFY_ORDER_LINK means only: Luffy recorded this venue order for
    this journal trade and for no other trade in the same market scope. It is
    never a claim that the trade exclusively owned the venue position — one
    net position per symbol can hold what the journal does not know about."""
    base = {"sibling_evidence_scope": SIBLING_SCOPE,
            "venue_position_exclusivity": VENUE_EXCLUSIVITY}
    if source != "live_booking":
        return "UNKNOWN_HISTORICAL", dict(base, reason="sibling_positions_at_booking_not_recorded")
    if market is None:
        return AMBIGUOUS, dict(base, reason="trade_market_type_unknown")
    siblings = [r[0] for r in db.execute(
        "SELECT id FROM trades WHERE symbol=? AND market_type=? AND status='open' AND id<>?",
        (symbol, market, trade_id)).fetchall()]
    base["journal_open_siblings_same_symbol"] = siblings
    if siblings:
        # the journal shows another open trade sharing the net position
        return AMBIGUOUS, dict(base, reason="journal_open_sibling_shares_symbol_net_position")
    if origin != "luffy_order":
        return "VENUE_EVENT_UNVERIFIED", dict(
            base, reason="venue_reduction_booked_without_a_luffy_order_or_fill_identity")
    if not oid:
        return AMBIGUOUS, dict(base, reason="luffy_order_without_venue_order_id")
    others = [r[0] for r in db.execute(
        "SELECT DISTINCT trade_id FROM trade_legs WHERE market_type=? AND symbol=? AND "
        "venue_order_id=? AND origin='luffy_order' AND trade_id<>?",
        (market, symbol, oid, trade_id)).fetchall()]
    if others:
        return AMBIGUOUS, dict(base, reason="order_recorded_for_multiple_trades",
                               other_trades=others)
    return EXACT_LINK, dict(base, reason="venue_order_id_recorded_by_luffy_for_this_trade_only")


def fill_key(fill: dict, market_type: str) -> str:
    """Unique within (market_type, symbol): the market namespace is part of
    the key, the venue fill id itself is stored untouched in venue_fill_id."""
    fid = _oid(fill.get("id"))
    return f"{market_type}|" + (f"id:{fid}" if fid else "digest:" + sha256(fill))


def _attribute(db, fill: dict, market: str, symbol: str):
    """(attribution, reason, trade_id, leg_id, leg) by exact order identity
    within one market scope only."""
    if not _oid(fill.get("id")):
        return AMBIGUOUS, "missing_venue_fill_id", None, None, None
    oid = _oid(fill.get("order"))
    if not oid:
        return UNATTRIBUTED, "missing_venue_order_id", None, None, None
    legs = db.execute(
        "SELECT id, trade_id, purpose, side FROM trade_legs WHERE market_type=? AND symbol=? "
        "AND venue_order_id=? AND origin='luffy_order'", (market, symbol, oid)).fetchall()
    trades = {r[1] for r in legs}
    if not trades:
        return UNATTRIBUTED, "order_not_recorded_by_luffy", None, None, None
    if len(trades) > 1:
        return AMBIGUOUS, "order_recorded_for_multiple_trades", None, None, None
    leg = legs[0]
    if leg[3] and fill.get("side") and fill["side"] != leg[3]:
        return AMBIGUOUS, "fill_side_differs_from_order_side", None, None, None
    return (ATTRIBUTED, "venue_order_id_matches_luffy_order", leg[1], leg[0],
            "entry" if leg[2] in ENTRY_PURPOSES else "exit")


def record_fill(db, fill: dict, *, source: str, observed_ms: int, context: dict) -> str:
    """Insert one venue fill, idempotently. Returns 'inserted', 'duplicate',
    'upgraded', 'conflict', 'invalid' or 'unscoped'. A conflicting replay
    never overwrites evidence. Without a known market scope (from the
    receipt's trade) nothing is keyed or attributed: the receipt keeps it."""
    if not isinstance(fill, dict):
        return "invalid"
    safe = clean(fill)
    if safe is None:
        return "invalid"
    market = context.get("market_type")
    if market not in MARKET_TYPES:
        return "unscoped"
    symbol = norm_symbol(str(safe.get("symbol") or context.get("symbol") or ""))
    key = fill_key(safe, market)
    existing = db.execute("SELECT id, fill_json, attribution, trade_id FROM trade_fills "
                          "WHERE market_type=? AND symbol=? AND fill_key=?",
                          (market, symbol, key)).fetchone()
    attribution, reason, trade_id, leg_id, leg = _attribute(db, safe, market, symbol)
    if existing:
        if existing[1] != _encode(safe):
            db.execute("UPDATE trade_fills SET attribution=?, attribution_reason=?, trade_id=NULL, "
                       "leg_id=NULL WHERE id=?", (AMBIGUOUS, "conflicting_fill_content_for_same_id",
                                                   existing[0]))
            return "conflict"
        if existing[2] == UNATTRIBUTED and attribution == ATTRIBUTED:
            db.execute("UPDATE trade_fills SET attribution=?, attribution_reason=?, trade_id=?, "
                       "leg_id=?, leg=? WHERE id=?",
                       (attribution, reason, trade_id, leg_id, leg, existing[0]))
            return "upgraded"
        if existing[2] == ATTRIBUTED and (attribution != ATTRIBUTED or trade_id != existing[3]):
            # the order identity now points at more than one trade: the earlier
            # binding is no longer exclusive, so it is withdrawn, not kept
            db.execute("UPDATE trade_fills SET attribution=?, attribution_reason=?, trade_id=NULL, "
                       "leg_id=NULL WHERE id=?", (AMBIGUOUS, "attribution_conflict:" + reason,
                                                  existing[0]))
            return "conflict"
        return "duplicate"
    db.execute(
        "INSERT INTO trade_fills(market_type,symbol,fill_key,venue_fill_id,venue_order_id,"
        "trade_id,leg_id,leg,side,qty,price,commission,commission_asset,realized_pnl,"
        "venue_ts_ms,observed_ms,attribution,attribution_reason,source,observation_json,"
        "fill_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (market, symbol, key, _oid(safe.get("id")), _oid(safe.get("order")), trade_id, leg_id,
         leg or "unknown", safe.get("side"), _num(safe.get("amount")), _num(safe.get("price")),
         None if safe.get("commission") is None else str(safe.get("commission")),
         safe.get("commission_asset"),
         None if safe.get("realized_pnl") is None else str(safe.get("realized_pnl")),
         int(safe["timestamp"]) if _num(safe.get("timestamp")) is not None else None,
         int(observed_ms), attribution, reason, source, _encode(context), _encode(safe)))
    return "inserted"


def _upgrade_unattributed(db, market, symbol, oid):
    """Fills seen before their order was recorded become attributable once it
    is — still strictly by exact order id within the same market scope."""
    for row in db.execute("SELECT fill_json FROM trade_fills WHERE market_type=? AND symbol=? "
                          "AND venue_order_id=? AND attribution=?",
                          (market, symbol, oid, UNATTRIBUTED)).fetchall():
        record_fill(db, json.loads(row[0]), source="upgrade", observed_ms=0,
                    context={"market_type": market})


def record_safely(db, *args, **kwargs):
    """record_booking behind a savepoint: a provenance failure is recorded as a
    control event and never rolls back or fails the booking itself."""
    db.execute("SAVEPOINT trade_provenance")
    try:
        record_booking(db, *args, **kwargs)
    except Exception as exc:                               # noqa: BLE001
        db.execute("ROLLBACK TO trade_provenance")
        try:
            db.execute("INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                       "VALUES (datetime('now'),'provenance_record_failed','','','journal',?)",
                       (_encode({"trade_id": args[0] if args else None,
                                 "error": type(exc).__name__}),))
        except Exception:                                  # noqa: BLE001
            pass
        log.warning("trade provenance not recorded (%s)", type(exc).__name__)
    db.execute("RELEASE trade_provenance")


# ── owner read ───────────────────────────────────────────────────────────────
def _dec(text):
    try:
        v = Decimal(str(text))
        return v if v.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _has(query, table: str) -> bool:
    return bool(query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _cols(query, table: str) -> set:
    return {r["name"] for r in query(f"PRAGMA table_info({table})", ())}


def exit_status(raw: str | None, market) -> str:
    """One vocabulary for every reader. The R1 draft's 'EXACT' was decided
    without market-scoped order ids and is read as LEGACY_EXACT_UNSCOPED;
    nothing without a known market scope reads as an exact link."""
    if raw == LEGACY_EXACT:
        return "LEGACY_EXACT_UNSCOPED"
    if raw == EXACT_LINK and market not in MARKET_TYPES:
        return "LEGACY_EXACT_UNSCOPED"
    return raw or UNKNOWN


def _exit_detail(raw_json) -> dict:
    detail = json.loads(raw_json) if raw_json else {}
    if "open_siblings_same_symbol" in detail:           # R1 draft key, same meaning
        detail["journal_open_siblings_same_symbol"] = detail.pop("open_siblings_same_symbol")
    detail["sibling_evidence_scope"] = SIBLING_SCOPE
    detail["venue_position_exclusivity"] = VENUE_EXCLUSIVITY
    return detail


def _terminal(trade: dict, legs: list, out_legs: list) -> dict:
    """The explicit terminal-close record of a closed trade. Only a leg whose
    own receipt moved the trade open -> closed counts; a partial exit or a
    final_exit label never stands in for it."""
    if trade.get("status") != "closed":
        return {"status": "NOT_APPLICABLE", "reason": f"trade status is {trade.get('status')}"}
    marked = [o for leg, o in zip(legs, out_legs) if leg.get("terminal_close") == 1]
    if not marked:
        legacy = any(leg.get("terminal_close") is None and str(leg.get("kind", "")).startswith(
            "close:") for leg in legs)
        return {"status": UNKNOWN if legacy else UNAVAILABLE, "leg_id": None,
                "reason": "legacy close leg predates the terminal-close marker" if legacy
                else "the trade is closed but no terminal-close provenance was recorded "
                     "(the recording failed or predates it); no other leg substitutes"}
    if len(marked) > 1:
        return {"status": AMBIGUOUS, "leg_id": None,
                "reason": "more than one leg is marked terminal"}
    leg = marked[0]
    gaps = []
    if leg["market_type"] not in MARKET_TYPES:
        gaps.append("market_type_unknown")
    if leg["origin"] != "luffy_order":
        gaps.append(f"closed_by_{leg['origin']}_{leg['purpose']}_without_luffy_order")
    elif leg["order"]["status"] != VERIFIED:
        gaps.append("luffy_order_without_venue_order_id")
    if leg["exit_attribution"]["status"] != EXACT_LINK:
        gaps.append(f"exit_attribution_{leg['exit_attribution']['status']}")
    if leg["fill_coverage"] != "COMPLETE":
        gaps.append(f"fill_coverage_{leg['fill_coverage']}")
    return {"status": VERIFIED if not gaps else PARTIAL, "leg_id": leg["leg_id"],
            "purpose": leg["purpose"], "gaps": gaps,
            "basis": "the leg whose booking receipt moved the trade open -> closed"}


def read(query, trade_id: str) -> dict | None:
    """Everything recorded about one trade's provenance, with every gap named.

    `query(sql, params) -> list[dict]`. Statuses: VERIFIED (exact recorded
    evidence), AMBIGUOUS (evidence exists but cannot be bound to one trade),
    UNAVAILABLE / UNKNOWN (not recorded), ESTIMATED (a model, never a venue
    figure). A closed trade is VERIFIED only with a verified terminal close.
    """
    rows = query("SELECT * FROM trades WHERE id=?", (trade_id,))
    if not rows:
        return None
    trade = rows[0]
    missing = []
    raw = trade.get("entry_identity_json")
    identity = None
    if raw:
        try:
            identity = json.loads(raw)
        except ValueError:
            identity = {"status": AMBIGUOUS, "reason": "entry_identity_unparseable"}
    if identity is None:
        strategy = {"status": UNKNOWN, "strategy_id": trade.get("strategy_id"),
                    "reason": "no strategy version was recorded when this trade opened; "
                              "the current registry spec is not substituted"}
        missing.append("strategy_version_at_entry")
    else:
        strategy = identity

    legs, fills, observed, unscoped = [], [], [], []
    if _has(query, "trade_legs"):
        legs = query("SELECT * FROM trade_legs WHERE trade_id=? ORDER BY id", (trade_id,))
    if _has(query, "trade_fills"):
        scoped = "market_type" in _cols(query, "trade_fills")
        every = query("SELECT * FROM trade_fills WHERE trade_id=? ORDER BY venue_ts_ms, id",
                      (trade_id,))
        # a fill keyed before market scoping has an unknown scope: shown, never counted
        fills = [f for f in every if scoped and f.get("market_type") in MARKET_TYPES]
        unscoped = [f for f in every if f not in fills]
        observed = query(
            "SELECT * FROM trade_fills WHERE attribution<>? AND "
            "json_extract(observation_json,'$.trade_id')=? ORDER BY venue_ts_ms, id",
            (ATTRIBUTED, trade_id))
    if not legs:
        missing.append("execution_legs")

    by_leg: dict = {}
    for f in fills:
        by_leg.setdefault(f["leg_id"], []).append(f)
    out_legs = []
    fee_totals: dict = {}
    fee_states = []
    for leg in legs:
        market = leg.get("market_type")
        lf = [f for f in by_leg.get(leg["id"], []) if f["market_type"] == market]
        qty = sum((f["qty"] or 0) for f in lf)
        booked = leg["booked_qty"]
        if not lf:
            coverage = UNAVAILABLE
        elif booked and math.isclose(qty, booked, rel_tol=1e-9, abs_tol=1e-12):
            coverage = "COMPLETE"
        else:
            coverage = PARTIAL
        fees = [(_dec(f["commission"]), f["commission_asset"]) for f in lf]
        exact = [(v, a) for v, a in fees if v is not None and a]
        if coverage == "COMPLETE" and len(exact) == len(lf):
            fee_state = VERIFIED
        elif exact:
            fee_state = PARTIAL
        elif leg["fee_basis"] in ("estimated_order_booking", "estimated"):
            fee_state = ESTIMATED
        else:
            fee_state = UNAVAILABLE
        by_asset: dict = {}
        for v, a in exact:
            by_asset[a] = by_asset.get(a, Decimal(0)) + v
            fee_totals[a] = fee_totals.get(a, Decimal(0)) + v
        fee_states.append(fee_state)
        reference = json.loads(leg["reference_json"]) if leg["reference_json"] else None
        exit_detail = (_exit_detail(leg["exit_attribution_json"])
                       if leg["exit_attribution"] != "NOT_APPLICABLE" else {})
        out_legs.append({
            "leg_id": leg["id"], "booking_id": leg["booking_id"], "kind": leg["kind"],
            "purpose": leg["purpose"], "origin": leg["origin"], "side": leg["side"],
            "market_type": market if market in MARKET_TYPES else None,
            "market_scope": VERIFIED if market in MARKET_TYPES else UNKNOWN,
            "status_transition": leg.get("status_transition"),
            "terminal_close": (None if leg.get("terminal_close") is None
                               else bool(leg["terminal_close"])),
            "order": {"venue_order_id": leg["venue_order_id"],
                      "client_order_id": leg["client_order_id"],
                      "protective_algo_id": leg["protective_algo_id"],
                      "status": leg["order_identity"]},
            "requested_qty": leg["requested_qty"], "booked_qty": booked,
            "booked_price": leg["booked_price"],
            "exit_attribution": {"status": exit_status(leg["exit_attribution"], market),
                                 **exit_detail},
            "fills": [_fill_out(f) for f in lf], "fill_coverage": coverage,
            "fill_qty": qty if lf else None,
            "commission": {"status": fee_state,
                           "verified_by_asset": {a: str(v) for a, v in by_asset.items()},
                           "note": "estimate is folded into booked P&L; not a venue figure"
                           if fee_state == ESTIMATED else None},
            "reference": reference, "source": leg["source"]})

    entry_legs = [l for l in out_legs if l["purpose"] in ENTRY_PURPOSES]
    exit_legs = [l for l in out_legs if l["purpose"] not in NON_EXIT_PURPOSES]
    terminal = _terminal(trade, legs, out_legs)
    if not entry_legs:
        missing.append("entry_order")
    if not fills:
        missing.append("verified_fills")
    if any(l["market_scope"] != VERIFIED for l in out_legs) or unscoped:
        missing.append("market_scope")
    if any(l["exit_attribution"]["status"] != EXACT_LINK for l in exit_legs):
        missing.append("exact_luffy_order_link")
    if terminal["status"] not in (VERIFIED, "NOT_APPLICABLE"):
        missing.append("terminal_close")
    commission_status = (VERIFIED if fee_states and all(s == VERIFIED for s in fee_states)
                         else PARTIAL if any(s in (VERIFIED, PARTIAL) for s in fee_states)
                         else ESTIMATED if any(s == ESTIMATED for s in fee_states)
                         else UNAVAILABLE)
    if commission_status != VERIFIED:
        missing.append("verified_commission")
    entry_ref = entry_legs[0]["reference"] if entry_legs else None
    complete = (strategy.get("status") == VERIFIED and legs and entry_legs and not unscoped
                and terminal["status"] in (VERIFIED, "NOT_APPLICABLE")
                and all(l["market_scope"] == VERIFIED for l in out_legs)
                and all(l["order"]["status"] == VERIFIED for l in out_legs
                        if l["origin"] == "luffy_order")
                and all(l["fill_coverage"] == "COMPLETE" for l in out_legs
                        if l["origin"] == "luffy_order")
                and all(l["exit_attribution"]["status"] == EXACT_LINK for l in exit_legs)
                and all(l["origin"] == "luffy_order" for l in exit_legs)
                and commission_status == VERIFIED)
    status = ("VERIFIED" if complete else UNKNOWN if identity is None and not legs
              else PARTIAL)
    return {
        "schema_version": READ_VERSION, "trade_id": trade_id, "provenance_status": status,
        "trade_status": trade.get("status"),
        "strategy_entry_identity": strategy,
        "decision_id": trade.get("decision_id") or None,
        "legs": out_legs,
        "terminal_close": terminal,
        "exit_attribution_meaning": (
            f"{EXACT_LINK}: the venue order Luffy recorded for this exit is recorded for this "
            "journal trade and no other; it does not assert exclusive ownership of the venue "
            "position. Sibling checks read the journal only."),
        "unattributed_observations": [_fill_out(f) for f in observed],
        "legacy_unscoped_fills": [_fill_out(f) for f in unscoped],
        "commission": {"status": commission_status,
                       "verified_by_asset": {a: str(v) for a, v in fee_totals.items()},
                       "basis": "sum of venue commission on fills attributed by exact order id "
                                "within the trade's market type"},
        "slippage_basis": entry_ref or {"status": UNAVAILABLE,
                                        "reason": "no pre-submission reference recorded"},
        "funding": {"status": UNAVAILABLE,
                    "reason": "per-trade funding is not attributed; account-level funding "
                              "is never assigned to individual trades"},
        "missing": missing,
    }


def _fill_out(f: dict) -> dict:
    commission_ok = _dec(f["commission"]) is not None and bool(f["commission_asset"])
    return {"venue_fill_id": f["venue_fill_id"], "venue_order_id": f["venue_order_id"],
            "market_type": f.get("market_type"),
            "attribution": f["attribution"], "attribution_reason": f["attribution_reason"],
            "leg": f["leg"], "side": f["side"], "qty": f["qty"], "price": f["price"],
            "commission": f["commission"], "commission_asset": f["commission_asset"],
            "commission_status": VERIFIED if commission_ok and f["attribution"] == ATTRIBUTED
            else UNAVAILABLE if not commission_ok else AMBIGUOUS,
            "realized_pnl": f["realized_pnl"], "venue_ts_ms": f["venue_ts_ms"],
            "observed_ms": f["observed_ms"], "source": f["source"]}
