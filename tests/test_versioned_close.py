"""Single-position close plans use real isolated Risk and recovery transport."""
from copy import deepcopy
import json
from unittest.mock import Mock
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup as recovery_setup
setup = recovery_setup
from tests.test_versioned_owned_close import opened
from trader.engine import versioned_close as V,coordinated_close as C
from trader.strategy import exit_policy as E


def ready(setup,monkeypatch,**kwargs):
    ex,j,e,tid=opened(setup,monkeypatch,**kwargs)
    read=ex.fetch_positions
    monkeypatch.setattr(ex,'fetch_positions',lambda:[dict(p,symbol=p['symbol']+':USDT',
        info={'symbol':p['symbol'].replace('/','')}) for p in read()])
    trade=j.open_trades()[0];root=json.loads(trade['entry_identity_json']);p,s=E.decode(root['exit_state'])
    result=E.advance(p,s,E.Observation(1000,100.,100.,99.,99.,signal_exit=True),float(trade['amount']))
    assert result.reason=='signal'
    evidence=dict(authority='STRATEGY',version_id=root['version_id'],install_id=root['install_id'],
        spec_hash=root['spec_sha256'],exit_semantics_id=E.EXIT_SEMANTICS_ID,intent='signal',
        intended_quantity=float(trade['amount']),state=json.loads(E.encode(p,result.state)),
        decision_reference=dict(price=99.,basis='TEST_ONLY_received_quote',observed_at='2026-10-31T00:00:00+00:00'))
    return ex,j,e,trade,evidence


@pytest.mark.parametrize('lost',[False,True])
def test_full_close_and_restart_adopt_original_request_and_intent_once(setup,monkeypatch,lost):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    if lost:ex.close_error=RequestTimeout('synthetic accepted reply lost')
    first=V.request_close(e,trade,'signal',evidence=evidence)
    count=len(ex.sent)
    if lost:
        assert first['status']=='CLOSING'
        root=json.loads(trade['entry_identity_json'])
        assert V.entry_block(j,root['execution_binding']['instrument_id'])=='versioned_position_exit_pending'
        ex.close_error=None
        assert V.resume(e)[0]['status']=='CLOSED'
    else:assert first['status']=='CLOSED'
    assert not j.open_trades() and not ex.positions
    assert V.request_close(e,trade,'time',evidence=None)['status']=='CLOSED'
    assert len(ex.sent)==count
    receipt=json.loads(j.query("SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id DESC LIMIT 1",(trade['id'],))[0]['payload'])
    assert receipt['evidence']['strategy_exit']['intent']=='signal'
    assert receipt['evidence']['strategy_exit']['source_trade_id']==trade['id']
    assert receipt['evidence']['order_id']


def test_partial_fill_keeps_one_plan_and_closes_only_remaining_quantity(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch,fraction=.5)
    assert V.request_close(e,trade,'signal',evidence=evidence)['status']=='CLOSING'
    plan=V.load(j.query('SELECT * FROM '+V.TABLE)[0])
    # A later known terminal partial may receive another reduction. The frozen
    # reason and original decision cannot become a newly sized trade entry.
    for _ in range(3):V.resume(e)
    rows=j.query('SELECT * FROM '+C.VERSIONED_TABLE+' ORDER BY rowid')
    assert [json.loads(r['request_json'])['quantity'] for r in rows]==[2.,1.,.5,.25]
    assert len({json.loads(r['request_json'])['client_order_id'] for r in rows})==4
    assert all(r['state']=='BOOKED' for r in rows)
    assert V.load(j.query('SELECT * FROM '+V.TABLE)[0])==plan
    assert len(j.open_trades())==1 and j.open_trades()[0]['amount']==pytest.approx(.125)


def test_unknown_position_or_account_never_submits_or_closes_another_position(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    ex.positions[0]['contracts']=3.
    count=len(ex.sent)
    with pytest.raises(ValueError,match='owned_quantity_unconfirmed'):
        V.request_close(e,trade,'signal',evidence=evidence)
    assert len(ex.sent)==count and j.open_trades()
    assert V.resume(e)[0]['status']=='UNAVAILABLE'


def test_wrong_decision_cannot_create_a_plan_or_order(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    wrong=deepcopy(evidence);wrong['version_id']='other'
    count=len(ex.sent)
    with pytest.raises(ValueError,match='strategy_decision_differs'):
        V.request_close(e,trade,'signal',evidence=wrong)
    assert len(ex.sent)==count and not j.query('SELECT * FROM '+V.TABLE)


def test_native_closure_completes_control_plan_without_inventing_a_fill(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    original=ex.positions[:];ex.positions=[]
    with pytest.raises(ValueError,match='owned_quantity_unconfirmed'):V.request_close(e,trade,'signal',evidence=evidence)
    assert original and not j.query('SELECT * FROM '+C.VERSIONED_TABLE)
    j.close_trade(trade['id'],95.,-10.,'sl_fill')
    count=len(ex.sent)
    result=V.resume(e)[0]
    assert result['status']=='CLOSED' and result['fill_claim'] is False
    assert result['observation_id'] and len(ex.sent)==count


def test_pending_close_reserves_only_its_instrument_through_the_existing_risk_boundary(setup,monkeypatch):
    from trader.engine import coordinated_unwind as U
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    ex.close_error=RequestTimeout('accepted response lost')
    V.request_close(e,trade,'signal',evidence=evidence)
    iid=json.loads(trade['entry_identity_json'])['execution_binding']['instrument_id']
    assert U.entry_block(j,iid)=='versioned_position_exit_pending'
    assert U.entry_block(j,'binance_usdm:futures:ETHUSDT') is None
    ex.close_error=None
    assert V.resume(e)[0]['status']=='CLOSED'
    assert U.entry_block(j,iid) is None


def test_bad_http_message_is_not_exposed_in_worker_status(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    ex.positions=[]
    with pytest.raises(ValueError):V.request_close(e,trade,'signal',evidence=evidence)
    monkeypatch.setattr(ex,'fetch_positions',Mock(side_effect=RuntimeError('private URL with secret placeholder')))
    result=V.resume(e)[0]
    assert result['reason']=='RuntimeError' and 'private URL' not in str(result)


@pytest.mark.parametrize('status,passes',[('CLOSING',True),('CLOSED',True),('UNAVAILABLE',False),('REDUCTION_REFUSED',False)])
def test_existing_position_worker_resumes_and_reports_versioned_close(tmp_path,monkeypatch,status,passes):
    from types import SimpleNamespace as NS
    from tests.test_split_kernel_runtime import minimal
    from trader.engine import coordinated_management as M,coordinated_live as L,coordinated_unwind as U
    import trader.scheduling.kernel_runtime as runtime
    k,r=minimal(tmp_path);k.executor=NS(journal=object());k._manages_exits=lambda:True
    monkeypatch.setattr(U,'resume',lambda e:[])
    monkeypatch.setattr(M,'run_once',lambda e:[])
    monkeypatch.setattr(L,'run_once',lambda *a:[])
    resume=Mock(return_value=[dict(trade_id='t',status=status)])
    monkeypatch.setattr(V,'resume',resume)
    monkeypatch.setattr(runtime,'analysis_context',lambda _:NS(_exposure_symbols=lambda:set(),_portfolio_holdings_market={}))
    if passes:r.positions();assert r.positions_at is not None
    else:
        with pytest.raises(ValueError,match='versioned_close_incomplete'):r.positions()
        assert r.positions_at is None
    resume.assert_called_once_with(k.executor)
    assert r.jobs['positions'].interval==30 and r.snapshot()['versioned_closes'][0]['status']==status


def test_recovery_reads_original_order_when_booking_committed_but_ack_was_lost(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    assert V.request_close(e,trade,'signal',evidence=evidence)['status']=='CLOSED'
    count=len(ex.sent)
    # Crash-window equivalent: actual accounting already committed, durable
    # transport acknowledgement/plan completion not yet saved.
    with j._tx() as db:
        db.execute("UPDATE "+C.VERSIONED_TABLE+" SET state='ATTEMPTED',order_json=NULL")
        db.execute("UPDATE "+V.TABLE+" SET state='CLOSING'")
    assert V.resume(e)[0]['status']=='CLOSED'
    assert len(ex.sent)==count
    assert j.query('SELECT state FROM '+C.VERSIONED_TABLE)[0]['state']=='BOOKED'


def test_terminal_zero_fill_has_no_booking_and_next_tick_can_retry_remainder(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch,fraction=0.)
    V.request_close(e,trade,'signal',evidence=evidence)
    count=len(ex.sent)
    assert V.resume(e)[0]['status']=='CLOSING'
    row=j.query('SELECT * FROM '+C.VERSIONED_TABLE)[0]
    assert row['state']=='NO_FILL' and json.loads(row['result_json'])['fill_claim'] is False
    assert len(ex.sent)==count and j.open_trades()[0]['amount']==2.
    assert not j.query("SELECT * FROM trade_accounting_bookings WHERE trade_id=? AND json_extract(payload,'$.kind') LIKE 'close:%'",(trade['id'],))
    V.resume(e)
    rows=j.query('SELECT * FROM '+C.VERSIONED_TABLE)
    assert len(rows)==2 and len(ex.sent)==count+1
    assert json.loads(rows[1]['request_json'])['quantity']==2.


def test_recovery_rejects_reframing_frozen_child_decision(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    ex.close_error=RequestTimeout('accepted response lost')
    V.request_close(e,trade,'signal',evidence=evidence)
    row=j.query('SELECT * FROM '+C.VERSIONED_TABLE)[0]
    count=len(ex.sent)
    with pytest.raises(ValueError,match='decision_conflict'):
        C.close_versioned_once(e,trade['id'],row['logical_id'],reason='time',exit_decision=None)
    assert len(ex.sent)==count


def test_unknown_original_order_stays_reserved_and_reports_unavailable(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    ex.close_error=RequestTimeout('accepted response lost')
    V.request_close(e,trade,'signal',evidence=evidence)
    count=len(ex.sent);ex.read_error=True
    result=V.resume(e)[0]
    assert result['status']=='UNAVAILABLE' and len(ex.sent)==count
    assert j.query('SELECT state FROM '+C.VERSIONED_TABLE)[0]['state']=='ATTEMPTED'
    iid=json.loads(trade['entry_identity_json'])['execution_binding']['instrument_id']
    assert V.entry_block(j,iid)=='versioned_position_exit_pending'


def test_external_close_without_original_order_booking_requires_reconciliation(setup,monkeypatch):
    ex,j,e,trade,evidence=ready(setup,monkeypatch)
    ex.close_error=RequestTimeout('accepted response lost')
    V.request_close(e,trade,'signal',evidence=evidence)
    j.close_trade(trade['id'],95.,-10.,'sl_fill')
    before=j.query('SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id',(trade['id'],))
    count=len(ex.sent)
    result=V.resume(e)[0]
    assert result['status']=='EXTERNAL_CLOSE_RECONCILIATION_REQUIRED'
    assert len(ex.sent)==count
    assert j.query('SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id',(trade['id'],))==before
    row=j.query('SELECT * FROM '+C.VERSIONED_TABLE)[0]
    assert row['state']=='TERMINAL' and not C._booked(j,trade['id'],json.loads(row['order_json'])['id'])
