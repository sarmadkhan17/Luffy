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


def test_open_group_can_mark_closed_leg_without_claiming_final_funding(monkeypatch):
    monkeypatch.setattr(T.time,'time',lambda:5.)
    ex=Venue();ex.events=[r for r in ex.events if r['fundingTime']<=4000]
    ex.fapiPrivateV2GetPositionRisk=lambda p:(_ for _ in ()).throw(AssertionError('no current position read'))
    a=T.capture(source(),ex,terminal_flat=proof(),marked=True)
    result=T.replay(a)
    assert result['status']=='marked_as_of_venue_history',result
    assert result['marked_net_pnl_usdt']==25. and result['marked_notional_usdt']==0.
    assert not result['learning_eligible'] and not result['final_realized_outcome']
    assert result['basis']=='CLOSED_INTERVAL_CURRENT_CASHFLOW_RECEIPTS'
    assert result['mark_observed_ms']==5000
    assert T.assess(a)['reasons']==['funding_publication_frontier_pending_retry']


def test_complete_closed_component_is_reused_without_venue_reads(monkeypatch,tmp_path):
    from types import SimpleNamespace as NS
    from trader.core.journal import Journal
    from trader.engine.coordinated_management import component_capture
    monkeypatch.setattr(T.time,'time',lambda:5.)
    j=Journal(tmp_path/'journal.db');s=source();ex=Venue()
    first=component_capture(NS(journal=j,ex=ex),s,[],proof())
    assert T.replay(first)['learning_eligible']
    s['observed_ms']=6000  # a new export time does not rewrite the closed trade
    def no_read(*a,**kw):raise AssertionError('closed history must be reused')
    for method in ('fapiPrivateGetOrder','fapiPrivateV2GetPositionRisk','fapiPrivateGetUserTrades','fapiPrivateGetIncome','fapiPublicGetFundingRate'):
        setattr(ex,method,no_read)
    assert component_capture(NS(journal=j,ex=ex),s,[],proof())==first
    assert len(j.query('SELECT * FROM coordinated_closed_captures'))==1
    s['trade']=dict(s['trade'],entry_price=101.)
    with pytest.raises(ValueError,match='closed_capture_binding_differs'):
        component_capture(NS(journal=j,ex=ex),s,[],proof())


def test_closed_component_is_not_cached_until_funding_is_final(monkeypatch,tmp_path):
    from types import SimpleNamespace as NS
    from trader.core.journal import Journal
    from trader.engine.coordinated_management import component_capture
    monkeypatch.setattr(T.time,'time',lambda:5.)
    j=Journal(tmp_path/'journal.db');ex=Venue();later=ex.events.pop()
    first=component_capture(NS(journal=j,ex=ex),source(),[],proof())
    assert not T.replay(first)['learning_eligible']
    assert not j.query("SELECT 1 FROM sqlite_master WHERE name='coordinated_closed_captures'")
    ex.events.append(later)
    second=component_capture(NS(journal=j,ex=ex),source(),[],proof())
    assert T.replay(second)['learning_eligible']
    assert len(j.query('SELECT * FROM coordinated_closed_captures'))==1
