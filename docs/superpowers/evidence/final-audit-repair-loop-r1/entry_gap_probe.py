"""Offline normal entry caller with a partial intent appearing between durable holds."""
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager
import json,pytest,time
from ccxt import RequestTimeout
from tests import stage1_pit_offline_plugin as guard
guard.pytest_sessionstart(None)
from tests.test_entry_recovery import setup
from tests.entry_authority_fixtures import permission
from trader.core.types import Position,Side
from trader.engine.evidence_capture import position_snapshot,record_snapshot
from trader.observability.portfolio_observation import observe_positions
with TemporaryDirectory(dir=str(Path(__file__).parent)) as name,pytest.MonkeyPatch.context() as patch:
    ex,j,e,d=setup.__wrapped__(Path(name),patch)
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
    previous_market=ex.market
    ex.market=lambda s:dict(previous_market(s),id='ETHUSDT',base='ETH') if s.startswith('ETH/') else previous_market(s)
    params=permission(e,d)
    previous_hold=e.risk_manager._durable_hold
    calls=[0]
    @contextmanager
    def held():
        calls[0]+=1
        if calls[0]==2:
            ex.close_error=RequestTimeout('partial executed then response lost')
            assert e.close_partial(dict(j.query("SELECT * FROM trades WHERE id='old'")[0]),5) is False
        with previous_hold() as db:yield db
    e.risk_manager._durable_hold=held
    result=e.open(d,2,2,95,110,'strategy','strategy',**params)
    print(json.dumps({'probe':'partial intent appears between durable entry reservation and submission','returned_position':bool(result),'new_exposure_orders':sum(not (x[4] or {}).get('reduceOnly') and x[1]=='market' for x in ex.sent),'partial_pending':bool(j.query("SELECT * FROM partial_exit_intents WHERE state!='CONSUMED'")),'skip_reason':d.skip_reason}))
assert not guard._attempts and not guard._store_attempts
print('OFFLINE GUARD: 0 network/store attempts')
