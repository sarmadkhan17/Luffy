"""LUFFY-ACTIVATION-AND-RISK-BASELINE-HARDENING-V1.

One kernel rule: no path grants ACTIVE while Risk denies release, while the
Risk baseline is corrupt/unproven, or over newer owner/risk containment.
Covers MacroGuard release, spot /resume, the corrupt-baseline contract, first
initialization, cadence, a previously approved entry, baseline preservation
and rollback. Real RiskManager, real Kernel._risk_step/_macro_step/owner_resume,
real Supervisor; only the venue (and its equity read) is a double.

Baseline: peak 1000, halt 20 % → 700 is an active breach, 900 is clear.
Scenario checks take `mutate(k, monkeypatch)` so negative controls (bottom)
run the same assertions against mutants.
"""
import json
import re
from pathlib import Path

import pytest

from trader.core.journal import Journal
from trader.core.types import Action, ControlState, Decision, MarketType
from trader.engine.control_fence import latest_intent_event_id, persisted_state
from trader.engine.risk import RiskManager, RiskRelease
from trader.engine.supervisor import OwnerContext
from tests.test_kernel_boot_recovery import (RISK_CFG, _kernel, _last_event,
                                             _protected, _status, _transitions)
from tests.test_owner_recovery import HookVenue, _events
from tests.test_owner_recovery_risk_guard import tg

OWNER = OwnerContext("operator", "test", principal="owner-1", request_ref="unit")
BREACH, CLEAR, NEW_HIGH = 700.0, 900.0, 1200.0
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), HookVenue()


def _activations(journal, after=0):
    return [t for t in _transitions(journal, after) if t[1] == "ACTIVE"]


def _state(journal):
    """As the kernel reads it (an absent key is the implicit first-run ACTIVE)."""
    state = persisted_state(journal)
    return state.value if state else None


def _risk_snapshot(k):
    return (k.journal.kv_get("risk_state"), k.risk._peak_equity,
            k.risk._day_start_equity, k.risk._day_key)


def _boot(journal, venue, monkeypatch, mutate=None, market=MarketType.FUTURES):
    _protected(journal, venue)
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.market_type = market
    if mutate:
        mutate(k, monkeypatch)
    if market == MarketType.FUTURES:
        k.boot()
    assert _state(journal) == "ACTIVE"
    return k


def _macro_frozen(journal, venue, monkeypatch, mutate=None, market=MarketType.FUTURES):
    k = _boot(journal, venue, monkeypatch, mutate, market)
    k._macro_step({"active": True, "event": "CPI"})
    assert _state(journal) == "FROZEN"
    eid = int(journal.kv_get("macro_guard_freeze_event_id"))
    assert eid == latest_intent_event_id(journal)
    return k, eid


def _on_risk_read(k, monkeypatch, hook, equity):
    """Run `hook` inside the fresh equity read (between observation and CAS)."""
    fired = []

    def read():
        if not fired:
            fired.append(1)
            hook()
        return equity
    monkeypatch.setattr(k, "_fetch_balance_fresh", read, raising=False)


# ── 1: kernel ACTIVE-path inventory (static) ────────────────────────────────
def test_every_kernel_active_setter_is_inventoried():
    """V2: the V1 line regex is retired; the AST inventory is the one scanner."""
    from tests.test_activation_risk_baseline_v2 import assert_inventory
    assert_inventory()
    kernel = (ROOT / "trader/kernel.py").read_text()
    assert "ControlState.ACTIVE, \"macro_guard\"" not in kernel
    assert "ControlState.ACTIVE, \"operator\"" not in kernel


def test_guarded_activate_is_only_reached_through_fresh_risk():
    src = (ROOT / "trader/engine/supervisor.py").read_text()
    calls = [m.start() for m in re.finditer(r"self\._guarded_activate\(", src)]
    assert len(calls) == 2                      # spot owner release, spot macro release
    body = src[src.index("def _guarded_activate"):src.index("def request_owner_release")]
    assert "if not risk.allowed:" in body and "f.watermark != watermark" in body


# ── 2: MacroGuard (futures: RECOVERY + full pass; spot: Risk-gated CAS) ─────
def check_macro_breach_contained(journal, venue, monkeypatch, mutate=None,
                                 market=MarketType.FUTURES):
    """A: Risk breached after the cycle's Risk step, before MacroGuard release."""
    k, eid = _macro_frozen(journal, venue, monkeypatch, mutate, market)
    venue.equity = BREACH
    mark = _last_event(journal)
    k._macro_step({"active": False})
    assert _activations(journal, mark) == [], "MacroGuard activated through a Risk breach"
    assert _state(journal) == "FROZEN"
    return k, eid, mark


@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_a_macro_release_refused_while_risk_breached(world, monkeypatch, market):
    journal, venue = world
    k, eid, mark = check_macro_breach_contained(*world, monkeypatch, market=market)
    assert _transitions(journal, mark) == []
    ev = [json.loads(e["detail"]) for e in _events(journal, "macro_release_refused")]
    assert ev[-1] == {"freeze_event_id": eid, "reasons": ["risk_halt_active"]}
    assert journal.kv_get("macro_guard_froze") == "1"        # still MacroGuard's hold
    k._risk_step()                                           # next cycle's Risk step
    assert _state(journal) == "HALTED"


def test_a2_macro_breach_arising_inside_the_pass_is_contained(world, monkeypatch):
    journal, venue = world
    k, eid = _macro_frozen(journal, venue, monkeypatch)
    venue.equity = CLEAR
    venue.on_positions = lambda: setattr(venue, "equity", BREACH)
    mark = _last_event(journal)
    k._macro_step({"active": False})
    assert _activations(journal, mark) == []
    assert _state(journal) == "RECOVERY"
    st = _status(journal)
    assert "risk_halt_active" in st["reasons"] and st["checks"]["risk_release"] is False
    assert journal.kv_get("macro_guard_froze") == "0"        # handed to the Supervisor


def check_macro_newer_intent_wins(journal, venue, monkeypatch, mutate=None, *,
                                  new=ControlState.HALTED, market=MarketType.FUTURES):
    """B/C: a newer HALTED/FROZEN lands between observation and release."""
    k, eid = _macro_frozen(journal, venue, monkeypatch, mutate, market)
    _on_risk_read(k, monkeypatch,
                  lambda: k.state_machine.set(new, "operator", "newer owner intent"), CLEAR)
    mark = _last_event(journal)
    k._macro_step({"active": False})
    assert _activations(journal, mark) == [], "MacroGuard overwrote newer containment"
    assert _state(journal) == new.value
    return k, mark


@pytest.mark.parametrize("new", [ControlState.HALTED, ControlState.FROZEN])
@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_bc_newer_halted_or_frozen_wins_over_macro_release(world, monkeypatch, new, market):
    journal, venue = world
    k, mark = check_macro_newer_intent_wins(*world, monkeypatch, new=new, market=market)
    assert journal.kv_get("macro_guard_froze") == "0"        # ownership superseded
    res = json.loads(_events(journal, "macro_release_result")[-1]["detail"])
    assert res["status"] == "REFUSED" and res["reasons"] == ["macro_freeze_not_owned"]
    venue.equity = CLEAR
    for _ in range(3):                                       # never resumes later
        k._macro_step({"active": False})
    assert _state(journal) == new.value


def test_c_stale_macro_observation_refused_by_supervisor(world, monkeypatch):
    """Direct call with the old freeze id after an owner re-freeze: refused."""
    journal, venue = world
    k, eid = _macro_frozen(journal, venue, monkeypatch)
    k.state_machine.set(ControlState.FROZEN, "operator", "owner hold")     # state_hold
    r = k.supervisor.request_macro_release(eid)
    assert r.status == "REFUSED" and r.reasons == ("macro_freeze_not_owned",)
    assert _state(journal) == "FROZEN"
    assert _activations(journal) == [("RECOVERY", "ACTIVE", "supervisor")]   # boot only


@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_d_safe_macro_release_activates_only_after_fresh_proof(world, monkeypatch, market):
    journal, venue = world
    k, eid = _macro_frozen(journal, venue, monkeypatch, market=market)
    before = _risk_snapshot(k)
    mark = _last_event(journal)
    k._macro_step({"active": False})
    assert _state(journal) == "ACTIVE"
    if market == MarketType.FUTURES:
        assert _transitions(journal, mark) == [("FROZEN", "RECOVERY", "macro_guard"),
                                               ("RECOVERY", "ACTIVE", "supervisor")]
        st = _status(journal)
        assert st["checks"]["risk_release"] is True and st["checks"]["entries_safe"] is True
    else:
        assert _transitions(journal, mark) == [("FROZEN", "ACTIVE", "macro_guard")]
    res = json.loads(_events(journal, "macro_release_result")[-1]["detail"])
    assert res["status"] == "ACTIVATED" and res["freeze_event_id"] == eid
    assert journal.kv_get("macro_guard_froze") == "0"
    assert _risk_snapshot(k) == before


def test_e_repeated_macro_attempts_are_quiet_and_never_unsafe(world, monkeypatch):
    journal, venue = world
    k, eid = _macro_frozen(journal, venue, monkeypatch)
    venue.equity = None                                      # Risk cannot be read
    mark = _last_event(journal)
    for _ in range(5):
        k._macro_step({"active": False})
    assert _transitions(journal, mark) == [] and _state(journal) == "FROZEN"
    refused = [e for e in _events(journal, "macro_release_refused") if e["id"] > mark]
    assert len(refused) == 1                                 # audited once, not per cycle
    assert json.loads(refused[0]["detail"])["reasons"] == ["risk_equity_unreadable"]
    assert venue.mutations == []
    venue.equity = CLEAR
    k._macro_step({"active": False})
    assert _state(journal) == "ACTIVE"


def test_e2_macro_owned_freeze_after_risk_halt_is_not_resumed(world, monkeypatch):
    """Macro FROZEN → Risk HALTED (FROZEN→HALTED): the stale flag is disowned."""
    journal, venue = world
    k, eid = _macro_frozen(journal, venue, monkeypatch)
    venue.equity = BREACH
    k._risk_step()
    assert _state(journal) == "HALTED"
    venue.equity = CLEAR
    mark = _last_event(journal)
    k._macro_step({"active": False})                         # state is HALTED: no attempt
    assert _transitions(journal, mark) == [] and _state(journal) == "HALTED"


# ── 3: spot /resume and /unhalt ─────────────────────────────────────────────
def _spot(journal, venue, monkeypatch, mutate=None):
    return _boot(journal, venue, monkeypatch, mutate, market=MarketType.SPOT)


def check_spot_resume_contained(journal, venue, monkeypatch, mutate=None):
    k = _spot(journal, venue, monkeypatch, mutate)
    venue.equity = BREACH
    k._risk_step()
    assert _state(journal) == "HALTED"
    mark = _last_event(journal)
    r1 = tg(k, monkeypatch, "/resume")
    r2 = tg(k, monkeypatch, "/unhalt")
    assert _activations(journal, mark) == [], "spot resume bypassed an active Risk halt"
    assert _state(journal) == "HALTED"
    tg(k, monkeypatch, "/freeze")
    r3 = tg(k, monkeypatch, "/resume")
    assert _activations(journal, mark) == [], "spot FROZEN resume hid a Risk halt"
    assert _state(journal) == "FROZEN"
    return k, (r1, r2, r3)


def test_spot_halted_and_frozen_resume_with_active_drawdown_contained(world, monkeypatch):
    journal, venue = world
    k, (r1, r2, r3) = check_spot_resume_contained(*world, monkeypatch)
    assert "does not release a halt" in r1[-1]
    assert "risk_halt_active" in r2[-1] and "risk_halt_active" in r3[-1]
    res = [json.loads(e["detail"]) for e in _events(journal, "owner_recovery_result")]
    assert [d["status"] for d in res[-3:]] == ["REFUSED"] * 3
    assert res[-1]["risk_release"]["drawdown_pct"] == 30.0
    assert res[-2]["operation"] == "owner_unhalt"


def test_spot_safe_recovery_activates_after_fresh_proof(world, monkeypatch):
    journal, venue = world
    k = _spot(journal, venue, monkeypatch)
    venue.equity = BREACH
    k._risk_step()
    venue.equity = CLEAR
    before = _risk_snapshot(k)
    mark = _last_event(journal)
    assert "risk_halt_active" not in tg(k, monkeypatch, "/unhalt")[-1]
    assert _transitions(journal, mark) == [("HALTED", "ACTIVE", "operator")]
    res = json.loads(_events(journal, "owner_recovery_result")[-1]["detail"])
    assert res["status"] == "ACTIVATED" and res["risk_release"]["allowed"] is True
    assert _risk_snapshot(k) == before


def check_spot_newer_halted_wins(journal, venue, monkeypatch, mutate=None):
    k = _spot(journal, venue, monkeypatch, mutate)
    tg(k, monkeypatch, "/freeze")
    _on_risk_read(k, monkeypatch, lambda: k.state_machine.set(
        ControlState.HALTED, "risk_engine", "drawdown"), CLEAR)
    mark = _last_event(journal)
    tg(k, monkeypatch, "/resume")
    assert _activations(journal, mark) == [], "spot resume overwrote a newer HALTED"
    assert _state(journal) == "HALTED"
    return k


def test_spot_newer_halted_during_recovery_wins(world, monkeypatch):
    journal, venue = world
    check_spot_newer_halted_wins(*world, monkeypatch)
    res = json.loads(_events(journal, "owner_recovery_result")[-1]["detail"])
    assert res["status"] == "REFUSED" and res["reasons"] == ["control_state_changed"]


def test_spot_unhalt_newer_owner_halt_wins(world, monkeypatch):
    journal, venue = world
    k = _spot(journal, venue, monkeypatch)
    tg(k, monkeypatch, "/halt")
    _on_risk_read(k, monkeypatch, lambda: k.state_machine.set(
        ControlState.HALTED, "operator", "second halt"), CLEAR)
    mark = _last_event(journal)
    tg(k, monkeypatch, "/unhalt")
    assert _activations(journal, mark) == [] and _state(journal) == "HALTED"


def test_spot_adapter_holds_no_safety_logic():
    """Telegram is an Owner Interface adapter: it builds a typed request and
    renders the typed result; futures/spot semantics live behind owner_resume."""
    import inspect
    from trader.kernel import Kernel
    from trader.owner import service
    from trader.owner.adapters import telegram
    src = inspect.getsource(Kernel._handle_tg_command)
    assert "service.execute(" in src and "service = self._owner()" in src
    for forbidden in ("state_machine", "MarketType", "owner_resume", "ControlState"):
        assert forbidden not in src, forbidden
    adapter = inspect.getsource(telegram)
    assert "state_machine" not in adapter and "MarketType" not in adapter
    assert "self._resume(ctx, allow_unhalt=allow_unhalt," in inspect.getsource(service)
    assert "MarketType" not in inspect.getsource(service)


# ── 4–7: corrupt baseline vs first initialization ───────────────────────────
def _history(journal, peak=1000.0):
    """Prior kernel cycles: equity rows (the durable first-init evidence)."""
    with journal._tx() as c:
        for i, eq in enumerate((900.0, peak, 950.0)):
            c.execute("INSERT INTO equity VALUES (?,?,?,?)",
                      (f"2026-09-2{i}T00:00:00+00:00", eq, eq, 1))


CORRUPTIONS = {
    "malformed_json": lambda j: j.kv_set("risk_state", "{corrupt"),
    "null_peak": lambda j: j.kv_set("risk_state", '{"peak_equity": null}'),
    "nan_peak": lambda j: j.kv_set("risk_state", '{"peak_equity": "NaN", "day_key": "x"}'),
    "negative_day": lambda j: j.kv_set("risk_state", json.dumps(
        {"peak_equity": 1000.0, "day_start_equity": -3, "day_key": "2026-09-27"})),
    "missing_with_history": lambda j: j.kv_set("risk_state", ""),
}


def _restart_corrupt(journal, venue, monkeypatch, corruption, mutate=None,
                     market=MarketType.FUTURES):
    """Peak 1000 established, cycles journalled, then the blob is corrupted and
    the kernel restarts at equity 700 (a 30 % drawdown it must not forget)."""
    _protected(journal, venue)
    seed = RiskManager(RISK_CFG, journal)
    seed.update_equity(1000.0)
    _history(journal)
    CORRUPTIONS[corruption](journal)
    blob = journal.kv_get("risk_state")
    venue.equity = BREACH
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.market_type = market
    if mutate:
        mutate(k, monkeypatch)
    return k, blob


def _never_reseeded(k, blob):
    assert k.journal.kv_get("risk_state") == blob, "corrupt evidence overwritten"
    assert k.risk._peak_equity != BREACH, "corrupt state silently re-seeded at current equity"
    assert k.risk.baseline_status == "corrupt"


def check_corrupt_contained_everywhere(journal, venue, monkeypatch, mutate=None,
                                       corruption="malformed_json"):
    k, blob = _restart_corrupt(journal, venue, monkeypatch, corruption, mutate)
    mark = _last_event(journal)
    k.boot()                                                   # boot
    assert _state(journal) == "RECOVERY"
    assert "risk_state_corrupt" in _status(journal)["reasons"]
    for _ in range(3):                                         # cycles + cadence
        status = k._risk_step()[1]
        assert status["risk_state"] == "corrupt" and status["halt_breached"] is False
        k.supervisor._next_pass = 0
        res = k.supervisor.cycle()
        assert "risk_state_corrupt" in res.reasons
    r = k.owner_resume(OWNER)                                  # owner recovery
    assert r.status == "CONTAINED" and "risk_state_corrupt" in r.reasons
    k.state_machine.set(ControlState.HALTED, "operator", "hold")
    r = k.owner_resume(OWNER, allow_unhalt=True)               # owner unhalt
    assert r.status == "REFUSED" and r.reasons == ("risk_state_corrupt",)
    venue.equity = NEW_HIGH                                    # even a new high …
    k._risk_step()
    k.state_machine.set(ControlState.FROZEN, "operator", "hold")
    k.owner_resume(OWNER)
    k.supervisor._next_pass = 0
    k.supervisor.cycle()
    assert _no_activation_after(journal, mark), "corrupt Risk baseline reached ACTIVE"
    _never_reseeded(k, blob)
    return k, blob


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_corrupt_baseline_matrix_boot_cadence_owner(world, monkeypatch, corruption):
    journal, venue = world
    k, blob = check_corrupt_contained_everywhere(*world, monkeypatch, corruption=corruption)
    ev = _events(journal, "risk_state_corrupt")
    assert len(ev) == 1                                        # audited once per latch
    detail = json.loads(ev[-1]["detail"])
    # V2: the durable evidence is the initialization marker, not equity rows
    assert detail["memory_peak"] == 1000.0 or detail.get("marker")
    assert journal.kv_get("risk_state_corrupt_latch")                # durable latch


def _no_activation_after(journal, mark):
    return [t for t in _transitions(journal, mark) if t[1] == "ACTIVE"] == []


@pytest.mark.parametrize("corruption", sorted(CORRUPTIONS))
def test_corrupt_baseline_never_active_after_restart(world, monkeypatch, corruption):
    """The strict form: after the corrupt restart, no transition to ACTIVE at all."""
    journal, venue = world
    k, blob = _restart_corrupt(journal, venue, monkeypatch, corruption)
    mark = _last_event(journal)
    k.boot()
    for eq in (BREACH, CLEAR, NEW_HIGH):
        venue.equity = eq
        k._risk_step()
        k.supervisor._next_pass = 0
        k.supervisor.cycle()
        k.owner_resume(OWNER)
    assert _no_activation_after(journal, mark)
    _never_reseeded(k, blob)


def check_corrupt_active_kernel_contained(journal, venue, monkeypatch, mutate=None,
                                          market=MarketType.FUTURES):
    """No boot pass (spot, or a running kernel): the cycle's Risk step contains."""
    k, blob = _restart_corrupt(journal, venue, monkeypatch, "malformed_json", mutate, market)
    assert _state(journal) == "ACTIVE"
    mark = _last_event(journal)
    k._risk_step()
    assert _state(journal) == "FROZEN"
    assert _transitions(journal, mark) == [("ACTIVE", "FROZEN", "risk_engine")]
    venue.equity = NEW_HIGH
    for _ in range(3):
        k._risk_step()
        k.owner_resume(OWNER)
        k.supervisor._next_pass = 0
        k.supervisor.cycle()
    assert _no_activation_after(journal, mark), "corrupt baseline activated"
    _never_reseeded(k, blob)
    return k, blob


@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_corrupt_baseline_running_kernel_and_spot(world, monkeypatch, market):
    journal, venue = world
    k, blob = check_corrupt_active_kernel_contained(*world, monkeypatch, market=market)
    sizing = k.risk.check_entry(ControlState.ACTIVE, "ETH/USDT", 100.0, 1.0, .02,
                                [], NEW_HIGH, 50, market.value)
    assert not sizing.ok and sizing.reason == "risk_state=corrupt: entries blocked"


def test_corrupt_baseline_blocks_macro_release(world, monkeypatch):
    journal, venue = world
    k, eid = _macro_frozen(journal, venue, monkeypatch)
    _history(journal)
    journal.kv_set("risk_state", "{corrupt")                   # corrupted at runtime
    mark = _last_event(journal)
    for _ in range(3):
        k._risk_step()
        k._macro_step({"active": False})
    assert _no_activation_after(journal, mark) and _state(journal) == "FROZEN"
    assert journal.kv_get("risk_state") == "{corrupt"
    refused = [json.loads(e["detail"]) for e in _events(journal, "macro_release_refused")]
    assert refused[-1]["reasons"] == ["risk_state_corrupt"] and len(refused) == 1


def test_missing_state_with_history_is_corrupt_not_first_init(world, monkeypatch):
    journal, venue = world
    k, blob = _restart_corrupt(journal, venue, monkeypatch, "missing_with_history")
    assert k.risk.baseline_status == "corrupt"
    assert k.risk.release_check(CLEAR).reason == "risk_state_corrupt"
    assert json.loads(_events(journal, "risk_state_corrupt")[-1]["detail"])["cause"] == \
        "risk_state_missing"


def test_runtime_deletion_after_init_is_corrupt(tmp_path):
    """Initialized in this process, then the durable row vanishes: corrupt."""
    j = Journal(tmp_path / "j.db")
    risk = RiskManager(RISK_CFG, j)
    risk.update_equity(1000.0)
    with j._tx() as c:
        c.execute("DELETE FROM state_kv WHERE key='risk_state'")
    st = risk.update_equity(BREACH)
    assert st["risk_state"] == "corrupt" and j.kv_get("risk_state") is None
    assert risk.release_check(CLEAR).reason == "risk_state_corrupt"


def test_corrupt_latch_survives_a_valid_looking_rewrite(tmp_path):
    """Once latched, only repair_baseline() clears it — not a later valid blob."""
    j = Journal(tmp_path / "j.db")
    RiskManager(RISK_CFG, j).update_equity(1000.0)
    j.log_equity(1000.0, 1000.0, 0)
    j.kv_set("risk_state", "{corrupt")
    risk = RiskManager(RISK_CFG, j)
    j.kv_set("risk_state", json.dumps({"peak_equity": 700.0}))  # someone writes a low peak
    assert risk.update_equity(BREACH)["risk_state"] == "corrupt"
    assert risk.release_check(BREACH).reason == "risk_state_corrupt"


def test_repair_is_explicit_and_cannot_forget_the_drawdown(world, monkeypatch):
    journal, venue = world
    k, blob = _restart_corrupt(journal, venue, monkeypatch, "malformed_json")
    k.boot()
    with pytest.raises(ValueError, match="below proven high-water mark"):
        k.risk.repair_baseline(BREACH, actor="operator", reason="test")
    assert k.risk.baseline_status == "corrupt" and journal.kv_get("risk_state") == blob
    rec = k.risk.repair_baseline(1000.0, actor="operator", reason="restore peak")
    assert rec["proven_floor"] == 1000.0 and rec["prior"]["cause"] == "risk_state_malformed"
    assert _events(journal, "risk_state_repaired")
    venue.equity = BREACH
    assert k._risk_step()[1]["halt_breached"] is True             # drawdown remembered
    assert _state(journal) == "HALTED"
    assert k.owner_resume(OWNER, allow_unhalt=True).reasons == ("risk_halt_active",)
    venue.equity = CLEAR
    assert k.owner_resume(OWNER, allow_unhalt=True).status == "ACTIVATED"


# ── 7: genuine first initialization ─────────────────────────────────────────
@pytest.mark.parametrize("market", [MarketType.FUTURES, MarketType.SPOT])
def test_first_initialization_needs_no_owner(world, monkeypatch, market):
    journal, venue = world
    _protected(journal, venue)
    venue.equity = None                                        # fixture must not seed
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.market_type = market
    assert journal.kv_get("risk_state") is None and k.risk.baseline_status == "uninitialized"
    if market == MarketType.FUTURES:
        venue.equity = 1000.0
        k.boot()
        assert _state(journal) == "RECOVERY"
        assert "risk_baseline_uninitialized" in _status(journal)["reasons"]
    # the venue read fails on the first cycle: nothing is invented from fallback 0
    venue.equity = None
    status = k._risk_step()[1]
    assert status["risk_state"] == "uninitialized" and journal.kv_get("risk_state") is None
    assert k.risk.baseline_status == "uninitialized"
    venue.equity = 1000.0
    status = k._risk_step()[1]                                 # fresh read: baseline set
    assert status["risk_state"] == "ok" and status["drawdown_pct"] == 0.0
    assert json.loads(journal.kv_get("risk_state"))["peak_equity"] == 1000.0
    journal.log_equity(1000.0, 1000.0, 1)                      # as cycle() does
    if market == MarketType.FUTURES:
        k.supervisor._next_pass = 0
        assert k.supervisor.cycle().actions.get("activated") is True
    assert _state(journal) == "ACTIVE"
    assert _events(journal, "risk_state_corrupt") == []        # no fake corruption
    venue.equity = 1100.0
    k._risk_step()
    assert json.loads(journal.kv_get("risk_state"))["peak_equity"] == 1100.0
    assert k.risk.check_entry(ControlState.ACTIVE, "ETH/USDT", 100.0, 1.0, .02, [],
                              1100.0, 50, market.value).ok


def test_first_init_never_from_fallback_or_unusable_equity(tmp_path):
    risk = RiskManager(RISK_CFG, Journal(tmp_path / "j.db"))
    for eq in (0.0, -1.0, float("nan"), float("inf")):
        assert risk.update_equity(eq)["risk_state"] == "uninitialized"
    assert risk.update_equity(1000.0, authoritative=False)["risk_state"] == "uninitialized"
    assert risk.journal.kv_get("risk_state") is None
    assert risk.update_equity(1000.0)["risk_state"] == "ok"


# ── 8: baseline preservation across every release path ──────────────────────
def test_releases_never_move_the_baseline(world, monkeypatch):
    journal, venue = world
    k, eid = _macro_frozen(journal, venue, monkeypatch)
    venue.equity = BREACH
    k._macro_step({"active": False})                          # macro refused
    k._risk_step()                                            # HALTED (peak stays 1000)
    assert _risk_snapshot(k)[1] == 1000.0
    before = _risk_snapshot(k)
    k.owner_resume(OWNER, allow_unhalt=True)                  # refused
    k.market_type = MarketType.SPOT
    k.owner_resume(OWNER, allow_unhalt=True)                  # spot refused
    assert _risk_snapshot(k) == before
    venue.equity = CLEAR
    assert k.owner_resume(OWNER, allow_unhalt=True).status == "ACTIVATED"   # spot release
    assert _risk_snapshot(k) == before
    k.market_type = MarketType.FUTURES
    k._macro_step({"active": True, "event": "FOMC"})
    k._macro_step({"active": False})                          # futures macro release
    assert _state(journal) == "ACTIVE" and _risk_snapshot(k) == before
    assert json.loads(journal.kv_get("risk_state"))["peak_equity"] == 1000.0


# ── 10: previously approved entry reaches the executor boundary ─────────────
@pytest.mark.parametrize("path", ["macro", "spot_resume", "owner_unhalt"])
def test_previously_approved_entry_submits_nothing(world, monkeypatch, path):
    journal, venue = world
    from tests.authority_legacy_fixtures import seed
    seed(journal, "s")  # explicit TEST-ONLY authority; exercise the control/Risk fence
    k, eid = _macro_frozen(journal, venue, monkeypatch) if path == "macro" else (
        _boot(journal, venue, monkeypatch), None)
    sizing = RiskManager(RISK_CFG, journal).check_entry(
        ControlState.ACTIVE, "ETH/USDT", 100.0, 1.0, .02, [], 1000.0, 50, "futures")
    assert sizing.ok                                          # approved while ACTIVE
    venue.equity = BREACH
    k._risk_step()
    assert _state(journal) == "HALTED"
    mark = _last_event(journal)
    if path == "macro":
        k._macro_step({"active": False})
    elif path == "spot_resume":
        k.market_type = MarketType.SPOT
        tg(k, monkeypatch, "/resume")
        tg(k, monkeypatch, "/unhalt")
        k.market_type = MarketType.FUTURES
    else:
        k.owner_resume(OWNER, allow_unhalt=True)
    assert _activations(journal, mark) == [] and _state(journal) == "HALTED"
    d = Decision("d-approved", "c-1", "ETH/USDT", Action.BUY, .7, .2, .8, [], [])
    pos = k.executor.open(d, sizing.amount, 1.0, 98.0, 103.0, "s", "s")
    assert pos is None and d.skip_reason == "state=HALTED"
    assert [m for m in venue.mutations if m[0] == "create_order"] == []
    # Recorded separately (LUFFY-ENTRY-FENCE-SIDE-EFFECT-V1): leverage is still
    # applied before the final fence even though no order is sent.
    assert [m[0] for m in venue.mutations] == ["set_leverage"]


# ── 14: rollback with a corrupt baseline cannot activate ────────────────────
def test_rollback_with_corrupt_baseline_is_blocked_never_active(world, monkeypatch):
    journal, venue = world
    k, blob = _restart_corrupt(journal, venue, monkeypatch, "malformed_json")
    k.boot()
    mark = _last_event(journal)
    r = k.supervisor.prepare_rollback(OWNER)
    # V2: an unrepaired corrupt baseline is never READY for the old kernel
    assert r.status == "BLOCKED" and "risk_state_corrupt" in r.reasons
    assert _state(journal) == "RECOVERY"
    k.supervisor._next_pass = 0
    k.supervisor.cycle()
    k.supervisor.pass_once()
    k._risk_step()
    assert _state(journal) == "RECOVERY" and _no_activation_after(journal, mark)
    _never_reseeded(k, blob)


# ── 12: negative controls — each mutant removes one guard and MUST fail ─────
def mutant_macro_direct_active(k, monkeypatch):
    """The pre-fix MacroGuard: set ACTIVE on the observed FROZEN."""
    def release():
        k.state_machine.set(ControlState.ACTIVE, "macro_guard", "macro event cleared")
        k.journal.kv_set("macro_guard_froze", "0")
    monkeypatch.setattr(k, "_macro_release", release)


def mutant_macro_unbound(k, monkeypatch):
    """Risk-checked but not bound to MacroGuard's freeze (stale observation)."""
    def release(eid, venue_recovery=True):
        risk = k.supervisor._risk_now()
        if risk.allowed and persisted_state(k.journal) == ControlState.FROZEN:
            k.state_machine.set(ControlState.ACTIVE, "macro_guard", "cleared")
        return type("R", (), {"status": "ACTIVATED", "reasons": (),
                              "control_state": "ACTIVE"})()
    monkeypatch.setattr(k.supervisor, "request_macro_release", release)


def mutant_spot_direct_active(k, monkeypatch):
    real = k.owner_resume

    def resume(ctx, *, allow_unhalt=False, **bound):   # accepts the rev-4 admission binding
        if k.market_type != MarketType.FUTURES:
            k.state_machine.set(ControlState.ACTIVE, "operator", "telegram")
            return type("R", (), {"status": "ACTIVATED", "reasons": (), "request_event_id": None,
                                  "control_state": "ACTIVE", "outcome": None})()
        return real(ctx, allow_unhalt=allow_unhalt, **bound)
    monkeypatch.setattr(k, "owner_resume", resume)


def mutant_no_fresh_risk(k, monkeypatch):
    """Forged allow with an always-pass verifier (V2: removes the CAS check too)."""
    from contextlib import nullcontext
    monkeypatch.setattr(k.supervisor, "_risk_now", lambda: RiskRelease(
        True, "risk_release_ok", authoritative=True, verify=lambda _p: nullcontext(None)))


def _reseed_mutant(k, monkeypatch):
    """The pre-fix loader: a corrupt blob means 'start clean'."""
    real = RiskManager._classify

    def classify(self):
        status, state, detail = real(self)
        return ("uninitialized", None, None) if status == "corrupt" else (status, state, detail)
    monkeypatch.setattr(RiskManager, "_classify", classify)
    k.risk.baseline_status = "uninitialized"


def mutant_corrupt_reseeded(k, monkeypatch):
    _reseed_mutant(k, monkeypatch)


def mutant_corrupt_as_first_init(k, monkeypatch):
    """Missing blob with the initialization marker misread as a first run."""
    monkeypatch.setattr(RiskManager, "_MARKER_KEY", "no_such_marker")
    monkeypatch.setattr(RiskManager, "_LATCH_KEY", "no_such_latch")
    k.risk.baseline_status, k.risk._established = "uninitialized", False
    k.risk.corrupt_detail = None


def mutant_cadence_clears_corrupt(k, monkeypatch):
    """A cycle that 'heals' a corrupt baseline from the current equity."""
    real = k._risk_step

    def step():
        if k.risk.baseline_status == "corrupt":
            k.risk.baseline_status = "uninitialized"
            k.journal.kv_set("risk_state", "")
            with k.journal._tx() as c:
                c.execute("DELETE FROM equity")
        return real()
    monkeypatch.setattr(k, "_risk_step", step)


def _check_missing_first_init(journal, venue, monkeypatch, mutate):
    k, blob = _restart_corrupt(journal, venue, monkeypatch, "missing_with_history", mutate)
    mark = _last_event(journal)
    k.boot()
    venue.equity = CLEAR
    for _ in range(2):
        k._risk_step()
        k.supervisor._next_pass = 0
        k.supervisor.cycle()
    assert _no_activation_after(journal, mark), "corrupt treated as first initialization"


def _check_corrupt_strict(journal, venue, monkeypatch, mutate):
    k, blob = _restart_corrupt(journal, venue, monkeypatch, "malformed_json", mutate)
    mark = _last_event(journal)
    k.boot()
    for eq in (BREACH, CLEAR):
        venue.equity = eq
        k._risk_step()
        k.supervisor._next_pass = 0
        k.supervisor.cycle()
    assert _no_activation_after(journal, mark), "corrupt baseline reached ACTIVE"
    _never_reseeded(k, blob)


def _check_spot_breach(journal, venue, monkeypatch, mutate):
    check_spot_resume_contained(journal, venue, monkeypatch, mutate)


def _check_macro_stale(journal, venue, monkeypatch, mutate):
    check_macro_newer_intent_wins(journal, venue, monkeypatch, mutate,
                                  new=ControlState.FROZEN)


CONTROLS = [
    (check_macro_breach_contained, mutant_macro_direct_active, "macro_direct_active"),
    (_check_macro_stale, mutant_macro_direct_active, "macro_direct_active:stale"),
    (_check_macro_stale, mutant_macro_unbound, "macro_stale_observation"),
    (check_macro_breach_contained, mutant_no_fresh_risk, "macro_without_fresh_risk"),
    (_check_spot_breach, mutant_spot_direct_active, "spot_direct_active"),
    (_check_spot_breach, mutant_no_fresh_risk, "spot_without_fresh_risk"),
    (check_spot_newer_halted_wins, mutant_spot_direct_active, "spot_overwrites_halted"),
    (_check_corrupt_strict, mutant_corrupt_reseeded, "corrupt_reseeded"),
    (_check_missing_first_init, mutant_corrupt_as_first_init, "corrupt_as_first_init"),
    (_check_corrupt_strict, mutant_cadence_clears_corrupt, "cadence_clears_corrupt"),
    (_check_corrupt_strict, mutant_no_fresh_risk, "corrupt_without_fresh_risk"),
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
