"""Observed open-position equity is distinct from a final realized outcome."""
from copy import deepcopy
import pytest
from tests.test_whole_trade_accounting import source, Venue, seal
from trader.engine import trade_accounting as A


def opened(monkeypatch, *, initial_only=False):
    s=source();s['receipts']=s['receipts'][:1 if initial_only else 2]
    s['trade']=deepcopy(s['receipts'][-1]['after']);s['observed_ms']=4200
    seal(s)
    v=Venue();v.fills=v.fills[:1 if initial_only else 2]
    v.events=[e for e in v.events if e['fundingTime']<4000]
    if initial_only:
        v.events=v.events[:1];v.funding=[]
    quantity=s['trade']['amount'];mark=120.
    v.fapiPrivateV2GetPositionRisk=lambda _: [dict(symbol='BTCUSDT',positionSide='BOTH',
        positionAmt=quantity,entryPrice=100.,markPrice=mark,unRealizedProfit=quantity*(mark-100.))]
    monkeypatch.setattr(A.time,'time',lambda:5.)
    return s,v


def reassess(a):
    a['assessment']=A.assess(a,marked=True)
    return A.replay(seal(a))


def test_actual_partial_exit_fee_and_funding_join_current_mark(monkeypatch):
    s,v=opened(monkeypatch)
    artifact=A.capture(s,v,marked=True);r=A.replay(artifact)
    assert r['status']=='marked_as_of_venue_history'
    assert r['marked_net_pnl_usdt']==26. # 10 realized + 20 unrealized - 2 fees - 2 funding
    assert r['marked_notional_usdt']==120.
    assert not r['learning_eligible'] and not r['final_realized_outcome']
    assert r['accounting'] is None and r['net_trade_pnl_usdt'] is None
    with pytest.raises(ValueError,match='incomplete_accounting'):
        A.verified_outcome(artifact,6000)


def test_marking_does_not_wait_for_a_future_funding_settlement(monkeypatch):
    s,v=opened(monkeypatch,initial_only=True)
    r=A.replay(A.capture(s,v,marked=True))
    assert r['status']=='marked_as_of_venue_history'
    assert r['marked_net_pnl_usdt']==39. # 2 * (120-100) minus the actual entry fee
    assert r['funding_usdt']==0.
    assert A.capture(s,v)['assessment']['status']=='incomplete' # default remains final accounting only


@pytest.mark.parametrize('field,value', [('positionAmt',.5),('markPrice',None),('unRealizedProfit',999.)])
def test_unknown_or_conflicting_venue_position_never_has_a_usable_mark(monkeypatch,field,value):
    s,v=opened(monkeypatch);a=A.capture(s,v,marked=True)
    a['position'][field]=value
    r=reassess(a)
    assert r['status']=='incomplete' and not r['learning_eligible']
    assert r.get('marked_net_pnl_usdt') is None


def test_unpublished_cashflow_stays_missing_not_zero(monkeypatch):
    s,v=opened(monkeypatch);v.funding=[]
    r=A.replay(A.capture(s,v,marked=True))
    assert r['status']=='incomplete'
    assert 'funding_event_cashflow_missing' in r['reasons'][0]


def test_old_position_or_foreign_fee_currency_cannot_authorize_equity_decision(monkeypatch):
    s,v=opened(monkeypatch);a=A.capture(s,v,marked=True)
    a['captured_ms']=a['position_observed_ms']+30_001
    assert reassess(a)['status']=='incomplete'
    a=A.capture(s,v,marked=True)
    a['fill_history']['pages'][0]['rows'][0]['commissionAsset']='BNB'
    assert reassess(a)['status']=='incomplete'
