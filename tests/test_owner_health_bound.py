"""Owner health read: bounded safety projection (offline, temp dirs, no Kernel).

The safety artifact can be ~700KB (market diagnostics in conditions' context and
heartbeats). The health answer must still fit one IPC frame, keep every safety
flag, and fail closed -- never truncate -- when the essential data is too large.
"""
import json
import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.owner_frontend_fixture import FakeChat, FakeGateway, TOKEN, make_app
from trader.core.journal import Journal
from trader.engine.state import ControlStateMachine
from trader.observability.safety import SCHEMA, SafetyHealth, health_path
from trader.owner.adapters import dashboard as owner_dashboard
from trader.owner.adapters.render import render
from trader.owner.contract import OwnerRequest, Status
from trader.owner.health_projection import OVERSIZE_REASON, WORST_NONCE, frame_size, response_fits
from trader.owner.ipc import (MAX_FRAME, SOCKET_NAME, WIRE_VERSION, OwnerClient, OwnerIPCServer,
                              _mac, load_key, read_frame, write_frame)
from trader.owner.service import OwnerService

BIG = {"blob": "x" * 200_000}


class Resume:
    def __call__(self, ctx, **_):
        raise AssertionError("health/freeze never runs recovery")


@pytest.fixture
def ipc_dir():
    # the IPC path check rejects group-writable ancestors; /tmp is sticky
    root = Path(tempfile.mkdtemp(prefix="oh-", dir="/tmp"))
    yield root / "ipc"
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def world(tmp_path, ipc_dir):
    j = Journal(tmp_path / "ipc.db")
    s = OwnerService(j, ControlStateMachine(j), resume=Resume())
    server = OwnerIPCServer(s, ipc_dir).start()
    yield j, s, server, ipc_dir
    server.stop()


def req(op, rid=None, identity="session"):
    return OwnerRequest(rid or f"dashboard-{op}-{time.time_ns()}", op, "dashboard", identity,
                        issued_at=time.time(), request_ref="r")


def artifact(j, **over):
    v = {"schema": SCHEMA, "revision": 7, "observed_at": 1.5, "recovery_required": True,
         "conditions": {
             "feed_stale": {"status": "ACTIVE", "reason": "no_bars", "incident_id": "abc",
                            "detected_at": 1.0, "context": {"record": BIG}},
             "old": {"status": "RESOLVED", "reason": "was", "context": BIG}},
         "heartbeats": {f"p{i}": {"record": BIG} for i in range(3)}}
    v.update(over)
    path = health_path(j)
    path.write_text(json.dumps(v))
    return path, v


def measured_size(ipc_dir, r, nonce):
    """Bytes of the response frame the server writes for ``nonce`` (real key and MAC)."""
    body = {"nonce": nonce, "result": r.to_wire()}
    return len(json.dumps({"v": WIRE_VERSION, **body,
                           "mac": _mac(load_key(ipc_dir, "dashboard"), body)},
                          default=str).encode("utf-8"))


def raw_call(ipc_dir, r, nonce):
    """Real IPC round trip with an arbitrary (server-valid) nonce; returns the answer frame."""
    body = {"channel": "dashboard", "nonce": nonce, "request": r.to_wire()}
    key = load_key(ipc_dir, "dashboard")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect(str(ipc_dir / SOCKET_NAME))
        write_frame(sock, {"v": WIRE_VERSION, **body, "mac": _mac(key, body)})
        return read_frame(sock)
    finally:
        sock.close()


def health(world):
    return OwnerClient(world[3], "dashboard").call(req("health"))


def test_oversized_artifact_still_answers_within_frame(world):
    j, s, _, ipc_dir = world
    path, _v = artifact(j)
    j.kv_set("control_state", "ACTIVE")
    assert path.stat().st_size > 600_000
    before = path.read_bytes()
    r = health(world)
    assert r.status == Status.ACCEPTED and r.data["control_state"] == "ACTIVE"
    res = s.execute(req("health"))
    std = measured_size(ipc_dir, res, "0" * 32)               # standard client: token_hex(16)
    ascii128 = measured_size(ipc_dir, res, "n" * 128)
    worst = measured_size(ipc_dir, res, WORST_NONCE)
    assert std == frame_size(res, "0" * 32) < ascii128 < worst == frame_size(res)
    assert worst + 1024 <= MAX_FRAME and response_fits(res)
    print(f"\nhealth frame bytes: standard-client={std} ascii-128-nonce={ascii128} "
          f"worst-case-envelope={worst} (limit {MAX_FRAME}), artifact bytes={len(before)}")
    assert path.read_bytes() == before                        # artifact untouched
    d = json.dumps(r.data)
    assert "x" * 100 not in d


def _supervisor(j, n):
    j.kv_set("supervisor_status", json.dumps(
        {"outcome": "SAFE", "stage": "x", "needs_owner": False, "reasons": ["é\U0001F600" * n]}))


def test_boundary_accepted_health_fits_with_worst_nonce_and_non_ascii(world):
    j, s, _, ipc_dir = world
    artifact(j)

    def accepted(n):
        _supervisor(j, n)
        return s.execute(req("health"))

    lo, hi = 1, 20_000                          # largest n still accepted (monotonic in n)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if accepted(mid).status == Status.ACCEPTED else (lo, mid - 1)
    res = accepted(lo)
    assert res.status == Status.ACCEPTED and response_fits(res)
    assert accepted(lo + 1).status == Status.REFUSED              # one more char: fail closed
    # the astral nonce really costs 12 bytes/char in the frame
    assert len(json.dumps(WORST_NONCE)) - 2 == 128 * 12
    worst = measured_size(ipc_dir, res, WORST_NONCE)
    assert worst == frame_size(res) <= MAX_FRAME - 1024
    _supervisor(j, lo)
    for nonce in (WORST_NONCE, "n" * 128, "0" * 32):
        frame = raw_call(ipc_dir, req("health"), nonce)            # server writes it; client reads it
        assert frame["nonce"] == nonce and frame["result"]["status"] == "ACCEPTED"
    print(f"\nboundary n={lo} worst-case-envelope={worst} "
          f"standard-client={measured_size(ipc_dir, res, '0' * 32)} limit={MAX_FRAME}")


def test_flags_and_reasons_retained(world):
    j, *_ = world
    _, v = artifact(j)
    sh = health(world).data["safety"]
    h = SafetyHealth(health_path(j))
    assert (sh["schema"], sh["revision"], sh["observed_at"], sh["recovery_required"]) == (SCHEMA, 7, 1.5, True)
    assert sh["entry_block"] == h.entry_block() == "critical_safety_requires_recovery"
    assert sh["active_reasons"] == h.active_reasons() == ["feed_stale:no_bars"]
    assert sh["bootstrap_block"] is False
    assert sh["conditions"]["feed_stale"] == {"status": "ACTIVE", "reason": "no_bars",
                                              "incident_id": "abc", "detected_at": 1.0}
    assert sh["conditions"]["old"]["status"] == "RESOLVED"
    assert "context" not in sh["conditions"]["feed_stale"] and "heartbeats" not in sh


@pytest.mark.parametrize("boot,blocked", [
    ({"status": "READY", "token": "SECRET-T"}, False),
    ({"status": "READY", "token": ""}, True),
    ({"status": "PENDING", "token": "SECRET-T"}, True),
    ("garbage", True)])
def test_bootstrap_blocking_semantics_and_token_never_sent(world, boot, blocked):
    j, *_ = world
    artifact(j, bootstrap=boot, recovery_required=False, conditions={})
    r = health(world)
    sh = r.data["safety"]
    h = SafetyHealth(health_path(j))
    assert sh["bootstrap_block"] is blocked is SafetyHealth.bootstrap_block(h.read())
    assert sh["entry_block"] == h.entry_block() == ("contained_bootstrap_not_ready" if blocked else None)
    assert sh["active_reasons"] == h.active_reasons()
    assert "SECRET-T" not in json.dumps(r.data)
    assert sh["bootstrap"]["token_present"] is (isinstance(boot, dict) and bool(boot["token"]))


@pytest.mark.parametrize("content", [None, "not json", json.dumps({"schema": "bad"})])
def test_invalid_or_missing_artifact_keeps_existing_semantics(world, content):
    j, *_ = world
    path = health_path(j)
    if content is not None:
        path.write_text(content)
    sh = health(world).data["safety"]
    if content is None:
        assert (sh["revision"], sh["recovery_required"], sh["entry_block"]) == (0, False, None)
    else:
        assert (sh["revision"], sh["recovery_required"]) == (-1, True)
        assert sh["active_reasons"] == ["safety_health_invalid:artifact_invalid"]
        assert sh["conditions"]["safety_health_invalid"]["reason"] == "artifact_invalid"
        assert sh["entry_block"] == "critical_safety_requires_recovery"
    if content is None:
        assert not path.exists()                              # the read never creates it


@pytest.mark.parametrize("state", ["ACTIVE", "RECOVERY"])
def test_freeze_eligible_from_active_and_recovery_with_blocked_safety(world, state):
    j, *_ = world
    artifact(j)                                               # entries/recovery blocked
    j.kv_set("control_state", state)
    h = health(world)
    assert h.status == Status.ACCEPTED and h.data["control_state"] == state
    assert h.data["safety"]["recovery_required"] is True      # blocked != unavailable
    f = OwnerClient(world[3], "dashboard").call(req("freeze"))
    assert f.status == Status.ACCEPTED and f.control_state_after == "FROZEN"
    assert j.kv_get("control_state") == "FROZEN"


def test_unbound_identity_still_refused(world):
    artifact(world[0])
    r = OwnerClient(world[3], "dashboard").call(req("health", identity="intruder"))
    assert r.status != Status.ACCEPTED and r.data is None


def test_oversized_reasons_fail_closed_not_truncated(world):
    j, s, _, _ = world
    path, _ = artifact(j, conditions={"c": {"status": "ACTIVE", "reason": "r" * 70_000}})
    r = health(world)
    assert r.status == Status.REFUSED and r.reasons == (OVERSIZE_REASON,) and r.data is None
    assert frame_size(s.execute(req("health"))) < 4096          # refusal envelope stays tiny
    big_name = {"n" * 70_000: {"status": "ACTIVE", "reason": "x"}}
    path.write_text(json.dumps({"schema": SCHEMA, "revision": 1, "recovery_required": True,
                                "conditions": big_name, "heartbeats": {}}))
    assert health(world).reasons == (OVERSIZE_REASON,)


def test_oversized_supervisor_fails_closed(world):
    j, *_ = world
    artifact(j)
    j.kv_set("supervisor_status", json.dumps({"outcome": "NEEDS_OWNER", "reasons": ["s" * 70_000]}))
    r = health(world)
    assert r.status == Status.REFUSED and r.reasons == (OVERSIZE_REASON,)
    j.kv_set("supervisor_status", json.dumps({"outcome": "SAFE", "stage": "x", "needs_owner": False,
                                              "reasons": [f"reason-{i}" for i in range(50)]}))
    ok = health(world)
    assert ok.status == Status.ACCEPTED and len(ok.data["supervisor"]["reasons"]) == 50
    assert ok.data["owner_interface"]["recovery_in_progress"] is False


def test_service_exception_is_unavailable_never_available(tmp_path, monkeypatch, world):
    j, _, _, ipc_dir = world
    artifact(j)

    def boom(self, request, principal):
        raise RuntimeError("service read failed")
    monkeypatch.setattr(OwnerService, "_read", boom)
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, _journal, _gw = make_app(tmp_path / "app", monkeypatch)
    client = OwnerClient(ipc_dir, "dashboard")

    def real(self, operation, request_id, issued_at_ms, request, args):
        return client.call(owner_dashboard.to_request(operation, request_id, issued_at_ms,
                                                      authenticated=True, args=args))
    monkeypatch.setattr(FakeGateway, "__call__", real)
    c = TestClient(app)
    out = c.get("/owner-api/v1/owner-interface", headers={"x-luffy-token": TOKEN}).json()
    assert out["availability"] == "UNAVAILABLE" and out["availability"] != "AVAILABLE"
    assert out["reasons"] == ["kernel_response_missing_outcome_unknown"]
    assert out["status"] != "ACCEPTED" and out["control_state"] is None
    assert out["supervisor"] is None and out["heartbeat_age_s"] is None
    assert c.get("/owner-api/v1/owner-interface").status_code == 401   # unauthenticated stays 401


def test_genuine_unavailability_is_unchanged(tmp_path):
    r = OwnerClient(tmp_path / "nope", "dashboard").call(req("health"))
    assert r.status == Status.UNAVAILABLE


def test_dashboard_endpoint_and_render_compat(tmp_path, monkeypatch, world):
    j, s, _, ipc_dir = world
    artifact(j)
    j.kv_set("control_state", "RECOVERY")
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, _journal, _gw = make_app(tmp_path / "app", monkeypatch)
    client = OwnerClient(ipc_dir, "dashboard")

    def real(self, operation, request_id, issued_at_ms, request, args):
        return client.call(owner_dashboard.to_request(operation, request_id, issued_at_ms,
                                                      authenticated=True, args=args))
    monkeypatch.setattr(FakeGateway, "__call__", real)
    c = TestClient(app)
    out = c.get("/owner-api/v1/owner-interface", headers={"x-luffy-token": TOKEN}).json()
    assert out["availability"] == "AVAILABLE" and out["status"] == "ACCEPTED"
    assert out["control_state"] == "RECOVERY" and out["recovery_in_progress"] is False
    assert set(out) >= {"supervisor", "heartbeat_age_s"}
    assert "state=RECOVERY" in render(health((j, s, None, ipc_dir)))
    j.kv_set("supervisor_status", json.dumps({"reasons": ["z" * 70_000]}))
    out = c.get("/owner-api/v1/owner-interface", headers={"x-luffy-token": TOKEN}).json()
    assert out["availability"] == "UNAVAILABLE" and out["reasons"] == [OVERSIZE_REASON]
