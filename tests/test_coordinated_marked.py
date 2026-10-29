"""One combined economic decision, using observed open-leg cashflows."""
from copy import deepcopy
import pytest
from tests.test_coordinated_accounting import component, assembled
from tests.test_whole_trade_accounting import Venue, seal
from tests.test_managed_hedge import policy
from trader.engine import trade_accounting as T, coordinated_marked as G
from trader.engine.entry_authority import digest


def leg(monkeypatch,role,symbol,mark,offset,base=0):
    part=component(monkeypatch,role=role,symbol=symbol,offset=offset,base=base)
    s=deepcopy(part['capture']['bookings']);s['receipts']=s['receipts'][:2]
    s['trade']=deepcopy(s['receipts'][-1]['after']);seal(s)
    v=Venue();v.fills=v.fills[:2];v.events=v.events[:2]
    for rows,key in ((v.fills,'time'),(v.funding,'time'),(v.events,'fundingTime')):
        for row in rows:row[key]+=offset+base;row['symbol']=symbol.replace('/','')
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


def test_closed_hedge_loss_stays_in_open_primary_combined_equity(monkeypatch):
    s=group(monkeypatch)
    closed=component(monkeypatch,role='hedge',symbol='ETH/USDT',offset=1200)
    cap=closed['capture']
    cap.update(capture_kind='OPEN_POSITION_MARKED_ONLY',position_observed_ms=cap['observed_ms'])
    cap['position'].update(entryPrice=0.,markPrice=140.,unRealizedProfit=0.)
    cap['assessment']=T.assess(cap,marked=True);seal(cap)
    assert T.replay(cap)['marked_net_pnl_usdt']==-35.
    closed['identity']['coordinated']=deepcopy(s['components'][1]['identity']['coordinated'])
    closed['entry_request']['intent']['strategy_identity']=deepcopy(closed['identity'])
    closed['entry_request']['intent_hash']=digest(closed['entry_request']['intent'])
    s['components'][1]=closed
    s['members']=assembled(s['components'])['closure']['plan']['members']
    r=G.assess(s,now_ms=8000)
    assert r['marked_net_pnl_usdt']==-9.  # +26 current primary, -35 closed hedge
    assert r['gross_notional_usdt']==120. and not r['learning_eligible'] and not r['hedge_present']


def test_closed_primary_cannot_leave_an_independent_hedge_running(monkeypatch):
    s=group(monkeypatch)
    closed=component(monkeypatch)
    cap=closed['capture']
    cap.update(capture_kind='OPEN_POSITION_MARKED_ONLY',position_observed_ms=cap['observed_ms'])
    cap['position'].update(entryPrice=0.,markPrice=120.,unRealizedProfit=0.)
    cap['assessment']=T.assess(cap,marked=True);seal(cap)
    closed['identity']['coordinated']=deepcopy(s['components'][0]['identity']['coordinated'])
    closed['entry_request']['intent']['strategy_identity']=deepcopy(closed['identity'])
    closed['entry_request']['intent_hash']=digest(closed['entry_request']['intent'])
    s['components'][0]=closed
    s['members']=assembled(s['components'])['closure']['plan']['members']
    r=G.assess(s,now_ms=8000)
    assert r['decision']['exit_reason']=='primary_closed'
    assert r['hedge_present'] and not r['learning_eligible']


@pytest.mark.parametrize('change',['stale','missing_component','wrong_version','unknown_fee','missing_capital'])
def test_missing_or_unowned_inputs_never_produce_usable_equity(monkeypatch,change):
    s=group(monkeypatch);now=8000
    if change=='stale':now=38_001
    elif change=='missing_component':s['components'].pop()
    elif change=='wrong_version':s['components'][1]['identity']['version_id']='another'
    elif change=='unknown_fee':s['components'][1]['capture']['fill_history']['pages'][0]['rows'][0]['commission']=None
    elif change=='missing_capital':s['components'][0]['identity']['coordinated'].pop('allocated_equity_usdt')
    with pytest.raises(ValueError):G.assess(s,now_ms=now)


def interval_part(part):
    """Seal a synthetic terminal observation from the fixture's owned close."""
    cap=part['capture'];end=int(cap['orders'][-1]['updateTime'])
    cap.update(schema_version=T.INTERVAL_VERSION,end_ms=end,
        position_source='RETAINED_TERMINAL_FLAT_OBSERVATION',terminal_flat=dict(
            schema='coordinated-terminal-flat.v1',trade_id=cap['bookings']['trade']['id'],
            logical_id='owned-close',order_id=str(cap['orders'][-1]['orderId']),
            symbol=cap['bookings']['trade']['symbol'],account_scope='demo-account',
            instrument_id=part['identity']['execution_binding']['instrument_id'],
            request_start_ms=end+1,observed_ms=end+50,flat=True,quantity=0.))
    for name in ('fill_history','funding_history'):
        stream=cap[name];stream['end_ms']=end
        for page in stream['pages']:
            page['end_ms']=end;page['rows']=[r for r in page['rows'] if r['time']<=end]
    cap['assessment']=T.assess(cap);seal(cap)
    assert T.replay(cap)['learning_eligible']
    return part


def test_closed_interval_hedge_contributes_net_loss_without_a_current_position(monkeypatch):
    s=group(monkeypatch)
    closed=interval_part(component(monkeypatch,role='hedge',symbol='ETH/USDT',offset=1200))
    closed['identity']['coordinated']=deepcopy(s['components'][1]['identity']['coordinated'])
    closed['entry_request']['intent']['strategy_identity']=deepcopy(closed['identity'])
    closed['entry_request']['intent_hash']=digest(closed['entry_request']['intent'])
    s['components'][1]=closed;s['members']=assembled(s['components'])['closure']['plan']['members']
    result=G.assess(s,now_ms=8000)
    assert result['marked_net_pnl_usdt']==-9. and result['gross_notional_usdt']==120.
    assert not result['hedge_present'] and not result['learning_eligible']
    # A flat observation from a different account must not be mixed in.
    closed['capture']['terminal_flat']['account_scope']='different-account'
    closed['capture']['assessment']=T.assess(closed['capture']);seal(closed['capture'])
    with pytest.raises(ValueError,match='terminal_flat_scope_differs'):G.assess(s,now_ms=8000)


def reidentify(part,new_id):
    """A different synthetic venue order/fill stream for the next hedge entry."""
    cap=part['capture'];s=cap['bookings'];s['trade']['id']=new_id
    for receipt in s['receipts']:
        receipt['trade_id']=new_id
        for side in ('before','after'):
            if receipt[side]:receipt[side]['id']=new_id
        receipt['evidence']['order_id']='next-'+str(receipt['evidence']['order_id']);seal(receipt)
    seal(s)
    for order in cap['orders']:order['orderId']='next-'+str(order['orderId'])
    for name in ('fill_history','funding_history'):
        for page in cap[name]['pages']:
            for row in page['rows']:
                if name=='fill_history':
                    row['id']='next-'+str(row['id']);row['orderId']='next-'+str(row['orderId'])
                else:row['tranId']='next-'+str(row['tranId'])
    binding=part['identity']['execution_binding'];binding['logical_id']='next-hedge-entry'
    request=part['entry_request'];request['logical_id']=binding['logical_id'];request['order_id']='next-1'
    request['result']['position_id']=new_id
    request['intent']['strategy_identity']=deepcopy(part['identity']);request['intent_hash']=digest(request['intent'])
    if cap.get('terminal_flat'):
        cap['terminal_flat'].update(trade_id=new_id,order_id='next-'+str(cap['terminal_flat']['order_id']))
    marked=cap.get('capture_kind') in ('OPEN_POSITION_MARKED_ONLY','CLOSED_INTERVAL_MARKED_ONLY')
    cap['assessment']=T.assess(cap,marked=marked);seal(cap)
    assert T.replay(cap)['status']==('marked_as_of_venue_history' if marked else 'complete_as_of_venue_history')
    return part


def test_reopened_hedge_keeps_prior_closed_loss_and_only_one_current_exposure(monkeypatch):
    s=group(monkeypatch)
    old=interval_part(component(monkeypatch,role='hedge',symbol='ETH/USDT',offset=1200))
    new=reidentify(leg(monkeypatch,'hedge','ETH/USDT',100.,1200,base=10000),'hedge-next')
    for part in (old,new):
        part['identity']['coordinated']=deepcopy(s['components'][1]['identity']['coordinated'])
        part['entry_request']['intent']['strategy_identity']=deepcopy(part['identity'])
        part['entry_request']['intent_hash']=digest(part['entry_request']['intent'])
    s['components']=[s['components'][0],old,new]
    s['members']=assembled(s['components'])['closure']['plan']['members']
    result=G.assess(s,now_ms=18000)
    assert result['marked_net_pnl_usdt']==-23.  # +26 main, -35 old hedge, -14 new hedge
    assert result['gross_notional_usdt']==220. and result['hedge_present']
    assert not result['learning_eligible']  # one still-open economic trade
    # A second hedge before the first closed is unsupported, even if net PnL looks good.
    overlap=reidentify(leg(monkeypatch,'hedge','ETH/USDT',100.,1200),'hedge-next')
    overlap['identity']['coordinated']=deepcopy(new['identity']['coordinated'])
    overlap['entry_request']['intent']['strategy_identity']=deepcopy(overlap['identity'])
    overlap['entry_request']['intent_hash']=digest(overlap['entry_request']['intent'])
    s['components'][2]=overlap;s['members']=assembled(s['components'])['closure']['plan']['members']
    with pytest.raises(ValueError,match='shared_instrument_unsupported'):G.assess(s,now_ms=18000)
