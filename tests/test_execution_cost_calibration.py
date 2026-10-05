import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from trader.engine import paper_cost_evidence as C
from trader.observability import funding_events as F, execution_calibration as E, execution_shadow as S
from trader.observability import depth_evidence as D
from trader.data.registry_provider import VenueTarget
from tests.test_paper_cost_protocol import context


def funding(rows):
    return SimpleNamespace(status=200, body=json.dumps(rows).encode())


def receipt(rows, start=100, end=200):
    return F.lookup('BTCUSDT', start, end, fetch=lambda *a, **k: funding(rows), clock=lambda: 300)


def test_no_crossing_enters_protocol_and_keeps_costs_unknown(tmp_path):
    j,cfg,v,t,inst = context(tmp_path)
    b=C.binding({**t, 'symbol':'BTC/USDT', 'market_type':'futures', 'venue_environment':'production'},v,inst)
    r=F.lookup(
        'BTCUSDT',b['entry']['time_ms'],b['exit']['time_ms'],
        fetch=lambda *a, **k: funding([]),clock=lambda:b['exit']['time_ms']+1)
    source=F.no_crossing_source(b,r,venue_symbol='BTCUSDT',environment='production')
    assert F.validate_no_crossing(source)
    with j._tx() as db:
        C.freeze_source(db,source)
        out=C.build(db,b,{'funding':source['source_id']})
    assert out['dimensions']['funding']['status']=='NOT_APPLICABLE'
    assert out['dimensions']['commission']['status']=='UNAVAILABLE'
    assert out['dimensions']['slippage']['status']=='UNAVAILABLE'
    assert out['net_pnl'] is None
    for key,value in [('kind','slippage'),('method','STATIC_BOOK_QUOTE'),('interval_complete',False)]:
        bad=copy.deepcopy(source);bad[key]=value
        assert not F.validate_no_crossing(bad)
        with j._tx() as db, pytest.raises(ValueError):C.freeze_source(db,bad)


def test_exact_events_signed_rates_and_no_invented_basis():
    rows=[dict(symbol='BTCUSDT',fundingTime=t,fundingRate='-0.0001',markPrice='50000') for t in (100,150,200)]
    r=receipt(rows)
    assert [e['timestamp_ms'] for e in r['events']]==[100,150,200]
    assert [e['timestamp_ms'] for e in r['crossed_events']]==[150]
    assert [e['timestamp_ms'] for e in r['boundary_events']]==[100,200]
    assert all(e['position_notional_basis'] is None for e in r['events'])
    b=dict(instrument='BTC/USDT',market_type='futures',venue_environment='production',entry={'time_ms':100},exit={'time_ms':200})
    with pytest.raises(ValueError,match='BOUNDARY'):F.no_crossing_source(b,r,venue_symbol='BTCUSDT',environment='production')
    with pytest.raises(ValueError,match='BASIS'):F.no_crossing_source(b,receipt(rows[1:2]),venue_symbol='BTCUSDT',environment='production')


@pytest.mark.parametrize('row', [dict(symbol='BTCUSDT',fundingTime=99,fundingRate='0.1'),
    dict(symbol='OTHER',fundingTime=150,fundingRate='0.1'),
    dict(symbol='BTCUSDT',fundingTime=201,fundingRate='0.1')])
def test_wrong_timestamp_or_symbol_refused(row):
    with pytest.raises(ValueError):receipt([row])


def test_pagination_and_coverage():
    rows=[dict(symbol='BTCUSDT',fundingTime=t,fundingRate='0.1') for t in range(100,1100)]
    urls=[]
    def fetch(url, **k):
        urls.append(url)
        return funding(rows if len(urls)==1 else [])
    r=F.lookup('BTCUSDT',100,1200,fetch=fetch,clock=lambda:1300)
    assert len(r['events'])==1000 and 'startTime=1100' in urls[1]
    bad=copy.deepcopy(r);bad['pages'].pop()
    with pytest.raises(ValueError,match='coverage'):F.replay(bad)
    bad=copy.deepcopy(r);bad['pages'][0]['response_body']='[]'
    with pytest.raises(ValueError,match='hash'):F.replay(bad)
    with pytest.raises(ValueError,match='bound'):F.lookup('BTCUSDT',100,1200,fetch=lambda *a,**k:funding(rows),clock=lambda:1300,max_pages=1)


def book():
    body=json.dumps(dict(lastUpdateId=1,E=300,T=300,bids=[['99','2'],['98','3']],asks=[['100','1'],['102','3']])).encode()
    return D.observe(body,target=VenueTarget.production(),symbol='BTCUSDT',limit=5,request_start_ms=300,received_ms=301,max_age_ms=10)


@pytest.mark.parametrize('side,vwap,difference', [('buy','101','1'),('sell','99','0')])
def test_book_walk(side,vwap,difference):
    out=E.book_walk(book(),side,'2')
    assert out['covered'] and out['book_vwap']==vwap
    assert out['static_book_price_difference']==difference
    assert out['qualifications']==['NOT_REALIZED_SLIPPAGE','NOT_MARKET_IMPACT_PROOF']
    assert not E.book_walk(book(),side,100)['covered']
    assert E.book_walk(book(),side,100)['book_vwap'] is None
    with pytest.raises(ValueError):C.freeze_source(sqlite3.connect(':memory:'),out)


def query_db():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    from trader.engine.trade_provenance import TABLES
    db.executescript(TABLES)
    db.execute('create table trades(id text,entry_identity_json text)')
    ref=json.dumps(dict(price=100,basis='decision',observed_at=100,submitted_ms=110))
    db.execute("insert into trade_legs(id,trade_id,booking_id,kind,purpose,origin,symbol,side,venue_order_id,order_identity,requested_qty,booked_qty,reference_json,exit_attribution,source,recorded_ms,market_type) values(1,'t',1,'entry','entry','luffy_order','BTC/USDT','buy','o','VERIFIED',2,2,?,'EXACT_LUFFY_ORDER_LINK','live_booking',200,'futures')",(ref,))
    db.execute("insert into trade_fills(symbol,fill_key,venue_fill_id,venue_order_id,trade_id,leg_id,side,qty,price,commission,commission_asset,venue_ts_ms,observed_ms,attribution,attribution_reason,source,fill_json,market_type) values('BTC/USDT','f','f','o','t',1,'buy',2,101,'0.1','USDT',120,200,'ATTRIBUTED','EXACT','venue','{}','futures')")
    return db,lambda sql,params=():[dict(r) for r in db.execute(sql,params)]


def test_exact_dataset_remains_not_validated():
    db,q=query_db();data=E.dataset(q)
    assert data['matched_orders']==1 and data['slippage_model']=='NOT_VALIDATED'
    assert data['orders'][0]['submission_to_first_fill_ms']==10
    assert data['orders'][0]['static_book_prediction'] is None


@pytest.mark.parametrize('field,value',[('venue_order_id','other'),('symbol','ETH/USDT'),('trade_id','other'),('market_type','spot'),('side','sell'),('attribution','UNATTRIBUTED')])
def test_no_fuzzy_attribution(field,value):
    db,q=query_db();db.execute(f'update trade_fills set {field}=?',(value,))
    assert E.dataset(q)['matched_orders']==0


def test_empty_inventory():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    q=lambda sql,params=():[dict(r) for r in db.execute(sql,params)]
    assert E.inventory(q)['by_instrument']=={}
    assert E.dataset(q)['slippage_model']=='NOT_VALIDATED'


def test_bounded_event_capture_only_public_get():
    event=dict(decision_id='d',intent_id='i',symbol='BTCUSDT',side='buy',requested_quantity='2',phase='decision',event_ms=299,environment='production')
    calls=[]
    def fetch(url,**kwargs):
        calls.append(url)
        return SimpleNamespace(status=200,body=json.dumps(dict(lastUpdateId=1,E=300,T=300,bids=[['99','2']],asks=[['100','2']])).encode())
    out=S.capture(event,depth=5,max_age_ms=10,fetch=fetch,clock=lambda:301)
    assert out['event']==event and len(calls)==1
    assert calls[0].startswith('https://fapi.binance.com/fapi/v1/depth?')
    assert 'NOT_EXACT_EVENT_TIME_BOOK' in out['authority']
    assert out['event_to_request_ms']==2
    with pytest.raises(ValueError):S.capture({},depth=5,max_age_ms=10,fetch=fetch)


def test_environment_cannot_be_inferred_from_current_config():
    bound=dict(instrument='BTC/USDT',market_type='futures',entry={'time_ms':100},exit={'time_ms':200})
    for environment in (None,'demo'):
        bound['venue_environment']=environment
        with pytest.raises(ValueError,match='environment'):
            F.no_crossing_source(bound,receipt([]),venue_symbol='BTCUSDT',environment='production')


def test_inventory_does_not_write_source(tmp_path):
    from scripts.execution_cost_inventory import inspect
    path=tmp_path/'source.db'
    db=sqlite3.connect(path)
    from trader.engine.trade_provenance import TABLES
    db.executescript(TABLES)
    db.execute('CREATE TABLE trades(id text,entry_identity_json text)')
    db.execute('CREATE TABLE trade_accounting_bookings(id integer,trade_id text,payload text)')
    db.commit();db.close()
    before=path.read_bytes()
    out=inspect(path)
    assert path.read_bytes()==before
    assert out['persisted']['total_fill_rows']==0
    assert out['booking_receipt_replay']['inventory']['calibration']['matched_orders']==0
    assert out['slippage_model']=='NOT_VALIDATED' and not out['first_live']


def test_duplicate_scoped_order_refused():
    db,q=query_db()
    columns=[r[1] for r in db.execute('PRAGMA table_info(trade_legs)') if r[1]!='id']
    db.execute('INSERT INTO trade_legs('+','.join(columns)+') SELECT '+','.join(columns)+' FROM trade_legs')
    assert E.dataset(q)['matched_orders']==0
