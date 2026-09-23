"""Control state machine — ACTIVE / FROZEN / HALTED / RECOVERY (REQUIREMENTS §3,
SDD v3.2 §27).

Only ACTIVE permits new entries. RECOVERY is entered only after containment
(FROZEN or HALTED), never directly from ACTIVE; it keeps exits, protection and
reconciliation running while venue truth is rebuilt.

Persisted in journal (state_kv) so restarts preserve operator intent.
Every transition is journaled with its actor. Panic = flatten + FROZEN.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.types import ControlState
from ..core.journal import Journal
from .control_fence import control_fence, persisted_state

log = logging.getLogger(__name__)

VALID_TRANSITIONS = {
    ControlState.ACTIVE: {ControlState.FROZEN, ControlState.HALTED},
    ControlState.FROZEN: {ControlState.ACTIVE, ControlState.HALTED,
                          ControlState.RECOVERY},
    ControlState.HALTED: {ControlState.FROZEN, ControlState.ACTIVE,
                          ControlState.RECOVERY},
    ControlState.RECOVERY: {ControlState.FROZEN, ControlState.ACTIVE,
                            ControlState.HALTED},
}
OWNER_ACTORS = ("operator", "dashboard", "chat")


@dataclass(frozen=True)
class TransitionResult:
    changed: bool
    state: ControlState | None
    event_id: int | None = None

    def __bool__(self) -> bool:
        return self.changed


class ControlStateMachine:
    def __init__(self, journal: Journal):
        self.journal = journal
        raw = journal.kv_get("control_state", ControlState.ACTIVE.value)
        self.state = ControlState(raw)

    def refresh(self) -> ControlState:
        """Reload operator state written by another process."""
        raw = self.state.value
        try:
            raw = self.journal.kv_get("control_state", raw)
            self.state = ControlState(raw)
        except (TypeError, ValueError) as e:
            log.warning("invalid persisted control state %r: %s", raw, e)
        return self.state

    def can_enter(self) -> bool:
        self.refresh()
        return self.state == ControlState.ACTIVE

    def manages_exits(self) -> bool:
        """FROZEN and RECOVERY still manage exits; HALTED does not."""
        self.refresh()
        return self.state in (ControlState.ACTIVE, ControlState.FROZEN,
                              ControlState.RECOVERY)

    def set(self, new: ControlState, actor: str, detail: str = "") -> None:
        # The shared entry/control fence: a transition waits for an entry
        # submission already past the Executor's final boundary, and once it
        # persists a blocking state no later entry can be submitted.
        with control_fence(self.journal):
            self._set_fenced(new, actor, detail)

    def set_if_current(self, expected: ControlState, new: ControlState,
                       actor: str, detail: str = "", *,
                       expected_control_event_id: int | None = None) -> TransitionResult:
        """Compare state and optional control-intent watermark under one fence."""
        with control_fence(self.journal):
            current = persisted_state(self.journal)
            if current is None or current != expected:
                return TransitionResult(False, current)
            self.state = current
            if expected_control_event_id is not None:
                rows = self.journal.query(
                    "SELECT COALESCE(MAX(id), 0) AS id FROM control_events "
                    "WHERE event IN ('state_change','state_hold')")
                if int(rows[0]["id"]) != expected_control_event_id:
                    return TransitionResult(False, current)
            if new == expected:
                raise ValueError("compare-and-set requires a transition")
            event_id = self._set_fenced(new, actor, detail)
            return TransitionResult(True, new, event_id)

    def _set_fenced(self, new: ControlState, actor: str, detail: str) -> int | None:
        self.refresh()
        # A same-state operator freeze still records an explicit hold, so
        # MacroGuard cannot later auto-resume a freeze it did not own.
        if new == ControlState.FROZEN and actor != "macro_guard":
            self.journal.kv_set("macro_guard_operator_hold", "1")
        elif new == ControlState.ACTIVE and actor != "macro_guard":
            self.journal.kv_set("macro_guard_operator_hold", "0")
        if new == self.state:
            # A same-state owner freeze is still an explicit hold. Record it
            # so an in-flight Supervisor containment loses ownership.
            if new == ControlState.FROZEN and actor in OWNER_ACTORS:
                return self.journal.log_control_event("state_hold", actor,
                                                      from_state=new.value,
                                                      to_state=new.value, detail=detail)
            return None
        if new not in VALID_TRANSITIONS[self.state]:
            raise ValueError(f"illegal transition {self.state}→{new}")
        old, self.state = self.state, new
        self.journal.kv_set("control_state", new.value)
        event_id = self.journal.log_control_event("state_change", actor,
                                                  from_state=old.value, to_state=new.value,
                                                  detail=detail)
        log.warning(f"CONTROL STATE {old.value} → {new.value} (by {actor}) {detail}")
        return event_id

    def panic(self, actor: str = "operator") -> int:
        """Journal panic; actual flattening is executed by the kernel loop."""
        self.refresh()
        self.journal.log_control_event("panic", actor,
                                       from_state=self.state.value,
                                       to_state="FROZEN",
                                       detail="panic requested")
        # kernel performs flatten then calls set(FROZEN); state change happens
        # after successful flattening so a crash mid-flatten leaves intent recorded.
        return 1
