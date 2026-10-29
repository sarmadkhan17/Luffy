"""Combined valuation through the real position worker; synthetic venue only."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.test_coordinated_marked import group
from trader.core.journal import Journal
from trader.engine import coordinated_management as C, entry_authority as A


def executor(tmp_path, monkeypatch, *, target=False):
    evidence=group(monkeypatch, pm=150. if target else 120., hm=80. if target else 140.)
    journal=Journal(tmp_path/'journal.db')
    trades=[dict(entry_identity_json=json.dumps(p['identity'])) for p in evidence['components']]
    monkeypatch.setattr(journal,'open_trades',lambda:trades)
    monkeypatch.setattr(C,'source',lambda *a:evidence)
    close=Mock(return_value=dict(status='REDUCING'))
    monkeypatch.setattr(C.U,'close_group_once',close)
    return NS(journal=journal),evidence,close


def test_combined_loss_does_not_close_on_primary_profit(tmp_path,monkeypatch):
    e,_,close=executor(tmp_path,monkeypatch)
    result=C.run_once(e)
    assert result[0]['marked_net_pnl_usdt']==-28.
    assert result[0]['status']=='OBSERVED'
    close.assert_not_called()
    assert json.loads(e.journal.kv_get('coordinated_management_latest'))['results']==result


def test_whole_position_exit_is_durable_across_crash_before_close(tmp_path,monkeypatch):
    e,_,close=executor(tmp_path,monkeypatch,target=True)
    close.side_effect=RuntimeError('synthetic crash after decision persisted')
    assert C.run_once(e)[0]['status']=='UNAVAILABLE'
    assert C.pending_exit(e.journal,'economic-group')=='combined_target'
    # Recover the persisted exit without rerunning valuation or changing the logical ID.
    monkeypatch.setattr(C,'source',Mock(side_effect=AssertionError('must use retained decision')))
    close.side_effect=None
    assert C.run_once(e)[0]['status']=='EXIT_REQUESTED'
    assert close.call_args.args[2]==close.call_args_list[0].args[2]
    assert close.call_args.kwargs['reason']=='combined_target'
    assert len(e.journal.query('SELECT * FROM coordinated_management_exits'))==1


def test_protective_trigger_never_grants_a_new_entry(tmp_path,monkeypatch):
    e,_,close=executor(tmp_path,monkeypatch)
    evidence=group(monkeypatch,pm=70.)
    monkeypatch.setattr(C,'source',lambda *a:evidence)
    evidence['members']=evidence['members'][:1];evidence['components']=evidence['components'][:1]
    result=C.run_once(e)[0]
    assert result['status']=='HEDGE_ENTRY_REQUIRED' and result['decision']['activate']
    assert result['marked_net_pnl_usdt']==-24.
    close.assert_not_called()


def test_unavailable_cashflow_is_not_a_zero_or_an_exit(tmp_path,monkeypatch):
    e,evidence,close=executor(tmp_path,monkeypatch)
    evidence['components'][1]['capture']['errors']=['offline']
    assert C.run_once(e)[0]['status']=='UNAVAILABLE'
    close.assert_not_called()


def test_empty_book_has_no_venue_reads_or_writes():
    journal=NS(open_trades=lambda:[],kv_set=Mock())
    assert C.run_once(NS(journal=journal))==[]
    journal.kv_set.assert_not_called()


def test_actual_source_reads_only_owned_group_and_is_replayable(tmp_path,monkeypatch):
    evidence=group(monkeypatch)
    journal=Journal(tmp_path/'journal.db')
    with journal._tx() as db:
        db.executescript(A.SCHEMA)
    with journal._tx() as db:
        for part in evidence['components']:
            booking=part['capture']['bookings'];trade=booking['trade'];request=part['entry_request']
            values=dict(trade,entry_identity_json=json.dumps(part['identity']))
            db.execute('INSERT INTO trades('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',list(values.values()))
            for receipt in booking['receipts']:
                db.execute('INSERT INTO trade_accounting_bookings(trade_id,payload) VALUES(?,?)',(trade['id'],json.dumps(receipt)))
            db.execute('INSERT INTO execution_requests(logical_id,decision_id,intent_hash,intent_json,client_order_id,state,order_id,result_json) '
                       'VALUES(?,?,?,?,?,?,?,?)',(request['logical_id'],request['logical_id'],request['intent_hash'],json.dumps(request['intent']),
                       request['logical_id'],'TERMINAL',request['order_id'],json.dumps(request['result'])))
    monkeypatch.setattr(C.U,'_members',lambda *a:evidence['members'])
    parts={p['capture']['venue_symbol']:p['capture'] for p in evidence['components']}
    calls=[]
    def read(kind,params):
        calls.append((kind,params['symbol']))
        part=parts[params['symbol']]
        if kind=='position':return [part['position']]
        if kind=='order':return next(o for o in part['orders'] if str(o['orderId'])==str(params['orderId']))
        time_key='fundingTime' if kind=='funding_events' else 'time'
        return [r for page in part[kind]['pages'] for r in page['rows'] if params['startTime']<=r[time_key]<=params['endTime']]
    ex=NS(urls={'api':{'fapiPrivateV3':'https://demo-fapi.binance.com/fapi/v3'}},
        fapiPrivateGetOrder=lambda p:read('order',p),fapiPrivateV2GetPositionRisk=lambda p:read('position',p),
        fapiPrivateGetUserTrades=lambda p:read('fill_history',p),fapiPrivateGetIncome=lambda p:read('funding_history',p),
        fapiPublicGetFundingRate=lambda p:read('funding_events',p))
    result=C.M.assess(C.source(NS(journal=journal,ex=ex),'economic-group'),now_ms=8000)
    assert result['marked_net_pnl_usdt']==-28.
    assert len(calls)==12 and {s for _,s in calls}=={'BTCUSDT','ETHUSDT'}


def test_position_worker_calls_manager_on_existing_thirty_second_clock(tmp_path,monkeypatch):
    from tests.test_split_kernel_runtime import minimal
    import trader.scheduling.kernel_runtime as runtime
    k,r=minimal(tmp_path)
    k.executor=NS();k._manages_exits=lambda:True
    monkeypatch.setattr(C.U,'resume',lambda e:[])
    manage=Mock(return_value=[dict(status='HEDGE_ENTRY_REQUIRED')])
    monkeypatch.setattr(C,'run_once',manage)
    view=NS(_exposure_symbols=lambda:set(),_portfolio_holdings_market={})
    monkeypatch.setattr(runtime,'analysis_context',lambda _:view)
    with pytest.raises(ValueError,match='coordinated_management_incomplete'):
        r.positions()
    manage.assert_called_once_with(k.executor)
    assert r.jobs['positions'].interval==30 and r.positions_at is None
