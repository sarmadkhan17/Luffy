"""Owned-cashflow budgets and exact Risk receipts, with a synthetic venue."""
from copy import deepcopy
import json
import sqlite3
import zlib
import pytest
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_venue import prepared
from tests.test_coordinated_two_instruments import pair
from trader.engine import coordinated_budget as B,coordinated_venue as V
from trader.engine.coordinated_live import LiveBooks


def booked(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    request=j.query('SELECT * FROM execution_requests WHERE logical_id=?',(result['action_id'],))[0]
    root=json.loads(request['intent_json'])['strategy_identity']
    return ex,j,e,book,ins,root,ref,result


def test_initial_entry_shrinks_to_one_capital_allocation_and_retains_replayable_receipt(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch,leverage=3)
    book.management_entry_equity=50.;identity['coordinated']['allocated_equity_usdt']=50.
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert result['filled_quantity']==pytest.approx(1.497),result
    row=j.query('SELECT intent_json FROM execution_requests WHERE logical_id=?',(result['action_id'],))[0]
    root=json.loads(row['intent_json'])['strategy_identity']
    proof=B.verify(j,V.F.load_version(j,'v'),root,1.497,ref)
    assert proof['initial_margin_after']+proof['incremental_cost']<=50.
    assert len(ex.sent)==2 and j.open_trades()[0]['amount']==1.497
    # The model adopts only the actual result; its original intent is unchanged.
    assert book.pos['primary']==0. and ins.qty==2.
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with j._tx() as db:db.execute('DELETE FROM '+B.TABLE)


def test_zero_affordable_lot_abandons_unsubmitted_primary_without_false_fill_or_order(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    book.management_entry_equity=.0001;identity['coordinated']['allocated_equity_usdt']=.0001
    live=LiveBooks(e);live.register(book,identity,prepared_ms=identity['coordinated_instruction_observed_ms'])
    result=live.drain('g',references={'primary':ref},atrs={'primary':2.})
    assert result['status']=='INTENT_REFUSED' and result['filled_quantity']==0.
    assert result['submission']=='NONE' and result['financial_result'] is None
    assert live.load('g')['status']=='ABANDONED'
    assert not ex.sent and not j.query('SELECT * FROM execution_requests')


def test_real_second_leg_uses_only_remaining_combined_margin_and_exact_actual_fill(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch,leverage=1,sizing_pct=60.,ratio=1.,price=96.)
    requested=ins.qty
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert result['status']=='TERMINAL' and 0<result['filled_quantity']<requested,result
    root=json.loads(j.query('SELECT intent_json FROM execution_requests WHERE logical_id=?',(result['action_id'],))[0]['intent_json'])['strategy_identity']
    proof=B.verify(j,V.F.load_version(j,'v'),root,result['filled_quantity'],ref)
    assert proof['gross_before']==pytest.approx(115.2)
    assert proof['equity']==pytest.approx(195.152)
    assert proof['initial_margin_after']+proof['incremental_cost']<=proof['equity']+1e-10
    assert len(j.open_trades())==2 and len(ex.positions)==2
    count=len(ex.sent)
    assert V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)==result
    assert len(ex.sent)==count  # it never spends a second allocation on retry


@pytest.mark.parametrize('change',['action','group','version','price','stale','oversize','receipt'])
def test_capital_receipt_cannot_be_reused_for_different_entry_or_size(setup,monkeypatch,change):
    ex,j,e,book,ins,root,ref,result=booked(setup,monkeypatch)
    identity=deepcopy(root);reference=deepcopy(ref);requested=2.
    if change=='action':identity['coordinated_action_id']='other'
    elif change=='group':identity['coordinated']['group_id']='other'
    elif change=='version':identity['version_id']='other'
    elif change=='price':reference['price']=99.
    elif change=='receipt':identity['coordinated_capital_receipt']='missing'
    elif change=='oversize':requested=2.001
    else:monkeypatch.setattr(B.time,'time',lambda:(ref['observed_ms']+30001)/1000.)
    with pytest.raises(ValueError,match='coordinated_capital_'):
        B.verify(j,V.F.load_version(j,'v'),identity,requested,reference)


def test_lost_fee_evidence_refuses_increase_without_touching_primary_protection(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch)
    stops=deepcopy(ex.stops);count=len(ex.sent)
    monkeypatch.setattr(ex,'fapiPrivateGetUserTrades',lambda p:[])
    with pytest.raises(ValueError):V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    assert len(ex.sent)==count and ex.stops==stops and len(j.open_trades())==1


def test_retained_evidence_contains_owned_cashflows_without_copying_them_into_entry_identity(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch)
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    raw=j.query('SELECT intent_json FROM execution_requests WHERE logical_id=?',(result['action_id'],))[0]['intent_json']
    root=json.loads(raw)['strategy_identity'];key=root['coordinated_capital_receipt']
    blob=j.query('SELECT payload FROM '+B.TABLE+' WHERE receipt_id=?',(key,))[0]['payload']
    proof=json.loads(zlib.decompress(blob))
    assert proof['source']['kind']=='OWNED_MARKED_POSITION'
    assert proof['source']['evidence']['components'][0]['capture']['fill_history']['pages'][0]['rows']
    assert 'fill_history' not in raw
    assert len(blob)<len(zlib.decompress(blob))


@pytest.mark.parametrize('hedge_required',[False,True])
def test_no_entry_is_normal_but_a_required_unfilled_hedge_remains_visible(tmp_path,monkeypatch,hedge_required):
    from types import SimpleNamespace as NS
    from tests.test_split_kernel_runtime import minimal
    from trader.engine import coordinated_management as M,coordinated_live as L
    from trader.scheduling import kernel_runtime as R
    k,r=minimal(tmp_path);k.executor=NS(journal=NS());k._manages_exits=lambda:True
    monkeypatch.setattr(M.U,'resume',lambda e:[])
    monkeypatch.setattr(M,'run_once',lambda e:[dict(group_id='g',status='HEDGE_ENTRY_REQUIRED')] if hedge_required else [])
    monkeypatch.setattr(L,'run_once',lambda *a:[dict(group_id='g',status='INTENT_REFUSED')])
    monkeypatch.setattr(R,'analysis_context',lambda r:NS(_exposure_symbols=lambda:set(),_portfolio_holdings_market={}))
    if hedge_required:
        with pytest.raises(ValueError,match='coordinated_management_incomplete'):r.positions()
        assert r.positions_at is None
    else:
        r.positions()
        assert r.positions_at is not None
