"""GUI-01 offline figure and stopped-kernel proof; no venue calls."""
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from tests.owner_frontend_fixture import make_app, TOKEN
from trader.dashboard.owner_api import read_realized_today


def test_partial_realized_is_unknown_and_reports_coverage(tmp_path, monkeypatch):
    _, journal, _ = make_app(tmp_path, monkeypatch)
    with journal._tx() as c:
        c.execute("UPDATE trades SET realized_pnl=NULL WHERE id=(SELECT id FROM trades WHERE status='closed' LIMIT 1)")
    value, err = read_realized_today(journal, datetime.now(timezone.utc))
    assert err is None
    assert value['value'] is None
    assert value['quality'] == 'PARTIAL_UNKNOWN'
    assert not value['coverage']['complete']
    assert value['coverage']['known'] < value['coverage']['total']
    assert value['source'] and value['observed_at'] and value['version']


def test_empty_day_is_measured_zero_not_unknown(tmp_path, monkeypatch):
    _, journal, _ = make_app(tmp_path, monkeypatch)
    with journal._tx() as c:
        c.execute("DELETE FROM trades WHERE status='closed'")
    value, _ = read_realized_today(journal, datetime.now(timezone.utc))
    assert value['value'] == 0
    assert value['coverage']['complete'] and value['closed_trades'] == 0


def test_overview_reads_with_stopped_kernel(tmp_path, monkeypatch):
    app, journal, _ = make_app(tmp_path, monkeypatch, startup_mode="READ_ONLY_GUI")
    (tmp_path / 'data/kernel.lock').touch()
    with patch('trader.runtime_identity.legacy_kernel_pids', return_value=[]):
        result = TestClient(app).get('/owner-api/v1/overview', headers={'x-luffy-token': TOKEN})
    assert result.status_code == 200
    d = result.json()
    assert d['kernel_process']['state'] == 'STOPPED'
    assert d['control']['state'] == 'FROZEN'
    assert d['exposure']['coverage']['complete']
    assert d['exposure']['account_observed_at'] == d['account']['observed_at']
    assert d['realized_today']['value'] == 2

    client = TestClient(app)
    assert client.get('/owner-api/v1/bootstrap', headers={'x-luffy-token': TOKEN}).json()['dashboard_mode'] == 'READ_ONLY_GUI'
    assert client.get('/owner-api/v1/enrichment/marks', headers={'x-luffy-token': TOKEN}).json()['marks'] == {}
    assert client.post('/owner-api/v1/chat', headers={'x-luffy-token': TOKEN}, json={'message': 'hello'}).status_code == 403
