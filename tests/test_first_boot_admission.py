"""OBS-05 no-receipt correction: fake signed HTTP, no live venue/services."""
import json
import sqlite3
from urllib.parse import urlsplit, parse_qs

import pytest
import requests

from trader.observability import first_boot as F, preflight as P, bootstrap as B
from trader.observability.safety import SafetyHealth
from trader.engine import venue_reads as V
from tests.test_launch_preflight import local, NOW
from tests.test_bootstrap_contract import phase_a, protection, phase_b
from tests.test_dashboard_readiness import ready, CFG
from tests.test_venue_reads import Env


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a:pytest.fail('real network forbidden'))
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('real network forbidden'))


def empty_receipt(local, *, absent_tables=False):
    with sqlite3.connect(local[0]/'data/luffy.db') as db:
        db.execute('DROP TABLE protection_evidence' if absent_tables else 'DELETE FROM protection_evidence')


class InventoryHTTP:
    def __init__(self):
        self.positions=[];self.orders=[];self.algos=[];self.sent=[];self.on_read=None
    def __call__(self,request):
        path=urlsplit(request.url).path
        self.sent.append((request.method,path,dict(request.headers),request.url))
        if self.on_read: self.on_read(len(self.sent))
        body={'/fapi/v3/positionRisk':self.positions,'/fapi/v1/openOrders':self.orders,
              '/fapi/v1/openAlgoOrders':self.algos}[path]
        result=requests.Response();result.status_code=200
        result._content=json.dumps(body).encode();result.headers['Content-Type']='application/json'
        result.headers[V.USED_WEIGHT_HEADER]='50';result.url=request.url
        return result


@pytest.fixture
def venue(local,monkeypatch):
    from trader.core import config
    transport=InventoryHTTP(); original=V.guarded_client
    # Existing fixture read credentials are distinct from its trading key.
    monkeypatch.setattr(config,'Env',Env(read_key=True))
    def guarded(**kwargs):
        client, guard = original(**kwargs, inner=transport)
        client.enableRateLimit = False  # fake HTTP only; production rate policy unchanged
        return client, guard
    monkeypatch.setattr(V,'guarded_client',guarded)
    return transport


def test_valid_persisted_receipt_preserves_existing_path_and_never_reads_venue(local,monkeypatch):
    monkeypatch.setattr(F,'read_zero_account',lambda **k:pytest.fail('valid receipt must not trigger venue reads'))
    result=phase_a(local,monkeypatch)
    assert result['allow'] and result['facts']['protection']['evidence_status']=='VERIFIED'


@pytest.mark.parametrize('absent_tables',[False,True])
def test_no_receipt_fresh_signed_global_zero_account_passes_contained_only(local,monkeypatch,venue,absent_tables):
    empty_receipt(local,absent_tables=absent_tables)
    before=(local[0]/'data/safety_health.json').read_bytes()
    result=phase_a(local,monkeypatch)
    assert result['allow'],result['reasons']
    fact=result['facts']['protection']
    assert fact['admission']=='FIRST_BOOT_ZERO_ACCOUNT' and fact['generation']=={'boot':0,'seq':0}
    assert fact['account_inventory']['requests']==6 and not fact['trading_authority']
    assert not result['trading_authority'] and (local[0]/'data/safety_health.json').read_bytes()==before
    assert len(venue.sent)==6 and all(method=='GET' for method,_,_,_ in venue.sent)
    for _,_,headers,url in venue.sent:
        assert headers['X-MBX-APIKEY']=='rk'  # distinct read credential, never tk
        query=parse_qs(urlsplit(url).query)
        assert 'signature' in query and 'symbol' not in query
    with sqlite3.connect(local[0]/'data/luffy.db') as db:
        if not absent_tables: assert db.execute('SELECT count(*) FROM protection_evidence').fetchone()[0]==0
    B.record_fence(local[0],'fixture','PENDING','a'*40)
    health=SafetyHealth(local[0]/'data/safety_health.json')
    assert health.entry_block()=='contained_bootstrap_not_ready' and health.read()['recovery_required']


@pytest.mark.parametrize('quantity',['1','-1','0.001','NaN','Infinity',None,True,'broken'])
def test_journal_zero_cannot_hide_any_venue_exposure_or_unknown_quantity(local,monkeypatch,venue,quantity):
    empty_receipt(local)
    venue.positions=[{'symbol':'SOLUSDT','positionSide':'BOTH','positionAmt':quantity}]
    with sqlite3.connect(local[0]/'data/luffy.db') as db: assert db.execute('SELECT count(*) FROM trades').fetchone()[0]==0
    assert not phase_a(local,monkeypatch)['allow']


@pytest.mark.parametrize('listing',['orders','algos'])
def test_any_ordinary_or_conditional_order_blocks_even_with_zero_positions(local,monkeypatch,venue,listing):
    empty_receipt(local);setattr(venue,listing,[{'orderId':1}])
    assert not phase_a(local,monkeypatch)['allow']


@pytest.mark.parametrize('bad',['positions_null','positions_unknown','missing_amount','missing_side','duplicate',
    'ordinary_unknown','algo_unknown','algo_paginated','algo_total_unknown','algo_unknown_status'])
def test_unknown_or_malformed_account_truth_blocks(local,monkeypatch,venue,bad):
    empty_receipt(local)
    if bad=='positions_null':venue.positions=None
    if bad=='positions_unknown':venue.positions={'status':'UNKNOWN'}
    if bad=='missing_amount':venue.positions=[{'symbol':'SOLUSDT','positionSide':'BOTH'}]
    if bad=='missing_side':venue.positions=[{'symbol':'SOLUSDT','positionAmt':'0'}]
    if bad=='duplicate':venue.positions=[{'symbol':'SOLUSDT','positionSide':'BOTH','positionAmt':'0'}]*2
    if bad=='ordinary_unknown':venue.orders={'orders':[]}
    if bad=='algo_unknown':venue.algos={'status':'UNKNOWN'}
    if bad=='algo_unknown_status':venue.algos={'orders':[],'status':'UNKNOWN'}
    if bad=='algo_paginated':venue.algos={'orders':[],'hasMore':True}
    if bad=='algo_total_unknown':venue.algos={'orders':[],'total':True}
    assert not phase_a(local,monkeypatch)['allow']


def test_zero_rows_are_explicitly_valid_and_algo_wrapper_complete(local,monkeypatch,venue):
    empty_receipt(local)
    venue.positions=[{'symbol':'SOLUSDT','positionSide':'BOTH','positionAmt':'0.000'}]
    venue.algos={'orders':[],'total':0,'hasMore':False}
    assert phase_a(local,monkeypatch)['allow']


@pytest.mark.parametrize('exception',[TimeoutError,OSError,ValueError])
def test_venue_unavailable_or_unreadable_blocks_without_fallback(local,monkeypatch,venue,exception):
    empty_receipt(local)
    venue.on_read=lambda n:(_ for _ in ()).throw(exception())
    assert not phase_a(local,monkeypatch)['allow']


@pytest.mark.parametrize('case',['stale','future','unknown_status','unknown_exposure','wrong_host',
    'wrong_environment','wrong_credential_scope','missing_clock','bool_clock','missing_listing','nonzero_orders','budget'])
def test_stale_or_unbound_snapshot_never_authorizes_first_boot(local,monkeypatch,venue,case):
    empty_receipt(local)
    valid=F.read_zero_account(clock=lambda:NOW-1)
    if case=='stale':valid['checked_at']=NOW-121
    if case=='future':valid['completed_at']=NOW+1
    if case=='unknown_status':valid['status']='UNKNOWN'
    if case=='unknown_exposure':valid['unknown_exposure']=None
    if case=='wrong_host':valid['venue_host']='fapi.binance.com'
    if case=='wrong_environment':valid['environment']='LIVE'
    if case=='wrong_credential_scope':valid['account_scope']='trading_key'
    if case=='missing_clock':valid.pop('checked_at')
    if case=='bool_clock':valid['checked_at']=True
    if case=='missing_listing':valid.pop('complete_listing')
    if case=='nonzero_orders':valid['ordinary_order_count']=1
    if case=='budget':valid['read_elapsed_seconds']=21
    monkeypatch.setattr(F,'read_zero_account',lambda **k:valid)
    assert not phase_a(local,monkeypatch)['allow']


@pytest.mark.parametrize('pending',['entry','partial','rearm','journal_position','journal_unknown','journal_bad_status','hold'])
def test_pending_execution_or_local_unknown_blocks_before_any_venue_read(local,monkeypatch,venue,pending):
    empty_receipt(local)
    with sqlite3.connect(local[0]/'data/luffy.db') as db:
        if pending=='entry':db.execute("INSERT INTO execution_requests VALUES ('SUBMITTED')")
        if pending=='partial':db.execute("INSERT INTO partial_exit_intents VALUES ('PENDING')")
        if pending=='rearm':db.execute("INSERT INTO state_kv VALUES ('reconcile_rearm_submitted','{\"SOLUSDT\":{}}')")
        if pending=='journal_position':db.execute("INSERT INTO trades VALUES ('t','SOL/USDT','long',1,10,'s','open')")
        if pending=='journal_bad_status':db.execute("INSERT INTO trades VALUES ('t','SOL/USDT','long',1,10,'s','UNKNOWN')")
        if pending=='journal_unknown':db.execute("INSERT INTO trades VALUES ('t','SOL/USDT','long',1,10,'s',NULL)")
        if pending=='hold':db.execute("UPDATE state_kv SET value='0' WHERE key='macro_guard_operator_hold'")
    assert not phase_a(local,monkeypatch)['allow'] and venue.sent==[]


def test_invalid_existing_receipt_never_switches_to_zero_account_path(local,monkeypatch,venue):
    with sqlite3.connect(local[0]/'data/luffy.db') as db: db.execute("UPDATE protection_evidence SET value='{'")
    assert not phase_a(local,monkeypatch)['allow'] and venue.sent==[]


def test_prior_attempt_without_publication_uses_generation_floor_not_fictitious_receipt(local,monkeypatch,venue):
    empty_receipt(local)
    with sqlite3.connect(local[0]/'data/luffy.db') as db:
        db.execute('CREATE TABLE protection_boots(boot INTEGER)');db.execute('INSERT INTO protection_boots VALUES (3)')
    result=phase_a(local,monkeypatch)
    assert result['allow'] and result['facts']['protection']['generation']['boot']==3
    assert result['facts']['protection']['evidence_status']=='AUTHORITATIVE_ZERO_EXPOSURE'


def test_local_execution_change_during_venue_inventory_refuses(local,monkeypatch,venue):
    empty_receipt(local)
    def changed(n):
        if n==3:
            with sqlite3.connect(local[0]/'data/luffy.db') as db:db.execute("INSERT INTO execution_requests VALUES ('SUBMITTED')")
    venue.on_read=changed
    assert not phase_a(local,monkeypatch)['allow']


def test_reader_deadline_blocks_additional_calls(local,monkeypatch,venue):
    times=iter([0.,0.,21.])
    with pytest.raises(TimeoutError): F.read_zero_account(clock=lambda:NOW,monotonic=lambda:next(times))
    assert len(venue.sent)==1


def test_first_boot_does_not_relax_phase_b(ready):
    baseline={'generation':{'boot':0,'seq':0}}
    result=B.readiness(ready.root,CFG,ready.instance.record,baseline,clock=lambda:ready.now)
    assert not result['allow']  # even fresh bound heartbeat cannot replace protection
    protection(ready)
    result=B.readiness(ready.root,CFG,ready.instance.record,baseline,clock=lambda:ready.now)
    assert result['allow'] and not result['trading_authority']
    health=ready.health.read();health['conditions']['journal_write']={'status':'ACTIVE'}
    ready.health.path.write_text(json.dumps(health))
    assert not B.readiness(ready.root,CFG,ready.instance.record,baseline,clock=lambda:ready.now)['allow']


@pytest.mark.parametrize('key',['missing','trading'])
def test_first_boot_does_not_fall_back_to_trading_credentials(local,monkeypatch,venue,key):
    from trader.core import config
    empty_receipt(local)
    env=Env(read_key=key!='missing')
    if key=='trading':env.values.update(BINANCE_READ_API_KEY='tk',BINANCE_READ_SECRET_KEY='ts')
    monkeypatch.setattr(config,'Env',env)
    assert not phase_a(local,monkeypatch)['allow'] and venue.sent==[]


def test_empty_malformed_receipt_schema_is_not_first_boot(local,monkeypatch,venue):
    with sqlite3.connect(local[0]/'data/luffy.db') as db:
        db.execute('DROP TABLE protection_evidence');db.execute('CREATE TABLE protection_evidence(unknown TEXT)')
    assert not phase_a(local,monkeypatch)['allow'] and venue.sent==[]
