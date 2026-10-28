"""One combined economic decision, using observed open-leg cashflows."""
from copy import deepcopy
import pytest
from tests.test_coordinated_accounting import component, assembled
from tests.test_whole_trade_accounting import Venue, seal
from tests.test_managed_hedge import policy
from trader.engine import trade_accounting as T, coordinated_marked as G
from trader.engine.entry_authority import digest


def leg(monkeypatch,role,symbol,mark,offset):
    part=component(monkeypatch,role=role,symbol=symbol,offset=offset)
    s=deepcopy(part['capture']['bookings']);s['receipts']=s['receipts'][:2]
    s['trade']=deepcopy(s['receipts'][-1]['after']);seal(s)
    v=Venue();v.fills=v.fills[:2];v.events=v.events[:2]
    for rows,key in ((v.fills,'time'),(v.funding,'time'),(v.events,'fundingTime')):
        for row in rows:row[key]+=offset;row['symbol']=symbol.replace('/','')
    sign=1 if role=='primary' else -1
    if sign<0:
        for fill in v.fills:
            fill['side']='SELL' if fill['side']=='BUY' else 'BUY';fill['realizedPnl']=-fill['realizedPnl']
    v.fapiPrivateV2GetPositionRisk=lambda _: [dict(symbol=symbol.replace('/',''),positionSide='BOTH',
        positionAmt=sign,entryPrice=100.,markPrice=mark,unRealizedProfit=sign*(mark-100.))]
    part['capture']=T.capture(s,v,marked=True)
    assert T.replay(part['capture'])['status']=='marked_as_of_venue_history'
    part['identity']['coordinated'].update(allocated_equity_usdt=1000.,management_policy=policy(),maintenance_fraction=.01)
    part['entry_request']['intent']['strategy_identity']=deepcopy(part['identity'])
    part['entry_request']['intent_hash']=digest(part['entry_request']['intent'])
    return part


def group(monkeypatch,pm=120.,hm=140.):
    parts=[leg(monkeypatch,'primary','BTC/USDT',pm,0),leg(monkeypatch,'hedge','ETH/USDT',hm,1200)]
    plan=assembled(parts)['closure']['plan']
    return dict(schema_version=G.VERSION,group_id='economic-group',members=plan['members'],components=parts)


def test_profitable_primary_does_not_hide_larger_hedge_loss(monkeypatch):
    result=G.assess(group(monkeypatch),now_ms=8000)
    assert result['marked_net_pnl_usdt']==-28.
    assert result['marked_equity_usdt']==972.
    assert result['decision']['exit_reason'] is None
    assert result['decision']['activate'] and result['hedge_present']
    assert not result['learning_eligible'] and not result['final_realized_outcome']


def test_profit_target_uses_total_equity_after_both_legs_costs(monkeypatch):
    result=G.assess(group(monkeypatch,pm=150.,hm=80.),now_ms=8000)
    assert result['marked_net_pnl_usdt']==62.
    assert result['decision']['exit_reason']=='combined_target'


def test_primary_alone_does_not_invent_a_second_position(monkeypatch):
    s=group(monkeypatch);s['members']=s['members'][:1];s['components']=s['components'][:1]
    r=G.assess(s,now_ms=8000)
    assert r['marked_net_pnl_usdt']==26. and not r['hedge_present']


@pytest.mark.parametrize('change',['stale','missing_component','wrong_version','unknown_fee','missing_capital'])
def test_missing_or_unowned_inputs_never_produce_usable_equity(monkeypatch,change):
    s=group(monkeypatch);now=8000
    if change=='stale':now=38_001
    elif change=='missing_component':s['components'].pop()
    elif change=='wrong_version':s['components'][1]['identity']['version_id']='another'
    elif change=='unknown_fee':s['components'][1]['capture']['fill_history']['pages'][0]['rows'][0]['commission']=None
    elif change=='missing_capital':s['components'][0]['identity']['coordinated'].pop('allocated_equity_usdt')
    with pytest.raises(ValueError):G.assess(s,now_ms=now)
