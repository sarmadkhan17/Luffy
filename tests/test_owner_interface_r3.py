"""Revision 3: executable regressions for the exact interleavings in the second
Astra review (W1–W4), each paired with the mutant it must catch.

W1  Telegram: a queued recovery must never run after a newer containment, and
    a blocked reply / informational command must never delay containment.
W2  Browser: a late answer to an older attempt must never delete a newer
    unresolved request id; storage failure must not produce a fresh id.
W3  Panic: a panic accepted during a flatten stays pending; a HALT during a
    flatten is never overwritten by the older panic's FROZEN.
W4  Audit: a failed / rolled-back flush loses no buffered row; admission
    refusals (IPC class-slot, Telegram recovery admission) are audited.
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
from trader.owner.contract import OwnerRequest, Status, new_request_id
from trader.owner.ipc import OwnerClient, OwnerIPCServer
from trader.owner.service import OwnerService
from tests.test_owner_interface import (_active, _audit, _count, _state, req, svc,
                                        _bind_tester)
from tests.test_owner_recovery import HookVenue
from tests.test_owner_recovery_risk_guard import tg_update

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _drain_workers(k):
    for w in list(getattr(k, "_tg_workers", {}).values()):
        w.shutdown(wait=True)


# ═══ W1 — Telegram ordering ═════════════════════════════════════════════════
def check_queued_recovery_cannot_outlive_newer_halt(world, monkeypatch):
    """Astra W1: RESUME blocked in its venue check → UNHALT → HALT → release."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    monkeypatch.setattr("trader.kernel.threading.Thread", RealThread)
    s = svc(k)
    s.execute(req("freeze"))
    k.notifier.chat_id = "1"
    replies = []
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    in_check, release = threading.Event(), threading.Event()

    def venue_check():
        in_check.set()
        release.wait(5)
    venue.on_positions = venue_check
    k._tg_dispatch("/resume", "https://x", tg_update())
    assert in_check.wait(3)
    k._tg_dispatch("/unhalt", "https://x", tg_update())
    k._tg_dispatch("/halt", "https://x", tg_update())
    assert _wait(lambda: _state(journal) == "HALTED")
    release.set()
    assert _wait(lambda: len(_count(journal, "owner_recovery_result")) >= 1)
    time.sleep(0.2)
    _drain_workers(k)
    assert _state(journal) == "HALTED"                      # the newer HALT holds
    assert len(_count(journal, "owner_recovery_requested")) == 1   # UNHALT never ran
    assert any("recovery_in_progress" in r for r in replies)
    refused = [r for r in _audit(journal) if r["operation"] == "unhalt"]
    assert refused and json.loads(refused[0]["reasons"]) == ["recovery_in_progress"]
    return k


def check_blocked_reply_never_delays_halt(world, monkeypatch):
    """Astra W1: a /status whose reply hangs must not hold a later /halt."""
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    monkeypatch.setattr("trader.kernel.threading.Thread", RealThread)
    k.notifier.chat_id = "1"
    stuck = threading.Event()

    def post(*a, **kw):
        if kw["json"]["text"].startswith("state="):
            stuck.wait(5)                                   # Telegram API hanging
    monkeypatch.setattr("requests.post", post)
    t0 = time.monotonic()
    k._tg_dispatch("/status", "https://x", tg_update())
    time.sleep(0.1)
    k._tg_dispatch("/halt", "https://x", tg_update())
    try:
        assert _wait(lambda: _state(journal) == "HALTED", 1.0)
        assert time.monotonic() - t0 < 1.0
    finally:
        stuck.set()
        _drain_workers(k)


def test_w1_queued_recovery_cannot_outlive_newer_halt(world, monkeypatch):
    check_queued_recovery_cannot_outlive_newer_halt(world, monkeypatch)


def test_w1_blocked_reply_never_delays_halt(world, monkeypatch):
    check_blocked_reply_never_delays_halt(world, monkeypatch)


def test_w1_recovery_admission_is_released_after_the_run(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    monkeypatch.setattr("trader.kernel.threading.Thread", RealThread)
    svc(k).execute(req("halt"))
    k.notifier.chat_id = "1"
    replies = []
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    k._tg_dispatch("/resume", "https://x", tg_update())
    assert _wait(lambda: any("still HALTED" in r for r in replies))
    k._tg_dispatch("/unhalt", "https://x", tg_update())        # admitted again
    assert _wait(lambda: _state(journal) == "ACTIVE")
    _drain_workers(k)


def _rev2_dispatch(monkeypatch):
    """Revision-2 dispatch: recovery queued without admission, reads on the
    containment worker, replies sent synchronously by the executing worker."""
    from concurrent.futures import ThreadPoolExecutor
    from trader.kernel import Kernel
    from trader.owner.adapters import telegram as owner_tg
    from trader.owner.contract import RECOVERY_OPERATIONS

    def dispatch(self, msg, base, update):
        op = owner_tg.command_of(msg)
        name = "recovery" if op in RECOVERY_OPERATIONS else "control"
        workers = self.__dict__.setdefault("_tg_workers", {})
        w = workers.setdefault(name, ThreadPoolExecutor(max_workers=1))
        w.submit(self._handle_tg_command, msg, base, update)
    monkeypatch.setattr(Kernel, "_tg_dispatch", dispatch)


@pytest.mark.parametrize("check", [check_queued_recovery_cannot_outlive_newer_halt,
                                   check_blocked_reply_never_delays_halt])
def test_negative_control_w1_rev2_dispatch_is_caught(world, monkeypatch, check):
    _rev2_dispatch(monkeypatch)
    with pytest.raises(AssertionError):
        check(world, monkeypatch)


# ═══ W3 — panic consumption ═════════════════════════════════════════════════
def _flatten_hook(monkeypatch, during):
    calls = []

    def flatten(exchange, journal, notifier):
        calls.append(journal.kv_get("panic_requested"))
        if len(calls) == 1:
            during()
        return 1
    monkeypatch.setattr("trader.kernel.flatten_all", flatten)
    return calls


def check_panic_during_flatten_stays_pending(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("panic", "test-panic-first01"))
    calls = _flatten_hook(monkeypatch, lambda: s.execute(req("panic", "test-panic-second1")))
    assert k._drain_panic() == 1
    token = lambda raw: json.loads(raw)["request_id"] if raw not in (None, "0") else raw
    assert token(journal.kv_get("panic_requested")) == "test-panic-second1"   # still pending
    assert _state(journal) == "FROZEN"
    assert k._drain_panic() == 1                                      # consumed next cycle
    assert [token(c) for c in calls] == ["test-panic-first01", "test-panic-second1"]
    assert journal.kv_get("panic_requested") == "0" and k._drain_panic() is None


def check_halt_during_flatten_is_kept(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    s = svc(k)
    s.execute(req("panic"))
    _flatten_hook(monkeypatch, lambda: s.execute(req("halt")))
    k._drain_panic()
    assert _state(journal) == "HALTED"                  # never overwritten by FROZEN
    assert journal.kv_get("panic_requested") == "0"


def test_w3_panic_during_flatten_stays_pending(world, monkeypatch):
    check_panic_during_flatten_stays_pending(world, monkeypatch)


def test_w3_halt_during_flatten_is_kept(world, monkeypatch):
    check_halt_during_flatten_is_kept(world, monkeypatch)


def test_w3_plain_panic_flattens_then_freezes_and_legacy_flag_works(world, monkeypatch):
    journal, venue = world
    k = _active(journal, venue, monkeypatch)
    _flatten_hook(monkeypatch, lambda: None)
    journal.kv_set("panic_requested", "1")                  # a pre-gateway flag
    assert k._drain_panic() == 1
    assert _state(journal) == "FROZEN" and journal.kv_get("panic_requested") == "0"


def _rev2_panic_drain(monkeypatch):
    from trader.core.types import ControlState
    from trader.kernel import Kernel

    def drain(self):
        if self.journal.kv_get("panic_requested") in (None, "", "0"):
            return None
        n = __import__("trader.kernel", fromlist=["x"]).flatten_all(
            self.exchange, self.journal, self.notifier)
        self.journal.kv_set("panic_requested", "0")
        self.state_machine.set(ControlState.FROZEN, "operator", f"panic flattened {n}")
        return n
    monkeypatch.setattr(Kernel, "_drain_panic", drain)


@pytest.mark.parametrize("check", [check_panic_during_flatten_stays_pending,
                                   check_halt_during_flatten_is_kept])
def test_negative_control_w3_rev2_panic_drain_is_caught(world, monkeypatch, check):
    _rev2_panic_drain(monkeypatch)
    with pytest.raises(AssertionError):
        check(world, monkeypatch)


# ═══ W4 — audit durability and admission refusals ═══════════════════════════
def _service(tmp_path):
    j = Journal(tmp_path / "a.db")
    s = OwnerService(j, ControlStateMachine(j), resume=None)
    _bind_tester(s.authorizer)
    return j, s


@contextmanager
def _failing_commit(journal):
    """A transaction that writes, then fails before commit (rolled back)."""
    with journal._write_lock:
        conn = journal._conn()
        try:
            yield conn
            raise OSError("commit failed")
        finally:
            conn.rollback()


def check_rolled_back_flush_loses_nothing(tmp_path, monkeypatch):
    j, s = _service(tmp_path)
    for i in range(3):
        s.execute(req("status", f"test-read-000{i}01"))
    real = j._tx
    monkeypatch.setattr(j, "_tx", lambda: _failing_commit(j))
    assert s.flush_audit() is False
    monkeypatch.setattr(j, "_tx", real)
    assert _audit(j) == []                                  # rolled back …
    assert s.flush_audit() is True
    ids = [r["request_id"] for r in _audit(j)]
    assert sorted(ids) == ["test-read-000001", "test-read-000101", "test-read-000201"]
    assert s.flush_audit() is True and len(_audit(j)) == 3  # … written exactly once


def test_w4_rolled_back_flush_loses_nothing(tmp_path, monkeypatch):
    check_rolled_back_flush_loses_nothing(tmp_path, monkeypatch)


def test_w4_unwritable_journal_is_bounded_and_the_loss_recorded(tmp_path, monkeypatch):
    j, s = _service(tmp_path)
    s.audit_buffer_max, s.audit_flush_rows = 5, 10 ** 6
    real = j._tx
    monkeypatch.setattr(j, "_tx", lambda: _failing_commit(j))
    for i in range(8):
        s.execute(req("status"))
    monkeypatch.setattr(j, "_tx", real)
    assert s.flush_audit() is True
    rows = _audit(j)
    assert len(rows) == 6 and json.loads(rows[-1]["reasons"]) == [
        "audit_buffer_overflow_dropped:3"]


def check_slot_refusal_is_audited(tmp_path, monkeypatch):
    j, s = _service(tmp_path)
    ipc_dir = Path(tempfile.mkdtemp(prefix="oi-")) / "ipc"
    server = OwnerIPCServer(s, ipc_dir, class_slots={"control": 0}).start()
    try:
        r = OwnerClient(ipc_dir, "dashboard").call(OwnerRequest(
            "dashboard-busy-halt-01", "halt", "dashboard", "session", issued_at=time.time()))
        assert r.status == Status.REFUSED and r.reasons == ("owner_interface_busy",)
        rows = _audit(j, request_id="dashboard-busy-halt-01")
        assert len(rows) == 1 and rows[0]["principal"] == "owner"
        assert json.loads(rows[0]["reasons"]) == ["owner_interface_busy"]
        assert _count(j, "owner_interface_rejected", request_id="dashboard-busy-halt-01")
        assert j.kv_get("control_state") is None
    finally:
        server.stop()


def test_w4_slot_refusal_is_audited(tmp_path, monkeypatch):
    check_slot_refusal_is_audited(tmp_path, monkeypatch)


def _rev2_flush(monkeypatch):
    """Revision-2 flush: rows dequeued before the transaction commits."""
    from trader.owner import service as svc_mod

    def flush(self):
        with self._audit_lock:
            rows, self._audit_buffer = self._audit_buffer, []
        try:
            with self.journal._tx() as c:
                c.executemany(self._AUDIT_SQL, [v for _s, v in rows])
        except Exception:
            return False
        return True
    monkeypatch.setattr(svc_mod.OwnerService, "flush_audit", flush)


def _rev2_slot_refusal(monkeypatch):
    from trader.owner import service as svc_mod
    from trader.owner.contract import refused
    monkeypatch.setattr(svc_mod.OwnerService, "record_admission_refusal",
                        lambda self, r, reason: refused(r, reason))


@pytest.mark.parametrize("check, mutant", [
    (check_rolled_back_flush_loses_nothing, _rev2_flush),
    (check_slot_refusal_is_audited, _rev2_slot_refusal)])
def test_negative_control_w4_is_caught(tmp_path, monkeypatch, check, mutant):
    mutant(monkeypatch)
    with pytest.raises(AssertionError):
        check(tmp_path, monkeypatch)


# ═══ review §T: W5 and W11 had no mutant column ═════════════════════════════
def _grants_skipped_for_intents(service):
    real = service.authorizer.authorize

    def authorize(channel, identity, op):
        principal, denial = real(channel, identity, op)
        if op in ("panic", "close_trade", "set_market_type") and principal:
            return principal, None
        return principal, denial
    service.authorizer.authorize = authorize


def _reads_not_audited(service):
    real = service._audit_row

    def row(req_, principal, result, *, buffered=False):
        if not buffered:
            real(req_, principal, result)
    service._audit_row = row


@pytest.mark.parametrize("check, mutant", [
    ("test_w5_panic_close_market_are_granted_typed_and_audited", _grants_skipped_for_intents),
    ("test_a_status_and_health_are_read_only_and_audited", _reads_not_audited)])
def test_negative_control_w5_w11_are_caught(tmp_path, monkeypatch, check, mutant):
    import tests.test_owner_interface as matrix
    from trader.kernel import Kernel
    real = Kernel._owner

    def mutated(self):
        fresh = getattr(self, "_owner_service", None) is None
        s = real(self)
        if fresh:
            mutant(s)
        return s
    monkeypatch.setattr(Kernel, "_owner", mutated)
    with pytest.raises(AssertionError):
        getattr(matrix, check)((Journal(tmp_path / "j.db"), HookVenue()), monkeypatch)


# ═══ W2 — browser retry identity (real JS under Node) ═══════════════════════
NODE = shutil.which("node") or shutil.which("nodejs")
HTML = (ROOT / "trader" / "dashboard" / "web" / "index.html").read_text()
OWNER_BLOCK = HTML[HTML.index("/* Owner controls: typed requests executed by the kernel"):
                   HTML.index("/* ── refresh ── */")]

W2_HARNESS = r"""
const vm = require('vm');
const store = {}; let storageBroken = STORAGE_BROKEN;
globalThis.localStorage = {getItem: k => (k in store ? store[k] : null),
                           setItem: (k, v) => { if (storageBroken) throw new Error('quota');store[k] = String(v); },
                           removeItem: k => { delete store[k]; },
                           key: i => Object.keys(store)[i] ?? null,
                           get length() { return Object.keys(store).length; }};
if (!globalThis.crypto) globalThis.crypto = require('crypto').webcrypto;
globalThis.confirm = () => true;
const toasts = []; globalThis.toast = (m) => toasts.push(m);
globalThis.refresh = () => {};
globalThis.document = {querySelectorAll: () => []};
const calls = []; const pending = {};
let failIds = new Set();
globalThis.gql = (q, v) => { calls.push(v.r);
  if (failIds.has(v.r)) return Promise.reject(new Error('network'));
  return new Promise(res => { (pending[v.r] = pending[v.r] || []).push(res); }); };
const ok = {res: {status: 'ACCEPTED', message: 'ok', replayed: false, reasons: []}};
vm.runInThisContext(OWNER_BLOCK);
const tick = () => new Promise(r => setTimeout(r, 5));
(async () => {
  const c1 = ownerControl('halt'); await tick();          // attempt 1 of A
  const c2 = ownerControl('halt'); await tick();          // double-click: same A
  const A = calls[0];
  pending[A].shift()(ok); await c1;                        // first A completes
  failIds = new Set();
  const origGql = globalThis.gql;
  globalThis.gql = (q, v) => { calls.push(v.r); return Promise.reject(new Error('network')); };
  await ownerControl('halt');                              // new action B: no answer
  const B = calls[calls.length - 1];
  globalThis.gql = origGql;
  pending[A].shift()(ok); await c2;                        // late second A completes
  globalThis.gql = (q, v) => { calls.push(v.r); return Promise.resolve(ok); };
  await ownerControl('halt');                              // retry must be B
  console.log(JSON.stringify({A, B, retry: calls[calls.length - 1], toasts}));
})();
"""


def _run_w2(block, storage_broken=False):
    script = (W2_HARNESS.replace("OWNER_BLOCK", json.dumps(block))
              .replace("STORAGE_BROKEN", "true" if storage_broken else "false"))
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def check_late_answer_keeps_newer_id(block, storage_broken=False):
    out = _run_w2(block, storage_broken)
    assert out["A"] != out["B"]
    assert out["retry"] == out["B"], out
    return out


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_w2_late_answer_never_deletes_a_newer_unresolved_id():
    check_late_answer_keeps_newer_id(OWNER_BLOCK)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_w2_storage_failure_keeps_the_id_in_page_and_warns():
    out = check_late_answer_keeps_newer_id(OWNER_BLOCK, storage_broken=True)
    assert any("storage unavailable" in t for t in out["toasts"])


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("old, new, broken", [
    # revision 2: unconditional delete on any definitive answer
    ("if(r&&OWNER_DEFINITIVE.includes(r.status))ownerDone(p.id);",
     "if(r&&OWNER_DEFINITIVE.includes(r.status))ownerEntries().filter(e=>e.action===action)"
     ".forEach(e=>ownerDone(e.id));", False),
    # revision 2: storage failure silently loses the id
    ("for(const id in OWNER_MEM)if(!out.some(e=>e.id===id))out.push(OWNER_MEM[id]);", "", True),
])
def test_negative_control_w2_is_caught(old, new, broken):
    assert old in OWNER_BLOCK
    with pytest.raises(AssertionError):
        check_late_answer_keeps_newer_id(OWNER_BLOCK.replace(old, new, 1), broken)
