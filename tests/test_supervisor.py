"""Supervisor containment, durable evidence and positive activation gates."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState, MarketType
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import KEY, Supervisor


class Venue:
    def __init__(self):
        self.positions = []
        self.stops = []
        self.unreadable = False
        self.open_order_symbols = []

    def fetch_positions(self):
        if self.unreadable:
            raise OSError("venue offline")
        return self.positions

    def fapiPrivateGetOpenAlgoOrders(self):
        if self.unreadable:
            raise OSError("venue offline")
        return self.stops

    def fetch_open_orders(self, symbol):
        if symbol is None:
            raise AssertionError("ccxt Binance USDM rejects fetch_open_orders(None)")
        self.open_order_symbols.append(symbol)
        return []


class BinanceVenue(Venue):
    def fapiPrivateDeleteAlgoOrder(self, params):
        aid = str(params["algoId"])
        self.stops = [stop for stop in self.stops
                      if str(stop["algoId"]) != aid]
        return {"algoId": aid}


@pytest.fixture
def system(tmp_path):
    journal = Journal(tmp_path / "j.db")
    state = ControlStateMachine(journal)
    venue = Venue()
    pending = {"value": None}
    executor = SimpleNamespace(
        recovery=SimpleNamespace(pending=lambda: pending["value"]),
        recover_entries=Mock())
    supervisor = Supervisor(journal, state, executor, venue, interval_s=0)
    return journal, state, venue, pending, executor, supervisor


def _transitions(journal):
    return [(row["from_state"], row["to_state"]) for row in journal.query(
        "SELECT from_state, to_state FROM control_events "
        "WHERE event='state_change' ORDER BY id")]


def test_serious_active_fault_contains_before_recovery(system):
    journal, state, venue, _, _, supervisor = system
    venue.unreadable = True
    result = supervisor.pass_once(boot=True)
    assert _transitions(journal) == [("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY")]
    assert state.refresh() == ControlState.RECOVERY
    assert result.needs_owner and not result.safe_to_activate
    assert "venue_state_unreadable" in result.reasons
    assert result.checks["venue_positions"] is False


def test_clean_boot_proves_safety_without_recovery(system):
    journal, state, venue, _, _, supervisor = system
    result = supervisor.pass_once(boot=True)
    assert result.outcome == "SAFE" and result.stage == "COMPLETE"
    assert result.safe_to_activate and not result.needs_owner
    assert _transitions(journal) == [("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY"),
                                     ("RECOVERY", "ACTIVE")]
    assert state.refresh() == ControlState.ACTIVE
    assert venue.open_order_symbols == []


def test_binance_style_live_protected_position_verifies(system):
    from tests.test_reconcile import _pos
    journal, state, venue, _, _, supervisor = system
    journal.add_trade(_pos(sl=95, sl_order_id="stop-1"))
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    venue.stops = [{"algoId": "stop-1", "symbol": "BTCUSDT", "side": "SELL",
                    "reduceOnly": True, "orderType": "STOP_MARKET",
                    "quantity": "1", "triggerPrice": "95"}]
    result = supervisor.pass_once(boot=True)
    assert result.safe_to_activate and state.refresh() == ControlState.ACTIVE
    assert venue.open_order_symbols == ["BTC/USDT"]


def test_binance_style_naked_position_rearms_without_global_ordinary_call(system):
    journal, state, venue, supervisor = _live_rearm_case(system)
    result = supervisor.pass_once(boot=True)
    assert "protection_awaiting_verification" in result.reasons
    assert not result.safe_to_activate and state.refresh() == ControlState.RECOVERY
    venue.create_order.assert_called_once()
    assert venue.open_order_symbols and set(venue.open_order_symbols) == {"BTC/USDT"}


def test_binance_style_orphan_algo_stop_is_swept(system):
    journal, state, _, pending, executor, _ = system
    venue = BinanceVenue()
    venue.stops = [{"algoId": "123", "symbol": "BTCUSDT", "side": "SELL",
                    "reduceOnly": True, "orderType": "STOP_MARKET",
                    "quantity": "1", "triggerPrice": "95"}]
    supervisor = Supervisor(journal, state, executor, venue, interval_s=0)
    result = supervisor.pass_once(boot=True)
    assert result.safe_to_activate and result.actions["reconcile"]["stops_swept"] == 1
    assert venue.stops == [] and venue.open_order_symbols == []


def test_safe_recovery_can_activate(system):
    journal, state, _, _, _, supervisor = system
    supervisor.trigger("explicit_boot_recovery")
    assert _transitions(journal)[:2] == [("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY")]
    result = supervisor.pass_once()
    assert result.safe_to_activate and state.refresh() == ControlState.ACTIVE


def test_pending_entry_intent_blocks_until_resolved(system):
    _, state, _, pending, _, supervisor = system
    pending["value"] = {"symbol": "BTC/USDT", "reason": "entry_order_not_terminal",
                        "attempts": 1}
    result = supervisor.pass_once(boot=True)
    assert result.outcome == "RECOVERING" and not result.safe_to_activate
    assert "entry_recovery_pending" in result.reasons
    assert state.refresh() == ControlState.RECOVERY
    pending["value"] = None
    assert supervisor.pass_once().safe_to_activate
    assert state.refresh() == ControlState.ACTIVE


def test_entry_resolved_in_first_pass_still_enters_recovery(system):
    journal, state, _, pending, executor, supervisor = system
    pending["value"] = {"symbol": "BTC/USDT", "reason": "entry_order_not_terminal"}
    executor.recover_entries.side_effect = lambda: pending.update(value=None)
    result = supervisor.pass_once(boot=True)
    assert result.safe_to_activate and state.refresh() == ControlState.ACTIVE
    assert _transitions(journal) == [
        ("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY"), ("RECOVERY", "ACTIVE")]


def test_unreadable_entry_ledger_requires_owner(system):
    _, state, _, _, executor, supervisor = system
    executor.recovery.pending = Mock(side_effect=ValueError("corrupt"))
    result = supervisor.pass_once(boot=True)
    assert result.needs_owner and "critical_recovery_state_unreadable" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def test_naked_position_requires_owner_and_persists_across_restart(system):
    journal, state, venue, _, executor, supervisor = system
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    result = supervisor.pass_once(boot=True)
    assert result.needs_owner and not result.safe_to_activate
    assert "protection_cannot_restore" in result.reasons
    assert state.refresh() == ControlState.RECOVERY
    restarted = Supervisor(Journal(journal.db_path), ControlStateMachine(Journal(journal.db_path)),
                           executor, venue)
    saved = restarted.status()
    assert saved["needs_owner"] is True
    assert "protection_cannot_restore" in saved["reasons"]
    assert json.loads(journal.kv_get(KEY))["outcome"] == "NEEDS_OWNER"
    # Even if the venue later looks healthy, a serious owner hold is durable.
    venue.positions = []
    assert restarted.pass_once().needs_owner
    assert restarted.state_machine.refresh() == ControlState.RECOVERY


def test_corrupt_critical_status_fails_closed(system):
    journal, state, _, _, _, supervisor = system
    journal.kv_set(KEY, "{")
    result = supervisor.pass_once(boot=True)
    assert result.needs_owner and "supervisor_status_unreadable" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def test_repeated_entry_failure_requires_owner(system):
    _, state, _, pending, _, supervisor = system
    pending["value"] = {"symbol": "BTC/USDT", "reason": "entry_order_not_terminal",
                        "attempts": 3}
    result = supervisor.pass_once(boot=True)
    assert result.needs_owner and "entry_recovery_owner_required" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def test_contradictory_position_ownership_requires_owner(system):
    from tests.test_reconcile import _pos
    journal, state, venue, _, _, supervisor = system
    journal.add_trade(_pos(sl=95))
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "short",
                        "entryPrice": 100, "markPrice": 100}]
    result = supervisor.pass_once(boot=True)
    assert result.needs_owner and not result.safe_to_activate
    assert "contradictory_position_side:BTC/USDT" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def test_wrong_side_stop_is_not_proven_protection(system):
    from tests.test_reconcile import _pos
    journal, state, venue, _, _, supervisor = system
    journal.add_trade(_pos(sl=95, sl_order_id="sl_001"))
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    venue.stops = [{"algoId": "sl_001", "symbol": "BTCUSDT", "side": "buy",
                    "reduceOnly": True, "orderType": "STOP_MARKET",
                    "quantity": "1", "triggerPrice": "95"}]
    result = supervisor.pass_once(boot=True)
    assert result.needs_owner and "position_unprotected:BTC/USDT" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def _live_rearm_case(system):
    from tests.test_reconcile import _pos
    journal, state, venue, _, _, supervisor = system
    journal.add_trade(_pos(sl=95, sl_order_id="old"))
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    venue.create_order = Mock(return_value={"id": "fresh"})
    return journal, state, venue, supervisor


def test_rearmed_stop_awaits_listing_then_allows_safe_recovery(system):
    _, state, venue, supervisor = _live_rearm_case(system)
    first = supervisor.pass_once(boot=True)
    assert first.outcome == "RECOVERING" and not first.safe_to_activate
    assert not first.needs_owner
    assert "protection_awaiting_verification" in first.reasons
    assert "position_unprotected:BTC/USDT" not in first.reasons
    assert state.refresh() == ControlState.RECOVERY
    venue.stops = [{"algoId": "fresh", "symbol": "BTCUSDT", "side": "SELL",
                    "reduceOnly": True, "orderType": "STOP_MARKET",
                    "quantity": "1", "triggerPrice": "95"}]
    second = supervisor.pass_once()
    assert second.safe_to_activate and not second.needs_owner
    assert state.refresh() == ControlState.ACTIVE
    venue.create_order.assert_called_once()


def test_awaiting_verification_never_places_duplicate_stop(system):
    _, state, venue, supervisor = _live_rearm_case(system)
    first = supervisor.pass_once(boot=True)
    assert "protection_awaiting_verification" in first.reasons
    second = supervisor.pass_once()
    assert not second.safe_to_activate and state.refresh() == ControlState.RECOVERY
    venue.create_order.assert_called_once()


def test_listed_rearm_without_journal_id_stays_contained(system, monkeypatch):
    journal, state, venue, supervisor = _live_rearm_case(system)
    monkeypatch.setattr(journal, "record_stop_order", Mock(side_effect=OSError("db write")))
    first = supervisor.pass_once(boot=True)
    assert "protection_awaiting_verification" in first.reasons
    venue.stops = [{"algoId": "fresh", "symbol": "BTCUSDT", "side": "SELL",
                    "reduceOnly": True, "orderType": "STOP_MARKET",
                    "quantity": "1", "triggerPrice": "95"}]
    second = supervisor.pass_once()
    assert not second.safe_to_activate and second.needs_owner
    assert "protection_cannot_restore" in second.reasons
    assert state.refresh() == ControlState.RECOVERY
    venue.create_order.assert_called_once()


def test_explicit_rearm_failure_remains_unsafe(system):
    _, state, venue, supervisor = _live_rearm_case(system)
    venue.create_order.side_effect = RuntimeError("rejected")
    result = supervisor.pass_once(boot=True)
    assert not result.safe_to_activate and result.needs_owner
    assert "protection_cannot_restore" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def test_supervisor_reconcile_uses_canonical_protection_match(system, monkeypatch):
    from trader.engine import protective
    journal, _, venue, supervisor = _live_rearm_case(system)
    venue.stops = [{"algoId": "old", "symbol": "BTCUSDT", "side": "SELL",
                    "reduceOnly": True, "orderType": "STOP_MARKET",
                    "quantity": "2", "triggerPrice": "95"}]
    real_match = protective.protection_match
    seen = []

    def match(*args):
        seen.append(args)
        return real_match(*args)

    monkeypatch.setattr(protective, "protection_match", match)
    result = supervisor.pass_once(boot=True)
    assert result.safe_to_activate and seen and seen[0][3] == 1
    venue.create_order.assert_not_called()


def test_flat_account_failed_orphan_cancellation_is_not_safe(system):
    _, state, venue, _, _, supervisor = system
    venue.stops = [{"algoId": "1", "symbol": "BTCUSDT", "side": "SELL",
                    "reduceOnly": True, "orderType": "STOP_MARKET",
                    "quantity": "1", "triggerPrice": "95"}]
    result = supervisor.pass_once(boot=True)
    assert not result.safe_to_activate and result.needs_owner
    assert "orphan_stop_sweep_unresolved" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def test_flat_account_unreadable_protection_snapshot_is_not_safe(system):
    _, state, venue, _, _, supervisor = system
    venue.fapiPrivateGetOpenAlgoOrders = Mock(side_effect=OSError("unreadable"))
    result = supervisor.pass_once(boot=True)
    assert not result.safe_to_activate and result.needs_owner
    assert "protection_snapshot_unreadable" in result.reasons
    assert state.refresh() == ControlState.RECOVERY


def test_flat_venue_without_global_listing_is_not_safe(system):
    journal, state, _, _, executor, _ = system
    venue = Venue()
    venue.fapiPrivateGetOpenAlgoOrders = None
    supervisor = Supervisor(journal, state, executor, venue, interval_s=0)
    result = supervisor.pass_once(boot=True)
    assert not result.safe_to_activate and result.needs_owner
    assert "protection_snapshot_unreadable" in result.reasons
    assert result.actions["reconcile"]["orphan_sweep_reason"] == (
        "global_stop_listing_unsupported")
    assert result.actions["reconcile"]["protection_snapshot_reason"] == (
        "global_stop_listing_unsupported")
    assert venue.open_order_symbols == []
    assert state.refresh() == ControlState.RECOVERY


def test_recovery_reconciliation_has_cadence(system):
    _, state, venue, _, _, supervisor = system
    venue.unreadable = True
    supervisor.interval_s = 60
    supervisor.pass_once(boot=True)
    assert state.refresh() == ControlState.RECOVERY
    assert supervisor.cycle() is None


def test_kernel_boot_uses_supervisor_without_real_services(system, monkeypatch):
    from trader.kernel import Kernel
    journal, state, venue, _, executor, supervisor = system
    k = object.__new__(Kernel)
    k.market_type = MarketType.FUTURES
    k.state_machine = state
    k.population = []
    k._filter_universe_to_venue = Mock()
    k.supervisor = supervisor
    k.notifier = SimpleNamespace(send=Mock())
    k.heartbeat = object()
    k.cfg = {"timeframes": {"scan_interval_seconds": 60}}
    monkeypatch.setattr("trader.kernel.start_stall_monitor", lambda *a, **kw: None)
    monkeypatch.setattr("trader.kernel.signal.signal", lambda *a, **kw: None)
    monkeypatch.setattr("trader.kernel.threading.Thread",
                        lambda *a, **kw: SimpleNamespace(start=lambda: None))
    k.boot()
    assert state.refresh() == ControlState.ACTIVE
    assert _transitions(journal)[-1] == ("RECOVERY", "ACTIVE")
    assert json.loads(journal.kv_get(KEY))["safe_to_activate"] is True


@pytest.mark.parametrize("owner_state", [ControlState.FROZEN, ControlState.HALTED])
def test_owner_change_during_reconcile_wins(system, monkeypatch, owner_state):
    journal, state, _, _, _, supervisor = system
    owner = ControlStateMachine(Journal(journal.db_path))

    def network(*args, **kwargs):
        owner.set(owner_state, "operator")
        return {"positions_readable": True, "safety_issues": []}

    monkeypatch.setattr("trader.engine.supervisor.reconcile_futures", network)
    result = supervisor.pass_once(boot=True)
    assert state.refresh() == owner_state
    assert result.actions["lost_control_race"] is True
    assert not result.safe_to_activate
    assert _transitions(journal)[-1][1] == owner_state.value


def test_owner_frozen_with_pending_recovery_never_auto_resumes(system):
    journal, state, _, pending, executor, supervisor = system
    state.set(ControlState.FROZEN, "operator")
    pending["value"] = {"symbol": "BTC/USDT", "reason": "entry_order_not_terminal"}
    result = supervisor.pass_once(boot=True)
    pending["value"] = None
    second = supervisor.pass_once()
    assert state.refresh() == ControlState.FROZEN
    assert not result.containment_owned and not second.safe_to_activate
    assert not any(row == ("FROZEN", "RECOVERY") for row in _transitions(journal))


@pytest.mark.parametrize("stale_outcome", ["RECOVERING", "NEEDS_OWNER"])
def test_stale_supervisor_evidence_cannot_promote_owner_frozen(system, stale_outcome):
    journal, state, _, _, _, supervisor = system
    state.set(ControlState.FROZEN, "operator")
    journal.kv_set(KEY, json.dumps({"outcome": stale_outcome,
                                    "containment_owned": True,
                                    "needs_owner": stale_outcome == "NEEDS_OWNER",
                                    "reasons": []}))
    supervisor.pass_once()
    assert state.refresh() == ControlState.FROZEN
    assert _transitions(journal) == [("ACTIVE", "FROZEN")]


def test_only_supervisor_owned_recovery_auto_activates(system):
    journal, state, _, _, _, supervisor = system
    state.set(ControlState.FROZEN, "operator")
    state.set(ControlState.RECOVERY, "operator")
    assert not supervisor.pass_once().safe_to_activate
    assert state.refresh() == ControlState.RECOVERY
    state.set(ControlState.ACTIVE, "operator")
    supervisor.trigger("owned")
    assert supervisor.pass_once().safe_to_activate
    assert state.refresh() == ControlState.ACTIVE


def test_unreadable_ledger_has_no_reconcile_mutations(system, monkeypatch):
    _, state, venue, _, executor, supervisor = system
    executor.recovery.pending = Mock(side_effect=ValueError("corrupt"))
    reconcile = Mock(side_effect=AssertionError("mutating reconcile called"))
    monkeypatch.setattr("trader.engine.supervisor.reconcile_futures", reconcile)
    venue.stops = [{"algoId": "preserve"}]
    result = supervisor.pass_once(boot=True)
    assert result.needs_owner and not result.safe_to_activate
    assert "critical_recovery_state_unreadable" in result.reasons
    executor.recover_entries.assert_not_called()
    reconcile.assert_not_called()
    assert venue.stops == [{"algoId": "preserve"}]
    assert state.refresh() == ControlState.RECOVERY


def test_halted_boot_with_pending_recovery_does_no_recovery(system, monkeypatch):
    _, state, _, pending, executor, supervisor = system
    state.set(ControlState.HALTED, "operator")
    pending["value"] = {"symbol": "BTC/USDT"}
    reconcile = Mock(side_effect=AssertionError("reconcile called"))
    monkeypatch.setattr("trader.engine.supervisor.reconcile_futures", reconcile)
    result = supervisor.pass_once(boot=True)
    executor.recover_entries.assert_not_called()
    reconcile.assert_not_called()
    assert state.refresh() == ControlState.HALTED
    assert not result.safe_to_activate


@pytest.mark.parametrize("actor", ["operator", "dashboard", "chat"])
def test_owner_hold_persists_until_later_owner_active_and_restart(system, actor):
    journal, state, venue, _, executor, supervisor = system
    venue.unreadable = True
    first = supervisor.pass_once(boot=True)
    assert first.needs_owner and first.needs_owner_since_control_event_id is not None
    venue.unreadable = False
    assert supervisor.pass_once().needs_owner
    state.set(ControlState.ACTIVE, actor)
    cleared = supervisor.pass_once()
    assert not cleared.needs_owner
    assert cleared.actions["owner_ack_control_event_id"] > first.needs_owner_since_control_event_id
    assert state.refresh() == ControlState.ACTIVE
    restarted = Supervisor(Journal(journal.db_path), ControlStateMachine(Journal(journal.db_path)),
                           executor, venue)
    assert not restarted.pass_once(boot=True).needs_owner
    assert journal.query("SELECT id FROM control_events "
                         "WHERE event='supervisor_owner_acknowledged'")


@pytest.mark.parametrize("actor", ["macro_guard", "risk_engine", "watchdog", "supervisor"])
def test_system_active_does_not_acknowledge_hold(system, actor):
    _, state, venue, _, _, supervisor = system
    venue.unreadable = True
    supervisor.pass_once(boot=True)
    venue.unreadable = False
    state.set(ControlState.ACTIVE, actor)
    assert supervisor.pass_once().needs_owner


def test_activation_cas_loses_to_other_process(system, monkeypatch):
    journal, state, _, _, _, supervisor = system
    other = ControlStateMachine(Journal(journal.db_path))
    original = state.set_if_current

    def interleave(expected, new, actor, detail="", **kwargs):
        if expected == ControlState.RECOVERY and new == ControlState.ACTIVE:
            other.set(ControlState.HALTED, "operator")
        return original(expected, new, actor, detail, **kwargs)

    monkeypatch.setattr(state, "set_if_current", interleave)
    result = supervisor.pass_once(boot=True)
    assert state.refresh() == ControlState.HALTED
    assert result.actions["lost_control_race"] is True
    assert not result.safe_to_activate
    assert _transitions(journal)[-1] == ("RECOVERY", "HALTED")


def test_failed_safety_evidence_write_blocks_activation(system, monkeypatch):
    _, state, _, _, _, supervisor = system
    save = supervisor._save

    def fail_proof(result):
        if result.stage == "PROVED":
            raise OSError("durable evidence unavailable")
        return save(result)

    monkeypatch.setattr(supervisor, "_save", fail_proof)
    with pytest.raises(OSError, match="durable evidence unavailable"):
        supervisor.pass_once(boot=True)
    assert state.refresh() == ControlState.RECOVERY


@pytest.mark.parametrize("actor", ["operator", "chat"])
def test_owner_reasserts_frozen_before_recovery_transition(system, monkeypatch, actor):
    journal, state, _, _, _, supervisor = system
    owner = ControlStateMachine(Journal(journal.db_path))
    save = supervisor._save

    def owner_hold_after_containment(result):
        saved = save(result)
        if result.stage == "CONTAIN":
            owner.set(ControlState.FROZEN, actor, "keep frozen")
        return saved

    monkeypatch.setattr(supervisor, "_save", owner_hold_after_containment)
    result = supervisor.pass_once(boot=True)
    assert state.refresh() == ControlState.FROZEN
    assert not result.containment_owned
    assert not result.safe_to_activate
    assert journal.query("SELECT id FROM control_events WHERE event='state_hold' "
                         "AND actor=?", (actor,))


def test_owner_hold_immediately_after_containment_cas_keeps_exact_event(system, monkeypatch):
    journal, state, _, _, _, supervisor = system
    owner = ControlStateMachine(Journal(journal.db_path))
    original = state.set_if_current

    def interleave(expected, new, actor, detail="", **kwargs):
        transition = original(expected, new, actor, detail, **kwargs)
        if expected == ControlState.ACTIVE and new == ControlState.FROZEN:
            owner.set(ControlState.FROZEN, "operator", "immediate hold")
        return transition

    monkeypatch.setattr(state, "set_if_current", interleave)
    result = supervisor.pass_once(boot=True)
    change_id = journal.query("SELECT id FROM control_events WHERE event='state_change' "
                              "AND actor='supervisor' ORDER BY id LIMIT 1")[0]["id"]
    hold_id = journal.query("SELECT id FROM control_events WHERE event='state_hold' "
                            "ORDER BY id LIMIT 1")[0]["id"]
    containment = [json.loads(row["detail"]) for row in journal.query(
        "SELECT detail FROM control_events WHERE event='supervisor_recovery' ORDER BY id")
        if json.loads(row["detail"]).get("stage") == "CONTAIN"]
    assert containment[0]["containment_event_id"] == change_id
    assert hold_id > change_id
    assert state.refresh() == ControlState.FROZEN
    assert ("FROZEN", "RECOVERY") not in _transitions(journal)


def test_owner_hold_between_ownership_check_and_recovery_cas(system, monkeypatch):
    journal, state, _, _, _, supervisor = system
    owner = ControlStateMachine(Journal(journal.db_path))
    original = state.set_if_current

    def interleave(expected, new, actor, detail="", **kwargs):
        if expected == ControlState.FROZEN and new == ControlState.RECOVERY:
            owner.set(ControlState.FROZEN, "operator", "hold in CAS window")
        return original(expected, new, actor, detail, **kwargs)

    monkeypatch.setattr(state, "set_if_current", interleave)
    result = supervisor.pass_once(boot=True)
    assert state.refresh() == ControlState.FROZEN
    assert not result.containment_owned and not result.safe_to_activate
    assert _transitions(journal) == [("ACTIVE", "FROZEN")]


def test_owner_hold_during_recovery_network_work_blocks_activation(system, monkeypatch):
    journal, state, _, _, _, supervisor = system
    owner = ControlStateMachine(Journal(journal.db_path))

    def network(*args, **kwargs):
        owner.set(ControlState.FROZEN, "dashboard", "hold during verification")
        return {"positions_readable": True, "safety_issues": []}

    monkeypatch.setattr("trader.engine.supervisor.reconcile_futures", network)
    result = supervisor.pass_once(boot=True)
    assert state.refresh() == ControlState.FROZEN
    assert not result.containment_owned and not result.safe_to_activate
    assert result.actions["lost_control_race"] is True


def _acknowledged_owner_hold(system):
    journal, state, venue, pending, executor, supervisor = system
    venue.unreadable = True
    assert supervisor.pass_once(boot=True).needs_owner
    venue.unreadable = False
    state.set(ControlState.ACTIVE, "operator", "resume previous hold")
    restarted = Supervisor(Journal(journal.db_path),
                           ControlStateMachine(Journal(journal.db_path)),
                           executor, venue, interval_s=0)
    return journal, state, venue, pending, executor, restarted


def test_restart_acknowledged_hold_with_pending_entry_is_contained(system):
    journal, state, _, pending, executor, restarted = _acknowledged_owner_hold(system)
    pending["value"] = {"symbol": "BTC/USDT", "reason": "entry_order_not_terminal",
                        "attempts": 1}
    result = restarted.pass_once(boot=True)
    assert not result.safe_to_activate and not result.needs_owner
    assert "entry_recovery_pending" in result.reasons
    assert result.actions["owner_ack_control_event_id"]
    assert state.refresh() == ControlState.RECOVERY
    assert _transitions(journal)[-2:] == [("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY")]


def test_restart_acknowledged_hold_with_awaiting_stop_is_contained(system):
    from tests.test_reconcile import _pos
    journal, state, venue, _, _, restarted = _acknowledged_owner_hold(system)
    journal.add_trade(_pos(sl=95, sl_order_id="fresh"))
    journal.kv_set("reconcile_rearm_submitted", json.dumps({
        "BTC/USDT": {"trade_id": journal.open_trades()[0]["id"],
                     "order_id": "fresh", "amount": 1, "stop_price": 95}}))
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    result = restarted.pass_once(boot=True)
    assert not result.safe_to_activate
    assert state.refresh() == ControlState.RECOVERY
    assert result.actions["owner_ack_control_event_id"]


def test_restart_acknowledged_hold_with_clean_venue_is_proved(system):
    journal, state, _, _, _, restarted = _acknowledged_owner_hold(system)
    result = restarted.pass_once(boot=True)
    assert result.safe_to_activate and not result.needs_owner
    assert result.actions["owner_ack_control_event_id"]
    assert state.refresh() == ControlState.ACTIVE
    assert _transitions(journal)[-3:] == [
        ("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY"), ("RECOVERY", "ACTIVE")]


def test_trigger_after_owner_ack_does_not_require_second_ack(system):
    journal, state, _, _, _, restarted = _acknowledged_owner_hold(system)
    restarted.trigger("new recovery episode")
    assert state.refresh() == ControlState.RECOVERY
    assert restarted.status()["needs_owner"] is False
    assert restarted.pass_once().safe_to_activate
    assert state.refresh() == ControlState.ACTIVE
    assert journal.query("SELECT id FROM control_events "
                         "WHERE event='supervisor_owner_acknowledged'")


def test_owner_active_during_fault_observation_is_not_refrozen(system, monkeypatch):
    journal, state, _, _, _, supervisor = system
    owner = ControlStateMachine(Journal(journal.db_path))

    def network(*args, **kwargs):
        owner.set(ControlState.ACTIVE, "dashboard")
        return {"positions_readable": False, "error": "unavailable"}

    monkeypatch.setattr("trader.engine.supervisor.reconcile_futures", network)
    result = supervisor.pass_once(boot=True)
    assert state.refresh() == ControlState.ACTIVE
    assert result.needs_owner and result.actions["lost_control_race"]
    assert _transitions(journal)[-1] == ("RECOVERY", "ACTIVE")
