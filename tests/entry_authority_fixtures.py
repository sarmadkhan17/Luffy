"""Offline observations through production registry/Risk contracts; no bypass."""
import json
import time
from datetime import datetime, timezone
from trader.core.instrument_registry import (InstrumentId, InstrumentRecord, RegistrySnapshot,
    OrderConstraints, EligibilityBasis, EligibilityBasisKind, Eligibility, AccountTrading,
    AssetClass, Presence, Capability)
from trader.core.types import MarketType
from trader.engine.entry_authority import register_capability, observe_leverage
from trader.engine.risk import RiskManager


def bind(journal, exchange, cfg, symbol='BTC/USDT'):
    journal.kv_set('control_state', 'ACTIVE')  # explicit isolated-test control intent
    now = int(time.time()*1000)
    iid = InstrumentId('binance_usdm', MarketType.FUTURES, 'BTCUSDT')
    exchange.id = 'binanceusdm'
    exchange.apiKey = 'TEST-ONLY-key'
    exchange.urls = {'api': {'fapiPrivateV3':'https://demo-fapi.binance.com/fapi/v3'}}
    from trader.data.feed import execution_account_scope
    scope = execution_account_scope(exchange)
    record = InstrumentRecord(iid, AssetClass.UNKNOWN, 'PERPETUAL', 'BTC','USDT','USDT','1',None,
        Presence.PRESENT,'TRADING',None,
        OrderConstraints(price_tick='0.01', quantity_step='0.001',minimum_quantity='0.001',
                        minimum_notional='5',market_minimum_quantity='0.001',
                        market_maximum_quantity='1000',market_quantity_step='0.001'),
        Capability.PROVEN,Eligibility.ELIGIBLE,scope,Presence.PRESENT,Presence.PRESENT,
        Presence.PRESENT,now,'TEST-ONLY-fixture',eligibility_basis=EligibilityBasis(
            EligibilityBasisKind.ACCOUNT_SYMBOL_PERMISSION,iid,'TEST-ONLY-account-permission'))
    snapshot = RegistrySnapshot(now,scope,AccountTrading.ENABLED,(record,),'TEST-ONLY-fixture')
    leverage = cfg['risk']['leverage']
    cap = register_capability(journal,snapshot,iid.value,execution_symbol=symbol,
        venue_market=dict(id='BTCUSDT',base='BTC',quote='USDT',settle='USDT',linear=True,contract=True,contractSize=1),
        leverage_evidence=observe_leverage(instrument_id=iid.value,account_scope=scope,snapshot_id=snapshot.snapshot_id,
            symbol_config={'symbol':'BTCUSDT','leverage':leverage,'marginType':'CROSSED'},
            leverage_brackets={'symbol':'BTCUSDT','brackets':[{'notionalFloor':'0','notionalCap':'100000','initialLeverage':leverage}]},
            observed_at_ms=now,valid_until_ms=now+300000,source='TEST-ONLY-fixture'),valid_until_ms=now+300000)
    ts = datetime.now(timezone.utc).isoformat()
    journal.kv_set('account_observation',json.dumps(dict(status='FRESH',value=10000.,
        authoritative=True,basis='venue_account',observed_at=ts,attempted_at=ts,recorded_at=ts,
        successful_read_at=ts,successful_value=10000.,consecutive_failures=0,
        account_scope=scope,source='TEST-ONLY-fixture')))
    exchange.urls = {'api': {'fapiPrivateV3':'https://demo-fapi.binance.com/fapi/v3'}}
    exchange.id = 'binanceusdm'
    exchange.entry_account_scope = scope
    exchange.market = lambda s: dict(id='BTCUSDT',base='BTC',quote='USDT',settle='USDT',
                                   linear=True,contract=True,contractSize=1)
    from trader.observability.portfolio_observation import observe_positions
    from trader.engine.evidence_capture import position_snapshot, record_snapshot, margin_observation, record_margin
    observation = observe_positions([], exchange_id='binanceusdm', market_type=MarketType.FUTURES,
        environment='demo',source_ref='https://demo-fapi.binance.com',request_start_ms=now,response_received_ms=now)
    record_snapshot(journal, position_snapshot(observation, [], account_scope=scope), at_ms=now)
    margin = margin_observation(json.dumps(dict(totalMarginBalance='10000',availableBalance='10000')).encode(),
        request_url='https://demo-fapi.binance.com/fapi/v3/account',request_start_ms=now,received_ms=now,account_scope=scope)
    record_margin(journal, margin)
    return RiskManager(cfg,journal), cap


def permission(executor, decision, amount=2., atr=2., stop=95., target=110., sid='strategy'):
    reference=dict(price=100.)
    proof=executor.risk_manager.authorize_entry(decision,amount,atr,stop,target,sid,reference=reference)
    return dict(reference=reference,risk_permission=proof)
