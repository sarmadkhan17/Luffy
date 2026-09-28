"""Observed safety evidence and Supervisor-owned containment for futures recovery."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..core.types import ControlState
from .control_fence import latest_intent_event_id, persisted_state
from .reconcile import reconcile_futures
from .risk import RiskRelease
from .state import OWNER_ACTORS

KEY = "supervisor_status"
OWNER_INTENT_REASONS = {
    "venue_or_recovery_unavailable", "venue_side_or_hedge_conflict",
    "journal_ownership_conflict", "journal_size_conflict",
    "entry_fill_position_mismatch", "protection_requires_verification",
    "flat_with_remaining_protection",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RecoveryResult:
    control_state_observed: str
    outcome: str
    stage: str
    safe_to_activate: bool
    needs_owner: bool
    containment_owned: bool = False
    containment_started_at: str | None = None
    containment_from: str | None = None
    containment_event_id: int | None = None
    needs_owner_since_control_event_id: int | None = None
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    actions: dict = field(default_factory=dict)
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class OwnerContext:
    """Transport-neutral identity of one owner request (the Owner Interface input).

    Every channel adapter (Telegram today; dashboard, LUFFY chat and the
    OpenClaw/WhatsApp gateway later) authenticates its own input and builds
    this; the Supervisor never sees a channel's native update object.

    actor:       owner authority class, must be in OWNER_ACTORS
    channel:     telegram | dashboard | chat | openclaw | whatsapp | test ...
    principal:   the channel-authenticated user identity, if the channel has one
    request_ref: the channel's own request/message reference, for audit joins
    meta:        small channel-neutral annotations (e.g. the owner's command)

    Carries no credentials and grants no Execution/Risk authority: the typed
    operations below decide everything from venue truth and control intent.
    """
    actor: str
    channel: str
    principal: str | None = None
    request_ref: str | None = None
    meta: dict = field(default_factory=dict)

    def audit(self) -> dict:
        return {"channel": self.channel, "principal": self.principal,
                "request_ref": self.request_ref, "meta": dict(self.meta)}


@dataclass(frozen=True)
class OwnerRecoveryResult:
    """Typed answer to an owner recovery or rollback-preparation request.

    status: ACTIVATED | CONTAINED | ALREADY_ACTIVE | REFUSED  (recovery)
            READY | BLOCKED | REFUSED                         (rollback)
    """
    status: str
    control_state: str | None
    outcome: str | None = None
    reasons: tuple[str, ...] = ()
    request_event_id: int | None = None
    hold_event_id: int | None = None      # rollback: this operation's own FROZEN hold


class Supervisor:
    """One bounded recovery pass at boot, then at most one per cadence."""

    def __init__(self, journal, state_machine, executor, exchange,
                 *, interval_s: float = 60.0, risk_release=None):
        self.journal = journal
        # Risk's fresh "may the system be ACTIVE now?" (a RiskRelease
        # callable). Every activation — boot, cadence, owner request — needs
        # it; unconfigured, unreadable or refused keeps the state contained.
        self.risk_release = risk_release
        self.state_machine = state_machine
        self.executor = executor
        self.exchange = exchange
        self.interval_s = interval_s
        self._next_pass = 0.0
        # One verification pass at a time in this process: two concurrent
        # reconciles could both see a naked position and both re-arm it. The
        # same lock serializes every supervisor_status writer (trigger, owner
        # request intake/acknowledgement), so an older pass cannot overwrite a
        # newer owner acknowledgement. Lock order: request → pass → fence.
        self._pass_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self.request_wait_s = 300.0

    def status(self) -> dict | None:
        raw = self.journal.kv_get(KEY)
        if not raw:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("reasons"), list):
            raise ValueError("invalid supervisor status")
        return value

    def _save(self, result: RecoveryResult) -> RecoveryResult:
        value = asdict(result)
        self.journal.kv_set(KEY, json.dumps(value, allow_nan=False))
        self.journal.log_control_event("supervisor_recovery", "supervisor", detail=value)
        return result

    def _last_event_id(self) -> int:
        rows = self.journal.query("SELECT COALESCE(MAX(id), 0) AS id FROM control_events")
        return int(rows[0]["id"])

    def _owner_ack(self, prior: dict | None, state: ControlState) -> int | None:
        if not prior or not prior.get("needs_owner") or state != ControlState.ACTIVE:
            return None
        watermark = prior.get("needs_owner_since_control_event_id")
        if watermark is None:
            return None  # legacy evidence has no provable acknowledgement boundary
        rows = self.journal.query(
            "SELECT id FROM control_events WHERE id>? AND event='state_change' "
            "AND to_state='ACTIVE' AND actor IN (?,?,?) "
            "ORDER BY id LIMIT 1", (int(watermark), *OWNER_ACTORS))
        return int(rows[0]["id"]) if rows else None

    def _owned(self, prior: dict | None, state: ControlState) -> bool:
        if not prior or not prior.get("containment_owned") or state not in (
                ControlState.FROZEN, ControlState.RECOVERY):
            return False
        event_id = prior.get("containment_event_id")
        if not event_id:
            return False
        return latest_intent_event_id(self.journal) == int(event_id)

    def _risk_now(self) -> RiskRelease:
        """Freshly ask Risk; anything but an explicit allow fails closed."""
        if self.risk_release is None:
            return RiskRelease(False, "risk_gate_unconfigured")
        try:
            release = self.risk_release()
        except Exception:
            return RiskRelease(False, "risk_state_unreadable")
        if not isinstance(release, RiskRelease):
            return RiskRelease(False, "risk_state_unreadable")
        return release

    @contextmanager
    def _risk_held(self, risk: RiskRelease):
        """Held across the final ACTIVE CAS: yields (refusal | None, conn).

        The fence watermark alone is not enough: Risk state (peak, latch,
        durable blob) can change without any control event. The proof's own
        verifier (RiskManager.hold_release) revalidates identity, generation,
        durable digest, latch and freshness, and holds the Risk lock plus the
        journal's write transaction until the CAS — written on `conn` —
        commits. A proof without a verifier cannot activate.
        """
        if not isinstance(risk, RiskRelease) or not risk.allowed:
            yield getattr(risk, "reason", None) or "risk_release_unproven", None
            return
        with self._held(risk.verify, risk) as held:
            yield held

    @contextmanager
    def _held(self, verifier, risk):
        """Enter a Risk verifier; normalize its yield to (refusal, conn)."""
        if verifier is None:
            yield "risk_proof_unverifiable", None
            return
        try:
            cm = verifier(risk)
            held = cm.__enter__()
        except Exception:
            yield "risk_proof_unverifiable", None
            return
        refusal, conn = held if isinstance(held, tuple) else (held, None)
        try:
            yield refusal, conn
        except BaseException:
            if not cm.__exit__(*__import__("sys").exc_info()):
                raise
        else:
            cm.__exit__(None, None, None)

    def _prior_status(self) -> dict | None:
        try:
            return self.status()
        except Exception:
            return {"needs_owner": True, "reasons": ["supervisor_status_unreadable"]}

    def _result(self, state: ControlState, prior: dict | None, owned: bool,
                **kwargs) -> RecoveryResult:
        return RecoveryResult(
            control_state_observed=state.value,
            containment_owned=owned,
            containment_started_at=(prior or {}).get("containment_started_at") if owned else None,
            containment_from=(prior or {}).get("containment_from") if owned else None,
            containment_event_id=(prior or {}).get("containment_event_id") if owned else None,
            started_at=(prior or {}).get("started_at") or _now(),
            **kwargs)

    def _contain_active(self, reason: str, prior: dict | None) -> tuple[bool, dict | None]:
        transition = self.state_machine.set_if_current(
            ControlState.ACTIVE, ControlState.FROZEN, "supervisor", reason)
        if not transition:
            return False, prior
        now = _now()
        evidence = self._result(ControlState.FROZEN, None, True,
                                outcome="RECOVERING", stage="CONTAIN",
                                safe_to_activate=False,
                                needs_owner=bool(prior and prior.get("needs_owner")),
                                needs_owner_since_control_event_id=(prior or {}).get(
                                    "needs_owner_since_control_event_id"),
                                reasons=list(dict.fromkeys([reason] +
                                    (prior or {}).get("reasons", []))))
        evidence.containment_started_at = now
        evidence.containment_from = ControlState.ACTIVE.value
        evidence.containment_event_id = transition.event_id
        self._save(evidence)  # no RECOVERY transition if ownership cannot be persisted
        return True, asdict(evidence)

    def _enter_recovery(self, prior: dict | None, state: ControlState, reason: str) -> bool:
        if state != ControlState.FROZEN or not self._owned(prior, state):
            return False
        transition = self.state_machine.set_if_current(
            ControlState.FROZEN, ControlState.RECOVERY, "supervisor", reason,
            expected_control_event_id=int(prior["containment_event_id"]))
        if not transition:
            return False
        prior["containment_event_id"] = transition.event_id
        return True

    def _acknowledge(self, prior: dict | None, state: ControlState) -> tuple[dict | None, dict]:
        ack_id = self._owner_ack(prior, state)
        if ack_id is None:
            return prior, {}
        actions = {"owner_ack_control_event_id": ack_id}
        self.journal.log_control_event("supervisor_owner_acknowledged", "supervisor",
                                       detail={"owner_active_event_id": ack_id,
                                               "hold_since": prior.get(
                                                   "needs_owner_since_control_event_id")})
        # Clear only the old approval hold. The caller must still establish
        # fresh venue and recovery safety before ACTIVE can remain permitted.
        self._save(self._result(state, None, False, outcome="DEGRADED",
                                stage="OWNER_ACKNOWLEDGED", safe_to_activate=False,
                                needs_owner=False, actions=actions.copy()))
        return None, actions

    def trigger(self, reason: str) -> None:
        """Contain an ACTIVE state; never appropriate an owner hold."""
        with self._pass_lock:
            self._trigger(reason)

    def _trigger(self, reason: str) -> None:
        prior, ack_actions = self._acknowledge(
            self._prior_status(), self.state_machine.refresh())
        owned, evidence = self._contain_active(reason, prior)
        if owned:
            self._enter_recovery(evidence, ControlState.FROZEN, reason)
        current = self.state_machine.refresh()
        owned = self._owned(evidence, current)
        self._save(self._result(current, evidence, owned,
                                outcome="RECOVERING" if owned else "DEGRADED",
                                stage="DETECT", safe_to_activate=False,
                                needs_owner=bool(prior and prior.get("needs_owner")),
                                needs_owner_since_control_event_id=(prior or {}).get(
                                    "needs_owner_since_control_event_id"),
                                actions=ack_actions,
                                reasons=list(dict.fromkeys([reason] + (prior or {}).get("reasons", [])))))

    def pass_once(self, *, boot: bool = False, advance_entry: bool = True) -> RecoveryResult:
        with self._pass_lock:
            return self._pass_once(boot=boot, advance_entry=advance_entry)

    def _pass_once(self, *, boot: bool, advance_entry: bool) -> RecoveryResult:
        prior = self._prior_status()
        state = self.state_machine.refresh()
        prior, actions = self._acknowledge(prior, state)
        owned = self._owned(prior, state)
        if state == ControlState.ACTIVE:
            owned, prior = self._contain_active(
                "boot_safety_verification" if boot else "recovery_pass", prior)
            state = self.state_machine.refresh()
        if self._enter_recovery(prior, state, "supervisor-owned containment"):
            state = ControlState.RECOVERY
        owned = self._owned(prior, state)

        reasons = []
        checks = {"venue_positions": False, "reconciliation": False,
                  "entry_intent_resolved": False, "venue_protection": False,
                  "entries_safe": False}
        if prior and "supervisor_status_unreadable" in prior.get("reasons", []):
            reasons.append("supervisor_status_unreadable")
        intent = None
        ledger_readable = True
        if state != ControlState.HALTED:
            try:
                intent = self.executor.recovery.pending()
            except Exception:
                ledger_readable = False
                reasons.append("critical_recovery_state_unreadable")
            if (ledger_readable and intent and advance_entry
                    and self.state_machine.refresh() != ControlState.HALTED):
                try:
                    self.executor.recover_entries()
                    actions["entry_recovery_tick"] = True
                    intent = self.executor.recovery.pending()
                except Exception:
                    ledger_readable = False
                    reasons.append("critical_recovery_state_unreadable")
            checks["entry_intent_resolved"] = ledger_readable and intent is None
            if intent:
                reasons.append("entry_recovery_pending")
                actions["entry_reason"] = intent.get("reason", "pending")
                actions["entry_attempts"] = intent.get("attempts", 0)
        else:
            reasons.append("halted_manual_only")

        if ledger_readable and state != ControlState.HALTED:
            excluded = [intent["symbol"]] if isinstance(intent, dict) and intent.get("symbol") else []
            try:
                report = reconcile_futures(self.exchange, self.journal,
                                           exclude_symbols=excluded, verify=True)
            except Exception as exc:
                report = {"error": type(exc).__name__, "positions_readable": False}
            actions["reconcile"] = report
            checks["venue_positions"] = report.get("positions_readable") is True
            issues = report.get("safety_issues")
            checks["reconciliation"] = (checks["venue_positions"]
                                        and isinstance(issues, list) and not issues)
            checks["venue_protection"] = checks["reconciliation"] and not excluded
            if not checks["venue_positions"]:
                reasons.append("venue_state_unreadable")
            if issues:
                reasons.extend(issues)
            if excluded:
                reasons.append("entry_owned_exposure_pending")
        checks["entries_safe"] = all((checks["venue_positions"], checks["reconciliation"],
                                      checks["entry_intent_resolved"],
                                      checks["venue_protection"])) and not reasons
        serious = bool(set(reasons) & {
            "venue_state_unreadable", "critical_recovery_state_unreadable",
            "supervisor_status_unreadable", "protection_cannot_restore",
            "orphan_stop_sweep_unresolved", "protection_snapshot_unreadable",
            "protection_rearm_evidence_unreadable",
            "contradictory_journal_ownership", "contradictory_venue_ownership",
        }) or any(r.startswith(("position_unprotected:",
                                 "protection_snapshot_unreadable:",
                                 "contradictory_position_side:")) for r in reasons)
        try:
            attempts = int(intent.get("attempts", 0)) if intent else 0
        except (TypeError, ValueError):
            attempts = 3
        if intent and (intent.get("reason") in OWNER_INTENT_REASONS or attempts >= 3):
            serious = True
            reasons.append("entry_recovery_owner_required")
        needs_owner = serious or bool(prior and prior.get("needs_owner"))
        watermark = (prior or {}).get("needs_owner_since_control_event_id") if needs_owner else None
        if needs_owner and watermark is None:
            watermark = self._last_event_id()
        if needs_owner and not serious:
            reasons.append("owner_resume_required")
        safe = checks["entries_safe"] and not needs_owner
        current = self.state_machine.refresh()
        if current != state:
            reasons.append("control_state_changed_during_pass")
            actions["lost_control_race"] = True
            owned = False
            safe = False
        if safe and owned and current == ControlState.RECOVERY:
            # The one activation rule: fresh venue/protection safety AND a
            # fresh Risk release, then the watermark CAS. Never a stored
            # clearance; an owner request or unhalt cannot override Risk.
            risk = self._risk_now()
            checks["risk_release"] = risk.allowed
            actions["risk_release"] = risk.as_dict()
            if not risk.allowed:
                safe = False
                reasons.append(risk.reason)
        if safe and owned and current == ControlState.RECOVERY:
            # The complete proof must be durable before the activation CAS.
            proof = self._result(current, prior, True, outcome="SAFE", stage="PROVED",
                                 safe_to_activate=True, needs_owner=False,
                                 reasons=reasons.copy(), checks=checks.copy(),
                                 actions=actions.copy())
            self._save(proof)
            transition = self.state_machine.set_if_current(ControlState.RECOVERY,
                    ControlState.ACTIVE, "supervisor", "all recovery safety checks proved",
                    expected_control_event_id=int(prior["containment_event_id"]),
                    guard=lambda: self._risk_held(risk))
            if transition:
                current = ControlState.ACTIVE
                owned = False
                actions["activated"] = True
            elif transition.refused:
                # Risk changed between its proof and the CAS: still contained,
                # still ours; the next pass asks Risk afresh.
                current = self.state_machine.refresh()
                checks["risk_release"] = False
                actions["risk_release_at_cas"] = transition.refused
                reasons.append(transition.refused)
                safe = False
            else:
                current = self.state_machine.refresh()
                reasons.append("control_state_changed_during_activation")
                actions["lost_control_race"] = True
                owned = False
                safe = False
        elif safe and current != ControlState.ACTIVE:
            safe = False
            reasons.append("control_state_not_supervisor_owned")
        if (not safe and current == ControlState.ACTIVE and serious
                and not actions.get("lost_control_race")):
            # An acknowledged owner resume can expose a fresh serious fault.
            owned, prior = self._contain_active("new_serious_safety_fault", prior)
            current = self.state_machine.refresh()
            if self._enter_recovery(prior, current, "new serious safety fault"):
                current = ControlState.RECOVERY
        if not safe and current == ControlState.FROZEN and owned:
            if self._enter_recovery(prior, current, ",".join(reasons[:3])):
                current = ControlState.RECOVERY
        outcome = "NEEDS_OWNER" if needs_owner else ("SAFE" if safe else
                  "RECOVERING" if owned else "DEGRADED")
        result = self._result(current, prior, owned, outcome=outcome,
                              stage="COMPLETE" if safe else "VERIFY",
                              safe_to_activate=safe, needs_owner=needs_owner,
                              needs_owner_since_control_event_id=watermark,
                              reasons=list(dict.fromkeys(reasons)), checks=checks,
                              actions=actions)
        self._next_pass = time.monotonic() + self.interval_s
        return self._save(result)

    def cycle(self) -> RecoveryResult | None:
        if self.state_machine.refresh() != ControlState.RECOVERY:
            return None
        if time.monotonic() < self._next_pass:
            return None
        if not self._pass_lock.acquire(blocking=False):
            return None           # an owner-requested pass is running now
        try:
            return self._pass_once(boot=False, advance_entry=False)
        finally:
            self._pass_lock.release()

    # ── owner-requested operations ────────────────────────────────────────
    def _audit_request(self, event: str, ctx: OwnerContext, operation: str,
                       state: ControlState | None, watermark: int) -> int:
        return self.journal.log_control_event(
            event, ctx.actor, from_state=state.value if state else "",
            detail={**ctx.audit(), "operation": operation,
                    "observed_state": state.value if state else None,
                    "watermark_control_event_id": watermark})

    def _finish(self, event: str, ctx: OwnerContext, operation: str,
                request_id: int, watermark: int,
                result: OwnerRecoveryResult, *, conn=None, **extra) -> OwnerRecoveryResult:
        detail = {"request_event_id": request_id, "channel": ctx.channel,
                  "principal": ctx.principal, "request_ref": ctx.request_ref,
                  "operation": operation, "status": result.status,
                  "outcome": result.outcome, "reasons": list(result.reasons),
                  "watermark_control_event_id": watermark, **extra}
        if conn is None:
            self.journal.log_control_event(event, ctx.actor,
                                           to_state=result.control_state or "",
                                           detail=detail)
        else:       # inside a held Risk transaction: commits with its verification
            self.state_machine._event(conn, event, ctx.actor, "",
                                      result.control_state or "", detail)
        return result

    def _refused(self, events: tuple[str, str], ctx: OwnerContext,
                 operation: str, reason: str) -> OwnerRecoveryResult:
        """A request that never reached intake still gets its own audit pair."""
        state = persisted_state(self.journal)
        watermark = latest_intent_event_id(self.journal)   # snapshot, not a CAS basis
        rid = self._audit_request(events[0], ctx, operation, state, watermark)
        return self._finish(events[1], ctx, operation, rid, watermark,
                            OwnerRecoveryResult("REFUSED", state.value if state else None,
                                                reasons=(reason,), request_event_id=rid))

    def _serialized(self, events, ctx: OwnerContext, operation, body):
        """Owner request → request lock (refuse overlap) → pass lock (wait)."""
        if not isinstance(ctx, OwnerContext) or ctx.actor not in OWNER_ACTORS:
            raise ValueError(f"not an owner request: {ctx!r}")
        if not self._request_lock.acquire(blocking=False):
            return self._refused(events, ctx, operation, "request_in_progress")
        try:
            if not self._pass_lock.acquire(timeout=self.request_wait_s):
                return self._refused(events, ctx, operation, "supervisor_pass_busy")
            try:
                return body()
            finally:
                self._pass_lock.release()
        finally:
            self._request_lock.release()

    def request_owner_recovery(self, ctx: OwnerContext, *, allow_unhalt: bool = False,
                               expected_intent_event_id: int | None = None
                               ) -> OwnerRecoveryResult:
        """Owner asks to leave containment. ACTIVE only on a fresh SAFE proof.

        Intake (state + intent watermark read, request journalled, owner hold
        released into RECOVERY) is one control-fence step, and intake, owner
        acknowledgement and the fresh pass all run under the pass lock, so no
        other pass can overwrite the acknowledgement and no set() can land
        unseen between the read and the release. Only the fresh pass's guarded
        compare-and-set may activate; any intent event after the watermark wins.

        HALTED is not an ordinary resume: without ``allow_unhalt=True`` it stays
        HALTED with no reconciliation. ``allow_unhalt`` must only be passed by
        an owner-authorized caller (actor is enforced to be an owner actor).
        Transport-neutral: every owner channel calls exactly this.

        ``expected_intent_event_id``: the intent watermark when the owner's
        request was admitted. If any intent event (a newer FROZEN/HALTED/…)
        landed since, the request is superseded and refused inside this intake
        fence — an admitted-but-not-started recovery never outlives a newer hold.
        """
        operation = "owner_unhalt" if allow_unhalt else "owner_recovery"
        events = ("owner_recovery_requested", "owner_recovery_result")

        def body():
            # An unhalt is permission to TRY release, never to override Risk:
            # ask Risk before leaving HALTED (outside the fence: a venue read).
            # Advisory only — activation re-asks Risk freshly in the pass.
            unhalt_risk = (self._risk_now() if allow_unhalt and
                           persisted_state(self.journal) == ControlState.HALTED else None)
            with self.state_machine.fenced() as f:
                state, watermark = f.state, f.watermark
                rid = self._audit_request(events[0], ctx, operation, state, watermark)
                early = None
                if state is None:
                    early = OwnerRecoveryResult("REFUSED", None,
                                                reasons=("control_state_unreadable",))
                elif (expected_intent_event_id is not None
                      and watermark != expected_intent_event_id):
                    early = OwnerRecoveryResult("REFUSED", state.value,
                                                reasons=("superseded_by_newer_intent",))
                elif state == ControlState.ACTIVE:
                    early = OwnerRecoveryResult("ALREADY_ACTIVE", state.value)
                elif state == ControlState.HALTED and not allow_unhalt:
                    early = OwnerRecoveryResult(
                        "REFUSED", state.value,
                        reasons=("halted_requires_explicit_unhalt",))
                elif state == ControlState.HALTED and not (unhalt_risk and unhalt_risk.allowed):
                    early = OwnerRecoveryResult(
                        "REFUSED", state.value,
                        reasons=(unhalt_risk.reason if unhalt_risk else "risk_release_unproven",))
                elif state in (ControlState.FROZEN, ControlState.HALTED):
                    watermark = f.apply(ControlState.RECOVERY, ctx.actor,
                                        f"owner {operation} request #{rid}: contained recheck")
            done = lambda r, **x: self._finish(events[1], ctx, operation,
                                               rid, watermark, r, **x)
            if early is not None:
                return done(OwnerRecoveryResult(early.status, early.control_state,
                                                reasons=early.reasons,
                                                request_event_id=rid),
                            risk_release=unhalt_risk.as_dict() if unhalt_risk else None)
            # Owner acknowledgement: clears only the approval hold and binds
            # Supervisor ownership to the exact intake/release event. Prior
            # evidence, including any stale SAFE, is replaced, never consulted.
            self._save(RecoveryResult(
                control_state_observed=ControlState.RECOVERY.value,
                outcome="RECOVERING", stage="OWNER_REQUESTED",
                safe_to_activate=False, needs_owner=False,
                containment_owned=True, containment_started_at=_now(),
                containment_from=state.value, containment_event_id=watermark,
                reasons=["owner_recovery_requested"],
                actions={"owner_request_event_id": rid, "channel": ctx.channel,
                         "operation": operation}))
            fresh = self._pass_once(boot=False, advance_entry=False)
            current = persisted_state(self.journal)
            activated = (bool(fresh.actions.get("activated"))
                         and current == ControlState.ACTIVE)
            return done(OwnerRecoveryResult(
                "ACTIVATED" if activated else "CONTAINED",
                current.value if current else None, outcome=fresh.outcome,
                reasons=tuple(fresh.reasons), request_event_id=rid),
                risk_release=fresh.actions.get("risk_release"))

        return self._serialized(events, ctx, operation, body)

    # ── Risk-only guarded release (markets without futures venue recovery) ──
    def _guarded_activate(self, f, expected: tuple[ControlState, ...], watermark: int,
                          risk: RiskRelease, actor: str, detail: str
                          ) -> tuple[str, tuple[str, ...], int | None]:
        """Inside one fence hold: CAS to ACTIVE on a fresh Risk release.

        `watermark` is the intent event the caller bound to before its (fresh,
        outside-the-fence) Risk read; any newer intent event wins.
        """
        state = f.state
        if state is None:
            return "REFUSED", ("control_state_unreadable",), None
        if state == ControlState.ACTIVE:
            return "ALREADY_ACTIVE", (), None
        if state not in expected or f.watermark != watermark:
            return "REFUSED", ("control_state_changed",), None
        if not risk.allowed:
            return "REFUSED", (risk.reason,), None
        with self._risk_held(risk) as (refusal, conn):  # held across the CAS
            if refusal:
                return "REFUSED", (refusal,), None
            return "ACTIVATED", (), f.apply(ControlState.ACTIVE, actor, detail, conn=conn)

    def request_owner_release(self, ctx: OwnerContext, *, allow_unhalt: bool = False,
                              expected_intent_event_id: int | None = None
                              ) -> OwnerRecoveryResult:
        """Owner release where no futures venue recovery applies (spot).

        Same authority as request_owner_recovery minus the venue pass: a fresh
        Risk release, the owner's exact intent watermark, one final CAS. HALTED
        needs ``allow_unhalt``; a newer intent event (HALTED, FROZEN) wins.
        """
        operation = "owner_unhalt" if allow_unhalt else "owner_release"
        events = ("owner_recovery_requested", "owner_recovery_result")

        def body():
            with self.state_machine.fenced() as f:
                state, watermark = f.state, f.watermark
                rid = self._audit_request(events[0], ctx, operation, state, watermark)
            if expected_intent_event_id is not None and watermark != expected_intent_event_id:
                # admitted before a newer intent: superseded (see request_owner_recovery)
                return self._finish(events[1], ctx, operation, rid, watermark,
                                    OwnerRecoveryResult("REFUSED", state.value if state else None,
                                                        reasons=("superseded_by_newer_intent",),
                                                        request_event_id=rid))
            allowed = (ControlState.FROZEN, ControlState.RECOVERY) + (
                (ControlState.HALTED,) if allow_unhalt else ())
            risk = self._risk_now() if state in allowed else None     # a venue read
            with self.state_machine.fenced() as f:
                if (f.state == ControlState.HALTED and not allow_unhalt
                        and f.watermark == watermark):
                    status, reasons = "REFUSED", ("halted_requires_explicit_unhalt",)
                else:
                    status, reasons, _ = self._guarded_activate(
                        f, allowed, watermark, risk or RiskRelease(False, "risk_release_unproven"),
                        ctx.actor, f"owner {operation} request #{rid}: fresh risk release")
                current = f.state
            return self._finish(events[1], ctx, operation, rid, watermark,
                                OwnerRecoveryResult(status, current.value if current else None,
                                                    reasons=reasons, request_event_id=rid),
                                risk_release=risk.as_dict() if risk else None)

        return self._serialized(events, ctx, operation, body)

    def request_macro_release(self, freeze_event_id: int, *,
                              venue_recovery: bool = True) -> OwnerRecoveryResult:
        """MacroGuard asks to lift the freeze it owns. It never sets ACTIVE itself.

        Bound to MacroGuard's own FROZEN event: any newer intent (owner/risk
        FROZEN or HALTED) wins. Risk is read fresh first; a refusal leaves the
        FROZEN untouched. Futures: FROZEN→RECOVERY under that watermark, then a
        fresh Supervisor pass whose single CAS needs venue/protection safety and
        Risk again. Without venue recovery (spot): the Risk-gated CAS alone.
        status: ACTIVATED | CONTAINED | REFUSED | BUSY
        """
        if not self._pass_lock.acquire(blocking=False):
            return OwnerRecoveryResult("BUSY", None, reasons=("supervisor_pass_busy",))
        try:
            with self.state_machine.fenced() as f:
                state, watermark = f.state, f.watermark
            if state != ControlState.FROZEN or watermark != int(freeze_event_id):
                return OwnerRecoveryResult("REFUSED", state.value if state else None,
                                           reasons=("macro_freeze_not_owned",))
            risk = self._risk_now()
            if not venue_recovery:
                with self.state_machine.fenced() as f:
                    status, reasons, _ = self._guarded_activate(
                        f, (ControlState.FROZEN,), watermark, risk, "macro_guard",
                        "macro event cleared: fresh risk release")
                    current = f.state
                if reasons == ("control_state_changed",):
                    reasons = ("macro_freeze_not_owned",)
                return OwnerRecoveryResult(status, current.value if current else None,
                                           reasons=reasons)
            with self.state_machine.fenced() as f:
                if f.state != ControlState.FROZEN or f.watermark != watermark:
                    current = f.state
                    return OwnerRecoveryResult("REFUSED", current.value if current else None,
                                               reasons=("macro_freeze_not_owned",))
                if not risk.allowed:
                    return OwnerRecoveryResult("REFUSED", ControlState.FROZEN.value,
                                               reasons=(risk.reason,))
                event_id = f.apply(ControlState.RECOVERY, "macro_guard",
                                   "macro event cleared: contained recheck")
            self._save(RecoveryResult(
                control_state_observed=ControlState.RECOVERY.value,
                outcome="RECOVERING", stage="MACRO_RELEASE",
                safe_to_activate=False, needs_owner=False,
                containment_owned=True, containment_started_at=_now(),
                containment_from=ControlState.FROZEN.value,
                containment_event_id=event_id,
                reasons=["macro_release_requested"],
                actions={"macro_freeze_event_id": watermark,
                         "risk_release_intake": risk.as_dict()}))
            fresh = self._pass_once(boot=False, advance_entry=False)
            current = persisted_state(self.journal)
            activated = bool(fresh.actions.get("activated")) and current == ControlState.ACTIVE
            return OwnerRecoveryResult("ACTIVATED" if activated else "CONTAINED",
                                       current.value if current else None,
                                       outcome=fresh.outcome, reasons=tuple(fresh.reasons))
        finally:
            self._pass_lock.release()

    @staticmethod
    def _baseline_refusal(risk: RiskRelease) -> str | None:
        """None only for a verified, unlatched Risk baseline (see prepare_rollback);
        the final re-verification is held through READY (RiskManager.hold_baseline)."""
        if not isinstance(risk, RiskRelease):
            return "risk_state_unreadable"
        if risk.reason not in ("risk_release_ok", "risk_halt_active"):
            return risk.reason
        if risk.verify_baseline is None:
            return "risk_baseline_unverifiable"
        return None

    def prepare_rollback(self, ctx: OwnerContext) -> OwnerRecoveryResult:
        """Leave a state an older kernel can parse (owner FROZEN), only on fresh proof.

        The operation contains at intake and keeps that event's exact identity.
        After verifying Risk and fresh protection/reconciliation, it establishes
        a final FROZEN hold and verifies its authority through the READY audit.
        The intake hold must still be authoritative before finalization. Any newer intent
        (FROZEN, HALTED, anything) wins and the result is BLOCKED with the
        newer state untouched. HALTED at intake is refused. Nothing here can
        activate.

        Risk (V2): an older kernel (51101d0) re-seeds a corrupt or missing
        `risk_state` from its first equity read and its owner resume sets
        ACTIVE directly, so READY also needs a verified Risk baseline: a fresh
        authoritative Risk check whose durable baseline is valid and
        unlatched (a latched corruption needs an explicit repair_baseline()
        first), re-verified unchanged under the final fence. Unverifiable,
        corrupt, unreadable or uninitialized Risk is BLOCKED. A drawdown halt
        is not a blocker: the older kernel's own Risk re-halts on that baseline.
        """
        operation = "rollback_prepare"
        events = ("rollback_prepare_requested", "rollback_prepare_result")

        def body():
            with self.state_machine.fenced() as f:
                state, watermark = f.state, f.watermark
                rid = self._audit_request(events[0], ctx, operation, state, watermark)
                hold = None
                if state is not None and state != ControlState.HALTED:
                    # ACTIVE/RECOVERY → FROZEN is a state_change; FROZEN → FROZEN
                    # by an owner actor is a state_hold. Either way: our own id.
                    hold = f.apply(ControlState.FROZEN, ctx.actor,
                                   f"rollback preparation #{rid}")
            done = lambda r, **x: self._finish(events[1], ctx, operation,
                                               rid, watermark, r, own_hold_event_id=hold, **x)
            if hold is None:
                return done(OwnerRecoveryResult(
                    "REFUSED", state.value if state else None,
                    reasons=("halted_manual_only" if state else "control_state_unreadable",),
                    request_event_id=rid))
            # 1. a repaired, verified Risk baseline (fresh, outside the fence)
            risk = self._risk_now()
            risk_reason = self._baseline_refusal(risk)
            fresh, proved = None, False
            if risk_reason is None:
                # 2. fresh protection / reconciliation (owner FROZEN: cannot activate)
                fresh = self._pass_once(boot=False, advance_entry=False)
                proved = (fresh.checks.get("entries_safe") is True
                          and not fresh.actions.get("lost_control_race"))
            outcome = fresh.outcome if fresh else None
            fresh_reasons = tuple(fresh.reasons) if fresh else ()
            # 3. Only after fresh checks, establish the final exact FROZEN hold.
            # Keep the baseline and control authority held through its verification
            # and the READY audit; never replace a newer owner's intent.
            with self.state_machine.fenced() as f:
                own = f.state == ControlState.FROZEN and f.watermark == hold
                if proved and own and risk_reason is None:
                    with self._held(risk.verify_baseline, risk) as (refusal, conn):
                        if refusal is None:
                            final_hold = f.apply(ControlState.FROZEN, ctx.actor,
                                                 f"rollback preparation #{rid}", conn=conn)
                            if (final_hold is not None and f.state == ControlState.FROZEN
                                    and f.watermark == final_hold):
                                hold = final_hold
                                return done(OwnerRecoveryResult(
                                    "READY", ControlState.FROZEN.value, outcome=outcome,
                                    reasons=fresh_reasons, request_event_id=rid,
                                    hold_event_id=hold), risk_baseline=risk.as_dict(),
                                    conn=conn)
                            risk_reason = "rollback_hold_unproven"
                        else:
                            risk_reason = refusal
                if own and state != ControlState.FROZEN:
                    # Our own hold, never a newer one: hand an ACTIVE/RECOVERY
                    # start back to RECOVERY so this kernel keeps re-verifying.
                    # A pre-existing owner FROZEN stays FROZEN.
                    f.apply(ControlState.RECOVERY, ctx.actor,
                            f"rollback preparation #{rid} blocked")
                current = f.state
            reasons = (fresh_reasons + (() if own else ("control_state_changed",))
                       + (() if risk_reason is None else (risk_reason,)))
            return done(OwnerRecoveryResult(
                "BLOCKED", current.value if current else None, outcome=outcome,
                reasons=tuple(dict.fromkeys(reasons)), request_event_id=rid,
                hold_event_id=hold), risk_baseline=risk.as_dict())

        return self._serialized(events, ctx, operation, body)
