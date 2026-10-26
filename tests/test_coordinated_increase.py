"""Actual Risk/entry/recovery contracts, offline venue only."""
import json
import time
from copy import deepcopy
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup  # noqa: F401 -- shared pytest fixture
from tests.test_coordinated_close import opened
from trader.core.types import Decision,Action
from trader.engine.entry_authority import proposal_binding


def refreshed(ex,j):
    from trader.observability.portfolio_observation import observe_positions
    from trader.engine.evidence_capture import position_snapshot,record_snapshot
    from trader.core.types import MarketType
    from trader.data.feed import execution_account_scope
    now=int(time.time()*1000)
    raw=[dict(ex.positions[0],symbol='BTC/USDT:USDT',info=dict(symbol='BTCUSDT'))]
    obs=observe_positions(raw,exchange_id='binanceusdm',market_type=MarketType.FUTURES,
        environment='demo',source_ref='https://demo-fapi.binance.com',request_start_ms=now,response_received_ms=now)
    rows=raw
    record_snapshot(j,position_snapshot(obs,rows,account_scope=execution_account_scope(ex)),at_ms=now)


def prepared(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch)
    ex.positions=[dict(symbol='BTC/USDT',side='long',contracts=2.,entryPrice=100.,markPrice=100.)]
    refreshed(ex,j)
    identity=json.loads(j.open_trades()[0]['entry_identity_json']);identity.pop('execution_binding')
    d=Decision('increase-d','c','BTC/USDT',Action.BUY,1.,.5,.8,[],[])
    d.instrument_binding_json=proposal_binding(j,d.symbol);j.log_decision(d)
    permission=e.risk_manager.authorize_entry(d,.5,2.,95.,110.,'strategy',identity=identity,
        reference=dict(price=100.),increase_trade_id=tid)
    return ex,j,e,tid,identity,d,permission


def submit_fixture(ex,monkeypatch):
    from tests.test_entry_recovery import Venue
    original=lambda *args,**kwargs:Venue.create_order(ex,*args,**kwargs)
    def create(symbol,type_,side,amount,params=None):
        old=deepcopy(ex.positions);stops_before=len(ex.stops)
        try:return original(symbol,type_,side,amount,params)
        finally:
            if not (params or {}).get('reduceOnly'):
                ex.positions=[dict(symbol=symbol,side='long',contracts=old[0]['contracts']+amount,
                    entryPrice=100.,markPrice=100.)]
            if (params or {}).get('stopLossPrice') and len(ex.stops)>stops_before:
                ex.stops[-1]['algoId']='full-stop'
            if (params or {}).get('reduceOnly') and not (params or {}).get('stopLossPrice'):
                q=max(0.,old[0]['contracts']-amount)
                ex.positions=[dict(old[0],contracts=q)] if q else []
                order=ex.orders.get((params or {}).get('newClientOrderId'))
                if order:order['reduceOnly']=True
    monkeypatch.setattr(ex,'create_order',create)
    original_cancel=ex.cancel_order
    def cancel(oid,symbol):
        if oid=='stop':ex.stops[:]=[s for s in ex.stops if s['algoId']!='stop'];return {}
        return original_cancel(oid,symbol)
    monkeypatch.setattr(ex,'cancel_order',cancel)


def increase(e,tid,identity,d,p):
    return e.increase(tid,d,.5,2.,95.,110.,'strategy',identity,dict(price=100.),p)


def test_delta_increase_is_one_net_trade_with_full_stop(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    submit_fixture(ex,monkeypatch)
    assert increase(e,tid,identity,d,p)['status']=='RECOVERY_PENDING'
    assert j.open_trades()[0]['amount']==2.
    e.recover_entries()
    assert not e.recovery_pending()
    rows=j.open_trades();assert len(rows)==1 and rows[0]['amount']==2.5
    assert rows[0]['sl_order_id']=='full-stop' and rows[0]['realized_pnl']==0
    assert json.loads(rows[0]['entry_identity_json'])['execution_binding']['logical_id']!='increase-d'
    assert len([x for x in ex.sent if not x[4].get('reduceOnly')])==2


def test_normal_open_cannot_consume_increase_permission(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch);count=len(ex.sent)
    assert e.open(d,.5,2.,95.,110.,'strategy','strategy',entry_identity=identity,
        reference=dict(price=100.),risk_permission=p) is None
    assert d.skip_reason=='increase_permission_requires_existing_position_path'
    assert len(ex.sent)==count


def test_ambiguous_increase_recovers_without_resubmission(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    submit_fixture(ex,monkeypatch);ex.entry_error=RequestTimeout('accepted synthetic')
    assert increase(e,tid,identity,d,p)['status']=='RECOVERY_PENDING'
    count=len(ex.sent);ex.entry_error=None
    e.recover_entries();e.recover_entries()
    assert not e.recovery_pending() and j.open_trades()[0]['amount']==2.5
    assert len(ex.sent)==count+1 # only the full protective stop


def test_changed_before_size_refuses_before_any_order(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch);count=len(ex.sent)
    with j._tx() as db:db.execute('UPDATE trades SET amount=1.9 WHERE id=?',(tid,))
    with pytest.raises(ValueError,match='binding_differs'):
        increase(e,tid,identity,d,p)
    assert len(ex.sent)==count


def test_crash_after_resize_booking_recovers_without_duplicate_delta(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    submit_fixture(ex,monkeypatch);increase(e,tid,identity,d,p)
    finish=e.recovery.finish
    monkeypatch.setattr(e.recovery,'finish',lambda *a:(_ for _ in ()).throw(RuntimeError('synthetic crash')))
    e.recover_entries()
    assert j.open_trades()[0]['amount']==2.5 and e.recovery_pending()
    count=len(ex.sent);monkeypatch.setattr(e.recovery,'finish',finish)
    e.recover_entries()
    assert not e.recovery_pending() and len(ex.sent)==count
    assert len(j.query("SELECT * FROM trade_accounting_bookings WHERE json_extract(payload,'$.kind')='increase'"))==1


def test_unknown_full_stop_cannot_release_increased_exposure(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    submit_fixture(ex,monkeypatch);ex.stop_error=True
    increase(e,tid,identity,d,p)
    assert e.recovery_pending()
    e.recover_entries()
    assert e.recovery.pending()['increase_unwind'] is True
    assert j.open_trades()[0]['amount']==2.5
    receipt=json.loads(j.query("SELECT payload FROM trade_accounting_bookings WHERE json_extract(payload,'$.kind')='increase'")[0]['payload'])
    assert receipt['evidence']['stop_observation']['status']=='UNCONFIRMED_FULL_REQUIRES_DELTA_ROLLBACK'
    assert receipt['assessment']['net_economic_pnl_usdt'] is None


def test_smaller_risk_budget_applies_to_total_not_just_delta(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    from trader.engine.risk import SizingResult
    monkeypatch.setattr(e.risk_manager,'check_entry',lambda *a,**kw:SizingResult(True,'ok',0,2.2,0,1))
    q=e.risk_manager.authorize_entry(d,.5,2.,95.,110.,'strategy',identity=identity,
        reference=dict(price=100.),increase_trade_id=tid)
    assert float(q.payload()['allowed'])==pytest.approx(.2)


def test_stop_failure_undoes_only_new_delta_and_keeps_existing_hedge(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    submit_fixture(ex,monkeypatch);ex.stop_error=True
    increase(e,tid,identity,d,p)
    e.recover_entries() # verified missing full stop -> record actual exposure
    e.recover_entries() # one durable reduce-only rollback of the added delta
    e.recover_entries() # original size and full original protection -> release
    assert not e.recovery_pending()
    assert j.open_trades()[0]['amount']==2.
    assert ex.positions[0]['contracts']==2. and ex.stops
    assert ex.sent[-1][3]==.5
    assert len([x for x in ex.sent if not x[4].get('reduceOnly')])==2
    assert len([x for x in ex.sent if x[4].get('reduceOnly') and not x[4].get('stopLossPrice')])==1


def test_changed_venue_identity_cannot_be_adopted_as_increased_exposure(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    submit_fixture(ex,monkeypatch);ex.entry_error=RequestTimeout('accepted synthetic')
    increase(e,tid,identity,d,p)
    ex.positions[0]['info']=dict(symbol='ETHUSDT')
    count=len(ex.sent);e.recover_entries()
    assert e.recovery_pending() and j.open_trades()[0]['amount']==2.
    assert len(ex.sent)==count


def test_increase_keeps_the_original_target_contract(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch);count=len(ex.sent)
    with pytest.raises(ValueError,match='target_must_preserve'):
        e.risk_manager.authorize_entry(d,.5,2.,95.,120.,'strategy',identity=identity,
            reference=dict(price=100.),increase_trade_id=tid)
    assert len(ex.sent)==count


def test_accepted_rollback_timeout_resolves_exact_action_before_size_reconciliation(setup,monkeypatch):
    ex,j,e,tid,identity,d,p=prepared(setup,monkeypatch)
    submit_fixture(ex,monkeypatch);ex.stop_error=True
    increase(e,tid,identity,d,p);e.recover_entries()
    ex.close_error=RequestTimeout('synthetic accepted rollback with lost response')
    e.recover_entries()
    assert ex.positions[0]['contracts']==2. and j.open_trades()[0]['amount']==2.5
    assert e.recovery_pending()
    count=len(ex.sent);ex.close_error=None
    e.recover_entries() # resolve and book the same reduction
    e.recover_entries() # prior protected quantity is now reconciled
    assert not e.recovery_pending() and j.open_trades()[0]['amount']==2.
    assert len(ex.sent)==count
