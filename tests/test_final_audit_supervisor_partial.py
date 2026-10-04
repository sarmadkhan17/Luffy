"""Real Supervisor must prove all durable execution intents resolved."""
from pathlib import Path
import pytest
from trader.core.journal import Journal
from trader.core.types import ControlState,MarketType
from trader.core.config import load_config
from trader.engine.executor import Executor
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import Supervisor
from tests.test_supervisor import RISK_OK
from tests.test_final_audit_partial_intent import book,row  # noqa: F401


def protected(book):
    venue,j,e=book
    assert not e.close_partial(row(j),5)
    venue.unavailable=True
    original=venue.fetch_positions
    venue.fetch_positions=lambda symbols=None:original(symbols)
    venue.fapiPrivateGetOpenAlgoOrders=lambda:[{'algoId':'stop-1','symbol':'BTCUSDT','side':'SELL',
        'reduceOnly':True,'orderType':'STOP_MARKET','quantity':'5','triggerPrice':'95'}]
    venue.fetch_open_orders=lambda sym:[]
    with j._tx() as db:db.execute("UPDATE trades SET sl_order_id='stop-1' WHERE id='trade'")
    return venue,j,e


def supervisor(venue,j,e):
    state=ControlStateMachine(j)
    return state,Supervisor(j,state,e,venue,interval_s=0,risk_release=RISK_OK)


def test_unknown_partial_blocks_supervisor_activation_then_exact_recovery_after_restart(book):
    venue,j,e=protected(book);state,s=supervisor(venue,j,e)
    result=s.pass_once(boot=True)
    assert not result.safe_to_activate and state.refresh()!=ControlState.ACTIVE
    assert result.checks['execution_intents_resolved'] is False
    assert 'partial_exit_recovery_pending' in result.reasons
    assert e.recovery_pending() and len(venue.calls)==1
    reopened=Journal(j.db_path)
    e=Executor(venue,reopened,load_config(),MarketType.FUTURES);e.fill_retry_s=0
    state,s=supervisor(venue,reopened,e)
    assert not s.pass_once().safe_to_activate
    venue.unavailable=False
    result=s.pass_once()
    assert result.safe_to_activate and state.refresh()==ControlState.ACTIVE
    assert result.checks['execution_intents_resolved'] is True
    assert not e.recovery_pending() and row(j)['tp1_done']==1
    assert len(venue.calls)==1 and venue.quantity==5


def test_partial_ledger_read_failure_never_qualifies_as_safe_recovery(book,monkeypatch):
    venue,j,e=protected(book);state,s=supervisor(venue,j,e)
    from trader.engine import partial_intent
    monkeypatch.setattr(partial_intent,'pending',lambda journal:(_ for _ in ()).throw(OSError('unreadable ledger')))
    result=s.pass_once(boot=True)
    assert not result.safe_to_activate and state.refresh()!=ControlState.ACTIVE
    assert 'critical_recovery_state_unreadable' in result.reasons
    assert len(venue.calls)==1


def test_partial_intent_appearing_during_risk_release_is_refused_at_activation_cas(book):
    venue,j,e=book
    original=venue.fetch_positions
    venue.fetch_positions=lambda symbols=None:original(symbols)
    venue.fapiPrivateGetOpenAlgoOrders=lambda:[{'algoId':'stop-1','symbol':'BTCUSDT','side':'SELL',
        'reduceOnly':True,'orderType':'STOP_MARKET','quantity':str(venue.quantity),'triggerPrice':'95'}]
    venue.fetch_open_orders=lambda sym:[]
    with j._tx() as db:db.execute("UPDATE trades SET sl_order_id='stop-1' WHERE id='trade'")
    state,s=supervisor(venue,j,e)
    def risk_release():
        assert not e.close_partial(row(j),5)
        venue.unavailable=True
        return RISK_OK()
    s.risk_release=risk_release
    result=s.pass_once(boot=True)
    assert not result.safe_to_activate and state.refresh()!=ControlState.ACTIVE
    assert result.checks['execution_intents_resolved'] is False
    assert 'execution_recovery_pending' in result.reasons
    assert e.recovery_pending() and len(venue.calls)==1
