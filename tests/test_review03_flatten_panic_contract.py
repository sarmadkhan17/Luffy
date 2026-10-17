"""Cross-lane contract for C-1: an incomplete flatten must not consume panic.

Execution half (flatten_all keeps stops, reports remaining) is covered in
tests/test_reconcile.py. These checks retain the pending owner request until
both journaled and unjournaled exposure are resolved.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.engine.reconcile import FlattenResult
from tests.test_owner_interface import _active, req, svc
from tests.test_owner_recovery import HookVenue


def test_flatten_result_is_a_drop_in_int():
    r = FlattenResult(2)
    assert r == 2 and r.complete and f"{r}" == "2"
    assert not FlattenResult(0, remaining=("A/USDT",)).complete
    assert not FlattenResult(1, unjournaled=("B/USDT",)).complete


def test_incomplete_flatten_keeps_panic_pending(tmp_path, monkeypatch):
    _incomplete_then_complete(tmp_path, monkeypatch, 'remaining')


def test_unjournaled_exposure_keeps_panic_pending(tmp_path, monkeypatch):
    _incomplete_then_complete(tmp_path, monkeypatch, 'unjournaled')


def _incomplete_then_complete(tmp_path, monkeypatch, exposure):
    journal, venue = Journal(tmp_path / "j.db"), HookVenue()
    k = _active(journal, venue, monkeypatch)
    svc(k).execute(req("panic", "test-panic-first01"))
    monkeypatch.setattr(
        "trader.kernel.flatten_all",
        lambda exchange, journal, notifier: FlattenResult(0, **{exposure: ("A/USDT",)}))
    k._drain_panic()
    raw = journal.kv_get("panic_requested")
    assert raw not in (None, "", "0")
    assert json.loads(raw)["request_id"] == "test-panic-first01"
    assert journal.kv_get('control_state') == 'FROZEN'
    monkeypatch.setattr('trader.kernel.flatten_all', lambda *args: FlattenResult(1))
    assert k._drain_panic() == 1
    assert journal.kv_get('panic_requested') == '0'
    assert journal.kv_get('control_state') == 'FROZEN'
