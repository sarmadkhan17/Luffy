"""Revision 5 — state-based adversarial tests for the Astra W1/W3 blockers and
the exact audit-buffer bound, each with the negative controls it must catch.
(W2, cross-tab ids, is exercised in real Chromium: test_owner_dashboard_browser.py.)

W1  An accepted panic supersedes every older recovery/activation attempt:
    acceptance applies an owner hold (advancing the control-intent fence), old
    recoveries stay bound to their earlier watermark, and the drain counts
    only *owner* intent after the acceptance hold as newer.
W3  Admission reserves the request id durably before anything can execute it;
    a duplicate is answered from the reservation / record, never with a
    definitive refusal because execution has not started yet.
AUD The audit buffer never exceeds its cap; in-flight rows are never
    discarded; every loss is counted.
"""
import json
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Thread as RealThread

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import OwnerRecoveryResult, Supervisor
from trader.owner.contract import Disposition, OwnerRequest, Status
from trader.owner.ipc import OwnerClient, OwnerIPCServer
from trader.owner.service import OwnerService, Reservation
from tests.test_kernel_boot_recovery import _transitions
from tests.test_owner_interface import _active, _audit, _bind_tester, _count, _state, req, svc
from tests.test_owner_interface_r3 import _drain_workers, _flatten_hook, _wait
from tests.test_owner_recovery import HookVenue
from tests.test_owner_recovery_risk_guard import tg_update


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def _frozen(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("freeze"))
    return journal, venue, k, s


def _activations_after(journal, event_id):
    return [r["id"] for r in journal.query(
        "SELECT id FROM control_events WHERE event='state_change' AND to_state='ACTIVE' "
        "AND id>?", (event_id,))]


def _drain(k, monkeypatch):
    _flatten_hook(monkeypatch, lambda: None)
    return k._drain_panic()


# ═══ W1 — panic supersedes older recovery ═══════════════════════════════════
def check_a_recovery_paused_then_panic(world, monkeypatch):
    """A. FROZEN → old resume pauses in recovery → panic accepted → recovery finishes."""
    journal, venue, k, s = _frozen(world, monkeypatch)
    in_pass, release, out = threading.Event(), threading.Event(), []

    def venue_check():
        in_pass.set()
        release.wait(5)
    venue.on_positions = venue_check
    t = RealThread(target=lambda: out.append(s.execute(req("resume"))))
    t.start()
    assert in_pass.wait(3)
    p = s.execute(req("panic"))
    assert p.status == Status.ACCEPTED
    release.set()
    t.join(10)
    assert out[0].status != Status.ACTIVATED
    assert _state(journal) != "ACTIVE" and _activations_after(journal, p.transition_event_id) == []
    _drain(k, monkeypatch)
    assert _state(journal) == "FROZEN"


def check_b_panic_just_before_cas(world, monkeypatch):
    """B. Panic accepted synchronously just before the recovery's final CAS."""
    journal, venue, k, s = _frozen(world, monkeypatch)
    real, accepted = k.supervisor._risk_now, []

    def risk_now():
        if not accepted:                  # the pass's pre-CAS Risk read
            accepted.append(s.execute(req("panic")))
        return real()
    monkeypatch.setattr(k.supervisor, "_risk_now", risk_now)
    r = s.execute(req("resume"))
    assert accepted and accepted[0].status == Status.ACCEPTED
    assert r.status != Status.ACTIVATED and _state(journal) == "FROZEN"
    _drain(k, monkeypatch)
    assert _state(journal) == "FROZEN"


def check_c_panic_after_proof_before_activation(world, monkeypatch):
    """C. Panic accepted while the verified Risk proof is held across the CAS:
    the fence orders it; whichever lands first, the end state is contained."""
    journal, venue, k, s = _frozen(world, monkeypatch)
    real_held = Supervisor._risk_held
    panics, threads = [], []

    @contextmanager
    def held(self, risk):
        t = RealThread(target=lambda: panics.append(s.execute(req("panic"))))
        t.start()                          # races the activation CAS for the fence
        threads.append(t)
        time.sleep(0.05)
        with real_held(self, risk) as out:
            yield out
    monkeypatch.setattr(Supervisor, "_risk_held", held)
    s.execute(req("resume"))
    assert threads, "the activation CAS guard was not reached"
    threads[0].join(10)
    assert panics[0].status == Status.ACCEPTED
    assert _state(journal) == "FROZEN"                  # contained after the panic
    assert _activations_after(journal, panics[0].transition_event_id) == []
    _drain(k, monkeypatch)
    assert _state(journal) == "FROZEN"


def check_d_old_completion_is_not_owner_intent(world, monkeypatch):
    """D. Panic accepted → an older recovery's completion event lands (system
    actor) → the drain must not treat it as newer owner intent."""
    journal, venue, k, s = _frozen(world, monkeypatch)
    s.execute(req("panic"))
    k.state_machine.set(ControlState.ACTIVE, "supervisor", "late completion of older work")
    _drain(k, monkeypatch)
    assert _state(journal) == "FROZEN"


def check_e_newer_owner_request_follows_normal_rules(world, monkeypatch):
    """E. A genuinely newer owner resume after the panic is honoured."""
    journal, venue, k, s = _frozen(world, monkeypatch)
    s.execute(req("panic"))
    r = s.execute(req("resume"))
    assert r.status == Status.ACTIVATED
    _drain(k, monkeypatch)                                 # the panic still flattens …
    assert _state(journal) == "ACTIVE"                     # … the newer owner intent stands
    s.execute(req("halt"))
    s.execute(req("panic"))                                # panic while HALTED
    _drain(k, monkeypatch)
    assert _state(journal) == "HALTED"                     # never de-escalated


W1_CHECKS = [check_a_recovery_paused_then_panic, check_b_panic_just_before_cas,
             check_c_panic_after_proof_before_activation,
             check_d_old_completion_is_not_owner_intent,
             check_e_newer_owner_request_follows_normal_rules]


@pytest.mark.parametrize("check", W1_CHECKS, ids=lambda f: f.__name__)
def test_w1(world, monkeypatch, check):
    check(world, monkeypatch)


def test_w1_panic_acceptance_is_an_owner_hold_bound_to_the_pending_panic(world, monkeypatch):
    journal, venue, k, s = _frozen(world, monkeypatch)
    p = s.execute(req("panic"))
    hold = journal.query("SELECT event, actor, from_state, to_state FROM control_events "
                         "WHERE id=?", (p.transition_event_id,))[0]
    assert (hold["event"], hold["actor"], hold["to_state"]) == ("state_hold", "operator",
                                                                "FROZEN")
    assert json.loads(journal.kv_get("panic_requested"))["intent_event_id"] == \
        p.transition_event_id


# negative controls ----------------------------------------------------------
def _nc_panic_fence_omitted(monkeypatch):
    """Revision 4: acceptance records the watermark but applies no hold."""
    def panic(self, r, principal):
        with self.state_machine.fenced() as f:
            state, watermark = f.state, f.watermark
            with self.journal._tx() as c:
                c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES "
                          "('panic_requested',?)",
                          (json.dumps({"request_id": r.request_id,
                                       "intent_event_id": watermark}),))
        from trader.owner.contract import OwnerResult
        return OwnerResult(r.request_id, r.operation, Status.ACCEPTED,
                           control_state_before=state.value, control_state_after=state.value,
                           transition_event_id=watermark)
    monkeypatch.setattr(OwnerService, "_panic", panic)


def _nc_completion_counted_as_intent(monkeypatch):
    """Any intent event after acceptance (system ones too) counts as newer."""
    import trader.engine.state as st
    monkeypatch.setattr(st, "OWNER_ACTORS", ("operator", "dashboard", "chat", "supervisor"))


def _nc_drain_wrong_identity(monkeypatch):
    """Drain compares against the watermark at drain start, not acceptance."""
    import trader.kernel as kmod
    from trader.engine.control_fence import latest_intent_event_id
    real = kmod.Kernel._drain_panic

    def drain(self):
        raw = self.journal.kv_get("panic_requested")
        if raw not in (None, "", "0"):
            self.journal.kv_set("panic_requested", json.dumps(
                {**json.loads(raw), "intent_event_id": latest_intent_event_id(self.journal)}))
        return real(self)
    monkeypatch.setattr(kmod.Kernel, "_drain_panic", drain)


@pytest.mark.parametrize("mutant, check", [
    (_nc_panic_fence_omitted, check_a_recovery_paused_then_panic),
    (_nc_panic_fence_omitted, check_b_panic_just_before_cas),
    (_nc_completion_counted_as_intent, check_d_old_completion_is_not_owner_intent),
    (_nc_drain_wrong_identity, check_e_newer_owner_request_follows_normal_rules),
], ids=lambda x: getattr(x, "__name__", str(x)))
def test_negative_control_w1(world, monkeypatch, mutant, check):
    mutant(monkeypatch)
    with pytest.raises(AssertionError):
        check(world, monkeypatch)


# ═══ W3 — reservation before execution ══════════════════════════════════════
class _Slow:
    def __init__(self, delay):
        self.delay, self.calls = delay, 0

    def __call__(self, ctx, *, allow_unhalt=False, **_bound):
        self.calls += 1
        time.sleep(self.delay)
        return OwnerRecoveryResult("ACTIVATED", "ACTIVE")


def _svc(tmp_path, resume=None, **kw):
    j = Journal(tmp_path / "w3.db")
    s = OwnerService(j, ControlStateMachine(j), resume=resume or _Slow(0), **kw)
    _bind_tester(s.authorizer)
    return j, s


def check_ipc_duplicate_before_claim(tmp_path, monkeypatch):
    """The original is reserved and paused before its durable claim; a
    duplicate arrives: IN_PROGRESS, never REFUSED; exactly one execution."""
    resume = _Slow(0)
    j, s = _svc(tmp_path, resume)
    real_claim, gate, paused = s._claim, threading.Event(), threading.Event()

    def claim(r, res, now):
        paused.set()
        gate.wait(5)
        return real_claim(r, res, now)
    monkeypatch.setattr(s, "_claim", claim)
    ipc_dir = Path(tempfile.mkdtemp(prefix="oi-")) / "ipc"
    server = OwnerIPCServer(s, ipc_dir).start()
    client = OwnerClient(ipc_dir, "dashboard")
    R = lambda: OwnerRequest("dashboard-dup-claim-1", "resume", "dashboard", "session",
                             issued_at=time.time())
    try:
        first = []
        t = RealThread(target=lambda: first.append(client.call(R())))
        t.start()
        assert paused.wait(3)
        dup = client.call(R())
        assert dup.status == Status.IN_PROGRESS and dup.disposition == Disposition.IN_PROGRESS
        gate.set()
        t.join(10)
        assert first[0].status == Status.ACTIVATED and resume.calls == 1
        final = client.call(R())
        assert final.replayed and final.disposition == Disposition.COMPLETED
        assert resume.calls == 1
    finally:
        gate.set()
        server.stop()


def check_telegram_duplicate_before_execution(world, monkeypatch):
    """A redelivered update while the original is reserved but not yet executing."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    monkeypatch.setattr("trader.kernel.threading.Thread", RealThread)
    s = svc(k)
    s.execute(req("halt"))
    k.notifier.chat_id = "1"
    replies = []
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    real_execute, gate, paused = s.execute, threading.Event(), threading.Event()

    def execute(r, **kw):
        if r.operation == "unhalt":
            paused.set()
            gate.wait(5)
        return real_execute(r, **kw)
    monkeypatch.setattr(s, "execute", execute)
    upd = tg_update()
    k._tg_dispatch("/unhalt", "https://x", upd)
    assert paused.wait(3)
    k._tg_dispatch("/unhalt", "https://x", upd)          # redelivery of the same update
    assert _wait(lambda: any("still running" in r for r in replies))
    assert not any("refused" in r for r in replies)
    gate.set()
    assert _wait(lambda: _state(journal) == "ACTIVE")
    _drain_workers(k)
    k._tg_workers = {}
    assert len(_count(journal, "owner_recovery_requested")) == 1
    k._tg_dispatch("/unhalt", "https://x", upd)          # after the final result
    _drain_workers(k)
    assert replies[-1] == "🙂 ACTIVE — fresh recovery check proved safe."


def test_w3_ipc_duplicate_before_claim(tmp_path, monkeypatch):
    check_ipc_duplicate_before_claim(tmp_path, monkeypatch)


def test_w3_telegram_duplicate_before_execution(world, monkeypatch):
    check_telegram_duplicate_before_execution(world, monkeypatch)


def test_w3_service_busy_while_same_id_reserved(tmp_path):
    j, s = _svc(tmp_path, busy_wait_s=2.0)
    s._fast_lock.acquire()                               # another containment running
    out = []
    t = RealThread(target=lambda: out.append(s.execute(req("halt", "test-busy-reserved"))))
    t.start()
    assert _wait(lambda: s._row("test-busy-reserved") is not None)
    dup = s.execute(req("halt", "test-busy-reserved"))
    assert dup.status == Status.IN_PROGRESS               # not "busy", not refused
    s._fast_lock.release()
    t.join(5)
    assert out[0].status == Status.ACCEPTED and j.kv_get("control_state") == "HALTED"


def test_w3_restart_after_reservation_before_execution(tmp_path):
    j, s = _svc(tmp_path)
    r = req("halt", "test-reserved-crash")
    assert isinstance(s.reserve(r), Reservation)           # reserved, never claimed …
    s2 = OwnerService(j, ControlStateMachine(j), resume=None)   # … the kernel restarts
    _bind_tester(s2.authorizer)
    out = s2.execute(r)
    assert out.status == Status.REFUSED and out.reasons == ("abandoned_before_execution",)
    assert j.kv_get("control_state") is None             # provably never executed
    again = s2.execute(r)
    assert again.replayed and again.reasons == ("abandoned_before_execution",)
    assert j.kv_get("control_state") is None


def test_w3_restart_after_claim_is_outcome_unknown(tmp_path):
    j, s = _svc(tmp_path)
    r = req("halt", "test-claimed-crash")
    s._claim(r, s.reserve(r), time.time())
    s2 = OwnerService(j, ControlStateMachine(j), resume=None)
    _bind_tester(s2.authorizer)
    out = s2.execute(r)
    assert out.status == Status.OUTCOME_UNKNOWN and out.disposition == "OUTCOME_UNKNOWN"


def test_w3_timeout_after_reservation(tmp_path):
    resume = _Slow(0.8)
    j, s = _svc(tmp_path, resume)
    ipc_dir = Path(tempfile.mkdtemp(prefix="oi-")) / "ipc"
    server = OwnerIPCServer(s, ipc_dir).start()
    R = lambda: OwnerRequest("dashboard-timeout-rsv", "resume", "dashboard", "session",
                             issued_at=time.time())
    try:
        r = OwnerClient(ipc_dir, "dashboard", control_timeout_s=0.2).call(R())
        assert r.status == Status.OUTCOME_UNKNOWN
        assert OwnerClient(ipc_dir, "dashboard").call(R()).status == Status.IN_PROGRESS
        time.sleep(1.0)
        done = OwnerClient(ipc_dir, "dashboard").call(R())
        assert done.replayed and done.status == Status.ACTIVATED and resume.calls == 1
    finally:
        server.stop()


def test_w3_reservation_failure_never_executes(tmp_path, monkeypatch):
    """This delivery never runs; another delivery cannot be excluded (R6)."""
    j, s = _svc(tmp_path)

    @contextmanager
    def broken():
        raise OSError("journal locked")
        yield
    monkeypatch.setattr(j, "_tx", broken)
    r = s.execute(req("halt"))
    assert r.status == Status.OUTCOME_UNKNOWN and r.reasons == ("persistence_unavailable",)
    monkeypatch.undo()
    assert j.kv_get("control_state") is None


def test_w3_refusals_before_reservation_stay_definitive(tmp_path):
    j, s = _svc(tmp_path)
    assert s.execute(req("halt", identity="mallory")).disposition == Disposition.REFUSED
    stale = s.execute(req("halt", "test-stale-r5-001", issued_at=time.time() - 3600))
    assert stale.disposition == Disposition.REFUSED
    assert s.execute(req("halt", "test-stale-r5-001",
                         issued_at=time.time() - 3600)).replayed   # recorded
    assert j.kv_get("control_state") is None


# negative controls ----------------------------------------------------------
def _nc_no_reservation(monkeypatch):
    """Revision 4: nothing is reserved at admission; the row appears only at claim."""
    def reserve(self, r, *, admitted_intent_event_id=None):
        principal, denial = self.authorizer.authorize(r.channel, r.identity, r.operation)
        prior = self._existing(r, principal)
        if prior is not None:
            return prior
        return Reservation(r.request_id, "no-row", principal, admitted_intent_event_id)
    real_claim = OwnerService._claim

    def claim(self, r, res, now):
        with self.journal._tx() as c:
            c.execute("INSERT OR IGNORE INTO owner_requests(request_id, fingerprint, operation,"
                      " channel, identity, principal, state, boot_id, reserver, created_at)"
                      " VALUES (?,?,?,?,?,?,'RESERVED',?,?,?)",
                      (r.request_id, r.fingerprint(), r.operation, r.channel, r.identity,
                       res.principal, self.boot_id, res.token, "t"))
        return real_claim(self, r, res, now)
    monkeypatch.setattr(OwnerService, "reserve", reserve)
    monkeypatch.setattr(OwnerService, "_claim", claim)


def _nc_duplicate_refused(monkeypatch):
    """A duplicate that sees an unclaimed reservation is refused definitively."""
    from trader.owner.contract import refused
    real = OwnerService._existing

    def existing(self, r, principal):
        row = self._row(r.request_id)
        if row is not None and row["state"] == "RESERVED":
            return refused(r, "owner_interface_busy", principal=principal)
        return real(self, r, principal)
    monkeypatch.setattr(OwnerService, "_existing", existing)


@pytest.mark.parametrize("mutant", [_nc_no_reservation, _nc_duplicate_refused],
                         ids=lambda f: f.__name__)
def test_negative_control_w3_ipc(tmp_path, monkeypatch, mutant):
    mutant(monkeypatch)
    with pytest.raises(AssertionError):
        check_ipc_duplicate_before_claim(tmp_path, monkeypatch)


@pytest.mark.parametrize("mutant", [_nc_no_reservation, _nc_duplicate_refused],
                         ids=lambda f: f.__name__)
def test_negative_control_w3_telegram(world, monkeypatch, mutant):
    mutant(monkeypatch)
    with pytest.raises(AssertionError):
        check_telegram_duplicate_before_execution(world, monkeypatch)


# ═══ AUD — exact audit-buffer bound ═════════════════════════════════════════
CAP = 5


def _abuf(tmp_path):
    j, s = _svc(tmp_path)
    s.audit_buffer_max, s.audit_flush_rows, s.audit_flush_s = CAP, 10 ** 6, 10 ** 6
    return j, s


def _read(s, i):
    s.execute(req("status", f"test-aud-{i:06d}"))
    assert len(s._audit_buffer) <= CAP                     # the invariant, after every append


def _persisted_and_dropped(j):
    rows = _audit(j)
    persisted = {r["request_id"] for r in rows if r["request_id"]}
    dropped = sum(int(json.loads(r["reasons"])[0].rsplit(":", 1)[1]) for r in rows
                  if r["request_id"] is None and json.loads(r["reasons"])[0]
                  .startswith("audit_buffer_overflow_dropped"))
    return persisted, dropped


@contextmanager
def _paused_flush(j, monkeypatch):
    real_tx, gate, entered = j._tx, threading.Event(), threading.Event()

    @contextmanager
    def paused():
        with real_tx() as c:
            yield c
            entered.set()
            gate.wait(5)
    monkeypatch.setattr(j, "_tx", paused)
    yield gate, entered, real_tx


def test_aud_cap_minus_one_and_cap_hold_everything(tmp_path):
    j, s = _abuf(tmp_path)
    for i in range(CAP - 1):
        _read(s, i)
    assert len(s._audit_buffer) == CAP - 1 and s._audit_dropped == 0
    _read(s, CAP - 1)
    assert len(s._audit_buffer) == CAP and s._audit_dropped == 0
    _read(s, CAP)                                          # one past the cap: oldest evicted
    assert len(s._audit_buffer) == CAP and s._audit_dropped == 1
    assert s.flush_audit()
    persisted, dropped = _persisted_and_dropped(j)
    assert len(persisted) + dropped == CAP + 1 and dropped == 1


def check_all_in_flight(tmp_path, monkeypatch):
    j, s = _abuf(tmp_path)
    for i in range(CAP):
        _read(s, i)
    with _paused_flush(j, monkeypatch) as (gate, entered, real_tx):
        t = RealThread(target=s.flush_audit)
        t.start()
        assert entered.wait(3)                             # all CAP rows in flight
        monkeypatch.setattr(j, "_tx", real_tx)
        for i in range(CAP, CAP + 3):
            _read(s, i)                                    # no room: new rows dropped, counted
        assert len(s._audit_buffer) == CAP
        gate.set()
        t.join(5)
    assert s.flush_audit()
    persisted, dropped = _persisted_and_dropped(j)
    assert persisted == {f"test-aud-{i:06d}" for i in range(CAP)}   # in-flight all kept
    assert dropped == 3 and len(persisted) + dropped == CAP + 3


def test_aud_cap_with_all_rows_in_flight(tmp_path, monkeypatch):
    check_all_in_flight(tmp_path, monkeypatch)


def test_aud_concurrent_append_and_flush_failure(tmp_path, monkeypatch):
    j, s = _abuf(tmp_path)
    for i in range(3):
        _read(s, i)
    real_tx, gate, entered = j._tx, threading.Event(), threading.Event()

    @contextmanager
    def failing():
        with real_tx() as c:
            yield c
            entered.set()
            gate.wait(5)
            raise OSError("commit failed")                 # rolled back
    monkeypatch.setattr(j, "_tx", failing)
    t = RealThread(target=s.flush_audit)
    t.start()
    assert entered.wait(3)
    monkeypatch.setattr(j, "_tx", real_tx)
    for i in range(3, 9):
        _read(s, i)                                        # 6 more while 3 are in flight
    gate.set()
    t.join(5)
    assert len(s._audit_buffer) <= CAP
    assert s.flush_audit()
    persisted, dropped = _persisted_and_dropped(j)
    assert persisted <= {f"test-aud-{i:06d}" for i in range(9)}
    assert len(persisted) + dropped == 9                   # every row persisted or counted
    assert {f"test-aud-{i:06d}" for i in range(3)} <= persisted   # in-flight rows restored


def _nc_cap_plus_one(monkeypatch):
    """Revision 4: with every row in flight, the new row is still appended."""
    real = OwnerService._audit_row

    def audit_row(self, r, principal, result, *, buffered=False):
        if buffered:
            with self._audit_lock:
                if len(self._audit_buffer) >= self.audit_buffer_max:
                    for i, (seq, _v) in enumerate(self._audit_buffer):
                        if seq not in self._audit_inflight:
                            del self._audit_buffer[i]
                            self._audit_dropped += 1
                            break
                self._audit_seq += 1
                self._audit_buffer.append((self._audit_seq,
                                           self._audit_values(r, principal, result)))
            return
        return real(self, r, principal, result)
    monkeypatch.setattr(OwnerService, "_audit_row", audit_row)


def test_negative_control_aud_cap_plus_one(tmp_path, monkeypatch):
    _nc_cap_plus_one(monkeypatch)
    with pytest.raises(AssertionError):
        check_all_in_flight(tmp_path, monkeypatch)
