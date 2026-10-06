"""Read-only attention API and authentication."""
import json

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    from trader.dashboard import server
    from fastapi import APIRouter
    monkeypatch.setattr(server,'ROOT',tmp_path)
    monkeypatch.setattr(server,'make_graphql_router',lambda *_a:APIRouter())
    monkeypatch.setenv('DASH_TOKEN','fixture-token')
    monkeypatch.setattr(server,'_account_snapshot',lambda:pytest.fail('venue lookup'))
    return server


def test_api_disabled_auth_and_missing_store(server, tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':False}}))
    assert client.get('/api/attention/latest').status_code==401
    r=client.get('/api/attention/latest?token=fixture-token')
    assert r.json()['status']=='disabled' and r.headers['cache-control'].startswith('no-store')
    assert not (tmp_path/'data/attention.db').exists()
    client=TestClient(server.create_app({'attention':{'enabled':True}}))
    assert client.get('/api/attention/latest?token=fixture-token').json()['status']=='waiting'


def test_api_bad_config_and_error_health(server, tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':True,'max_symbols':-1}}))
    assert client.get('/api/attention/latest?token=fixture-token').json()['status']=='configuration_error'
    client=TestClient(server.create_app({'attention':{'enabled':True}}))
    (tmp_path/'data/attention_health.json').write_text(json.dumps({'errors':1,'last_error':'worker_timeout'}))
    assert client.get('/api/attention/latest?token=fixture-token').json()['status']=='error'


def test_learning_health_is_read_only_and_visible_when_enabled(server, tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':True},
                                      'attention_learning':{'enabled':True}}))
    data=client.get('/api/attention/latest?token=fixture-token').json()
    assert data['learning']['status']=='unavailable'
    assert not (tmp_path/'data/attention_learning.db').exists()


def test_recovery_visibility_is_authenticated_and_read_only(server, tmp_path):
    from trader.core.journal import Journal
    from trader.engine.recovery import KEY
    client = TestClient(server.create_app({'attention': {'enabled': True}}))
    journal = Journal(tmp_path/'data/luffy.db')
    raw = json.dumps({'id': 'intent', 'symbol': 'BTC/USDT', 'phase': 'entry',
                      'reason': 'entry_fill_unconfirmed', 'position': {'internal': 'omitted'}})
    journal.kv_set(KEY, raw)
    assert client.get('/api/attention/latest').status_code == 401
    result = client.get('/api/attention/latest?token=fixture-token').json()['execution_recovery']
    assert result['reason'] == 'entry_fill_unconfirmed'
    assert 'position' not in result
    assert journal.kv_get(KEY) == raw
    journal.kv_set(KEY, '{}')
    result = client.get('/api/attention/latest?token=fixture-token').json()['execution_recovery']
    assert result['reason'] == 'recovery_ledger_unreadable'


def test_admission_counts_independent_of_investigation_worker(server,tmp_path,monkeypatch):
    from trader import attention_admission as A
    from trader.core.journal import Journal
    from tests.test_hierarchical_admission import observation,CUT
    j=Journal(tmp_path/'data/luffy.db')
    receipt=A.persist(j,observation(),A.AdmissionPolicy(12,2,2,0))
    before=j.query('SELECT key,value FROM state_kv ORDER BY key')
    monkeypatch.setattr(server.time,'time',lambda:CUT/1000)
    client=TestClient(server.create_app({'attention':{'enabled':True}}))
    got=client.get('/api/attention/latest?token=fixture-token').json()
    assert got['status']=='waiting'
    assert got['admission']['status']=='current'
    assert got['admission']['broad_observed']==30
    assert got['admission']['deep_admitted']==12
    assert got['admission']['peer_cohort']==30
    assert got['admission']['exposure_required']==1
    assert got['admission']['receipt_id']==receipt['receipt_id']
    assert j.query('SELECT key,value FROM state_kv ORDER BY key')==before
