"""OwnerService — the single executor of owner operations. Kernel process only.

Every adapter's request ends here, either in-process (the kernel's Telegram
listener) or through the local IPC server (dashboard, OpenClaw/WhatsApp, CLI),
which authenticates the channel before calling execute(). This module holds
no exchange client, Risk manager or Supervisor of its own: the kernel hands
it its ControlStateMachine and its guarded `owner_resume`.

Control request lifecycle (one row in `owner_requests`):

  RESERVED  the first delivery that authorizes and passes the age check
            durably reserves the request id (INSERT, primary key) with a
            reservation token — at the earliest admission point (Telegram
            dispatch, IPC receipt, or service entry). Nothing can execute a
            request id that is not reserved, and only the reservation owner
            (matching token) may claim it.
  PENDING   the reservation owner claimed it (claim + intake audit, one
            transaction) and is executing.
  DONE      a final result is recorded (executed, or refused after
            reservation, e.g. busy / superseded). Replays return it.

A duplicate never receives a definitive refusal because "no row yet": it
either finds the reservation (IN_PROGRESS), the recorded result (replay), an
unresolvable claim (OUTCOME_UNKNOWN), or — only if it reserves first — becomes
the executor itself. Definitive REFUSED is returned only when the request can
never execute: unauthorized, malformed, stale, or refused *by the reservation
owner* (recorded DONE, so every later delivery sees the same refusal). A
reservation left by a crashed kernel was never claimed, so it provably never
executed: it is settled once as REFUSED `abandoned_before_execution` and never
re-run. A claim left by a crashed kernel stays OUTCOME_UNKNOWN.

Recovery never queues: a second, different recovery while one is running is
refused at once (recorded) and holds no connection.
Reservation write failure is not a terminal refusal: reconcile the same id
again, or report OUTCOME_UNKNOWN if the journal is unreadable or the id is
absent. Another delivery may still reserve or execute it; this failed
delivery never claims or executes it.
Every request, reads and refusals included, leaves one `owner_audit` row:
controls synchronously; reads and IPC ingress refusals in batches (≤64 rows or
~2 s). A batch leaves memory only after its transaction commits, so a failed
write loses nothing; a *process crash* can lose at most the unflushed window
of read/ingress rows (never a control record). Normal flushing loses nothing.
Only a *prolonged journal failure* that fills the buffer (10 000 rows) makes
it evict the oldest rows not in a committing batch; exactly those evictions
are counted and the count is written when the journal recovers.

Idempotency is bounded by retention: a completed request id is remembered for
`request_retention_days` (≥7 d and ≥2× the max request age). A redelivery of
the original request inside that window replays its result; after it, the
original is refused as stale by age. Only a *new* request that reuses an old
id with a fresh issue time would execute again.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from types import SimpleNamespace

from ..core.types import ControlState
from ..engine.control_fence import latest_intent_event_id, persisted_state
from ..engine.state import OWNER_ACTORS
from ..engine.supervisor import OwnerContext
from .authz import Authorizer, actor_for
from .contract import (CONTAINMENT_OPERATIONS, RECOVERY_OPERATIONS, OwnerRequest,
                       OwnerResult, Status, refused)

log = logging.getLogger("owner_interface")

SCHEMA_VERSION = "3"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS owner_requests (
    request_id  TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    operation   TEXT NOT NULL,
    channel     TEXT NOT NULL,
    identity    TEXT NOT NULL,
    principal   TEXT NOT NULL,
    request_ref TEXT,
    state       TEXT NOT NULL,           -- RESERVED | PENDING | DONE
    result      TEXT,
    boot_id     TEXT NOT NULL,
    reserver    TEXT NOT NULL,           -- reservation token; only its owner claims
    intent_event_id INTEGER,             -- control-intent watermark at admission
    created_at  TEXT NOT NULL,
    finished_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS owner_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    request_id  TEXT,
    operation   TEXT,
    channel     TEXT,
    identity    TEXT,
    principal   TEXT,
    request_ref TEXT,
    status      TEXT NOT NULL,
    reasons     TEXT NOT NULL,
    state_before TEXT,
    state_after  TEXT,
    event_ids   TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS owner_audit_ts ON owner_audit(ts)",
    "CREATE INDEX IF NOT EXISTS owner_requests_finished ON owner_requests(finished_at)",
)
_REQUEST_COLUMNS = {"request_id", "fingerprint", "operation", "channel", "identity",
                    "principal", "request_ref", "state", "result", "boot_id",
                    "reserver", "intent_event_id", "created_at", "finished_at"}
_RECOVERY_STATUS = {"ACTIVATED": Status.ACTIVATED, "CONTAINED": Status.CONTAINED,
                    "ALREADY_ACTIVE": Status.ALREADY_SET, "REFUSED": Status.REFUSED,
                    "BUSY": Status.REFUSED}


@dataclass(frozen=True)
class Reservation:
    """Proof that this delivery owns the request id (see module docstring)."""
    request_id: str
    token: str
    principal: str
    intent_event_id: int | None      # control-intent watermark at admission


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


class OwnerService:
    def __init__(self, journal, state_machine, *, resume, snapshot=None,
                 authorizer: Authorizer | None = None, clock=time.time,
                 max_age_s: float = 300.0, future_skew_s: float = 60.0,
                 busy_wait_s: float = 10.0, request_retention_days: float = 7.0,
                 audit_retention_days: float = 30.0):
        self.journal = journal
        self.state_machine = state_machine
        self._resume = resume                  # Kernel.owner_resume (guarded path)
        self._snapshot = snapshot or (lambda: {})
        self.authorizer = authorizer or Authorizer()
        self.clock = clock
        self.max_age_s = float(max_age_s)
        self.future_skew_s = float(future_skew_s)
        self.busy_wait_s = float(busy_wait_s)
        # a completed id must outlive the replay window, or a late duplicate
        # could be re-evaluated; stale ones are refused by age anyway
        self.request_retention = timedelta(days=max(float(request_retention_days),
                                                    2 * self.max_age_s / 86400))
        self.audit_retention = timedelta(days=float(audit_retention_days))
        self.boot_id = uuid.uuid4().hex
        self._fast_lock = threading.Lock()      # containment + intents (milliseconds)
        self._recover_lock = threading.Lock()   # guarded recovery (a venue pass)
        self._recovering: str | None = None     # request id holding _recover_lock
        self._unresolved: set[str] = set()      # claimed here, result not recorded
        self._abandoned: set[str] = set()       # reserved here, settle not persisted
        self._writes = 0
        # read / ingress-refusal audit rows are batched: a durable commit per
        # read contends with the trading loop for the journal write lock.
        # Controls (and control refusals) are always written synchronously.
        self._audit_buffer: list[tuple[int, tuple]] = []   # (seq, row)
        self._audit_lock = threading.Lock()
        self._audit_oldest = 0.0
        self.audit_flush_rows, self.audit_flush_s = 64, 2.0
        self.audit_buffer_max = 10_000
        self._audit_dropped = 0
        self._audit_seq = 0
        self._audit_inflight: set[int] = set()
        self._recovery_watermark: int | None = None
        self._flush_lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.journal._tx() as c:
            for ddl in SCHEMA:
                c.execute(ddl)
            row = c.execute("SELECT value FROM state_kv WHERE key='owner_interface_schema'"
                            ).fetchone()
            found = row[0] if row else None
            if found not in (None, SCHEMA_VERSION):
                raise RuntimeError(f"owner_interface schema {found!r}, expected {SCHEMA_VERSION}")
            cols = {r[1] for r in c.execute("PRAGMA table_info(owner_requests)")}
            if cols != _REQUEST_COLUMNS:
                raise RuntimeError(f"owner_requests columns {sorted(cols)} do not match v{SCHEMA_VERSION}")
            c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES "
                      "('owner_interface_schema',?)", (SCHEMA_VERSION,))

    # ── entry point ───────────────────────────────────────────────────────
    def admission_watermark(self) -> int:
        """The control-intent watermark now: bind an admitted request to it."""
        return latest_intent_event_id(self.journal)

    def execute(self, req, *, reservation: Reservation | None = None,
                admitted_intent_event_id: int | None = None) -> OwnerResult:
        """Execute one request. A control request runs only under a reservation:
        the adapter's (reserved at admission) or one taken here on arrival.
        `admitted_intent_event_id` binds a recovery to the control intent at
        admission (the Supervisor refuses it as superseded inside its intake
        fence if any intent event landed since)."""
        if not isinstance(req, OwnerRequest):
            result = refused(None, "malformed_request")
            self._audit_row(None, None, result)
            return result
        if not req.is_control:
            principal, denial = self.authorizer.authorize(req.channel, req.identity,
                                                          req.operation)
            if denial:
                return self._finish_refusal(req, principal, denial)
            result = self._read(req, principal)
            self._audit_row(req, principal, result, buffered=True)
            return result
        if reservation is None:
            reserved = self.reserve(req, admitted_intent_event_id=admitted_intent_event_id)
            if isinstance(reserved, OwnerResult):
                return reserved
            reservation = reserved
        return self._control(req, reservation)

    def reserve(self, req: OwnerRequest, *, admitted_intent_event_id: int | None = None
                ) -> Reservation | OwnerResult:
        """Durably reserve a control request id, or answer from what exists.

        Returns a Reservation (this delivery owns the id) or an OwnerResult:
        REFUSED (unauthorized / stale — can never execute), a replayed final
        result, IN_PROGRESS, or OUTCOME_UNKNOWN. Never a definitive refusal
        merely because another delivery has not reached execution yet.
        """
        principal, denial = self.authorizer.authorize(req.channel, req.identity, req.operation)
        if denial:          # never reveals or touches a recorded request
            ids = (self._event("owner_interface_rejected", req, principal,
                               {"reasons": [denial]}),)
            result = refused(req, denial, principal=principal, audit_event_ids=ids)
            self._audit_row(req, principal, result)
            return result
        prior = self._existing(req, principal)
        if prior is not None:
            return prior
        now = self.clock()
        stale = ("request_stale" if req.issued_at < now - self.max_age_s else
                 "request_from_future" if req.issued_at > now + self.future_skew_s else None)
        watermark = admitted_intent_event_id
        if watermark is None and req.operation in RECOVERY_OPERATIONS:
            watermark = self.admission_watermark()
        token = uuid.uuid4().hex
        try:
            with self.journal._tx() as c:
                cur = c.execute(
                    "INSERT OR IGNORE INTO owner_requests(request_id, fingerprint, operation,"
                    " channel, identity, principal, request_ref, state, result, boot_id,"
                    " reserver, intent_event_id, created_at, finished_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (req.request_id, req.fingerprint(), req.operation, req.channel,
                     req.identity, principal, req.request_ref,
                     "DONE" if stale else "RESERVED",
                     json.dumps(refused(req, stale, principal=principal).to_wire())
                     if stale else None,
                     self.boot_id, token, watermark, _iso(), _iso() if stale else None))
                won = cur.rowcount == 1
        except Exception:
            log.exception("owner request reservation failed")
            return self._reconcile_reservation_failure(req, principal)
        if not won:                     # another delivery reserved it first
            return self._existing(req, principal) or self._finish_refusal(
                req, principal, "request_in_progress", Status.IN_PROGRESS)
        if stale:                       # recorded: every later delivery sees the same
            return self._finish_refusal(req, principal, stale)
        return Reservation(req.request_id, token, principal, watermark)

    def _reconcile_reservation_failure(self, req, principal) -> OwnerResult:
        """A failed delivery cannot establish a negative outcome for its ID.

        Another delivery may have reserved or completed the same request since
        our initial lookup. Reuse its durable answer when readable. Even an
        absent row cannot exclude a concurrent reservation, so absence or an
        unreadable journal is OUTCOME_UNKNOWN, never a definitive REFUSED.
        This delivery does not claim, execute, or automatically retry anything.
        """
        try:
            prior = self._existing(req, principal)
            if prior is not None:
                return prior
        except Exception:
            log.exception("owner reservation reconciliation failed")
        return self._finish_refusal(req, principal, "persistence_unavailable",
                                    Status.OUTCOME_UNKNOWN)

    def settle_refusal(self, req: OwnerRequest, reservation: Reservation,
                       reason: str) -> OwnerResult:
        """The reservation owner refuses its own request (busy, admission):
        recorded DONE so every other delivery gets the same answer."""
        principal = reservation.principal
        ids = (self._event("owner_interface_rejected", req, principal, {"reasons": [reason]}),)
        result = refused(req, reason, principal=principal, audit_event_ids=ids)
        try:
            with self.journal._tx() as c:
                c.execute("UPDATE owner_requests SET state='DONE', result=?, finished_at=?"
                          " WHERE request_id=? AND state='RESERVED' AND reserver=?",
                          (json.dumps(result.to_wire(), default=str), _iso(),
                           req.request_id, reservation.token))
                self._audit_insert(c, req, principal, result)
        except Exception:
            log.exception("owner refusal settle failed")
            self._abandoned.add(req.request_id)     # never claimed: never executes
        return result

    def record_admission_refusal(self, req: OwnerRequest, reason: str) -> OwnerResult:
        """An adapter cannot admit a delivery now (slot limit, recovery already
        admitted). Reserve first: an existing request is answered from its
        record (replay / IN_PROGRESS / OUTCOME_UNKNOWN); only a delivery that
        itself won the reservation is refused — recorded, so it stays refused."""
        reserved = self.reserve(req)
        if isinstance(reserved, OwnerResult):
            return reserved
        return self.settle_refusal(req, reserved, reason)

    # ── reads ─────────────────────────────────────────────────────────────
    def _read(self, req: OwnerRequest, principal: str) -> OwnerResult:
        state = persisted_state(self.journal)
        data = {"control_state": state.value if state else None}
        try:
            data.update(self._snapshot())
        except Exception as e:                   # a read never breaks the kernel
            data["snapshot_error"] = type(e).__name__
        if req.operation == "health":
            data["supervisor"] = self._supervisor_summary()
            data["owner_interface"] = {"boot_id": self.boot_id,
                                       "recovery_in_progress": self._recover_lock.locked()}
        return OwnerResult(req.request_id, req.operation, Status.ACCEPTED,
                           principal=principal, channel=req.channel,
                           request_ref=req.request_ref,
                           control_state_before=data["control_state"],
                           control_state_after=data["control_state"], data=data)

    def _supervisor_summary(self) -> dict | None:
        raw = self.journal.kv_get("supervisor_status")
        if not raw:
            return None
        try:
            s = json.loads(raw)
            return {k: s.get(k) for k in ("outcome", "stage", "safe_to_activate",
                                          "needs_owner", "reasons", "updated_at")}
        except (TypeError, ValueError):
            return {"error": "supervisor_status_unreadable"}

    # ── controls ──────────────────────────────────────────────────────────
    def _finish_refusal(self, req, principal, reason, status=Status.REFUSED) -> OwnerResult:
        result = refused(req, reason, status, principal=principal)
        self._audit_row(req, principal, result)
        return result

    def _control(self, req: OwnerRequest, res: Reservation) -> OwnerResult:
        principal = res.principal
        recovery = req.operation in RECOVERY_OPERATIONS
        lock = self._recover_lock if recovery else self._fast_lock
        # a recovery never parks behind another request's venue pass
        got = lock.acquire(blocking=False) if recovery else lock.acquire(
            timeout=self.busy_wait_s)
        if not got:
            return self.settle_refusal(req, res, "recovery_in_progress" if recovery
                                       else "owner_interface_busy")
        if recovery:
            self._recovering = req.request_id
            self._recovery_watermark = res.intent_event_id   # read only under _recover_lock
        try:
            now = self.clock()
            try:
                intake = self._claim(req, res, now)
            except Exception:
                log.exception("owner request claim failed")
                # the claim rolled back: still RESERVED by us, never executes
                self._abandoned.add(req.request_id)
                return self._finish_refusal(req, principal, "persistence_unavailable")
            if intake is None:          # not our reservation any more (never expected)
                return self._existing(req, principal) or refused(
                    req, "reservation_lost", Status.OUTCOME_UNKNOWN, principal=principal)
            try:
                result = self._execute(req, principal)
            except Exception as e:
                log.exception("owner operation %s failed", req.operation)
                result = refused(req, f"execution_error:{type(e).__name__}",
                                 Status.OUTCOME_UNKNOWN)
            result = result.with_(principal=principal, channel=req.channel,
                                  request_ref=req.request_ref)
            try:
                return self._complete(req, principal, intake, result)
            except Exception:
                log.exception("owner result persistence failed")
                self._unresolved.add(req.request_id)
                return refused(req, "result_not_recorded", Status.OUTCOME_UNKNOWN,
                               principal=principal,
                               data={"observed_status": result.status,
                                     "observed_state_after": result.control_state_after})
        finally:
            if recovery:
                self._recovering = None
            lock.release()

    def _execute(self, req: OwnerRequest, principal: str) -> OwnerResult:
        op = req.operation
        if op == "freeze":
            return self._contain(req, principal, ControlState.FROZEN)
        if op == "halt":
            return self._contain(req, principal, ControlState.HALTED)
        if op == "panic":
            return self._panic(req, principal)
        if op == "close_trade":
            return self._close_trade(req, principal)
        if op == "set_market_type":
            return self._set_market_type(req, principal)
        return self._recover(req, principal, allow_unhalt=op == "unhalt")

    def _detail(self, req: OwnerRequest, principal: str) -> str:
        return (f"owner {req.operation} via {req.channel} principal {principal} "
                f"request {req.request_id}")

    def _contain(self, req, principal, target: ControlState) -> OwnerResult:
        actor, detail = actor_for(req.channel), self._detail(req, principal)
        with self.state_machine.fenced() as f:
            before = f.state
            if before is None:
                return refused(req, "control_state_unreadable")
            if target == ControlState.HALTED:
                event_id = f.apply(ControlState.HALTED, actor, detail)
            else:
                event_id = f.apply(ControlState.FROZEN, actor, detail)
            after = f.state
        return OwnerResult(req.request_id, req.operation,
                           Status.ALREADY_SET if before == target else Status.ACCEPTED,
                           control_state_before=before.value,
                           control_state_after=after.value if after else None,
                           transition_event_id=event_id)

    def _intent_event(self, c, event: str, req, principal, detail: dict) -> int:
        cur = c.execute(
            "INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
            "VALUES (?,?,?,?,?,?)",
            (_iso(), event, "", "", actor_for(req.channel),
             json.dumps({"request_id": req.request_id, "principal": principal,
                         "channel": req.channel, **detail})))
        return int(cur.lastrowid)

    def _panic(self, req, principal) -> OwnerResult:
        """Accepting a panic is itself owner intent, and it contains at once.

        Under the control fence it applies an owner hold — FROZEN, or a HALTED
        hold when already HALTED (never de-escalates) — which advances the
        control-intent fence. Every older recovery/activation is bound to an
        earlier fence and its final compare-and-set fails. The pending panic
        records that hold's event id as its identity; the kernel flattens next
        cycle and treats only *owner* intent recorded after this id as newer.
        """
        with self.state_machine.fenced() as f:
            before = f.state
            if before is None:
                return refused(req, "control_state_unreadable")
            actor, detail = actor_for(req.channel), f"panic accepted: {self._detail(req, principal)}"
            if before == ControlState.HALTED:
                hold = f.apply(ControlState.HALTED, actor, detail)
            else:
                hold = f.apply(ControlState.FROZEN, actor, detail)
            after = f.state
            pending = json.dumps({"request_id": req.request_id, "intent_event_id": hold},
                                 sort_keys=True)
            with self.journal._tx() as c:
                c.execute("INSERT OR REPLACE INTO state_kv(key,value) "
                          "VALUES ('panic_requested',?)", (pending,))
                self._intent_event(c, "panic", req, principal,
                                   {"note": "contained now; flatten on the next cycle",
                                    "intent_event_id": hold})
        return OwnerResult(req.request_id, req.operation, Status.ACCEPTED,
                           control_state_before=before.value,
                           control_state_after=after.value if after else None,
                           reasons=("contained_flatten_queued_next_cycle",),
                           transition_event_id=hold)

    def _close_trade(self, req, principal) -> OwnerResult:
        trade_id = req.args["trade_id"]
        with self.journal._tx() as c:
            if not c.execute("SELECT 1 FROM trades WHERE id=? AND status='open'",
                             (trade_id,)).fetchone():
                return refused(req, "trade_not_open")
            row = c.execute("SELECT value FROM state_kv WHERE key='close_requests'").fetchone()
            try:
                pending = json.loads(row[0]) if row else []
                if not isinstance(pending, list):
                    pending = []
            except ValueError:
                pending = []
            already = trade_id in pending
            if not already:
                pending.append(trade_id)
                c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES ('close_requests',?)",
                          (json.dumps(pending),))
            event_id = self._intent_event(c, "manual_close", req, principal,
                                          {"trade_id": trade_id})
        return OwnerResult(req.request_id, req.operation,
                           Status.ALREADY_SET if already else Status.ACCEPTED,
                           reasons=("close_queued_next_cycle",), transition_event_id=event_id)

    def _set_market_type(self, req, principal) -> OwnerResult:
        market = req.args["market"]
        with self.journal._tx() as c:
            row = c.execute("SELECT value FROM state_kv WHERE key='market_type'").fetchone()
            before = row[0] if row else None
            if before != market:
                c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES ('market_type',?)",
                          (market,))
            event_id = self._intent_event(c, "mode_switch", req, principal,
                                          {"from": before, "to": market})
        return OwnerResult(req.request_id, req.operation,
                           Status.ALREADY_SET if before == market else Status.ACCEPTED,
                           reasons=("applies_at_next_kernel_start",),
                           transition_event_id=event_id)

    def _recover(self, req, principal, *, allow_unhalt: bool) -> OwnerResult:
        before = persisted_state(self.journal)
        ctx = OwnerContext(actor=actor_for(req.channel), channel=req.channel,
                           principal=principal, request_ref=req.request_ref,
                           meta={**req.meta, "identity": req.identity,
                                 "request_id": req.request_id})
        r = self._resume(ctx, allow_unhalt=allow_unhalt,
                         expected_intent_event_id=self._recovery_watermark)
        result_id, risk = self._supervisor_result(r.request_event_id)
        transition = self._activation_event(r.request_event_id) if r.status == "ACTIVATED" else None
        return OwnerResult(
            req.request_id, req.operation, _RECOVERY_STATUS.get(r.status, Status.OUTCOME_UNKNOWN),
            control_state_before=before.value if before else None,
            control_state_after=r.control_state, reasons=tuple(r.reasons),
            supervisor_outcome=r.outcome, risk_release=risk,
            transition_event_id=transition,
            audit_event_ids=tuple(i for i in (r.request_event_id, result_id) if i))

    def _supervisor_result(self, request_event_id) -> tuple[int | None, dict | None]:
        if not request_event_id:
            return None, None
        for row in self.journal.query(
                "SELECT id, detail FROM control_events WHERE event='owner_recovery_result' "
                "AND id>? ORDER BY id LIMIT 50", (int(request_event_id),)):
            try:
                detail = json.loads(row["detail"] or "{}")
            except ValueError:
                continue
            if detail.get("request_event_id") == int(request_event_id):
                return int(row["id"]), detail.get("risk_release")
        return None, None

    def _activation_event(self, request_event_id) -> int | None:
        if not request_event_id:
            return None
        rows = self.journal.query(
            "SELECT id FROM control_events WHERE event='state_change' AND to_state='ACTIVE' "
            "AND id>? ORDER BY id LIMIT 1", (int(request_event_id),))
        return int(rows[0]["id"]) if rows else None

    # ── idempotency record ────────────────────────────────────────────────
    def _row(self, request_id: str):
        rows = self.journal.query("SELECT * FROM owner_requests WHERE request_id=?",
                                  (request_id,))
        return rows[0] if rows else None

    def _existing(self, req: OwnerRequest, principal: str) -> OwnerResult | None:
        """The answer for an id that already has a row; None if it has none."""
        row = self._row(req.request_id)
        if row is None:
            return None
        if row["fingerprint"] != req.fingerprint():
            ids = (self._event("owner_interface_rejected", req, principal,
                               {"reasons": ["request_id_conflict"]}),)
            result = refused(req, "request_id_conflict", principal=principal,
                             audit_event_ids=ids)
            self._audit_row(req, principal, result)
            return result
        if row["state"] == "DONE":
            result = OwnerResult.from_wire(json.loads(row["result"])).with_(replayed=True)
            self._audit_row(req, principal, result, buffered=True)
            return result
        if row["boot_id"] != self.boot_id:
            if row["state"] == "RESERVED":
                # never claimed by the kernel that reserved it: provably never
                # executed. Settle it once; it is never re-run.
                return self._settle_orphan(req, principal, row)
            return self._finish_refusal(req, principal, "outcome_unknown_after_restart",
                                        Status.OUTCOME_UNKNOWN)
        if req.request_id in self._unresolved:
            return self._finish_refusal(req, principal, "outcome_unknown",
                                        Status.OUTCOME_UNKNOWN)
        if req.request_id in self._abandoned:
            return self._finish_refusal(req, principal, "persistence_unavailable")
        return self._finish_refusal(req, principal, "request_in_progress", Status.IN_PROGRESS)

    def _settle_orphan(self, req, principal, row) -> OwnerResult:
        result = refused(req, "abandoned_before_execution", principal=principal)
        with self.journal._tx() as c:
            c.execute("UPDATE owner_requests SET state='DONE', result=?, finished_at=?"
                      " WHERE request_id=? AND state='RESERVED' AND boot_id=?",
                      (json.dumps(result.to_wire()), _iso(), req.request_id, row["boot_id"]))
        return self._existing(req, principal) or result

    def _lookup(self, req: OwnerRequest, principal: str) -> OwnerResult | None:
        return self._existing(req, principal)      # revision-4 name

    def _claim(self, req: OwnerRequest, res: Reservation, now: float) -> int | None:
        """RESERVED → PENDING by the reservation owner only, + intake audit, one
        transaction. Returns the intake event id, or None if not ours."""
        with self.journal._tx() as c:
            cur = c.execute("UPDATE owner_requests SET state='PENDING' WHERE request_id=?"
                            " AND state='RESERVED' AND reserver=?",
                            (req.request_id, res.token))
            if cur.rowcount != 1:
                return None
            cur = c.execute(
                "INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                "VALUES (?,?,?,?,?,?)",
                (_iso(), "owner_interface_request", "", "", actor_for(req.channel),
                 json.dumps(self._event_detail(req, res.principal, {
                     "issued_at": req.issued_at, "age_s": round(now - req.issued_at, 3),
                     "admitted_intent_event_id": res.intent_event_id}))))
            return int(cur.lastrowid)

    def _complete(self, req, principal, intake: int, result: OwnerResult) -> OwnerResult:
        """Result audit + completion (+ audit row) in one transaction."""
        with self.journal._tx() as c:
            cur = c.execute(
                "INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                "VALUES (?,?,?,?,?,?)",
                (_iso(), "owner_interface_result", result.control_state_before or "",
                 result.control_state_after or "", actor_for(req.channel),
                 json.dumps(self._event_detail(req, principal, {
                     "intake_event_id": intake, "status": result.status,
                     "before": result.control_state_before,
                     "after": result.control_state_after,
                     "reasons": list(result.reasons),
                     "supervisor_outcome": result.supervisor_outcome,
                     "transition_event_id": result.transition_event_id,
                     "supervisor_event_ids": list(result.audit_event_ids),
                     "risk_release": result.risk_release}), default=str)))
            result = result.with_(audit_event_ids=(intake, *result.audit_event_ids,
                                                   int(cur.lastrowid)))
            c.execute("UPDATE owner_requests SET state='DONE', result=?, finished_at=?"
                      " WHERE request_id=? AND state='PENDING'",
                      (json.dumps(result.to_wire(), default=str), _iso(), req.request_id))
            self._audit_insert(c, req, principal, result)
        self.flush_audit()
        self._maybe_prune()
        log.info("owner_interface %s", json.dumps(self._event_detail(req, principal, {
            "status": result.status, "reasons": list(result.reasons)})))
        return result

    # ── audit ─────────────────────────────────────────────────────────────
    @staticmethod
    def _event_detail(req, principal, extra: dict) -> dict:
        return {"request_id": req.request_id, "operation": req.operation,
                "principal": principal, "channel": req.channel, "identity": req.identity,
                "request_ref": req.request_ref, "args": dict(req.args),
                "meta": dict(req.meta), **extra}

    def _event(self, event: str, req, principal, extra: dict) -> int:
        return self.journal.log_control_event(
            event, actor_for(req.channel),
            detail=json.dumps(self._event_detail(req, principal, extra), default=str))

    _AUDIT_SQL = ("INSERT INTO owner_audit(ts, request_id, operation, channel, identity,"
                  " principal, request_ref, status, reasons, state_before, state_after,"
                  " event_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")

    @staticmethod
    def _audit_values(req, principal, result: OwnerResult) -> tuple:
        return (_iso(), getattr(req, "request_id", None), getattr(req, "operation", None),
                getattr(req, "channel", None), getattr(req, "identity", None), principal,
                getattr(req, "request_ref", None),
                result.status + (" (replayed)" if result.replayed else ""),
                json.dumps(list(result.reasons)), result.control_state_before,
                result.control_state_after, json.dumps(list(result.audit_event_ids)))

    def _audit_insert(self, c, req, principal, result: OwnerResult) -> None:
        c.execute(self._AUDIT_SQL, self._audit_values(req, principal, result))

    def flush_audit(self) -> bool:
        """Write buffered read/ingress audit rows (the IPC server calls this ~2 s).

        Rows leave memory only after their transaction commits: a failed or
        rolled-back flush keeps them for the next attempt. One flush at a time,
        so no row is written twice. Returns True when the buffer was written.
        """
        if not self._flush_lock.acquire(blocking=False):
            return False                      # another flush is writing these rows
        try:
            with self._audit_lock:
                entries = list(self._audit_buffer)
                dropped = self._audit_dropped
                self._audit_inflight = {seq for seq, _values in entries}
            if not entries and not dropped:
                return True
            rows = [values for _seq, values in entries]
            if dropped:
                rows.append((_iso(), None, None, None, None, None, None, "AUDIT_DROPPED",
                             json.dumps([f"audit_buffer_overflow_dropped:{dropped}"]),
                             None, None, "[]"))
            try:
                with self.journal._tx() as c:
                    c.executemany(self._AUDIT_SQL, rows)
            except Exception:
                log.exception("owner audit flush failed; %d rows kept for retry", len(rows))
                return False
            written = {seq for seq, _values in entries}
            with self._audit_lock:            # committed: only now drop what was written
                self._audit_buffer = [e for e in self._audit_buffer if e[0] not in written]
                self._audit_dropped -= dropped
            self._maybe_prune()
            return True
        finally:
            with self._audit_lock:
                self._audit_inflight = set()
            self._flush_lock.release()

    def _audit_row(self, req, principal, result: OwnerResult, *, buffered=False) -> None:
        """Durable, bounded record of every request; never breaks the answer."""
        if buffered:
            with self._audit_lock:
                if not self._audit_buffer:
                    self._audit_oldest = time.monotonic()
                accept = True
                if len(self._audit_buffer) >= self.audit_buffer_max:
                    # journal unwritable for a long time. The buffer NEVER
                    # exceeds its cap: evict the oldest row that is not in a
                    # committing batch; if every row is in flight (they are
                    # about to be committed or restored), the new row is the
                    # one discarded. Either way exactly one row is dropped
                    # and counted; nothing is dropped silently.
                    for i, (seq, _values) in enumerate(self._audit_buffer):
                        if seq not in self._audit_inflight:
                            del self._audit_buffer[i]
                            break
                    else:
                        accept = False
                    self._audit_dropped += 1
                if accept:
                    self._audit_seq += 1
                    self._audit_buffer.append((self._audit_seq,
                                               self._audit_values(req, principal, result)))
                due = (len(self._audit_buffer) >= self.audit_flush_rows
                       or time.monotonic() - self._audit_oldest >= self.audit_flush_s)
            if due:
                self.flush_audit()
        else:
            try:
                with self.journal._tx() as c:
                    self._audit_insert(c, req, principal, result)
            except Exception:
                # never lost silently: keep it for the next flush instead
                log.exception("owner audit write failed; row buffered")
                return self._audit_row(req, principal, result, buffered=True)
            self.flush_audit()
            self._maybe_prune()
        log.info("owner_interface %s", json.dumps({
            "request_id": getattr(req, "request_id", None),
            "operation": getattr(req, "operation", None),
            "channel": getattr(req, "channel", None), "principal": principal,
            "status": result.status, "reasons": list(result.reasons)}))

    def record_ingress_refusal(self, channel: str | None, reason: str) -> None:
        """IPC refusals before a request exists (bad MAC, malformed frame)."""
        self._audit_row(SimpleNamespace(channel=channel), None,
                        OwnerResult(None, None, Status.REFUSED, channel=channel,
                                    reasons=(reason,)), buffered=True)

    def _maybe_prune(self) -> None:
        self._writes += 1
        if self._writes % 256:
            return
        now = datetime.now(timezone.utc)
        with self.journal._tx() as c:
            c.execute("DELETE FROM owner_requests WHERE state='DONE' AND finished_at < ?",
                      (_iso(now - self.request_retention),))
            c.execute("DELETE FROM owner_audit WHERE ts < ?", (_iso(now - self.audit_retention),))
