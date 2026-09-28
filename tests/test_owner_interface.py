"""Owner Interface gateway: the adapter-independent test matrix (A–R) and the
regression tests for the 2026-09-28 Astra review counterexamples (W1–W11).

The kernel fixture is the real one (tests.test_kernel_boot_recovery._kernel):
real ControlStateMachine, Supervisor, RiskManager and Kernel.owner_resume;
only the venue is a recording double. The OwnerService under test is the
kernel's own (`k._owner()`), so resume/unhalt run the reviewed guarded path.
"""
import json
import os
import socket
import struct
import tempfile
import threading
import time
import uuid
from pathlib import Path
from threading import Thread as RealThread   # the kernel fixture patches threading.Thread
from types import SimpleNamespace

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import OwnerRecoveryResult
from trader.owner.authz import Authorizer, Principal
from trader.owner.contract import (OPERATIONS, MalformedRequest, OwnerRequest, Status,
                                   new_request_id)
from trader.owner.ipc import (SOCKET_NAME, InsecurePath, OwnerClient, OwnerIPCServer,
                              _mac, load_key, read_frame, write_frame)
from trader.owner.service import OwnerService
from tests.test_kernel_boot_recovery import _kernel, _last_event, _protected, _transitions
from tests.test_owner_recovery import HookVenue, _contained, loosen
from tests.test_owner_recovery_risk_guard import risk_halted, tg_update

TESTER = Principal("tester", frozenset(OPERATIONS), frozenset({"test"}))


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def _active(journal, venue, monkeypatch):
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert journal.kv_get("control_state") == "ACTIVE"
    return k


def _bind_tester(authz: Authorizer):
    authz.principals["tester"] = TESTER
    authz.bind("test", "fixture-owner", "tester")


def svc(k) -> OwnerService:
    s = k._owner()
    _bind_tester(s.authorizer)
    return s


def req(op, rid=None, *, identity="fixture-owner", channel="test", ref="ref-1",
        issued_at=None, **kw):
    return OwnerRequest(rid or new_request_id(channel), op, channel, identity,
                        issued_at=time.time() if issued_at is None else issued_at,
                        request_ref=ref, **kw)


def _count(journal, event, **where):
    rows = journal.query("SELECT detail FROM control_events WHERE event=?", (event,))
    out = [json.loads(r["detail"]) for r in rows]
    return [d for d in out if all(d.get(k) == v for k, v in where.items())]


def _state(journal):
    return journal.kv_get("control_state")


def _audit(journal, **where):
    rows = [dict(r) for r in journal.query("SELECT * FROM owner_audit ORDER BY id")]
    return [r for r in rows if all(r.get(k) == v for k, v in where.items())]


# ── A. status read ──────────────────────────────────────────────────────────
def test_a_status_and_health_are_read_only_and_audited(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s, mark = svc(k), _last_event(journal)
    r = s.execute(req("status", "test-status-00001"))
    assert r.status == Status.ACCEPTED and r.data["control_state"] == "ACTIVE"
    assert r.data["open_trades"] == 1 and r.data["market"] == "futures"
    assert (r.principal, r.channel, r.request_ref) == ("tester", "test", "ref-1")
    h = s.execute(req("health"))
    assert h.status == Status.ACCEPTED and h.data["supervisor"]["outcome"]
    assert _last_event(journal) == mark                     # no control events
    assert journal.query("SELECT COUNT(*) n FROM owner_requests")[0]["n"] == 0
    assert _audit(journal, request_id="test-status-00001") == []   # batched, not per read
    s.flush_audit()
    row = _audit(journal, request_id="test-status-00001")    # then a durable audit row
    assert len(row) == 1 and row[0]["status"] == "ACCEPTED" and row[0]["principal"] == "tester"
    assert venue.mutations == []


# ── B/C. freeze, halt ───────────────────────────────────────────────────────
def test_b_freeze_is_typed_audited_and_exact(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    r = svc(k).execute(req("freeze", "test-freeze-000001"))
    assert (r.status, r.control_state_before, r.control_state_after) == (
        Status.ACCEPTED, "ACTIVE", "FROZEN")
    row = journal.query("SELECT * FROM control_events WHERE id=?", (r.transition_event_id,))[0]
    assert (row["event"], row["from_state"], row["to_state"], row["actor"]) == (
        "state_change", "ACTIVE", "FROZEN", "operator")
    assert "test-freeze-000001" in row["detail"] and "principal tester" in row["detail"]
    intake, done = (_count(journal, e, request_id="test-freeze-000001")
                    for e in ("owner_interface_request", "owner_interface_result"))
    assert len(intake) == 1 and len(done) == 1
    assert intake[0]["principal"] == "tester" and intake[0]["identity"] == "fixture-owner"
    assert done[0]["before"] == "ACTIVE" and done[0]["after"] == "FROZEN"
    assert r.audit_event_ids[0] < r.transition_event_id < r.audit_event_ids[-1]
    assert _audit(journal, request_id="test-freeze-000001")[0]["status"] == "ACCEPTED"


def test_c_halt_then_repeat_is_already_set_with_owner_hold(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    assert s.execute(req("halt")).status == Status.ACCEPTED and _state(journal) == "HALTED"
    r2 = s.execute(req("halt"))
    assert r2.status == Status.ALREADY_SET
    hold = journal.query("SELECT event, actor FROM control_events WHERE id=?",
                         (r2.transition_event_id,))[0]
    assert (hold["event"], hold["actor"]) == ("state_hold", "operator")


# ── D/E. guarded resume ─────────────────────────────────────────────────────
def test_d_guarded_resume_safe_activates_through_supervisor(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("freeze"))
    mark = _last_event(journal)
    r = s.execute(req("resume"))
    assert r.status == Status.ACTIVATED and _state(journal) == "ACTIVE"
    assert _transitions(journal, mark) == [("FROZEN", "RECOVERY", "operator"),
                                           ("RECOVERY", "ACTIVE", "supervisor")]
    assert r.risk_release and r.risk_release["allowed"] is True
    sup = _count(journal, "owner_recovery_result")[-1]
    assert sup["status"] == "ACTIVATED" and sup["principal"] == "tester"
    assert sup["request_event_id"] in r.audit_event_ids


def test_e_guarded_resume_refused_stays_contained(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    s = svc(k)
    r = s.execute(req("resume"))
    assert r.status == Status.CONTAINED and _state(journal) == "RECOVERY"
    assert "position_unprotected:BTC/USDT" in r.reasons
    s.execute(req("halt"))
    h = s.execute(req("resume"))
    assert h.status == Status.REFUSED and h.reasons == ("halted_requires_explicit_unhalt",)
    assert _state(journal) == "HALTED"


# ── F/G. explicit unhalt ────────────────────────────────────────────────────
def test_f_explicit_unhalt_safe(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("halt"))
    r = s.execute(req("unhalt"))
    assert r.status == Status.ACTIVATED and _state(journal) == "ACTIVE"
    assert _count(journal, "owner_recovery_requested")[-1]["operation"] == "owner_unhalt"


def test_g_explicit_unhalt_risk_refused(world, monkeypatch):
    journal, venue = world
    k = risk_halted(journal, venue, monkeypatch)
    mark, reads = _last_event(journal), venue.reads
    r = svc(k).execute(req("unhalt"))
    assert r.status == Status.REFUSED and r.reasons == ("risk_halt_active",)
    assert r.risk_release["allowed"] is False
    assert _state(journal) == "HALTED" and _transitions(journal, mark) == []
    assert venue.reads == reads and venue.mutations == []


# ── H/I/J. idempotency ──────────────────────────────────────────────────────
def test_h_duplicate_delivery_executes_once(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("freeze"))
    first = s.execute(req("resume", "test-dup-resume-01"))
    mark = _last_event(journal)
    again = s.execute(req("resume", "test-dup-resume-01"))
    assert again.replayed is True and first.replayed is False
    assert (again.status, again.audit_event_ids) == (first.status, first.audit_event_ids)
    assert _last_event(journal) == mark
    assert len(_count(journal, "owner_interface_request", request_id="test-dup-resume-01")) == 1


def test_h2_reused_id_with_different_body_is_refused(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("freeze", "test-conflict-0001"))
    r = s.execute(req("halt", "test-conflict-0001"))
    assert r.status == Status.REFUSED and r.reasons == ("request_id_conflict",)
    assert _state(journal) == "FROZEN"


def test_i_concurrent_duplicates_execute_at_most_once(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("freeze"))
    gate = threading.Event()
    venue.on_positions = lambda: gate.wait(1.0)
    results, rid = [], "test-concurrent-01"
    threads = [RealThread(target=lambda: results.append(s.execute(req("resume", rid))))
               for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(10)
    assert len(results) == 8
    assert len(_count(journal, "owner_recovery_requested")) == 1
    # revision 5: a duplicate never waits on the executor; it is answered
    # IN_PROGRESS (reserved/running) or, if it arrives after, with the replay
    executed = [r for r in results if r.status == Status.ACTIVATED and not r.replayed]
    assert len(executed) == 1
    assert {r.status for r in results} <= {Status.ACTIVATED, Status.IN_PROGRESS}
    assert s.execute(req("resume", rid)).replayed                 # the final answer


def test_i2_a_different_recovery_is_refused_at_once(world, monkeypatch):
    """Recovery never queues: while one runs, another is refused immediately."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("freeze"))
    gate = threading.Event()
    venue.on_positions = lambda: gate.wait(2.0)
    t = RealThread(target=lambda: s.execute(req("resume", "test-first-recov1")))
    t.start()
    time.sleep(0.2)
    t0 = time.monotonic()
    other = s.execute(req("unhalt", "test-second-recov"))
    assert time.monotonic() - t0 < 0.3
    assert other.status == Status.REFUSED and other.reasons == ("recovery_in_progress",)
    gate.set()
    t.join(10)


def test_j_stale_future_and_missing_time(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    old = s.execute(req("halt", "test-stale-000001", issued_at=time.time() - 3600))
    assert old.status == Status.REFUSED and old.reasons == ("request_stale",)
    fut = s.execute(req("halt", "test-future-00001", issued_at=time.time() + 3600))
    assert fut.reasons == ("request_from_future",)
    assert _state(journal) == "ACTIVE"
    replay = s.execute(req("halt", "test-stale-000001", issued_at=time.time() - 3600))
    assert replay.replayed and replay.reasons == ("request_stale",)
    wire = req("halt").to_wire()
    del wire["issued_at"]
    with pytest.raises(MalformedRequest):                   # W10: never defaulted
        OwnerRequest.from_wire(wire)


def test_j2_claim_left_by_a_crashed_kernel_is_never_reexecuted(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    r = req("halt", "test-crashed-00001")
    s._claim(r, s.reserve(r), time.time())                   # claimed, then the kernel dies
    k._owner_service = None
    out = svc(k).execute(r)
    assert out.status == Status.OUTCOME_UNKNOWN
    assert out.reasons == ("outcome_unknown_after_restart",)
    assert _state(journal) == "ACTIVE"


def test_w8_result_persistence_failure_is_outcome_unknown_not_conflict(world, monkeypatch):
    """Astra E: effect applied, result not recorded, same process → retries
    answer outcome unknown, never request_id_conflict, never re-execute."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    real = s._complete

    def broken(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(s, "_complete", broken)
    r = s.execute(req("halt", "test-persist-fail1"))
    assert r.status == Status.ERROR and r.reasons == ("result_not_recorded",)
    assert r.data["observed_status"] == "ACCEPTED" and _state(journal) == "HALTED"
    monkeypatch.setattr(s, "_complete", real)
    mark = _last_event(journal)
    for _ in range(2):
        again = s.execute(req("halt", "test-persist-fail1"))
        assert again.status == Status.ERROR and again.reasons == ("outcome_unknown",)
    assert _last_event(journal) == mark                      # never re-executed


def test_w8_claim_failure_means_not_executed(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)

    def broken(*a, **kw):
        raise OSError("locked")
    monkeypatch.setattr(s, "_claim", broken)
    r = s.execute(req("halt"))
    assert r.status == Status.REFUSED and r.reasons == ("persistence_unavailable",)
    assert _state(journal) == "ACTIVE"


# ── K. authorization ────────────────────────────────────────────────────────
def test_k_unbound_identity_or_grant_never_executes_or_claims(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    rid = "test-authz-000001"
    assert s.execute(req("halt", rid, identity="mallory")).reasons == ("identity_not_bound",)
    assert s.execute(req("halt", rid, channel="whatsapp", identity="+1555")).reasons == (
        "identity_not_bound",)
    ro = s.execute(req("halt", rid, channel="cli", identity="local"))
    assert ro.reasons == ("operation_not_permitted",) and ro.principal == "local-operator"
    assert s.execute(req("status", channel="cli", identity="local")).status == Status.ACCEPTED
    assert _state(journal) == "ACTIVE"
    assert len(_count(journal, "owner_interface_rejected")) == 3
    assert s.execute(req("halt", rid)).status == Status.ACCEPTED   # id never poisoned


def test_k2_requests_cannot_carry_a_principal():
    wire = req("halt").to_wire()
    with pytest.raises(MalformedRequest):
        OwnerRequest.from_wire({**wire, "principal": "owner"})


# ── L/M. unsupported / malformed ────────────────────────────────────────────
@pytest.mark.parametrize("op", ["close_all", "set_state", "ACTIVE", "", "place_order",
                                "repair_baseline", "prepare_rollback"])
def test_l_unsupported_operation_is_malformed(op):
    with pytest.raises(MalformedRequest):
        req(op)


@pytest.mark.parametrize("patch", [
    {"request_id": "short"}, {"request_id": "x" * 200}, {"request_id": "bad id with spaces"},
    {"identity": ""}, {"identity": "a b"}, {"channel": "sms"},
    {"issued_at": "yesterday"}, {"issued_at": True}, {"issued_at": float("nan")},
    {"issued_at": float("inf")},
    # W10: malformed metadata never becomes {} and executes
    {"meta": []}, {"meta": False}, {"meta": 0}, {"meta": ""}, {"meta": None},
    {"meta": {"note": "SENTINEL-SECRET"}}, {"meta": {"bot_token": "x"}},
    {"meta": {"command": "has space"}}, {"meta": {"command": 1}},
    {"args": []}, {"args": {"x": 1}}, {"args": None}, {"extra_field": 1},
])
def test_m_malformed_payload_is_refused(patch):
    wire = req("freeze", "test-malformed-01").to_wire()
    wire.update(patch)
    with pytest.raises(MalformedRequest):
        OwnerRequest.from_wire(wire)


@pytest.mark.parametrize("op, args", [
    ("close_trade", {}), ("close_trade", {"trade_id": "a b"}),
    ("close_trade", {"trade_id": "x", "extra": 1}),
    ("set_market_type", {"market": "margin"}), ("set_market_type", {}),
    ("panic", {"now": True})])
def test_m2_operation_args_are_typed(op, args):
    with pytest.raises(MalformedRequest):
        req(op, args=args)


def test_m3_non_request_objects_never_execute(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    for bad in (None, {"operation": "halt"}, "halt", SimpleNamespace(operation="halt")):
        assert svc(k).execute(bad).status == Status.REFUSED     # malformed: can never run
    assert _state(journal) == "ACTIVE"


# ── intents moved behind the gateway (W5) ───────────────────────────────────
def test_w5_panic_close_market_are_granted_typed_and_audited(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    p = s.execute(req("panic", "test-panic-000001"))
    assert p.status == Status.ACCEPTED
    pending = json.loads(journal.kv_get("panic_requested"))            # token + acceptance watermark
    assert pending["request_id"] == "test-panic-000001" and pending["intent_event_id"] > 0
    # revision 5: acceptance is an owner hold (the panic's identity) …
    hold = journal.query("SELECT event, actor, to_state FROM control_events WHERE id=?",
                         (p.transition_event_id,))[0]
    assert (hold["event"], hold["actor"], hold["to_state"]) == ("state_change", "operator",
                                                                "FROZEN")
    assert pending["intent_event_id"] == p.transition_event_id
    ev = journal.query("SELECT actor, detail FROM control_events WHERE event='panic'")[-1]
    assert ev["actor"] == "operator" and json.loads(ev["detail"])["principal"] == "tester"
    trade = journal.open_trades()[0]["id"]
    c = s.execute(req("close_trade", args={"trade_id": trade}))
    assert c.status == Status.ACCEPTED and json.loads(journal.kv_get("close_requests")) == [trade]
    assert s.execute(req("close_trade", args={"trade_id": "nope"})).reasons == ("trade_not_open",)
    m = s.execute(req("set_market_type", args={"market": "spot"}))
    assert m.status == Status.ACCEPTED and journal.kv_get("market_type") == "spot"
    s.authorizer.principals["tester"] = Principal("tester", frozenset(), frozenset({"test"}))
    journal.kv_set("panic_requested", "0")
    for op, args in (("panic", {}), ("close_trade", {"trade_id": trade}),
                     ("set_market_type", {"market": "futures"})):
        assert s.execute(req(op, args=args)).reasons == ("operation_not_permitted",)
    assert journal.kv_get("panic_requested") == "0" and journal.kv_get("market_type") == "spot"


def test_w5_kernel_drain_consumes_owner_appends(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    trade = journal.open_trades()[0]["id"]
    s.execute(req("close_trade", args={"trade_id": trade}))
    closed = []
    k.executor = SimpleNamespace(close=lambda t, *a, **kw: closed.append(t["id"]) or True)
    k.feed = SimpleNamespace(price=lambda sym: 100.0)
    assert k._drain_close_requests() == 1 and closed == [trade]
    assert json.loads(journal.kv_get("close_requests")) == []


# ── IPC ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def ipc_dir():
    return Path(tempfile.mkdtemp(prefix="oi-")) / "ipc"


class SlowResume:
    def __init__(self, delay):
        self.delay, self.calls = delay, 0

    def __call__(self, ctx, *, allow_unhalt=False, **_bound):
        self.calls += 1
        time.sleep(self.delay)
        return OwnerRecoveryResult("CONTAINED", "RECOVERY", outcome="NEEDS_OWNER",
                                   reasons=("fixture",))


def _plain_service(tmp_path, resume, **kw):
    j = Journal(tmp_path / "ipc.db")
    return j, OwnerService(j, ControlStateMachine(j), resume=resume, **kw)


def dreq(op, rid=None, **kw):
    """A dashboard-channel request (identity 'session' → owner by default)."""
    return req(op, rid, channel="dashboard", identity="session", **kw)


def test_n_timeout_while_kernel_busy_is_outcome_unknown_then_replays(tmp_path, ipc_dir):
    resume = SlowResume(0.8)
    j, s = _plain_service(tmp_path, resume)
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        r = OwnerClient(ipc_dir, "dashboard", control_timeout_s=0.2).call(
            dreq("resume", "dashboard-timeout-01"))
        assert r.status == Status.ERROR and r.reasons == ("kernel_timeout_outcome_unknown",)
        time.sleep(1.0)
        again = OwnerClient(ipc_dir, "dashboard").call(dreq("resume", "dashboard-timeout-01"))
        assert again.replayed and again.status == Status.CONTAINED and resume.calls == 1
    finally:
        server.stop()


def test_o_adapter_reconnect_retry_no_fallback(tmp_path, ipc_dir):
    j, s = _plain_service(tmp_path, SlowResume(0))
    client = OwnerClient(ipc_dir, "dashboard")
    r = client.call(dreq("halt", "dashboard-retry-0001"))
    assert r.status == Status.UNAVAILABLE                    # no keys, no socket yet
    assert j.kv_get("control_state") is None
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        first = client.call(dreq("halt", "dashboard-retry-0001"))
        second = client.call(dreq("halt", "dashboard-retry-0001"))
        assert first.status == Status.ACCEPTED and second.replayed
        assert second.audit_event_ids == first.audit_event_ids
    finally:
        server.stop()
    r = client.call(dreq("status"))
    assert r.status == Status.UNAVAILABLE and r.reasons == ("kernel_unavailable",)


def test_w7_recovery_cannot_exhaust_containment_slots(tmp_path, ipc_dir):
    """Astra O: eight pending recoveries no longer block an urgent halt."""
    resume = SlowResume(1.0)
    j, s = _plain_service(tmp_path, resume)
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        client = OwnerClient(ipc_dir, "dashboard")
        results = []
        threads = [RealThread(target=lambda i=i: results.append(
            client.call(dreq("resume", f"dashboard-flood-{i:04d}")))) for i in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        t0 = time.monotonic()
        h = client.call(dreq("halt", "dashboard-urgent-01"))
        assert h.status == Status.ACCEPTED and time.monotonic() - t0 < 0.5
        for t in threads:
            t.join(10)
        assert resume.calls == 1                              # one ran; the rest refused
        assert sum(r.reasons in (("recovery_in_progress",), ("owner_interface_busy",))
                   for r in results) == 7
    finally:
        server.stop()


class _BlockingRecoveryService:
    """A service whose recoveries hold their connection (the reviewed worst case)."""

    def __init__(self):
        self.gate = threading.Event()

    def execute(self, r):
        from trader.owner.contract import OwnerResult
        if r.operation in ("resume", "unhalt"):
            self.gate.wait(3)
        return OwnerResult(r.request_id, r.operation, Status.ACCEPTED)

    def record_ingress_refusal(self, channel, reason):
        pass

    def record_admission_refusal(self, r, reason):
        from trader.owner.contract import refused
        return refused(r, reason)


def test_w7_ipc_class_slots_reserve_containment(ipc_dir):
    """Even if recoveries held their connections, containment keeps its own slots."""
    fake = _BlockingRecoveryService()
    server = OwnerIPCServer(fake, ipc_dir).start()
    try:
        client = OwnerClient(ipc_dir, "dashboard")
        results = []
        threads = [RealThread(target=lambda i=i: results.append(
            client.call(dreq("resume", f"dashboard-block-{i:04d}")))) for i in range(8)]
        for t in threads:
            t.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        h = client.call(dreq("halt"))
        assert h.status == Status.ACCEPTED and time.monotonic() - t0 < 0.5
        fake.gate.set()
        for t in threads:
            t.join(10)
        assert sum(r.reasons == ("owner_interface_busy",) for r in results) == 6
    finally:
        fake.gate.set()
        server.stop()


def _raw(ipc_dir, frame):
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(3)
    c.connect(str(ipc_dir / SOCKET_NAME))
    try:
        write_frame(c, frame)
        return read_frame(c)
    except (ConnectionError, OSError):
        return None
    finally:
        c.close()


def test_w3_channel_is_authenticated_not_asserted(tmp_path, ipc_dir):
    """Astra F: a same-uid client without the dashboard key cannot act as the dashboard."""
    j, s = _plain_service(tmp_path, SlowResume(0))
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        cli = OwnerClient(ipc_dir, "cli")
        assert cli.call(dreq("halt")).reasons == ("wrong_channel_for_client",)
        cli_key = load_key(ipc_dir, "cli")
        forged = dreq("halt", "dashboard-forged-01").to_wire()
        for key in (cli_key, b"0" * 32):
            body = {"channel": "dashboard", "nonce": "n" * 32, "request": forged}
            assert _raw(ipc_dir, {"v": 2, **body, "mac": _mac(key, body)}) is None
        body = {"channel": "cli", "nonce": "n" * 32, "request": forged}
        out = _raw(ipc_dir, {"v": 2, **body, "mac": _mac(cli_key, body)})
        assert out["result"]["reasons"] == ["malformed_request"]  # channel mismatch
        halt_as_cli = cli.call(req("halt", channel="cli", identity="local"))
        assert halt_as_cli.reasons == ("operation_not_permitted",)
        assert j.kv_get("control_state") is None
        s.flush_audit()
        ingress = [r["reasons"] for r in _audit(j) if r["request_id"] is None]
        assert ingress.count('["channel_not_authenticated"]') == 2
    finally:
        server.stop()


def test_m4_malformed_frames_over_ipc(tmp_path, ipc_dir):
    j, s = _plain_service(tmp_path, SlowResume(0))
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        for payload in (struct.pack(">I", 5) + b"nope!", struct.pack(">I", 10 ** 6)):
            c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            c.settimeout(3)
            c.connect(str(ipc_dir / SOCKET_NAME))
            c.sendall(payload)
            assert c.recv(10) == b""
            c.close()
        key = load_key(ipc_dir, "dashboard")
        for bad in ({**dreq("halt").to_wire(), "operation": "panic_all"},
                    {**dreq("halt").to_wire(), "meta": []},
                    {"method": "set", "args": ["ACTIVE"]}):
            body = {"channel": "dashboard", "nonce": "m" * 32, "request": bad}
            out = _raw(ipc_dir, {"v": 2, **body, "mac": _mac(key, body)})
            assert out["result"]["status"] == Status.REFUSED        # malformed: definitive
        assert j.kv_get("control_state") is None
        assert OwnerClient(ipc_dir, "dashboard").call(dreq("status")).status == Status.ACCEPTED
    finally:
        server.stop()


def test_w9_socket_dir_and_keys_are_private(tmp_path, ipc_dir):
    import stat as st
    j, s = _plain_service(tmp_path, SlowResume(0))
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        assert st.S_IMODE(os.stat(ipc_dir).st_mode) == 0o700
        assert st.S_IMODE(os.stat(ipc_dir / SOCKET_NAME).st_mode) == 0o600
        for key in (ipc_dir / "keys").iterdir():
            assert st.S_IMODE(os.stat(key).st_mode) == 0o600
    finally:
        server.stop()
    other = OwnerIPCServer(s, ipc_dir, allowed_uid=os.getuid() + 1).start()
    try:
        r = OwnerClient(ipc_dir, "dashboard").call(dreq("halt"))
        assert r.status == Status.ERROR                      # refused peer: no answer
        assert j.kv_get("control_state") is None
    finally:
        other.stop()


def test_w9_stop_never_unlinks_a_replacement_socket(tmp_path, ipc_dir):
    j, s = _plain_service(tmp_path, SlowResume(0))
    server = OwnerIPCServer(s, ipc_dir).start()
    path = ipc_dir / SOCKET_NAME
    os.unlink(path)
    other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    other.bind(str(path))
    try:
        server.stop()
        assert path.exists()                                  # the replacement survives
    finally:
        other.close()


@pytest.mark.parametrize("fault", ["symlink", "group_writable_dir", "open_parent"])
def test_w9_insecure_paths_are_refused(tmp_path, fault):
    base = Path(tempfile.mkdtemp(prefix="oi-"))
    j, s = _plain_service(tmp_path, SlowResume(0))
    if fault == "symlink":
        real = base / "real"
        real.mkdir(mode=0o700)
        (base / "ipc").symlink_to(real)
        target = base / "ipc"
    elif fault == "group_writable_dir":
        target = base / "ipc"
        target.mkdir()
        os.chmod(target, 0o770)
    else:
        parent = base / "shared"
        parent.mkdir()
        os.chmod(parent, 0o775)                               # like production data/
        target = parent / "ipc"
    with pytest.raises(InsecurePath):
        OwnerIPCServer(s, target).start()
    assert OwnerClient(target, "dashboard").call(dreq("status")).status == Status.UNAVAILABLE


def test_w9_client_rejects_a_fabricated_listener(tmp_path, ipc_dir):
    """Astra C: a replacement listener returning ACTIVATED is not believed."""
    j, s = _plain_service(tmp_path, SlowResume(0))
    OwnerIPCServer(s, ipc_dir).start().stop()                 # keys remain, socket gone
    fake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    fake.bind(str(ipc_dir / SOCKET_NAME))
    fake.listen(4)

    def serve(nonce_override=None, key=None):
        conn, _ = fake.accept()
        frame = read_frame(conn)
        body = {"nonce": nonce_override or frame["nonce"],
                "result": {"request_id": frame["request"]["request_id"],
                           "operation": "resume", "status": "ACTIVATED"}}
        out = {"v": 2, **body}
        if key is not None:
            out["mac"] = _mac(key, body)
        write_frame(conn, out)
        conn.close()
    client = OwnerClient(ipc_dir, "dashboard")
    try:
        for kw in ({}, {"key": load_key(ipc_dir, "cli")},
                   {"key": load_key(ipc_dir, "dashboard"), "nonce_override": "x" * 32}):
            t = RealThread(target=serve, kwargs=kw)
            t.start()
            r = client.call(dreq("resume"))
            t.join(5)
            assert r.status == Status.ERROR and r.reasons[0].endswith("outcome_unknown")
    finally:
        fake.close()


def test_w8_failure_after_delivery_is_outcome_unknown(tmp_path, ipc_dir):
    """Astra P: a peer that takes the request and drops the connection is
    reported as outcome unknown, not UNAVAILABLE / not executed."""
    j, s = _plain_service(tmp_path, SlowResume(0))
    OwnerIPCServer(s, ipc_dir).start().stop()
    fake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    fake.bind(str(ipc_dir / SOCKET_NAME))
    fake.listen(1)

    def swallow():
        conn, _ = fake.accept()
        read_frame(conn)
        conn.close()
    t = RealThread(target=swallow)
    t.start()
    try:
        r = OwnerClient(ipc_dir, "dashboard").call(dreq("halt"))
        assert r.status == Status.ERROR and "outcome_unknown" in r.reasons[0]
    finally:
        t.join(5)
        fake.close()


# ── N. persistence ──────────────────────────────────────────────────────────
def test_n2_schema_version_is_checked(tmp_path):
    j = Journal(tmp_path / "j.db")
    OwnerService(j, ControlStateMachine(j), resume=None)
    j.kv_set("owner_interface_schema", "99")
    with pytest.raises(RuntimeError):
        OwnerService(j, ControlStateMachine(j), resume=None)


def test_n4_read_audit_batch_flushes_by_size(tmp_path):
    j, s = _plain_service(tmp_path, SlowResume(0))
    _bind_tester(s.authorizer)
    for _ in range(s.audit_flush_rows):
        s.execute(req("status"))
    assert len(_audit(j)) == s.audit_flush_rows


def test_n3_completed_records_are_pruned_after_retention(tmp_path):
    j, s = _plain_service(tmp_path, SlowResume(0))
    _bind_tester(s.authorizer)
    s.execute(req("halt", "test-prune-000001"))
    with j._tx() as c:
        c.execute("UPDATE owner_requests SET finished_at='2000-01-01T00:00:00+00:00'")
        c.execute("UPDATE owner_audit SET ts='2000-01-01T00:00:00+00:00'")
    s._writes = 255
    s.execute(req("status"))
    s.flush_audit()
    assert j.query("SELECT COUNT(*) n FROM owner_requests")[0]["n"] == 0
    rows = _audit(j)
    assert [r["operation"] for r in rows] == ["status"]   # only the fresh row survives


# ── P. dashboard (GraphQL → IPC → OwnerService) ─────────────────────────────
def _dashboard_app(journal_path, ipc_dir, authenticated=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from trader.api.graphql_schema import make_graphql_router
    from trader.dashboard.server import _owner_gateway
    auth = SimpleNamespace(authenticated=lambda scope: authenticated)
    cfg = {"owner_interface": {"ipc_dir": str(ipc_dir)}}
    app = FastAPI()
    app.include_router(make_graphql_router(Journal(journal_path), _owner_gateway(cfg, auth)))
    return TestClient(app)


FIELDS = "{request_id status control_state_after reasons replayed audit_event_ids message}"
Q = ("mutation($o:String!,$r:String!,$t:Float!){owner_control(operation:$o,request_id:$r,"
     "issued_at_ms:$t)" + FIELDS + "}")


def _gql(client, query, **v):
    out = client.post("/graphql", json={"query": query, "variables": v}).json()
    assert "errors" not in out, out
    return next(iter(out["data"].values()))


def test_p_dashboard_double_submit_executes_once(tmp_path, ipc_dir):
    j, s = _plain_service(tmp_path, SlowResume(0))
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        client = _dashboard_app(tmp_path / "ipc.db", ipc_dir)
        cid, t = uuid.uuid4().hex, time.time() * 1000
        ra = _gql(client, Q, o="halt", r=cid, t=t)
        rb = _gql(client, Q, o="halt", r=cid, t=t)
        assert ra["status"] == "ACCEPTED" and ra["control_state_after"] == "HALTED"
        assert rb["replayed"] is True and rb["audit_event_ids"] == ra["audit_event_ids"]
        changes = j.query("SELECT actor FROM control_events WHERE event='state_change'")
        assert [r["actor"] for r in changes] == ["dashboard"]
        for op in ("ACTIVE", "set_control_state", "close_trade"):
            assert _gql(client, Q, o=op, r=uuid.uuid4().hex, t=t)["reasons"] == [
                "unsupported_operation"]
        assert _gql(client, Q, o="halt", r="x", t=t)["reasons"] == ["malformed_request"]
        # a retry later with the ORIGINAL click time is judged by that time
        old = _gql(client, Q, o="freeze", r=uuid.uuid4().hex, t=t - 3_600_000)
        assert old["reasons"] == ["request_stale"]
    finally:
        server.stop()


def test_w5_dashboard_legacy_mutations_use_the_gateway(tmp_path, ipc_dir):
    """Astra H: panic / close_trade / set_market_type obey grants, derive the actor."""
    from trader.core.types import Position, Side
    j, s = _plain_service(tmp_path, SlowResume(0))
    j.add_trade(Position(id="pos_0", symbol="UNI/USDT", side=Side.LONG, amount=1.0,
                         entry_price=5.0, notional_usdt=5.0, stop_loss=4.5,
                         market_type="futures", exec_mode="live",
                         strategy_id="s", strategy_name="x"))
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        client = _dashboard_app(tmp_path / "ipc.db", ipc_dir)
        t = time.time() * 1000
        P = "mutation($r:String!,$t:Float!){panic(request_id:$r,issued_at_ms:$t)" + FIELDS + "}"
        C = ("mutation($x:String!,$r:String!,$t:Float!){close_trade(trade_id:$x,request_id:$r,"
             "issued_at_ms:$t)" + FIELDS + "}")
        M = ("mutation($m:String!,$r:String!,$t:Float!){set_market_type(market:$m,request_id:$r,"
             "issued_at_ms:$t)" + FIELDS + "}")
        assert _gql(client, P, r=uuid.uuid4().hex, t=t)["status"] == "ACCEPTED"
        assert _gql(client, C, x="pos_0", r=uuid.uuid4().hex, t=t)["status"] == "ACCEPTED"
        assert _gql(client, M, m="spot", r=uuid.uuid4().hex, t=t)["status"] == "ACCEPTED"
        actors = {r["actor"] for r in j.query("SELECT actor FROM control_events WHERE event "
                                              "IN ('panic','manual_close','mode_switch')")}
        assert actors == {"dashboard"}
        s.authorizer.principals["owner"] = Principal("owner", frozenset(),
                                                     frozenset({"dashboard"}))
        j.kv_set("panic_requested", "0")
        for q, v in ((P, {}), (C, {"x": "pos_0"}), (M, {"m": "futures"})):
            assert _gql(client, q, r=uuid.uuid4().hex, t=t, **v)["reasons"] == [
                "operation_not_permitted"]
        assert j.kv_get("panic_requested") == "0" and j.kv_get("market_type") == "spot"
        # no mutation takes an actor argument any more
        forged = client.post("/graphql", json={"query": 'mutation{panic(actor:"supervisor",'
                             'request_id:"abcdefabcdefabcdef",issued_at_ms:1){status}}'}).json()
        assert "errors" in forged
    finally:
        server.stop()


def test_p2_dashboard_unauthenticated_and_kernel_down(tmp_path, ipc_dir):
    j, s = _plain_service(tmp_path, SlowResume(0))
    t = time.time() * 1000
    unauth = _dashboard_app(tmp_path / "ipc.db", ipc_dir, authenticated=False)
    assert _gql(unauth, Q, o="freeze", r=uuid.uuid4().hex, t=t)["reasons"] == [
        "not_authenticated"]
    down = _dashboard_app(tmp_path / "ipc.db", ipc_dir)
    assert _gql(down, Q, o="freeze", r=uuid.uuid4().hex, t=t)["status"] == "UNAVAILABLE"
    assert j.kv_get("control_state") is None


# ── Q. Telegram ─────────────────────────────────────────────────────────────
def _tg(k, monkeypatch, text, update):
    replies = []
    k.notifier.chat_id = "1"
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    k._handle_tg_command(text, "https://api.telegram.org/botSECRET-TOKEN", update=update)
    return replies


def test_q_telegram_duplicate_stale_and_malformed_updates(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    upd = tg_update()
    assert _tg(k, monkeypatch, "/halt", upd) == ["😴 HALTED."]
    mark = _last_event(journal)
    assert _tg(k, monkeypatch, "/halt", upd) == ["😴 HALTED."]      # redelivered update
    assert _last_event(journal) == mark
    assert "request_stale" in _tg(k, monkeypatch, "/unhalt", tg_update(age_s=86400))[0]
    assert _tg(k, monkeypatch, "/unhalt", tg_update(chat=999)) == []
    nodate = tg_update()
    del nodate["message"]["date"]                               # W10: never refreshed
    assert "message_date_missing" in _tg(k, monkeypatch, "/unhalt", nodate)[0]
    assert _tg(k, monkeypatch, "/haltnow", tg_update()) == []
    assert _state(journal) == "HALTED"
    assert _tg(k, monkeypatch, "/status", tg_update())[0].startswith("state=HALTED open=1")


def test_w4_telegram_foreign_sender_in_owner_chat_is_refused(world, monkeypatch):
    """Astra G: a group member other than the owner is not the owner."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    mark = _last_event(journal)
    out = _tg(k, monkeypatch, "/halt", tg_update(sender=42))
    assert "identity_not_bound" in out[0] and _state(journal) == "ACTIVE"
    assert _transitions(journal, mark) == []
    assert "identity_not_bound" in _tg(k, monkeypatch, "/panic", tg_update(sender=42))[0]
    assert journal.kv_get("panic_requested") in (None, "0")
    # an explicit sender binding replaces the chat-id default
    k.cfg["owner_interface"] = {"identities": {"telegram": {"42": "owner"}}}
    k._owner_service = None
    assert _tg(k, monkeypatch, "/halt", tg_update(sender=42)) == ["😴 HALTED."]
    assert "identity_not_bound" in _tg(k, monkeypatch, "/freeze", tg_update(sender=1))[0]


def test_w7_telegram_halt_is_not_serialized_behind_resume(world, monkeypatch):
    """Astra G/O: a /halt in the same batch as a slow /resume is handled at once."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    monkeypatch.setattr("trader.kernel.threading.Thread", RealThread)   # real workers
    s = svc(k)
    s.execute(req("freeze"))
    k.notifier.chat_id = "1"
    replies = []
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(
        (time.monotonic(), kw["json"]["text"])))
    gate = threading.Event()
    venue.on_positions = lambda: gate.wait(3.0)
    t0 = time.monotonic()
    k._tg_dispatch("/resume", "https://x", tg_update())
    k._tg_dispatch("/halt", "https://x", tg_update())
    deadline = time.monotonic() + 2
    while not any(t == "😴 HALTED." for _, t in replies) and time.monotonic() < deadline:
        time.sleep(0.01)
    halted_at = next(ts for ts, t in replies if t == "😴 HALTED.")
    assert halted_at - t0 < 0.5 and _state(journal) == "HALTED"
    gate.set()
    for w in k._tg_workers.values():
        w.shutdown(wait=True)
    assert _state(journal) == "HALTED"                         # the newer halt won
    assert _transitions(journal)[-1][1] != "ACTIVE"


# ── R. WhatsApp / OpenClaw ──────────────────────────────────────────────────
class _ActivatingResume:
    """Fixture recovery that activates, so a wrong execution is visible."""

    def __init__(self, j):
        self.j, self.calls = j, []

    def __call__(self, ctx, *, allow_unhalt=False, **_bound):
        self.calls.append((ctx.channel, ctx.principal, allow_unhalt))
        ControlStateMachine(self.j).set(ControlState.ACTIVE, "supervisor", "fixture")
        return OwnerRecoveryResult("ACTIVATED", "ACTIVE")


def _oc(tmp_path, ipc_dir, identities=None):
    from trader.owner.adapters.openclaw import OpenClawAdapter
    identities = identities or {"whatsapp": {"+15550000001": "owner"},
                                "openclaw": {"alice": "owner"}}
    cfg = {"owner_interface": {"identities": identities}}
    j = Journal(tmp_path / "ipc.db")
    j.kv_set("control_state", "FROZEN")
    s = OwnerService(j, ControlStateMachine(j), resume=_ActivatingResume(j),
                     authorizer=Authorizer.from_config(cfg))
    clients = {p: OwnerClient(ipc_dir, p) for p in ("whatsapp", "openclaw")}
    return j, s, OpenClawAdapter(Authorizer.from_config(cfg), clients)


def _msg(mid, text, sender="+15550000001", provider="whatsapp", ts=None):
    return {"provider": provider, "message_id": mid, "sender": sender, "text": text,
            "timestamp": ts if ts is not None else time.time()}


def _code(out):
    return out.text.split("CONFIRM ")[1].split()[0]


@pytest.mark.parametrize("provider, sender", [("whatsapp", "+15550000001"),
                                              ("openclaw", "alice")])
def test_r_whatsapp_openclaw_duplicate_messages_execute_once(tmp_path, ipc_dir, provider,
                                                             sender):
    j, s, a = _oc(tmp_path, ipc_dir)
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        m = lambda mid, text, **kw: _msg(mid, text, sender=sender, provider=provider, **kw)
        prop = a.handle(m("m1", "/halt"))
        assert prop.kind == "proposal" and prop.request is None
        assert j.kv_get("control_state") == "FROZEN"
        assert a.handle(m("m1", "/halt")).text == prop.text
        code = _code(prop)
        assert len(code) == 10
        done = a.handle(m("m2", f"CONFIRM {code}"))
        assert done.kind == "result" and done.text == "😴 HALTED."
        again = a.handle(m("m2", f"CONFIRM {code}"))
        also = a.handle(m("m3", f"confirm {code}"))
        assert again.request.request_id == done.request.request_id == also.request.request_id
        assert j.query("SELECT COUNT(*) n FROM control_events WHERE event='state_change'"
                       )[0]["n"] == 1
        assert a.handle(m("m4", "CONFIRM 0000000000")).kind == "refused"
        stranger = a.handle(_msg("m6", "/halt", sender="+19999", provider=provider))
        assert stranger.kind == "refused" and stranger.text is None
        assert a.handle(m("m7", "/status")).text.startswith("state=HALTED")
        for bad in ({}, {**m("x", "/halt"), "raw": {}}, {**m("x", "/halt"), "provider": "sms"},
                    {**m("x", "/halt"), "timestamp": "now"}):
            assert a.handle(bad).kind == "invalid"
    finally:
        server.stop()


def test_w1_code_collision_can_never_swap_the_confirmed_operation(tmp_path, ipc_dir):
    """Astra L: a displayed 'Confirm HALT' must never execute a resume."""
    j, s, a = _oc(tmp_path, ipc_dir)
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        a._code = lambda provider, sender, mid: "63d233aaaa"      # force a collision
        first = a.handle(_msg("m1664", "/resume", sender="alice", provider="openclaw"))
        second = a.handle(_msg("m1864", "/halt", sender="alice", provider="openclaw"))
        assert first.text.startswith("Confirm RESUME")
        assert second.kind == "refused" and "collision" in second.text
        out = a.handle(_msg("m9", "CONFIRM 63d233aaaa", sender="alice", provider="openclaw"))
        assert out.request.operation == "resume"                 # what was displayed
        assert s._resume.calls == [("openclaw", "owner", False)]
    finally:
        server.stop()


def test_w2_confirmation_is_bound_to_provider_sender_and_principal(tmp_path, ipc_dir):
    """Astra K/L: no cross-provider, cross-sender or re-bound confirmation."""
    idents = {"whatsapp": {"+15550000001": "owner", "+15550000002": "owner"},
              "openclaw": {"+15550000001": "owner"}}
    j, s, a = _oc(tmp_path, ipc_dir, idents)
    server = OwnerIPCServer(s, ipc_dir).start()
    try:
        code = _code(a.handle(_msg("m1", "/resume")))
        assert a.handle(_msg("m2", f"CONFIRM {code}", provider="openclaw")).kind == "refused"
        assert a.handle(_msg("m3", f"CONFIRM {code}", sender="+15550000002")).kind == "refused"
        a.authorizer.bind("whatsapp", "+15550000001", "local-operator")   # re-bound
        assert a.handle(_msg("m4", f"CONFIRM {code}")).kind == "refused"
        assert s._resume.calls == [] and j.kv_get("control_state") == "FROZEN"
    finally:
        server.stop()


def test_r2_confirmation_expires(tmp_path, ipc_dir):
    j, s, a = _oc(tmp_path, ipc_dir)
    now = [1000.0]
    a.clock = lambda: now[0]
    code = _code(a.handle(_msg("m1", "/freeze")))
    now[0] += a.confirm_ttl_s + 1
    late = a.handle(_msg("m2", f"CONFIRM {code}"))
    assert late.kind == "refused" and late.request is None
