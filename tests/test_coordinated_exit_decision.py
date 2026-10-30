"""Frozen exit intent survives group reloads and actual reduction recovery.

Synthetic venue/qualification only; input authenticity and strategy quality
are not established by these isolated transport checks.
"""
from copy import deepcopy
import json
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_two_instruments import pair
from trader.engine import coordinated_venue as V,coordinated_unwind as U
from trader.research.compose import hedge as H
from trader.strategy import exit_policy as E


def position(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch)
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    book.apply_fills({ins.oid:result['filled_quantity']})
    read=ex.fetch_positions
    monkeypatch.setattr(ex,'fetch_positions',lambda:[dict(p,symbol=p['symbol']+':USDT',
        info={'symbol':p['symbol'].replace('/','')}) for p in read()])
    main=next(r for r in j.open_trades() if r['symbol']=='BTC/USDT')
    root=json.loads(main['entry_identity_json'])
    p,s=E.decode(root['exit_state'])
    transition=E.advance(p,s,E.Observation(p.step_ms,100.,100.,94.,94.),float(main['amount']))
    assert transition.reason=='stop'
    main['_strategy_exit_evidence']=dict(authority='STRATEGY',exit_semantics_id=E.EXIT_SEMANTICS_ID,
        version_id=root['version_id'],install_id=root['install_id'],spec_hash=root['spec_sha256'],
        intent='stop',intended_quantity=float(main['amount']),state=json.loads(E.encode(p,transition.state)),
        group_id='g',group_exit_semantics_id=H.BOOK_EXIT_SEMANTICS_ID,scope='WHOLE_POSITION',
        decision_reference=dict(price=94.,basis='exit_decision_snapshot_price',observed_at='2026-10-30T00:00:00+00:00'))
    return ex,j,e,main


@pytest.mark.parametrize('lost_response',[False,True])
def test_whole_position_retains_first_exit_intent_on_both_actual_leg_bookings(setup,monkeypatch,lost_response):
    ex,j,e,main=position(setup,monkeypatch)
    if lost_response:ex.close_error=RequestTimeout('accepted close response lost')
    U.request_close(e,main,'stop')
    plan=U._plan(j.query('SELECT * FROM coordinated_group_unwinds')[0])
    retained=plan['strategy_exit_decision']
    assert retained['source_trade_id']==main['id'] and retained['evidence_scope']=='STRATEGY_INTENT_NOT_FILL'
    assert retained['source_receipt_status']=='NOT_RETAINED_BY_ADAPTER'
    ex.close_error=None
    for _ in range(5):
        U.resume(e)
        if not U.active(j,'g'):break
    assert not U.active(j,'g') and not j.open_trades() and not ex.positions
    closes=[x for x in ex.sent if x[1]=='market' and x[4].get('reduceOnly') and not x[4].get('stopLossPrice')]
    assert len(closes)==2
    bookings=[json.loads(r['payload']) for r in j.query('SELECT payload FROM trade_accounting_bookings')]
    terminal=[r for r in bookings if r['kind']=='close:stop']
    assert len(terminal)==2
    for receipt in terminal:
        assert receipt['evidence']['strategy_exit']==retained
        assert receipt['evidence']['order_id']
        reference=receipt['evidence']['reference']
        if receipt['trade_id']==main['id']:
            assert reference['price']==94. and reference['basis']=='exit_decision_snapshot_price'
        else:assert reference['price'] is None  # never reuse the main quote for its hedge
    requests=j.query('SELECT request_json FROM coordinated_reductions')
    assert len(requests)==2
    assert all(json.loads(r['request_json'])['strategy_exit_decision']==retained for r in requests)
    assert U._plan(j.query('SELECT * FROM coordinated_group_unwinds')[0])==plan


@pytest.mark.parametrize('field,value',[('version_id','other'),('install_id','other'),('spec_hash','other'),
    ('scope','HEDGE_ONLY'),('intent','signal'),('group_id','other')])
def test_conflicting_strategy_exit_metadata_is_not_booked_as_verified_intent(setup,monkeypatch,field,value):
    ex,j,e,main=position(setup,monkeypatch);count=len(ex.sent)
    main['_strategy_exit_evidence'][field]=value
    with pytest.raises(ValueError,match='strategy_exit_decision_differs'):U.request_close(e,main,'stop')
    assert len(ex.sent)==count and len(j.open_trades())==2
    if U._exists(j):assert not j.query('SELECT * FROM coordinated_group_unwinds')


def test_later_reason_does_not_replace_original_exit_decision(setup,monkeypatch):
    ex,j,e,main=position(setup,monkeypatch)
    U.request_close(e,main,'stop')
    first=U._plan(j.query('SELECT * FROM coordinated_group_unwinds')[0])
    later=deepcopy(main);later['_strategy_exit_evidence']['intent']='time'
    U.request_close(e,later,'time')
    assert U._plan(j.query('SELECT * FROM coordinated_group_unwinds')[0])==first
    assert all(json.loads(r['request_json'])['reason']=='stop' for r in j.query('SELECT request_json FROM coordinated_reductions'))


def test_strategy_hedge_stop_retains_intent_and_reconciles_only_closed_hedge(setup,monkeypatch):
    from tests.test_coordinated_native_hedge import held
    from trader.engine import coordinated_close as C
    ex,j,e,live,hedge=held(setup,monkeypatch)
    root=json.loads(hedge['entry_identity_json'])
    p,s=E.decode(root['hedge_exit_state'])
    transition=E.advance(p,s,E.Observation(s.last_bar_ms+p.step_ms,50.,56.,50.,56.),float(hedge['amount']))
    assert transition.reason=='stop'
    evidence=dict(authority='STRATEGY',exit_semantics_id=E.EXIT_SEMANTICS_ID,
        version_id=root['version_id'],install_id=root['install_id'],spec_hash=root['spec_sha256'],
        intent='stop',intended_quantity=float(hedge['amount']),state=json.loads(E.encode(p,transition.state)),
        group_id='g',group_exit_semantics_id=H.BOOK_EXIT_SEMANTICS_ID,scope='HEDGE_ONLY')
    result=C.close_hedge_once(e,hedge['id'],'g','fixture-stop',reason='stop',exit_evidence=evidence)
    assert result['status']=='CLOSED'
    receipt=json.loads(j.query("SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id DESC LIMIT 1",
                               (hedge['id'],))[0]['payload'])
    assert receipt['evidence']['strategy_exit']['source_trade_id']==hedge['id']
    assert receipt['evidence']['strategy_exit']['scope']=='HEDGE_ONLY'
    assert [r['symbol'] for r in j.open_trades()]==['BTC/USDT']
    assert live.drain('g',references={},atrs={})['status']=='HEDGE_CLOSED_RECONCILED'
    assert live.load('g')['book']['pos']=={'primary':2.,'hedge':0.}
    count=len(ex.sent)
    assert C.close_hedge_once(e,hedge['id'],'g','fixture-stop',reason='stop',exit_evidence=evidence)['status']=='CLOSED'
    assert len(ex.sent)==count
