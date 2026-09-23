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

from ..core.types import ControlState
from ..core.journal import Journal
from .control_fence import control_fence

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

    def _set_fenced(self, new: ControlState, actor: str, detail: str) -> None:
        self.refresh()
        # A same-state operator freeze still records an explicit hold, so
        # MacroGuard cannot later auto-resume a freeze it did not own.
        if new == ControlState.FROZEN and actor != "macro_guard":
            self.journal.kv_set("macro_guard_operator_hold", "1")
        elif new == ControlState.ACTIVE and actor != "macro_guard":
            self.journal.kv_set("macro_guard_operator_hold", "0")
        if new == self.state:
            return
        if new not in VALID_TRANSITIONS[self.state]:
            raise ValueError(f"illegal transition {self.state}→{new}")
        old, self.state = self.state, new
        self.journal.kv_set("control_state", new.value)
        self.journal.log_control_event("state_change", actor,
                                       from_state=old.value, to_state=new.value,
                                       detail=detail)
        log.warning(f"CONTROL STATE {old.value} → {new.value} (by {actor}) {detail}")

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
