"""Pending signal decisions cannot suspend independently required protection.

Frozen synthetic qualification and candle receipt fixtures; no venue calls,
order submissions or profitable strategy claims.
"""
import json
import sqlite3
from unittest.mock import Mock
from types import SimpleNamespace as NS
import pytest
from trader.engine.versioned_exits import LiveExitAdapter
from trader.engine import live_signal_evidence as S
from trader.strategy import exit_policy as E,compile as compile_module
from trader.strategy.spec import ExitSpec
from trader.core.config import load_config
from trader.core.types import Decision
from trader.engine.paper import PaperExecutor
from tests.test_sol1_paper_cost import installed,bars,snapshot,H4,no_public_network  # noqa: F401
from tests.test_sol1_strategy_lifecycle import _spec
from tests.authority_factory_fixtures import T0

SIGNAL=(110.,110.2,108.5,108.8)


def position(tmp_path,*,trail=False,max_bars=3):
    ex=ExitSpec(stop={'kind':'pct','v':.04},target={'kind':'none'},time={'max_bars':max_bars},
        signal_exit='close < 109',trail={'kind':'atr','mult':1.,'arm_at_r':1.} if trail else {'kind':'none'})
    spec=_spec(direction='long',entry_long='close > 0',entry_short='',exit=ex)
    cfg=load_config();j,version,_=installed(tmp_path,cfg,spec)
    start=T0//H4*H4
    snap=snapshot(bars(60,start))
    signal=compile_module.compile_version(j,version['version_id']).to_evaluator()(None,snap)
    decision=Decision('signal','signal',snap.symbol,signal.action,1.,.5,.8,[],[])
    decision.strategy_signals=[dict(strategy_id=spec.id,action=signal.action.value,params=signal.params)]
    paper=PaperExecutor(j,cfg);paper.capture=None
    tid=paper.enter(decision,snap,spec.id,reference_equity=10000,state='ACTIVE')
    trade=j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    executor=NS(close=Mock(return_value=False));adapter=LiveExitAdapter(j,executor)
    first=snapshot(bars(60,start,[SIGNAL]))
    return j,trade,executor,adapter,first,start


def wait_signal(adapter,trade,snap):
    with pytest.raises(ValueError,match='live_signal_exit_execution_pending_D6'):
        adapter.manage(trade,snap)


def pending(j,trade):
    with j._tx() as db:return S.load(db,trade['id'],json.loads(trade['entry_identity_json']))


def test_first_signal_receipts_are_immutable_and_retries_do_not_duplicate(tmp_path):
    j,trade,executor,adapter,first,start=position(tmp_path)
    wait_signal(adapter,trade,first)
    original=pending(j,trade)
    assert original['exec_mode']=='live' and original['signal_decision']['inputs']
    assert original['signal_decision']['inputs'][-1]['ts']==original['signal_decision']['bar_ms']
    assert original['financial_result'] is None and original['fill_claim'] is False
    assert not original['signal_execution_ready']
    from trader.engine import exit_signal_context as K
    assert 'context' in original,original.get('context_error')
    assert K.replay(original['context'],original['signal_decision']['bar_ms'])=={'signal':True,'available':True}
    wait_signal(LiveExitAdapter(j,executor),trade,first)
    assert pending(j,trade)==original and len(j.query('SELECT * FROM '+S.TABLE))==1
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with j._tx() as db:db.execute('UPDATE '+S.TABLE+' SET sha256=?',('wrong',))
    executor.close.assert_not_called()


@pytest.mark.parametrize('reason',['stop','time'])
def test_pending_signal_preserves_main_stop_and_maximum_holding_time(tmp_path,monkeypatch,reason):
    j,trade,executor,adapter,first,start=position(tmp_path)
    wait_signal(adapter,trade,first);original=pending(j,trade)
    # After a proven decision, unavailable signal features cannot block the
    # independent price/time policy or rewrite the original input receipts.
    monkeypatch.setattr(compile_module,'compile_spec',Mock(side_effect=AssertionError('do not re-evaluate pending signal')))
    tails=[SIGNAL,(108.8,109.,104.,104.)] if reason=='stop' else [SIGNAL,(108.8,109.,108.,108.8),(108.8,109.,108.,108.8)]
    assert LiveExitAdapter(j,executor).manage(trade,snapshot(bars(60,start,tails))) is None
    assert executor.close.call_args.kwargs['reason']==reason
    assert pending(j,trade)==original
    saved=E.decode(j.query('SELECT state_json FROM versioned_live_exit_states')[0]['state_json'])[1]
    assert saved.reason==reason


def test_pending_signal_keeps_trailing_stop_advancing(tmp_path):
    j,trade,executor,adapter,first,start=position(tmp_path,trail=True,max_bars=10)
    wait_signal(adapter,trade,first)
    before=E.decode(j.query('SELECT state_json FROM versioned_live_exit_states')[0]['state_json'])[1]
    next_snap=snapshot(bars(60,start,[SIGNAL,(108.8,116.,108.8,115.)]))
    wait_signal(adapter,trade,next_snap)
    after=E.decode(j.query('SELECT state_json FROM versioned_live_exit_states')[0]['state_json'])[1]
    assert after.active_stop>before.active_stop and after.trail=='armed' and after.reason is None
    executor.close.assert_not_called()


def test_old_terminal_signal_recovers_protection_without_inventing_old_receipts(tmp_path):
    j,trade,executor,adapter,first,start=position(tmp_path)
    p,s=E.decode(json.loads(trade['entry_identity_json'])['exit_state'])
    old=E.advance(p,s,E.Observation(s.last_bar_ms+p.step_ms,*SIGNAL,signal_exit=True),float(trade['amount']))
    assert old.reason=='signal'
    with j._tx() as db:
        root=json.loads(trade['entry_identity_json'])
        db.execute('INSERT INTO versioned_live_exit_states VALUES(?,?,?,?,?,?)',
            (trade['id'],root['version_id'],root['install_id'],root['spec_sha256'],E.EXIT_SEMANTICS_ID,E.encode(p,old.state)))
    adapter.manage(trade,snapshot(bars(60,start,[SIGNAL,(108.8,109.,104.,104.)])))
    assert executor.close.call_args.kwargs['reason']=='stop'
    receipt=pending(j,trade)['signal_decision']
    assert receipt['availability']=='UNAVAILABLE_LEGACY_DECISION_RECEIPTS' and not receipt['inputs']


def test_bad_signal_blob_is_reported_but_cannot_block_a_main_stop(tmp_path):
    j,trade,executor,adapter,first,start=position(tmp_path)
    wait_signal(adapter,trade,first)
    with j._tx() as db:
        db.execute('DROP TRIGGER versioned_live_signal_no_update')
        db.execute('UPDATE '+S.TABLE+' SET payload=?',(b'corrupt fixture',))
    with pytest.raises(ValueError,match='live_signal_evidence_unavailable'):
        adapter.manage(trade,first)
    executor.close.reset_mock()
    with pytest.raises(ValueError,match='live_signal_evidence_unavailable'):
        adapter.manage(trade,snapshot(bars(60,start,[SIGNAL,(108.8,109.,104.,104.)])))
    assert executor.close.call_args.kwargs['reason']=='stop'


@pytest.mark.parametrize('failure',['context','size'])
def test_source_capture_gap_is_explicit_and_does_not_suspend_time_exit(tmp_path,monkeypatch,failure):
    from trader.engine import exit_signal_context as K
    j,trade,executor,adapter,first,start=position(tmp_path)
    if failure=='context':monkeypatch.setattr(K,'capture',Mock(side_effect=ValueError('source unavailable')))
    else:monkeypatch.setattr(S,'MAX_BYTES',4096)
    wait_signal(adapter,trade,first)
    body=pending(j,trade)
    assert not body['signal_execution_ready'] and 'context' not in body
    if failure=='size':assert body['signal_decision']['availability']=='UNAVAILABLE_SOURCE_RECEIPTS_EXCEED_BOUND'
    else:assert body['context_error']=='ValueError'
    adapter.manage(trade,snapshot(bars(60,start,[SIGNAL,(108.8,109.,108.,108.8),(108.8,109.,108.,108.8)])))
    assert executor.close.call_args.kwargs['reason']=='time'


def test_legacy_signal_cannot_postpone_an_already_due_holding_limit(tmp_path):
    j,trade,executor,adapter,first,start=position(tmp_path,max_bars=1)
    root=json.loads(trade['entry_identity_json']);p,s=E.decode(root['exit_state'])
    old=E.advance(p,s,E.Observation(s.last_bar_ms+p.step_ms,*SIGNAL,signal_exit=True),float(trade['amount']))
    assert old.reason=='signal' and old.state.bars_held==p.max_bars
    with j._tx() as db:
        db.execute('INSERT INTO versioned_live_exit_states VALUES(?,?,?,?,?,?)',
            (trade['id'],root['version_id'],root['install_id'],root['spec_sha256'],E.EXIT_SEMANTICS_ID,E.encode(p,old.state)))
    adapter.manage(trade,first)
    assert executor.close.call_args.kwargs['reason']=='time'
    state=E.decode(j.query('SELECT state_json FROM versioned_live_exit_states')[0]['state_json'])[1]
    assert state.reason=='time' and state.reference_exit is None  # no invented historical fill price
    assert pending(j,trade)['signal_decision']['availability']=='UNAVAILABLE_LEGACY_DECISION_RECEIPTS'


@pytest.mark.parametrize('state',['CLOSING','CLOSED'])
def test_registered_actual_close_is_not_reclassified_as_an_old_unsubmitted_signal(tmp_path,state):
    # Adapter routing fixture only. Actual owned-plan registration and venue
    # recovery have separate Risk/Executor transport tests.
    from trader.engine import versioned_close as V,entry_authority as A
    j,trade,executor,adapter,first,start=position(tmp_path)
    root=json.loads(trade['entry_identity_json'])
    V.ensure(j)
    plan=dict(trade_id=trade['id'],entry_identity_sha256=A.digest(root))
    with j._tx() as db:
        db.execute('INSERT INTO '+V.TABLE+' VALUES(?,?,?,?)',(trade['id'],A.canonical(plan),A.digest(plan),state))
    assert adapter.manage(trade,first) is None
    executor.close.assert_not_called()
    assert not j.query('SELECT * FROM '+S.TABLE)
