"""Cross-process entry/control fence (SDD v3.2 §27).

One exclusive ``fcntl.flock`` on a lock file beside the journal database
serializes two things across the kernel, dashboard and any other process:

- ``ControlStateMachine.set()`` — refresh, validate, persist, journal;
- the Executor's final new-entry boundary — re-read persisted state, persist
  the recovery intent, submit the entry, record the immediate result.

So once FROZEN / RECOVERY / HALTED is persisted, no later entry can be sent;
an entry already past the fence finishes its submission first and the
transition waits. The fence is a file lock, never a SQLite transaction: each
journal write inside it commits on its own, so no database write lock is held
across the venue call. Fill polling, protective stops, exits and
reconciliation never take it and keep running in any blocking state.

Each acquisition opens its own file description, so threads in one process
contend exactly as separate processes do. Not reentrant: never call
``ControlStateMachine.set()`` while holding the fence.
"""
from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from ..core import reason_codes as rc
from ..core.types import ControlState

#: duck-typed journals without a database file (unit-test doubles) only
#: share one process, so an in-process lock is the whole fence for them.
_LOCAL = threading.Lock()


def fence_path(journal) -> Path | None:
    db = getattr(journal, "db_path", None)
    if db is None:
        return None
    db = Path(db)
    return db.with_name(db.name + ".control.lock")


@contextmanager
def control_fence(journal):
    path = fence_path(journal)
    if path is None:
        with _LOCAL:
            yield
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def persisted_state(journal) -> ControlState | None:
    """The control state as persisted now; None if unreadable (fail closed)."""
    try:
        return ControlState(journal.kv_get("control_state",
                                           ControlState.ACTIVE.value))
    except Exception:
        return None


def entry_block_reason(state: ControlState | None) -> str | None:
    """Stable skip reason, matching RiskManager wording; None when ACTIVE."""
    return entry_block(state)[0]


def entry_block(state: ControlState | None) -> tuple:
    """(skip reason, reason code) of the blocking branch; (None, None) when ACTIVE."""
    if state == ControlState.ACTIVE:
        return None, None
    if state == ControlState.FROZEN:
        return "state=FROZEN: entries blocked", rc.FENCE_STATE_FROZEN
    if state == ControlState.HALTED:
        return "state=HALTED", rc.FENCE_STATE_HALTED
    if state is None:
        return "state=UNREADABLE: entries blocked", rc.FENCE_STATE_UNREADABLE
    return f"state={state.value}: entries blocked", rc.FENCE_STATE_NOT_ACTIVE
