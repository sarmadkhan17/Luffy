"""Synthetic COIN-M inventory; no order authority or invented USDT equity."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from trader.core.types import MarketType
from trader.core.journal import Journal
from trader.observability.portfolio_observation import observe_positions
from trader.engine.evidence_capture import position_snapshot,record_snapshot,snapshot_history,SNAPSHOT_KV,snapshot_storage_key


def row(*,symbol='BTCUSD_PERP',unified='BTC/USD:BTC',quantity=2.,side='long'):
    return dict(symbol=unified,contracts=abs(quantity),side=side,contractSize=100.,hedged=False,
        info=dict(symbol=symbol,positionSide='BOTH',positionAmt=str(quantity),
                  entryPrice='50000.00',markPrice='51000.00'))


def observe(rows,*,coin=True,received=1000):
    return observe_positions(rows,exchange_id='binancecoinm' if coin else 'binanceusdm',
        market_type=MarketType.FUTURES,environment='demo',
        source_ref='https://demo-dapi.binance.com' if coin else 'https://demo-fapi.binance.com',
        request_start_ms=900,response_received_ms=received)


@pytest.mark.parametrize('quantity,side',[(2.,'long'),(-3.,'short'),(0.,None)])
def test_native_position_retains_contract_quantity_and_exact_instrument(quantity,side):
    obs=observe([row(quantity=quantity,side=side)])
    assert obs.venue=='binancecoinm' and obs.source=='ccxt.binancecoinm.fetch_positions'
    p=obs.positions[0]
    assert p.instrument_id.value=='binancecoinm:futures:BTCUSD_PERP'
    assert p.absolute_quantity==abs(quantity) and p.side==side
    assert p.position_present==bool(quantity)


def test_delivery_and_perpetual_are_distinct_in_full_account_inventory():
    rows=[row(),row(symbol='BTCUSD_261225',unified='BTC/USD:BTC-261225',quantity=-1.,side='short')]
    obs=observe(rows)
    assert len(obs.positions)==2
    assert len({p.instrument_id for p in obs.positions})==2


@pytest.mark.parametrize('mutation',[
    {'symbol':'BTC/USDT:USDT'}, {'contracts':.1}, {'contracts':True}, {'contracts':float('nan')},
    {'side':'short'}, {'hedged':True}, {'contractSize':None}, {'contractSize':0.},
    {'contractSize':float('inf')}, {'contractSize':True},
    {'info':dict(symbol='BTCUSD_PERP',positionSide='LONG',positionAmt='2')},
    {'info':dict(symbol='BTCUSD_PERP',positionSide='BOTH',positionAmt='-2')},
    {'info':dict(symbol='BTCUSD_PERP',positionSide='BOTH',positionAmt='NaN')},
    {'info':dict(symbol='BTCUSD_261332',positionSide='BOTH',positionAmt='2')},
    {'contracts':2**53+1,'info':dict(symbol='BTCUSD_PERP',positionSide='BOTH',positionAmt=str(2**53+1))},
])
def test_unproven_native_identity_or_quantity_cannot_establish_inventory(mutation):
    r=row();r.update(mutation)
    with pytest.raises(ValueError):observe([r])


def test_duplicate_native_rows_and_mixed_sources_refuse():
    with pytest.raises(ValueError,match='duplicate'):observe([row(),row()])
    with pytest.raises(ValueError,match='REST venue'):
        observe_positions([row()],exchange_id='binancecoinm',market_type=MarketType.FUTURES,
            environment='demo',source_ref='https://demo-fapi.binance.com',
            request_start_ms=900,response_received_ms=1000)


def test_native_snapshot_binds_rows_prices_face_value_and_account():
    rows=[row()];obs=observe(rows)
    snap=position_snapshot(obs,rows,account_scope=('execution-account.v1:'+'a'*64))
    p=snap['positions'][0]
    assert p['entry_price_text']=='50000.00' and p['mark_price_text']=='51000.00'
    assert p['quantity']==2. and p['quantity_unit']=='contracts'
    assert p['contract_size_text']=='100.0' and p['settlement_asset']=='BTC'
    assert p['contract_kind']=='inverse'
    changed=deepcopy(rows);changed[0]['contracts']=3.;changed[0]['info']['positionAmt']='3'
    with pytest.raises(ValueError,match='rows differ'):position_snapshot(obs,changed,account_scope=('execution-account.v1:'+'a'*64))
    with pytest.raises(ValueError,match='account scope'):position_snapshot(obs,rows)


def test_native_inventory_cannot_replace_usdm_or_other_native_account_view(tmp_path):
    journal=Journal(tmp_path/'journal.db')
    usd=position_snapshot(observe([],coin=False),[])
    first=position_snapshot(observe([]),[],account_scope=('execution-account.v1:'+'a'*64))
    second=position_snapshot(observe([]),[],account_scope=('execution-account.v1:'+'b'*64))
    for snap in (usd,first,second):assert record_snapshot(journal,snap,at_ms=1100)=='inserted'
    assert json.loads(journal.kv_get(SNAPSHOT_KV))==usd
    assert json.loads(journal.kv_get(snapshot_storage_key(first)))==first
    assert json.loads(journal.kv_get(snapshot_storage_key(second)))==second
    assert len(snapshot_history(journal))==3
    # Interleaved other accounts cannot create duplicate unchanged-book history.
    later=position_snapshot(observe([],received=1200),[],account_scope=('execution-account.v1:'+'a'*64))
    assert record_snapshot(journal,later,at_ms=1200)=='unchanged'
    assert record_snapshot(journal,usd,at_ms=1200)=='unchanged'
    assert len(snapshot_history(journal))==3


def test_existing_unwind_reader_uses_native_inventory_and_real_account_scope_format(monkeypatch):
    from trader.engine import coordinated_unwind as U
    from trader.data.feed import execution_account_scope
    calls=[]
    def fetch():calls.append('read');return [row(quantity=-2.,side='short')]
    venue=SimpleNamespace(id='binancecoinm',apiKey='SYNTHETIC_NOT_A_CREDENTIAL',fetch_positions=fetch,
        urls={'api':{'dapiPrivate':'https://demo-dapi.binance.com/dapi/v1'}})
    monkeypatch.setattr(U.time,'time',lambda:1.)
    obs=U._position_observation(SimpleNamespace(ex=venue))
    snap=position_snapshot(obs,fetch(),account_scope=execution_account_scope(venue))
    assert snap['positions'][0]['quantity']==2.
    assert snap['positions'][0]['side']=='short'
    assert snapshot_storage_key(snap).startswith(SNAPSHOT_KV+':binancecoinm:execution-account.v1:')
    assert calls==['read','read']


def test_native_contracts_cannot_enter_linear_exposure_arithmetic():
    from trader.portfolio.allocator import Source
    from trader.portfolio.common_factor import snapshot
    rows=[row()]
    native=position_snapshot(observe(rows),rows,account_scope=('execution-account.v1:'+'a'*64))
    with pytest.raises(ValueError,match='VENUE_CONTRACT_VALUATION_UNAVAILABLE'):
        snapshot(Source.freeze('venue_position_snapshot',native),1100)
