"""Synthetic storage-boundary checks; no research run or provider access."""
import builtins
import hashlib

import pytest

from trader.core.journal import Journal
from trader.cognition import research_shadow_store as st


def primitive_row():
    # Journal accepts projected primitives; canonical contract validation belongs
    # to the family writer. This isolates persistence and capture dispatch.
    row = {key: "synthetic" for key in Journal._UNREADABLE_BANK_COLUMNS}
    row.update(schema="strategy-health-unreadable-bank-object.v1",
               canonical_json="{}",
               canonical_sha256=hashlib.sha256(b"{}").hexdigest())
    return row


def test_shadow_unreadable_insert_never_imports_learning(tmp_path, monkeypatch):
    source = Journal(tmp_path / "source.db")
    before = source.query("SELECT * FROM research_unreadable_bank_objects")
    shadow_path = tmp_path / "shadow.db"
    st.init_shadow(shadow_path)
    shadow = st.open_shadow(shadow_path, source.db_path, readonly=False)
    original_import = builtins.__import__

    def refuse_learning(name, globals=None, locals=None, fromlist=(), level=0):
        if "learning" in name:
            raise ImportError("shadow must not import learning capture")
        return original_import(name, globals, locals, fromlist, level)

    try:
        monkeypatch.setattr(builtins, "__import__", refuse_learning)
        row = primitive_row()
        assert shadow.record_research_unreadable_bank_object(
            row, recorded_at_ms=1) == "inserted"
        assert shadow.record_research_unreadable_bank_object(
            row, recorded_at_ms=2) == "duplicate"
        assert shadow.record_research_unreadable_bank_object(
            dict(row, builder_id="different"), recorded_at_ms=3) == "conflict"
        assert shadow.research_unreadable_bank_objects() == [
            dict(row, recorded_at_ms=1)]
        assert len(shadow.query("SELECT * FROM research_registrations")) == 1
        assert not shadow.query("SELECT name FROM sqlite_master "
                                "WHERE name LIKE 'learning_%'")
        assert source.query("SELECT * FROM research_unreadable_bank_objects") == before
    finally:
        shadow.close()


def test_regular_unreadable_insert_keeps_original_capture(tmp_path, monkeypatch):
    from trader.learning import capture as lc, capture_runtime as lr

    journal = Journal(tmp_path / "regular.db")
    calls = []
    monkeypatch.setattr(lc, "safely", lambda *args, **kwargs: calls.append((args, kwargs)))
    row = primitive_row()
    assert journal.record_research_unreadable_bank_object(row, recorded_at_ms=1) == "inserted"
    assert journal.record_research_unreadable_bank_object(row, recorded_at_ms=2) == "duplicate"
    assert journal.record_research_unreadable_bank_object(
        dict(row, builder_id="different"), recorded_at_ms=3) == "conflict"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[1:] == ("research-bank:synthetic", lr.research_bank, row, 1, journal)
    assert kwargs == {"unreadable": True}


def test_unreadable_capture_error_rolls_back_object_and_receipt(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "rollback.db")

    def fail(*args):
        raise RuntimeError("synthetic capture error")

    monkeypatch.setattr(journal, "_capture_research_unreadable_bank", fail)
    with pytest.raises(RuntimeError, match="synthetic capture error"):
        journal.record_research_unreadable_bank_object(primitive_row(), recorded_at_ms=1)
    assert journal.research_unreadable_bank_objects() == []
    assert journal.query("SELECT * FROM research_registrations") == []
