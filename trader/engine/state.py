"""Control state machine — ACTIVE / FROZEN / HALTED / RECOVERY (REQUIREMENTS §3,
SDD v3.2 §27).

Only ACTIVE permits new entries. RECOVERY is entered only after containment
(FROZEN or HALTED), never directly from ACTIVE; it keeps exits, protection and
reconciliation running while venue truth is rebuilt.

Persisted in journal (state_kv) so restarts preserve operator intent.
Every transition is journaled with its actor. Panic = flatten + FROZEN.
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass

from ..core.types import ControlState, now_utc
from ..core.journal import Journal
from .control_fence import control_fence, latest_intent_event_id, persisted_state

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
    refused: str | None = None            # the guard's refusal, when it refused

    def __bool__(self) -> bool:
        return self.changed


class FencedControl:
    """State, intent watermark and transitions read/applied under one fence hold.

    Lets an operation observe the persisted state and the latest intent event,
    journal its own request, and apply its own transition atomically: no other
    process's set() can land between the read and the write.
    """

    def __init__(self, machine: "ControlStateMachine"):
        self._machine = machine

    @property
    def state(self) -> ControlState | None:
        return persisted_state(self._machine.journal)

    @property
    def watermark(self) -> int:
        return latest_intent_event_id(self._machine.journal)

    def apply(self, new: ControlState, actor: str, detail: str = "", *,
              conn=None) -> int | None:
        """Transition (or same-state owner hold); returns its exact event id.
        `conn`: write inside that open transaction (e.g. a Risk hold's)."""
        return self._machine._set_fenced(new, actor, detail, conn=conn)


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
        from ..observability.safety import entry_refusal
        if entry_refusal(self.journal, probe=True):
            return False
        self.refresh()
        return self.state == ControlState.ACTIVE

    def manages_exits(self) -> bool:
        """FROZEN and RECOVERY still manage exits; HALTED does not."""
        try:
            self.refresh()
        except (OSError, __import__('sqlite3').DatabaseError):
            # Unavailable persistence cannot authorize entries. Previously
            # known exit management remains permitted; HALTED stays respected.
            pass
        return self.state in (ControlState.ACTIVE, ControlState.FROZEN,
                              ControlState.RECOVERY)

    def set(self, new: ControlState, actor: str, detail: str = "") -> int | None:
        # The shared entry/control fence: a transition waits for an entry
        # submission already past the Executor's final boundary, and once it
        # persists a blocking state no later entry can be submitted.
        with control_fence(self.journal):
            return self._set_fenced(new, actor, detail)

    @contextmanager
    def fenced(self):
        """Hold the control fence for one atomic read/journal/transition step.

        Not reentrant: never call set()/set_if_current() inside the block.
        """
        with control_fence(self.journal):
            yield FencedControl(self)

    def set_if_current(self, expected: ControlState, new: ControlState,
                       actor: str, detail: str = "", *,
                       expected_control_event_id: int | None = None,
                       guard=None) -> TransitionResult:
        """Compare state and optional control-intent watermark under one fence.

        `guard`, if given, is a zero-argument callable returning a context
        manager that yields None / a refusal reason, or (refusal, conn). It is
        entered inside the fence after the state/watermark compare and held
        across the transition (e.g. the Risk proof's hold_release); with a
        `conn`, the transition is written in that transaction.
        """
        with control_fence(self.journal):
            current = persisted_state(self.journal)
            if current is None or current != expected:
                return TransitionResult(False, current)
            self.state = current
            if (expected_control_event_id is not None
                    and latest_intent_event_id(self.journal) != expected_control_event_id):
                return TransitionResult(False, current)
            if new == expected:
                raise ValueError("compare-and-set requires a transition")
            if guard is None:
                event_id = self._set_fenced(new, actor, detail)
                return TransitionResult(True, new, event_id)
            with guard() as held:
                refusal, conn = held if isinstance(held, tuple) else (held, None)
                if refusal:
                    return TransitionResult(False, current, refused=str(refusal))
                event_id = self._set_fenced(new, actor, detail, conn=conn)
            return TransitionResult(True, new, event_id)

    def _kv_set(self, conn, key: str, value: str) -> None:
        if conn is None:
            self.journal.kv_set(key, value)
        else:
            conn.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                         (key, value))

    def _event(self, conn, event: str, actor: str, from_state: str, to_state: str,
               detail) -> int:
        if conn is None:
            return self.journal.log_control_event(event, actor, from_state=from_state,
                                                  to_state=to_state, detail=detail)
        cursor = conn.execute(
            "INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
            "VALUES (?,?,?,?,?,?)",
            (now_utc().isoformat(), event, from_state, to_state, actor,
             detail if isinstance(detail, str) else json.dumps(detail)))
        return int(cursor.lastrowid)

    def _set_fenced(self, new: ControlState, actor: str, detail: str, *,
                    conn=None) -> int | None:
        self.refresh()
        # A same-state operator freeze still records an explicit hold, so
        # MacroGuard cannot later auto-resume a freeze it did not own.
        if new == ControlState.FROZEN and actor != "macro_guard":
            self._kv_set(conn, "macro_guard_operator_hold", "1")
        elif new == ControlState.ACTIVE and actor != "macro_guard":
            self._kv_set(conn, "macro_guard_operator_hold", "0")
        if new == self.state:
            # A same-state owner FROZEN/HALTED is still explicit owner intent.
            # Record it so any in-flight operation bound to an older intent
            # watermark (Supervisor containment, owner recovery, rollback
            # preparation) loses. Non-owner reassertions (a per-cycle risk
            # re-halt) stay silent: they are refreshes, not new intent.
            if (new in (ControlState.FROZEN, ControlState.HALTED)
                    and actor in OWNER_ACTORS):
                return self._event(conn, "state_hold", actor, new.value, new.value, detail)
            return None
        if new not in VALID_TRANSITIONS[self.state]:
            raise ValueError(f"illegal transition {self.state}→{new}")
        old = self.state
        self._kv_set(conn, "control_state", new.value)
        event_id = self._event(conn, "state_change", actor, old.value, new.value, detail)
        self.state = new
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
