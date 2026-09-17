"""Synthetic venue receipts, never observed trading performance."""
from copy import deepcopy
import json
import sqlite3
import subprocess

import pytest

from trader.engine import booking, trade_accounting as A
from trader.engine.accounting import digest
from trader.cognition.outcomes import replay as outcome_replay


def seal(value):
    value['sha256'] = digest({k:v for k,v in value.items() if k != 'sha256'})
    return value


def source():
    t = dict(id='trade', decision_id='decision', symbol='BTC/USDT', side='long',
             amount=2, entry_price=100, exit_price=None, realized_pnl=0, status='open',
             strategy_id='strategy', exec_mode='live', market_type='futures',
             opened_at='1970-01-01T00:00:01+00:00', closed_at=None)
    rs = []
    def add(kind, before, after, e, observed):
        rs.append(seal(dict(schema_version='trade-booking.v1', trade_id='trade', kind=kind,
                           observed_ms=observed, before=deepcopy(before), after=deepcopy(after),
                           evidence=e, assessment=booking.assess(e))))
    add('entry', None, t, dict(basis='entry_order_confirmation',order_id='1',confirmed_quantity=2), 1100)
    before=deepcopy(t);t.update(amount=1,realized_pnl=9)
    add('align_delta',before,t,dict(basis='venue_order_fills',order_id='2',quantity=1), 3100)
    before=deepcopy(t);t.update(status='closed',exit_price=120,realized_pnl=28,closed_at='1970-01-01T00:00:04+00:00')
    add('close:signal',before,t,dict(basis='venue_order_fills',order_id='3',quantity=1),4100)
    return seal(dict(schema_version='trade-booking-export.v1',trade=t,observed_ms=4200,receipts=rs,
        status='incomplete_accounting',funding_usdt=None,net_economic_pnl_usdt=None,learning_eligible=False,
        reasons=['funding_unattributed','whole_trade_fill_attribution_pending']))


class Venue:
    def __init__(self):
        self.fills = [dict(id=i,orderId=i,symbol='BTCUSDT',side=side,positionSide='BOTH',
            time=ts,qty=qty,price=price,realizedPnl=pnl,commission=1,commissionAsset='USDT')
            for i,side,ts,qty,price,pnl in [(1,'BUY',1000,2,100,0),(2,'SELL',3000,1,110,10),(3,'SELL',4000,1,120,20)]]
        self.funding = [dict(tranId=42,symbol='BTCUSDT',incomeType='FUNDING_FEE',time=2000,income=-2,asset='USDT')]
        self.events = [dict(symbol='BTCUSDT',fundingTime=ts,fundingRate=.01) for ts in (500,2000,4500)]
    def fapiPrivateGetOrder(self,p):
        f=self.fills[int(p['orderId'])-1]
        return dict(orderId=f['orderId'],symbol=f['symbol'],side=f['side'],positionSide='BOTH',
                    status='FILLED',executedQty=f['qty'],time=f['time'],updateTime=f['time'])
    def fapiPrivateV2GetPositionRisk(self,p):
        return [dict(symbol='BTCUSDT',positionSide='BOTH',positionAmt='0')]
    def fapiPrivateGetUserTrades(self,p):
        return [f for f in self.fills if p['startTime']<=f['time']<=p['endTime']]
    def fapiPrivateGetIncome(self,p):
        return [f for f in self.funding if p['startTime']<=f['time']<=p['endTime']]
    def fapiPublicGetFundingRate(self,p):
        return [f for f in self.events if p['startTime']<=f['fundingTime']<=p['endTime']]


@pytest.fixture
def artifact(monkeypatch):
    monkeypatch.setattr(A.time,'time',lambda:5)
    return A.capture(source(),Venue())


def reassess(a):
    a['assessment']=A.assess(a)
    return A.replay(seal(a))


def test_whole_trade_signed_funding_entry_fee_and_partial_exit(artifact):
    r=A.replay(artifact)
    assert r['status']=='complete_as_of_venue_history'
    assert r['net_trade_pnl_usdt']==25  # 30 gross - all 3 fees - 2 funding
    assert r['funding_usdt']==-2
    outcome=A.verified_outcome(artifact,6000)
    assert outcome_replay(outcome)==outcome
    assert outcome['actual_execution']['net_pnl']==25
    assert outcome['actual_execution']['environment']=='demo'


def test_positive_funding_is_income(artifact):
    artifact['funding_history']['pages'][0]['rows'][0]['income']=2
    assert reassess(artifact)['net_trade_pnl_usdt']==29


def test_zero_funding_requires_complete_event_and_income_history(artifact):
    artifact['funding_history']['pages'][0]['rows']=[]
    assert not reassess(artifact)['learning_eligible']
    artifact['funding_events']['pages'][0]['rows'].pop(1)
    assert reassess(artifact)['funding_usdt']==0
    artifact['funding_history']['pages']=[]
    assert reassess(artifact)['funding_usdt'] is None


@pytest.mark.parametrize('field,value', [('commission',None),('commissionAsset',None),('positionSide','LONG'),
    ('realizedPnl','nan'),('qty',3),('orderId',4),('id',None),('price',105),('side','SELL')])
def test_missing_or_conflicting_fill_evidence_refuses(artifact,field,value):
    artifact['fill_history']['pages'][0]['rows'][0][field]=value
    assert not reassess(artifact)['learning_eligible']
    with pytest.raises(ValueError,match='incomplete_accounting_retry'):A.verified_outcome(artifact,6000)


def test_non_usdt_fees_preserve_currency_and_unknown_conversion(artifact):
    artifact['fill_history']['pages'][0]['rows'][0]['commissionAsset']='BNB'
    r=reassess(artifact)
    assert r['fees_by_currency']=={'BNB':1,'USDT':2}
    assert r['net_trade_pnl_usdt'] is None
    assert r['reasons']==['currency_conversion_missing_retry']


@pytest.mark.parametrize('mutation', ['overlap','nonflat','missing_order','nonterminal','quantity','entry_realized',
    'funding_currency','funding_boundary','funding_duplicate','funding_missing','history_gap','history_error','legacy','chain','order_reused'])
def test_refusal_paths(artifact,mutation):
    if mutation=='overlap':artifact['overlapping_trade_ids']=['other']
    elif mutation=='nonflat':artifact['position']['positionAmt']='1'
    elif mutation=='missing_order':artifact['orders'].pop()
    elif mutation=='nonterminal':artifact['orders'][0]['status']='NEW'
    elif mutation=='quantity':artifact['orders'][0]['executedQty']=3
    elif mutation=='entry_realized':artifact['fill_history']['pages'][0]['rows'][0]['realizedPnl']=1
    elif mutation=='funding_currency':artifact['funding_history']['pages'][0]['rows'][0]['asset']='BNB'
    elif mutation=='funding_boundary':artifact['funding_history']['pages'][0]['rows'][0]['time']=3000
    elif mutation=='funding_duplicate':
        row=deepcopy(artifact['funding_history']['pages'][0]['rows'][0]);row['tranId']=43
        artifact['funding_history']['pages'][0]['rows'].append(row)
    elif mutation=='funding_missing':artifact['funding_history']['pages'][0]['rows'][0]['income']=None
    elif mutation=='history_gap':artifact['fill_history']['pages'][0]['start_ms']+=1
    elif mutation=='history_error':artifact['fill_history']['errors']=['network_retry']
    elif mutation=='legacy':artifact['bookings']['receipts']=[];seal(artifact['bookings'])
    elif mutation=='chain':
        r=artifact['bookings']['receipts'][1];r['before']['amount']=5;seal(r);seal(artifact['bookings'])
    elif mutation=='order_reused':
        r=artifact['bookings']['receipts'][1];r['evidence']['order_id']='1';r['assessment']=booking.assess(r['evidence']);seal(r);seal(artifact['bookings'])
    assert not reassess(artifact)['learning_eligible']


def test_same_fill_dedup_conflict_refused(artifact):
    rows=artifact['fill_history']['pages'][0]['rows']
    rows.append(deepcopy(rows[0]))
    assert reassess(artifact)['net_trade_pnl_usdt']==25
    rows[-1]['commission']=2
    assert not reassess(artifact)['learning_eligible']


def test_saturated_history_is_split_without_timestamp_skips(monkeypatch):
    monkeypatch.setattr(A,'LIMIT',2)
    ex=Venue()
    h=A.history(ex,'fills','BTCUSDT',1000,5000,5000)
    assert len(h['pages'])>1
    assert len(A.history_rows(h,'fills','BTCUSDT',1000,5000,5000))==3
    ex.fills=[ex.fills[0]]*2
    h=A.history(ex,'fills','BTCUSDT',1000,1000,5000)
    assert 'history_timestamp_saturated_retry' in h['errors']


def test_retention_budget_and_redacted_failure(monkeypatch):
    ex=Venue()
    assert A.history(ex,'fills','BTCUSDT',0,1,81*A.DAY)['errors']
    monkeypatch.setattr(A,'MAX_PAGES',1)
    h=A.history(ex,'fills','BTCUSDT',0,7*A.DAY,7*A.DAY)
    assert 'history_page_budget_retry' in h['errors']
    def fail(p):raise RuntimeError('PRIVATE TOKEN')
    ex.fapiPrivateGetIncome=fail
    h=A.history(ex,'income','BTCUSDT',1000,5000,5000)
    assert 'PRIVATE' not in json.dumps(h)
    assert h['errors']==['history_fetch_failed:RuntimeError']


def test_rehashed_promotion_cannot_replay(artifact):
    artifact['assessment']['net_trade_pnl_usdt']=999
    seal(artifact)
    with pytest.raises(ValueError,match='replay_mismatch'):A.replay(artifact)


def test_source_free_cli_replay_and_complete_import(artifact,tmp_path):
    from trader.observability.outcomes import schema
    path=tmp_path/'receipt.json';path.write_text(json.dumps(artifact))
    memory=tmp_path/'memory.db'
    with sqlite3.connect(memory) as db:
        schema(db)
        db.execute("INSERT INTO typed_outcome_meta VALUES ('activated_ms',0)")
    cmd=['./venv/bin/python','-m','scripts.reconcile_trade_accounting','--replay',str(path),'--import-memory',str(memory)]
    subprocess.run(cmd,check=True,capture_output=True)
    subprocess.run(cmd,check=True,capture_output=True)
    with sqlite3.connect(memory) as db:
        rows=db.execute('SELECT payload FROM typed_outcomes').fetchall()
    assert len(rows)==1
    assert outcome_replay(json.loads(rows[0][0]))['actual_execution']['net_pnl']==25


def test_publication_frontier_missing_cannot_assert_zero(artifact):
    artifact['funding_events']['pages'][0]['rows'].pop()
    assert reassess(artifact)['reasons']==['funding_publication_frontier_pending_retry']


def test_nonfinite_raw_response_still_captures_retry(monkeypatch):
    monkeypatch.setattr(A.time,'time',lambda:5)
    venue=Venue();venue.fills[0]['commission']=float('nan')
    artifact=A.capture(source(),venue)
    assert A.replay(artifact)['retry_required']
    json.dumps(artifact,allow_nan=False)


def test_captured_availability_not_query_cutoff(artifact):
    artifact['captured_ms']=5500
    reassess(artifact)
    outcome=A.verified_outcome(artifact,6000)
    assert outcome['resolved_ms']==outcome['available_ms']==5500
    assert outcome_replay(outcome)==outcome


def test_saturated_parent_conflicts_with_child_refuse(monkeypatch):
    monkeypatch.setattr(A,'LIMIT',2)
    h=A.history(Venue(),'fills','BTCUSDT',1000,5000,5000)
    h['pages'][0]['rows'][0]['commission']=10
    with pytest.raises(ValueError,match='history_changed'):
        A.history_rows(h,'fills','BTCUSDT',1000,5000,5000)
