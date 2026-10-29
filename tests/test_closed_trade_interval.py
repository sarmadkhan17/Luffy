"""Closed economics stay bound to the original interval when a coin is reused."""
import pytest
from trader.engine import trade_accounting as T
from tests.test_whole_trade_accounting import Venue,source


def proof():
    return dict(schema='coordinated-terminal-flat.v1',trade_id='trade',logical_id='close-owned',
        order_id='3',symbol='BTC/USDT',account_scope='demo-account',
        instrument_id='binance_usdm:futures:BTCUSDT',request_start_ms=4001,observed_ms=4050,
        flat=True,quantity=0.,source='fresh complete venue position read')


def test_later_trade_position_fees_and_funding_do_not_enter_closed_trade(monkeypatch):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    ex=Venue()
    ex.fills.append(dict(ex.fills[0],id=4,orderId=4,time=4600,qty=7,realizedPnl=0,commission=100))
    ex.funding.append(dict(ex.funding[0],tranId=43,time=4700,income=-100))
    def no_current_position(*args):raise AssertionError('a current reused position is not the old closed position')
    ex.fapiPrivateV2GetPositionRisk=no_current_position
    a=T.capture(source(),ex,terminal_flat=proof())
    r=T.replay(a)
    assert r['status']=='complete_as_of_venue_history',r
    assert r['net_trade_pnl_usdt']==25. and r['funding_usdt']==-2.
    assert a['schema_version']==T.INTERVAL_VERSION and a['end_ms']==4000
    assert a['funding_events']['end_ms']==5000  # wait for publication beyond close
    assert a['fill_history']['end_ms']==a['funding_history']['end_ms']==4000
    assert a['position_source']=='RETAINED_TERMINAL_FLAT_OBSERVATION'
    assert r['accounting']['reconciliation_version']==T.INTERVAL_VERSION
    assert [f['id'] for f in r['accounting']['fills']]==['1','2','3']


@pytest.mark.parametrize('field,value',[('trade_id','other'),('order_id','other'),('symbol','ETH/USDT'),
    ('flat',False),('quantity',1.),('request_start_ms',3999),('observed_ms',4200),('account_scope','')])
def test_missing_or_foreign_flat_evidence_cannot_finalize(monkeypatch,field,value):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    flat=proof();flat[field]=value
    a=T.capture(source(),Venue(),terminal_flat=flat)
    result=T.replay(a)
    assert result['status']=='incomplete' and not result['learning_eligible']


def test_no_public_funding_frontier_still_requires_retry(monkeypatch):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    ex=Venue();ex.events=[r for r in ex.events if r['fundingTime']<=4000]
    result=T.replay(T.capture(source(),ex,terminal_flat=proof()))
    assert result['reasons']==['funding_publication_frontier_pending_retry']


def test_unowned_fill_inside_original_interval_still_refuses(monkeypatch):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    ex=Venue();ex.fills.append(dict(ex.fills[0],id=4,orderId=4,time=3900))
    result=T.replay(T.capture(source(),ex,terminal_flat=proof()))
    assert result['reasons']==['unowned_or_missing_fills_retry']


def test_new_interval_schema_cannot_be_relabelled_as_legacy(monkeypatch):
    from tests.test_whole_trade_accounting import seal
    monkeypatch.setattr(T.time,'time',lambda:5.)
    a=T.capture(source(),Venue(),terminal_flat=proof())
    a['schema_version']=T.VERSION
    a['assessment']=T.assess(a)
    assert T.replay(seal(a))['reasons']==['capture_clock_conflict']
