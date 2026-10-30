"""Native hedge closure adopts venue absence, not an invented fill or PnL."""
from copy import deepcopy
from dataclasses import asdict
import json
import time
from unittest.mock import Mock
import pytest
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_two_instruments import pair
from tests.test_strategy_hedge_stops import current,observed
from trader.engine import coordinated_venue as V
from trader.engine.coordinated_live import LiveBooks,run_once


def held(setup,monkeypatch,action='close_hedge'):
    ex,j,e,book,ins,identity,ref=pair(setup,monkeypatch,management=current(action=action))
    result=V.dispatch(e,book,ins,identity=identity,reference=ref,atr=2.)
    book.apply_fills({ins.oid:result['filled_quantity']})
    root=deepcopy(identity);root['coordinated']['leg']='primary'
    live=LiveBooks(e);live.register(book,root,prepared_ms=int(time.time()*1000))
    hedge=next(r for r in j.open_trades() if r['symbol']=='ETH/USDT')
    read=ex.fetch_positions
    monkeypatch.setattr(ex,'fetch_positions',lambda:[dict(p,symbol=p['symbol']+':USDT',
        info={'symbol':p['symbol'].replace('/','')}) for p in read()])
    return ex,j,e,live,hedge


def native_close(ex,j,hedge):
    j.close_trade(hedge['id'],56.,-12.,'sl_fill')
    ex.positions[:]=[p for p in ex.positions if p['symbol']!='ETH/USDT']


def test_native_hedge_close_survives_restart_and_does_not_close_main_or_claim_pnl(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch);native_close(ex,j,hedge)
    count=len(ex.sent);stops=deepcopy(ex.stops)
    result=run_once(e,None,[])[0]
    assert result['status']=='HEDGE_CLOSED_RECONCILED'
    state=LiveBooks(e).load('g')
    assert state['book']['pos']=={'primary':2.,'hedge':0.}
    assert state['book']['hedge_exit_state'] is None
    assert state['hedge_reentry_cooldown_pending']
    assert len(ex.sent)==count and ex.stops==stops
    assert [r['symbol'] for r in j.open_trades()]==['BTC/USDT']
    assert live.drain('g',references={},atrs={})['status']=='IDLE'
    events=j.query('SELECT payload FROM coordinated_live_actions')
    assert len(events)==1
    event=json.loads(events[0]['payload'])
    assert event['fill_claim'] is False and event['financial_result'] is None
    assert event['observation']['complete'] and event['observation']['observation_id']


@pytest.mark.parametrize('failure',['residual','unavailable','identity','primary_quantity'])
def test_no_reconciliation_without_exact_current_ownership_and_venue_absence(setup,monkeypatch,failure):
    ex,j,e,live,hedge=held(setup,monkeypatch)
    if failure=='residual':j.close_trade(hedge['id'],56.,-12.,'sl_fill')
    else:native_close(ex,j,hedge)
    if failure=='unavailable':monkeypatch.setattr(ex,'fetch_positions',Mock(side_effect=RuntimeError('unavailable')))
    if failure=='identity':
        before=live.load('g');after=deepcopy(before);after['identity']['install_id']='different'
        live._save(before,after)
    if failure=='primary_quantity':ex.positions[0]['contracts']=1.
    before=live.load('g');count=len(ex.sent)
    with pytest.raises((ValueError,RuntimeError)):live.drain('g',references={},atrs={})
    assert live.load('g')==before and len(ex.sent)==count


def test_open_current_hedge_is_not_erased(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch)
    before=live.load('g')
    assert live.drain('g',references={},atrs={})['status']=='IDLE'
    assert live.load('g')==before


def test_discard_only_unsubmitted_hedge_intent_and_rebase_cooldown_at_next_native_cut(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch)
    before=live.load('g');after=deepcopy(before)
    from trader.research.compose import hedge as H
    book=H.HedgeBook(**after['book']);out=[]
    book._emit(out,'UNWIND','hedge',1,abs(book.pos['hedge']),'strategy_hedge_stop',1)
    after['book']=asdict(book);live._save(before,after)
    native_close(ex,j,hedge)
    result=live.drain('g',references={},atrs={})
    assert result['reconciliation']['discarded_unsubmitted_intents']==[out[0].oid]
    before=live.load('g');at=max(int(time.time()*1000),before['observed_ms']+1)
    monkeypatch.setattr('trader.engine.coordinated_live.time.time',lambda:at/1000.)
    observation=observed(2,p=97.,h=56.,equity=190.);observation['decision_ms']=at
    after=live.prepare('g',2,observation)
    assert after['book']['last_rebalance']==2 and not after['book']['pending']
    assert not after.get('hedge_reentry_cooldown_pending')


def test_original_submitted_action_is_adopted_before_native_absence(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch)
    # Simulate a crash after the hedge entry completed but before model adoption.
    before=live.load('g');after=deepcopy(before)
    root=json.loads(hedge['entry_identity_json']);ins=root['coordinated_instruction']
    after['book']['pos']['hedge']=0.
    after['book']['pending']={ins['oid']:[ins['leg'],ins['side'],ins['qty'],ins['bar']]}
    after['book']['log']=[ins];live._save(before,after)
    native_close(ex,j,hedge);count=len(ex.sent)
    assert live.drain('g',references={},atrs={})['filled_quantity']>0
    assert live.load('g')['book']['pos']['hedge']<0
    assert live.drain('g',references={},atrs={})['status']=='HEDGE_CLOSED_RECONCILED'
    assert len(ex.sent)==count


def test_close_group_policy_keeps_whole_position_response(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch,action='close_group');native_close(ex,j,hedge)
    result=live.drain('g',references={},atrs={})
    assert result['status']!='HEDGE_CLOSED_RECONCILED'
    assert not j.open_trades()
    assert live.load('g')['book']['pos']['hedge']<0  # whole-group reconciliation owns the next step


def test_crash_before_reconciliation_commit_retries_without_any_order(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch);native_close(ex,j,hedge)
    before=live.load('g');count=len(ex.sent)
    monkeypatch.setattr(live,'_save',Mock(side_effect=RuntimeError('crash before commit')))
    with pytest.raises(RuntimeError,match='crash before commit'):live.drain('g',references={},atrs={})
    assert LiveBooks(e).load('g')==before
    assert LiveBooks(e).drain('g',references={},atrs={})['status']=='HEDGE_CLOSED_RECONCILED'
    assert len(j.query('SELECT * FROM coordinated_live_actions'))==1 and len(ex.sent)==count


def test_unresolved_owned_submission_cannot_be_erased_by_current_flat_read(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch);native_close(ex,j,hedge)
    # Another durable accepted delta can still produce a late position.
    root=json.loads(hedge['entry_identity_json'])
    with j._tx() as db:
        db.execute("INSERT INTO execution_requests(logical_id,decision_id,intent_hash,intent_json,client_order_id,state) "
                   "SELECT 'late-delta','late-delta',intent_hash,intent_json,'late-delta','ENTRY_UNKNOWN' "
                   "FROM execution_requests WHERE logical_id=?",(root['execution_binding']['logical_id'],))
    before=live.load('g');count=len(ex.sent)
    with pytest.raises(ValueError,match='hedge_closure_action_unresolved'):
        live.drain('g',references={},atrs={})
    assert live.load('g')==before and len(ex.sent)==count


def test_hedge_absence_does_not_discard_pending_main_exit(setup,monkeypatch):
    ex,j,e,live,hedge=held(setup,monkeypatch)
    before=live.load('g');after=deepcopy(before)
    from trader.research.compose import hedge as H
    book=H.HedgeBook(**after['book']);out=[]
    for leg in ('primary','hedge'):
        book._emit(out,'UNWIND',leg,-1 if leg=='primary' else 1,abs(book.pos[leg]),'strategy_exit',2)
    book.state='UNWINDING';after['book']=asdict(book);live._save(before,after)
    native_close(ex,j,hedge)
    assert live.drain('g',references={},atrs={})['status']=='HEDGE_CLOSED_RECONCILED'
    saved=live.load('g')['book']
    assert saved['state']=='UNWINDING' and list(saved['pending'])==[out[0].oid]
    live.drain('g',references={},atrs={})
    assert not j.open_trades()


@pytest.mark.parametrize('status,passes',[('HEDGE_CLOSED_RECONCILED',True),('UNAVAILABLE',False)])
def test_position_worker_recognizes_reconciliation_without_hiding_failure(tmp_path,monkeypatch,status,passes):
    from types import SimpleNamespace as NS
    from tests.test_split_kernel_runtime import minimal
    import trader.scheduling.kernel_runtime as runtime
    from trader.engine import coordinated_management as M,coordinated_unwind as U,coordinated_live as L
    k,r=minimal(tmp_path);k.executor=NS(journal=object());k._manages_exits=lambda:True
    monkeypatch.setattr(U,'resume',lambda e:[])
    monkeypatch.setattr(M,'run_once',lambda e:[dict(group_id='g',status='HEDGE_ENTRY_REQUIRED')])
    monkeypatch.setattr(L,'run_once',lambda *a:[dict(group_id='g',status=status)])
    monkeypatch.setattr(runtime,'analysis_context',lambda _:NS(_exposure_symbols=lambda:set(),_portfolio_holdings_market={}))
    if passes:
        r.positions();assert r.positions_at is not None
    else:
        with pytest.raises(ValueError,match='coordinated_management_incomplete'):r.positions()
        assert r.positions_at is None


def test_unknown_external_reduction_is_not_silently_forgotten(setup,monkeypatch):
    from trader.engine import coordinated_close as C
    ex,j,e,live,hedge=held(setup,monkeypatch);native_close(ex,j,hedge)
    C.ensure(j)
    with j._tx() as db:
        db.execute('INSERT INTO coordinated_reductions(logical_id,trade_id,group_id,request_json,state) VALUES(?,?,?,?,?)',
                   ('pending-stop',hedge['id'],'g','{}','ORDER_UNKNOWN'))
    before=live.load('g');count=len(ex.sent)
    with pytest.raises(ValueError,match='hedge_closure_reduction_unresolved'):
        live.drain('g',references={},atrs={})
    assert live.load('g')==before and len(ex.sent)==count
