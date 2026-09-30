"""Final blocker: a successful venue read's completion time is its source time,
kept exactly as reported. An impossible completion (after the record, before
the attempt) is an invalid clock; missing/malformed completion leaves the
source time unknown. Never substituted with the publication time. The value
and its authority for Risk are unchanged. Producer (Kernel) → reader tests.
"""
import json
import math
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.dashboard import current_truth
from tests.test_current_truth_contract import _kernel, _obs

T = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)      # the kernel's "now"
ATTEMPT = T - timedelta(seconds=1)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return T


@pytest.fixture
def frozen(monkeypatch):
    import datetime as real_dt
    import trader.kernel as kmod
    monkeypatch.setattr(kmod, "dt", NS(datetime=_Clock, timezone=real_dt.timezone,
                                       timedelta=real_dt.timedelta))


class _KV:
    """Stub journal: kv only (the reviewer's reproduction)."""

    def __init__(self):
        self.kv = {}

    def kv_get(self, k, default=None):
        return self.kv.get(k, default)

    def kv_set(self, k, v):
        self.kv[k] = v

    def query(self, *a, **k):
        return []


def _produce(completed, basis="venue_total_margin_balance"):
    from trader.kernel import Kernel
    k = object.__new__(Kernel)
    k.journal = _KV()
    k._balance_read = {"basis": basis, "errors": []}
    if completed is not ...:
        k._balance_read["completed_at"] = completed
    k._record_account_observation(ATTEMPT, 100.0, 100.0, 100.0)
    return k, json.loads(k.journal.kv["account_observation"])


def _read(k, now):
    return current_truth.read_account(NS(kv_get=k.journal.kv_get,
                                         query=lambda *a, **kw: []), now)


def test_reviewer_reproduction_future_completion_is_not_repaired(frozen):
    k, o = _produce(T.timestamp() + 60)
    assert o["observed_at"] == (T + timedelta(seconds=60)).isoformat()   # not ~T
    assert o["observed_at"] != o["recorded_at"]
    assert o["completion_relation"] == "completion_after_record"


@pytest.mark.parametrize("delta,relation", [
    (timedelta(microseconds=1), "completion_after_record"),
    (timedelta(seconds=60), "completion_after_record"),
    (-(timedelta(seconds=1) + timedelta(microseconds=1)), "completion_before_attempt"),
    (-timedelta(seconds=30), "completion_before_attempt"),
])
def test_impossible_completion_is_preserved_and_read_as_invalid_clock(frozen, delta,
                                                                      relation):
    src = T + delta
    k, o = _produce(src.timestamp())
    assert datetime.fromisoformat(o["observed_at"]) == pytest.approx(src, abs=timedelta(
        microseconds=1))                                   # the actual completion, kept
    assert o["observed_at"] == o["successful_read_at"]
    assert o["completion_relation"] == relation
    assert (o["status"], o["value"], o["authoritative"], o["risk_input"]) == \
        ("FRESH", 100.0, True, 100.0)                       # value/authority unchanged
    for now in (T + timedelta(seconds=90), T + timedelta(seconds=10)):
        a, err = _read(k, now)
        assert err is None and a["freshness"] == "invalid" and a["equity"] == 100.0
        assert f"invalid_clock:{relation}" in a["reasons"]


def test_valid_completion_is_the_source_time(frozen):
    src = T - timedelta(seconds=0.5)
    k, o = _produce(src.timestamp())
    assert o["observed_at"] == src.isoformat() and o["completion_relation"] == "ok"
    a, _ = _read(k, T + timedelta(seconds=5))
    assert a["freshness"] == "fresh" and a["observed_at"] == src.isoformat()


def test_missing_completion_invents_no_source_time(frozen):
    k, o = _produce(...)
    assert o["observed_at"] is None and o["successful_read_at"] is None
    assert o["completion_relation"] == "completion_missing" and o["authoritative"] is True
    a, err = _read(k, T + timedelta(seconds=5))
    assert err is None and a["freshness"] == "unavailable" and a["observed_at"] is None
    assert "completion_missing" in a["reasons"]


@pytest.mark.parametrize("bad", ["abc", float("nan"), float("inf"), True, 1e20, None, [1]])
def test_malformed_completion_is_invalid_not_publication_time(frozen, bad):
    k, o = _produce(bad)
    if bad is None:
        assert o["completion_relation"] == "completion_missing"
        return
    assert o["observed_at"] is None and o["completion_relation"] == "completion_malformed"
    a, err = _read(k, T + timedelta(seconds=5))
    assert err is None and a["freshness"] == "invalid"
    assert "invalid_clock:completion_malformed" in a["reasons"]


def test_invalid_completion_cannot_support_a_risk_pass(tmp_path, monkeypatch):
    j = Journal(tmp_path / "j.db")
    k = _kernel(j, monkeypatch, [])

    def read_before_attempt():
        k._balance_read = {"basis": "venue_total_margin_balance", "errors": [],
                           "completed_at": time.time() - 5}   # before this attempt
        return 1000.0
    monkeypatch.setattr(k, "_fetch_balance_fresh", read_before_attempt, raising=False)
    balance, status = k._risk_step()
    assert balance == 1000.0 and status["risk_state"] == "ok"   # Risk semantics intact
    k._record_risk_assessment(ControlState.ACTIVE, status, True, "")
    rec = json.loads(j.kv_get("risk_assessment"))
    assert rec["status"] == "DEGRADED"
    assert "equity_input_not_fresh_authoritative" in rec["reasons"]


def test_invalid_clock_row_is_never_carried_into_a_fallback(tmp_path, monkeypatch):
    from trader.dashboard import owner_api
    j = Journal(tmp_path / "j.db")
    k = _kernel(j, monkeypatch, [])
    reads = iter([(1000.0, time.time() + 3600), (None, None)])

    def read():
        v, done = next(reads)
        k._balance_read = ({"basis": "venue_total_margin_balance", "errors": [],
                            "completed_at": done} if v else None)
        return v
    monkeypatch.setattr(k, "_fetch_balance_fresh", read, raising=False)
    k._risk_step()
    j.log_equity(1000.0, 1000.0, 0, provenance=k._equity_provenance(1000.0))
    pt = owner_api.read_equity_series(j, datetime.now(timezone.utc))[0]["points"][-1]
    assert pt["source_observed_at"] is None and pt["source_clock"] == "completion_after_record"
    k._risk_step()
    o = _obs(j)
    assert o["status"] == "JOURNAL_FALLBACK" and o["observed_at"] is None
    assert "fallback_row_source_time_invalid" in o["reason"]


# ── current status naming: never FRESH unless the source time is fresh ────
def _statuses(a):
    return (a["status"], a["source_status"], a["last_reported"], a["authoritative"],
            a["source_authoritative"], a["freshness"])


@pytest.mark.parametrize("completed", [
    T.timestamp() + 60, (T + timedelta(microseconds=1)).timestamp(),
    (ATTEMPT - timedelta(seconds=30)).timestamp()])
def test_impossible_completion_is_currently_unavailable_not_fresh(frozen, completed):
    k, _ = _produce(completed)
    a, err = _read(k, T + timedelta(seconds=90))
    assert err is None and _statuses(a) == ("UNAVAILABLE", "FRESH", "FRESH", False, True,
                                            "invalid")
    assert a["equity"] == 100.0 and a["observed_at"]          # evidence preserved


@pytest.mark.parametrize("completed,fresh", [(..., "unavailable"), ("abc", "invalid")])
def test_missing_or_malformed_completion_is_currently_unavailable(frozen, completed, fresh):
    k, _ = _produce(completed)
    a, _ = _read(k, T + timedelta(seconds=5))
    assert _statuses(a) == ("UNAVAILABLE", "FRESH", "FRESH", False, True, fresh)


def test_stale_successful_read_is_stale_and_fresh_one_is_fresh(frozen):
    k, _ = _produce((T - timedelta(seconds=0.5)).timestamp())
    a, _ = _read(k, T + timedelta(seconds=5))
    assert _statuses(a) == ("FRESH", "FRESH", None, True, True, "fresh")
    a, _ = _read(k, T + timedelta(seconds=900))
    assert _statuses(a) == ("STALE", "FRESH", "FRESH", False, True, "stale")
    k, _ = _produce((T - timedelta(seconds=0.5)).timestamp(), basis="venue_wallet_usdt_total")
    a, _ = _read(k, T + timedelta(seconds=5))
    assert a["status"] == "VENUE_FALLBACK" and a["authoritative"] is True


def test_fresh_fallback_keeps_fallback_status_and_original_age():
    from tests.owner_frontend_fixture import account_observation
    now = datetime.now(timezone.utc)
    origin = now - timedelta(seconds=120)
    rec = account_observation(now - timedelta(seconds=2), status="JOURNAL_FALLBACK",
                              authoritative=False, observed_at=origin.isoformat(),
                              successful_read_at=origin.isoformat(),
                              fallback={"row_written_at": origin.isoformat()})
    kv = {"account_observation": json.dumps(rec)}
    a, _ = current_truth.read_account(NS(kv_get=kv.get, query=lambda *x, **y: []), now)
    assert (a["status"], a["source_status"], a["last_reported"]) == \
        ("JOURNAL_FALLBACK", "JOURNAL_FALLBACK", None)
    assert a["age_s"] == pytest.approx(120, abs=1) and a["authoritative"] is False


def test_status_is_fresh_only_when_freshness_is_fresh():
    for src in ("FRESH", "VENUE_FALLBACK", "JOURNAL_FALLBACK", "UNAVAILABLE",
                "PROVENANCE_UNRECORDED", None):
        for f in ("fresh", "stale", "invalid", "unavailable"):
            cur = current_truth.current_account_status(src, f)
            assert cur != "FRESH" or f == "fresh"
            assert cur in ("FRESH", "VENUE_FALLBACK", "JOURNAL_FALLBACK", "STALE",
                           "UNAVAILABLE")
            if f != "fresh":
                assert cur in ("STALE", "UNAVAILABLE")


def test_owner_consumers_never_show_a_future_account_as_fresh(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import TOKEN, account_observation, make_app
    from tests.test_current_truth_contract import _gql, _kv
    from trader.dashboard import server
    app, journal, _ = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_account_snapshot", lambda: {})
    monkeypatch.setattr(server, "_universe_prices", lambda: {})
    _kv(journal, "account_observation",
        account_observation(datetime.now(timezone.utc) + timedelta(seconds=60)))
    c, h = TestClient(app), {"x-luffy-token": TOKEN}
    ov = c.get("/owner-api/v1/overview", headers=h).json()["account"]
    assert (ov["status"], ov["source_status"], ov["freshness"]) == \
        ("UNAVAILABLE", "FRESH", "invalid")
    assert isinstance(ov["equity"], float)               # frontend field kept (evidence)
    summ = c.get("/api/summary", headers=h).json()["account"]
    assert summ["status"] == "UNAVAILABLE" and summ["authoritative"] is False
    with c.websocket_connect("/ws/live", headers=h) as ws:
        eq = json.loads(ws.receive_text())["equity"][0]
    assert eq["status"] == "UNAVAILABLE" and eq["freshness"] == "invalid"
    g = _gql(journal, "{ status { equity equity_freshness } }")["data"]["status"]
    assert g["equity_freshness"] == "invalid"
