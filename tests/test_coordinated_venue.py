"""Real isolated Risk/Executor transport under a synthetic qualification fixture.

Qualification/installation are mocked only to test dispatch. No admission,
statistical qualification or production runtime readiness is claimed.
"""
from dataclasses import asdict
from copy import deepcopy
import json
import time
from unittest.mock import Mock
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_strategy_hedge_stops import current
from trader.engine import coordinated_venue as V,coordinated_authority as CA
from trader.research.compose import hedge as H
from trader.strategy import factory_handoff as F
from trader.strategy.spec import ExitSpec


def prepared(setup,monkeypatch,*,ready=True,leverage=None,sizing_pct=100.,ratio=.5,management=None,exit_definition=None,entry_bar_ms=0,step_ms=1000):
    ex,j,e,_=setup;e.fill_retry_s=0
    if leverage is not None:
        from tests.entry_authority_fixtures import bind
        from trader.engine.executor import Executor
        cfg=deepcopy(e.cfg);cfg['risk']['leverage']=leverage
        risk,_cap=bind(j,ex,cfg)
        e=Executor(ex,j,cfg,e.market_type,risk_manager=risk);e.fill_retry_s=0
    from tests.coordinated_cashflow_fixture import attach
    attach(ex,monkeypatch)
    hs=H.make_spec(purpose='protective_beta',account_mode='netting',leverage=e.risk_manager.leverage,
        primary={'symbol':'BTC/USDT'},hedge={'symbol':'ETH/USDT'},ratio={'method':'fixed','value':ratio},
        sizing={'primary_notional_pct':sizing_pct},management=management or current())
    definition=exit_definition or asdict(ExitSpec(stop={'kind':'pct','v':.05},target={'kind':'rr','v':2.},time={'max_bars':100}))
    book=H.HedgeBook(hs,primary_exit_definition=definition)
    ins=book.step(0,dict(p=100.,h=50.,signal=1,exit=False,ratio=.5,corr=1.,equity=200.,
        prim_c=H.Contract(),hedge_c=H.Contract(),primary_exit_observation=dict(
            bar_ms=entry_bar_ms,step_ms=step_ms,open=100.,high=100.,low=100.,close=100.,atr=2.)))[0]
    version=dict(version_id='v',spec_hash='s',strategy_id='strategy',source={'kind':F.HEDGE_HQ_KIND},spec={'exit':definition},
                 lineage=dict(research=dict(multi_leg=dict(hedge_spec=hs))))
    monkeypatch.setattr(F,'load_version',lambda *a:version)
    monkeypatch.setattr(F,'verify_validation',Mock())
    monkeypatch.setattr(F,'verify_install',lambda *a,**kw:{'install_id':'i'})
    monkeypatch.setattr(CA,'RUNTIME_READY',ready)
    identity=dict(status='VERIFIED',strategy_id='strategy',version_id='v',spec_hash='s',spec_sha256='s',
        install_id='i',exit_semantics_id=H.BOOK_EXIT_SEMANTICS_ID,exit_state=book.primary_exit_state,
        coordinated_instruction_observed_ms=int(time.time()*1000),
        coordinated=dict(group_id='g',leg='primary',management_policy=hs['management'],
            allocated_equity_usdt=200.,maintenance_fraction=hs['maint_margin']))
    reference=dict(price=100.,observed_ms=int(time.time()*1000),basis='synthetic_current_quote',
        instrument_id=json.loads(_.instrument_binding_json)['instrument_id'],source='synthetic-public-quote')
    return ex,j,e,book,ins,identity,reference


def test_shared_intent_uses_real_protected_order_once_and_adopts_original_receipt(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert result['status']=='TERMINAL' and result['filled_quantity']==pytest.approx(2.)
    assert not e.recovery_pending() and ex.stops
    assert book.pos['primary']==0.  # only the durable caller may adopt the actual fill
    count=len(ex.sent)
    assert V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)==result
    assert len(ex.sent)==count
    bound=json.loads(j.open_trades()[0]['entry_identity_json'])
    assert bound['coordinated_action_id']==result['action_id']
    assert not CA.matches({'version_id':'v','strategy_id':'strategy','spec_hash':'s'})


def test_timeout_is_not_renamed_or_resubmitted(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    ex.entry_error=RequestTimeout('accepted response lost')
    first=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert first['filled_quantity'] is None and e.recovery_pending()
    count=len(ex.sent)
    assert V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)==first
    assert len(ex.sent)==count
    ex.entry_error=None
    e.recover_entries();e.recover_entries()
    terminal=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert terminal['status']=='TERMINAL' and terminal['filled_quantity']==pytest.approx(2.)
    assert len([r for r in ex.sent if r[1]=='market' and not r[4].get('reduceOnly')])==1


@pytest.mark.parametrize('change',['runtime','stale','old_instruction','pending','stop','version'])
def test_unready_or_changed_instruction_cannot_reach_venue(setup,monkeypatch,change):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch,ready=change!='runtime')
    if change=='stale':ref['observed_ms']-=30001
    elif change=='old_instruction':identity['coordinated_instruction_observed_ms']-=60001
    elif change=='pending':book.pending.clear()
    elif change=='stop':identity['exit_state']='invalid'
    elif change=='version':book.spec=deepcopy(book.spec);book.spec['fee_bps']+=1
    with pytest.raises((ValueError,TypeError)):
        V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert not ex.sent


def test_same_action_cannot_be_reused_for_a_different_instruction(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    count=len(ex.sent)
    ins.qty=1.;book.pending[ins.oid]=(ins.leg,ins.side,ins.qty,ins.bar)
    with pytest.raises(ValueError,match='action_receipt_differs'):
        V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert len(ex.sent)==count


def test_dispatch_reports_actual_partial_fill_without_advancing_shared_model(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    submit=ex.create_order
    def partial(*args,**kw):
        order=submit(*args,**kw)
        params=kw.get('params') or (args[4] if len(args)>4 else {})
        if not params.get('reduceOnly'):
            order['filled']=1.;ex.positions[0]['contracts']=1.
        return order
    monkeypatch.setattr(ex,'create_order',partial)
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert result['filled_quantity']==1. and j.open_trades()[0]['amount']==1.
    assert book.pos['primary']==0. and ins.qty==2.
    assert V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)==result


def test_existing_owned_delta_reduction_uses_existing_transport_without_live_scope(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    submit=ex.create_order
    def order(*args,**kw):
        result=submit(*args,**kw)
        params=kw.get('params') or (args[4] if len(args)>4 else {})
        if params.get('reduceOnly') and not params.get('stopLossPrice'):result['reduceOnly']=True
        return result
    monkeypatch.setattr(ex,'create_order',order)
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    book.apply_fills({ins.oid:result['filled_quantity']})
    emitted=[];book._emit(emitted,'REDUCE_PRIMARY','primary',-1,.5,'ratio_drift',1)
    monkeypatch.setattr(CA,'RUNTIME_READY',False)
    reduced=V.dispatch(e,book,emitted[0],identity=identity,reference=ref,atr=2.)
    assert reduced['status']=='BOOKED' and j.open_trades()[0]['amount']==1.5
    assert ex.stops  # no flatten/reentry and existing protection remains
    count=len(ex.sent)
    # The durable caller still has its old model quantity until this receipt
    # is adopted. A retry returns the same actual fill, never a second delta.
    assert V.dispatch(e,book,emitted[0],identity=identity,reference=ref,atr=2.)['filled_quantity']==.5
    assert len(ex.sent)==count


def test_owned_increase_keeps_one_trade_and_adopts_exact_delta_after_recovery(setup,monkeypatch):
    from tests.test_coordinated_increase import refreshed,submit_fixture
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    entry=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    book.apply_fills({ins.oid:entry['filled_quantity']})
    refreshed(ex,j);submit_fixture(ex,monkeypatch)
    root=j.open_trades()[0]['entry_identity_json'];tid=j.open_trades()[0]['id']
    identity=json.loads(root)
    emitted=[];book._emit(emitted,'REBALANCE','primary',1,.5,'ratio_drift',1)
    result=V.dispatch(e,book,emitted[0],identity=identity,reference=ref,atr=2.)
    assert result['filled_quantity'] is None and e.recovery_pending()
    count=len(ex.sent)
    assert V.dispatch(e,book,emitted[0],identity=identity,reference=ref,atr=2.)==result
    assert len(ex.sent)==count
    e.recover_entries()
    result=V.dispatch(e,book,emitted[0],identity=identity,reference=ref,atr=2.)
    assert result['status']=='TERMINAL' and result['filled_quantity']==.5
    rows=j.open_trades()
    assert len(rows)==1 and rows[0]['id']==tid and rows[0]['amount']==2.5
    assert rows[0]['entry_identity_json']==root and rows[0]['sl_order_id']=='full-stop'
    assert book.pos['primary']==2.  # durable model adoption is a separate step


def test_whole_exit_uses_group_lock_once_and_retries_after_journal_closes(setup,monkeypatch):
    import threading
    from tests.test_coordinated_unwind import normalized
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    entry=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    book.apply_fills({ins.oid:entry['filled_quantity']})
    submit=ex.create_order
    def order(*args,**kw):
        result=submit(*args,**kw)
        params=kw.get('params') or (args[4] if len(args)>4 else {})
        if params.get('reduceOnly') and not params.get('stopLossPrice'):
            result['reduceOnly']=True;ex.positions=[]
        return result
    monkeypatch.setattr(ex,'create_order',order);normalized(ex,monkeypatch)
    cancel=ex.cancel_order
    def cancelled(oid,symbol):
        if oid=='stop':ex.stops.clear();return {}
        return cancel(oid,symbol)
    monkeypatch.setattr(ex,'cancel_order',cancelled)
    emitted=[];book._emit(emitted,'UNWIND','primary',-1,2.,'combined_stop',1)
    results=[];errors=[]
    def run():
        try:results.append(V.dispatch(e,book,emitted[0],identity=identity,reference=ref,atr=2.))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=run,daemon=True);thread.start();thread.join(5)
    assert not thread.is_alive(),'group close must not reacquire an already-held non-reentrant lock'
    assert not errors and results[0]['status']=='UNWINDING'
    count=len(ex.sent)
    assert V.dispatch(e,book,emitted[0],identity=identity,reference=ref,atr=2.)['status']=='CLOSED'
    assert len(ex.sent)==count and not j.open_trades()
