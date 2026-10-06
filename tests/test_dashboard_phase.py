"""Phase startup and authority boundaries; fixture stores only, no venue/provider calls."""
from pathlib import Path
from unittest.mock import Mock
import shutil
import pytest
from fastapi.testclient import TestClient
from trader.dashboard import phase, server

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('value', [None, '', 'read_only', 'UNKNOWN'])
def test_mode_must_be_explicit(tmp_path, value):
    assert not phase.startup_check(tmp_path, {'dashboard': {'startup_mode': value}})['allow']


def test_read_only_direct_start_without_kernel(tmp_path, monkeypatch):
    import uvicorn
    from trader.observability import dashboard_readiness
    cfg = {'dashboard': {'startup_mode': 'READ_ONLY_GUI', 'host': 'fixture', 'port': 1}}
    monkeypatch.setattr(dashboard_readiness, 'check', lambda *a: pytest.fail('live gate used'))
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setattr(server, 'load_config', lambda: cfg)
    app = object(); monkeypatch.setattr(server, 'create_app', lambda cfg: app)
    run = Mock(); monkeypatch.setattr(uvicorn, 'run', run)
    server.main()
    assert run.call_args.args == (app,)


def test_live_missing_kernel_still_refuses(tmp_path):
    r = phase.startup_check(tmp_path, {'dashboard': {'startup_mode': 'LIVE'}})
    assert not r['allow'] and 'kernel_identity_missing' in r['reasons']


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setenv('DASH_TOKEN', 'phase-test-token')
    target = tmp_path / 'docs/tracker'; target.mkdir(parents=True)
    shutil.copy(ROOT / 'docs/tracker/LUFFY_Product_Tracker_v1.yaml', target)
    shutil.copy(ROOT / 'NEXT.yaml', tmp_path)
    # If the boundary fails, these spies catch control/provider/LLM execution.
    gateway = Mock(side_effect=AssertionError('gateway used'))
    marks = Mock(side_effect=AssertionError('provider used'))
    monkeypatch.setattr(server, '_owner_gateway', lambda *a: gateway)
    monkeypatch.setattr(server, '_position_marks', marks)
    monkeypatch.setattr(server, '_position_quotes', marks)
    cfg = {'dashboard': {'startup_mode': 'READ_ONLY_GUI'}, 'owner_interface': {'enabled': True}}
    app = server.create_app(cfg)
    with TestClient(app, headers={'x-luffy-token': 'phase-test-token'}) as c:
        yield c, gateway, marks
    gateway.assert_not_called(); marks.assert_not_called()


def test_tracker_and_truth_available_without_kernel(client):
    c, _, _ = client
    b = c.get('/owner-api/v1/bootstrap').json()
    assert b['dashboard_mode'] == 'READ_ONLY_GUI'
    assert b['kernel_process']['state'] in ('STOPPED', 'UNKNOWN', 'UNAVAILABLE')
    assert b['capabilities']['controls'] == [] and b['capabilities']['chat'] == 'disabled'
    assert b['owner_interface']['configured'] is False
    for route in ['/api/tracker', '/owner-api/v1/tracker']:
        r = c.get(route); assert r.status_code == 200
        d = r.json(); assert d['selected']['id'] == 'OBS-05' and d['selected']['status'] == 'CLOSED'
        assert d['counts']['IN_PROGRESS'] == 0
    system = c.get('/owner-api/v1/system').json()
    kernel = next(n['telemetry'] for n in system['nodes'] if n['id'] == 'kernel')
    assert kernel['health'] == 'unknown'
    assert kernel['status_label'] in ('STOPPED', 'UNKNOWN', 'UNAVAILABLE')
    assert c.get('/owner-api/v1/enrichment/marks').json()['observed_at'] is None


@pytest.mark.parametrize('path', ['/graphql', '/api/chat', '/api/brain/autopsy',
                                  '/owner-api/v1/chat', '/owner-api/v1/needs-you/decision'])
def test_every_action_rejected_before_gateway_provider_or_handler(client, path):
    c, _, _ = client
    assert c.post(path, json={'query': 'mutation { panic }'}).status_code == 403


def test_legacy_provider_reads_and_websocket_rejected(client):
    from starlette.websockets import WebSocketDisconnect
    c, _, _ = client
    assert c.get('/api/summary').status_code == 403
    assert c.get('/graphql', params={'query': 'mutation { panic }'}).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect('/ws/live'):
            pass


def test_historical_graphql_queries_remain_available(client):
    c, _, _ = client
    assert c.post('/graphql', json={'query': '{ trades_total }'}).status_code == 200


def test_read_only_gateway_cannot_call_ipc(monkeypatch):
    from trader.owner.ipc import OwnerClient
    from trader.dashboard.auth import DashboardAuth
    monkeypatch.setattr(OwnerClient, 'call', lambda *a: pytest.fail('IPC control executed'))
    cfg = {'dashboard': {'startup_mode': 'READ_ONLY_GUI'}, 'owner_interface': {'ipc_dir': '/tmp/unused'}}
    gateway = server._owner_gateway(cfg, DashboardAuth('fixture-token'))
    result = gateway('panic', 'id', 1, None, {})
    assert result.status == 'UNAVAILABLE' and 'dashboard_read_only' in result.reasons
