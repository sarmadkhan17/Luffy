"""Observed safety evidence and Supervisor-owned containment for futures recovery."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..core.types import ControlState
from .reconcile import reconcile_futures
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


class Supervisor:
    """One bounded recovery pass at boot, then at most one per cadence."""

    def __init__(self, journal, state_machine, executor, exchange,
                 *, interval_s: float = 60.0):
        self.journal = journal
        self.state_machine = state_machine
        self.executor = executor
        self.exchange = exchange
        self.interval_s = interval_s
        self._next_pass = 0.0

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
        rows = self.journal.query(
            "SELECT COALESCE(MAX(id), 0) AS id FROM control_events "
            "WHERE event IN ('state_change','state_hold')")
        return int(rows[0]["id"]) == int(event_id)

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
            # The complete proof must be durable before the activation CAS.
            proof = self._result(current, prior, True, outcome="SAFE", stage="PROVED",
                                 safe_to_activate=True, needs_owner=False,
                                 reasons=reasons.copy(), checks=checks.copy(),
                                 actions=actions.copy())
            self._save(proof)
            if self.state_machine.set_if_current(ControlState.RECOVERY,
                    ControlState.ACTIVE, "supervisor", "all recovery safety checks proved",
                    expected_control_event_id=int(prior["containment_event_id"])):
                current = ControlState.ACTIVE
                owned = False
                actions["activated"] = True
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
        return self.pass_once(advance_entry=False)
