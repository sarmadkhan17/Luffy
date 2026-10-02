"""LUFFY-ACTIVATION-AND-RISK-BASELINE-HARDENING-V2.

Six blockers on top of V1:
1. a Risk release is a proof bound to the exact Risk identity, revalidated
   and held through the final ACTIVE compare-and-set (every activation path);
2. first initialization is proven by a durable marker written atomically with
   the first baseline — equity rows prove nothing;
3. every corruption detection latches (memory + durable); only repair clears;
4. repair_baseline validates input, reads history authoritatively, serializes
   validation with persistence and commits state + audit atomically;
5. an AST inventory of ACTIVE setters (tests/active_setter_inventory.py);
6. rollback READY needs a verified (repaired) Risk baseline.

Real RiskManager, Kernel._risk_step/cycle/_macro_step/owner_resume and
Supervisor; only the venue (and its equity read) is a double. Scenario checks
take `mutate` so the negative controls (bottom) run the same assertions.
"""
import json
import inspect
import sqlite3
import subprocess
import sys
import threading
import textwrap
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState, MarketType
from trader.engine.control_fence import latest_intent_event_id
from trader.engine.risk import RiskManager, RiskRelease
from trader.engine.supervisor import Supervisor
from tests.active_setter_inventory import load
from tests.test_activation_risk_baseline import (
    BREACH, CLEAR, NEW_HIGH, OWNER, _activations, _macro_frozen, _spot, _state)
from tests.test_kernel_boot_recovery import (RISK_CFG, _kernel, _last_event,
                                             _protected, _status, _transitions)
from tests.test_owner_recovery import HookVenue, _events, old_kernel_src  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
#: _kernel() patches threading.Thread (the shared module attribute) for boot
_Thread = threading.Thread
MARKER, LATCH = RiskManager._MARKER_KEY, RiskManager._LATCH_KEY


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def _details_after(journal, mark):
    return " ".join(r["detail"] or "" for r in journal.query(
        "SELECT detail FROM control_events WHERE id>?", (mark,)))


# ═══ 1. Risk release proof identity ═════════════════════════════════════════
def _risk(tmp_path, peak=1000.0):
    j = Journal(tmp_path / "r.db")
    r = RiskManager(RISK_CFG, j)
    r.update_equity(peak)
    return j, r


def _held(proof):
    with proof.verify(proof) as (refusal, _conn):
        return refusal


def test_proof_carries_exact_identity(tmp_path):
    j, r = _risk(tmp_path)
    p = r.release_check(CLEAR)
    assert p.allowed and p.authoritative and p.identity == r._identity
    assert p.generation == r._generation and p.state_digest and p.verify is not None
    d = p.as_dict()
    assert {"generation", "identity", "state_digest", "authoritative"} <= set(d)
    assert _held(p) is None                                   # unchanged: holds
    assert r.release_check(CLEAR, authoritative=False).reason == \
        "risk_equity_not_authoritative"
    assert r.release_check(None).reason == "risk_equity_unreadable"
    assert r.release_check(True).reason == "risk_equity_unreadable"
    assert r.release_check(BREACH).verify is None             # a refusal is no proof


@pytest.mark.parametrize("change, reason", [
    (lambda j, r: r.update_equity(1000.0), "risk_proof_superseded"),     # re-evaluation
    (lambda j, r: r.update_equity(850.0), "risk_proof_superseded"),      # new drawdown
    (lambda j, r: r.update_equity(NEW_HIGH), "risk_proof_state_changed"),  # new baseline
    (lambda j, r: j.kv_set("risk_state", json.dumps(
        {"peak_equity": 1000.0, "day_start_equity": 999.0, "day_key": "x"})),
     "risk_proof_state_changed"),                                        # external write
    (lambda j, r: j.kv_set("risk_state", "{corrupt"), "risk_state_corrupt"),
    (lambda j, r: setattr(r, "release_max_age_s", -1), "risk_proof_stale"),
], ids=["reevaluate", "drawdown", "new_baseline", "external_write", "corrupt", "stale"])
def test_proof_refused_after_any_risk_change(tmp_path, change, reason):
    j, r = _risk(tmp_path)
    p = r.release_check(CLEAR)
    change(j, r)
    assert _held(p) == reason
    if reason == "risk_state_corrupt":
        assert r.baseline_status == "corrupt" and j.kv_get(LATCH)


@pytest.mark.parametrize("reeval", [
    lambda r: r.release_check(BREACH),                    # refused (halt)
    lambda r: r.release_check(None),                      # unreadable
    lambda r: r.release_check(CLEAR, authoritative=False),
    lambda r: r.release_check(CLEAR),                     # allowed, newer
], ids=["refused", "unreadable", "not_authoritative", "allowed"])
def test_any_release_check_supersedes_older_proofs(tmp_path, reeval):
    j, r = _risk(tmp_path)
    p = r.release_check(CLEAR)
    reeval(r)
    assert _held(p) == "risk_proof_superseded"


def test_proof_from_another_risk_instance_refused(tmp_path):
    j, r = _risk(tmp_path)
    other = RiskManager(RISK_CFG, j)
    assert other.hold_release is not None
    p = r.release_check(CLEAR)
    with other.hold_release(p) as (refusal, _conn):
        assert refusal == "risk_proof_identity_mismatch"


# Every activation path: the Risk proof is taken, then Risk changes, then CAS.
def _between_proof_and_cas(k, monkeypatch, action, fire_on):
    real, calls = k.supervisor.risk_release, []

    def release():
        proof = real()
        calls.append(proof)
        if len(calls) == fire_on and action is not None:
            action()
        return proof
    monkeypatch.setattr(k.supervisor, "risk_release", release)
    return calls


def _path_owner_recovery(journal, venue, monkeypatch, mutate):
    k = _boot_active(journal, venue, monkeypatch, mutate)
    k.state_machine.set(ControlState.FROZEN, "operator", "hold")
    return k, lambda: k.owner_resume(OWNER), 1


def _path_cadence(journal, venue, monkeypatch, mutate):
    k = _boot_active(journal, venue, monkeypatch, mutate)
    k.supervisor.trigger("test_containment")
    assert _state(journal) == "RECOVERY"

    def run():
        k.supervisor._next_pass = 0
        return k.supervisor.cycle()
    return k, run, 1


def _path_boot(journal, venue, monkeypatch, mutate):
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    if mutate:
        mutate(k, monkeypatch)
    return k, k.boot, 1


def _path_macro_futures(journal, venue, monkeypatch, mutate):
    k, _ = _macro_frozen(journal, venue, monkeypatch, mutate)
    return k, lambda: k._macro_step({"active": False}), 2   # intake read, then the pass


def _path_spot_owner(journal, venue, monkeypatch, mutate):
    k = _spot(journal, venue, monkeypatch, mutate)
    k.state_machine.set(ControlState.FROZEN, "operator", "hold")
    return k, lambda: k.owner_resume(OWNER), 1


def _path_spot_macro(journal, venue, monkeypatch, mutate):
    k, _ = _macro_frozen(journal, venue, monkeypatch, mutate, MarketType.SPOT)
    return k, lambda: k._macro_step({"active": False}), 1


def _boot_active(journal, venue, monkeypatch, mutate):
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    if mutate:
        mutate(k, monkeypatch)
    k.boot()
    assert _state(journal) == "ACTIVE"
    return k


PATHS = {"owner_recovery": _path_owner_recovery, "cadence": _path_cadence,
         "boot": _path_boot, "macro_futures": _path_macro_futures,
         "spot_owner": _path_spot_owner, "spot_macro": _path_spot_macro}


def _act_corrupt(k):
    k.journal.kv_set("risk_state", "{corrupt")


def _act_new_baseline(k):
    k.exchange.equity = NEW_HIGH
    k._risk_step()


def _act_drawdown(k):
    k.exchange.equity = 850.0
    k._risk_step()


def _act_reevaluate(k):
    k._risk_step()


def _act_refused_reevaluation(k):
    assert k.risk.release_check(BREACH).reason == "risk_halt_active"


def _act_unreadable_reevaluation(k):
    assert k.risk.release_check(None).reason == "risk_equity_unreadable"


ACTIONS = {"corrupt": (_act_corrupt, "risk_state_corrupt"),
           "refused_reevaluation": (_act_refused_reevaluation, "risk_proof_superseded"),
           "unreadable_reevaluation": (_act_unreadable_reevaluation, "risk_proof_superseded"),
           "new_baseline": (_act_new_baseline, "risk_proof_state_changed"),
           "drawdown": (_act_drawdown, "risk_proof_superseded"),
           "reevaluate": (_act_reevaluate, "risk_proof_superseded")}


def check_risk_change_before_cas_refused(journal, venue, monkeypatch, mutate=None,
                                         path="owner_recovery", action="reevaluate"):
    k, run, fire_on = PATHS[path](journal, venue, monkeypatch, mutate)
    act, reason = ACTIONS[action]
    mark = _last_event(journal)
    calls = _between_proof_and_cas(k, monkeypatch, lambda: act(k), fire_on)
    run()
    assert len(calls) >= fire_on, "the path never asked Risk"
    assert _activations(journal, mark) == [], f"{path}: stale Risk proof reached ACTIVE"
    assert _state(journal) != "ACTIVE"
    return k, reason, mark


@pytest.mark.parametrize("action", sorted(ACTIONS))
@pytest.mark.parametrize("path", sorted(PATHS))
def test_risk_change_between_proof_and_cas_refused(world, monkeypatch, path, action):
    journal, venue = world
    k, reason, mark = check_risk_change_before_cas_refused(
        *world, monkeypatch, path=path, action=action)
    assert reason in _details_after(journal, mark), (path, _details_after(journal, mark))
    if action == "corrupt":
        assert k.risk.baseline_status == "corrupt" and journal.kv_get(LATCH)


@pytest.mark.parametrize("path", sorted(PATHS))
def test_unchanged_risk_proof_activates(world, monkeypatch, path):
    journal, venue = world
    k, run, fire_on = PATHS[path](journal, venue, monkeypatch, None)
    mark = _last_event(journal)
    _between_proof_and_cas(k, monkeypatch, None, fire_on)
    run()
    assert _state(journal) == "ACTIVE"
    assert len(_activations(journal, mark)) == 1


def test_refused_stale_proof_then_fresh_proof_activates(world, monkeypatch):
    journal, venue = world
    k, run, _ = _path_cadence(journal, venue, monkeypatch, None)
    _between_proof_and_cas(k, monkeypatch, lambda: _act_reevaluate(k), 1)
    run()
    assert _state(journal) == "RECOVERY"
    assert "risk_proof_superseded" in _status(journal)["reasons"]
    run()                                             # next pass: a new, unchanged proof
    assert _state(journal) == "ACTIVE"


def test_concurrent_risk_step_serialized_after_the_cas(world, monkeypatch):
    """A cycle's _risk_step racing the ACTIVE CAS waits for it (Risk lock held
    through the CAS), then evaluates the breach and HALTs: never a Risk update
    landing between the proof's revalidation and the persisted ACTIVE."""
    journal, venue = world
    k = _boot_active(journal, venue, monkeypatch, None)
    k.state_machine.set(ControlState.FROZEN, "operator", "hold")
    real, seen = k.state_machine._set_fenced, {}

    def set_fenced(new, actor, detail, *, conn=None):
        if new == ControlState.ACTIVE and "thread" not in seen:
            venue.equity = BREACH
            t = _Thread(target=k._risk_step, daemon=True)
            seen["thread"] = t
            t.start()
            t.join(0.3)
            seen["blocked_during_cas"] = t.is_alive()
            seen["gen_during_cas"] = k.risk._generation
        return real(new, actor, detail, conn=conn)
    monkeypatch.setattr(k.state_machine, "_set_fenced", set_fenced)
    k.owner_resume(OWNER)
    seen["thread"].join(10)
    assert seen["blocked_during_cas"] is True, "Risk update was not serialized"
    assert _transitions(journal)[-2:] == [("RECOVERY", "ACTIVE", "supervisor"),
                                          ("ACTIVE", "HALTED", "risk_engine")]
    assert _state(journal) == "HALTED"


# ═══ 2. durable first-initialization marker, full Kernel.cycle ══════════════
def _cycle_kernel(journal, venue, monkeypatch, market):
    """A real Kernel.cycle over a real journal; only the scan inputs are empty."""
    saved, venue.equity = venue.equity, None          # _kernel() must not seed
    k, _, _ = _kernel(journal, venue, monkeypatch)
    venue.equity = saved
    k.market_type = market
    k.cfg["timeframes"]["execution"] = "15m"
    k._drain_close_requests = lambda: 0
    k.macro_guard = NS(check=lambda: {"active": False})
    k.news_guard = NS(check=lambda: {"active": False})
    k._funding_map = k._oi_map = lambda: {}
    k._refresh_btc_context = lambda: None
    k._scan_symbols = lambda: []
    k._universe_frames = lambda _s: {}
    k._attention_call = lambda *a, **kw: None
    k._manage_orphan_positions = lambda _s: 0
    k._maybe_resolve_outcomes = lambda: None
    k._record_excursions = lambda: None
    k.heartbeat = NS(beat=lambda *_a: None)
    return k


def _equity_rows(journal):
    return int(journal.query("SELECT COUNT(*) AS n FROM equity")[0]["n"])


BAD_READS = {"unreadable": None, "nan": float("nan"), "zero": 0.0, "negative": -5.0,
             "text": "1000", "bool": True, "inf": float("inf")}


def check_full_cycle_initialization(journal, venue, monkeypatch, mutate=None,
                                    market=MarketType.FUTURES):
    _protected(journal, venue)
    k = _cycle_kernel(journal, venue, monkeypatch, market)
    if mutate:
        mutate(k, monkeypatch)
    if market == MarketType.FUTURES:
        venue.equity = None
        k.boot()
        assert _state(journal) == "RECOVERY"
        assert "risk_equity_unreadable" in _status(journal)["reasons"]
    for label, bad in BAD_READS.items():            # failed first attempts, with telemetry
        monkeypatch.setattr(k, "_fetch_balance_fresh", lambda b=bad: b, raising=False)
        out = k.cycle()
        assert out["dd_pct"] is None, label
        assert journal.kv_get("risk_state") is None and journal.kv_get(MARKER) is None, label
        assert k.risk.baseline_status == "uninitialized", label
    assert _equity_rows(journal) >= 1                 # cycle telemetry was recorded
    # reboot between failed attempts: telemetry rows are not a prior baseline
    k = _cycle_kernel(journal, venue, monkeypatch, market)
    if mutate:
        mutate(k, monkeypatch)
    assert k.risk.baseline_status == "uninitialized", "failed first attempt read as corrupt"
    venue.equity = None
    k.cycle()
    assert _events(journal, "risk_state_corrupt") == [], "false corruption from telemetry"
    venue.equity = 1000.0                              # later valid read: initialize
    monkeypatch.setattr(k, "_fetch_balance_fresh", lambda: venue.equity, raising=False)
    out = k.cycle()
    assert out["dd_pct"] == 0.0
    assert json.loads(journal.kv_get("risk_state"))["peak_equity"] == 1000.0
    assert json.loads(journal.kv_get(MARKER))["established"] is True
    if market == MarketType.FUTURES:
        k.cycle()                                     # cadence pass activates
    assert _state(journal) == "ACTIVE"
    assert _events(journal, "risk_state_corrupt") == []
    # reboot after success: loads, marker kept
    k = _cycle_kernel(journal, venue, monkeypatch, market)
    assert k.risk.baseline_status == "ok" and k.risk._peak_equity == 1000.0
    return k


@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_full_cycle_first_init_matrix(world, monkeypatch, market):
    journal, venue = world
    k = check_full_cycle_initialization(*world, monkeypatch, market=market)
    marker = journal.kv_get(MARKER)
    k.cycle()
    assert journal.kv_get(MARKER) == marker            # written once, never rewritten


@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_marker_present_and_state_corrupt_fails_closed_without_reseed(world, monkeypatch,
                                                                     market):
    journal, venue = world
    check_full_cycle_initialization(*world, monkeypatch, market=market)
    for blob in ("{corrupt", ""):
        journal.kv_set("risk_state", blob)
        k = _cycle_kernel(journal, venue, monkeypatch, market)       # reboot
        assert k.risk.baseline_status == "corrupt"
        venue.equity = 700.0
        mark = _last_event(journal)
        for _ in range(2):
            k.cycle()
            k.owner_resume(OWNER)
        assert journal.kv_get("risk_state") == blob, "corrupt state reseeded"
        assert _activations(journal, mark) == []
        assert _state(journal) in ("FROZEN", "RECOVERY")
        venue.equity = 1000.0


@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_marker_alone_proves_a_lost_baseline_after_restart(world, monkeypatch, market):
    """No latch, no memory: only the marker distinguishes this from a first run."""
    journal, venue = world
    check_full_cycle_initialization(*world, monkeypatch, market=market)
    with journal._tx() as c:
        c.execute("DELETE FROM state_kv WHERE key IN ('risk_state', ?)", (LATCH,))
        c.execute("DELETE FROM equity")               # no telemetry either
    assert journal.kv_get(MARKER) and journal.kv_get(LATCH) is None
    k = _cycle_kernel(journal, venue, monkeypatch, market)        # restart
    assert k.risk.baseline_status == "corrupt"
    assert k.risk.corrupt_detail["cause"] == "risk_state_missing"
    venue.equity = 700.0
    mark = _last_event(journal)
    k.cycle()
    assert journal.kv_get("risk_state") is None, "lost baseline re-seeded as a first run"
    assert _activations(journal, mark) == [] and _state(journal) == "FROZEN"


def test_unreadable_risk_state_contains_an_active_kernel(world, monkeypatch):
    journal, venue = world
    k = _boot_active(journal, venue, monkeypatch, None)
    real = journal.kv_get
    monkeypatch.setattr(journal, "kv_get", lambda key, d=None: (_ for _ in ()).throw(
        sqlite3.OperationalError("disk I/O")) if key == "risk_state" else real(key, d))
    mark = _last_event(journal)
    assert k._risk_step()[1]["risk_state"] == "unreadable"
    assert _transitions(journal, mark) == [("ACTIVE", "FROZEN", "risk_engine")]
    assert k.owner_resume(OWNER).status == "CONTAINED"
    assert _activations(journal, mark) == []


def test_empty_journal_without_marker_initializes_even_with_equity_rows(tmp_path):
    j = Journal(tmp_path / "j.db")
    for i in range(5):
        j.log_equity(0.0, 0.0, 0)                       # failed-first-cycle telemetry
        with j._tx() as c:
            c.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?,?)",
                      (f"2026-09-2{i}T00:00:00+00:00", 1500.0, 1500.0, 0))
    r = RiskManager(RISK_CFG, j)
    assert r.baseline_status == "uninitialized"
    assert r.update_equity(1000.0)["risk_state"] == "ok"
    assert j.kv_get(MARKER) and json.loads(j.kv_get("risk_state"))["peak_equity"] == 1000.0


@pytest.mark.parametrize("fail_key", [MARKER, "risk_state"])
def test_first_baseline_and_marker_are_atomic(tmp_path, fail_key):
    j = Journal(tmp_path / "j.db")
    with j._tx() as c:
        c.execute(f"CREATE TRIGGER fail_{fail_key} BEFORE INSERT ON state_kv "
                  f"WHEN NEW.key='{fail_key}' BEGIN SELECT RAISE(ABORT, 'disk'); END")
    r = RiskManager(RISK_CFG, j)
    st = r.update_equity(1000.0)
    assert st["risk_state"] == "uninitialized"
    assert j.kv_get("risk_state") is None and j.kv_get(MARKER) is None
    assert r._peak_equity is None and r.baseline_status == "uninitialized"
    with j._tx() as c:
        c.execute(f"DROP TRIGGER fail_{fail_key}")
    assert RiskManager(RISK_CFG, j).baseline_status == "uninitialized"   # not corrupt
    assert r.update_equity(1000.0)["risk_state"] == "ok" and j.kv_get(MARKER)


def test_legacy_valid_state_without_marker_is_ok_and_backfilled(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.kv_set("risk_state", json.dumps({"peak_equity": 1000.0, "day_start_equity": 990.0,
                                       "day_key": "2026-09-26"}))
    r = RiskManager(RISK_CFG, j)
    assert r.baseline_status == "ok" and j.kv_get(MARKER) is None
    r.update_equity(950.0)
    assert json.loads(j.kv_get(MARKER))["established"] is True


def test_unreadable_risk_state_fails_closed_without_latch(tmp_path, monkeypatch):
    j, r = _risk(tmp_path)
    real = j.kv_get
    monkeypatch.setattr(j, "kv_get", lambda k, d=None: (_ for _ in ()).throw(
        sqlite3.OperationalError("locked")) if k == "risk_state" else real(k, d))
    assert r.update_equity(1000.0)["risk_state"] == "unreadable"
    assert r.release_check(CLEAR).reason == "risk_state_unreadable"
    monkeypatch.setattr(j, "kv_get", real)
    assert r.baseline_status != "corrupt" and j.kv_get(LATCH) is None
    assert r.release_check(CLEAR).allowed


# ═══ 3. corruption latch ════════════════════════════════════════════════════
PLAUSIBLE = json.dumps({"peak_equity": 1000.0, "day_start_equity": 1000.0,
                        "day_key": "2026-09-27"})


def check_release_check_latches(journal, venue, monkeypatch, mutate=None):
    """Corruption seen first by release_check → plausible replacement → refused."""
    k = _boot_active(journal, venue, monkeypatch, mutate)
    k.state_machine.set(ControlState.FROZEN, "operator", "hold")
    journal.kv_set("risk_state", "{corrupt")
    assert k.risk.release_check(CLEAR).reason == "risk_state_corrupt"
    journal.kv_set("risk_state", PLAUSIBLE)            # an external "fix"
    mark = _last_event(journal)
    assert k.risk.release_check(CLEAR).reason == "risk_state_corrupt", "latch cleared"
    assert k.risk.update_equity(CLEAR)["risk_state"] == "corrupt"
    k.owner_resume(OWNER)                              # futures owner recovery
    k.supervisor._next_pass = 0
    k.supervisor.cycle()                               # cadence
    k.market_type = MarketType.SPOT
    k.state_machine.set(ControlState.FROZEN, "operator", "hold")
    k.owner_resume(OWNER)                              # spot owner release
    k.market_type = MarketType.FUTURES
    assert _activations(journal, mark) == [], "plausible replacement cleared the latch"
    return k, mark


def test_release_check_latch_survives_replacement_until_explicit_repair(world, monkeypatch):
    journal, venue = world
    k, mark = check_release_check_latches(*world, monkeypatch)
    assert journal.kv_get(LATCH)
    assert len(_events(journal, "risk_state_corrupt")) == 1
    k2, _, _ = _kernel(journal, venue, monkeypatch)    # restart: durable latch holds
    assert k2.risk.baseline_status == "corrupt"
    assert k2.risk.release_check(CLEAR).reason == "risk_state_corrupt"
    assert not k.risk.check_entry(ControlState.ACTIVE, "ETH/USDT", 100.0, 1.0, .02, [],
                                  CLEAR, 50, "futures").ok
    k.risk.repair_baseline(1000.0, actor="operator", reason="restore after audit")
    assert journal.kv_get(LATCH) is None and k.risk.baseline_status == "ok"
    assert _state(journal) == "FROZEN"                  # repair never activates
    assert k.owner_resume(OWNER).status == "ACTIVATED"  # separate guarded recovery


DETECTORS = {
    "release_check": lambda r, p: r.release_check(CLEAR),
    "update_equity": lambda r, p: r.update_equity(CLEAR),
    "check_entry": lambda r, p: r.check_entry(ControlState.ACTIVE, "ETH/USDT", 100.0, 1.0,
                                              .02, [], CLEAR, 50, "futures"),
    "load": lambda r, p: RiskManager(RISK_CFG, r.journal),
    "cas_revalidation": lambda r, p: _held(p),
}


@pytest.mark.parametrize("detector", sorted(DETECTORS))
def test_every_detector_latches(tmp_path, detector):
    j, r = _risk(tmp_path)
    proof = r.release_check(CLEAR)
    j.kv_set("risk_state", "{corrupt")
    DETECTORS[detector](r, proof)
    assert j.kv_get(LATCH), detector                   # durable latch on first detection
    j.kv_set("risk_state", PLAUSIBLE)                  # plausible replacement
    assert RiskManager(RISK_CFG, j).release_check(CLEAR).reason == "risk_state_corrupt"
    if detector != "load":
        assert r.release_check(CLEAR).reason == "risk_state_corrupt"
        assert r.update_equity(CLEAR)["risk_state"] == "corrupt"


# ═══ 4. repair_baseline ═════════════════════════════════════════════════════
def _latched(tmp_path, history=(900.0, 1000.0, 950.0)):
    j = Journal(tmp_path / "j.db")
    r = RiskManager(RISK_CFG, j)
    r.update_equity(1000.0)
    with j._tx() as c:
        for i, eq in enumerate(history):
            c.execute("INSERT INTO equity VALUES (?,?,?,?)",
                      (f"2026-09-2{i}T00:00:00+00:00", eq, eq, 1))
    j.kv_set("risk_state", "{corrupt")
    r = RiskManager(RISK_CFG, j)                        # restart: corrupt, latched
    assert r.baseline_status == "corrupt" and j.kv_get(LATCH)
    return j, r


def _untouched(j, r):
    assert j.kv_get("risk_state") == "{corrupt", "repair state written"
    assert j.kv_get(LATCH), "latch cleared without a committed repair"
    assert r.baseline_status == "corrupt", "memory repaired without a committed repair"
    assert _events(j, "risk_state_repaired") == [], "repair audited without a repair"


BAD_REPAIRS = {
    "bool": dict(peak_equity=True), "string": dict(peak_equity="1000"),
    "nan": dict(peak_equity=float("nan")), "inf": dict(peak_equity=float("inf")),
    "zero": dict(peak_equity=0), "negative": dict(peak_equity=-1000.0),
    "empty_actor": dict(peak_equity=1000.0, actor=""),
    "blank_actor": dict(peak_equity=1000.0, actor="  "),
    "empty_reason": dict(peak_equity=1000.0, reason=""),
    "none_reason": dict(peak_equity=1000.0, reason=None),
    "below_floor": dict(peak_equity=999.99),
}


@pytest.mark.parametrize("case", sorted(BAD_REPAIRS))
def test_repair_rejects_invalid_input(tmp_path, case):
    j, r = _latched(tmp_path)
    kw = {"actor": "operator", "reason": "restore", **BAD_REPAIRS[case]}
    with pytest.raises(ValueError):
        r.repair_baseline(kw.pop("peak_equity"), **kw)
    _untouched(j, r)


@pytest.mark.parametrize("peak", [1000.0, 1000, 1100.0])
def test_repair_at_or_above_floor_succeeds_atomically(tmp_path, peak):
    j, r = _latched(tmp_path)
    rec = r.repair_baseline(peak, actor="operator", reason="restore")
    assert rec["proven_floor"] == 1000.0 and rec["history_max"] == 1000.0
    assert json.loads(j.kv_get("risk_state"))["peak_equity"] == float(peak)
    assert j.kv_get(LATCH) is None and j.kv_get(MARKER)
    ev = _events(j, "risk_state_repaired")
    assert len(ev) == 1 and ev[0]["actor"] == "operator"
    assert json.loads(ev[0]["detail"])["prior"]["cause"] == "risk_state_malformed"
    assert r.baseline_status == "ok" and r.update_equity(700.0)["halt_breached"] is True


def check_repair_refuses_unreadable_history(tmp_path, mutate=None):
    j, r = _latched(tmp_path)
    if mutate:
        mutate(r)
    with j._tx() as c:
        c.execute("ALTER TABLE equity RENAME TO equity_offline")
    with pytest.raises(ValueError, match="history unreadable"):
        r.repair_baseline(1000.0, actor="operator", reason="restore")
    _untouched(j, r)


def test_repair_refuses_unreadable_history(tmp_path):
    check_repair_refuses_unreadable_history(tmp_path)


def check_repair_audit_failure_leaves_no_repair(tmp_path, mutate=None):
    j, r = _latched(tmp_path)
    if mutate:
        mutate(r)
    with j._tx() as c:
        c.execute("CREATE TRIGGER audit_down BEFORE INSERT ON control_events "
                  "WHEN NEW.event='risk_state_repaired' "
                  "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
    with pytest.raises(Exception):
        r.repair_baseline(1000.0, actor="operator", reason="restore")
    _untouched(j, r)
    assert r._peak_equity is None                       # memory untouched too


def test_repair_audit_failure_leaves_no_repair(tmp_path):
    check_repair_audit_failure_leaves_no_repair(tmp_path)


def check_repair_never_overwrites_newer_higher_peak(tmp_path, mutate=None):
    """The owner saw floor 1000 and asks for 1000; meanwhile a peak of 1200 is
    being written by another connection. The repair waits for it and refuses."""
    j, r = _latched(tmp_path)
    if mutate:
        mutate(r)
    other = sqlite3.connect(j.db_path, timeout=30, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO equity VALUES ('2026-09-27T12:00:00+00:00', 1200, 1200, 1)")
    out = {}

    def repair():
        try:
            out["rec"] = r.repair_baseline(1000.0, actor="operator", reason="restore")
        except ValueError as e:
            out["err"] = str(e)
    t = _Thread(target=repair)
    t.start()
    t.join(0.5)
    blocked = t.is_alive()
    other.execute("COMMIT")
    other.close()
    t.join(35)
    assert "rec" not in out, "repair overwrote a newer higher peak"
    assert blocked and "1200" in out["err"]
    _untouched(j, r)


def test_repair_serialized_against_concurrent_higher_peak(tmp_path):
    check_repair_never_overwrites_newer_higher_peak(tmp_path)


@pytest.mark.parametrize("source", ["history", "durable_blob"])
def test_repair_sees_intervening_higher_peak(tmp_path, source):
    j, r = _latched(tmp_path)
    if source == "history":
        j.log_equity(1200.0, 1200.0, 1)
    else:
        j.kv_set("risk_state", json.dumps({"peak_equity": 1200.0}))
    with pytest.raises(ValueError, match="1200"):
        r.repair_baseline(1000.0, actor="operator", reason="restore")
    assert r.baseline_status == "corrupt" and j.kv_get(LATCH)


def check_repair_needs_valid_inputs_without_floor(tmp_path, mutate=None):
    """No proven floor at all (no history, fresh process): only the input rules
    stand between a bogus repair and a cleared latch."""
    j = Journal(tmp_path / "nf.db")
    j.kv_set(MARKER, json.dumps({"established": True}))
    j.kv_set("risk_state", "{corrupt")
    r = RiskManager(RISK_CFG, j)
    if mutate:
        mutate(r)
    for peak, actor, reason in ((True, "operator", "x"), (1000.0, "", "x"),
                                (1000.0, "operator", "")):
        with pytest.raises(ValueError):
            r.repair_baseline(peak, actor=actor, reason=reason)
        assert r.baseline_status == "corrupt" and j.kv_get(LATCH), (peak, actor, reason)


def test_repair_input_rules_hold_without_floor(tmp_path):
    check_repair_needs_valid_inputs_without_floor(tmp_path)


def test_repair_keeps_system_contained_until_guarded_recovery(world, monkeypatch):
    journal, venue = world
    k = _boot_active(journal, venue, monkeypatch, None)
    journal.kv_set("risk_state", "{corrupt")
    k._risk_step()                                     # contains ACTIVE → FROZEN
    assert _state(journal) == "FROZEN"
    mark = _last_event(journal)
    k.risk.repair_baseline(1000.0, actor="operator", reason="restore")
    k._risk_step()
    k.supervisor._next_pass = 0
    k.supervisor.cycle()                               # owner FROZEN: no cadence release
    assert _state(journal) == "FROZEN" and _activations(journal, mark) == []
    r = k.owner_resume(OWNER)
    assert r.status == "ACTIVATED"
    assert _activations(journal, mark) == [("RECOVERY", "ACTIVE", "supervisor")]


# ═══ 5. AST inventory of ACTIVE setters ═════════════════════════════════════
KERNEL_SIDE = ["trader/kernel.py", "trader/engine", "trader/agents", "trader/core",
               "trader/owner"]
ALLOWED_SITES = {
    ("trader/engine/supervisor.py", "Supervisor._pass_once", "set_if_current"),
    ("trader/engine/supervisor.py", "Supervisor._guarded_activate", "apply"),
}
ALLOWED_WRAPPERS = {
    ("trader/core/journal.py", "Journal.kv_set"),                  # keyed primitive
    ("trader/engine/state.py", "ControlStateMachine._kv_set"),     # keyed primitive
    ("trader/engine/state.py", "ControlStateMachine._set_fenced"),
    ("trader/engine/state.py", "ControlStateMachine.set"),
    ("trader/engine/state.py", "ControlStateMachine.set_if_current"),
    ("trader/engine/state.py", "FencedControl.apply"),
}
#: outside the kernel: none. The dashboard GraphQL setter and the chat
#: _set_state path were removed by owner-interface-gateway-v1; owner controls
#: now reach the kernel only through trader/owner (OwnerService).
KNOWN_OUT_OF_SCOPE_SITES: set = set()
# The receiver-agnostic scanner sees LearningApplication.apply as the
# FencedControl.apply homonym. These pass a Journal, never a control state;
# actual ACTIVE sites remain forbidden and fully inventoried.
KNOWN_OUT_OF_SCOPE_WRAPPERS: set = {
    ('trader/learning/foundation.py','apply_to_isolated_journal'),
    ('trader/learning/runtime.py','checkpoint'),
}


def assert_inventory(overrides=None):
    inv = load(ROOT, ["trader"], overrides)
    sites = {s.key() for s in inv.sites()}
    kernel = {s for s in sites if any(s[0].startswith(p) for p in KERNEL_SIDE)}
    wrappers = {w for w in inv.wrapper_defs if any(w[0].startswith(p) for p in KERNEL_SIDE)}
    assert kernel == ALLOWED_SITES, sorted(kernel ^ ALLOWED_SITES)
    assert wrappers == ALLOWED_WRAPPERS, sorted(wrappers ^ ALLOWED_WRAPPERS)
    assert sites - kernel == KNOWN_OUT_OF_SCOPE_SITES, sorted(sites - kernel)
    assert inv.wrapper_defs - wrappers == KNOWN_OUT_OF_SCOPE_WRAPPERS
    return inv


def test_every_active_setter_is_inventoried():
    inv = assert_inventory()
    assert inv.setters["set_if_current"] == {("new", 1)}


def test_guarded_sites_bind_the_risk_proof_to_the_cas():
    import ast
    import inspect
    src = inspect.getsource(Supervisor)
    tree = ast.parse("class _:\n" + "\n".join("    " + l for l in src.splitlines()))
    calls = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for c in ast.walk(fn):
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute):
                    calls.setdefault(fn.name, []).append(c)
    cas = [c for c in calls["_pass_once"] if c.func.attr == "set_if_current"
           and any(k.arg == "guard" for k in c.keywords)]
    assert len(cas) == 1                                       # the activation CAS is guarded
    with_items = [w for w in ast.walk(tree) if isinstance(w, ast.With)]
    guarded = [w for w in with_items if "_risk_held" in ast.unparse(w.items[0].context_expr)]
    assert any("ControlState.ACTIVE" in ast.unparse(w) for w in guarded)


NEGATIVE_FIXTURES = {
    "multiline_direct": '''
def _x(self):
    self.state_machine.set(
        ControlState.ACTIVE,
        "operator",
        "multiline")
''',
    "wrapper_helper": '''
def _go(sm, target, who):
    sm.set(target, who, "helper")

def _resume(self):
    _go(self.state_machine, ControlState.ACTIVE, "operator")
''',
    "wrapper_dynamic": '''
def _apply_owner_state(self, requested):
    self.state_machine.set(ControlState(requested.upper()), "operator")
''',
    "value_alias": '''
_ON = ControlState.ACTIVE

def _x(self):
    target = _ON if self else ControlState.FROZEN
    self.state_machine.set(target, "operator")
''',
    "import_alias": '''
from .core.types import ControlState as CS

def _x(self):
    self.state_machine.set_if_current(CS.FROZEN, CS.ACTIVE, "operator")
''',
    "module_alias": '''
import trader.core.types as T

def _x(self):
    self.state_machine.set(T.ControlState.ACTIVE, "operator")
''',
    "method_alias": '''
def _x(self):
    go = self.state_machine.set
    go(ControlState["ACTIVE"], "operator")
''',
    "partial": '''
import functools

def _x(self):
    functools.partial(self.state_machine.set, ControlState.ACTIVE)("operator")
''',
    "fenced_apply": '''
def _x(self):
    with self.state_machine.fenced() as f:
        f.apply(getattr(ControlState, "ACTIVE"), "operator")
''',
    "raw_kv": '''
def _x(self):
    self.journal.kv_set(
        "control_state",
        "ACTIVE")
''',
    "raw_sql": '''
def _x(self):
    with self.journal._tx() as c:
        c.execute("UPDATE state_kv SET value=? WHERE key='control_state'", ("ACTIVE",))
''',
    "kwonly_wrapper": '''
def _release(sm, *, target):
    sm.set(target, "operator")

def _go(sm):
    _release(sm, target=ControlState.ACTIVE)
''',
    "alias_chain": '''
def _go(sm):
    a = sm.set
    b = a
    b(ControlState.ACTIVE, "operator")
''',
    "annotated_alias_chain": '''
from typing import Callable

def _go(sm):
    a: Callable = sm.apply
    c = a
    c(ControlState.ACTIVE)
''',
    "function_alias": '''
def _release(sm, target):
    sm.set(target, "operator")

def _go(sm):
    r = _release
    r(sm, ControlState.ACTIVE)
''',
    "keyed_wrapper": '''
def _put(journal, key, value):
    journal.kv_set(key, value)

def _go(self):
    _put(self.journal, "control_state", "ACTIVE")
''',
    "sql_param_wrapper": '''
def _put(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)", (key, value))

def _go(self, conn):
    _put(conn, "control_state", ControlState.ACTIVE.value)
''',
    "cas_keyword": '''
def _x(self):
    self.state_machine.set_if_current(expected=ControlState.RECOVERY,
                                      new=ControlState.ACTIVE, actor="operator")
''',
}


def _with_fixture(name, where="kernel_append"):
    if where == "new_module":                      # a brand-new kernel-side module
        return {"trader/engine/added_probe.py": NEGATIVE_FIXTURES[name]}
    kernel = (ROOT / "trader/kernel.py").read_text()
    return {"trader/kernel.py": kernel + "\n" + NEGATIVE_FIXTURES[name]}


@pytest.mark.parametrize("where", ["kernel_append", "new_module"])
@pytest.mark.parametrize("name", sorted(NEGATIVE_FIXTURES))
def test_inventory_catches_added_setter(name, where):
    with pytest.raises(AssertionError):
        assert_inventory(_with_fixture(name, where))


def test_inventory_ignores_containment_and_comparisons():
    benign = '''
def _x(self):
    if self.state_machine.refresh() == ControlState.ACTIVE:
        self.state_machine.set_if_current(ControlState.ACTIVE, ControlState.FROZEN,
                                          "risk_engine", "contain")
    self.journal.kv_get("control_state", "ACTIVE")
    self.state_machine.set(ControlState.HALTED, "operator")
'''
    kernel = (ROOT / "trader/kernel.py").read_text()
    assert_inventory({"trader/kernel.py": kernel + benign})


V1_REGEX = __import__("re").compile(
    r"(\.set\(\s*ControlState\.ACTIVE|apply\(\s*ControlState\.ACTIVE"
    r"|set_if_current\([^)]*ControlState\.ACTIVE\s*,\s*\"supervisor\""
    r"|ControlState\.ACTIVE,\s*\"supervisor\""
    r"|kv_set\(\s*[\"']control_state[\"']\s*,\s*[\"']ACTIVE)")


def test_v1_line_regex_missed_what_the_ast_inventory_catches():
    """Recorded evidence for replacing the V1 scanner (not a gate by itself)."""
    missed = sorted(n for n, src in NEGATIVE_FIXTURES.items()
                    if not any(V1_REGEX.search(l) for l in src.splitlines()))
    assert {"multiline_direct", "wrapper_helper", "value_alias", "import_alias",
            "module_alias", "method_alias", "raw_kv", "raw_sql"} <= set(missed)


# ═══ 6. rollback to 51101d0 needs a verified Risk baseline ══════════════════
OLD_ROLLBACK = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import trader
assert trader.__file__.startswith(sys.argv[1]), trader.__file__
from trader.core.journal import Journal
from trader.engine.state import ControlStateMachine
from trader.engine.reconcile import reconcile_futures
from trader.engine.risk import RiskManager
cfg = json.loads(sys.argv[3]); venue = json.loads(sys.argv[4])
j = Journal(sys.argv[2])
sm = ControlStateMachine(j)
out = {"state": sm.state.value, "can_enter": sm.can_enter(),
       "manages_exits": sm.manages_exits()}
risk = RiskManager(cfg, j)
out["old_peak"] = risk._peak_equity
class V:
    mutations = []
    def fetch_positions(self): return venue["positions"]
    def fapiPrivateGetOpenAlgoOrders(self): return venue["stops"]
    def fetch_open_orders(self, s): return []
    def create_order(self, *a, **k): self.mutations.append("create_order"); return {"id": "x"}
    def cancel_order(self, *a, **k): self.mutations.append("cancel_order")
    def fapiPrivateDeleteAlgoOrder(self, p): self.mutations.append("cancel_algo")
    def set_leverage(self, *a, **k): self.mutations.append("set_leverage")
v = V()
out["reconcile"] = reconcile_futures(v, j)
out["mutations"] = v.mutations
out["old_halts_at_700"] = risk.update_equity(700.0)["halt_breached"]
print(json.dumps(out, default=str))
"""

EIGHT = [("BTC", 100.0), ("ETH", 50.0), ("SOL", 20.0), ("BNB", 30.0),
         ("XRP", 2.0), ("ADA", 1.0), ("DOGE", .5), ("LINK", 10.0)]


def _eight_positions(journal, venue):
    from tests.test_kernel_boot_recovery import _trade, _stop
    venue.positions, venue.stops = [], []
    for i, (base, px) in enumerate(EIGHT):
        t = _trade(sl=round(px * .95, 4), sl_order_id=f"70{i:02d}")
        t = t.__class__(**{**t.__dict__, "id": f"pos_{base.lower()}", "symbol": f"{base}/USDT",
                           "entry_price": px, "notional_usdt": px})
        journal.add_trade(t)
        venue.positions.append({"symbol": f"{base}/USDT", "contracts": 1, "side": "long",
                                "entryPrice": px, "markPrice": px})
        s = _stop(trigger=str(round(px * .95, 4)), algo_id=f"70{i:02d}")
        s["symbol"] = f"{base}USDT"
        venue.stops.append(s)


def _old_rollback(src, journal, venue):
    proc = subprocess.run(
        [sys.executable, "-c", OLD_ROLLBACK, str(src), str(journal.db_path),
         json.dumps(RISK_CFG), json.dumps({"positions": venue.positions,
                                           "stops": venue.stops})],
        capture_output=True, text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _corrupt_eight(journal, venue, monkeypatch):
    _eight_positions(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)       # seeds peak 1000 + marker
    k.boot()
    assert _state(journal) == "ACTIVE", _status(journal)["reasons"]
    journal.kv_set("risk_state", "{corrupt")
    k._risk_step()
    assert _state(journal) == "FROZEN" and k.risk.baseline_status == "corrupt"
    return k


def check_rollback_unrepaired_corrupt_blocked(journal, venue, monkeypatch, mutate=None):
    k = _corrupt_eight(journal, venue, monkeypatch)
    if mutate:
        mutate(k, monkeypatch)
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "BLOCKED", "rollback READY over an unrepaired corrupt Risk latch"
    assert "risk_state_corrupt" in r.reasons
    return k, r


def test_rollback_unrepaired_corrupt_is_blocked(world, monkeypatch, old_kernel_src):
    journal, venue = world
    k, r = check_rollback_unrepaired_corrupt_blocked(*world, monkeypatch)
    assert _state(journal) == "FROZEN"                    # the pre-existing hold stays
    res = json.loads(_events(journal, "rollback_prepare_result")[-1]["detail"])
    assert res["status"] == "BLOCKED" and res["risk_baseline"]["reason"] == "risk_state_corrupt"
    assert journal.kv_get("risk_state") == "{corrupt"
    old = _old_rollback(old_kernel_src, journal, venue)   # what BLOCKED protects against
    assert old["old_peak"] is None                        # 51101d0 "starts clean"
    assert venue.mutations == []


def test_rollback_repaired_verified_frozen_loads_in_51101d0(world, monkeypatch,
                                                          old_kernel_src):
    journal, venue = world
    k = _corrupt_eight(journal, venue, monkeypatch)
    k.risk.repair_baseline(1000.0, actor="operator", reason="pre-rollback repair")
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "READY" and r.control_state == "FROZEN", r.reasons
    assert r.hold_event_id == latest_intent_event_id(journal)
    res = json.loads(_events(journal, "rollback_prepare_result")[-1]["detail"])
    assert res["risk_baseline"]["reason"] == "risk_release_ok"
    assert _status(journal)["checks"]["entries_safe"] is True
    before = list(venue.mutations)
    old = _old_rollback(old_kernel_src, journal, venue)
    assert old["state"] == "FROZEN" and old["can_enter"] is False and old["manages_exits"]
    assert old["old_peak"] == 1000.0                       # repaired peak, not reseeded
    assert old["mutations"] == [], old["reconcile"]        # 8 positions: zero venue changes
    assert old["old_halts_at_700"] is True                 # drawdown remembered
    assert venue.mutations == before == []


@pytest.mark.parametrize("fault", ["corrupt_after_read", "uninitialized", "equity_unreadable"])
def test_rollback_blocked_without_verified_risk(world, monkeypatch, fault):
    journal, venue = world
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    if fault == "uninitialized":
        k.risk = RiskManager(RISK_CFG, None)
    elif fault == "equity_unreadable":
        venue.equity = None
    else:
        real = k.supervisor.risk_release

        def read():
            p = real()
            journal.kv_set("risk_state", "{corrupt")       # after the read, before the fence
            return p
        monkeypatch.setattr(k.supervisor, "risk_release", read)
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "BLOCKED" and r.control_state == "RECOVERY", r.reasons
    assert {"corrupt_after_read": "risk_state_corrupt",
            "uninitialized": "risk_baseline_uninitialized",
            "equity_unreadable": "risk_equity_unreadable"}[fault] in r.reasons


# ═══ Astra review (V2 re-probe): cross-connection CAS, history, publication ═
def _reader_blob(path):
    """Read risk_state on a fresh, independent connection (a third party)."""
    c = sqlite3.connect(path, timeout=5)
    try:
        row = c.execute("SELECT value FROM state_kv WHERE key='risk_state'").fetchone()
        return row[0] if row else None
    finally:
        c.close()


def _writer(kind, db_path):
    """Runs in its own thread: a separate Journal (own connections) writing Risk."""
    def write():
        other = Journal(db_path)
        if kind == "raw_journal":
            other.kv_set("risk_state", "{corrupt")
        else:
            RiskManager(RISK_CFG, other).update_equity(NEW_HIGH)       # new baseline
    return write


def check_cross_connection_write_serialized(tmp_path, monkeypatch, mutate=None,
                                            kind="raw_journal"):
    """A durable Risk write on another connection cannot land between the
    proof's revalidation and the persisted ACTIVE: it waits for the CAS."""
    from trader.engine.state import ControlStateMachine
    j = Journal(tmp_path / f"x-{kind}.db")
    r = RiskManager(RISK_CFG, j)
    r.update_equity(1000.0)
    if mutate:
        mutate(r, monkeypatch)
    sm = ControlStateMachine(j)
    sm.set(ControlState.FROZEN, "operator", "hold")
    before = j.kv_get("risk_state")
    proof = r.release_check(CLEAR)
    real, seen = sm._set_fenced, {}

    def set_fenced(new, actor, detail, *, conn=None):
        if new == ControlState.ACTIVE and "t" not in seen:
            t = _Thread(target=_writer(kind, j.db_path), daemon=True)
            seen["t"] = t
            t.start()
            t.join(0.5)
            seen["blocked"] = t.is_alive()
            seen["blob_during_cas"] = _reader_blob(j.db_path)
        return real(new, actor, detail, conn=conn)
    monkeypatch.setattr(sm, "_set_fenced", set_fenced)
    res = sm.set_if_current(ControlState.FROZEN, ControlState.ACTIVE, "supervisor",
                            "probe", guard=lambda: r.hold_release(proof))
    seen["t"].join(40)
    assert seen["blob_during_cas"] == before, "Risk changed inside the final CAS"
    assert seen["blocked"] is True, "another connection wrote Risk inside the final CAS"
    assert res.changed and sm.refresh() == ControlState.ACTIVE
    assert j.kv_get("risk_state") != before          # the write landed after the CAS
    return j, r


@pytest.mark.parametrize("kind", ["raw_journal", "risk_manager"])
def test_cross_connection_risk_write_cannot_land_inside_final_cas(tmp_path, monkeypatch,
                                                                  kind):
    j, r = check_cross_connection_write_serialized(tmp_path, monkeypatch, kind=kind)
    later = r.release_check(CLEAR)                    # and is seen afterwards
    if kind == "raw_journal":
        assert later.reason == "risk_state_corrupt"
    else:                                             # the new 1200 peak: 900 is a halt
        assert later.reason == "risk_halt_active" and later.peak_equity == NEW_HIGH


def test_cross_connection_corruption_during_kernel_cas_is_contained_next(world,
                                                                         monkeypatch):
    journal, venue = world
    k = _boot_active(journal, venue, monkeypatch, None)
    k.state_machine.set(ControlState.FROZEN, "operator", "hold")
    real, seen = k.state_machine._set_fenced, {}

    def set_fenced(new, actor, detail, *, conn=None):
        if new == ControlState.ACTIVE and "t" not in seen:
            t = _Thread(target=_writer("raw_journal", journal.db_path), daemon=True)
            seen["t"] = t
            t.start()
            t.join(0.5)
            seen["blocked"] = t.is_alive()
        return real(new, actor, detail, conn=conn)
    monkeypatch.setattr(k.state_machine, "_set_fenced", set_fenced)
    assert k.owner_resume(OWNER).status == "ACTIVATED"      # on the verified state
    seen["t"].join(40)
    assert seen["blocked"] is True
    mark = _last_event(journal)
    k._risk_step()                                          # the later write is contained
    assert _transitions(journal, mark) == [("ACTIVE", "FROZEN", "risk_engine")]


def _history_with(j, rows):
    with j._tx() as c:
        for ts, eq in rows:
            c.execute("INSERT INTO equity VALUES (?,?,?,?)", (ts, eq, 0, 0))


MALFORMED_HISTORY = {
    "text": [("2026-01-01T00:00:00+00:00", 1200.0), ("2026-01-02T00:00:00+00:00", "bad")],
    "inf": [("2026-01-01T00:00:00+00:00", 1200.0), ("2026-01-02T00:00:00+00:00", float("inf"))],
    "negative": [("2026-01-01T00:00:00+00:00", 1200.0), ("2026-01-02T00:00:00+00:00", -5.0)],
    "blob": [("2026-01-01T00:00:00+00:00", 1200.0), ("2026-01-02T00:00:00+00:00", b"\x00")],
}


def check_history_floor_ignores_malformed_rows(tmp_path, case="text", mutate=None):
    j = Journal(tmp_path / f"h-{case}.db")
    j.kv_set(MARKER, json.dumps({"established": True}))
    _history_with(j, MALFORMED_HISTORY[case])
    j.kv_set("risk_state", "{corrupt")
    r = RiskManager(RISK_CFG, j)                      # no memory peak
    if mutate:
        mutate(r)
    with pytest.raises(ValueError, match="1200"):
        r.repair_baseline(1000.0, actor="operator", reason="restore")
    assert r.baseline_status == "corrupt" and j.kv_get(LATCH), "repair below true floor"
    return j, r


@pytest.mark.parametrize("case", sorted(MALFORMED_HISTORY))
def test_history_floor_ignores_malformed_rows(tmp_path, case):
    j, r = check_history_floor_ignores_malformed_rows(tmp_path, case)
    rec = r.repair_baseline(1200.0, actor="operator", reason="restore")
    assert rec["history_max"] == 1200.0 and rec["invalid_history_rows"] == 1
    assert RiskManager._parse(j.kv_get("risk_state")) is not None


def test_repaired_day_baseline_is_valid_or_absent(tmp_path):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for i, rows in enumerate((
            [(f"{today}T00:00:01+00:00", "bad"), (f"{today}T00:00:02+00:00", float("inf")),
             (f"{today}T00:00:03+00:00", 950.0)],
            [(f"{today}T00:00:01+00:00", "bad"), (f"{today}T00:00:02+00:00", -1.0)])):
        j = Journal(tmp_path / f"d{i}.db")
        j.kv_set(MARKER, json.dumps({"established": True}))
        _history_with(j, [("2026-01-01T00:00:00+00:00", 1000.0)] + rows)
        j.kv_set("risk_state", "{corrupt")
        r = RiskManager(RISK_CFG, j)
        rec = r.repair_baseline(1000.0, actor="operator", reason="restore")
        state = RiskManager._parse(j.kv_get("risk_state"))
        assert state is not None
        assert rec["day_start_equity"] == (950.0 if i == 0 else None)
        assert state["day_start_equity"] == rec["day_start_equity"]


def check_repair_publication_serialized(tmp_path, monkeypatch, mutate=None):
    """Commit → (a newer corruption from another connection is detected) →
    the older repair must not overwrite that newer latch in memory."""
    j, r = _latched(tmp_path)
    if mutate:
        mutate(r, monkeypatch)
    real_lock, seen = r._lock, []

    class ReleaseInterleave:
        def __enter__(self):
            real_lock.acquire()
            return self

        def __exit__(self, *exc):
            real_lock.release()
            if not seen:
                # Deterministic scheduling boundary: run the competing detector
                # immediately after unlock, before the repair caller resumes.
                seen.append(True)
                assert _events(j, "risk_state_repaired"), "repair did not commit"
                Journal(j.db_path).kv_set("risk_state", "{corrupt-again")
                r.update_equity(1000.0)

    monkeypatch.setattr(r, "_lock", ReleaseInterleave())
    r.repair_baseline(1000.0, actor="operator", reason="restore")
    assert seen
    assert r.baseline_status == "corrupt", "older repair overwrote a newer latch in memory"
    assert j.kv_get(LATCH)


def test_repair_publication_serialized_with_newer_corruption(tmp_path, monkeypatch):
    check_repair_publication_serialized(tmp_path, monkeypatch)


def test_rollback_ready_audit_is_held_with_the_baseline(world, monkeypatch):
    """The final baseline verification stays authoritative through READY: a
    Risk write from another connection waits until the READY audit commits."""
    journal, venue = world
    k = _corrupt_eight(journal, venue, monkeypatch)
    k.risk.repair_baseline(1000.0, actor="operator", reason="pre-rollback repair")
    real, seen = k.state_machine._event, {}

    def event(conn, name, *a, **kw):
        if name == "rollback_prepare_result" and conn is not None and "t" not in seen:
            t = _Thread(target=_writer("raw_journal", journal.db_path), daemon=True)
            seen["t"] = t
            t.start()
            t.join(0.5)
            seen["blocked"] = t.is_alive()
            seen["blob"] = _reader_blob(journal.db_path)
        return real(conn, name, *a, **kw)
    monkeypatch.setattr(k.state_machine, "_event", event)
    r = k.supervisor.prepare_rollback(OWNER)
    seen["t"].join(40)
    assert r.status == "READY" and seen["blocked"] is True
    assert RiskManager._parse(seen["blob"]) is not None      # verified blob at READY
    ready = _events(journal, "rollback_prepare_result")[-1]["id"]
    assert journal.kv_get("risk_state") == "{corrupt"        # landed only after READY
    assert k.supervisor.prepare_rollback(OWNER).status == "BLOCKED"   # and is seen next
    assert ready


def test_rollback_checks_risk_before_venue(world, monkeypatch):
    """User sequence: repaired/verified Risk first; an unverified baseline
    stops before any fresh venue pass."""
    journal, venue = world
    k = _corrupt_eight(journal, venue, monkeypatch)
    reads = venue.reads
    r = k.supervisor.prepare_rollback(OWNER)
    assert r.status == "BLOCKED" and r.reasons == ("risk_state_corrupt",)
    assert venue.reads == reads and venue.mutations == []


def test_rollback_final_hold_follows_fresh_checks(world, monkeypatch):
    journal, venue = world
    k = _corrupt_eight(journal, venue, monkeypatch)
    k.risk.repair_baseline(1000.0, actor="operator", reason="restore")
    real_pass, real_set = k.supervisor._pass_once, k.state_machine._set_fenced
    order, holds = [], []

    def run_pass(**kw):
        # Initial Risk check has verified the repaired baseline before venue work.
        assert k.risk.baseline_status == "ok"
        result = real_pass(**kw)
        assert result.checks["entries_safe"]
        order.append("checks")
        return result

    def set_fenced(new, actor, detail, *, conn=None):
        result = real_set(new, actor, detail, conn=conn)
        if detail.startswith("rollback preparation #"):
            order.append("final_hold" if conn is not None else "intake")
            holds.append(result)
        return result

    monkeypatch.setattr(k.supervisor, "_pass_once", run_pass)
    monkeypatch.setattr(k.state_machine, "_set_fenced", set_fenced)
    result = k.supervisor.prepare_rollback(OWNER)
    assert result.status == "READY"
    assert order == ["intake", "checks", "final_hold"]
    assert holds[0] < holds[1] == result.hold_event_id == latest_intent_event_id(journal)


def test_guarded_cas_passes_held_connection_before_any_write(tmp_path, monkeypatch):
    """Fail immediately if CAS drops the connection: never enter a nested _tx
    and hang. The same assertion is the bounded M18 negative control."""
    from trader.engine.state import ControlStateMachine
    j, r = _risk(tmp_path)
    sm = ControlStateMachine(j)
    sm.set(ControlState.FROZEN, "operator", "hold")
    real = sm._set_fenced
    seen = []

    def write(new, actor, detail, *, conn=None):
        assert conn is j._conn() and conn.in_transaction, "CAS dropped held transaction"
        seen.append(True)
        return real(new, actor, detail, conn=conn)

    monkeypatch.setattr(sm, "_set_fenced", write)
    proof = r.release_check(CLEAR)
    result = sm.set_if_current(ControlState.FROZEN, ControlState.ACTIVE, "operator",
                               guard=lambda: r.hold_release(proof))
    assert seen and result.changed and sm.can_enter()


@pytest.mark.parametrize("later", [700.0, None])
def test_nested_release_check_cannot_lend_its_generation(tmp_path, later):
    _, r = _risk(tmp_path)

    class Equity:
        def __float__(self):
            assert not r.release_check(later).allowed
            return CLEAR

    proof = r.release_check(Equity())
    assert proof.allowed
    assert _held(proof) == "risk_proof_superseded"


def test_concurrent_release_check_supersedes_older_proof(tmp_path, monkeypatch):
    _, r = _risk(tmp_path)
    entered, resume, attempted = threading.Event(), threading.Event(), threading.Event()
    real = r._evaluate_release
    results, errors = {}, []

    def evaluate(equity, *args):
        if equity == CLEAR:
            entered.set()
            assert resume.wait(3), "test did not resume first evaluation"
        return real(equity, *args)

    def run(name, equity):
        try:
            if name == "later":
                attempted.set()
            results[name] = r.release_check(equity)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(r, "_evaluate_release", evaluate)
    first = _Thread(target=run, args=("first", CLEAR), daemon=True)
    later = _Thread(target=run, args=("later", BREACH), daemon=True)
    first.start()
    assert entered.wait(3)
    later.start()
    assert attempted.wait(3)
    resume.set()
    first.join(3)
    later.join(3)
    assert not first.is_alive() and not later.is_alive() and not errors
    assert not results["later"].allowed
    assert _held(results["first"]) == "risk_proof_superseded"


def test_repair_retains_finite_history_above_1e308(tmp_path):
    j = Journal(tmp_path / "large.db")
    _history_with(j, [("2026-01-01", 1.5e308), ("2026-01-02", float("inf"))])
    j.kv_set("risk_state", "{corrupt")
    r = RiskManager(RISK_CFG, j)
    with pytest.raises(ValueError, match="below proven"):
        r.repair_baseline(1e308, actor="operator", reason="restore")
    record = r.repair_baseline(1.5e308, actor="operator", reason="restore")
    assert record["history_max"] == record["proven_floor"] == 1.5e308
    assert record["invalid_history_rows"] == 1
    assert RiskManager._parse(j.kv_get("risk_state")) is not None


def _mutate_method(monkeypatch, cls, name, old, new):
    """Compile a real source mutation in memory; checkout bytes never change."""
    method = getattr(cls, name)
    source = inspect.getsource(method)
    assert source.count(old) == 1, "mutation anchor changed"
    namespace = dict(method.__globals__)
    exec(compile(textwrap.dedent(source.replace(old, new)),
                 f"<negative-control:{cls.__name__}.{name}>", "exec"), namespace)
    monkeypatch.setattr(cls, name, namespace[name])


def test_negative_publication_after_unlock_is_caught(tmp_path, monkeypatch):
    publication = (
        "            self._peak_equity, self._day_start_equity, self._day_key = peak, day_start, day_key\n"
        '            self.baseline_status = "ok"\n'
        "            self.corrupt_detail = None\n"
        "            self._established = True\n"
        "            self._generation += 1\n")
    outside = "\n".join(line[4:] for line in publication.splitlines()) + "\n"
    _mutate_method(monkeypatch, RiskManager, "repair_baseline", publication, outside)
    with pytest.raises(AssertionError, match="older repair overwrote a newer latch"):
        check_repair_publication_serialized(tmp_path, monkeypatch)


def test_negative_cas_without_held_connection_is_caught(tmp_path, monkeypatch):
    from trader.engine.state import ControlStateMachine
    _mutate_method(monkeypatch, ControlStateMachine, "set_if_current",
                   "event_id = self._set_fenced(new, actor, detail, conn=conn)",
                   "event_id = self._set_fenced(new, actor, detail)")
    with pytest.raises(AssertionError, match="CAS dropped held transaction"):
        test_guarded_cas_passes_held_connection_before_any_write(tmp_path, monkeypatch)


def test_negative_rollback_reuses_intake_hold_is_caught(world, monkeypatch):
    _mutate_method(monkeypatch, Supervisor, "prepare_rollback",
                   'final_hold = f.apply(ControlState.FROZEN, ctx.actor,\n'
                   '                                                 f"rollback preparation #{rid}", conn=conn)',
                   "final_hold = hold")
    with pytest.raises(AssertionError):
        test_rollback_final_hold_follows_fresh_checks(world, monkeypatch)


def test_negative_release_borrows_newer_generation_is_caught(tmp_path, monkeypatch):
    _mutate_method(monkeypatch, RiskManager, "_evaluate_release",
                   "ident = dict(generation=generation,", "ident = dict(generation=self._generation,")
    with pytest.raises(AssertionError):
        test_nested_release_check_cannot_lend_its_generation(tmp_path, 700.0)


def test_negative_history_truncates_valid_finite_peak_is_caught(tmp_path, monkeypatch):
    _mutate_method(monkeypatch, RiskManager, "repair_baseline",
                   '"AND equity <= 1.7976931348623157e308").fetchone()',
                   '"AND equity <= 1e308").fetchone()')
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        test_repair_retains_finite_history_above_1e308(tmp_path)


def mutant_hold_lock_only(r, monkeypatch):
    """The first V2 candidate: only this instance's in-process lock is held."""
    @contextmanager
    def lock_only(self):
        with self._lock:
            yield None
    monkeypatch.setattr(RiskManager, "_durable_hold", lock_only)


# ═══ negative controls: each mutant removes one V2 guard and MUST fail ══════
def mutant_cas_ignores_proof(k, monkeypatch):
    """Stale proof reuse: the CAS accepts the proof without revalidation."""
    @contextmanager
    def held(self, risk):
        yield None if risk.allowed else risk.reason
    monkeypatch.setattr(Supervisor, "_risk_held", held)


def mutant_revalidate_watermark_only(k, monkeypatch):
    """Only 'allowed' is rechecked (the control watermark alone guards)."""
    monkeypatch.setattr(RiskManager, "_revalidate",
                        lambda self, p: None if p.allowed else p.reason)


def mutant_equity_rows_prove_init(k, monkeypatch):
    """V1: any equity row is taken as evidence that a baseline existed."""
    real = RiskManager._classify

    def classify(self):
        status, state, detail = real(self)
        if status == "uninitialized" and self.journal and self.journal.query(
                "SELECT 1 FROM equity LIMIT 1"):
            return "corrupt", None, {"cause": "risk_state_missing"}
        return status, state, detail
    monkeypatch.setattr(RiskManager, "_classify", classify)


def mutant_release_check_does_not_latch(k, monkeypatch):
    """V1: release_check detects corruption but does not latch, durable or not."""
    real = RiskManager.release_check

    def release_check(self, equity, *, authoritative=True):
        saved = RiskManager._mark_corrupt
        RiskManager._mark_corrupt = lambda self, d: None
        try:
            return real(self, equity, authoritative=authoritative)
        finally:
            RiskManager._mark_corrupt = saved
    monkeypatch.setattr(RiskManager, "release_check", release_check)


def _repair_v1(self, peak_equity, *, actor, reason):
    """The V1 repair: floor read up front, unserialized, state then audit."""
    import math
    peak = float(peak_equity)
    if not math.isfinite(peak) or peak <= 0:
        raise ValueError("repair peak must be a finite positive equity")
    try:
        m = self.journal.query("SELECT MAX(equity) AS m FROM equity")[0]["m"]
    except Exception:
        m = None
    floor = max([v for v in (m, self._peak_equity) if isinstance(v, (int, float))],
                default=0.0)
    if peak < floor:
        raise ValueError(f"repair peak {peak} below proven high-water mark {floor}")
    self.journal.kv_set(self._STATE_KEY, json.dumps({"peak_equity": peak}))
    with self.journal._tx() as c:
        c.execute("DELETE FROM state_kv WHERE key=?", (self._LATCH_KEY,))
    self._peak_equity, self.baseline_status, self.corrupt_detail = peak, "ok", None
    self.journal.log_control_event("risk_state_repaired", actor, detail={"reason": reason})


def mutant_repair_v1(r, monkeypatch=None):
    r.repair_baseline = _repair_v1.__get__(r)


def mutant_rollback_ignores_risk(k, monkeypatch):
    monkeypatch.setattr(Supervisor, "_baseline_refusal",
                        staticmethod(lambda risk, recheck=False: None))


def _chk_stale_proof(journal, venue, monkeypatch, mutate):
    for path in ("owner_recovery", "spot_owner"):
        check_risk_change_before_cas_refused(journal.__class__(
            Path(journal.db_path).with_name(f"{path}.db")), HookVenue(), monkeypatch,
            mutate, path=path, action="reevaluate")


def _chk_first_init(journal, venue, monkeypatch, mutate):
    check_full_cycle_initialization(journal, venue, monkeypatch, mutate)


def _chk_latch(journal, venue, monkeypatch, mutate):
    check_release_check_latches(journal, venue, monkeypatch, mutate)


def _chk_rollback(journal, venue, monkeypatch, mutate):
    check_rollback_unrepaired_corrupt_blocked(journal, venue, monkeypatch, mutate)


CONTROLS = [
    (_chk_stale_proof, mutant_cas_ignores_proof, "stale_proof_reuse:cas_unguarded"),
    (_chk_stale_proof, mutant_revalidate_watermark_only, "stale_proof_reuse:watermark_only"),
    (_chk_first_init, mutant_equity_rows_prove_init, "fake_equity_row_init"),
    (_chk_latch, mutant_release_check_does_not_latch, "release_check_unlatched"),
    (_chk_rollback, mutant_rollback_ignores_risk, "unrepaired_corrupt_rollback_ready"),
]


@pytest.mark.parametrize("check, mutant", [(c, m) for c, m, _ in CONTROLS],
                         ids=[i for _, _, i in CONTROLS])
def test_negative_control_mutant_is_caught(world, monkeypatch, check, mutant):
    journal, venue = world
    with pytest.raises(AssertionError):
        check(journal, venue, monkeypatch, mutant)


@pytest.mark.parametrize("check", sorted({c for c, _, _ in CONTROLS}, key=lambda f: f.__name__))
def test_checks_pass_on_real_code(world, monkeypatch, check):
    journal, venue = world
    check(journal, venue, monkeypatch, None)


REPAIR_CONTROLS = [
    (check_repair_never_overwrites_newer_higher_peak, "concurrent_higher_peak_overwrite"),
    (check_repair_refuses_unreadable_history, "unreadable_history_accepted"),
    (check_repair_audit_failure_leaves_no_repair, "audit_missing"),
    (check_repair_needs_valid_inputs_without_floor, "bool_or_empty_inputs"),
]


@pytest.mark.parametrize("check", [c for c, _ in REPAIR_CONTROLS],
                         ids=[i for _, i in REPAIR_CONTROLS])
def test_negative_control_repair_v1_is_caught(tmp_path, check):
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        check(tmp_path, mutant_repair_v1)


def mutant_history_max_untyped(r, monkeypatch=None):
    """The first V2 candidate's floor: SQL MAX over mixed-type rows."""
    import math
    real = RiskManager.repair_baseline

    def repair(peak_equity, *, actor, reason):
        row = r.journal.query("SELECT MAX(equity) AS m FROM equity WHERE equity > 0")[0]
        m = row["m"]
        floor = m if isinstance(m, (int, float)) and math.isfinite(m) else 0.0
        if peak_equity < floor:
            raise ValueError(f"repair peak {peak_equity} below proven high-water mark {floor}")
        with r.journal._tx() as c:
            c.execute("DELETE FROM equity WHERE typeof(equity) NOT IN ('real','integer')")
            c.execute("DELETE FROM equity WHERE equity > ?", (peak_equity,))
        return real(r, peak_equity, actor=actor, reason=reason)
    r.repair_baseline = repair


REVIEW_CONTROLS = [
    (lambda t, m, mut: check_cross_connection_write_serialized(t, m, mut, kind="raw_journal"),
     mutant_hold_lock_only, "cas_lock_only:raw_journal_writer"),
    (lambda t, m, mut: check_cross_connection_write_serialized(t, m, mut, kind="risk_manager"),
     mutant_hold_lock_only, "cas_lock_only:risk_manager_writer"),
    (lambda t, m, mut: check_history_floor_ignores_malformed_rows(
        t, "text", mutate=lambda r: mut(r, m)),
     mutant_history_max_untyped, "history_max_untyped"),
]


@pytest.mark.parametrize("check, mutant", [(c, m) for c, m, _ in REVIEW_CONTROLS],
                         ids=[i for _, _, i in REVIEW_CONTROLS])
def test_negative_control_review_mutant_is_caught(tmp_path, monkeypatch, check, mutant):
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        check(tmp_path, monkeypatch, mutant)
