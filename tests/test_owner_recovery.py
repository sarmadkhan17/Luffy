"""Typed owner recovery: contained fresh re-verification before ACTIVE.

Scenario checks take the operation under test as a callable so the same
assertions run against the real implementation and against deliberately
broken mutants (the negative controls at the bottom must FAIL them).
"""
import json
import subprocess
import sys
import tarfile
import io
from pathlib import Path

import pytest

from trader.core.types import Action, ControlState, Decision, Position, Side
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import KEY, OwnerContext, OwnerRecoveryResult

OWNER = OwnerContext("operator", "test", principal="owner-1", request_ref="unit",
                     meta={"case": "unit"})
from tests.test_kernel_boot_recovery import (RecordingVenue, _kernel, _last_event,
                                             _protected, _status, _stop,
                                             _transitions)
from trader.core.journal import Journal

REPO = Path(__file__).resolve().parents[1]


class HookVenue(RecordingVenue):
    """RecordingVenue that can run a callback inside a venue read."""

    def __init__(self):
        super().__init__()
        self.reads = 0
        self.on_positions = None     # fires on the first fetch_positions only
        self.on_listing = None       # fires on every per-symbol listing

    def fetch_positions(self):
        self.reads += 1
        hook, self.on_positions = self.on_positions, None
        if hook:
            hook()
        return super().fetch_positions()

    def fetch_open_orders(self, symbol):
        if self.on_listing:
            self.on_listing()
        return super().fetch_open_orders(symbol)


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def owner_request(k):
    return k.supervisor.request_owner_recovery(OWNER)


def _contained(journal, venue, monkeypatch, *, fault):
    """Boot into owner-held RECOVERY with `fault` applied to the venue."""
    _protected(journal, venue)
    fault(venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert journal.kv_get("control_state") == "RECOVERY"
    assert _status(journal)["needs_owner"] is True
    return k


def unreadable(v):
    v.unreadable = True


def loosen(v):
    v.stops = [_stop(trigger="80")]


def heal(v):
    v.unreadable = False
    v.stops = [_stop()]


def _entry_probe(k):
    from tests.test_kernel_boot_recovery import _entry_probe as probe
    return probe(k)


def _events(journal, name):
    return [dict(r) for r in journal.query(
        "SELECT id, actor, from_state, to_state, detail FROM control_events "
        "WHERE event=? ORDER BY id", (name,))]


# ── scenario checks (shared by real implementation and mutants) ─────────────
def check_cleared_fault_activates(journal, venue, monkeypatch, request):
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    mark, reads = _last_event(journal), venue.reads
    r = request(k)
    assert venue.reads > reads, "no fresh venue read"
    assert journal.kv_get("control_state") == "ACTIVE"
    assert _transitions(journal, mark) == [("RECOVERY", "ACTIVE", "supervisor")]
    return k, r


def check_fault_remains_contained(journal, venue, monkeypatch, request):
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    r = request(k)
    assert journal.kv_get("control_state") == "RECOVERY"
    assert _status(journal)["outcome"] == "NEEDS_OWNER"
    assert "position_unprotected:BTC/USDT" in _status(journal)["reasons"]
    assert _entry_probe(k)[1:] == ("state=RECOVERY: entries blocked", 0)
    assert venue.mutations == [] or all(m[0] == "set_leverage" for m in venue.mutations)
    return k, r


def check_concurrent_frozen_wins(journal, venue, monkeypatch, request):
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    venue.on_positions = lambda: ControlStateMachine(journal).set(
        ControlState.FROZEN, "dashboard", "owner hold during recheck")
    r = request(k)
    assert journal.kv_get("control_state") == "FROZEN"
    last = _transitions(journal)[-1]
    assert last == ("RECOVERY", "FROZEN", "dashboard")
    return k, r


def check_stale_safe_cannot_authorize(journal, venue, monkeypatch, request):
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    stale = _status(journal)
    stale.update(outcome="SAFE", stage="PROVED", safe_to_activate=True,
                 needs_owner=False, reasons=[])
    journal.kv_set(KEY, json.dumps(stale))
    r = request(k)
    assert journal.kv_get("control_state") == "RECOVERY"
    assert _status(journal)["outcome"] == "NEEDS_OWNER"
    return k, r


# ── A–J ─────────────────────────────────────────────────────────────────────
def test_a_recovery_fault_cleared_activates_after_fresh_proof(world, monkeypatch):
    journal, venue = world
    k, r = check_cleared_fault_activates(journal, venue, monkeypatch, owner_request)
    assert r == OwnerRecoveryResult("ACTIVATED", "ACTIVE", outcome="SAFE",
                                    reasons=(), request_event_id=r.request_event_id)
    req, res = _events(journal, "owner_recovery_requested"), _events(journal, "owner_recovery_result")
    assert len(req) == len(res) == 1
    assert json.loads(req[0]["detail"])["observed_state"] == "RECOVERY"
    assert json.loads(res[0]["detail"])["status"] == "ACTIVATED"
    assert venue.mutations == []


def test_b_fault_remains_stays_recovery(world, monkeypatch):
    journal, venue = world
    _, r = check_fault_remains_contained(journal, venue, monkeypatch, owner_request)
    assert r.status == "CONTAINED" and r.control_state == "RECOVERY"
    assert r.outcome == "NEEDS_OWNER" and "position_unprotected:BTC/USDT" in r.reasons


def test_c_stale_reason_fresh_safe_activates_only_after_fresh_pass(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    assert "position_unprotected:BTC/USDT" in _status(journal)["reasons"]   # stale
    venue.stops = [_stop()]                                                # fixed
    calls = []
    real = k.supervisor._pass_once
    monkeypatch.setattr(k.supervisor, "_pass_once",
                        lambda **kw: calls.append(kw) or real(**kw))
    r = owner_request(k)
    assert calls == [{"boot": False, "advance_entry": False}]
    assert r.status == "ACTIVATED" and r.reasons == ()
    assert _status(journal)["checks"]["venue_protection"] is True


def test_c2_stale_safe_status_cannot_authorize(world, monkeypatch):
    journal, venue = world
    _, r = check_stale_safe_cannot_authorize(journal, venue, monkeypatch, owner_request)
    assert r.status == "CONTAINED"


def test_d_concurrent_frozen_during_recheck_wins(world, monkeypatch):
    journal, venue = world
    _, r = check_concurrent_frozen_wins(journal, venue, monkeypatch, owner_request)
    assert r.status == "CONTAINED" and r.control_state == "FROZEN"
    assert "control_state_changed_during_pass" in r.reasons


def test_d2_concurrent_frozen_between_proof_and_activation_wins(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    real_save = k.supervisor._save

    def save(result):
        out = real_save(result)
        if result.stage == "PROVED":         # proof persisted; CAS is next
            ControlStateMachine(journal).set(ControlState.FROZEN, "chat", "late hold")
        return out
    monkeypatch.setattr(k.supervisor, "_save", save)
    r = owner_request(k)
    assert journal.kv_get("control_state") == "FROZEN"
    assert r.status == "CONTAINED"
    assert "control_state_changed_during_activation" in r.reasons


def test_e_concurrent_new_containment_prevents_activation(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)

    def recontain():
        sm = ControlStateMachine(journal)
        sm.set(ControlState.FROZEN, "supervisor", "new containment")
        sm.set(ControlState.RECOVERY, "supervisor", "new containment")
    venue.on_positions = recontain
    r = owner_request(k)
    assert journal.kv_get("control_state") == "RECOVERY"
    assert r.status == "CONTAINED"
    assert ("RECOVERY", "ACTIVE") not in [t[:2] for t in _transitions(journal)]


def test_e2_new_fault_appearing_during_recheck_prevents_activation(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    venue.on_positions = lambda: loosen(venue)
    r = owner_request(k)
    assert journal.kv_get("control_state") == "RECOVERY"
    assert r.status == "CONTAINED" and r.outcome == "NEEDS_OWNER"


def test_f_venue_unreadable_during_recheck_stays_contained(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    r = owner_request(k)
    assert journal.kv_get("control_state") == "RECOVERY"
    assert r.status == "CONTAINED" and "venue_state_unreadable" in r.reasons
    assert venue.mutations == []


def test_g_loosened_protection_zero_mutation(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    r = owner_request(k)
    assert r.status == "CONTAINED" and journal.kv_get("control_state") == "RECOVERY"
    assert venue.mutations == []


def test_h_unresolved_entry_intent_stays_recovery(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    intent = k.executor.recovery.begin(Position(
        id="pos_eth", symbol="ETH/USDT", side=Side.LONG, amount=1.0, entry_price=0,
        notional_usdt=0, leverage=5, stop_loss=90.0, take_profit=110.0,
        strategy_id="s", strategy_name="s", decision_id="d-crash",
        market_type="futures", exec_mode="live"))
    intent["order_id"] = "entry-1"
    k.executor.recovery.save(intent, "entry_submitted")
    venue.order_status_unreadable = True
    k.boot()
    before = k.executor.recovery.pending()
    r = owner_request(k)
    assert r.status == "CONTAINED" and journal.kv_get("control_state") == "RECOVERY"
    assert "entry_recovery_pending" in r.reasons
    assert k.executor.recovery.pending() == before     # verified, not ticked
    assert venue.mutations == []


def test_i_repeated_requests_auditable_without_duplicate_actions(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    assert [owner_request(k).status for _ in range(2)] == ["CONTAINED", "CONTAINED"]
    assert len(_events(journal, "owner_recovery_requested")) == 2
    assert len(_events(journal, "owner_recovery_result")) == 2
    assert venue.mutations == []
    # heal by removing the stop entirely: the recheck re-arms exactly once
    venue.stops = []
    first, second = owner_request(k), owner_request(k)
    assert (first.status, second.status) == ("ACTIVATED", "ALREADY_ACTIVE")
    assert [m[0] for m in venue.mutations] == ["create_order"]


def test_i2_overlapping_request_and_cadence_are_serialized(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    venue.stops = []                 # a naked position: the one re-arm must not double
    inner = {}

    def overlap():
        inner["request"] = owner_request(k)
        k.supervisor._next_pass = 0
        inner["cycle"] = k.supervisor.cycle()
    venue.on_positions = overlap
    r = owner_request(k)
    assert inner["request"].status == "REFUSED"
    assert inner["request"].reasons == ("request_in_progress",)
    assert inner["request"].request_event_id is not None      # refused, still audited
    assert len(_events(journal, "owner_recovery_requested")) == 2
    assert len(_events(journal, "owner_recovery_result")) == 2
    assert inner["cycle"] is None
    assert r.status == "ACTIVATED"
    assert [m[0] for m in venue.mutations] == ["create_order"]


def test_j_already_active_is_a_typed_noop(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    status, mark = journal.kv_get(KEY), _last_event(journal)
    r = owner_request(k)
    assert r.status == "ALREADY_ACTIVE" and r.control_state == "ACTIVE"
    assert _transitions(journal, mark) == []
    assert journal.kv_get(KEY) == status
    assert venue.reads == 1                      # boot only; no recheck


@pytest.mark.parametrize("hold, unhalt", [(ControlState.FROZEN, False),
                                          (ControlState.HALTED, True)])
def test_owner_hold_at_intake_is_released_into_contained_recheck(world, monkeypatch,
                                                                 hold, unhalt):
    journal, venue = world
    _protected(journal, venue)
    ControlStateMachine(journal).set(hold, "operator", "owner hold")
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    mark = _last_event(journal)
    r = k.supervisor.request_owner_recovery(OWNER, allow_unhalt=unhalt)
    assert r.status == "ACTIVATED"
    assert [t[:2] for t in _transitions(journal, mark)] == [
        (hold.value, "RECOVERY"), ("RECOVERY", "ACTIVE")]


def test_non_owner_actor_rejected(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    with pytest.raises(ValueError):
        k.supervisor.request_owner_recovery(OwnerContext("supervisor", "test"))
    with pytest.raises(ValueError):
        k.supervisor.request_owner_recovery(OwnerContext("macro_guard", "test"))


# ── entry safety ────────────────────────────────────────────────────────────
def test_entry_blocked_during_recheck_and_after_refusal_allowed_after_activation(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    heal(venue)
    during = []
    venue.on_listing = lambda: during.append(_entry_probe(k))
    r = owner_request(k)
    venue.on_listing = None
    assert r.status == "ACTIVATED"
    assert during and all(p[0] is None and p[2] == 0 for p in during)
    assert {p[1] for p in during} == {"state=RECOVERY: entries blocked"}
    pos, reason, orders = _entry_probe(k)            # ordinary guards only
    assert pos is None and orders == 0 and reason == 'exact_risk_permission_required'



def test_entry_blocked_when_recovery_refused(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    assert owner_request(k).status == "CONTAINED"
    assert _entry_probe(k)[1:] == ("state=RECOVERY: entries blocked", 0)


# ── Telegram ────────────────────────────────────────────────────────────────
def _tg(k, monkeypatch, update=None):
    from tests.test_owner_recovery_risk_guard import tg_update
    replies = []
    k.notifier.chat_id = "1"
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    k._handle_tg_command("/resume", "https://api.telegram.org/botSECRET-TOKEN",
                         update=update or tg_update())
    return replies


def tg_resume(k):
    from tests.test_owner_recovery_risk_guard import tg_update
    k.notifier.chat_id = "1"
    return k._handle_tg_command("/resume", "https://api.telegram.org/botSECRET-TOKEN",
                                update=tg_update())


def test_telegram_resume_is_guarded_and_audited(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    update = {"update_id": 77, "message": {"message_id": 5, "from": {"id": 1},
                                           "chat": {"id": 1}, "text": "/resume",
                                           "date": int(__import__("time").time())}}
    replies = _tg(k, monkeypatch, update)
    assert journal.kv_get("control_state") == "RECOVERY"
    assert replies == ["🔒 still RECOVERY: CONTAINED NEEDS_OWNER — "
                       "position_unprotected:BTC/USDT"]
    req = json.loads(_events(journal, "owner_recovery_requested")[-1]["detail"])
    # principal: resolved by the kernel from the authenticated Telegram sender
    # (owner-interface-gateway-v1); the sender id and gateway request id stay
    # in the Supervisor audit as metadata
    assert (req["channel"], req["principal"], req["request_ref"]) == ("telegram", "owner", "77:5")
    assert req["meta"]["command"] == "/resume" and req["meta"]["identity"] == "1"
    assert req["meta"]["request_id"].startswith("telegram-")
    assert not any("SECRET-TOKEN" in (r["detail"] or "") for r in journal.query(
        "SELECT detail FROM control_events"))
    heal(venue)
    assert _tg(k, monkeypatch) == ["🙂 ACTIVE — fresh recovery check proved safe."]
    assert _tg(k, monkeypatch) == ["ℹ️ already ACTIVE — nothing changed."]


def test_telegram_listener_keeps_chat_id_authorization(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    k.notifier.configured, k.notifier.token, k.notifier.chat_id = True, "T", "1"
    updates = [{"update_id": 1, "message": {"chat": {"id": 999}, "text": "/resume"}},
               {"update_id": 2, "message": {"chat": {"id": 1}, "text": "/resume",
                                            "from": {"id": 42}}}]

    class R:
        def json(self):
            return {"result": updates}
    monkeypatch.setattr("requests.get", lambda *a, **kw: R())
    monkeypatch.setattr("trader.kernel.time.sleep", lambda _s: None)
    seen = []
    monkeypatch.setattr(k, "_tg_dispatch",
                        lambda msg, base, update: (seen.append(update["update_id"]),
                                                   setattr(k, "_stop", True)))
    k._stop = False
    k._telegram_listener()
    assert seen == [2]                    # the foreign chat never reaches a handler


# ── rollback compatibility ──────────────────────────────────────────────────
@pytest.fixture(scope="module")
def old_kernel_src(tmp_path_factory):
    """51101d0 trader/ package extracted from git history (never modified)."""
    out = tmp_path_factory.mktemp("k51101d0")
    blob = subprocess.run(["git", "-C", str(REPO), "archive", "51101d0", "trader"],
                          capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(blob)).extractall(out, filter="data")
    return out


OLD_STARTUP = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import trader
assert trader.__file__.startswith(sys.argv[1]), trader.__file__
from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.engine.state import ControlStateMachine
from trader.engine.reconcile import reconcile_futures
j = Journal(sys.argv[2])
sm = ControlStateMachine(j)                    # 51101d0 Kernel.__init__ step
out = {"state": sm.state.value, "can_enter": sm.can_enter(),
       "manages_exits": sm.manages_exits()}
class V:
    mutations = []
    def fetch_positions(self):
        return [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                 "entryPrice": 100, "markPrice": 100}]
    def fapiPrivateGetOpenAlgoOrders(self):
        return [{"algoId": "7001", "symbol": "BTCUSDT", "side": "SELL", "reduceOnly": True,
                 "orderType": "STOP_MARKET", "quantity": "1", "triggerPrice": "95"}]
    def fetch_open_orders(self, s): return []
    def create_order(self, *a, **k): self.mutations.append("create_order"); return {"id": "x"}
    def cancel_order(self, *a, **k): self.mutations.append("cancel_order")
    def fapiPrivateDeleteAlgoOrder(self, p): self.mutations.append("cancel_algo")
v = V()
out["boot_reconcile"] = reconcile_futures(v, j)     # 51101d0 boot step
out["mutations"] = v.mutations
ControlStateMachine(j).set(ControlState.ACTIVE, "dashboard")   # old owner resume
out["after_owner_resume"] = j.kv_get("control_state")
print(json.dumps(out))
"""


def _old_startup(src, db):
    proc = subprocess.run([sys.executable, "-c", OLD_STARTUP, str(src), str(db)],
                          capture_output=True, text=True,
                          env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    return proc


def test_rollback_prepare_ready_converts_to_old_compatible_frozen(world, monkeypatch, old_kernel_src):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=unreadable)
    venue.stops = [_stop(algo_id="7001")]
    with journal._tx() as c:
        c.execute("UPDATE trades SET sl_order_id='7001'")
    venue.unreadable = False
    failing = _old_startup(old_kernel_src, journal.db_path)     # RECOVERY persisted
    assert failing.returncode != 0 and "'RECOVERY' is not a valid ControlState" in failing.stderr
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "READY" and r.control_state == "FROZEN"
    assert journal.kv_get("control_state") == "FROZEN"
    assert _transitions(journal)[-1] == ("RECOVERY", "FROZEN", "operator")
    assert _status(journal)["checks"]["entries_safe"] is True
    assert len(_events(journal, "rollback_prepare_result")) == 1
    assert _events(journal, "owner_recovery_requested") == []      # evidence kept separate
    proc = _old_startup(old_kernel_src, journal.db_path)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["state"] == "FROZEN" and out["can_enter"] is False and out["manages_exits"]
    assert out["mutations"] == []
    assert out["after_owner_resume"] == "ACTIVE"
    assert venue.mutations == []


def test_rollback_prepare_from_active_and_supervisor_cannot_reactivate(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert journal.kv_get("control_state") == "ACTIVE"
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "READY" and journal.kv_get("control_state") == "FROZEN"
    k2, _, _ = _kernel(journal, venue, monkeypatch)       # newer kernel reboots
    k2.boot()
    assert journal.kv_get("control_state") == "FROZEN"    # owner hold respected


def test_rollback_prepare_blocked_when_fault_remains(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "BLOCKED" and r.control_state == "RECOVERY"
    assert "position_unprotected:BTC/USDT" in r.reasons
    assert venue.mutations == []


def test_rollback_prepare_refuses_halted(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    ControlStateMachine(journal).set(ControlState.HALTED, "operator", "halt")
    k, _, _ = _kernel(journal, venue, monkeypatch)
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "REFUSED" and r.reasons == ("halted_manual_only",)
    assert journal.kv_get("control_state") == "HALTED"


# ── negative controls: each mutant MUST fail the scenario check ─────────────
def mutant_direct_resume(k):
    k.state_machine.set(ControlState.ACTIVE, "operator", "telegram")


def mutant_no_fresh_pass(k):
    sup = k.supervisor
    status = sup.status()
    status.update(needs_owner=False)
    sup.journal.kv_set(KEY, json.dumps(status))
    sup.state_machine.set_if_current(ControlState.RECOVERY, ControlState.ACTIVE,
                                     "supervisor", "unproved")


def mutant_ignores_concurrent_hold(k):
    sup = k.supervisor
    fresh = sup.pass_once(advance_entry=False)
    if fresh.checks.get("entries_safe"):
        sup.state_machine.set(ControlState.ACTIVE, "supervisor", "blind")


def mutant_reuses_stale_safe(k):
    sup = k.supervisor
    if sup.status().get("safe_to_activate"):
        sup.state_machine.set(ControlState.ACTIVE, "supervisor", "stale proof")
        return
    return owner_request(k)


@pytest.mark.parametrize("check, mutant", [
    (check_fault_remains_contained, mutant_direct_resume),
    (check_fault_remains_contained, mutant_no_fresh_pass),
    (check_concurrent_frozen_wins, mutant_ignores_concurrent_hold),
    (check_stale_safe_cannot_authorize, mutant_reuses_stale_safe),
])
def test_negative_control_mutant_is_caught(world, monkeypatch, check, mutant):
    journal, venue = world
    with pytest.raises(AssertionError):
        check(journal, venue, monkeypatch, mutant)


def test_negative_control_old_telegram_resume_is_caught(world, monkeypatch):
    """The d46d045 /resume body (direct set ACTIVE) fails the guarded check."""
    journal, venue = world

    def old_handler(msg, base, update=None):
        if msg.startswith("/resume"):
            k.state_machine.set(ControlState.ACTIVE, "operator", "telegram")
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    monkeypatch.setattr(k, "_handle_tg_command", old_handler)
    with pytest.raises(AssertionError):
        check_fault_remains_contained_after(journal, k, tg_resume)


def check_fault_remains_contained_after(journal, k, request):
    request(k)
    assert journal.kv_get("control_state") == "RECOVERY"


def test_guarded_telegram_passes_same_check(world, monkeypatch):
    journal, venue = world
    k = _contained(journal, venue, monkeypatch, fault=loosen)
    monkeypatch.setattr("requests.post", lambda *a, **kw: None)
    check_fault_remains_contained_after(journal, k, tg_resume)
