"""OWN-04: temporary local evidence, no Kernel/provider/venue execution."""
import fcntl
import json
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from trader.dashboard import owner_api, owner_reads, owner_runtime
from trader import runtime_identity
from tests.owner_frontend_fixture import make_app, TOKEN

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def heartbeat(root, **changes):
    data = root / 'data'
    data.mkdir(exist_ok=True)
    t = NOW.timestamp()
    value = dict(schema='luffy-heartbeat.v1', producer='luffy', instance_id='fixture',
                 sequence=4, started_at=t-1000, timestamp=t,
                 context={'last_successful_cycle_at': t, 'state': 'ACTIVE'})
    value.update(changes)
    (data / 'heartbeat_luffy.json').write_text(json.dumps(value))
    return value


@pytest.fixture
def process(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_identity, 'legacy_kernel_pids', lambda: [])
    heartbeat(tmp_path)
    record = dict(schema=runtime_identity.SCHEMA, pid=os.getpid(),
                  start_ticks=runtime_identity.proc_start_ticks(os.getpid()),
                  instance_id='process-fixture', heartbeat_instance_id='fixture')
    (tmp_path / 'data/kernel_instance.json').write_text(json.dumps(record))
    with (tmp_path / 'data/kernel.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield tmp_path, lock, record


def test_bound_running_and_stopped_are_distinct_from_active_permission(process):
    root, lock, _ = process
    hb, err = owner_api.read_heartbeat(root, NOW)
    assert err is None and hb['runtime_state'] == hb['process_state'] == 'RUNNING'
    assert hb['reported_state'] == 'ACTIVE'
    fcntl.flock(lock, fcntl.LOCK_UN)
    hb, _ = owner_api.read_heartbeat(root, NOW)
    assert hb['runtime_state'] == 'STOPPED' and hb['freshness'] == 'fresh'
    assert hb['reported_state'] == 'ACTIVE'  # retained permission cannot grant liveness


@pytest.mark.parametrize('changes,expected', [
    ({'instance_id': 'previous-instance'}, 'UNKNOWN'),
    ({'context': {'last_successful_cycle_at': NOW.timestamp()-500}}, 'STALE'),
    ({'context': {}}, 'UNKNOWN'),
    ({'sequence': False}, 'UNKNOWN'),
    ({'timestamp': NOW.timestamp()+.01}, 'UNKNOWN'),
    ({'timestamp': float('nan')}, 'UNKNOWN'),
    ({'started_at': NOW.timestamp()+1}, 'UNKNOWN'),
])
def test_adverse_work_or_identity_never_proves_running(process, changes, expected):
    root, _, _ = process
    heartbeat(root, **changes)
    hb, _ = owner_api.read_heartbeat(root, NOW)
    assert hb['runtime_state'] == expected
    assert hb['runtime_state'] != 'ACTIVE'


def test_pid_reuse_and_missing_identity_are_unknown(process):
    root, _, record = process
    record['start_ticks'] += 1
    (root / 'data/kernel_instance.json').write_text(json.dumps(record))
    assert owner_runtime.kernel_process(root)['state'] == 'UNKNOWN'
    (root / 'data/kernel_instance.json').unlink()
    assert owner_runtime.kernel_process(root)['state'] == 'UNKNOWN'


def test_absent_identity_does_not_create_artifacts(tmp_path):
    assert owner_runtime.kernel_process(tmp_path)['state'] == 'UNKNOWN'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('stamp,expected', [
    (NOW.timestamp()*1000, 'fresh'),
    ((NOW.timestamp()-10000)*1000, 'stale'),
    ((NOW.timestamp()+.01)*1000, 'invalid'),
    (True, 'unavailable'), (None, 'unavailable'),
    (float('nan'), 'unavailable'), (float('inf'), 'unavailable'),
])
def test_collector_timestamp_is_not_runtime_truth(tmp_path, stamp, expected):
    p = tmp_path / 'health.json'
    p.write_text(json.dumps(dict(updated_ms=stamp, status='ACTIVE')))
    value = owner_reads._health_file(p, NOW.timestamp())
    assert value['freshness'] == expected
    assert value['status'] == 'ACTIVE'  # explicitly last reported, not observed daemon work


@pytest.mark.parametrize('status,label', [('waiting', 'WAITING'), ('failed', 'FAILED'),
                                         ('disabled', 'DISABLED'), ('stopped', 'STOPPED')])
def test_api_separates_report_config_control_and_work(tmp_path, monkeypatch, status, label):
    app, journal, _ = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(owner_api, '_now', lambda: NOW)
    journal.kv_set('control_state', 'ACTIVE')
    # No matching process observation; even a fresh ACTIVE heartbeat proves no running work.
    heartbeat(tmp_path)
    (tmp_path / 'data/attention_health.json').write_text(json.dumps(
        dict(updated_ms=NOW.timestamp()*1000, status=status)))
    before = journal.query('SELECT * FROM state_kv')
    files = {p: p.read_bytes() for p in (tmp_path/'data').iterdir() if p.is_file()}
    client = TestClient(app)
    overview = client.get('/owner-api/v1/overview', headers={'x-luffy-token': TOKEN}).json()
    assert overview['control']['state'] == 'ACTIVE'
    assert overview['control']['time_basis'] == 'read_time'
    assert overview['heartbeat']['runtime_state'] == 'UNKNOWN'
    system = client.get('/owner-api/v1/system', headers={'x-luffy-token': TOKEN}).json()
    nodes = {n['id']: n for n in system['nodes']}
    assert nodes['kernel']['telemetry']['health'] != 'active'
    assert nodes['attention']['telemetry']['status_label'] == label
    assert nodes['attention']['telemetry']['health'] == 'unknown'
    assert system['events'] == []
    assert all(e['kind'] == 'architecture' for e in system['edges'])
    assert journal.query('SELECT * FROM state_kv') == before
    assert all(p.read_bytes() == content for p, content in files.items())
    assert not (tmp_path/'data/kernel.lock').exists()
    declared = owner_api.system(journal, tmp_path, {'attention': {'enabled': False}})
    attention = next(n for n in declared['nodes'] if n['id'] == 'attention')
    assert attention['configured_enabled'] is False
    assert attention['telemetry']['status_label'] == label
