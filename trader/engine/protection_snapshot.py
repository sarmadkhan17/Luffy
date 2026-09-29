"""Read-only protection snapshot: current venue protection truth, as evidence.

The Supervisor verifies protection at boot, in RECOVERY and on an owner
request — and repairs as it goes (adopt, ghost-close, orphan sweep, re-arm).
This module answers the owner's question — "is every venue position
protected right now?" — in every control state, without touching anything.

Capabilities (R2). The snapshot never receives an exchange, a Journal, the
Supervisor, the state machine, the Executor or any callback into them. It
receives an ``Observer`` holding exactly:

  * ``VenueReads`` (``venue_reads.py``): ``positions()``, ``algo_orders()``,
    ``open_orders(symbol)`` and a pure ``TickPrecision`` table, backed by a
    dedicated client whose transport refuses every non-GET / non-allow-listed
    request before sending;
  * ``JournalReads``: a path. Each read opens a SQLite ``mode=ro`` connection
    with an authorizer that denies everything but SELECT/READ, and ATTACH
    disabled; one read transaction per call;
  * ``supervisor_busy``: the Supervisor pass lock's ``locked`` (a lock, not
    the Supervisor).

Coherent observation. One logical venue state is proven, or nothing is
VERIFIED:

    J1 → positions P1 → stops A1 → positions P2 → stops A2 → ordinary orders → J2

Each venue read is one request (the algo listing is global). What is proven:

  * matching samples: P1 == P2 over *every* non-zero row (duplicates kept;
    either read being contradictory blocks VERIFIED), A1 == A2 over the raw
    algo rows (unique ids, quantity, trigger, type, reduce-only, and the
    venue's updateTime when supplied), J1 == J2 (open trades, re-arm
    evidence, entry-recovery ledger, supervisor status, control state,
    latest control-event id — the last is monotonic);
  * position continuity: equal samples alone do NOT prove the interval
    (1 → 2 → 1 returns equal samples). VERIFIED additionally requires every
    position's venue ``updateTime`` to be a valid epoch-millisecond integer
    at both reads, and equal. That is evidence the venue *reported* no
    position update in between — conditional on Binance's updateTime
    semantics (changes on every position update, only then), which are
    unverified and must be confirmed at the pre-deploy gate; it is not an
    unconditional proof. Absent or malformed → ``observation_continuity_unproven``;
  * no Supervisor overlap: a pass completing anywhere inside the bracket
    changes J2; a pass still holding its lock after J2 is refused.

Any failure → PARTIAL ``observation_*``, never VERIFIED. Ordinary orders are
read inside the bracket (so activity during them is caught) but serve
reconciliation cleanliness only; they never support a protection claim.

Interpretation is pure (``evaluate``): ``protective.open_stops`` normalizes
the algo listing and ``protective.protection_match`` decides adequacy, with
venue tick sizes from exchangeInfo (never a six-decimal fallback).

Status:
  VERIFIED   observation consistent; every venue position has journal and
             venue quantity that agree, no pending/malformed re-arm or entry
             recovery evidence, an adequate stop, and a trigger on a known tick
  PARTIAL    readable, but some fact is not verified (reasons say which)
  UNREADABLE venue positions or the stop listing could not be read

Reconciliation cleanliness (``cleanliness``) is separate and never changes
status: orphan/unrecognised stops a sweep would cancel, reduce-only closes,
entry orders, reversal-capable orders, stale re-arm records.

Persistence: one row in ``protection_evidence`` ordered by (boot, seq) — boot
from an AUTOINCREMENT table, seq an in-process counter; never wall clock and
never a value parsed out of stored JSON. Publication uses its own short-lived
connection with a bounded busy timeout, never the Journal's write lock.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from ..core.types import norm_symbol
from . import protective
from . import recovery as entry_recovery
from .reconcile import REARM_KEY

log = logging.getLogger(__name__)

SCHEMA = 2
TABLE = "protection_evidence"
BOOTS = "protection_boots"
SOURCE = "kernel protection monitor (read-only venue snapshot)"
STATUSES = ("VERIFIED", "PARTIAL", "UNREADABLE")
STALE_AFTER_S = 120.0          # 2 × the 60 s cadence; owner_api mirrors this
MAX_REASONS = 64
MAX_ITEMS = 50
MAX_ORDINARY_SYMBOLS = 16
STOP_TYPES = ("STOP_MARKET", "STOP")
JOURNAL_KEYS = ("control_state", REARM_KEY, entry_recovery.KEY, "supervisor_status")


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts,
                                  timezone.utc).isoformat()


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class VenueReadTimeout(TimeoutError):
    """The snapshot's read budget is spent; no further venue call is made."""


class Budget:
    def __init__(self, deadline: float, clock=time.monotonic):
        self.deadline = deadline
        self.clock = clock

    def check(self) -> None:
        if self.clock() > self.deadline:
            raise VenueReadTimeout("snapshot_read_budget_spent")


# ── journal: read-only by construction ────────────────────────────────────────
_READ_ACTIONS = frozenset({sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                           sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION})


def _authorize(action, *_):
    return sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY


def ro_connect(path, timeout_s: float = 2.0) -> sqlite3.Connection:
    """A connection that cannot write: mode=ro, SELECT-only authorizer, no ATTACH."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_s, isolation_level=None)
    conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    conn.set_authorizer(_authorize)
    conn.row_factory = sqlite3.Row
    return conn


class JournalReads:
    """The snapshot's only journal capability: one consistent read of facts."""

    __slots__ = ("_path",)

    def __init__(self, path):
        object.__setattr__(self, "_path", str(path))

    def __setattr__(self, name, value):
        raise AttributeError("JournalReads is immutable")

    def facts(self) -> dict:
        conn = ro_connect(self._path)
        try:
            conn.execute("BEGIN")
            trades = [dict(r) for r in conn.execute(
                "SELECT id, symbol, side, amount, stop_loss, sl_order_id, market_type, "
                "opened_at FROM trades WHERE status='open' ORDER BY id")]
            marks = ",".join("?" * len(JOURNAL_KEYS))
            kv = {r["key"]: r["value"] for r in conn.execute(
                f"SELECT key, value FROM state_kv WHERE key IN ({marks})", JOURNAL_KEYS)}
            event = conn.execute("SELECT COALESCE(MAX(id), 0) FROM control_events").fetchone()[0]
            conn.execute("COMMIT")
        finally:
            conn.close()
        return {"trades": trades, "kv": kv, "control_event_id": int(event)}


class Observer:
    """Everything a snapshot may use. No mutation authority is reachable."""

    __slots__ = ("reads", "journal", "supervisor_busy", "unavailable")

    def __init__(self, reads, journal: JournalReads, supervisor_busy=None,
                 unavailable: str | None = None):
        object.__setattr__(self, "reads", reads)
        object.__setattr__(self, "journal", journal)
        object.__setattr__(self, "supervisor_busy", supervisor_busy or (lambda: False))
        object.__setattr__(self, "unavailable", unavailable)

    def __setattr__(self, name, value):
        raise AttributeError("Observer is immutable")

    @property
    def precision(self):
        return getattr(self.reads, "precision", None)


# ── observation (reads only) ──────────────────────────────────────────────────
def _classify(exc: BaseException | None) -> str:
    seen = 0
    while exc is not None and seen < 5:
        names = {c.__name__ for c in type(exc).__mro__}
        if names & {"VenueRateBudget"}:
            return "venue_rate_budget"
        if names & {"ReadOnlyViolation"}:
            return "venue_read_refused"
        if names & {"TimeoutError", "RequestTimeout", "VenueReadTimeout",
                    "ReadTimeout", "ConnectTimeout"}:
            return "venue_timeout"
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return "venue_state_unreadable"


def observe(observer: Observer, *, budget: Budget) -> dict:
    """Read J1, P1, A1, P2, A2, ordinary orders, J2 (one bracket). Never writes."""
    obs = {"started_wall": time.time(), "requests": [], "journal_before": None,
           "journal_after": None, "positions_before": None, "algo": None,
           "positions_after": None, "algo_after": None, "supervisor_busy_after": None,
           "venue_error": None, "venue_error_type": None, "journal_error": None,
           "ordinary": {}, "ordinary_unread": [], "rate": None, "window_ms": None}
    reads = observer.reads

    def call(name, fn, *args):
        budget.check()
        obs["requests"].append(name)
        return fn(*args)

    def journal():
        try:
            return observer.journal.facts()
        except Exception as exc:
            obs["journal_error"] = type(exc).__name__
            return None

    if reads is None:
        obs["venue_error"] = "venue_reader_unavailable:" + str(observer.unavailable or "none")
        return obs
    obs["journal_before"] = journal()
    t0 = time.monotonic()
    try:
        obs["positions_before"] = call("positions", reads.positions)
        obs["algo"] = call("algo_orders", reads.algo_orders)
        obs["positions_after"] = call("positions", reads.positions)
        obs["algo_after"] = call("algo_orders", reads.algo_orders)
    except Exception as exc:
        obs["venue_error"] = _classify(exc)
        obs["venue_error_type"] = type(exc).__name__
    if obs["venue_error"] is None:
        # cleanliness only — never a protection claim — but read *inside* the
        # bracket, so Supervisor activity during these reads is detected too
        symbols = sorted({str(p.get("symbol")) for p in obs["positions_before"] or []
                          if (_num(p.get("contracts")) or 0) > 0})
        for i, sym in enumerate(symbols):
            if i >= MAX_ORDINARY_SYMBOLS:
                obs["ordinary_unread"].extend(symbols[i:])
                break
            try:
                obs["ordinary"][sym] = call("open_orders", reads.open_orders, sym)
            except Exception as exc:
                obs["ordinary_unread"].extend(symbols[i:])
                obs["ordinary_error"] = _classify(exc)
                break
    obs["window_ms"] = round((time.monotonic() - t0) * 1000, 1)
    obs["journal_after"] = journal()
    try:
        obs["supervisor_busy_after"] = bool(observer.supervisor_busy())
    except Exception:
        obs["supervisor_busy_after"] = True
    try:
        obs["rate"] = reads.rate_state()
    except Exception:
        obs["rate"] = None
    return obs


# ── evaluation (pure) ─────────────────────────────────────────────────────────
class _AlgoView:
    """Lets ``protective.open_stops`` interpret already-read data."""

    def __init__(self, rows):
        self._rows = rows

    def fapiPrivateGetOpenAlgoOrders(self):
        return self._rows


class _KV:
    def __init__(self, kv: dict):
        self._kv = kv

    def kv_get(self, key, default=None):
        return self._kv.get(key, default)


def _positions(rows) -> tuple[dict, bool, list]:
    """Validated as reconcile's verify mode does. Returns (by symbol,
    contradictory, every non-zero row's identity). The identity keeps every
    row — duplicates included — so a dict that drops one cannot hide it."""
    if not isinstance(rows, list):
        raise ValueError("invalid position snapshot")
    out: dict[str, dict] = {}
    ident: list = []
    contradictory = False
    for p in rows:
        amount = float(p["contracts"])
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("invalid venue position size")
        if amount > 0:
            key = norm_symbol(p["symbol"])
            if key in out or p.get("side") not in ("long", "short"):
                contradictory = True
            out[key] = p
            ident.append(tuple(map(repr, (key, p.get("side"), amount,
                                          _num(p.get("entryPrice")), p.get("updateTime")))))
    return out, contradictory, sorted(ident)


UPDATE_TIME_MIN_MS = 1_483_228_800_000     # 2017-01-01: before Binance USD-M existed
UPDATE_TIME_MAX_MS = 4_102_444_800_000     # 2100-01-01


def _update_time(value) -> int | None:
    """A venue position updateTime, or None when it is not valid evidence.

    Only a plain integer (or a string of ASCII digits) of epoch milliseconds
    in a plausible range counts. Booleans, floats, negatives, "NaN", empty,
    dicts and arbitrary text are rejected — never read as continuity."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        ms = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        ms = int(value)
    else:
        return None
    return ms if UPDATE_TIME_MIN_MS <= ms <= UPDATE_TIME_MAX_MS else None


def _stop_identity(rows) -> list:
    """Raw algo rows, venue updateTime included when the venue supplies it."""
    rows = rows.get("orders", []) if isinstance(rows, dict) else (rows or [])
    return sorted(tuple(map(repr, (str(o.get("algoId") or ""), str(o.get("symbol") or ""),
                                   str(o.get("side") or "").lower(), _num(o.get("quantity")),
                                   _num(o.get("triggerPrice")),
                                   str(o.get("orderType") or o.get("type") or ""),
                                   str(o.get("reduceOnly")), o.get("updateTime"))))
                  for o in rows)


def _fingerprint(facts: dict | None) -> str | None:
    return None if facts is None else json.dumps(facts, sort_keys=True, default=str)


def _rearms(raw) -> dict | None:
    """Reconcile's own validity rule; None when malformed."""
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or any(
            not isinstance(sym, str) or not isinstance(rec, dict)
            or not isinstance(rec.get("trade_id"), str) for sym, rec in value.items()):
        return None
    return value


def _closing(side: str | None) -> str | None:
    return {"long": "sell", "short": "buy"}.get(side)


def _stop_row(s: dict) -> dict:
    return {"id": str(s.get("id") or ""), "symbol": s.get("symbol"), "side": s.get("side"),
            "amount": _num(s.get("amount")), "stop_price": _num(s.get("stop_price")),
            "order_type": s.get("order_type"), "reduce_only": s.get("reduce_only"),
            "kind": s.get("kind")}


def _ordinary_item(o: dict, symbol: str, position_side: str | None) -> dict:
    info = o.get("info") or {}
    typ = str(info.get("origType") or info.get("type") or o.get("type") or "").upper()
    ro = str(o.get("reduceOnly", info.get("reduceOnly", ""))).lower() == "true"
    side = str(o.get("side") or "").lower()
    closing = side == _closing(position_side)
    stop_price = _num(info.get("stopPrice") or o.get("stopPrice"))
    if typ in STOP_TYPES:
        cls, severity = "ordinary_stop_order", "attention"   # never counted as protection
    elif ro and closing:
        cls, severity = "reduce_only_close", "info"          # TP / limit close
    elif not ro and closing:
        cls, severity = "reversal_capable_order", "attention"
    else:
        cls, severity = "entry_order", "attention"
    return {"class": cls, "severity": severity, "id": str(o.get("id") or ""),
            "symbol": symbol, "side": side, "type": typ, "reduce_only": ro,
            "amount": _num(o.get("amount")), "price": _num(o.get("price")),
            "stop_price": stop_price,
            "sweep_may_cancel": stop_price is not None}


def _skeleton(boot: int, seq: int, started: float) -> dict:
    return {"schema": SCHEMA, "generation": {"boot": int(boot), "seq": int(seq)},
            "source": SOURCE, "checked_at": _iso(started), "completed_at": None,
            "stale_after_s": STALE_AFTER_S, "status": "UNREADABLE",
            "checks": {"venue_positions": False, "observation_consistent": False,
                       "reconciliation": False, "venue_protection": False,
                       "precision_known": False},
            "complete_listing": False, "listing_reason": None,
            "observation": {"positions_stable": None, "stops_stable": None,
                            "position_continuity": None,
                            "journal_stable": None, "supervisor_pass_overlap": None,
                            "window_ms": None},
            "control_state_observed": None, "position_count": None, "symbols": [],
            "cleanliness": {"status": "UNKNOWN", "coverage": "position_symbols",
                            "items": [], "items_truncated": False, "unread_symbols": []},
            "reasons": [], "venue_requests": 0, "venue_request_names": [],
            "rate": None, "duration_ms": None, "mutations": 0, "pid": os.getpid()}


def unreadable(boot: int, seq: int, reason: str, *, state: str | None = None) -> dict:
    """Failure evidence with its own (newer) generation — never old VERIFIED."""
    snap = _skeleton(boot, seq, time.time())
    snap.update(reasons=[reason], completed_at=snap["checked_at"], duration_ms=0,
                control_state_observed=state)
    return snap


def evaluate(obs: dict, precision, *, boot: int, seq: int) -> dict:
    """Pure: turn one observation into evidence. No I/O, no authority."""
    from .venue_reads import TickPrecision
    precision = precision if precision is not None else TickPrecision({})
    snap = _skeleton(boot, seq, obs["started_wall"])
    reasons: list[str] = []
    items: list[dict] = []
    jb, ja = obs.get("journal_before"), obs.get("journal_after")
    snap["control_state_observed"] = ((jb or {}).get("kv") or {}).get("control_state")
    snap["venue_requests"] = len(obs.get("requests") or [])
    snap["venue_request_names"] = list(obs.get("requests") or [])
    snap["rate"] = obs.get("rate")
    snap["observation"]["window_ms"] = obs.get("window_ms")
    c = snap["checks"]

    def finish():
        snap["reasons"] = list(dict.fromkeys(reasons))[:MAX_REASONS]
        snap["completed_at"] = _iso()
        cl = snap["cleanliness"]
        cl["items_truncated"] = len(items) > MAX_ITEMS
        cl["items"] = items[:MAX_ITEMS]
        if not (c["venue_positions"] and snap["complete_listing"]):
            snap["status"] = "UNREADABLE"
        elif all(c.values()) and not snap["reasons"]:
            snap["status"] = "VERIFIED"
        else:
            snap["status"] = "PARTIAL"
        if snap["status"] == "UNREADABLE" or cl["unread_symbols"]:
            cl["status"] = "UNKNOWN"
        elif any(i["severity"] == "attention" for i in items):
            cl["status"] = "ATTENTION"
        else:
            cl["status"] = "INFO" if items else "CLEAN"
        return snap

    if obs.get("venue_error"):
        reasons.append(obs["venue_error"])
        snap["listing_reason"] = obs.get("venue_error_type")
        return finish()
    try:
        pos1, contradictory1, ident1 = _positions(obs["positions_before"])
        pos2, contradictory2, ident2 = _positions(obs["positions_after"])
        contradictory = contradictory1 or contradictory2   # both reads must be sound
    except (KeyError, TypeError, ValueError) as exc:
        reasons.append("venue_state_unreadable")
        snap["listing_reason"] = type(exc).__name__
        return finish()
    c["venue_positions"] = True
    snap["position_count"] = len(pos1)
    try:
        stops = protective.open_stops(_AlgoView(obs["algo"]), strict=True)
        stops_after = protective.open_stops(_AlgoView(obs["algo_after"]), strict=True)
        snap["complete_listing"] = bool(stops.complete and stops_after.complete)
        snap["listing_reason"] = stops.reason or stops_after.reason
    except Exception as exc:
        snap["listing_reason"] = getattr(exc, "reason", None) or type(exc).__name__
        reasons.append("protection_snapshot_unreadable:" + str(snap["listing_reason"]))
        return finish()

    # observation consistency — one logical venue + journal state
    positions_stable = ident1 == ident2
    stops_stable = _stop_identity(obs["algo"]) == _stop_identity(obs["algo_after"])
    # Equal samples alone do not prove the interval between them (1 → 2 → 1).
    # Continuity additionally needs a *valid* venue updateTime (integer epoch
    # ms in a plausible range) on every position in both reads; equal valid
    # values mean the venue reported no position update in between — under
    # Binance's updateTime semantics, which the pre-deploy gate must confirm.
    stamps = [_update_time(p.get("updateTime"))
              for p in list(pos1.values()) + list(pos2.values())]
    continuity = all(t is not None for t in stamps)
    journal_stable = jb is not None and ja is not None and _fingerprint(jb) == _fingerprint(ja)
    overlap = obs.get("supervisor_busy_after") is not False
    snap["observation"].update(positions_stable=positions_stable, stops_stable=stops_stable,
                               position_continuity=continuity,
                               journal_stable=journal_stable,
                               supervisor_pass_overlap=overlap)
    if not positions_stable:
        reasons.append("observation_inconsistent:venue_positions_changed")
    if not stops_stable:
        reasons.append("observation_inconsistent:stop_listing_changed")
    if positions_stable and not continuity:
        missing = any(p.get("updateTime") in (None, "")
                      for p in list(pos1.values()) + list(pos2.values()))
        reasons.append("observation_continuity_unproven:position_update_time_"
                       + ("missing" if missing else "invalid"))
    if jb is None or ja is None:
        reasons.append("journal_unreadable")
    elif not journal_stable:
        reasons.append("observation_inconsistent:journal_changed")
    if overlap:
        reasons.append("observation_inconsistent:supervisor_pass_overlap")
    c["observation_consistent"] = (positions_stable and stops_stable and continuity
                                   and journal_stable and not overlap)

    # journal facts (as of J1; equal to J2 when consistent)
    agreement: list[str] = []
    if contradictory:
        agreement.append("contradictory_venue_ownership")
    trades: dict[str, dict] = {}
    for t in (jb or {}).get("trades") or []:
        if t["symbol"] in trades:
            agreement.append("contradictory_journal_ownership")
        trades[t["symbol"]] = t
    kv = (jb or {}).get("kv") or {}
    rearms = _rearms(kv.get(REARM_KEY))
    if rearms is None:
        agreement.append("protection_rearm_evidence_unreadable")
    intent = None
    try:
        intent = entry_recovery.read(_KV(kv))
    except Exception:
        agreement.append("critical_recovery_state_unreadable")
    if intent:
        agreement.append("entry_recovery_pending")

    # per venue position
    by_key: dict[str, list[dict]] = {}
    for s in stops:
        by_key.setdefault(protective.venue_key(s.get("symbol", "")), []).append(s)
    live_keys = {protective.venue_key(sym) for sym in pos1}
    protected_all = precision_all = True
    for sym, p in sorted(pos1.items()):
        side = p.get("side")
        qty = _num(p.get("contracts"))
        t = trades.get(sym)
        own = by_key.get(protective.venue_key(sym), [])
        journal_qty = _num((t or {}).get("amount"))
        expected = _num((t or {}).get("stop_loss"))
        journal_stop_id = str((t or {}).get("sl_order_id") or "") or None
        tick = precision.tick(sym)
        rec = {"symbol": sym, "venue_symbol": str(p.get("symbol")), "side": side,
               "quantity": qty, "entry_price": _num(p.get("entryPrice")),
               "journal_trade_id": (t or {}).get("id"), "journal_side": (t or {}).get("side"),
               "journal_amount": journal_qty, "quantity_agrees": None,
               "journal_stop_id": journal_stop_id, "expected_stop": expected,
               "stop_present": any(s.get("order_type") in STOP_TYPES for s in own),
               "stop_ids": [str(s.get("id") or "") for s in own],
               "stop_id": None, "stop_side": None, "reduce_only": None, "order_type": None,
               "stop_kind": None, "covered_quantity": None, "trigger_price": None,
               "tick_size": tick, "precision_status": "UNKNOWN", "precision_valid": None,
               "stop_id_matches_journal": None, "match_reason": None,
               "rearm_evidence": None, "verified": False, "reasons": []}
        mine: list[str] = []
        if t is None:
            mine.append("untracked_venue_position:" + sym)       # reconcile would adopt
        else:
            if t.get("side") != side:
                mine.append("contradictory_position_side:" + sym)
            if journal_qty is None or journal_qty <= 0:
                mine.append("journal_quantity_unknown:" + sym)
            elif qty is None or abs(journal_qty - qty) > max(qty * 0.01, 1e-9):
                mine.append("journal_size_drift:" + sym)        # reconcile would align
            else:
                rec["quantity_agrees"] = True
        if rearms is None:
            rec["rearm_evidence"] = "UNREADABLE"
            mine.append("protection_rearm_evidence_unreadable")
        elif sym in rearms:
            rec["rearm_evidence"] = "PENDING"
            if t is None or rearms[sym].get("trade_id") != t.get("id"):
                mine.append("protection_rearm_evidence_mismatch:" + sym)
            mine.append("protection_rearm_pending:" + sym)       # awaiting verification
        else:
            rec["rearm_evidence"] = "NONE"
        if isinstance(intent, dict) and norm_symbol(str(intent.get("symbol") or "")) == sym:
            mine.append("entry_owned_exposure_pending:" + sym)
        matches = [(s, protective.protection_match(precision, sym, side, qty or 0.0,
                                                   expected or 0.0, s)) for s in own]
        adequate = [(s, m) for s, m in matches if m.matches]
        chosen = (adequate[0] if adequate else
                  next(((s, m) for s, m in matches if s.get("order_type") in STOP_TYPES),
                       matches[0] if matches else None))
        if chosen is not None:
            s, m = chosen
            rec.update(stop_id=str(s.get("id") or "") or None, stop_side=s.get("side"),
                       reduce_only=s.get("reduce_only"), order_type=s.get("order_type"),
                       stop_kind=s.get("kind"), covered_quantity=_num(s.get("amount")),
                       trigger_price=_num(s.get("stop_price")), match_reason=m.reason)
        if not adequate:
            protected_all = False
            if not rec["stop_present"]:
                mine.append("position_unprotected:" + sym)
            elif tick is None:
                mine.append("stop_adequacy_unknown:" + sym)     # needs the venue tick
            else:
                mine.append(f"stop_inadequate:{sym}:{chosen[1].reason if chosen else 'none'}")
        on_tick = precision.on_tick(sym, rec["trigger_price"]) if adequate else None
        if tick is None:
            precision_all = False
            mine.append("price_precision_unknown:" + sym)
        elif adequate:
            rec["precision_status"] = "VALID" if on_tick else "INVALID"
            rec["precision_valid"] = bool(on_tick)
            if not on_tick:
                mine.append("stop_trigger_precision_invalid:" + sym)
        # cleanliness: what a reconcile sweep / policy would act on
        keep = {journal_stop_id} | {str(((rearms or {}).get(sym) or {}).get("order_id") or "")}
        if journal_stop_id is not None and own:
            rec["stop_id_matches_journal"] = journal_stop_id in rec["stop_ids"]
        for s in own:
            sid = str(s.get("id") or "")
            if journal_stop_id is None or sid in keep:
                continue        # the sweep keeps every stop on a live symbol without a known id
            is_chosen = adequate and s is adequate[0][0]
            if is_chosen:
                cls, sev = "stop_id_mismatch", "attention"   # sweep would cancel, then re-arm
            elif (s.get("reduce_only") is True and s.get("side") == _closing(side)
                  and s.get("order_type") not in STOP_TYPES):
                cls, sev = "reduce_only_close", "info"
            else:
                cls, sev = "unrecognised_stop", "attention"
            items.append({"class": cls, "severity": sev, "sweep_may_cancel": True,
                          "position_symbol": sym, **_stop_row(s)})
        rec["verified"] = bool(adequate) and rec["precision_status"] == "VALID" and not mine
        rec["reasons"] = list(dict.fromkeys(mine))
        snap["symbols"].append(rec)
        reasons.extend(rec["reasons"])

    for sym, t in trades.items():
        if sym not in pos1 and (t.get("market_type") or "futures") == "futures":
            agreement.append("journal_position_absent_on_venue:" + sym)  # reconcile would ghost
    for sym in sorted(rearms or {}):
        if protective.venue_key(sym) not in live_keys:
            items.append({"class": "stale_rearm_evidence", "severity": "attention",
                          "symbol": sym, "sweep_may_cancel": False})
    for key, rows in sorted(by_key.items()):
        if key not in live_keys:
            for s in rows:
                items.append({"class": "orphan_stop", "severity": "attention",
                              "sweep_may_cancel": True, **_stop_row(s)})
    for vsym, rows in sorted((obs.get("ordinary") or {}).items()):
        key = norm_symbol(vsym)
        side = (pos1.get(key) or {}).get("side")
        for o in rows or []:
            items.append(_ordinary_item(o, key, side))
    snap["cleanliness"]["unread_symbols"] = list(obs.get("ordinary_unread") or [])

    reasons.extend(agreement)
    per_position_agreement = all(
        r["quantity_agrees"] and r["rearm_evidence"] == "NONE"
        and not any(x.startswith(("contradictory_position_side", "entry_owned_exposure"))
                    for x in r["reasons"])
        for r in snap["symbols"])
    c["reconciliation"] = not agreement and per_position_agreement
    c["venue_protection"] = protected_all and all(
        r["precision_status"] == "VALID" for r in snap["symbols"])
    c["precision_known"] = precision_all
    return finish()


# ── persistence: generation-ordered, own bounded connection ──────────────────
_SCHEMA_SQL = (
    f"CREATE TABLE IF NOT EXISTS {BOOTS}("
    "boot INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, pid INTEGER NOT NULL)",
    f"CREATE TABLE IF NOT EXISTS {TABLE}("
    "slot INTEGER PRIMARY KEY CHECK (slot = 1), "
    "boot INTEGER NOT NULL CHECK (typeof(boot) = 'integer' AND boot > 0), "
    "seq INTEGER NOT NULL CHECK (typeof(seq) = 'integer' AND seq > 0), "
    "value TEXT NOT NULL CHECK (json_valid(value)))",
)
# Newer (boot, seq) wins; equal is rejected. A stored row that no allocated
# boot could have written (boot beyond the counter, or this boot's seq beyond
# what this process issued), or with a non-integer identity, never blocks.
_UPSERT = (
    f"INSERT INTO {TABLE}(slot, boot, seq, value) VALUES (1, :boot, :seq, :value) "
    "ON CONFLICT(slot) DO UPDATE SET boot = excluded.boot, seq = excluded.seq, "
    "value = excluded.value WHERE "
    f"typeof({TABLE}.boot) != 'integer' OR typeof({TABLE}.seq) != 'integer' "
    f"OR NOT json_valid({TABLE}.value) "
    f"OR excluded.boot > {TABLE}.boot "
    f"OR (excluded.boot = {TABLE}.boot AND excluded.seq > {TABLE}.seq) "
    f"OR {TABLE}.boot > (SELECT COALESCE(MAX(boot), 0) FROM {BOOTS}) "
    f"OR ({TABLE}.boot = :boot AND {TABLE}.seq > :max_seq)")


class SnapshotStore:
    """Publication path. Never the Journal's write lock; bounded busy wait."""

    def __init__(self, path, *, timeout_s: float = 2.0):
        self.path = str(path)
        self.timeout_s = float(timeout_s)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=self.timeout_s, isolation_level=None)

    def _tx(self, fn):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                out = fn(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return out
        finally:
            conn.close()

    def allocate_boot(self) -> int:
        def run(conn):
            for stmt in _SCHEMA_SQL:
                conn.execute(stmt)
            boot = conn.execute(f"INSERT INTO {BOOTS}(started_at, pid) VALUES (?, ?)",
                                (_iso(), os.getpid())).lastrowid
            conn.execute(f"DELETE FROM {BOOTS} WHERE boot < ?", (boot - 16,))
            return int(boot)
        return self._tx(run)

    def publish(self, snap: dict, *, boot: int, seq: int, max_seq: int) -> bool:
        value = json.dumps(snap, allow_nan=False, default=str)

        def run(conn):
            cur = conn.execute(_UPSERT, {"boot": int(boot), "seq": int(seq),
                                         "value": value, "max_seq": int(max_seq)})
            return cur.rowcount == 1
        return bool(self._tx(run))

    def read(self) -> dict | None:
        conn = ro_connect(self.path, self.timeout_s)
        try:
            row = conn.execute(f"SELECT boot, seq, value FROM {TABLE} WHERE slot = 1").fetchone()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
        return None if row is None else {"boot": row["boot"], "seq": row["seq"],
                                         "value": json.loads(row["value"])}



# ── monitor: fixed-start cadence, single flight, bounded ─────────────────────
def _check(observer: Observer, gen: tuple[int, int], budget: Budget, box: dict) -> None:
    """The worker. It holds an Observer and nothing else — no store, no
    monitor — so a check can never publish; only the monitor thread can, and
    only a result whose recorded completion time is within the deadline.

    ``done_at`` is the monotonic time the finished evidence existed, stamped
    by the worker itself, so a monitor that is descheduled past the deadline
    still judges the worker by when it actually completed."""
    try:
        snap = evaluate(observe(observer, budget=budget), observer.precision,
                        boot=gen[0], seq=gen[1])
    except Exception as exc:
        snap = unreadable(*gen, "snapshot_internal_error:" + type(exc).__name__)
    box["done_at"] = budget.clock()            # set before "snap": readers see both
    box["snap"] = snap


class ProtectionMonitor:
    """Kernel-owned cadence for the read-only snapshot, in every control state.

    * fixed-start cadence on the monotonic clock: slot k starts at t0 + k·I.
      A long check never shifts later slots; a slot that passes while a check
      runs is skipped, never replayed (no catch-up burst);
    * single flight: a worker still inside a venue call blocks the next start
      (its slot publishes UNREADABLE ``previous_check_still_running``);
    * single publisher: workers cannot publish (``_check`` has no store). The
      monitor thread publishes a worker's result only if it completed before
      the deadline; a result that arrives later is discarded unseen, whether
      or not the timeout evidence could be published. No lock is shared with
      a worker, so no timeout decision ever waits behind one;
    * bounded: reads stop at 75 % of the timeout; at the timeout a newer
      UNREADABLE ``venue_timeout`` is published; shutdown interrupts the wait;
    * never waits on, or takes, the Supervisor's pass lock: a slot that finds
      a pass running retries after ``defer_s`` within the same slot;
    * publication has its own connection and busy timeout; failure leaves the
      previous evidence to age to STALE from its own ``checked_at``.
    """

    def __init__(self, observer: Observer, store: SnapshotStore, *,
                 interval_s: float = 60.0, timeout_s: float = 20.0, defer_s: float = 5.0,
                 clock=time.monotonic, wait=None):
        self.observer = observer
        self.store = store
        self.interval_s = float(interval_s)
        self.timeout_s = float(timeout_s)
        self.defer_s = float(defer_s)
        self.clock = clock
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait
        self._boot: int | None = None
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._orphan: dict | None = None      # a timed-out check's box (never published)
        self._slot: float | None = None
        self._retry_at = 0.0
        self.starts: deque[float] = deque(maxlen=64)   # recent start times (diagnostics)
        self.stats = {"runs": 0, "timeouts": 0, "deferred_supervisor": 0,
                      "blocked_by_running_check": 0, "persist_rejected": 0,
                      "late_discarded": 0, "publish_failed": 0, "skipped_slots": 0,
                      "stopped_mid_check": 0}
        try:                        # allocate the boot identity up front when possible
            self._boot = self.store.allocate_boot()
        except Exception as exc:    # retried lazily; nothing publishes without one
            log.warning("protection snapshot boot not allocated yet: %s", type(exc).__name__)

    # generation -------------------------------------------------------------
    def _generation(self) -> tuple[int, int] | None:
        with self._seq_lock:
            if self._boot is None:
                try:
                    self._boot = self.store.allocate_boot()
                except Exception as exc:
                    self.stats["publish_failed"] += 1
                    log.warning("protection snapshot boot not allocated: %s", type(exc).__name__)
                    return None
            self._seq += 1
            return self._boot, self._seq

    def _publish(self, snap: dict, gen: tuple[int, int]) -> bool:
        """Monitor thread only (under ``_run_lock``)."""
        try:
            ok = self.store.publish(snap, boot=gen[0], seq=gen[1], max_seq=self._seq)
        except Exception as exc:
            self.stats["publish_failed"] += 1
            log.warning("protection snapshot not published: %s", type(exc).__name__)
            return False
        if not ok:
            self.stats["persist_rejected"] += 1
        return ok

    def _state(self) -> str | None:
        try:
            return self.observer.journal.facts()["kv"].get("control_state")
        except Exception:
            return None

    def collect_late(self) -> None:
        """Account for a timed-out check that has since finished (diagnostics)."""
        if self._orphan is not None and "snap" in self._orphan:
            self.stats["late_discarded"] += 1
            self._orphan = None

    # one check --------------------------------------------------------------
    def _run(self) -> tuple[str, dict | None]:
        if not self._run_lock.acquire(blocking=False):
            return "busy_caller", None
        try:
            self.collect_late()
            if self._worker is not None and self._worker.is_alive():
                self.stats["blocked_by_running_check"] += 1
                gen = self._generation()
                if gen is None:
                    return "no_generation", None
                snap = unreadable(*gen, "previous_check_still_running", state=self._state())
                self._publish(snap, gen)
                return "blocked", snap
            try:
                busy = bool(self.observer.supervisor_busy())
            except Exception:
                busy = True
            if busy:
                self.stats["deferred_supervisor"] += 1
                return "deferred", None
            gen = self._generation()
            if gen is None:
                return "no_generation", None
            box: dict = {}
            start = self.clock()
            deadline = start + self.timeout_s
            worker = threading.Thread(
                target=_check, daemon=True, name="protection-snapshot-check",
                args=(self.observer, gen, Budget(start + self.timeout_s * 0.75, self.clock),
                      box))
            self._worker = worker
            self.starts.append(start)
            worker.start()
            while worker.is_alive() and not self._stop.is_set():
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                worker.join(min(0.05, remaining))
            # the decision: accepted only if the worker's own completion time
            # is within the deadline — never "whenever the monitor looked"
            self.stats["runs"] += 1
            done = "snap" in box and box["done_at"] <= deadline
            if not done:
                self._orphan = box
                if self._stop.is_set():
                    self.stats["stopped_mid_check"] += 1
                    return "stopped", None
                self.stats["timeouts"] += 1
                gen2 = self._generation()
                if gen2 is None:
                    return "timeout", None
                snap = unreadable(*gen2, "venue_timeout", state=self._state())
                self._publish(snap, gen2)
                log.warning("protection snapshot timed out after %.0fs", self.timeout_s)
                return "timeout", snap
            snap = box["snap"]
            self._publish(snap, gen)
            return "ran", snap
        finally:
            self._run_lock.release()

    def run_once(self) -> dict | None:
        """One bounded check. Returns the evidence produced, or None if skipped."""
        return self._run()[1]

    # cadence ------------------------------------------------------------------
    def _advance(self) -> None:
        self._slot += self.interval_s
        now = self.clock()
        while self._slot < now:                  # never replay missed slots
            self._slot += self.interval_s
            self.stats["skipped_slots"] += 1
        self._retry_at = 0.0

    def stop(self) -> None:
        self._stop.set()

    def loop(self, stopped=lambda: False, tick_s: float = 0.5) -> None:
        self._slot = self.clock()
        last = None
        while not (self._stop.is_set() or stopped()):
            now = self.clock()
            due = max(self._slot, self._retry_at)
            if now >= due:
                try:
                    outcome, snap = self._run()
                except Exception as exc:          # never kill the thread
                    log.warning("protection snapshot failed: %s", type(exc).__name__)
                    outcome, snap = "error", None
                if outcome == "deferred" and self.clock() + self.defer_s < self._slot + self.interval_s:
                    self._retry_at = self.clock() + self.defer_s
                else:
                    if outcome == "deferred":
                        self.stats["skipped_slots"] += 1
                    self._advance()
                if snap is not None and snap["status"] != last:
                    log.info("protection snapshot %s %s", snap["status"],
                             ",".join(snap["reasons"][:4]))
                    last = snap["status"]
                continue
            self._wait(min(tick_s, due - now))


def make_monitor(kernel_exchange, db_path, *, supervisor_busy, interval_s=60.0,
                 timeout_s=20.0, publish_timeout_s=2.0, env=None) -> ProtectionMonitor:
    """Kernel setup: a dedicated read client, a read-only journal, a store.

    If the read client cannot be built, the monitor still runs and publishes
    UNREADABLE ``venue_reader_unavailable:<reason>`` — visible, never silent.
    """
    from .venue_reads import make_venue_reads
    reads, why = None, None
    try:
        reads = make_venue_reads(kernel_exchange, env=env,
                                 timeout_ms=int(min(8.0, timeout_s * 0.4) * 1000))
    except Exception as exc:
        why = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        log.warning("protection snapshot reader unavailable: %s", why)
    observer = Observer(reads, JournalReads(db_path), supervisor_busy, unavailable=why)
    return ProtectionMonitor(observer, SnapshotStore(db_path, timeout_s=publish_timeout_s),
                             interval_s=interval_s, timeout_s=timeout_s)
