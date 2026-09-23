"""RECOVERY control state (SDD v3.2 §27): no new entries; exits, protection and
reconciliation continue; entered only after containment, never from ACTIVE."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from trader.core.types import Action, ControlState, Decision, MarketType
from trader.engine.state import VALID_TRANSITIONS, ControlStateMachine

A, F, H, R = (ControlState.ACTIVE, ControlState.FROZEN,
              ControlState.HALTED, ControlState.RECOVERY)


@pytest.fixture
def journal(tmp_path):
    from trader.core.journal import Journal
    return Journal(tmp_path / "j.db")


def _at(journal, *path):
    sm = ControlStateMachine(journal)
    for s in path:
        sm.set(s, "test")
    return sm


def test_recovery_persists_and_reloads(journal):
    sm = _at(journal, F, R)
    assert sm.state == R
    assert journal.kv_get("control_state") == "RECOVERY"
    assert ControlStateMachine(journal).state == R          # restart
    ev = journal.query("SELECT from_state, to_state FROM control_events "
                       "WHERE event='state_change' ORDER BY id DESC LIMIT 1")
    assert dict(ev[0]) == {"from_state": "FROZEN", "to_state": "RECOVERY"}


def test_refresh_sees_recovery_written_by_another_process(journal):
    kernel_sm = ControlStateMachine(journal)
    assert kernel_sm.can_enter()
    _at(journal, F, R)                         # e.g. dashboard/supervisor
    assert kernel_sm.refresh() == R
    assert not kernel_sm.can_enter() and kernel_sm.manages_exits()


@pytest.mark.parametrize("state,enter,exits", [
    (A, True, True), (F, False, True), (H, False, False), (R, False, True)])
def test_entry_and_exit_permissions(journal, state, enter, exits):
    journal.kv_set("control_state", state.value)
    sm = ControlStateMachine(journal)
    assert sm.can_enter() is enter
    assert sm.manages_exits() is exits


def test_transition_table_exact():
    assert VALID_TRANSITIONS == {
        A: {F, H},
        F: {A, H, R},
        H: {F, A, R},
        R: {F, A, H},
    }


@pytest.mark.parametrize("path", [
    (F, R), (H, R), (F, R, F), (F, R, A), (F, R, H), (H, R, A)])
def test_specified_transitions(journal, path):
    sm = _at(journal, *path)
    assert sm.state == path[-1]
    assert ControlStateMachine(journal).state == path[-1]


def test_active_to_recovery_is_refused(journal):
    sm = ControlStateMachine(journal)
    with pytest.raises(ValueError, match="illegal transition"):
        sm.set(R, "test")
    assert sm.state == A
    assert journal.kv_get("control_state", "ACTIVE") == "ACTIVE"


def test_compare_and_set_refreshes_cross_process_state_and_leaves_no_event(journal):
    stale = ControlStateMachine(journal)
    owner = ControlStateMachine(journal)
    owner.set(F, "operator")
    before = journal.query("SELECT COUNT(*) AS n FROM control_events WHERE event='state_change'")[0]["n"]
    assert not stale.set_if_current(A, H, "supervisor", "stale observation")
    assert stale.refresh() == F
    assert journal.query("SELECT COUNT(*) AS n FROM control_events WHERE event='state_change'")[0]["n"] == before
    assert stale.set_if_current(F, R, "supervisor", "fresh observation")
    assert owner.refresh() == R


def test_compare_and_set_refuses_unreadable_persisted_state(journal):
    sm = ControlStateMachine(journal)
    journal.kv_set("control_state", "INVALID")
    assert not sm.set_if_current(A, F, "supervisor")
    assert journal.kv_get("control_state") == "INVALID"
    assert not journal.query("SELECT id FROM control_events WHERE event='state_change'")


def test_compare_and_set_returns_exact_event_and_refuses_stale_intent(journal):
    sm = ControlStateMachine(journal)
    owner = ControlStateMachine(journal)
    first = sm.set_if_current(A, F, "supervisor")
    assert first.changed and first.state == F and first.event_id
    row = journal.query("SELECT event, actor FROM control_events WHERE id=?",
                        (first.event_id,))[0]
    assert row["event"] == "state_change" and row["actor"] == "supervisor"
    owner.set(F, "operator", "explicit hold")
    refused = sm.set_if_current(F, R, "supervisor",
                                expected_control_event_id=first.event_id)
    assert not refused.changed and refused.state == F and refused.event_id is None
    assert sm.refresh() == F
    hold_id = journal.query("SELECT MAX(id) AS id FROM control_events "
                            "WHERE event='state_hold'")[0]["id"]
    assert hold_id > first.event_id
    resumed = sm.set_if_current(F, R, "supervisor",
                                expected_control_event_id=hold_id)
    assert resumed.changed and resumed.state == R and resumed.event_id > hold_id


def test_risk_check_entry_blocks_recovery(journal):
    from trader.core.config import load_config
    from trader.engine.risk import RiskManager
    rm = RiskManager(load_config(), journal)
    r = rm.check_entry(R, "BTC/USDT", 100.0, 1.0, 0.02, [], 2000.0, 100,
                       "futures")
    assert not r.ok and "RECOVERY" in r.reason and r.size_usdt == 0


class _KV:
    """Dict-backed journal satisfying both the kernel cycle and the SM."""
    def __init__(self, **kv):
        self.kv = dict(kv)
        self.update_decision_outcome = Mock()
        self.log_control_event = Mock()
        self.log_equity = Mock()

    def kv_get(self, k, d=None): return self.kv.get(k, d)
    def kv_set(self, k, v): self.kv[k] = v
    def query(self, *a): return [{"n": 0}]
    def open_trades(self): return [{"symbol": "S0/USDT"}]


@pytest.mark.parametrize("macro_active", [False, True])
def test_kernel_cycle_in_recovery_blocks_entries_but_manages_exits(macro_active):
    """Real ControlStateMachine; the orchestrator mock ignores entry_allowed
    and always proposes an unskipped BUY, so only the kernel gate can stop it."""
    from trader.kernel import Kernel
    j = _KV(control_state="RECOVERY", macro_guard_froze="1")
    k = object.__new__(Kernel)
    k.cfg = {"timeframes": {"execution": "15m"}}
    k.population = []
    k.journal = j
    k.state_machine = ControlStateMachine(j)
    k._fetch_balance = lambda: 1000
    k.risk = NS(update_equity=lambda _: {"equity": 1000, "drawdown_pct": 0},
                daily_loss_block=.05)
    k._drain_close_requests = lambda: 0
    k.macro_guard = NS(check=lambda: {"active": macro_active, "event": "cpi"})
    k.news_guard = NS(check=lambda: {"active": False})
    k.market_type = MarketType.FUTURES
    k.executor = NS(recovery_pending=lambda: False, recover_entries=Mock())
    k.supervisor = NS(cycle=Mock())
    k._funding_map = k._oi_map = lambda: {}
    k._refresh_btc_context = lambda: None
    k._scan_symbols = lambda: ["S0/USDT"]
    k._universe_frames = lambda _: {}
    k._snapshot_for = lambda s, **kw: NS(symbol=s, price=100)
    k.positioning_agent = k.depth_agent = NS(set_context=lambda *a: None)
    k._order_book = lambda _: {}
    d = Decision("d", "c", "S0/USDT", Action.BUY, .7, .2, .8, [], [])
    decide = Mock(return_value=d)
    k.orchestrator = NS(decide=decide, journalize=Mock())
    k._try_enter = Mock(return_value=True)
    k._detect_exchange_exits = Mock(return_value=0)
    k._manage_one = Mock(return_value=None)
    k._manage_orphan_positions = Mock(return_value=0)
    k._maybe_resolve_outcomes = Mock()
    k._record_excursions = Mock()
    k._attention_call = lambda *a, **kw: None
    k.heartbeat = NS(beat=Mock())
    k.notifier = NS(send=Mock())

    stats = k.cycle()

    assert stats["entries"] == 0
    k._try_enter.assert_not_called()
    assert decide.call_args.kwargs["entry_allowed"] is False
    assert decide.call_args.kwargs["blocked_reason"] == "state=RECOVERY"
    k._manage_one.assert_called_once()                 # exits continue
    k._detect_exchange_exits.assert_called_once_with("S0/USDT")
    k.executor.recover_entries.assert_called_once()    # reconciliation continues
    k.supervisor.cycle.assert_called_once()             # bounded Supervisor progress
    # MacroGuard neither freezes nor auto-resumes out of RECOVERY.
    assert j.kv["control_state"] == "RECOVERY"
    assert k.heartbeat.beat.call_args.args[0]["state"] == "RECOVERY"
