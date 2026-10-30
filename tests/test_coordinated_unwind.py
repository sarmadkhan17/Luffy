"""Whole-group recovery, including the existing Risk/venue receipt boundary."""
import json
from copy import deepcopy
from types import SimpleNamespace as NS
import threading
from unittest.mock import Mock
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_close import opened
from trader.engine import coordinated_unwind as G
from trader.engine import coordinated_close as C
from trader.engine import entry_authority as A
from trader.core.journal import Journal


def normalized(ex,monkeypatch):
    read=ex.fetch_positions
    def fetch():
        return [dict(p,symbol='BTC/USDT:USDT',info={'symbol':'BTCUSDT'}) for p in read()]
    monkeypatch.setattr(ex,'fetch_positions',fetch)


def test_real_owned_primary_closes_once_then_group_releases_reservation(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch);normalized(ex,monkeypatch)
    result=G.close_group_once(e,'group-fixture','whole-close')
    assert result['leg']=='primary' and result['reduction']['status']=='BOOKED'
    instrument=json.loads(j.open_trades()[0]['entry_identity_json']) if j.open_trades() else None
    assert instrument is None
    assert G.entry_block(j,'binance_usdm:futures:BTCUSDT')=='coordinated_instrument_unwind_pending'
    delayed_hedge={'coordinated':{'group_id':'group-fixture','leg':'hedge'}}
    assert G.entry_block(j,'binance_usdm:futures:ETHUSDT',delayed_hedge)=='coordinated_group_unwind_started'
    count=len(ex.sent)
    assert G.resume(e)==[{'group_id':'group-fixture','status':'CLOSED'}]
    assert not G.active(j,'group-fixture') and G.entry_block(j,'binance_usdm:futures:BTCUSDT') is None
    assert G.entry_block(j,'binance_usdm:futures:ETHUSDT',delayed_hedge)=='coordinated_group_unwind_started'
    assert G.entry_block(j,'binance_usdm:futures:ETHUSDT',{'coordinated':{'group_id':'new-economic-trade'}}) is None
    assert G.close_group_once(e,'group-fixture','whole-close')['status']=='CLOSED'
    assert len(ex.sent)==count
    with pytest.raises(ValueError,match='identity_differs'):
        G.close_group_once(e,'group-fixture','renamed-close')


def test_unknown_read_leaves_group_reserved_without_order_or_stop_change(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch);normalized(ex,monkeypatch)
    count=len(ex.sent);stops=deepcopy(ex.stops);ex.read_error=True
    with pytest.raises(RequestTimeout):G.close_group_once(e,'group-fixture','whole-close')
    assert G.active(j,'group-fixture') and len(ex.sent)==count and ex.stops==stops
    ex.read_error=False
    assert G.resume(e)[0]['reduction']['status']=='BOOKED'


def test_accepted_timeout_resolves_same_child_without_repeating_close(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch);normalized(ex,monkeypatch)
    ex.close_error=RequestTimeout('accepted then response lost')
    first=G.close_group_once(e,'group-fixture','whole-close')
    assert first['reduction']['status']=='ATTEMPTED'
    count=len(ex.sent)
    ex.close_error=None
    assert G.resume(e)[0]['reduction']['status']=='BOOKED'
    assert G.resume(e)[0]['status']=='CLOSED'
    assert len(ex.sent)==count


def test_real_partial_reductions_keep_stop_and_use_new_child_only_after_booking(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch,fraction=.5);normalized(ex,monkeypatch)
    submit=ex.create_order
    def unique_order(*args,**kw):
        order=submit(*args,**kw)
        params=kw.get('params') or (args[4] if len(args)>4 else {})
        if params.get('reduceOnly') and not params.get('stopLossPrice'):
            order['id']=params['newClientOrderId'];ex.orders[order['id']]=order
        return order
    monkeypatch.setattr(ex,'create_order',unique_order)
    first=G.close_group_once(e,'group-fixture','whole-close')
    assert first['reduction']['status']=='BOOKED'
    assert j.open_trades()[0]['amount']==pytest.approx(1.) and ex.stops
    second=G.resume(e)[0]
    assert second['reduction']['status']=='BOOKED'
    assert second['reduction']['client_order_id']!=first['reduction']['client_order_id']
    assert j.open_trades()[0]['amount']==pytest.approx(.5) and ex.stops
    assert G.active(j,'group-fixture')


def test_position_worker_resumes_group_and_individual_exit_cannot_race_it(setup,monkeypatch,tmp_path):
    from tests.test_split_kernel_runtime import minimal
    from trader.kernel import Kernel
    ex,j,e,tid=opened(setup,monkeypatch);normalized(ex,monkeypatch)
    ex.read_error=True
    with pytest.raises(RequestTimeout):G.close_group_once(e,'group-fixture','whole-close')
    # Existing individual exit path returns before using a leg's own geometry.
    assert Kernel._manage_one.__wrapped__(NS(journal=j),j.open_trades()[0],None,0) is None
    ex.read_error=False
    k,r=minimal(tmp_path);k.executor=e;k.journal=j;k._manages_exits=lambda:False
    view=NS(_exposure_symbols=lambda:set(),_portfolio_holdings_market={})
    monkeypatch.setattr('trader.scheduling.kernel_runtime.analysis_context',lambda runtime:view)
    count=len(ex.sent)
    r.positions()
    assert r.coordinated_unwind_results==[] and len(ex.sent)==count
    k._manages_exits=lambda:True
    r.positions()
    assert r.coordinated_unwind_results[0]['reduction']['status']=='BOOKED'
    r.positions()
    assert r.coordinated_unwind_results==[{'group_id':'group-fixture','status':'CLOSED'}]
    assert r.positions_at is not None


def test_pending_close_blocks_permission_already_issued_before_unwind(setup,monkeypatch):
    from tests.test_coordinated_increase import prepared
    ex,j,e,tid,identity,d,permission=prepared(setup,monkeypatch)
    normalized(ex,monkeypatch);ex.read_error=True
    with pytest.raises(RequestTimeout):G.close_group_once(e,'group-fixture','whole-close')
    count=len(ex.sent)
    with pytest.raises(ValueError,match='coordinated_instrument_unwind_pending'):
        A.validate(e,permission,d,.5,2.,95.,110.,'strategy',identity,{'price':100.})
    with pytest.raises(ValueError,match='coordinated_instrument_unwind_pending'):
        e.risk_manager.authorize_entry(d,.5,2.,95.,110.,'strategy',identity=identity,
            reference={'price':100.},increase_trade_id=tid)
    assert G.entry_block(j,'binance_usdm:futures:ETHUSDT') is None
    assert len(ex.sent)==count


def test_existing_executor_full_close_requests_whole_group_and_reports_pending(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch);normalized(ex,monkeypatch)
    row=j.open_trades()[0]
    assert e.close(row,100.,'manual') is False  # group completion still needs a fresh flat check
    count=len(ex.sent)
    assert G.resume(e)[0]['status']=='CLOSED'
    assert e.close(row,100.,'changed_later_reason') is True
    assert len(ex.sent)==count
    plan=json.loads(j.query('SELECT plan_json FROM coordinated_group_unwinds')[0]['plan_json'])
    assert plan['reason']=='manual'


def test_individual_partial_does_not_bypass_group_but_owned_delta_still_works(setup,monkeypatch):
    from tests.test_coordinated_increase import submit_fixture
    ex,j,e,tid=opened(setup,monkeypatch);submit_fixture(ex,monkeypatch)
    count=len(ex.sent)
    assert e.close_partial(j.open_trades()[0],1.,100.,'tp1') is False
    assert len(ex.sent)==count and j.open_trades()[0]['amount']==2.
    assert C.close_once(e,tid,'group-fixture','planned-group-delta',quantity=.5)['status']=='BOOKED'
    assert j.open_trades()[0]['amount']==pytest.approx(1.5) and ex.stops


def test_manual_close_message_is_pending_instead_of_false_venue_rejection(setup,monkeypatch):
    from trader.kernel import Kernel
    ex,j,e,tid=opened(setup,monkeypatch);normalized(ex,monkeypatch)
    j.kv_set('close_requests',json.dumps([tid]))
    kernel=NS(journal=j,executor=e,feed=NS(price=lambda symbol:100.),notifier=NS(send=Mock()))
    assert Kernel._drain_close_requests.__wrapped__(kernel)==0
    messages=[call.args[0] for call in kernel.notifier.send.call_args_list]
    assert len(messages)==1 and 'in progress' in messages[0] and 'REJECTED' not in messages[0]


def protocol(tmp_path,monkeypatch):
    # Leg transport itself is exercised above with real execution receipts.
    # This stand-in isolates two-leg sequencing and deliberately lost outcomes.
    j=Journal(tmp_path/'group.db')
    members=[dict(trade_id='p',leg='primary',instrument_id='P',symbol='P/USDT',entry_identity_sha256='p'),
             dict(trade_id='h',leg='hedge',instrument_id='H',symbol='H/USDT',entry_identity_sha256='h')]
    rows={m['trade_id']:{'id':m['trade_id'],'status':'open'} for m in members}
    query=j.query
    def read(sql,params=()):
        if sql=='SELECT * FROM trades WHERE id=?':return [dict(rows[params[0]])]
        return query(sql,params)
    monkeypatch.setattr(j,'query',read)
    locks={}
    e=NS(journal=j,_entry_lock=threading.RLock(),_lock_for=lambda key:locks.setdefault(key,threading.Lock()),recovery_pending=lambda:False)
    monkeypatch.setattr(G,'_members',lambda *args:deepcopy(members))
    monkeypatch.setattr(G,'_positions',lambda *args:{m['instrument_id']:1. for m in members if rows[m['trade_id']]['status']=='open'})
    calls=[]
    def close(ex,tid,group,logical_id,**kw):
        calls.append((tid,logical_id))
        rows[tid]['status']='closed'
        return {'status':'BOOKED'}
    monkeypatch.setattr(C,'close_once',close)
    return e,rows,calls,close


def test_both_legs_close_in_separate_ticks_with_primary_resolved_first(tmp_path,monkeypatch):
    e,rows,calls,close=protocol(tmp_path,monkeypatch)
    assert G.close_group_once(e,'g','close')['leg']=='primary'
    assert [c[0] for c in calls]==['p'] and rows['h']['status']=='open'
    assert G.resume(e)[0]['leg']=='hedge'
    assert [c[0] for c in calls]==['p','h']
    assert G.resume(e)==[{'group_id':'g','status':'CLOSED'}]


def test_uncertain_primary_never_releases_hedge_and_entry_recovery_blocks_all_closes(tmp_path,monkeypatch):
    e,rows,calls,close=protocol(tmp_path,monkeypatch)
    e.recovery_pending=lambda:True
    assert G.close_group_once(e,'g','close')['status']=='ENTRY_RECOVERY_PENDING'
    assert not calls
    e.recovery_pending=lambda:False
    def pending(*args,**kw):
        calls.append((args[1],args[3]));return {'status':'ATTEMPTED'}
    monkeypatch.setattr(C,'close_once',pending)
    assert G.resume(e)[0]['leg']=='primary'
    assert G.resume(e)[0]['leg']=='primary'
    assert calls[0]==calls[1] and rows['h']['status']=='open'
    # A closed journal leg alone is also insufficient when venue exposure remains.
    rows['p']['status']='closed'
    monkeypatch.setattr(G,'_positions',lambda *a:{'P':.1,'H':1.})
    assert G.resume(e)[0]['status']=='CLOSED_LEG_VENUE_EXPOSURE_UNRESOLVED'
    assert len(calls)==2


def test_changed_members_and_corrupt_plan_cannot_submit_a_hedge_reduction(tmp_path,monkeypatch):
    e,rows,calls,close=protocol(tmp_path,monkeypatch)
    G.close_group_once(e,'g','close')
    monkeypatch.setattr(G,'_members',lambda *a:[])
    assert G.resume(e)[0]['reason']=='coordinated_unwind_members_changed'
    assert len(calls)==1
    with e.journal._tx() as db:db.execute("UPDATE coordinated_group_unwinds SET plan_json='{}'")
    assert G.resume(e)[0]['reason']=='coordinated_unwind_plan_corrupt'
    assert len(calls)==1


def test_native_primary_close_schedules_hedge_unwind_only_after_venue_flat(tmp_path,monkeypatch):
    from trader.core.types import Position,Side
    j=Journal(tmp_path/'native-close.db')
    for tid,leg in (('p','primary'),('h','hedge'),('unrelated',None)):
        identity=(dict(coordinated=dict(group_id='g',leg=leg),execution_binding=dict(instrument_id=tid))
                  if leg else None)
        j.add_trade(Position(tid,tid+'/USDT',Side.LONG if leg!='hedge' else Side.SHORT,
            1.,100.,100.,strategy_id='fixture',entry_identity=identity))
    locks={};calls=[]
    e=NS(journal=j,_entry_lock=threading.RLock(),_lock_for=lambda key:locks.setdefault(key,threading.Lock()),recovery_pending=lambda:False)
    # Sequencing fixture: actual receipt/ownership transport is covered by
    # the real-Risk tests above. Neither a native close nor a mark implies PnL.
    monkeypatch.setattr(C,'ownership',lambda ex,trade,group:json.loads(trade['entry_identity_json'])['coordinated'])
    positions={'p':1.,'h':1.,'unrelated':1.}
    monkeypatch.setattr(G,'_positions',lambda *a:dict(positions))
    def close(ex,tid,*args,**kw):
        calls.append(tid);j.close_trade(tid,100.,0.,'fixture_close');positions.pop(tid,None)
        return {'status':'BOOKED'}
    monkeypatch.setattr(C,'close_once',close)
    assert G.resume(e)==[] and not calls  # an open primary still owns its hedge
    j.close_trade('p',100.,0.,'native_stop_fixture')
    assert G.resume(e)[0]['status']=='CLOSED_LEG_VENUE_EXPOSURE_UNRESOLVED'
    assert not calls  # journal closure alone cannot release protection
    positions.pop('p')
    assert G.resume(e)[0]['leg']=='hedge' and calls==['h']
    assert G.resume(e)==[{'group_id':'g','status':'CLOSED'}]
    assert j.open_trades()[0]['id']=='unrelated'


def test_one_failed_group_does_not_stop_another_group(tmp_path,monkeypatch):
    j=Journal(tmp_path/'groups.db');G.ensure(j)
    with j._tx() as db:
        for group in ('g1','g2'):
            plan=dict(group_id=group,logical_id=group,reason='fixture',members=[])
            db.execute('INSERT INTO coordinated_group_unwinds VALUES(?,?,?,?,?)',
                (group,group,A.canonical(plan),A.digest(plan),'UNWINDING'))
    def close(ex,group,*a,**kw):
        if group=='g1':raise ValueError('coordinated_fixture_failure')
        return {'group_id':group,'status':'CLOSED'}
    monkeypatch.setattr(G,'close_group_once',close)
    assert G.resume(NS(journal=j))==[
        {'group_id':'g1','status':'UNAVAILABLE','reason':'coordinated_fixture_failure'},
        {'group_id':'g2','status':'CLOSED'}]
    assert G._unavailable('g',RequestTimeout('private signed request'))['reason']=='RequestTimeout'


def test_group_failure_still_services_unrelated_positions_without_refreshing_health(tmp_path,monkeypatch):
    from tests.test_split_kernel_runtime import minimal
    k,r=minimal(tmp_path);k.executor=NS();k._manages_exits=lambda:True
    def fail(ex):raise ValueError('fixture')
    monkeypatch.setattr(G,'resume',fail)
    service=Mock(return_value=1)
    view=NS(_exposure_symbols=lambda:{'BTC/USDT'},_refresh_btc_context=Mock(),_peer_frames=lambda symbols:{},
        _service_exposure=service,_exposure_errors=[],_portfolio_holdings_market={})
    monkeypatch.setattr('trader.scheduling.kernel_runtime.analysis_context',lambda runtime:view)
    with pytest.raises(ValueError,match='scheduled_coordinated_unwind_incomplete'):r.positions()
    service.assert_called_once()
    assert r.positions_at is None and r.coordinated_unwind_results[0]['status']=='UNAVAILABLE'


@pytest.mark.parametrize('quantity,expected',[(1.,'UNWINDING'),(2.,'CLOSED_LEG_VENUE_EXPOSURE_UNRESOLVED')])
def test_group_close_can_pass_old_hedge_to_its_exact_owned_replacement(tmp_path,monkeypatch,quantity,expected):
    e,rows,calls,close=protocol(tmp_path,monkeypatch)
    members=G._members(e,'g')
    members.insert(1,dict(trade_id='h-old',leg='hedge',instrument_id='H',symbol='H/USDT',entry_identity_sha256='old'))
    rows['p']['status']='closed'
    rows['h-old']=dict(id='h-old',status='closed',closed_at='2026-10-29T00:00:00+00:00')
    rows['h'].update(amount=quantity,opened_at='2026-10-29T00:01:00+00:00')
    monkeypatch.setattr(G,'_members',lambda *a:deepcopy(members))
    positions={'H':1.}
    monkeypatch.setattr(G,'_positions',lambda *a:dict(positions))
    result=G.close_group_once(e,'g','close-reopened')
    assert result['status']==expected
    if quantity==1.:
        assert calls[0][0]=='h'  # close the current owned hedge, never repeat old close
        positions.clear()
        assert G.resume(e)[0]['status']=='CLOSED'
    else:assert calls==[]


@pytest.mark.parametrize('action,planned,replacement,expected',[
    ('close_group',False,False,True),
    ('close_hedge',False,False,False),
    ('close_group',True,False,False),
    ('close_group',False,True,False),
])
def test_frozen_strategy_response_to_reconciled_hedge_closure(tmp_path,monkeypatch,action,planned,replacement,expected):
    from trader.core.types import Position,Side
    from tests.test_strategy_hedge_stops import current
    j=Journal(tmp_path/'hedge-closed.db')
    for tid,leg in [('p','primary'),('h','hedge')]+([('replacement','hedge')] if replacement else []):
        identity=dict(coordinated=dict(group_id='g',leg=leg,management_policy=current(action=action)),
                      execution_binding=dict(instrument_id=tid))
        j.add_trade(Position(tid,tid+'/USDT',Side.LONG if leg=='primary' else Side.SHORT,
            1.,100.,100.,strategy_id='fixture',entry_identity=identity))
    j.close_trade('h',100.,0.,'reconciled_ghost')
    e=NS(journal=j)
    monkeypatch.setattr(C,'ownership',lambda ex,trade,group:json.loads(trade['entry_identity_json'])['coordinated'])
    monkeypatch.setattr(G,'_planned_hedge_close',lambda *a:planned)
    request=Mock(return_value={'group_id':'g','status':'UNWINDING'})
    monkeypatch.setattr(G,'request_close',request)
    assert bool(G.resume(e))==expected
    assert request.call_count==int(expected)
    if expected:
        assert request.call_args.args[2]=='hedge_closed_unexpectedly'
        # An observed closure is sufficient for the strategy response, but is
        # not presented as proof of a venue stop or a financial result.
        assert request.call_args.args[1]['id']=='p'


def test_planned_hedge_close_requires_exact_terminal_flat_receipt(tmp_path,monkeypatch):
    from trader.core.types import Position,Side
    j=Journal(tmp_path/'planned-hedge.db')
    j.add_trade(Position('h','ETH/USDT',Side.SHORT,1.,100.,100.,strategy_id='fixture'))
    j.close_trade('h',100.,0.,'ratio_drift')
    proof=Mock(return_value=None)
    monkeypatch.setattr(C,'retained_terminal_flat',proof)
    assert not G._planned_hedge_close(j,'h','g')
    proof.assert_called_once()
    proof.return_value={'schema':'coordinated-terminal-flat.v1'}
    assert G._planned_hedge_close(j,'h','g')
    # An unexpected close cannot be relabelled intentional using a flat proof.
    j.add_trade(Position('h2','ETH/USDT',Side.SHORT,1.,100.,100.,strategy_id='fixture'))
    j.close_trade('h2',100.,0.,'reconciled_ghost')
    assert not G._planned_hedge_close(j,'h2','g')
