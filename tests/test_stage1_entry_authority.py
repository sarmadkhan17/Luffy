"""Offline exact-authority and durable entry boundary acceptance proofs."""
import json
from dataclasses import replace
import pytest
from ccxt import InsufficientFunds
from tests.test_entry_recovery import setup
from tests.entry_authority_fixtures import permission, bind
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import ControlState, MarketType
from trader.engine.executor import Executor
from trader.engine.state import ControlStateMachine
from trader.engine import entry_authority as A


def call(e,d,**changes):
    args=dict(amount=2.,atr=2.,stop_loss=95.,take_profit=110.,strategy_id='strategy',strategy_name='strategy')
    args.update(permission(e,d));args.update(changes)
    return e.open(d,**args)


def test_active_needs_exact_risk(setup):
    ex,j,e,d=setup
    assert e.open(d,2,2,95,110,'strategy','strategy') is None
    assert d.skip_reason == 'exact_risk_permission_required'
    assert not ex.sent


def test_raw_symbol_is_not_capability(setup):
    ex,j,e,d=setup
    j.kv_set(A.CAP_KEY+d.symbol,'null')
    with pytest.raises(ValueError,match='canonical_capability'):
        permission(e,d)
    assert not ex.sent


@pytest.mark.parametrize('field,value', [('amount',float('nan')),('amount',float('inf')),('amount',-1),
    ('amount',0),('amount','1e-9999'),('stop_loss',float('nan')),('atr',float('inf'))])
def test_invalid_numeric_never_submits(setup,field,value):
    ex,j,e,d=setup
    assert call(e,d,**{field:value}) is None
    assert not ex.sent


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,0])
def test_invalid_equity_cannot_authorize(setup,value):
    ex,j,e,d=setup
    rec=json.loads(j.kv_get('account_observation'));rec['value']=value
    j.kv_set('account_observation',json.dumps(rec))
    with pytest.raises(ValueError):
        permission(e,d)
    assert not ex.sent


def test_size_bound(setup):
    ex,j,e,d=setup
    kw=permission(e,d,amount=1)
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert d.skip_reason=='risk_size_exceeded' and not ex.sent


def test_changed_capability_receipt_refused(setup):
    ex,j,e,d=setup
    kw=permission(e,d)
    cap=json.loads(j.kv_get(A.CAP_KEY+d.symbol));cap['valid_until_ms']+=1
    cap['receipt_id']=A.digest({k:v for k,v in cap.items() if k!='receipt_id'})
    j.kv_set(A.CAP_KEY+d.symbol,A.canonical(cap))
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert d.skip_reason=='proposal_capability_changed' and not ex.sent


def test_identity_mismatch_refused(setup):
    ex,j,e,d=setup
    kw=permission(e,d)
    ex.market=lambda _:dict(id='ETHUSDT',base='ETH',quote='USDT',settle='USDT',linear=True,contract=True,contractSize=1)
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert d.skip_reason=='venue_capability_record_changed' and not ex.sent


def test_policy_changed_refused(setup):
    ex,j,e,d=setup
    kw=permission(e,d);e.risk_manager.risk_pct /= 2
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert d.skip_reason=='risk_policy_changed' and not ex.sent


def test_unissued_permission_refused(setup):
    ex,j,e,d=setup
    kw=permission(e,d);real=kw['risk_permission']
    kw['risk_permission']=replace(real)
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert d.skip_reason=='risk_permission_not_issued' and not ex.sent


def test_unknown_leverage_refused(setup):
    ex,j,e,d=setup
    cap=json.loads(j.kv_get(A.CAP_KEY+d.symbol));cap['leverage']['status']='UNKNOWN'
    cap['receipt_id']=A.digest({k:v for k,v in cap.items() if k!='receipt_id'})
    j.kv_set(A.CAP_KEY+d.symbol,A.canonical(cap))
    d.instrument_binding_json=A.proposal_binding(j,d.symbol)
    with pytest.raises(ValueError,match='leverage_unverified'):
        permission(e,d)
    assert not ex.sent


@pytest.mark.parametrize('change,reason', [('scalar', 'leverage_unverified'),
                                         ('projection', 'leverage_receipt_mismatch')])
def test_leverage_requires_replayable_venue_records(setup, change, reason):
    ex,j,e,d=setup
    cap=json.loads(j.kv_get(A.CAP_KEY+d.symbol))
    if change == 'scalar':
        cap['leverage']={'status':'VERIFIED','actual':'5','maximum':'5'}
    else:
        cap['leverage']['actual']='6'
        cap['leverage']['receipt_id']=A.digest({k:v for k,v in cap['leverage'].items() if k!='receipt_id'})
    cap['receipt_id']=A.digest({k:v for k,v in cap.items() if k!='receipt_id'})
    j.kv_set(A.CAP_KEY+d.symbol,A.canonical(cap))
    d.instrument_binding_json=A.proposal_binding(j,d.symbol)
    with pytest.raises(ValueError,match=reason):
        permission(e,d)
    assert not ex.sent


@pytest.mark.parametrize('state',[ControlState.FROZEN,ControlState.HALTED,ControlState.RECOVERY])
def test_containment_fences_queued_permission(setup,state):
    ex,j,e,d=setup
    kw=permission(e,d)
    j.kv_set('control_state',state.value)
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert not ex.sent


def check_late_freeze(setup,monkeypatch):
    ex,j,e,d=setup
    kw=permission(e,d)
    def quantize(ex,sym,amount):
        ControlStateMachine(j).set(ControlState.FROZEN,'operator','late freeze')
        return amount
    monkeypatch.setattr('trader.engine.executor.quantize',quantize)
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert not ex.sent, 'late containment was bypassed'


def test_late_freeze(setup,monkeypatch):
    check_late_freeze(setup,monkeypatch)


def test_completed_action_survives_store_reopen(setup):
    ex,j,e,d=setup
    ex.entry_error=InsufficientFunds('definite rejection')
    assert call(e,d) is None
    assert j.query('SELECT state FROM execution_requests')[0]['state']=='REFUSED'
    reopened=Journal(j.db_path)
    risk,_=bind(reopened,ex,load_config())
    d.instrument_binding_json=A.proposal_binding(reopened,d.symbol)
    restarted=Executor(ex,reopened,load_config(),MarketType.FUTURES,risk_manager=risk)
    assert call(restarted,d) is None
    assert len(ex.sent)==1, 'completed action submitted again'
    assert d.skip_reason=='logical_action_already_reserved'


def test_accounting_preserves_exact_identity(setup):
    ex,j,e,d=setup
    pos=call(e,d)
    assert pos is not None
    identity=json.loads(j.open_trades()[0]['entry_identity_json'])
    b=identity['execution_binding']
    assert b['instrument_id']=='binance_usdm:futures:BTCUSDT'
    assert b['logical_id']==d.id
    recorded=json.loads(j.query('SELECT intent_json FROM execution_requests')[0]['intent_json'])
    assert b['capability']==recorded['capability']


def mutant(monkeypatch, replacements):
    import inspect
    code=inspect.getsource(A.submit)
    for old,new in replacements:
        assert old in code
        code=code.replace(old,new)
    scope=dict(A.__dict__)
    exec(compile(code,'<negative-control>','exec'),scope)
    monkeypatch.setattr(A,'submit',scope['submit'])


def test_control_mutant_detected(setup,monkeypatch):
    mutant(monkeypatch,[("if blocked:\n            raise ValueError(blocked)","if False:\n            raise ValueError(blocked)"),
        ("if executor.journal.kv_get('control_state') != ControlState.ACTIVE.value:","if False:")])
    import inspect
    source=inspect.getsource(A.validate).replace(
        "if latest_intent_event_id(executor.journal) != raw['control_intent_id']:","if False:")
    namespace=dict(A.__dict__)
    exec(compile(source,'<control-watermark-mutant>','exec'),namespace)
    monkeypatch.setattr(A,'validate',namespace['validate'])
    A.submit.__globals__['validate']=namespace['validate']
    with pytest.raises(AssertionError):
        check_late_freeze(setup,monkeypatch)


def test_risk_mutant_detected(setup,monkeypatch):
    real=A.validate
    monkeypatch.setattr(A,'validate',lambda e,p,*a:p.payload())
    with pytest.raises(AssertionError):
        test_unissued_permission_refused(setup)


def test_identity_mutant_detected(setup,monkeypatch):
    monkeypatch.setattr(A,'validate',lambda e,p,*a:p.payload())
    with pytest.raises(AssertionError):
        test_identity_mismatch_refused(setup)


def test_dedup_mutant_detected(setup,monkeypatch):
    # Removing history retention allows the rejected completed request to recur.
    mutant(monkeypatch,[("with manager._durable_hold() as conn:\n            if conn is None:",
                        "with manager._durable_hold() as conn:\n            conn.execute('DELETE FROM execution_requests')\n            conn.execute(\"DELETE FROM control_events WHERE event='execution_recovery'\")\n            if conn is None:")])
    with pytest.raises(AssertionError,match='completed action submitted again'):
        test_completed_action_survives_store_reopen(setup)


def test_missing_control_is_not_active(setup):
    ex,j,e,d=setup
    with j._tx() as db:
        db.execute("DELETE FROM state_kv WHERE key='control_state'")
    with pytest.raises(ValueError,match='control_entry_blocked'):
        permission(e,d)
    assert not ex.sent


def test_direction_mismatch_refused(setup):
    from trader.core.types import Action
    ex,j,e,d=setup
    kw=permission(e,d)
    other=replace(d,action=Action.SELL)
    assert e.open(other,2,2,105,90,'strategy','strategy',**kw) is None
    assert not ex.sent


def test_completed_fill_retry_and_reopen_never_recreates_exposure(setup):
    ex,j,e,d=setup
    kw=permission(e,d)
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is not None
    sent=len(ex.sent)
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    restarted=Executor(ex,Journal(j.db_path),load_config(),MarketType.FUTURES,risk_manager=e.risk_manager)
    assert restarted.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert len(ex.sent)==sent
    assert j.query('SELECT state FROM execution_requests')[0]['state']=='TERMINAL'


def test_reconciliation_refuses_changed_canonical_binding(setup):
    from trader.engine.reconcile import reconcile_futures
    ex,j,e,d=setup
    assert call(e,d) is not None
    ex.market=lambda _:dict(id='ETHUSDT')
    before=len(ex.sent)
    result=reconcile_futures(ex,j,verify=True)
    assert result['safety_issues']==['canonical_binding_mismatch']
    assert len(ex.sent)==before


def test_recovery_refuses_conflicting_venue_position_identity(setup):
    from ccxt import RequestTimeout
    ex,j,e,d=setup
    ex.entry_error=RequestTimeout('accepted, response lost')
    assert call(e,d) is None
    ex.positions[0]['info']={'symbol':'ETHUSDT'}
    before=len(ex.sent)
    e.recover_entries()
    assert e.recovery.pending()['error_type']=='ValueError'
    assert not j.open_trades() and len(ex.sent)==before


def test_reconciliation_refuses_symbol_alias_collision(setup):
    from trader.engine.reconcile import reconcile_futures
    ex,j,e,d=setup
    assert call(e,d) is not None
    old_market=ex.market
    ex.positions[0]['symbol']='BTC/USDT:USDT'
    ex.market=lambda symbol: dict(id='ETHUSDT') if symbol.endswith(':USDT') else old_market(symbol)
    before=len(ex.sent)
    result=reconcile_futures(ex,j,verify=True)
    assert result['safety_issues']==['canonical_position_identity_unverified']
    assert len(ex.sent)==before


def test_all_live_order_callers_have_classified_boundaries():
    import ast
    from pathlib import Path
    expected={'trader/engine/entry_authority.py','trader/engine/executor.py',
              'trader/engine/recovery.py','trader/engine/protective.py',
              'trader/engine/reconcile.py','trader/observability/prospective_execution.py'}
    found=set()
    for path in Path('trader').rglob('*.py'):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or getattr(node.func,'attr',None)!='create_order':continue
            found.add(path.as_posix())
            if path.as_posix().startswith('trader/engine/') and path.name!='entry_authority.py':
                params=next((k.value for k in node.keywords if k.arg=='params'),None)
                assert isinstance(params,ast.Dict),str(path)
                values={getattr(k,'value',None):getattr(v,'value',None) for k,v in zip(params.keys,params.values)}
                assert values.get('reduceOnly') is True,str(path)
    assert found==expected


def test_bracket_must_cover_this_exact_notional(setup):
    ex,j,e,d=setup
    cap=json.loads(j.kv_get(A.CAP_KEY+d.symbol))
    old=cap['leverage']
    raw_brackets={'symbol':'BTCUSDT','brackets':[dict(notionalFloor='0',notionalCap='150',initialLeverage=5),
                                               dict(notionalFloor='150',notionalCap='100000',initialLeverage=1)]}
    cap['leverage']=A.observe_leverage(instrument_id=old['instrument_id'],account_scope=old['account_scope'],
        snapshot_id=old['snapshot_id'],symbol_config=old['symbol_config'],leverage_brackets=raw_brackets,
        observed_at_ms=old['observed_at_ms'],valid_until_ms=old['valid_until_ms'],source=old['source'])
    cap['receipt_id']=A.digest({k:v for k,v in cap.items() if k!='receipt_id'})
    j.kv_set(A.CAP_KEY+d.symbol,A.canonical(cap))
    d.instrument_binding_json=A.proposal_binding(j,d.symbol)
    with pytest.raises(ValueError,match='leverage_bracket_not_permitted'):
        permission(e,d)
    assert not ex.sent


def test_partial_retry_never_recreates_original_amount(setup):
    from ccxt import RequestTimeout
    from tests.test_entry_recovery import enter
    ex,j,e,d=setup
    ex.entry_error=RequestTimeout('accepted partial')
    enter(e,d)
    ex.orders['entry'].update(status='open',filled=.5)
    ex.positions[0]['contracts']=.5
    before=len(ex.sent)
    assert enter(e,d) is None
    assert len(ex.sent)==before
    e.recover_entries();e.recover_entries()
    assert j.open_trades()[0]['amount']==.5
    sent=len(ex.sent)
    assert enter(e,d) is None
    assert len(ex.sent)==sent


def test_trade_intent_capability_is_immutable_and_exact(setup):
    from trader.portfolio.trade_intent import TradeIntent,bind_capability
    from trader.portfolio.allocator import canonical,digest
    ex,j,e,d=setup
    raw=dict(schema='portfolio-trade-intent.v1',allocation_proposal_id='proposal',opportunity_id='opportunity',
        strategy_id='strategy',version_id='version',spec_hash='spec',portfolio_snapshot_id='portfolio',
        portfolio_sha256='portfolio-hash',instrument='binance_usdm:futures:BTCUSDT',direction='LONG',market_type='futures',
        as_of_ms=1,risk_final_authority=True,execution_routed=False,risk_approval='NOT_REQUESTED',side_effects='NONE',
        provenance={'source_ids':[]},requested_action='OPEN',status='COMPLETE',requested_size='2',size_unit='base_quantity',context_id='context')
    unbound=TradeIntent(digest(raw),canonical(raw))
    cap=json.loads(j.kv_get(A.CAP_KEY+d.symbol))
    bound=bind_capability(unbound,cap)
    assert bound.payload()['capability_receipt_id']==cap['receipt_id']
    assert 'capability_receipt_id' not in unbound.payload()
    with pytest.raises(ValueError,match='trade_intent_binding_mismatch'):
        e.risk_manager.authorize_entry(d,2,2,95,110,'strategy',reference={'price':100},trade_intent=unbound)
    altered=dict(cap,instrument_id='binance_usdm:futures:ETHUSDT')
    with pytest.raises(ValueError,match='TRADE_INTENT_CAPABILITY_MISMATCH'):
        bind_capability(unbound,altered)


@pytest.mark.parametrize('field',['amount','entry_price','notional_usdt','stop_loss','leverage'])
def test_risk_rejects_invalid_held_position_numbers(setup,field):
    from trader.core.types import Position,Side
    ex,j,e,d=setup
    p=Position('held','ETH/USDT',Side.LONG,1,100,100,5,95,110)
    setattr(p,field,float('nan'))
    result=e.risk_manager.check_entry(ControlState.ACTIVE,d.symbol,100.,2.,.05,[p],10000.,0,'futures')
    assert not result.ok and result.reason=='risk_position_inputs_invalid'
    assert not ex.sent


def test_stale_account_after_authorization_refuses(setup):
    from datetime import datetime,timezone,timedelta
    ex,j,e,d=setup
    kw=permission(e,d)
    rec=json.loads(j.kv_get('account_observation'))
    ts=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    rec.update(observed_at=ts,successful_read_at=ts,attempted_at=ts,recorded_at=ts)
    j.kv_set('account_observation',json.dumps(rec))
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert not ex.sent


def test_raw_symbol_with_existing_registry_still_cannot_authorize(setup):
    ex,j,e,d=setup
    d.instrument_binding_json=None
    with pytest.raises(ValueError,match='proposal_canonical_capability_required'):
        permission(e,d)
    assert not ex.sent


def test_registry_changed_after_proposal_before_risk_refuses(setup):
    ex,j,e,d=setup
    cap=json.loads(j.kv_get(A.CAP_KEY+d.symbol));cap['valid_until_ms']+=1
    cap['receipt_id']=A.digest({k:v for k,v in cap.items() if k!='receipt_id'})
    j.kv_set(A.CAP_KEY+d.symbol,A.canonical(cap))
    with pytest.raises(ValueError,match='proposal_capability_changed'):
        permission(e,d)
    assert not ex.sent


def test_portfolio_proposal_carries_exact_registry_source(setup,monkeypatch):
    from tests.economics_fixtures import install_models
    from tests.test_portfolio_allocator import candidate,inputs
    from trader.portfolio.allocator import Source,allocate
    from trader.portfolio.trade_intent import build,bind_capability
    install_models(monkeypatch)
    ex,j,e,d=setup
    cap=json.loads(d.instrument_binding_json)
    c=candidate(instrument=cap['instrument_id'])
    frozen=inputs(c)
    source=Source.freeze('entry-capability:'+cap['instrument_id'],cap)
    frozen=replace(frozen,sources=(*frozen.sources,source))
    proposal=allocate(frozen)
    [typed]=build(proposal,frozen)
    assert typed.payload()['capability']==cap
    assert typed.payload()['capability_receipt_id']==cap['receipt_id']
    changed=dict(cap,valid_until_ms=cap['valid_until_ms']+1)
    changed['receipt_id']=A.digest({k:v for k,v in changed.items() if k!='receipt_id'})
    with pytest.raises(ValueError,match='TRADE_INTENT_CAPABILITY_REBIND_REFUSED'):
        bind_capability(typed,changed)
    assert not ex.sent


def test_account_context_changed_before_execution_refuses(setup):
    ex,j,e,d=setup
    kw=permission(e,d)
    ex.apiKey='TEST-ONLY-different-account'
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert d.skip_reason=='venue_account_binding_unverified'
    assert not ex.sent


def test_venue_book_receipt_for_another_account_refuses(setup):
    ex,j,e,d=setup
    snapshot=json.loads(j.kv_get('venue_position_snapshot'))
    snapshot['account_scope']='other-account'
    snapshot['snapshot_id']=A.digest({k:v for k,v in snapshot.items() if k!='snapshot_id'})
    j.kv_set('venue_position_snapshot',A.canonical(snapshot))
    with pytest.raises(ValueError,match='risk_venue_account_scope_unverified'):
        permission(e,d)
    assert not ex.sent


def test_completed_request_is_retained_in_fresh_interpreter(setup):
    import subprocess,sys
    from pathlib import Path
    ex,j,e,d=setup
    ex.entry_error=InsufficientFunds('definite rejection')
    assert call(e,d) is None
    script='''
import json,sys,socket
from ccxt import InsufficientFunds
from tests.test_entry_recovery import Venue
from tests.entry_authority_fixtures import bind,permission
from trader.core.journal import Journal
from trader.core.config import load_config
from trader.core.types import Decision,Action,MarketType
from trader.engine.executor import Executor
from trader.engine.entry_authority import proposal_binding
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden'))
j=Journal(sys.argv[1]);ex=Venue();ex.entry_error=InsufficientFunds('definite rejection')
r,_=bind(j,ex,load_config());e=Executor(ex,j,load_config(),MarketType.FUTURES,risk_manager=r)
d=Decision('d','c','BTC/USDT',Action.BUY,1.,.5,.8,[],[])
d.instrument_binding_json=proposal_binding(j,d.symbol)
result=e.open(d,2,2,95,110,'strategy','strategy',**permission(e,d))
print(json.dumps(dict(submissions=len(ex.sent),reason=d.skip_reason,result=result)))
'''
    result=subprocess.run([sys.executable,'-c',script,str(j.db_path)],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    outcome=json.loads(result.stdout)
    assert outcome==dict(submissions=0,reason='logical_action_already_reserved',result=None)


def test_db_lock_failure_at_reservation_prevents_submission(setup,monkeypatch):
    from contextlib import contextmanager
    import sqlite3
    ex,j,e,d=setup
    kw=permission(e,d)
    @contextmanager
    def failed():
        raise sqlite3.OperationalError('injected database lock failure')
        yield
    monkeypatch.setattr(e.risk_manager,'_durable_hold',failed)
    with pytest.raises(sqlite3.OperationalError):
        e.open(d,2,2,95,110,'strategy','strategy',**kw)
    assert not ex.sent


def test_ack_write_failure_preserves_committed_unknown_attempt(setup,monkeypatch):
    import sqlite3
    ex,j,e,d=setup
    kw=permission(e,d)
    real=e.recovery.save
    def failed(intent,reason,**options):
        if reason=='entry_submitted':
            raise sqlite3.OperationalError('injected acknowledgement journal failure')
        return real(intent,reason,**options)
    monkeypatch.setattr(e.recovery,'save',failed)
    with pytest.raises(sqlite3.OperationalError):
        e.open(d,2,2,95,110,'strategy','strategy',**kw)
    assert len(ex.sent)==1
    assert j.query('SELECT state FROM execution_requests')[0]['state']=='SUBMISSION_ATTEMPTED'
    reopened=Executor(ex,Journal(j.db_path),load_config(),MarketType.FUTURES)
    assert reopened.recovery_pending()
    assert reopened.open(d,2,2,95,110,'strategy','strategy') is None
    assert len(ex.sent)==1
    reopened.recover_entries();reopened.recover_entries()
    assert j.open_trades()[0]['amount']==2
    assert not reopened.recovery_pending()


def test_account_producer_uses_exact_execution_credential_context(setup,monkeypatch):
    from types import SimpleNamespace
    from trader.kernel import Kernel
    from trader.core.config import Env
    ex,j,e,d=setup
    k=object.__new__(Kernel);k.exchange=ex
    monkeypatch.setattr(Env,'binance_keys',staticmethod(lambda:('TEST-ONLY-key','TEST-ONLY-secret')))
    calls=[]
    body={'totalMarginBalance':'10000','availableBalance':'10000'}
    def get(url,**kw):
        calls.append(url)
        return SimpleNamespace(content=json.dumps(body).encode(),json=lambda:body)
    monkeypatch.setattr('requests.get',get)
    assert k._fetch_balance_fresh()==10000
    assert calls==['https://demo-fapi.binance.com/fapi/v3/account']
    assert k._account_response['account_scope']==ex.entry_account_scope
    assert k._balance_read['account_scope']==ex.entry_account_scope
    calls.clear()
    monkeypatch.setattr(Env,'binance_keys',staticmethod(lambda:('TEST-ONLY-other-key','TEST-ONLY-secret')))
    assert k._fetch_balance_fresh() is None
    assert calls==[]


def test_newer_control_intent_invalidates_old_permission_even_after_active(setup):
    ex,j,e,d=setup
    kw=permission(e,d)
    machine=ControlStateMachine(j)
    machine.set(ControlState.FROZEN,'operator','newer hold')
    machine.set(ControlState.ACTIVE,'operator','test-only resumed state')
    assert e.open(d,2,2,95,110,'strategy','strategy',**kw) is None
    assert d.skip_reason=='control_intent_superseded'
    assert not ex.sent


def test_pre_upgrade_durable_history_also_blocks_completed_action(setup):
    ex,j,e,d=setup
    j.log_control_event('execution_recovery','executor',detail={'decision_id':d.id,'reason':'entry_submission_rejected'})
    assert call(e,d) is None
    assert d.skip_reason=='logical_action_already_reserved'
    assert not ex.sent
