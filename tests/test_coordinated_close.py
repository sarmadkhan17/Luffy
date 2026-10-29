"""Offline real Risk/entry/Executor receipts; no network or production writes."""
from copy import deepcopy
import json
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup
from trader.engine.coordinated_close import close_once,_Transport


def opened(setup,monkeypatch,*,fraction=1.):
    ex,j,e,d=setup;e.fill_retry_s=0
    create=ex.create_order
    def submit(symbol,type_,side,amount,params=None):
        reduction=(params or {}).get('reduceOnly') and not (params or {}).get('stopLossPrice') and type_!='STOP_MARKET'
        try:order=create(symbol,type_,side,amount,params)
        except Exception:
            order=ex.orders.get((params or {}).get('newClientOrderId'))
            if order is not None and reduction:
                order['reduceOnly']=True;order['filled']=amount*fraction
                ex.positions=[] if fraction==1. else [dict(symbol=symbol,side='long',contracts=amount*(1-fraction),entryPrice=100.,markPrice=100.)]
            raise
        if reduction:
            order['reduceOnly']=True;order['filled']=amount*fraction
            ex.positions=[] if fraction==1. else [dict(symbol=symbol,side='long',contracts=amount*(1-fraction),entryPrice=100.,markPrice=100.)]
        return order
    monkeypatch.setattr(ex,'create_order',submit)
    cancel=ex.cancel_order
    def cancel_(oid,symbol):
        if oid=='stop':ex.stops.clear();return {}
        return cancel(oid,symbol)
    monkeypatch.setattr(ex,'cancel_order',cancel_)
    identity=dict(coordinated=dict(group_id='group-fixture',leg='primary'))
    reference=dict(price=100.)
    permission=e.risk_manager.authorize_entry(d,2.,2.,95.,110.,'strategy',identity=identity,reference=reference)
    position=e.open(d,2.,2.,95.,110.,'strategy','strategy',entry_identity=identity,reference=reference,risk_permission=permission)
    assert position is not None,d.skip_reason
    return ex,j,e,position.id


def test_exact_reduce_is_booked_once_and_repeated_call_never_resubmits(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch)
    result=close_once(e,tid,'group-fixture','close-fixture')
    assert result['status']=='BOOKED'
    count=len(ex.sent)
    assert close_once(e,tid,'group-fixture','close-fixture')['status']=='BOOKED'
    assert len(ex.sent)==count
    assert j.query('SELECT status FROM trades WHERE id=?',(tid,))[0]['status']=='closed'


def test_accepted_timeout_and_renamed_retry_cannot_submit_again(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch)
    ex.close_error=RequestTimeout('synthetic accepted then timed out')
    first=close_once(e,tid,'group-fixture','close-timeout')
    assert first['status']=='ATTEMPTED'
    count=len(ex.sent)
    with pytest.raises(ValueError,match='previous_reduction_pending'):
        close_once(e,tid,'group-fixture','renamed-close')
    ex.read_error=True
    assert close_once(e,tid,'group-fixture','close-timeout')['status']=='ATTEMPTED'
    ex.read_error=False;ex.close_error=None
    assert close_once(e,tid,'group-fixture','close-timeout')['status']=='BOOKED'
    assert len(ex.sent)==count


def test_crash_after_booking_is_recovered_from_exact_accounting_receipt(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch)
    save=_Transport._save
    def crash(self,state,*args,**kw):
        if state=='BOOKED':raise RuntimeError('synthetic marker crash')
        return save(self,state,*args,**kw)
    monkeypatch.setattr(_Transport,'_save',crash)
    with pytest.raises(RuntimeError,match='marker crash'):
        close_once(e,tid,'group-fixture','close-crash')
    count=len(ex.sent)
    monkeypatch.setattr(_Transport,'_save',save)
    assert close_once(e,tid,'group-fixture','close-crash')['status']=='BOOKED'
    assert len(ex.sent)==count


def test_a_small_partial_fill_remains_open_and_keeps_its_stop(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch,fraction=.995)
    assert close_once(e,tid,'group-fixture','partial-close')['status']=='BOOKED'
    trade=j.query('SELECT * FROM trades WHERE id=?',(tid,))[0]
    assert trade['status']=='open' and trade['amount']==pytest.approx(.01)
    assert ex.stops


def test_an_unowned_leg_can_never_reach_submission(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch);count=len(ex.sent)
    with pytest.raises(ValueError,match='ownership_unproven'):
        close_once(e,tid,'other-group','wrong-close')
    assert len(ex.sent)==count


def test_contradictory_venue_position_does_not_erase_a_stop_or_claim_flat(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch)
    monkeypatch.setattr(ex,'fetch_positions',lambda:[dict(symbol='BTC/USDT',side='long',contracts=2.)])
    result=close_once(e,tid,'group-fixture','contradictory-close')
    assert result['status']=='TERMINAL'
    assert j.query('SELECT status FROM trades WHERE id=?',(tid,))[0]['status']=='open'
    assert ex.stops


def test_declared_delta_reduction_does_not_flatten_the_position(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch)
    result=close_once(e,tid,'group-fixture','rebalance-reduce',quantity=.5)
    assert result['status']=='BOOKED'
    row=j.open_trades()[0]
    assert row['amount']==1.5 and row['tp1_done']==0
    assert ex.sent[-1][3]==.5 and ex.stops
    count=len(ex.sent)
    assert close_once(e,tid,'group-fixture','rebalance-reduce',quantity=.5)['status']=='BOOKED'
    assert len(ex.sent)==count


def test_delta_reduction_quantization_cannot_increase_the_requested_rebalance(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch);count=len(ex.sent)
    monkeypatch.setattr('trader.engine.executor.quantize',lambda ex,symbol,amount:amount*2)
    with pytest.raises(ValueError,match='reduce_quantity_invalid'):
        close_once(e,tid,'group-fixture','oversized-rebalance',quantity=.5)
    assert len(ex.sent)==count


@pytest.mark.parametrize('field',['version_id','spec_hash','spec_sha256','install_id','exit_semantics_id','exit_state'])
def test_changed_caller_identity_does_not_match_original_entry_receipt(setup,monkeypatch,field):
    ex,j,e,tid=opened(setup,monkeypatch);count=len(ex.sent)
    root=json.loads(j.open_trades()[0]['entry_identity_json']);root[field]='changed'
    from trader.engine.coordinated_close import ownership
    trade=dict(j.open_trades()[0],entry_identity_json=json.dumps(root))
    with pytest.raises(ValueError,match='coordinated_entry_receipt_differs'):
        ownership(e,trade,'group-fixture')
    assert len(ex.sent)==count


def test_actual_terminal_flat_observation_survives_crash_after_booking(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch)
    save=_Transport._save
    def crash(self,state,*args,**kwargs):
        if state=='BOOKED':raise RuntimeError('after booking')
        return save(self,state,*args,**kwargs)
    monkeypatch.setattr(_Transport,'_save',crash)
    with pytest.raises(RuntimeError,match='after booking'):close_once(e,tid,'group-fixture','retained-flat')
    row=j.query('SELECT * FROM coordinated_reductions')[0]
    proof=json.loads(row['result_json'])['flat_evidence']
    assert proof['trade_id']==tid and proof['logical_id']=='retained-flat'
    assert proof['order_id']==str(json.loads(row['order_json'])['id'])
    assert proof['flat'] is True and proof['quantity']==0.
    assert proof['request_start_ms']<=proof['observed_ms']
    count=len(ex.sent)
    monkeypatch.setattr(_Transport,'_save',save)
    # Recovery uses the retained booking; no later position claim is invented.
    ex.positions=[dict(symbol='BTC/USDT',side='long',contracts=7.)]
    assert close_once(e,tid,'group-fixture','retained-flat')['status']=='BOOKED'
    assert json.loads(j.query('SELECT result_json FROM coordinated_reductions')[0]['result_json'])['flat_evidence']==proof
    from trader.engine.coordinated_close import retained_terminal_flat
    assert retained_terminal_flat(j,tid,'group-fixture',proof['order_id'])==proof
    assert retained_terminal_flat(j,tid,'group-fixture','different-order') is None
    assert len(ex.sent)==count
