import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests.test_attention_view import server
from trader.dashboard.auth import DashboardAuth


@pytest.mark.parametrize('token', [None, '', 'luffy'])
def test_missing_default_tokens_fail_closed(server, monkeypatch, token):
    if token is None: monkeypatch.delenv('DASH_TOKEN', raising=False)
    else: monkeypatch.setenv('DASH_TOKEN', token)
    client = TestClient(server.create_app({'attention': {'enabled': True}}))
    for path in ('/', '/graphql', '/api/investigations/latest', '/docs'):
        assert client.get(path, headers={'x-luffy-token': 'luffy'}).status_code == 401


def test_browser_session_all_routes_csrf_logout(server):
    c = TestClient(server.create_app({'attention': {'enabled': True}}))
    assert c.get('/').status_code == 401
    origin = {'origin': 'http://testserver'}
    assert c.post('/graphql', json={}, headers=origin).status_code == 401
    assert c.post('/auth/login', json={'password': 'fixture-token'}).status_code == 403
    assert c.post('/auth/login', json={'password': 'wrong'}, headers=origin).status_code == 401
    r = c.post('/auth/login', json={'password': 'fixture-token'}, headers=origin)
    assert r.status_code == 200
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert 'fixture-token' not in r.headers['set-cookie']
    assert c.get('/').status_code == 200
    assert c.get('/api/investigations/latest').status_code == 200
    assert c.get('/investigation.js').status_code == 200
    assert c.post('/graphql', json={}, headers={'origin':'http://evil'}).status_code == 403
    with c.websocket_connect('/ws/live', headers=origin) as ws:
        assert 'open_trades' in ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect('/ws/live', headers={'origin': 'http://evil'}): pass
    assert c.post('/auth/logout', headers=origin).status_code == 200
    assert c.get('/api/investigations/latest').status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect('/ws/live', headers=origin): pass


def test_expired_forged_rotated_session(monkeypatch):
    a = DashboardAuth('test-secret')
    monkeypatch.setattr('trader.dashboard.auth.time.time', lambda: 100000)
    cookie = a.session()
    scope = {'headers':[(b'cookie', ('luffy_session='+cookie).encode())]}
    assert a.authenticated(scope)
    assert not DashboardAuth('new-secret').authenticated(scope)
    scope['headers'] = [(b'cookie', ('luffy_session='+cookie+'x').encode())]
    assert not a.authenticated(scope)
    scope['headers'] = [(b'cookie', ('luffy_session='+cookie).encode())]
    monkeypatch.setattr('trader.dashboard.auth.time.time', lambda: 200000)
    assert not a.authenticated(scope)
