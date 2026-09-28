"""Revision 4: the exact counterexamples from the third Astra review (W1–W5),
each paired with a mutant that restores the revision-3 behaviour.

W1  An admitted-but-not-started recovery is bound to the control intent at
    admission: a newer hold supersedes it (checked in the Supervisor intake).
W2  Panic ordering starts at acceptance: PANIC → HALT → drain keeps HALTED.
W3  Pending browser ids are shared across tabs; a stale tab never erases
    another tab's unresolved request.
W4  Admission refusal consults the idempotency record: running → PENDING,
    completed → replay; only an unknown request is definitively refused.
W5  Overflow counts only rows actually discarded, never rows in a
    committing batch.
"""
import json
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Thread as RealThread

import pytest

from trader.core.journal import Journal
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import OwnerContext, OwnerRecoveryResult
from trader.owner.contract import OwnerRequest, Status
from trader.owner.ipc import OwnerClient, OwnerIPCServer
from trader.owner.service import OwnerService
from tests.test_owner_interface import (_active, _audit, _count, _state, _bind_tester,
                                        req, svc)
from tests.test_owner_interface_r3 import _drain_workers, _flatten_hook, _wait
from tests.test_owner_recovery import HookVenue
from tests.test_owner_recovery_risk_guard import tg_update

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


# ═══ W1 — admission bound to the control intent ═════════════════════════════
def check_admitted_unhalt_superseded_by_newer_halt(world, monkeypatch):
    """Astra W1: admit UNHALT → pause before OwnerService → newer HALT → release."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    monkeypatch.setattr("trader.kernel.threading.Thread", RealThread)
    svc(k).execute(req("halt"))
    k.notifier.chat_id = "1"
    replies = []
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    s = svc(k)
    real_execute, paused, go = s.execute, threading.Event(), threading.Event()

    def execute(r, **kw):
        if r.operation == "unhalt":
            paused.set()
            go.wait(5)                 # admitted + reserved, not yet executing
        return real_execute(r, **kw)
    monkeypatch.setattr(s, "execute", execute)
    k._tg_dispatch("/unhalt", "https://x", tg_update())
    assert paused.wait(3)
    k._tg_dispatch("/halt", "https://x", tg_update())     # newer hold (same-state owner hold)
    assert _wait(lambda: any(r == "😴 HALTED." for r in replies))
    go.set()
    assert _wait(lambda: len(_count(journal, "owner_recovery_result")) >= 1)
    _drain_workers(k)
    assert _state(journal) == "HALTED"                      # the newer hold is kept
    assert any("superseded_by_newer_intent" in r for r in replies)
    res = _count(journal, "owner_recovery_result")[-1]
    assert res["status"] == "REFUSED" and res["reasons"] == ["superseded_by_newer_intent"]
    assert venue.mutations == []


def test_w1_admitted_unhalt_superseded_by_newer_halt(world, monkeypatch):
    check_admitted_unhalt_superseded_by_newer_halt(world, monkeypatch)


def test_w1_supervisor_binding_futures_and_spot(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    svc(k).execute(req("halt"))
    stale = svc(k).admission_watermark()
    svc(k).execute(req("halt"))                             # a newer owner hold
    ctx = OwnerContext("operator", "test", principal="tester", request_ref="r")
    for entry in (k.supervisor.request_owner_recovery, k.supervisor.request_owner_release):
        r = entry(ctx, allow_unhalt=True, expected_intent_event_id=stale)
        assert (r.status, r.reasons) == ("REFUSED", ("superseded_by_newer_intent",))
    assert _state(journal) == "HALTED" and venue.mutations == []
    fresh = svc(k).admission_watermark()                    # not superseded: proceeds
    r = k.supervisor.request_owner_recovery(ctx, allow_unhalt=True,
                                            expected_intent_event_id=fresh)
    assert r.status == "ACTIVATED"


def test_w1_service_binds_at_arrival_by_default(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("halt"))
    stale = s.admission_watermark()
    s.execute(req("halt"))
    r = s.execute(req("unhalt"), admitted_intent_event_id=stale)
    assert r.status == Status.REFUSED and r.reasons == ("superseded_by_newer_intent",)
    assert s.execute(req("unhalt")).status == Status.ACTIVATED   # bound at arrival


def _rev3_unbound_admission(monkeypatch):
    """Revision 3: no admission binding — the recovery reaches the Supervisor
    without the watermark it was admitted under."""
    real = OwnerService._recover

    def unbound(self, r, principal, *, allow_unhalt):
        self._recovery_watermark = None
        return real(self, r, principal, allow_unhalt=allow_unhalt)
    monkeypatch.setattr(OwnerService, "_recover", unbound)


def test_negative_control_w1_unbound_admission_is_caught(world, monkeypatch):
    _rev3_unbound_admission(monkeypatch)
    with pytest.raises(AssertionError):
        check_admitted_unhalt_superseded_by_newer_halt(world, monkeypatch)


# ═══ W2 — panic ordering from acceptance ════════════════════════════════════
def check_halt_after_panic_acceptance_is_kept(world, monkeypatch):
    """Astra W2: accept PANIC → accept HALT → run _drain_panic (no concurrency)."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("panic"))
    s.execute(req("halt"))
    _flatten_hook(monkeypatch, lambda: None)
    assert k._drain_panic() == 1                            # the panic still flattens
    assert _state(journal) == "HALTED"                      # … but never overwrites HALT
    assert journal.kv_get("panic_requested") == "0"


def test_w2_halt_after_panic_acceptance_is_kept(world, monkeypatch):
    check_halt_after_panic_acceptance_is_kept(world, monkeypatch)


def test_w2_panic_without_newer_intent_freezes(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    svc(k).execute(req("panic"))
    _flatten_hook(monkeypatch, lambda: None)
    k._drain_panic()
    assert _state(journal) == "FROZEN"


def _rev3_drain_start_watermark(monkeypatch):
    from trader.core.types import ControlState
    from trader.engine.control_fence import latest_intent_event_id
    from trader.kernel import Kernel
    import trader.kernel as kmod

    def drain(self):
        raw = self.journal.kv_get("panic_requested")
        if raw in (None, "", "0"):
            return None
        watermark = latest_intent_event_id(self.journal)       # at drain start
        n = kmod.flatten_all(self.exchange, self.journal, self.notifier)
        with self.journal._tx() as c:
            c.execute("UPDATE state_kv SET value='0' WHERE key='panic_requested' AND value=?",
                      (raw,))
        with self.state_machine.fenced() as f:
            if f.watermark == watermark:
                f.apply(ControlState.FROZEN, "operator", "panic")
        return n
    monkeypatch.setattr(Kernel, "_drain_panic", drain)


def test_negative_control_w2_drain_start_watermark_is_caught(world, monkeypatch):
    _rev3_drain_start_watermark(monkeypatch)
    with pytest.raises(AssertionError):
        check_halt_after_panic_acceptance_is_kept(world, monkeypatch)


# ═══ W4 — replay-aware admission refusal ════════════════════════════════════
class _SlowResume:
    def __init__(self, delay):
        self.delay, self.calls = delay, 0

    def __call__(self, ctx, *, allow_unhalt=False, **_bound):
        self.calls += 1
        time.sleep(self.delay)
        return OwnerRecoveryResult("ACTIVATED", "ACTIVE")


def check_admission_refusal_consults_the_record(tmp_path, monkeypatch):
    j = Journal(tmp_path / "w4.db")
    resume = _SlowResume(0.8)
    s = OwnerService(j, ControlStateMachine(j), resume=resume)
    ipc_dir = Path(tempfile.mkdtemp(prefix="oi-")) / "ipc"
    # one recovery slot, held by the executing request: every other recovery
    # delivery reaches the class-slot admission refusal path
    server = OwnerIPCServer(s, ipc_dir, class_slots={"recovery": 1}).start()
    client = OwnerClient(ipc_dir, "dashboard")
    R = lambda rid: OwnerRequest(rid, "resume", "dashboard", "session", issued_at=time.time())
    try:
        resume.delay = 0.0
        assert client.call(R("dashboard-completed-x1")).status == Status.ACTIVATED
        resume.delay = 0.8
        out = []
        t = RealThread(target=lambda: out.append(client.call(R("dashboard-running-y1"))))
        t.start()
        time.sleep(0.2)                                    # the recovery slot is occupied
        dup = client.call(R("dashboard-running-y1"))      # same request, class full
        assert dup.status == Status.IN_PROGRESS and dup.reasons == ("request_in_progress",)
        again = client.call(R("dashboard-completed-x1"))  # completed request, class full
        assert again.replayed and again.status == Status.ACTIVATED
        fresh = client.call(R("dashboard-unknown-z1"))    # never seen: definitive, recorded
        assert fresh.status == Status.REFUSED and fresh.reasons == ("owner_interface_busy",)
        t.join(10)
        assert resume.calls == 2 and out[0].status == Status.ACTIVATED
        assert client.call(R("dashboard-running-y1")).replayed
        assert client.call(R("dashboard-unknown-z1")).replayed    # stays refused, never runs
        assert resume.calls == 2
    finally:
        server.stop()


def test_w4_admission_refusal_consults_the_record(tmp_path, monkeypatch):
    check_admission_refusal_consults_the_record(tmp_path, monkeypatch)


def _rev3_blind_admission_refusal(monkeypatch):
    from trader.owner.contract import refused

    def blind(self, r, reason):
        principal, _ = self.authorizer.authorize(r.channel, r.identity, r.operation)
        result = refused(r, reason, principal=principal)
        self._audit_row(r, principal, result)
        return result
    monkeypatch.setattr(OwnerService, "record_admission_refusal", blind)


def test_negative_control_w4_blind_refusal_is_caught(tmp_path, monkeypatch):
    _rev3_blind_admission_refusal(monkeypatch)
    with pytest.raises(AssertionError):
        check_admission_refusal_consults_the_record(tmp_path, monkeypatch)


def test_w4_refused_sender_never_sees_a_recorded_result(tmp_path):
    j = Journal(tmp_path / "w4b.db")
    s = OwnerService(j, ControlStateMachine(j), resume=None)
    _bind_tester(s.authorizer)
    s.execute(req("halt", "test-secret-result"))
    r = s.record_admission_refusal(req("halt", "test-secret-result", identity="mallory"),
                                   "owner_interface_busy")
    assert r.status == Status.REFUSED and r.reasons == ("identity_not_bound",)
    assert not r.replayed


# ═══ W5 — truthful overflow accounting ══════════════════════════════════════
def check_overflow_counts_only_discarded_rows(tmp_path, monkeypatch):
    """Astra W5: pause a flush before commit, overflow the buffer, finish both."""
    j = Journal(tmp_path / "w5.db")
    s = OwnerService(j, ControlStateMachine(j), resume=None)
    _bind_tester(s.authorizer)
    s.audit_buffer_max, s.audit_flush_rows, s.audit_flush_s = 5, 10 ** 6, 10 ** 6
    emitted = [f"test-ovf-{i:06d}" for i in range(8)]
    for rid in emitted[:3]:
        s.execute(req("status", rid))
    real_tx, gate, entered = j._tx, threading.Event(), threading.Event()

    @contextmanager
    def paused_tx():
        with real_tx() as c:
            yield c
            entered.set()
            gate.wait(5)                                   # rows written, not committed
    monkeypatch.setattr(j, "_tx", paused_tx)
    t = RealThread(target=s.flush_audit)
    t.start()
    assert entered.wait(3)
    monkeypatch.setattr(j, "_tx", real_tx)
    for rid in emitted[3:]:
        s.execute(req("status", rid))                      # overflows while in flight
    gate.set()
    t.join(5)
    assert s.flush_audit() is True
    rows = _audit(j)
    persisted = {r["request_id"] for r in rows if r["request_id"]}
    dropped = sum(int(json.loads(r["reasons"])[0].split(":")[1]) for r in rows
                  if r["request_id"] is None
                  and json.loads(r["reasons"])[0].startswith("audit_buffer_overflow_dropped"))
    assert persisted <= set(emitted)
    assert len(persisted) + dropped == len(emitted)        # every row persisted or counted
    assert dropped == len(set(emitted) - persisted)       # … and only real losses counted
    assert set(emitted[:3]) <= persisted                   # the in-flight batch survived


def test_w5_overflow_counts_only_discarded_rows(tmp_path, monkeypatch):
    check_overflow_counts_only_discarded_rows(tmp_path, monkeypatch)


def _rev3_overflow_evicts_inflight(monkeypatch):
    from trader.owner import service as svc_mod
    real = svc_mod.OwnerService._audit_row

    def audit_row(self, req_, principal, result, *, buffered=False):
        if buffered:
            with self._audit_lock:
                if len(self._audit_buffer) >= self.audit_buffer_max:
                    self._audit_buffer.pop(0)              # may be in a committing batch
                    self._audit_dropped += 1
                self._audit_seq += 1
                self._audit_buffer.append((self._audit_seq,
                                           self._audit_values(req_, principal, result)))
            return
        return real(self, req_, principal, result)
    monkeypatch.setattr(svc_mod.OwnerService, "_audit_row", audit_row)


def test_negative_control_w5_inflight_eviction_is_caught(tmp_path, monkeypatch):
    _rev3_overflow_evicts_inflight(monkeypatch)
    with pytest.raises(AssertionError):
        check_overflow_counts_only_discarded_rows(tmp_path, monkeypatch)


# ═══ W3 — cross-tab pending ids (real JS, two isolated tab contexts) ════════
NODE = shutil.which("node") or shutil.which("nodejs")
HTML = (ROOT / "trader" / "dashboard" / "web" / "index.html").read_text()
OWNER_BLOCK = HTML[HTML.index("/* Owner controls: typed requests executed by the kernel"):
                   HTML.index("/* ── refresh ── */")]

CROSS_TAB = r"""
const vm = require('vm');
const shared = {};
const localStorage = {getItem: k => (k in shared ? shared[k] : null),
                           setItem: (k, v) => { shared[k] = String(v); },
                           removeItem: k => { delete shared[k]; },
                           key: i => Object.keys(shared)[i] ?? null,
                           get length() { return Object.keys(shared).length; }};
function pendingMap() { const m = {};
  for (const k of Object.keys(shared)) if (k.startsWith('luffy.owner.req.')) {
    const e = JSON.parse(shared[k]); m[e.action] = e; }
  return m; }
const cryptoObj = globalThis.crypto || require('crypto').webcrypto;
function tab(name) {                      // one browser tab: its own globals, shared storage
  const t = {name, calls: [], toasts: [], answer: null, allow: true};
  const ctx = {localStorage, crypto: cryptoObj, console, setTimeout,
               confirm: () => t.allow, toast: m => t.toasts.push(m), refresh: () => {},
               document: {querySelectorAll: () => []},
               gql: (q, v) => { t.calls.push({r: v.r, op: v.o || 'panic'}); return t.answer(); }};
  vm.createContext(ctx);
  vm.runInContext(OWNER_BLOCK, ctx);
  t.ctx = ctx;
  return t;
}
const ok = s => Promise.resolve({res: {status: s, message: s, replayed: false, reasons: []}});
(async () => {
  const B = tab('B');
  B.allow = false; await B.ctx.ownerControl('halt');       // click + cancel: initializes B
  const A = tab('A');
  A.answer = () => Promise.reject(new Error('network'));
  await A.ctx.ownerControl('panic');                       // outcome unknown: id A stored
  const idA = A.calls[0].r;
  const afterA = pendingMap();
  B.allow = true; B.answer = () => ok('ACCEPTED');
  await B.ctx.ownerControl('halt');                        // B completes HALT
  const afterB = pendingMap();
  const A2 = tab('A-reloaded'); A2.allow = false;          // reload; must not need consent
  A2.answer = () => ok('ACCEPTED');
  await A2.ctx.ownerControl('panic');
  // IN_PROGRESS is non-definitive: the id must survive it
  const C = tab('C'); C.answer = () => ok('IN_PROGRESS');
  await C.ctx.ownerControl('freeze');
  const afterPending = pendingMap();
  console.log(JSON.stringify({idA, afterA, afterB, retry: A2.calls.map(c => c.r),
                              afterPending, freezeId: C.calls[0].r}));
})();
"""


def _run_cross_tab(block):
    script = CROSS_TAB.replace("OWNER_BLOCK", json.dumps(block))
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def check_other_tab_never_erases_unresolved_id(block):
    out = _run_cross_tab(block)
    assert out["afterA"]["panic"]["id"] == out["idA"]
    assert out["afterB"].get("panic", {}).get("id") == out["idA"], out   # B kept A's entry
    assert out["retry"] == [out["idA"]], out                           # reload reuses id A
    assert out["afterPending"]["freeze"]["id"] == out["freezeId"]      # PENDING keeps the id
    return out


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_w3_other_tab_never_erases_unresolved_id():
    check_other_tab_never_erases_unresolved_id(OWNER_BLOCK)


# The revision-4 "stale per-tab cached read" mutant no longer applies: with one
# storage key per request no tab ever writes another tab's entries. The
# concurrent-interleaving negative controls (revision-4 whole-map block, stale
# tab, sibling erasure, simultaneous creation) are real-browser tests in
# tests/test_owner_dashboard_browser.py.
