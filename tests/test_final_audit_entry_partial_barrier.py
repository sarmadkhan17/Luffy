"""Partial UNKNOWN admission is also checked at both held entry boundaries."""
from contextlib import contextmanager
import json,time,pytest
from ccxt import RequestTimeout
from trader.core.types import Position,Side
from trader.engine.evidence_capture import position_snapshot,record_snapshot
from trader.observability.portfolio_observation import observe_positions
from tests.test_entry_recovery import setup  # noqa: F401
from tests.entry_authority_fixtures import permission


@pytest.mark.parametrize('appears_before_hold',[1,2])
def test_partial_intent_after_early_gate_cannot_reach_new_exposure_submit(setup,appears_before_hold):
    ex,j,e,d=setup
    cap=json.loads(j.kv_get('entry_capability:'+d.symbol))
    old_cap=dict(cap,record=dict(cap['record'],instrument_id=dict(cap['record']['instrument_id'],venue_symbol='ETHUSDT')))
    binding=dict(instrument_id='binance_usdm:futures:ETHUSDT',capability=old_cap)
    j.add_trade(Position(id='old',symbol='ETH/USDT',side=Side.LONG,amount=10,entry_price=100,
        notional_usdt=1000,stop_loss=95,market_type='futures',exec_mode='live',entry_identity={'execution_binding':binding}))
    ex.positions=[dict(symbol='ETH/USDT:USDT',side='long',contracts=10,entryPrice=100,markPrice=100,info={'symbol':'ETHUSDT'})]
    now=int(time.time()*1000)
    observation=observe_positions(ex.positions,exchange_id=ex.id,market_type=e.market_type,
        environment='demo',source_ref='https://demo-fapi.binance.com',request_start_ms=now,response_received_ms=now)
    record_snapshot(j,position_snapshot(observation,j.open_trades(),account_scope=cap['account_scope']),at_ms=now)
    market=ex.market
    ex.market=lambda s:dict(market(s),id='ETHUSDT',base='ETH') if s.startswith('ETH/') else market(s)
    params=permission(e,d)
    original_hold=e.risk_manager._durable_hold;calls=[0]
    original_order=ex.create_order
    def provider(symbol,kind,side,amount,params=None):
        if params and params.get('reduceOnly'):
            ex.positions[0]['contracts']-=amount
        return original_order(symbol,kind,side,amount,params)
    ex.create_order=provider
    @contextmanager
    def held():
        calls[0]+=1
        if calls[0]==appears_before_hold:
            ex.close_error=RequestTimeout('partial executed then response lost')
            assert e.close_partial(dict(j.query("SELECT * FROM trades WHERE id='old'")[0]),5) is False
        with original_hold() as db:yield db
    e.risk_manager._durable_hold=held
    assert e.open(d,2,2,95,110,'strategy','strategy',**params) is None
    assert d.skip_reason=='execution_recovery_pending'
    assert len(ex.sent)==1 and ex.sent[0][4]['reduceOnly'] is True
    assert ex.positions[0]['contracts']==5
    assert j.query("SELECT * FROM partial_exit_intents WHERE state!='CONSUMED'")
    assert not j.query('SELECT * FROM trades WHERE decision_id=?',(d.id,))
    assert e.recovery.pending() is None
    if appears_before_hold==2:
        refused=j.query('SELECT * FROM execution_requests WHERE decision_id=?',(d.id,))[0]
        assert refused['state']=='REFUSED'
        assert json.loads(refused['result_json'])=={'reason':'execution_recovery_pending','submitted':False}
