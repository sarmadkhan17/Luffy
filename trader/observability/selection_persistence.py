"""Durable record of each registry Attention `Selection` and its cursor.

Translates a `Selection` into the Journal's primitive attention_selections
row and writes it together with the rotating cursor in ONE SQLite
transaction (`Journal.record_attention_selection`). The ordering is locked:
select, commit the selection and cursor_after, and only then may a later
integration start a supplemental fetch, whose outcome is recorded separately
and can never move or roll back the cursor.

The Journal knows nothing of `Selection`; this module owns validation, the
canonical-JSON integrity check and the read-side reconstruction. No network,
no Kernel, no Attention Store/Collector.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from trader.core.instrument_registry import is_canonical_instrument_id
from trader.observability.registry_selector import (
    NOTHING_SELECTABLE, SELECTED, SNAPSHOT_STALE, Exclusion, Selection,
)

#: the one rotating supplemental slot's cursor row
CURSOR_NAME = "registry-attention-selector"
OUTCOMES = frozenset({SELECTED, SNAPSHOT_STALE, NOTHING_SELECTABLE})

INSERTED = "inserted"
DUPLICATE = "duplicate"

# refusal reasons
INVALID_SELECTION = "invalid_selection"
SELECTION_ID_MISMATCH = "selection_id_mismatch"
CONFLICTING_DUPLICATE = "conflicting_duplicate"
CURSOR_CONFLICT = "cursor_conflict"
CORRUPT_CURSOR = "corrupt_cursor"
CORRUPT_SELECTION = "corrupt_selection"
UNKNOWN_SELECTION = "unknown_selection"
INVALID_FETCH_OUTCOME = "invalid_fetch_outcome"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[a-z0-9_]{1,64}")


class SelectionPersistenceError(ValueError):
    """Refused or corrupt; nothing was written by the refusing call."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def _is_ms(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _from_canonical(text: str) -> Selection:
    """Rebuild a Selection from its canonical JSON; raises on any mismatch."""
    d = json.loads(text)
    d["candidate_ids"] = tuple(d["candidate_ids"])
    d["exclusions"] = tuple(Exclusion(i, r) for i, r in d["exclusions"])
    d["unmatched_scan_symbols"] = tuple(d["unmatched_scan_symbols"])
    sel = Selection(**d)
    if sel.canonical_json() != text:
        raise ValueError("canonical JSON does not round-trip")
    return sel


def _check_selection(sel: Selection) -> None:
    def bad(detail):
        raise SelectionPersistenceError(INVALID_SELECTION, detail)

    if not isinstance(sel, Selection):
        bad("not a Selection")
    if not isinstance(sel.rule_version, str) or not sel.rule_version:
        bad("rule_version")
    if sel.outcome not in OUTCOMES:
        bad(f"outcome {sel.outcome!r} is not persisted here")
    if not isinstance(sel.snapshot_id, str) or not sel.snapshot_id:
        bad("snapshot_id")
    for name in ("cycle_as_of_ms", "snapshot_as_of_ms", "registry_age_ms",
                 "max_snapshot_age_ms"):
        if not _is_ms(getattr(sel, name)):
            bad(name)
    for name in ("cursor_before", "cursor_after"):
        v = getattr(sel, name)
        if v is not None and not is_canonical_instrument_id(v):
            bad(f"{name} is not a canonical instrument ID")
    if sel.outcome == SELECTED:
        if not is_canonical_instrument_id(sel.selected_id):
            bad("selected_id")
        if not isinstance(sel.selected_symbol, str) or not sel.selected_symbol:
            bad("selected_symbol")
        if sel.cursor_after is None:
            bad("a selected outcome must advance the cursor")
    else:
        if sel.selected_id is not None or sel.selected_symbol is not None:
            bad("only a selected outcome names an instrument")
        if sel.cursor_after != sel.cursor_before:
            bad("an unselected outcome must keep the cursor unchanged")
    try:
        text = sel.canonical_json()
        rebuilt = _from_canonical(text)
    except (TypeError, ValueError, KeyError) as exc:
        bad(f"canonical JSON: {exc}")
    if rebuilt != sel:
        bad("canonical JSON does not reconstruct the Selection")


def _row(sel: Selection, selection_id: str, text: str) -> dict:
    return {"selection_id": selection_id, "rule_version": sel.rule_version,
            "cycle_as_of_ms": sel.cycle_as_of_ms, "outcome": sel.outcome,
            "snapshot_id": sel.snapshot_id, "snapshot_as_of_ms": sel.snapshot_as_of_ms,
            "registry_age_ms": sel.registry_age_ms,
            "max_snapshot_age_ms": sel.max_snapshot_age_ms,
            "selected_id": sel.selected_id, "selected_symbol": sel.selected_symbol,
            "cursor_before": sel.cursor_before, "cursor_after": sel.cursor_after,
            "canonical_json": text}


def record_selection(journal, selection: Selection, *, selection_id: str,
                     recorded_at_ms: int) -> str:
    """Commit the selection row and its cursor_after atomically.

    Returns INSERTED, or DUPLICATE when this exact selection is already
    recorded (then nothing changes, including the cursor). A different
    payload under the same ID is refused with the cursor untouched.

    Compare-and-swap: a new selection commits only if the durable cursor is
    exactly its cursor_before (None = no cursor row); otherwise CURSOR_CONFLICT
    and nothing is written. This holds for unselected outcomes too, so a stale
    selection built from an old None cursor cannot delete an advanced one."""
    _check_selection(selection)
    if not _is_ms(recorded_at_ms):
        raise SelectionPersistenceError(INVALID_SELECTION, "recorded_at_ms")
    text = selection.canonical_json()
    if not isinstance(selection_id, str) or selection_id != _digest(text):
        raise SelectionPersistenceError(SELECTION_ID_MISMATCH,
                                        "selection_id != sha256(canonical_json)")
    status = journal.record_attention_selection(
        _row(selection, selection_id, text), cursor_name=CURSOR_NAME,
        recorded_at_ms=recorded_at_ms)
    if status == "conflict":
        raise SelectionPersistenceError(CONFLICTING_DUPLICATE, selection_id)
    if status == "cursor_conflict":
        raise SelectionPersistenceError(
            CURSOR_CONFLICT, f"durable cursor is not cursor_before {selection.cursor_before!r}")
    return INSERTED if status == "inserted" else DUPLICATE


def load_cursor(journal) -> str | None:
    """The durable cursor for the next `select(cursor_before=...)`.

    None only when no cursor row exists; a stored value that is not a
    canonical InstrumentId.value fails closed."""
    raw = journal.attention_cursor(CURSOR_NAME)
    if raw is None:
        return None
    if not is_canonical_instrument_id(raw):
        raise SelectionPersistenceError(CORRUPT_CURSOR, repr(raw))
    return raw


def load_selection(journal, selection_id: str) -> Selection | None:
    """The recorded Selection, or None when absent. The stored canonical JSON
    must hash to its ID, reconstruct a valid Selection and agree with every
    projected column; anything else is CORRUPT_SELECTION."""
    row = journal.attention_selection(selection_id)
    if row is None:
        return None
    text = row["canonical_json"]
    if not isinstance(text, str) or _digest(text) != selection_id:
        raise SelectionPersistenceError(CORRUPT_SELECTION, "payload does not hash to its ID")
    try:
        sel = _from_canonical(text)
        _check_selection(sel)
    except (TypeError, ValueError, KeyError) as exc:
        raise SelectionPersistenceError(CORRUPT_SELECTION, str(exc)) from None
    expected = _row(sel, selection_id, text)
    if any(row[k] != v for k, v in expected.items()):
        raise SelectionPersistenceError(CORRUPT_SELECTION, "columns disagree with payload")
    return sel


def record_fetch_outcome(journal, selection_id: str, *, status: str, reason: str,
                         started_ms: int | None, ended_ms: int | None,
                         recorded_at_ms: int) -> int:
    """Record what a later supplemental fetch did, in its own transaction.

    Never reads or writes the cursor, so failing here cannot undo an already
    committed selection/cursor. The selection must already be recorded."""
    def bad(detail):
        raise SelectionPersistenceError(INVALID_FETCH_OUTCOME, detail)

    if not isinstance(selection_id, str) or not _SHA256.fullmatch(selection_id):
        bad("selection_id")
    for name, v in (("status", status), ("reason", reason)):
        if not isinstance(v, str) or not _TOKEN.fullmatch(v):
            bad(name)
    for name, v in (("started_ms", started_ms), ("ended_ms", ended_ms)):
        if v is not None and not _is_ms(v):
            bad(name)
    if started_ms is not None and ended_ms is not None and ended_ms < started_ms:
        bad("ended_ms before started_ms")
    if not _is_ms(recorded_at_ms):
        bad("recorded_at_ms")
    try:
        return journal.record_attention_fetch_outcome(
            selection_id, status, reason, started_ms, ended_ms, recorded_at_ms)
    except sqlite3.IntegrityError:
        raise SelectionPersistenceError(UNKNOWN_SELECTION, selection_id) from None
