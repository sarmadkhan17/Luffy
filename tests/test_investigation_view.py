import json
from fastapi.testclient import TestClient
from tests.test_attention_view import server
from trader.observability import investigation as C


def test_authenticated_missing_readonly_endpoint(server,tmp_path):
    client=TestClient(server.create_app({'attention':{'enabled':True}}))
    assert client.get('/api/investigations/latest').status_code==401
    r=client.get('/api/investigations/latest?token=fixture-token')
    assert r.json()['status']=='unavailable'
    assert r.headers['cache-control'].startswith('no-store')
    assert not (tmp_path/'data/investigation.db').exists()


def test_health_stale(tmp_path):
    path=tmp_path/'investigation.db'
    C.ledger(path).close()
    path.with_name('investigation_health.json').write_text(json.dumps({'status':'ok','updated_ms':1}))
    assert C.owner_view(path,700000)['status']=='stale'
