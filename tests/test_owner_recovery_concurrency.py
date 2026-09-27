"""Owner recovery / rollback preparation under adversarial interleavings.

Each scenario check takes the operation under test as a callable so the same
assertions run against the real Supervisor and against a mutant that restores
one confirmed race (the negative controls at the bottom must FAIL them).

Threads are real (captured before `_kernel` patches `threading.Thread`) and
every interleaving is forced with events or deterministic hook points, never
with sleeps.
"""
import json
import threading
from contextlib import contextmanager

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.engine.control_fence import (control_fence, latest_intent_event_id,
                                         persisted_state)
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import (OwnerContext, OwnerRecoveryResult,
                                      RecoveryResult, _now)
from tests.test_kernel_boot_recovery import (_kernel, _last_event, _protected,
                                             _status, _transitions)
from tests.test_owner_recovery import (HookVenue, _contained, _events, heal,
                                       loosen, unreadable)

OWNER = OwnerContext("operator", "test", principal="owner-1", request_ref="unit")
RealThread = threading.Thread          # _kernel() replaces threading.Thread
JOIN = 10.0


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def owner_request(k, **kw):
    return k.supervisor.request_owner_recovery(OWNER, **kw)


def rollback(k):
    return k.supervisor.prepare_rollback(OWNER)


def owner_set(journal, state, actor="operator", detail="newer owner intent"):
    return ControlStateMachine(journal).set(state, actor, detail)


def _held(journal, venue, monkeypatch, hold, *, boot=True):
    _protected(journal, venue)
    owner_set(journal, hold, detail="owner hold")
    k, _, _ = _kernel(journal, venue, monkeypatch)
    if boot:
        k.boot()
    assert journal.kv_get("control_state") == hold.value
    return k


def _run(fn, *a):
    box = {}

    def target():
        try:
            box["result"] = fn(*a)
        except BaseException as exc:          # surfaced by the caller
            box["error"] = exc
    t = RealThread(target=target, daemon=True)
    t.start()
    return t, box


def _joined(t, box):
    t.join(JOIN)
    assert not t.is_alive(), "thread deadlocked"
    if "error" in box:
        raise box["error"]
    return box["result"]


class RecordingLock:
    """Wraps the Supervisor pass lock; signals every contended acquire."""

    def __init__(self):
        self._lock = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self._lock.acquire(blocking=False):
            return True
        if blocking:
            self.waiting.set()
            return self._lock.acquire(True, timeout)
        return False

    def release(self):
        self._lock.release()

    def locked(self):
        return self._lock.locked()

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()


def _activations(journal, after):
    return [t for t in _transitions(journal, after) if t[1] == "ACTIVE"]


# ── A/B: HALTED reasserted after intake / before activation CAS ─────────────
def check_halt_after_intake_wins(journal, venue, monkeypatch, request):
    k = _held(journal, venue, monkeypatch, ControlState.HALTED)
    mark = _last_event(journal)
    real_save = k.supervisor._save

    def save(result):
        out = real_save(result)
        if result.stage == "OWNER_REQUESTED":           # right after intake
            owner_set(journal, ControlState.HALTED, "chat", "halt after intake")
        return out
    monkeypatch.setattr(k.supervisor, "_save", save)
    r = request(k)
    assert journal.kv_get("control_state") == "HALTED"
    assert _activations(journal, mark) == []
    return k, r


def test_a_halted_reasserted_after_intake_stays_halted(world, monkeypatch):
    journal, venue = world
    _, r = check_halt_after_intake_wins(
        journal, venue, monkeypatch, lambda k: owner_request(k, allow_unhalt=True))
    assert r.status == "CONTAINED" and r.control_state == "HALTED"
    assert venue.mutations == []


def test_a2_halted_reasserted_during_fresh_proof_stays_halted(world, monkeypatch):
    journal, venue = world
    k = _held(journal, venue, monkeypatch, ControlState.HALTED)
    mark = _last_event(journal)
    venue.on_positions = lambda: owner_set(journal, ControlState.HALTED, "dashboard")
    r = owner_request(k, allow_unhalt=True)
    assert journal.kv_get("control_state") == "HALTED"
    assert r.status == "CONTAINED" and "control_state_changed_during_pass" in r.reasons
    assert _activations(journal, mark) == []


def test_b_halted_between_fresh_proof_and_activation_cas(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    mark = _last_event(journal)
    real_save = k.supervisor._save

    def save(result):
        out = real_save(result)
        if result.stage == "PROVED":                    # proof durable; CAS next
            owner_set(journal, ControlState.HALTED, "chat", "late halt")
        return out
    monkeypatch.setattr(k.supervisor, "_save", save)
    r = owner_request(k)
    assert journal.kv_get("control_state") == "HALTED"
    assert r.status == "CONTAINED"
    assert "control_state_changed_during_activation" in r.reasons
    assert _activations(journal, mark) == []


# ── same-state intent visibility (state machine boundary) ───────────────────
def check_same_state_halt_observable(journal, set_state):
    owner_set(journal, ControlState.HALTED, detail="first halt")
    before = latest_intent_event_id(journal)
    set_state(journal, ControlState.HALTED)                 # same-state reassertion
    after = latest_intent_event_id(journal)
    assert after > before, "same-state owner HALTED left no intent event"
    stale = ControlStateMachine(journal).set_if_current(
        ControlState.HALTED, ControlState.RECOVERY, "operator", "bound to old watermark",
        expected_control_event_id=before)
    assert not stale and journal.kv_get("control_state") == "HALTED"


@pytest.mark.parametrize("actor", ["operator", "dashboard", "chat"])
def test_same_state_owner_halt_and_freeze_are_holds(tmp_path, actor):
    journal = Journal(tmp_path / "j.db")
    check_same_state_halt_observable(
        journal, lambda j, s: ControlStateMachine(j).set(s, actor, "reassert"))
    owner_set(journal, ControlState.FROZEN)
    before = latest_intent_event_id(journal)
    eid = ControlStateMachine(journal).set(ControlState.FROZEN, actor, "reassert")
    assert eid == latest_intent_event_id(journal) > before
    row = journal.query("SELECT event, actor FROM control_events WHERE id=?", (eid,))[0]
    assert (row["event"], row["actor"]) == ("state_hold", actor)


def test_non_owner_same_state_refresh_is_not_noisy(tmp_path):
    journal = Journal(tmp_path / "j.db")
    sm = ControlStateMachine(journal)
    sm.set(ControlState.HALTED, "risk_engine", "drawdown")
    mark = _last_event(journal)
    for _ in range(5):                         # per-cycle drawdown re-halt
        assert sm.set(ControlState.HALTED, "risk_engine", "drawdown") is None
    assert _last_event(journal) == mark


# ── C: FROZEN reasserted (incl. same-state) after intake ────────────────────
def test_c_frozen_reasserted_same_state_after_intake_newer_hold_wins(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    mark = _last_event(journal)
    real_save = k.supervisor._save
    holds = []

    def save(result):
        out = real_save(result)
        if result.stage == "OWNER_REQUESTED":
            holds.append(owner_set(journal, ControlState.FROZEN, "dashboard"))
        return out
    monkeypatch.setattr(k.supervisor, "_save", save)
    # a second, same-state FROZEN during the fresh venue read
    venue.on_positions = lambda: holds.append(owner_set(journal, ControlState.FROZEN, "chat"))
    r = owner_request(k)
    assert journal.kv_get("control_state") == "FROZEN"
    assert r.status == "CONTAINED" and _activations(journal, mark) == []
    assert holds and latest_intent_event_id(journal) == holds[-1]
    # a later cadence or reboot never takes the newer owner hold over
    k.supervisor._next_pass = 0
    assert k.supervisor.cycle() is None
    k.supervisor.pass_once()
    assert journal.kv_get("control_state") == "FROZEN"


# ── D: a cadence pass already running cannot overwrite the acknowledgement ──
def check_running_cycle_cannot_overwrite_ack(journal, venue, monkeypatch, request):
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    assert _status(journal)["needs_owner"] is True
    heal(venue)                          # the owner's fresh proof should now pass
    sup = k.supervisor
    sup._pass_lock = RecordingLock()
    in_cycle, release = threading.Event(), threading.Event()

    def block():
        in_cycle.set()
        assert release.wait(JOIN)
    venue.on_positions = block
    sup._next_pass = 0
    ct, cbox = _run(sup.cycle)
    assert in_cycle.wait(JOIN)
    rt, rbox = _run(request, k)
    sup._pass_lock.waiting.wait(JOIN)    # request is now contending for the pass
    release.set()
    cycle_result = _joined(ct, cbox)
    r = _joined(rt, rbox)
    assert cycle_result.outcome == "NEEDS_OWNER"           # the older pass's view
    assert journal.kv_get("control_state") == "ACTIVE"
    assert r.status == "ACTIVATED"
    return k, r


def test_d_running_cycle_cannot_overwrite_owner_acknowledgement(world, monkeypatch):
    journal, venue = world
    check_running_cycle_cannot_overwrite_ack(journal, venue, monkeypatch, owner_request)
    # intake happened only after the older pass saved its result
    cycle_save = [e for e in _events(journal, "supervisor_recovery")
                  if json.loads(e["detail"])["outcome"] == "NEEDS_OWNER"][-1]["id"]
    assert _events(journal, "owner_recovery_requested")[0]["id"] > cycle_save


# ── E: owner request first; cadence arrives → one serialized pass ───────────
def test_e_owner_request_first_cadence_skips_one_pass(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    venue.stops = []                     # naked: a second concurrent pass would re-arm twice
    sup = k.supervisor
    passes, in_pass, release = [], threading.Event(), threading.Event()
    real = sup._pass_once
    monkeypatch.setattr(sup, "_pass_once", lambda **kw: passes.append(kw) or real(**kw))

    def block():
        in_pass.set()
        assert release.wait(JOIN)
    venue.on_positions = block
    rt, rbox = _run(owner_request, k)
    assert in_pass.wait(JOIN)
    sup._next_pass = 0
    ct, cbox = _run(sup.cycle)
    assert _joined(ct, cbox) is None     # skipped, never queued behind the request
    release.set()
    r = _joined(rt, rbox)
    assert r.status == "ACTIVATED" and len(passes) == 1
    assert [m[0] for m in venue.mutations] == ["create_order"]


# ── F: overlapping owner requests: second refused, still audited ────────────
def check_overlap_refused_and_audited(journal, venue, monkeypatch, request):
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    in_pass, release = threading.Event(), threading.Event()

    def block():
        in_pass.set()
        assert release.wait(JOIN)
    venue.on_positions = block
    t1, b1 = _run(owner_request, k)
    assert in_pass.wait(JOIN)
    second = _joined(*_run(request, k))
    release.set()
    first = _joined(t1, b1)
    assert first.status == "ACTIVATED"
    assert second.status == "REFUSED" and second.reasons == ("request_in_progress",)
    req = {e["id"]: e for e in _events(journal, "owner_recovery_requested")}
    res = [json.loads(e["detail"]) for e in _events(journal, "owner_recovery_result")]
    assert second.request_event_id in req, "refused overlap has no request record"
    rec = [d for d in res if d["request_event_id"] == second.request_event_id]
    assert len(rec) == 1, "refused overlap has no result record"
    assert rec[0]["status"] == "REFUSED" and rec[0]["reasons"] == ["request_in_progress"]
    assert rec[0]["operation"] == "owner_recovery" and rec[0]["channel"] == "test"
    assert isinstance(rec[0]["watermark_control_event_id"], int)
    detail = json.loads(req[second.request_event_id]["detail"])
    assert req[second.request_event_id]["actor"] == "operator"
    assert detail["operation"] == "owner_recovery" and detail["channel"] == "test"
    assert (detail["principal"], detail["request_ref"]) == ("owner-1", "unit")
    return k, second


def test_f_overlapping_requests_second_refused_but_audited(world, monkeypatch):
    journal, venue = world
    check_overlap_refused_and_audited(journal, venue, monkeypatch, owner_request)
    assert len(_events(journal, "owner_recovery_requested")) == 2
    assert len(_events(journal, "owner_recovery_result")) == 2


def test_overlap_between_request_and_rollback_is_audited(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    venue.on_positions = lambda: inner.append(rollback(k))
    inner = []
    assert owner_request(k).status == "ACTIVATED"
    assert inner[0].status == "REFUSED" and inner[0].reasons == ("request_in_progress",)
    assert len(_events(journal, "rollback_prepare_requested")) == 1
    assert json.loads(_events(journal, "rollback_prepare_result")[0]["detail"])[
        "operation"] == "rollback_prepare"


# ── G–J: rollback preparation retains its own hold identity ─────────────────
def check_rollback_frozen_then_newer_halted(journal, venue, monkeypatch, prepare):
    """HALTED injected right after the rollback observes FROZEN (its request row)."""
    k = _held(journal, venue, monkeypatch, ControlState.FROZEN)
    mark = _last_event(journal)
    injector = {}
    real_log = journal.log_control_event

    def log(event, *a, **kw):
        eid = real_log(event, *a, **kw)
        if event == "rollback_prepare_requested" and "t" not in injector:
            injector["t"], injector["b"] = _run(
                owner_set, journal, ControlState.HALTED, "chat", "newer halt")
            injector["t"].join(1.0)      # lands now unless the fence is held
        return eid
    monkeypatch.setattr(journal, "log_control_event", log)
    venue.on_positions = lambda: _joined(injector["t"], injector["b"])
    r = prepare(k)
    _joined(injector["t"], injector["b"])
    assert journal.kv_get("control_state") == "HALTED", "newer HALTED was overwritten"
    assert r.status == "BLOCKED"
    assert [t for t in _transitions(journal, mark) if t[0] == "HALTED"] == []
    return k, r


def test_g_rollback_sees_frozen_then_newer_halted(world, monkeypatch):
    journal, venue = world
    _, r = check_rollback_frozen_then_newer_halted(journal, venue, monkeypatch, rollback)
    assert "control_state_changed" in r.reasons
    assert r.hold_event_id is not None and r.hold_event_id != latest_intent_event_id(journal)


@contextmanager
def after_own_hold(monkeypatch, journal, action):
    """Run `action` once, right after the rollback's own FROZEN hold is established
    and the fence is released — whichever API (set or fenced) established it."""
    fired = []
    real_set, real_fenced = ControlStateMachine.set, ControlStateMachine.fenced

    def maybe_fire():
        rows = journal.query(
            "SELECT detail FROM control_events WHERE event IN ('state_change','state_hold') "
            "ORDER BY id DESC LIMIT 1")
        if not fired and rows and str(rows[0]["detail"]).startswith("rollback preparation #"):
            fired.append(True)
            action()

    def set_(self, *a, **kw):
        out = real_set(self, *a, **kw)
        maybe_fire()
        return out

    @contextmanager
    def fenced(self):
        with real_fenced(self) as f:
            yield f
        maybe_fire()
    monkeypatch.setattr(ControlStateMachine, "set", set_)
    monkeypatch.setattr(ControlStateMachine, "fenced", fenced)
    yield fired


def check_rollback_newer_frozen_wins(journal, venue, monkeypatch, prepare):
    k = _held(journal, venue, monkeypatch, ControlState.FROZEN)
    newer = []
    with after_own_hold(monkeypatch, journal, lambda: newer.append(
            ControlStateMachine(journal).set(ControlState.FROZEN, "chat", "newer hold"))) as fired:
        r = prepare(k)
    assert fired and newer[0] is not None
    assert r.hold_event_id != newer[0], "rollback adopted a newer hold as its own"
    assert r.status == "BLOCKED"
    assert journal.kv_get("control_state") == "FROZEN"
    assert latest_intent_event_id(journal) == newer[0]       # newer hold untouched
    return k, r


def test_h_rollback_own_frozen_then_newer_frozen_wins(world, monkeypatch):
    journal, venue = world
    _, r = check_rollback_newer_frozen_wins(journal, venue, monkeypatch, rollback)
    row = journal.query("SELECT event, actor, detail FROM control_events WHERE id=?",
                        (r.hold_event_id,))[0]
    assert (row["event"], row["actor"]) == ("state_hold", "operator")
    assert row["detail"].startswith("rollback preparation #")


def check_rollback_late_event_blocks(journal, venue, monkeypatch, prepare, late):
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    sup = k.supervisor
    real = sup._pass_once

    def pass_then_late(**kw):
        out = real(**kw)
        assert out.checks["entries_safe"] is True       # fresh proof succeeded
        late(journal)
        return out
    monkeypatch.setattr(sup, "_pass_once", pass_then_late)
    monkeypatch.setattr(sup, "pass_once", lambda **kw: pass_then_late(**kw))
    r = prepare(k)
    assert r.status == "BLOCKED", "READY despite a newer control event"
    return k, r


@pytest.mark.parametrize("late, expect", [
    (lambda j: owner_set(j, ControlState.FROZEN, "dashboard"), "FROZEN"),
    (lambda j: owner_set(j, ControlState.HALTED, "chat"), "HALTED"),
])
def test_i_rollback_newer_event_before_finalization_blocks(world, monkeypatch, late, expect):
    journal, venue = world
    _, r = check_rollback_late_event_blocks(journal, venue, monkeypatch, rollback, late)
    assert journal.kv_get("control_state") == expect
    assert "control_state_changed" in r.reasons


def test_j_rollback_ready_with_exact_own_hold(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    r = rollback(k)
    assert r.status == "READY" and r.control_state == "FROZEN"
    assert r.hold_event_id == latest_intent_event_id(journal)
    row = journal.query("SELECT event, from_state, to_state, actor, detail "
                        "FROM control_events WHERE id=?", (r.hold_event_id,))[0]
    assert (row["event"], row["from_state"], row["to_state"], row["actor"]) == (
        "state_hold", "FROZEN", "FROZEN", "operator")
    intake = journal.query("SELECT id FROM control_events WHERE event='state_change' "
                           "AND from_state='RECOVERY' AND to_state='FROZEN' "
                           "AND id>?", (r.request_event_id,))
    assert len(intake) == 1 and intake[0]["id"] < r.hold_event_id
    assert row["detail"] == f"rollback preparation #{r.request_event_id}"
    res = json.loads(_events(journal, "rollback_prepare_result")[-1]["detail"])
    assert res["own_hold_event_id"] == r.hold_event_id and res["status"] == "READY"


def test_j2_rollback_from_owner_frozen_ready_with_own_same_state_hold(world, monkeypatch):
    journal, venue = world
    k = _held(journal, venue, monkeypatch, ControlState.FROZEN)
    prior = latest_intent_event_id(journal)
    r = rollback(k)
    assert r.status == "READY" and r.hold_event_id > prior
    assert r.hold_event_id == latest_intent_event_id(journal)


def test_rollback_blocked_from_owner_frozen_keeps_owner_frozen(world, monkeypatch):
    journal, venue = world
    k = _held(journal, venue, monkeypatch, ControlState.FROZEN)
    loosen(venue)
    r = rollback(k)
    assert r.status == "BLOCKED" and journal.kv_get("control_state") == "FROZEN"
    assert venue.mutations == []


# ── K/L: HALTED requires explicit unhalt ────────────────────────────────────
def check_halted_default_request_stays_halted(journal, venue, monkeypatch, request):
    k = _held(journal, venue, monkeypatch, ControlState.HALTED)
    venue.stops = []                     # a pass here WOULD re-arm: prove none ran
    reads, mark = venue.reads, _last_event(journal)
    r = request(k)
    assert journal.kv_get("control_state") == "HALTED"
    assert [t for t in _transitions(journal, mark)] == []
    assert venue.mutations == [] and venue.reads == reads
    return k, r


def test_k_halted_request_without_unhalt_stays_halted(world, monkeypatch):
    journal, venue = world
    _, r = check_halted_default_request_stays_halted(journal, venue, monkeypatch,
                                                     owner_request)
    assert r.status == "REFUSED" and r.reasons == ("halted_requires_explicit_unhalt",)
    res = json.loads(_events(journal, "owner_recovery_result")[-1]["detail"])
    assert res["status"] == "REFUSED" and res["operation"] == "owner_recovery"


def test_l_halted_explicit_unhalt_enters_recovery_and_needs_fresh_proof(world, monkeypatch):
    journal, venue = world
    k = _held(journal, venue, monkeypatch, ControlState.HALTED)
    loosen(venue)
    mark, reads = _last_event(journal), venue.reads
    r = owner_request(k, allow_unhalt=True)
    assert venue.reads > reads
    assert r.status == "CONTAINED" and journal.kv_get("control_state") == "RECOVERY"
    assert _transitions(journal, mark) == [("HALTED", "RECOVERY", "operator")]
    req = json.loads(_events(journal, "owner_recovery_requested")[-1]["detail"])
    assert req["operation"] == "owner_unhalt"
    heal(venue)
    mark = _last_event(journal)
    r2 = owner_request(k)
    assert r2.status == "ACTIVATED"
    assert _transitions(journal, mark) == [("RECOVERY", "ACTIVE", "supervisor")]


def test_non_owner_cannot_unhalt(world, monkeypatch):
    journal, venue = world
    k = _held(journal, venue, monkeypatch, ControlState.HALTED)
    for actor in ("supervisor", "risk_engine", "macro_guard"):
        with pytest.raises(ValueError):
            k.supervisor.request_owner_recovery(OwnerContext(actor, "test"), allow_unhalt=True)
    assert journal.kv_get("control_state") == "HALTED"


def test_telegram_resume_keeps_halt_and_unhalt_is_explicit(world, monkeypatch):
    journal, venue = world
    k = _held(journal, venue, monkeypatch, ControlState.HALTED)
    replies = []
    k.notifier.chat_id = "1"
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    k._handle_tg_command("/resume", "https://api.telegram.org/botX")
    assert journal.kv_get("control_state") == "HALTED"
    assert replies[-1].startswith("😴 still HALTED")
    k._handle_tg_command("/unhalt", "https://api.telegram.org/botX")
    assert journal.kv_get("control_state") == "ACTIVE"
    assert replies[-1] == "🙂 ACTIVE — fresh recovery check proved safe."
    ops = [json.loads(e["detail"])["meta"]["command"]
           for e in _events(journal, "owner_recovery_requested")]
    assert ops == ["/resume", "/unhalt"]


def test_owner_interface_is_transport_neutral(world, monkeypatch):
    """A future gateway channel calls the identical typed operation; the core
    audits its identity without any channel-native object."""
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    ctx = OwnerContext("chat", "openclaw", principal="wa:+000", request_ref="gw-1",
                       meta={"intent": "resume"})
    r = k.supervisor.request_owner_recovery(ctx)
    assert r.status == "ACTIVATED"
    req = json.loads(_events(journal, "owner_recovery_requested")[-1]["detail"])
    assert (req["channel"], req["principal"], req["request_ref"]) == ("openclaw", "wa:+000", "gw-1")
    with pytest.raises(ValueError):                          # untyped input refused
        k.supervisor.request_owner_recovery({"actor": "operator"})


# ── negative controls: each mutant restores one race and MUST fail ──────────
def mutant_invisible_same_state_halt(monkeypatch):
    real = ControlStateMachine._set_fenced

    def _set_fenced(self, new, actor, detail):
        self.refresh()
        if new == ControlState.HALTED and self.state == ControlState.HALTED:
            return None                   # the pre-fix behavior: no hold event
        return real(self, new, actor, detail)
    monkeypatch.setattr(ControlStateMachine, "_set_fenced", _set_fenced)


def mutant_ack_outside_lock(k):
    """Pre-fix protocol: intake + acknowledgement, THEN wait for the pass lock."""
    sup = k.supervisor
    with control_fence(sup.journal):
        watermark = latest_intent_event_id(sup.journal)
    sup._save(RecoveryResult(control_state_observed="RECOVERY", outcome="RECOVERING",
                             stage="OWNER_REQUESTED", safe_to_activate=False,
                             needs_owner=False, containment_owned=True,
                             containment_started_at=_now(), containment_from="RECOVERY",
                             containment_event_id=watermark))
    fresh = sup.pass_once(advance_entry=False)
    active = persisted_state(sup.journal) == ControlState.ACTIVE
    return OwnerRecoveryResult("ACTIVATED" if active else "CONTAINED",
                               persisted_state(sup.journal).value, outcome=fresh.outcome)


def mutant_unaudited_overlap(k):
    if k.supervisor._request_lock.locked():
        return OwnerRecoveryResult("REFUSED", None, reasons=("request_in_progress",))
    return owner_request(k)


def mutant_old_rollback(k):
    """The previous package's prepare_rollback (unfenced FROZEN branch, adopts latest)."""
    sup, sm, j = k.supervisor, k.state_machine, k.journal
    with control_fence(j):
        state, watermark = persisted_state(j), latest_intent_event_id(j)
    rid = j.log_control_event("rollback_prepare_requested", "operator", detail={})
    if state == ControlState.FROZEN:
        sm.set(ControlState.FROZEN, "operator", f"rollback preparation #{rid}")
        hold = latest_intent_event_id(j)
        if persisted_state(j) != ControlState.FROZEN:
            hold = None
    else:
        moved = sm.set_if_current(state, ControlState.FROZEN, "operator",
                                  f"rollback preparation #{rid}",
                                  expected_control_event_id=watermark)
        hold = moved.event_id if moved else None
    if hold is None:
        return OwnerRecoveryResult("REFUSED", None)
    fresh = sup.pass_once(advance_entry=False)
    undisturbed = (persisted_state(j) == ControlState.FROZEN
                   and latest_intent_event_id(j) == hold)
    status = "READY" if fresh.checks.get("entries_safe") and undisturbed else "BLOCKED"
    return OwnerRecoveryResult(status, persisted_state(j).value, hold_event_id=hold)


def mutant_rollback_without_final_check(k):
    sup, sm = k.supervisor, k.state_machine
    with sm.fenced() as f:
        hold = f.apply(ControlState.FROZEN, "operator", "rollback preparation #0")
    fresh = sup._pass_once(boot=False, advance_entry=False)
    ok = fresh.checks.get("entries_safe") and persisted_state(k.journal) != ControlState.HALTED
    return OwnerRecoveryResult("READY" if ok else "BLOCKED", "FROZEN", hold_event_id=hold)


def mutant_halted_auto_release(k):
    return owner_request(k, allow_unhalt=True)     # unhalt without owner's explicit ask


def test_negative_invisible_same_state_halt_is_caught(tmp_path, monkeypatch):
    mutant_invisible_same_state_halt(monkeypatch)
    journal = Journal(tmp_path / "j.db")
    with pytest.raises(AssertionError):
        check_same_state_halt_observable(
            journal, lambda j, s: ControlStateMachine(j).set(s, "operator", "reassert"))


@pytest.mark.parametrize("check, mutant", [
    (check_running_cycle_cannot_overwrite_ack, mutant_ack_outside_lock),
    (check_overlap_refused_and_audited, mutant_unaudited_overlap),
    (check_rollback_newer_frozen_wins, mutant_old_rollback),
    (check_rollback_frozen_then_newer_halted, mutant_old_rollback),
    (check_halted_default_request_stays_halted, mutant_halted_auto_release),
], ids=["ack_outside_lock", "unaudited_overlap", "adopt_latest_frozen",
        "lost_halted_in_rollback", "halted_auto_release"])
def test_negative_control_mutant_is_caught(world, monkeypatch, check, mutant):
    journal, venue = world
    with pytest.raises(AssertionError):
        check(journal, venue, monkeypatch, mutant)


@pytest.mark.parametrize("late", [lambda j: owner_set(j, ControlState.FROZEN, "dashboard")])
def test_negative_rollback_without_final_check_is_caught(world, monkeypatch, late):
    journal, venue = world
    with pytest.raises(AssertionError):
        check_rollback_late_event_blocks(journal, venue, monkeypatch,
                                         mutant_rollback_without_final_check, late)
