"""Control state machine — ACTIVE / FROZEN / HALTED (REQUIREMENTS §3).

Persisted in journal (state_kv) so restarts preserve operator intent.
Every transition is journaled with its actor. Panic = flatten + FROZEN.
"""
from __future__ import annotations

import logging

from ..core.types import ControlState
from ..core.journal import Journal

log = logging.getLogger(__name__)

VALID_TRANSITIONS = {
    ControlState.ACTIVE: {ControlState.FROZEN, ControlState.HALTED},
    ControlState.FROZEN: {ControlState.ACTIVE, ControlState.HALTED},
    ControlState.HALTED: {ControlState.FROZEN, ControlState.ACTIVE},
}


class ControlStateMachine:
    def __init__(self, journal: Journal):
        self.journal = journal
        raw = journal.kv_get("control_state", ControlState.ACTIVE.value)
        self.state = ControlState(raw)

    def can_enter(self) -> bool:
        return self.state == ControlState.ACTIVE

    def manages_exits(self) -> bool:
        """FROZEN still manages exits to natural close; HALTED does not."""
        return self.state in (ControlState.ACTIVE, ControlState.FROZEN)

    def set(self, new: ControlState, actor: str, detail: str = "") -> None:
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
        self.journal.log_control_event("panic", actor,
                                       from_state=self.state.value,
                                       to_state="FROZEN",
                                       detail="panic requested")
        # kernel performs flatten then calls set(FROZEN); state change happens
        # after successful flattening so a crash mid-flatten leaves intent recorded.
        return 1
