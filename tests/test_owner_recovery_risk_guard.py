"""Risk authority across every activation path (owner, unhalt, FROZEN, cadence).

The kernel fixture wires the real RiskManager and the real Kernel._risk_release;
only the venue equity read is replaced (`venue.equity`, None = read fails).
Baseline: peak 1000, halt_drawdown_pct 20 → equity 700 is an active breach.

Scenario checks take a `mutate(k, monkeypatch)` hook so the same assertions
run against the real code and against mutants (negative controls at bottom).
"""
import inspect
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from trader.core.journal import Journal
from trader.core.types import Action, ControlState, Decision
from trader.engine.risk import RiskManager, RiskRelease
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import KEY, OwnerContext, Supervisor
from tests.test_kernel_boot_recovery import (RISK_CFG, _kernel, _last_event,
                                             _protected, _status, _transitions)
from tests.test_owner_recovery import HookVenue, _events

OWNER = OwnerContext("operator", "test", principal="owner-1", request_ref="unit")
BREACH, CLEAR = 700.0, 900.0          # 30 % and 10 % drawdown from peak 1000


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def _boot(journal, venue, monkeypatch, mutate=None):
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    if mutate:
        mutate(k, monkeypatch)
    k.boot()
    return k


def risk_cycle(k):
    """The kernel cycle's real Risk step (Kernel._risk_step: update + halt)."""
    return k._risk_step()[1]


def risk_halted(journal, venue, monkeypatch, mutate=None):
    k = _boot(journal, venue, monkeypatch, mutate)
    assert journal.kv_get("control_state") == "ACTIVE"
    venue.equity = BREACH
    assert risk_cycle(k)["halt_breached"] is True
    assert journal.kv_get("control_state") == "HALTED"
    return k


def tg(k, monkeypatch, command):
    replies = []
    k.notifier.chat_id = "1"
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    k._handle_tg_command(command, "https://api.telegram.org/botX",
                         update={"update_id": 9, "message": {"message_id": 3,
                                                             "from": {"id": 42}}})
    return replies


def _activations(journal, after=0):
    return [t for t in _transitions(journal, after) if t[1] == "ACTIVE"]


def _delete_risk_state(journal):
    with journal._tx() as c:
        c.execute("DELETE FROM state_kv WHERE key='risk_state'")
    assert journal.kv_get("risk_state") is None


def _risk_snapshot(k):
    return (k.journal.kv_get("risk_state"), k.risk._peak_equity,
            k.risk._day_start_equity, k.risk._day_key)


# ── RiskManager.release_check (the authoritative read-only check) ───────────
def test_release_check_uses_the_halt_rule_and_is_read_only(tmp_path):
    j = Journal(tmp_path / "j.db")
    risk = RiskManager(RISK_CFG, j)
    risk.update_equity(1000.0)
    before = j.kv_get("risk_state")
    assert risk.release_check(BREACH).reason == "risk_halt_active"
    assert risk.release_check(800.0).reason == "risk_halt_active"      # exactly 20 %
    ok = risk.release_check(CLEAR)
    assert ok.allowed and ok.drawdown_pct == 10.0 and ok.peak_equity == 1000.0
    assert risk.release_check(1200.0).allowed                          # new high: no move
    assert j.kv_get("risk_state") == before and risk._peak_equity == 1000.0
    # same rule as the cycle's halt
    assert risk.update_equity(800.0)["halt_breached"] is True


@pytest.mark.parametrize("equity", [None, 0, -5, float("nan"), float("inf"), "x"])
def test_release_check_unreadable_equity_fails_closed(tmp_path, equity):
    risk = RiskManager(RISK_CFG, Journal(tmp_path / "j.db"))
    risk.update_equity(1000.0)
    r = risk.release_check(equity)
    assert not r.allowed and r.reason == "risk_equity_unreadable"


@pytest.mark.parametrize("stored", [None, "not json", '{"peak_equity": null}',
                                    '{"peak_equity": "NaN"}', '{"peak_equity": -1}'])
def test_release_check_missing_or_corrupt_baseline_fails_closed(tmp_path, stored):
    j = Journal(tmp_path / "j.db")
    risk = RiskManager(RISK_CFG, j)
    risk.update_equity(1000.0)          # a healthy in-memory peak is not enough
    if stored is None:
        _delete_risk_state(j)
    else:
        j.kv_set("risk_state", stored)
    r = risk.release_check(CLEAR)
    assert not r.allowed and r.reason == "risk_state_corrupt"


def test_release_check_uses_the_more_conservative_peak(tmp_path):
    j = Journal(tmp_path / "j.db")
    risk = RiskManager(RISK_CFG, j)
    risk.update_equity(1000.0)
    j.kv_set("risk_state", json.dumps({"peak_equity": 1200.0, "day_start_equity": 1000.0,
                                       "day_key": risk._day_key}))
    assert risk.release_check(CLEAR).reason == "risk_halt_active"     # 25 % from 1200


def test_kernel_wires_risk_into_the_supervisor_and_balance_fallback_is_unchanged(world, monkeypatch):
    from trader.kernel import Kernel
    assert "risk_release=self._risk_release" in inspect.getsource(Kernel.__init__)
    journal, venue = world
    k = _boot(journal, venue, monkeypatch)
    with journal._tx() as c:
        c.execute("INSERT INTO equity(ts, equity, balance, open_positions) "
                  "VALUES ('2026-09-27T00:00:00', 1234, 1234, 0)")
    venue.equity = None
    assert k._fetch_balance() == 1234.0            # sizing keeps its fallback
    assert k._risk_release().reason == "risk_equity_unreadable"   # Risk never does


def test_real_equity_read_failure_fails_closed(world, monkeypatch):
    """The unpatched Kernel._fetch_balance_fresh: v3 and ccxt both fail → None."""
    journal, venue = world
    k = _boot(journal, venue, monkeypatch)
    monkeypatch.delattr(k, "_fetch_balance_fresh")          # the real method
    monkeypatch.setattr("trader.core.config.Env.binance_keys",
                        classmethod(lambda cls: ("k", "s")))
    monkeypatch.setattr("requests.get", lambda *a, **kw: (_ for _ in ()).throw(
        OSError("no network in tests")))
    monkeypatch.setattr("trader.kernel.time.sleep", lambda _s: None)
    venue.urls = {}
    assert k._fetch_balance_fresh() is None                  # venue has no fetch_balance
    assert k._risk_release().reason == "risk_equity_unreadable"


# ── A: active drawdown + /unhalt → never ACTIVE ─────────────────────────────
def check_unhalt_blocked_by_active_breach(journal, venue, monkeypatch, mutate=None):
    k = risk_halted(journal, venue, monkeypatch, mutate)
    mark, reads = _last_event(journal), venue.reads
    replies = tg(k, monkeypatch, "/unhalt")
    assert journal.kv_get("control_state") == "HALTED"
    assert _activations(journal, mark) == []
    return k, replies, mark, reads


def test_a_active_drawdown_unhalt_stays_halted(world, monkeypatch):
    journal, venue = world
    k, replies, mark, reads = check_unhalt_blocked_by_active_breach(journal, venue, monkeypatch)
    assert replies == ["🔒 still HALTED: REFUSED  — risk_halt_active"]
    assert _transitions(journal, mark) == []                    # never left HALTED
    assert venue.reads == reads and venue.mutations == []       # no reconciliation
    res = json.loads(_events(journal, "owner_recovery_result")[-1]["detail"])
    assert res["status"] == "REFUSED" and res["reasons"] == ["risk_halt_active"]
    assert res["operation"] == "owner_unhalt"
    assert res["risk_release"]["drawdown_pct"] == 30.0 and res["risk_release"]["allowed"] is False


def test_a2_breach_arising_after_unhalt_intake_blocks_activation(world, monkeypatch):
    """The intake pre-check is advisory; the pass re-asks Risk before the CAS."""
    journal, venue = world
    k = risk_halted(journal, venue, monkeypatch)
    venue.equity = CLEAR                                  # clears at intake …
    venue.on_positions = lambda: setattr(venue, "equity", BREACH)   # … breaches again
    mark = _last_event(journal)
    r = k.supervisor.request_owner_recovery(OWNER, allow_unhalt=True)
    assert r.status == "CONTAINED" and "risk_halt_active" in r.reasons
    assert journal.kv_get("control_state") == "RECOVERY"
    assert _activations(journal, mark) == []
    assert _status(journal)["checks"]["risk_release"] is False
    risk_cycle(k)                                         # the next kernel cycle
    assert journal.kv_get("control_state") == "HALTED"
    assert _transitions(journal)[-1] == ("RECOVERY", "HALTED", "risk_engine")


# ── B: risk HALTED → owner FROZEN → /resume (the FROZEN bypass) ─────────────
def check_frozen_bypass_blocked(journal, venue, monkeypatch, mutate=None):
    k = risk_halted(journal, venue, monkeypatch, mutate)
    tg(k, monkeypatch, "/freeze")
    assert journal.kv_get("control_state") == "FROZEN"
    mark = _last_event(journal)
    replies = tg(k, monkeypatch, "/resume")
    assert _activations(journal, mark) == [], "FROZEN hid an active risk halt"
    assert journal.kv_get("control_state") == "RECOVERY"
    return k, replies


def test_b_frozen_bypass_does_not_activate(world, monkeypatch):
    journal, venue = world
    k, replies = check_frozen_bypass_blocked(journal, venue, monkeypatch)
    assert "risk_halt_active" in replies[-1]
    res = json.loads(_events(journal, "owner_recovery_result")[-1]["detail"])
    assert res["status"] == "CONTAINED" and "risk_halt_active" in res["reasons"]
    assert _entry_blocked(k, "state=RECOVERY: entries blocked")
    risk_cycle(k)
    assert journal.kv_get("control_state") == "HALTED"      # Risk re-asserts


# ── C: unreadable Risk → never ACTIVE ───────────────────────────────────────
def _corrupt(k):
    k.journal.kv_set("risk_state", "{corrupt")


def _missing(k):
    _delete_risk_state(k.journal)


def _gate_raises(k):
    k.risk.release_check = lambda equity: (_ for _ in ()).throw(RuntimeError("risk down"))


@pytest.mark.parametrize("fault, reason", [
    (lambda k: setattr(k.exchange, "equity", None), "risk_equity_unreadable"),
    (_missing, "risk_state_corrupt"),
    (_corrupt, "risk_state_corrupt"),
    (_gate_raises, "risk_state_unreadable"),
    (lambda k: setattr(k.supervisor, "risk_release", None), "risk_gate_unconfigured"),
    (lambda k: setattr(k.supervisor, "risk_release", lambda: {"allowed": True}),
     "risk_state_unreadable"),
], ids=["equity_read_fails", "baseline_missing", "baseline_corrupt", "risk_raises",
        "gate_unconfigured", "untyped_answer"])
def test_c_unreadable_risk_stays_contained(world, monkeypatch, fault, reason):
    check_unreadable_risk_contained(*world, monkeypatch, fault, reason)


def check_unreadable_risk_contained(journal, venue, monkeypatch, fault, reason, mutate=None):
    k = _boot(journal, venue, monkeypatch, mutate)
    owner_freeze = ControlStateMachine(journal).set(ControlState.FROZEN, "operator", "hold")
    assert owner_freeze
    fault(k)
    mark = _last_event(journal)
    r = k.supervisor.request_owner_recovery(OWNER)
    assert _activations(journal, mark) == [], "unreadable Risk treated as safe"
    assert r.status == "CONTAINED" and reason in r.reasons
    assert journal.kv_get("control_state") == "RECOVERY"
    k.supervisor._next_pass = 0
    k.supervisor.cycle()                                   # cadence: same rule
    assert journal.kv_get("control_state") == "RECOVERY"


def test_c_boot_with_unreadable_equity_stays_contained(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    venue.equity = None
    k.boot()
    assert journal.kv_get("control_state") == "RECOVERY"
    assert "risk_equity_unreadable" in _status(journal)["reasons"]


# ── D: a stale Risk clearance is never reused ───────────────────────────────
def check_stale_risk_clearance_not_reused(journal, venue, monkeypatch, mutate=None):
    k = _boot(journal, venue, monkeypatch, mutate)
    ControlStateMachine(journal).set(ControlState.FROZEN, "operator", "hold")
    real_save = k.supervisor._save

    def save(result):
        out = real_save(result)
        if result.stage == "PROVED" and not save.fired:     # Risk cleared, then a hold
            save.fired = True
            ControlStateMachine(journal).set(ControlState.FROZEN, "chat", "late hold")
        return out
    save.fired = False
    monkeypatch.setattr(k.supervisor, "_save", save)
    first = k.supervisor.request_owner_recovery(OWNER)
    assert first.status == "CONTAINED" and journal.kv_get("control_state") == "FROZEN"
    assert _status(journal)["actions"]["risk_release"]["allowed"] is True   # stale clearance
    venue.equity = BREACH                                   # Risk now halts
    stale = _status(journal)
    stale.update(outcome="SAFE", safe_to_activate=True, needs_owner=False)
    journal.kv_set(KEY, json.dumps(stale))
    mark = _last_event(journal)
    second = k.supervisor.request_owner_recovery(OWNER)
    assert _activations(journal, mark) == [], "stale Risk clearance authorized ACTIVE"
    assert second.status == "CONTAINED" and "risk_halt_active" in second.reasons
    return k


def test_d_stale_risk_clearance_cannot_authorize(world, monkeypatch):
    check_stale_risk_clearance_not_reused(*world, monkeypatch)


# ── E: the halt genuinely clears → guarded ACTIVE ───────────────────────────
def test_e_risk_clears_and_recovery_safe_activates(world, monkeypatch):
    journal, venue = world
    k = risk_halted(journal, venue, monkeypatch)
    before = _risk_snapshot(k)
    venue.equity = CLEAR
    mark = _last_event(journal)
    replies = tg(k, monkeypatch, "/unhalt")
    assert replies == ["🙂 ACTIVE — fresh recovery check proved safe."]
    assert _transitions(journal, mark) == [("HALTED", "RECOVERY", "operator"),
                                           ("RECOVERY", "ACTIVE", "supervisor")]
    st = _status(journal)
    assert st["checks"]["risk_release"] is True and st["checks"]["entries_safe"] is True
    assert st["actions"]["risk_release"]["drawdown_pct"] == 10.0
    assert _risk_snapshot(k) == before                      # peak 1000 untouched


def test_e2_frozen_path_activates_once_risk_clears(world, monkeypatch):
    journal, venue = world
    k, _ = check_frozen_bypass_blocked(journal, venue, monkeypatch)
    venue.equity = CLEAR
    r = k.supervisor.request_owner_recovery(OWNER)
    assert r.status == "ACTIVATED" and journal.kv_get("control_state") == "ACTIVE"


# ── F: Risk clears, then a newer HALTED → never ACTIVE ──────────────────────
def test_f_risk_clears_but_newer_halted_wins(world, monkeypatch):
    journal, venue = world
    k = risk_halted(journal, venue, monkeypatch)
    venue.equity = CLEAR
    real = k.supervisor.risk_release
    calls = []

    def release():
        out = real()
        calls.append(out)
        if len(calls) == 2:                  # the pass's check (1st is the intake pre-check)
            ControlStateMachine(journal).set(ControlState.HALTED, "chat", "newer halt")
        return out
    k.supervisor.risk_release = release
    mark = _last_event(journal)
    r = k.supervisor.request_owner_recovery(OWNER, allow_unhalt=True)
    assert all(c.allowed for c in calls) and len(calls) == 2
    assert journal.kv_get("control_state") == "HALTED"
    assert r.status == "CONTAINED" and _activations(journal, mark) == []


# ── G: previously approved entry during a failed unhalt → no order ──────────
def _entry_blocked(k, reason):
    d = Decision("d-approved", "c-1", "ETH/USDT", Action.BUY, .7, .2, .8, [], [])
    before = [m for m in k.exchange.mutations if m[0] == "create_order"]
    pos = k.executor.open(d, 1.0, 1.0, 90.0, 110.0, "s", "s")
    after = [m for m in k.exchange.mutations if m[0] == "create_order"]
    return pos is None and d.skip_reason == reason and after == before


@pytest.mark.parametrize("path", ["unhalt", "frozen_resume"])
def test_g_previously_approved_entry_during_failed_unhalt_submits_nothing(world, monkeypatch, path):
    journal, venue = world
    k = _boot(journal, venue, monkeypatch)
    sizing = k.risk.check_entry(ControlState.ACTIVE, "ETH/USDT", 100.0, 1.0, 0.02,
                                [], 1000.0, 50, "futures")
    assert sizing.ok                                  # Risk approved while ACTIVE
    venue.equity = BREACH
    risk_cycle(k)                                     # drawdown → HALTED
    mark = _last_event(journal)
    if path == "unhalt":
        assert k.supervisor.request_owner_recovery(OWNER, allow_unhalt=True).status == "REFUSED"
        expect = "state=HALTED"
    else:
        tg(k, monkeypatch, "/freeze")
        assert k.supervisor.request_owner_recovery(OWNER).status == "CONTAINED"
        expect = "state=RECOVERY: entries blocked"
    assert _activations(journal, mark) == []
    assert journal.kv_get("control_state") != "ACTIVE"
    d = Decision("d-approved", "c-1", "ETH/USDT", Action.BUY, .7, .2, .8, [], [])
    pos = k.executor.open(d, sizing.amount, 1.0, 98.0, 103.0, "s", "s")
    assert pos is None and d.skip_reason == expect
    assert [m for m in venue.mutations if m[0] == "create_order"] == []
    # Known, recorded separately (LUFFY-ENTRY-FENCE-SIDE-EFFECT-V1): leverage
    # is still applied before the final fence even though no order is sent.
    assert [m[0] for m in venue.mutations] == ["set_leverage"]


# ── H: cadence cannot bypass an active Risk halt ────────────────────────────
def check_cadence_respects_risk(journal, venue, monkeypatch, mutate=None):
    k, _ = check_frozen_bypass_blocked(journal, venue, monkeypatch, mutate)
    st = _status(journal)
    assert st["containment_owned"] is True and st["needs_owner"] is False
    mark = _last_event(journal)
    for _ in range(3):
        k.supervisor._next_pass = 0
        res = k.supervisor.cycle()
        assert res is not None and "risk_halt_active" in res.reasons
    assert _activations(journal, mark) == [], "cadence activated through a Risk halt"
    assert journal.kv_get("control_state") == "RECOVERY"
    return k


def test_h_cadence_cannot_bypass_active_risk_halt(world, monkeypatch):
    journal, venue = world
    k = check_cadence_respects_risk(journal, venue, monkeypatch)
    venue.equity = CLEAR                         # one rule: now cadence may activate
    k.supervisor._next_pass = 0
    assert k.supervisor.cycle().actions.get("activated") is True
    assert journal.kv_get("control_state") == "ACTIVE"


def test_h2_boot_pass_cannot_activate_through_breach(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)        # baseline peak 1000
    venue.equity = BREACH
    k.boot()
    assert journal.kv_get("control_state") == "RECOVERY"
    assert "risk_halt_active" in _status(journal)["reasons"]


# ── I: owner recovery never rewrites Risk baselines ─────────────────────────
def test_i_owner_recovery_preserves_risk_baseline(world, monkeypatch):
    journal, venue = world
    k = risk_halted(journal, venue, monkeypatch)
    before = _risk_snapshot(k)
    assert json.loads(before[0])["peak_equity"] == 1000.0
    k.supervisor.request_owner_recovery(OWNER, allow_unhalt=True)       # refused
    tg(k, monkeypatch, "/freeze")
    k.supervisor.request_owner_recovery(OWNER)                          # contained
    k.supervisor._next_pass = 0
    k.supervisor.cycle()                                                # cadence
    k.supervisor.prepare_rollback(OWNER)
    assert _risk_snapshot(k) == before
    venue.equity = CLEAR
    k.supervisor.request_owner_recovery(OWNER)                          # activates
    assert journal.kv_get("control_state") == "ACTIVE"
    assert _risk_snapshot(k) == before


# ── J: repeated requests stay audited and idempotent ────────────────────────
def test_j_repeated_unhalts_audited_and_idempotent(world, monkeypatch):
    journal, venue = world
    k = risk_halted(journal, venue, monkeypatch)
    mark, reads = _last_event(journal), venue.reads
    results = [k.supervisor.request_owner_recovery(OWNER, allow_unhalt=True) for _ in range(3)]
    assert [r.status for r in results] == ["REFUSED"] * 3
    assert all(r.reasons == ("risk_halt_active",) for r in results)
    assert len({r.request_event_id for r in results}) == 3
    req = [e for e in _events(journal, "owner_recovery_requested") if e["id"] > mark]
    res = [json.loads(e["detail"]) for e in _events(journal, "owner_recovery_result")
           if e["id"] > mark]
    assert len(req) == len(res) == 3
    assert [d["request_event_id"] for d in res] == [r.request_event_id for r in results]
    assert all(d["risk_release"]["reason"] == "risk_halt_active" for d in res)
    assert _transitions(journal, mark) == [] and venue.reads == reads
    assert venue.mutations == []


# ── rollback: FROZEN readiness does not need an ACTIVE release ──────────────
def test_rollback_with_active_breach_is_frozen_never_active(world, monkeypatch):
    journal, venue = world
    k, _ = check_frozen_bypass_blocked(journal, venue, monkeypatch)    # RECOVERY, breach
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "READY" and journal.kv_get("control_state") == "FROZEN"
    k.supervisor._next_pass = 0
    assert k.supervisor.cycle() is None                    # nothing activates FROZEN
    k.supervisor.pass_once()
    assert journal.kv_get("control_state") == "FROZEN"
    risk_cycle(k)
    assert journal.kv_get("control_state") == "HALTED"     # Risk still re-halts


# ── negative controls: each mutant removes one guard and MUST fail ──────────
def FORGED_OK():
    """A forged allow. V2: it also carries an always-pass verifier, so the
    mutant removes the CAS-time Risk revalidation too and still exercises the
    guard under test rather than being stopped by the later one."""
    return RiskRelease(True, "risk_release_ok", authoritative=True,
                       verify=lambda _proof: nullcontext(None))


def mutant_no_risk_check(k, monkeypatch):
    monkeypatch.setattr(k.supervisor, "_risk_now",
                        lambda: FORGED_OK())


def mutant_unreadable_is_safe(k, monkeypatch):
    real = RiskManager.release_check

    def release_check(self, equity):
        out = real(self, equity)
        if out.reason in ("risk_equity_unreadable", "risk_state_corrupt"):
            return FORGED_OK()
        return out
    monkeypatch.setattr(RiskManager, "release_check", release_check)
    real_now = Supervisor._risk_now

    def _risk_now(self):
        out = real_now(self)
        return FORGED_OK() if out.reason in (
            "risk_state_unreadable", "risk_gate_unconfigured") else out
    monkeypatch.setattr(Supervisor, "_risk_now", _risk_now)


def mutant_frozen_bypass(k, monkeypatch):
    """Risk checked only at HALTED-unhalt intake, not at activation."""
    sup, real_pass, real_now = k.supervisor, k.supervisor._pass_once, k.supervisor._risk_now
    flag = []

    def _pass_once(**kw):
        flag.append(1)
        try:
            return real_pass(**kw)
        finally:
            flag.pop()
    monkeypatch.setattr(sup, "_pass_once", _pass_once)
    monkeypatch.setattr(sup, "_risk_now", lambda: FORGED_OK()
                        if flag else real_now())


def mutant_cadence_skips_risk(k, monkeypatch):
    sup, real_cycle, real_now = k.supervisor, k.supervisor.cycle, k.supervisor._risk_now
    flag = []

    def cycle():
        flag.append(1)
        try:
            return real_cycle()
        finally:
            flag.pop()
    monkeypatch.setattr(sup, "cycle", cycle)
    monkeypatch.setattr(sup, "_risk_now", lambda: FORGED_OK()
                        if flag else real_now())


def mutant_stale_risk_reuse(k, monkeypatch):
    real_now, cache = k.supervisor._risk_now, []

    def _risk_now():
        if not cache:
            out = real_now()
            if out.allowed:
                cache.append(out)
            return out
        return cache[0]
    monkeypatch.setattr(k.supervisor, "_risk_now", _risk_now)


def _check_unreadable(journal, venue, monkeypatch, mutate):
    root = Path(journal.db_path).parent
    for i, (fault, reason) in enumerate([(lambda k: setattr(k.exchange, "equity", None), "risk_equity_unreadable"),
                          (_corrupt, "risk_state_corrupt"),
                          (_gate_raises, "risk_state_unreadable")]):
        check_unreadable_risk_contained(Journal(root / f"u{i}.db"), HookVenue(),
                                        monkeypatch, fault, reason, mutate)


@pytest.mark.parametrize("check, mutant", [
    (check_unhalt_blocked_by_active_breach, mutant_no_risk_check),
    (check_frozen_bypass_blocked, mutant_no_risk_check),
    (_check_unreadable, mutant_unreadable_is_safe),
    (check_frozen_bypass_blocked, mutant_frozen_bypass),
    (check_cadence_respects_risk, mutant_cadence_skips_risk),
    (check_stale_risk_clearance_not_reused, mutant_stale_risk_reuse),
], ids=["omit_risk_check:unhalt", "omit_risk_check:frozen", "unreadable_as_safe",
        "frozen_bypass", "cadence_without_risk", "stale_risk_reuse"])
def test_negative_control_mutant_is_caught(world, monkeypatch, check, mutant):
    journal, venue = world
    with pytest.raises(AssertionError):
        check(journal, venue, monkeypatch, mutant)


@pytest.mark.parametrize("check", [check_unhalt_blocked_by_active_breach,
                                   check_frozen_bypass_blocked, _check_unreadable,
                                   check_cadence_respects_risk,
                                   check_stale_risk_clearance_not_reused])
def test_checks_pass_on_real_code(world, monkeypatch, check):
    journal, venue = world
    check(journal, venue, monkeypatch, None)
