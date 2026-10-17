"""Glacier owner truth: offline reads, synthetic journals, no running services."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from trader.core.journal import Journal
from trader.core.types import MarketType
from trader.dashboard.glacier_truth import runtime_truth, filesystems, versioned_strategies
from trader.dashboard.owner_reads import diagnostics
from trader.engine import evidence_capture as E
from trader.observability.portfolio_observation import observe_positions
from tests.owner_frontend_fixture import make_app
from tests.test_trade_history import H
from tests.authority_factory_fixtures import cfg  # noqa: F401
from tests.test_str04_governor_lifecycle import world  # noqa: F401

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)

@pytest.mark.parametrize('environment,source', [('demo','https://demo-fapi.binance.com'), ('production','https://fapi.binance.com')])
def test_account_label_is_verified_and_expires(tmp_path, environment, source):
    j = Journal(tmp_path/'j.db')
    ms = int(NOW.timestamp()*1000)
    observation = observe_positions([], exchange_id="binanceusdm", market_type=MarketType.FUTURES, request_start_ms=ms-10, response_received_ms=ms,
                                    environment=environment, source_ref=source)
    snapshot = E.position_snapshot(observation, [])
    E.record_snapshot(j, snapshot, at_ms=ms)
    before = j.query('SELECT * FROM state_kv')
    truth = runtime_truth(j, {'brain': {'enabled': False}}, NOW)
    assert truth['account']['environment'] == environment
    assert truth['account']['snapshot_id'] == snapshot['snapshot_id']
    assert truth['brain']['provider_state'] == 'DISABLED'
    assert truth['brain']['response_mode'] == 'EXTERNAL_CHAT_UNAVAILABLE'
    late = datetime.fromtimestamp(NOW.timestamp()+61, timezone.utc)
    assert runtime_truth(j, {}, late)['account']['environment'] is None
    assert runtime_truth(j, {}, late)['account']['freshness'] == 'stale'
    assert j.query('SELECT * FROM state_kv') == before


def test_unavailable_never_implies_account_or_provider_health(tmp_path):
    j = Journal(tmp_path/'j.db')
    t = runtime_truth(j, {}, NOW)
    assert t['account']['venue'] is None and t['account']['environment'] is None
    assert t['brain']['provider_state'] == 'UNKNOWN'
    assert t['brain']['external_llm'] == 'UNKNOWN'
    assert versioned_strategies(j, {}, NOW.timestamp())['versions'] is None


def test_versioned_board_reuses_authority_and_never_writes(world):
    w = world
    w.act()
    before = w.j.query('SELECT canonical_json FROM strategy_governor_events')
    w.j._conn().execute('PRAGMA query_only=ON')
    out = versioned_strategies(w.j, w.cfg, NOW.timestamp())
    v = out['versions'][0]
    assert v['version']['version_id'] == w.vid
    assert v['version']['lineage_sha256']
    assert v['validation']['receipt_id'] and v['install']['install_id']
    assert v['probation']['status'] == 'SATISFIED'
    assert v['approval']['affected_object'] == w.vid
    assert v['governor']['to_state'] == 'ACTIVE'
    assert v['economics']['receipt_id'] == w.rid
    assert w.j.query('SELECT canonical_json FROM strategy_governor_events') == before


def test_multifilesystem_and_missing_growth_are_explicit(tmp_path, monkeypatch):
    from trader.dashboard import glacier_truth as G
    import shutil
    real_exists = Path.exists
    monkeypatch.setattr(Path, 'exists', lambda p: True if str(p) == '/mnt/luffy-recovery' else real_exists(p))
    real_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: '1 0 0:1 / / rw - ext4 root rw\n2 1 0:2 / /mnt/luffy-recovery rw - ext4 recovery rw\n' if str(p) == '/proc/self/mountinfo' else real_read(p, *a, **kw))
    monkeypatch.setattr(G.shutil, 'disk_usage', lambda p: shutil._ntuple_diskusage(100,90,10))
    fs = filesystems(tmp_path)
    assert [x['label'] for x in fs] == ['Root /','LUFFY data','Recovery']
    assert fs[2]['source'] == 'recovery' and all(x['percent_free'] == 10 for x in fs)
    j = Journal(tmp_path/'j.db')
    d = diagnostics(j, tmp_path)
    assert d['storage']['growth_baseline'] is None
    assert d['storage']['growth_status'] == 'Growth baseline unavailable'
    assert d['storage']['files'][0]['bytes'] is None


def test_browser_fixture_artifact_and_readonly_contract(tmp_path, monkeypatch):
    """Serialize isolated API fixtures in process; browser starts no Dashboard."""
    app,j,gateway = make_app(tmp_path, monkeypatch, startup_mode='READ_ONLY_GUI')
    from trader import runtime_identity
    monkeypatch.setattr(runtime_identity, 'legacy_kernel_pids', lambda: [])
    client = TestClient(app)
    paths = ['bootstrap','overview','desk','research/board','strategies/board','operations/log','diagnostics','knowledge','system','needs-you','trades','owner-interface']
    fixtures = {}
    for path in paths:
        response = client.get('/owner-api/v1/'+path, headers=H)
        assert response.status_code == 200, (path,response.text)
        fixtures['/owner-api/v1/'+path] = response.json()
    boot = fixtures['/owner-api/v1/bootstrap']
    assert boot['dashboard_mode'] == 'READ_ONLY_GUI' and boot['capabilities']['controls'] == []
    boot['runtime_truth'] = runtime_truth(j, {'brain': {'enabled': False}}, NOW)
    assert all(call['operation'] == 'health' for call in gateway.calls)
    log = fixtures['/owner-api/v1/operations/log']
    assert log['window_start'] and log['window_end'] and log['truncated'] is False
    artifact = tmp_path / 'glacier-browser-fixtures.json'
    artifact.write_text(json.dumps(fixtures, allow_nan=False))
    assert json.loads(artifact.read_text()) == fixtures
