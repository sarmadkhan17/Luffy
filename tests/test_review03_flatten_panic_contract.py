"""Cross-lane contract for C-1: an incomplete flatten must not consume panic.

Execution half (flatten_all keeps stops, reports remaining) is covered in
tests/test_reconcile.py. The Kernel half — `_drain_panic` honouring
`FlattenResult.complete` — lives in Sol1-owned trader/kernel.py and is pinned
here as a strict expected failure until Sol1 implements it.
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


@pytest.mark.xfail(strict=True, reason=(
    "BLOCKED_WITH_DEPENDENCY (Sol1, trader/kernel.py _drain_panic): the panic "
    "request is compare-and-cleared after one flatten_all call regardless of "
    "FlattenResult.complete. It should stay pending while exposure remains."))
def test_incomplete_flatten_keeps_panic_pending(tmp_path, monkeypatch):
    journal, venue = Journal(tmp_path / "j.db"), HookVenue()
    k = _active(journal, venue, monkeypatch)
    svc(k).execute(req("panic", "test-panic-first01"))
    monkeypatch.setattr(
        "trader.kernel.flatten_all",
        lambda exchange, journal, notifier: FlattenResult(0, remaining=("A/USDT",)))
    k._drain_panic()
    raw = journal.kv_get("panic_requested")
    assert raw not in (None, "", "0")
    assert json.loads(raw)["request_id"] == "test-panic-first01"
