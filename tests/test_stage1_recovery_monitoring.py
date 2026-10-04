"""MI-6/MI-7 offline acceptance, injected failures and executable mutants."""
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import socket
import sqlite3
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.engine.state import ControlStateMachine
from trader.engine.watchdog import Heartbeat
from trader.observability.safety import (SafetyHealth, SafetyObserver, HeartbeatPolicy,
    check_journal, entry_refusal, health_path)
from trader.persistence.backup import (Asset, Inventory, BackupSets, Refused,
    critical_inventory, STORES, canonical, digest)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    connect = sqlite3.connect
    def offline_connect(database, *args, **kwargs):
        from urllib.parse import unquote, urlparse
        name = str(database)
        if name != ':memory:':
            path = Path(unquote(urlparse(name).path) if name.startswith('file:') else name).resolve()
            assert path.is_relative_to(tmp_path.resolve()), 'production_store_attempt'
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', offline_connect)
    def no_network(*args, **kwargs):
        raise AssertionError('network_attempt')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'create_connection', no_network)


@pytest.fixture
def backup(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    for p in ('SDD.md', 'STATE.yaml', 'NEXT.yaml', 'config.yaml'):
        (root / p).write_text('test: version1\n')
    j = Journal(root / 'data/luffy.db')
    j._conn().execute('PRAGMA journal_mode=WAL')
    j.kv_set('control_state', 'ACTIVE')
    j.kv_set('execution_recovery', 'null')
    j.kv_set('replay_sentinel', 'exact-bytes-state')
    (root / '.env').write_text('API_SECRET=DO_NOT_CAPTURE')
    (root / 'credential.json').write_text('DO_NOT_CAPTURE')
    (root / 'data/ewa_state.json').write_text('{"agents":{},"processed":[],"updated":0}')
    inv = critical_inventory(root)
    sets = BackupSets(root, tmp_path / 'external-mount', inv,
                      repository_version='fixture-commit+config-hashes', clock=lambda: 1234.)
    return root, j, sets


def test_wal_snapshot_exact_restore_requires_recovery_restart_destination(backup, tmp_path):
    root, j, sets = backup
    # Data still in live WAL when online snapshot occurs.
    assert (root / 'data/luffy.db-wal').exists()
    j.kv_set('transaction_marker', 'committed-in-wal')
    generation = sets.capture()
    assert not (generation / 'data/luffy.db-wal').exists()
    reopen = BackupSets(root, sets.destination, sets.inventory, repository_version='fixture')
    assert generation in reopen.discover()
    m = reopen.verify(generation)
    assert m['capture_boundary'].endswith('NOT_GLOBAL_ATOMIC')
    assert 'physical_offhost_NOT_VERIFIED' in m['destination_claim']
    receipt = reopen.restore(generation, tmp_path / 'restored')
    restored = Journal(tmp_path / 'restored/data/luffy.db')
    assert restored.kv_get('transaction_marker') == 'committed-in-wal'
    assert restored.kv_get('replay_sentinel') == 'exact-bytes-state'
    assert ControlStateMachine(restored).state == ControlState.RECOVERY
    assert not ControlStateMachine(restored).can_enter()
    assert json.loads(restored.kv_get('supervisor_status'))['needs_owner']
    for asset in sets.inventory.assets:
        if asset.kind == 'file':
            assert (tmp_path / 'restored' / asset.path).read_bytes() == (root / asset.path).read_bytes()
    assert receipt['verification'] == 'PASS' and receipt['restore'] == 'PUBLISHED'
    assert receipt['backup_id'] == m['backup_id']
    assert receipt['manifest_hash'] == (generation / 'COMPLETE').read_text()
    assert list((sets.destination / 'restore-receipts').glob('*.json'))
    assert not (tmp_path / 'restored/.env').exists()
    assert not (tmp_path / 'restored/credential.json').exists()


@pytest.mark.parametrize('fault', ['bit', 'missing', 'manifest', 'db', 'incomplete', 'duplicate', 'traversal', 'schema'])
def test_backup_refuses_faults(backup, tmp_path, fault):
    root, j, sets = backup
    if fault == 'incomplete':
        def crash(phase):
            if phase == 'before_publish':
                raise RuntimeError('power-loss')
        with pytest.raises(RuntimeError):
            sets.capture(interrupt=crash)
        generation = next(sets.destination.glob('*.incomplete'))
        assert not sets.discover()
    else:
        generation = sets.capture()
        if fault == 'bit':
            p = generation / 'SDD.md'; raw = bytearray(p.read_bytes()); raw[0] ^= 1; p.write_bytes(raw)
        elif fault == 'missing':
            (generation / 'NEXT.yaml').unlink()
        elif fault == 'manifest':
            (generation / 'manifest.json').write_text('{}')
        elif fault == 'db':
            (generation / 'data/luffy.db').write_bytes(b'corrupt sqlite')
        else:
            manifest = json.loads((generation / 'manifest.json').read_text())
            if fault == 'duplicate':
                manifest['assets'].append(manifest['assets'][0])
            elif fault == 'traversal':
                manifest['assets'][0]['path'] = '../escape'
            else:
                manifest['schema'] = 'unknown.v999'
            raw = canonical(manifest)
            (generation / 'manifest.json').write_bytes(raw)
            (generation / 'COMPLETE').write_text(digest(raw))
    with pytest.raises(Refused):
        sets.restore(generation, tmp_path / 'refused-target')
    assert not (tmp_path / 'refused-target').exists()
    receipts = [json.loads(p.read_text()) for p in (sets.destination / 'restore-receipts').glob('*.json')]
    assert receipts[-1]['restore'] == 'REFUSED' and receipts[-1]['reasons']


def test_sqlite_corruption_even_rehashed_is_refused(backup):
    _, _, sets = backup
    g = sets.capture(); p = g / 'data/luffy.db'; p.write_bytes(b'not sqlite')
    m = json.loads((g / 'manifest.json').read_text())
    e = next(e for e in m['assets'] if e['kind'] == 'sqlite')
    e.update(size=p.stat().st_size, sha256=digest(p.read_bytes()))
    raw = canonical(m); (g / 'manifest.json').write_bytes(raw); (g / 'COMPLETE').write_text(digest(raw))
    with pytest.raises(Refused, match='invalid'):
        sets.verify(g)


@pytest.mark.parametrize('phase', ['asset', 'before_publish'])
def test_interrupted_restore_does_not_publish_or_overwrite(backup, tmp_path, phase):
    _, _, sets = backup; g = sets.capture()
    target = tmp_path / 'isolated-restore'
    def crash(at):
        if at == phase:
            raise OSError('injected interruption')
    with pytest.raises(Refused):
        sets.restore(g, target, interrupt=crash)
    assert not target.exists()
    live = tmp_path / 'existing'; live.mkdir(); (live / 'keep').write_text('unchanged')
    with pytest.raises(Refused):
        sets.restore(g, live)
    assert (live / 'keep').read_text() == 'unchanged'


@pytest.mark.parametrize('name', ['.env', 'credential.json', 'data/session_key', 'graphify-out/graph.json'])
def test_secrets_cannot_be_registered(backup, name):
    root, _, _ = backup
    with pytest.raises(Refused):
        critical_inventory(root, registered=[Asset(name, 'arbitrary')])


def test_embedded_config_secrets_refused(backup):
    root, _, sets = backup
    (root / 'config.yaml').write_text('api_secret: fixture_secret\n')
    with pytest.raises(Refused, match='secret'):
        sets.capture()
    assert not sets.discover()


def test_cross_file_change_refused(backup):
    root, _, sets = backup
    count = [0]
    def change(phase):
        count[0] += 1
        if count[0] == len(sets.inventory.assets):
            (root / 'SDD.md').write_text('changed')
    with pytest.raises(Refused, match='files_changed'):
        sets.capture(interrupt=change)


def test_schema_incompatibility_and_destination_refused(backup):
    root, j, sets = backup
    with pytest.raises(Refused, match='outside'):
        BackupSets(root, root / 'backups', sets.inventory, repository_version='fixture')
    g = sets.capture()
    sets.compatible_stores['data/luffy.db'] = dict(sets.compatible_stores['data/luffy.db'], user_version=777)
    with pytest.raises(Refused, match='incompatible'):
        sets.verify(g)


@pytest.fixture
def monitor(tmp_path):
    now = [100.]
    events = []
    health = SafetyHealth(tmp_path / 'data/safety_health.json', clock=lambda: now[0], sink=events.append)
    observer = SafetyObserver(health, clock=lambda: now[0])
    hb = Heartbeat(path=tmp_path / 'data/heartbeat_luffy.json', clock=lambda: now[0], instance_id='boot1')
    return now, events, health, observer, hb


def test_heartbeat_fresh_stale_stopped_dedup_reopen_recovery_restart(monitor):
    now, events, health, observer, hb = monitor
    policy = HeartbeatPolicy(10, 'injected-fixture')
    hb.beat({'last_successful_cycle_at': now[0]})
    assert observer.heartbeat(hb.path, 'luffy', policy)['status'] == 'FRESH'
    assert not events
    original = hb.path.read_bytes()
    now[0] += 11
    assert observer.heartbeat(hb.path, 'luffy', policy)['status'] == 'STALE'
    incident = events[-1]['incident_id']
    for _ in range(5):
        observer.heartbeat(hb.path, 'luffy', policy)
    assert len(events) == 1 and hb.path.read_bytes() == original
    reopened = SafetyObserver(SafetyHealth(health.path, clock=lambda: now[0], sink=events.append), clock=lambda: now[0])
    reopened.heartbeat(hb.path, 'luffy', policy)
    assert len(events) == 1
    hb.beat({'last_successful_cycle_at': hb.clock()})
    assert reopened.heartbeat(hb.path, 'luffy', policy)['status'] == 'FRESH'
    assert events[-1]['status'] == 'RESOLVED' and events[-1]['incident_id'] == incident
    assert health.entry_block()  # new heartbeat alone cannot release
    restart = Heartbeat(path=hb.path, clock=lambda: now[0], instance_id='boot2'); restart.beat({'last_successful_cycle_at': restart.clock()})
    assert reopened.heartbeat(hb.path, 'luffy', policy)['status'] == 'PRODUCER_RESTART_REQUIRES_RECOVERY'
    assert events[-1]['incident_id'] != incident
    assert health.read()['heartbeats']['luffy']['sequence'] == 1


@pytest.mark.parametrize('raw', ['{', '{}', 'null', '{"timestamp":100}', '{"timestamp":NaN}'])
def test_malformed_partial_heartbeat_never_fresh(monitor, raw):
    _, events, _, observer, hb = monitor
    hb.path.parent.mkdir(parents=True, exist_ok=True); hb.path.write_text(raw)
    assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'UNAVAILABLE'
    assert events[-1]['status'] == 'ACTIVE'
    assert hb.age_seconds() is None


def test_missing_unconfigured_future_and_restamped_heartbeat(monitor):
    now, _, health, observer, hb = monitor
    assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'MISSING'
    hb.beat({'last_successful_cycle_at': hb.clock()})
    assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(None))['status'] == 'POLICY_NOT_CONFIGURED'
    observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))
    raw = json.loads(hb.path.read_text()); now[0] += 1; raw['timestamp'] = now[0]; hb.path.write_text(json.dumps(raw))
    assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'UNCHANGED_SEQUENCE_RESTAMPED'
    raw['timestamp'] += 100; hb.path.write_text(json.dumps(raw))
    assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'UNAVAILABLE'
    assert health.entry_block()


def test_journal_read_failure_alert_and_entry_block(tmp_path, monkeypatch):
    j = Journal(tmp_path / 'data/j.db'); j.kv_set('control_state', 'ACTIVE')
    state = ControlStateMachine(j)
    def failed_conn():
        raise sqlite3.OperationalError('disk read failure')
    monkeypatch.setattr(j, '_conn', failed_conn)
    assert not state.can_enter()
    h = SafetyHealth(health_path(j)).read()
    assert h['conditions']['journal_read']['status'] == 'ACTIVE'
    assert h['recovery_required']


def test_journal_readable_unwritable_entry_and_independent_alert(tmp_path):
    j = Journal(tmp_path / 'data/j.db'); j.kv_set('control_state', 'ACTIVE')
    state = ControlStateMachine(j)
    j._conn().execute('PRAGMA query_only=ON')
    assert j.kv_get('control_state') == 'ACTIVE'
    assert not check_journal(j)
    assert not state.can_enter()
    assert entry_refusal(j)
    assert state.manages_exits()
    h = SafetyHealth(health_path(j)).read()
    assert h['conditions']['journal_write']['status'] == 'ACTIVE'
    incident = h['conditions']['journal_write']['incident_id']
    for _ in range(3):
        assert not check_journal(j)
    assert SafetyHealth(health_path(j)).read()['conditions']['journal_write']['incident_id'] == incident
    j._conn().execute('PRAGMA query_only=OFF')
    assert check_journal(j)
    assert not state.can_enter()  # storage recovery does not release
    assert SafetyHealth(health_path(j)).read()['conditions']['journal_write']['status'] == 'RESOLVED'


from tests.test_entry_recovery import setup


def test_actual_entry_write_failure_prevents_submission(setup):
    from tests.entry_authority_fixtures import permission
    ex, j, executor, d = setup
    proof = permission(executor, d)
    j._conn().execute('PRAGMA query_only=ON')
    result = executor.open(d, 2., 2., 95., 110., 'strategy', 'fixture', **proof)
    assert result is None
    assert not ex.sent
    assert 'critical_journal_unavailable' in d.skip_reason


def test_critical_store_unavailable_and_monitor_without_journal(tmp_path, monitor):
    now, events, health, observer, hb = monitor
    assert observer.store(tmp_path / 'missing.db', 'required_world')['status'] == 'READ_UNAVAILABLE'
    assert health.entry_block() and events
    from scripts.monitor import safety_check
    hb.beat({'last_successful_cycle_at': hb.clock()})
    result = safety_check(tmp_path, {'timeframes': {'scan_interval_seconds': 5}},
                          clock=lambda: now[0], sink=events.append)
    assert result['components'][0]['status'] == 'READ_UNAVAILABLE'
    assert result['health']['conditions']['journal_read']['status'] == 'ACTIVE'


def test_alert_sink_survives_artifact_and_journal_failure(monitor, monkeypatch):
    _, events, health, _, _ = monitor
    import trader.observability.safety as S
    monkeypatch.setattr(S, 'publish', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        health.observe('journal_write', 'disk full')
    assert events[-1]['condition'] == 'journal_write' and events[-1]['status'] == 'ACTIVE'


def test_supervisor_only_release_after_venue_risk_proof(tmp_path):
    from trader.engine.supervisor import Supervisor
    from trader.engine.risk import RiskRelease
    from tests.test_supervisor import Venue, RISK_OK
    j = Journal(tmp_path / 'data/j.db'); state = ControlStateMachine(j)
    h = SafetyHealth(health_path(j))
    h.observe('journal_write', 'injected'); h.observe('journal_write', None)
    venue = Venue()
    supervisor = Supervisor(j, state, SimpleNamespace(recovery=SimpleNamespace(pending=lambda: None),
                            recover_entries=Mock()), venue, risk_release=lambda: RiskRelease(False, 'risk_denied'))
    r = supervisor.pass_once(boot=True)
    assert not r.safe_to_activate and not state.can_enter() and h.entry_block()
    assert state.state == ControlState.RECOVERY
    supervisor.risk_release = RISK_OK
    r = supervisor.pass_once()
    assert r.safe_to_activate and state.can_enter() and not h.entry_block()


def test_heartbeat_outage_blocks_supervisor_and_new_entry(tmp_path, monitor):
    from trader.engine.supervisor import Supervisor
    from tests.test_supervisor import Venue, RISK_OK
    _, _, h, observer, hb = monitor
    j = Journal(tmp_path / 'data/j.db'); state = ControlStateMachine(j)
    observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))
    supervisor = Supervisor(j, state, SimpleNamespace(recovery=SimpleNamespace(pending=lambda: None),
                            recover_entries=Mock()), Venue(), risk_release=RISK_OK)
    r = supervisor.pass_once(boot=True)
    assert not r.safe_to_activate and not state.can_enter()
    assert any('heartbeat:luffy' in reason for reason in r.reasons)
    hb.beat({'last_successful_cycle_at': hb.clock()}); observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))
    assert not state.can_enter()
    assert supervisor.pass_once().safe_to_activate
    assert state.can_enter()


def test_owner_health_query_visible_with_failed_journal(tmp_path):
    from trader.owner.service import OwnerService
    from trader.owner.contract import OwnerRequest
    j = Journal(tmp_path / 'data/j.db'); state = ControlStateMachine(j)
    service = OwnerService(j, state, resume=lambda *a, **k: None)
    j._conn().execute('PRAGMA query_only=ON'); check_journal(j)
    # Existing typed query boundary bypasses neither authority nor persistence.
    req = OwnerRequest(request_id='health0001', operation='health', channel='test', identity='fixture-owner', issued_at=100.)
    result = service._read(req, 'owner')
    assert result.data['safety']['conditions']['journal_write']['status'] == 'ACTIVE'


# Mutate actual function bodies in memory, run the acceptance assertions, and
# require the exact intended assertion to fail. No production source mutation.
def mutated_method(obj, name, old, new):
    function = getattr(type(obj), name)
    source = textwrap.dedent(inspect.getsource(function))
    assert old in source
    namespace = dict(function.__globals__)
    exec(source.replace(old, new), namespace)
    setattr(obj, name, namespace[name].__get__(obj, type(obj)))


def must_refuse(sets, generation):
    try:
        sets.verify(generation)
    except Refused:
        return
    raise AssertionError('unsafe backup accepted')


def test_mutant_ignoring_hash_mismatch_detected(backup):
    _, _, sets = backup; g = sets.capture()
    p = g / 'SDD.md'; raw = bytearray(p.read_bytes()); raw[0] ^= 1; p.write_bytes(raw)
    mutated_method(sets, 'verify', "len(raw_asset) != e['size'] or digest(raw_asset) != e['sha256']", 'False')
    with pytest.raises(AssertionError, match='unsafe backup accepted'):
        must_refuse(sets, g)


def test_mutant_accepting_incomplete_detected(backup):
    _, _, sets = backup
    def crash(phase):
        if phase == 'before_publish':
            raise OSError('crash')
    with pytest.raises(OSError):
        sets.capture(interrupt=crash)
    g = next(sets.destination.glob('*.incomplete'))
    original = sets.verify
    sets.verify = lambda generation: original(generation, allow_staging=True)
    with pytest.raises(AssertionError, match='unsafe backup accepted'):
        must_refuse(sets, g)


def test_mutant_restoring_active_detected(backup, tmp_path, monkeypatch):
    _, _, sets = backup; g = sets.capture()
    monkeypatch.setattr(ControlStateMachine, 'set', lambda *a, **kw: None)
    sets.restore(g, tmp_path / 'mutant-restored')
    restored = Journal(tmp_path / 'mutant-restored/data/luffy.db')
    with pytest.raises(AssertionError, match='restored ACTIVE'):
        assert ControlStateMachine(restored).state != ControlState.ACTIVE, 'restored ACTIVE'


def test_mutant_allowing_write_failure_entry_detected(tmp_path, monkeypatch):
    import trader.observability.safety as S
    j = Journal(tmp_path / 'data/j.db'); j.kv_set('control_state', 'ACTIVE')
    state = ControlStateMachine(j); j._conn().execute('PRAGMA query_only=ON')
    monkeypatch.setattr(S, 'entry_refusal', lambda *a, **kw: None)
    with pytest.raises(AssertionError, match='write failure permitted entry'):
        assert not state.can_enter(), 'write failure permitted entry'


def test_mutant_stale_as_fresh_detected(monitor):
    now, _, _, observer, hb = monitor; hb.beat({'last_successful_cycle_at': hb.clock()}); now[0] += 11
    mutated_method(observer, 'heartbeat', "max(now - record['timestamp'],\n                 now - record['context']['last_successful_cycle_at']) > policy.stale_after_s", 'False')
    with pytest.raises(AssertionError, match='stale accepted fresh'):
        assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'STALE', 'stale accepted fresh'


def test_mutant_journal_only_alert_detected(tmp_path, monkeypatch):
    import trader.observability.safety as S
    j = Journal(tmp_path / 'data/j.db'); j._conn().execute('PRAGMA query_only=ON')
    def journal_only(journal, mode, exc):
        try:
            journal._conn().execute('INSERT OR REPLACE INTO state_kv VALUES (?,?)', ('unsafe_alert', mode))
        except sqlite3.Error:
            pass
    monkeypatch.setattr(S, 'journal_failure', journal_only)
    assert not check_journal(j)
    with pytest.raises(AssertionError, match='independent safety alert absent'):
        assert SafetyHealth(health_path(j)).read()['conditions'], 'independent safety alert absent'


def test_backup_remains_verifiable_after_source_state_loss(backup, tmp_path):
    root, j, sets = backup
    g = sets.capture()
    j._conn().close()
    for a in sets.inventory.assets:
        (root / a.path).unlink()
    # Compatibility comes from the trusted registered inventory, not from the
    # backup's own declaration or the lost source database.
    reopened = BackupSets(root, sets.destination, sets.inventory, repository_version='offline-recovery')
    assert reopened.verify(g)['backup_id'] == g.name
    assert reopened.restore(g, tmp_path / 'source-loss-restore')['restore'] == 'PUBLISHED'


def test_interruption_during_capture_has_no_complete_marker(backup):
    _, _, sets = backup
    def crash(phase):
        if phase == 'asset':
            raise OSError('capture interruption')
    with pytest.raises(OSError):
        sets.capture(interrupt=crash)
    stage = next(sets.destination.glob('*.incomplete'))
    assert not (stage / 'COMPLETE').exists()
    with pytest.raises(Refused):
        sets.verify(stage)


def test_atomic_restore_target_race_refused(backup, tmp_path, monkeypatch):
    import trader.persistence.backup as B
    _, _, sets = backup; g = sets.capture(); target = tmp_path / 'race-target'
    rename = B.rename_absent
    def race(source, destination):
        Path(destination).mkdir()  # exact race after existence check
        return rename(source, destination)
    monkeypatch.setattr(B, 'rename_absent', race)
    with pytest.raises(Refused, match='atomic_publish_refused'):
        sets.restore(g, target)
    assert list(target.iterdir()) == []


def test_accounting_detached_artifacts_mandatory(backup):
    from trader.observability.accounting import initialize
    root, _, sets = backup
    directory = root / 'data/accounting-worker'; directory.mkdir()
    path = directory / 'receipt.json'; path.write_text('{"schema":"fixture-accounting"}')
    with sqlite3.connect(directory / 'queue.db') as db:
        initialize(db)
        db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?)',
                   ('attempt1', 'trade1', str(path), 'complete', 1, 2, '[]'))
    sets = BackupSets(root, sets.destination, critical_inventory(root), repository_version='fixture')
    g = sets.capture()
    assert any(e['role'] == 'accounting_receipt' for e in sets.verify(g)['assets'])
    path.unlink()
    incomplete = BackupSets(root, sets.destination, critical_inventory(root), repository_version='fixture')
    with pytest.raises(Refused, match='artifact_missing'):
        incomplete.capture()


def test_required_additional_store_write_failure(tmp_path, monitor, monkeypatch):
    _, events, health, observer, _ = monitor
    path = tmp_path / 'required.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE retained(value TEXT)')
    real = sqlite3.connect
    def readonly(*a, **kw):
        db = real(*a, **kw); db.execute('PRAGMA query_only=ON'); return db
    monkeypatch.setattr(sqlite3, 'connect', readonly)
    assert observer.store(path, 'critical_world')['status'] == 'WRITE_UNAVAILABLE'
    assert events[-1]['condition'] == 'critical_world_write'
    assert health.entry_block()


def test_critical_store_registration_blocks_entry_immediately(tmp_path):
    j = Journal(tmp_path / 'data/j.db'); state = ControlStateMachine(j)
    j._critical_store_paths = [(tmp_path / 'missing-replay.db', 'required_replay')]
    assert not state.can_enter()
    assert SafetyHealth(health_path(j)).read()['conditions']['required_replay_read']['status'] == 'ACTIVE'
    assert state.manages_exits()


def test_failed_notifier_does_not_hide_health(monitor):
    _, _, health, _, _ = monitor
    health.sink = Mock(side_effect=OSError('notifier offline'))
    health.observe('journal_write', 'disk failure')
    assert health.read()['conditions']['journal_write']['status'] == 'ACTIVE'
    assert health.read()['conditions']['journal_write']['alert_sink_error'] == 'OSError'


def test_new_outage_has_new_incident_and_durable_transition_receipts(monitor):
    _, events, health, _, _ = monitor
    health.observe('journal_write', 'disk failure'); first = events[-1]['incident_id']
    health.observe('journal_write', 'disk failure')
    health.observe('journal_write', None)
    health.observe('journal_write', 'later outage'); second = events[-1]['incident_id']
    assert first != second and len(events) == 3
    receipts = list((health.path.parent / 'safety-incidents').glob('*.json'))
    assert len(receipts) == 3
    assert any(json.loads(p.read_text())['status'] == 'RESOLVED' for p in receipts)


def test_recovery_clear_cannot_erase_newer_outage(monitor):
    _, _, health, _, _ = monitor
    health.observe('journal_read', 'failure'); health.observe('journal_read', None)
    revision = health.read()['revision']
    health.observe('heartbeat:luffy', 'STALE')
    assert not health.clear_after_supervisor(revision)
    assert health.entry_block()


def test_owner_authenticated_health_remains_observable_read_failure(tmp_path, monkeypatch):
    from trader.owner.service import OwnerService
    from trader.owner.authz import Authorizer, Principal
    from trader.owner.contract import OwnerRequest, OPERATIONS, Status
    import trader.observability.safety as S
    j = Journal(tmp_path / 'data/j.db'); state = ControlStateMachine(j)
    auth = Authorizer(); auth.principals['owner'] = Principal('owner', frozenset(OPERATIONS), frozenset({'test'}))
    auth.bind('test', 'fixture-owner', 'owner')
    service = OwnerService(j, state, resume=lambda *a, **kw: None, authorizer=auth)
    def failed():
        raise sqlite3.OperationalError('read failure')
    monkeypatch.setattr(j, '_conn', failed)
    assert not S.check_journal(j)
    result = service.execute(OwnerRequest('read-failed-health', 'health', 'test', 'fixture-owner', issued_at=100.))
    assert result.status == Status.ACCEPTED
    assert result.data['safety']['conditions']['journal_read']['status'] == 'ACTIVE'
    assert result.data['supervisor']['error'] == 'supervisor_status_unreadable'
    assert state.manages_exits()


def test_outage_between_risk_proof_and_activation_cas_refuses_active(tmp_path):
    from trader.engine.supervisor import Supervisor
    from tests.test_supervisor import Venue, RISK_OK
    j = Journal(tmp_path / 'data/j.db'); state = ControlStateMachine(j)
    h = SafetyHealth(health_path(j))
    def risk_with_new_outage():
        h.observe('heartbeat:luffy', 'STALE')
        return RISK_OK()
    supervisor = Supervisor(j, state, SimpleNamespace(recovery=SimpleNamespace(pending=lambda: None),
                            recover_entries=Mock()), Venue(), risk_release=risk_with_new_outage)
    result = supervisor.pass_once(boot=True)
    assert state.state == ControlState.RECOVERY and not result.safe_to_activate
    assert 'critical_storage_or_heartbeat_unavailable' in result.reasons
    assert not state.can_enter()


def test_restored_state_boot_requires_owner_and_fresh_venue_risk(backup, tmp_path):
    from trader.engine.supervisor import Supervisor, OwnerContext
    from tests.test_supervisor import Venue, RISK_OK
    _, _, sets = backup; g = sets.capture(); target = tmp_path / 'contained-restore'
    sets.restore(g, target)
    j = Journal(target / 'data/luffy.db'); state = ControlStateMachine(j)
    venue = Venue(); venue.unreadable = True
    supervisor = Supervisor(j, state, SimpleNamespace(recovery=SimpleNamespace(pending=lambda: None),
                            recover_entries=Mock()), venue, risk_release=RISK_OK)
    first = supervisor.pass_once(boot=True)
    assert first.needs_owner and not first.safe_to_activate and state.state == ControlState.RECOVERY
    venue.unreadable = False
    assert not supervisor.pass_once().safe_to_activate
    assert not state.can_enter()
    result = supervisor.request_owner_recovery(OwnerContext('operator', 'test'))
    assert result.status == 'ACTIVATED' and state.can_enter()


def test_incomplete_inventory_cannot_claim_complete(backup):
    root, _, sets = backup
    inv = Inventory(tuple(a for a in sets.inventory.assets if a.path != 'NEXT.yaml'))
    with pytest.raises(Refused, match='critical_inventory_incomplete'):
        BackupSets(root, sets.destination, inv, repository_version='fixture')


def test_secret_content_in_operational_store_refused(backup):
    _, j, sets = backup
    j.kv_set('apiKey', 'fixture-secret')
    with pytest.raises(Refused, match='secret_content'):
        sets.capture()
    assert not sets.discover()


@pytest.fixture
def pit_backup(backup):
    from tests.test_pit_population import config, NOW
    from trader.observability import population as P
    root, j, old = backup
    cfg = config(root)
    ledger = root / 'data/attention_learning.db'
    cfg['local_ledgers'] = {'forecast': str(ledger)}
    with sqlite3.connect(ledger) as db:
        producer = P.Producer(db, cfg, 'forecast', NOW)
        producer.emit('required-fixture', 'registration', {'fixture': 'synthetic'})
        db.commit()
        P.flush(db, cfg, 'forecast')
        assert db.execute('SELECT COUNT(*) FROM population_events WHERE payload IS NULL').fetchone()[0] == 2
    (root / 'data/pit_population.json').write_text(json.dumps(cfg))
    inv = critical_inventory(root)
    sets = BackupSets(root, old.destination, inv, repository_version='fixture', clock=lambda: 1234.)
    return root, cfg, sets


def test_pit_population_dependency_closure_exact_and_restored(pit_backup, tmp_path):
    root, cfg, sets = pit_backup
    generation = sets.capture()
    manifest = sets.verify(generation)
    paths = {e['path']: e for e in manifest['assets']}
    for reference in ['data/pit_population.json', 'declaration.json', 'freeze.json']:
        assert reference in paths
    receipts = list((root / 'exports/forecast').glob('*.json'))
    assert len(receipts) == 2
    for path in receipts:
        entry = paths[path.relative_to(root).as_posix()]
        assert entry['sha256'] == digest(path.read_bytes())
        assert entry['dependency_identity']['id'] == json.loads(path.read_text())['id']
        assert entry['dependency_identity']['declaration_version'] == paths['declaration.json']['dependency_identity']['declaration_version']
    sets.restore(generation, tmp_path / 'pit-restored')
    for reference in paths:
        if reference != 'data/luffy.db':
            assert (tmp_path / 'pit-restored' / reference).read_bytes() == (generation / reference).read_bytes()


@pytest.mark.parametrize('missing', ['declaration', 'receipt', 'export'])
def test_pit_missing_required_dependency_refuses_backup(pit_backup, missing):
    root, cfg, sets = pit_backup
    path = next((root / 'exports/forecast').glob('*.json')) if missing == 'export' else Path(cfg[missing])
    path.unlink()
    with pytest.raises(Refused, match='pit_required_dependency_missing'):
        critical_inventory(root)
    with pytest.raises((Refused, FileNotFoundError)):
        sets.capture()
    assert not sets.discover()


@pytest.mark.parametrize('reference', ['declaration.json', 'freeze.json', 'export'])
def test_pit_tampered_dependency_restore_refused(pit_backup, tmp_path, reference):
    _, _, sets = pit_backup
    generation = sets.capture()
    path = next((generation / 'exports/forecast').glob('*.json')) if reference == 'export' else generation / reference
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(Refused, match='restore_refused'):
        sets.restore(generation, tmp_path / 'refused')
    assert not (tmp_path / 'refused').exists()


def test_pit_manifest_dependency_omission_even_rehashed_refuses(pit_backup, tmp_path):
    _, _, sets = pit_backup
    generation = sets.capture()
    manifest = sets.verify(generation)
    omitted = next(e for e in manifest['assets'] if e['role'] == 'pit_population_exported_receipt')
    manifest['assets'].remove(omitted)
    (generation / omitted['path']).unlink()
    (generation / 'manifest.json').write_bytes(canonical(manifest))
    (generation / 'COMPLETE').write_text(digest(canonical(manifest)))
    # Even a weakened caller-provided inventory cannot remove ledger dependencies.
    sets.inventory = Inventory(tuple(a for a in sets.inventory.assets if a.path != omitted['path']))
    with pytest.raises(Refused, match='pit_required_dependency_missing'):
        sets.verify(generation)
    with pytest.raises(Refused):
        sets.restore(generation, tmp_path / 'refused')


def test_pit_sqlite_only_manual_inventory_cannot_pass(pit_backup):
    root, _, sets = pit_backup
    reduced = Inventory(tuple(a for a in sets.inventory.assets if not a.role.startswith('pit_population')))
    with pytest.raises(Refused, match='pit_critical_inventory_incomplete'):
        BackupSets(root, sets.destination, reduced, repository_version='fixture')


def test_pit_config_missing_but_ledger_remains_refuses(pit_backup):
    root, _, _ = pit_backup
    (root / 'data/pit_population.json').unlink()
    with pytest.raises(Refused, match='pit_population_configuration_or_ledger_missing'):
        critical_inventory(root)


@pytest.mark.parametrize('context', [{}, {'last_successful_cycle_at': None},
    {'last_successful_cycle_at': 101.}, {'last_successful_cycle_at': 'not-a-timestamp'},
    {'last_successful_cycle_at': True}, {'last_successful_cycle_at': float('nan')},
    {'last_successful_cycle_at': 99.}])
def test_invalid_cycle_context_unavailable_alert_entry_block_no_restamp(monitor, tmp_path, context):
    now, events, health, observer, hb = monitor
    j = Journal(tmp_path / 'data/luffy.db'); j.kv_set('control_state', 'ACTIVE')
    hb.beat({'last_successful_cycle_at': now[0]})
    raw = json.loads(hb.path.read_text()); raw['context'] = context
    hb.path.write_text(json.dumps(raw)); exact = hb.path.read_bytes()
    for _ in range(3):
        assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'UNAVAILABLE'
        assert not ControlStateMachine(j).can_enter()
    assert hb.path.read_bytes() == exact and len(events) == 1
    assert health.read()['recovery_required'] and hb.age_seconds() is None


@pytest.mark.parametrize('field,value', [('producer',''), ('instance_id',' '),
    ('timestamp','invalid'), ('started_at',-1), ('sequence',0)])
def test_invalid_heartbeat_identity_cannot_be_fresh(monitor, field, value):
    now, events, _, observer, hb = monitor
    hb.beat({'last_successful_cycle_at': now[0]})
    raw = json.loads(hb.path.read_text()); raw[field] = value
    hb.path.write_text(json.dumps(raw))
    assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'UNAVAILABLE'
    assert events[-1]['status'] == 'ACTIVE'


@pytest.mark.parametrize('context', [{}, {'last_successful_cycle_at': None}, {'last_successful_cycle_at':101.}])
def test_mutant_removing_cycle_context_chronology_detected(monitor, monkeypatch, context):
    import trader.engine.watchdog as W
    now, _, _, observer, hb = monitor
    hb.beat({'last_successful_cycle_at': now[0]})
    raw = json.loads(hb.path.read_text()); raw['context'] = context; hb.path.write_text(json.dumps(raw))
    source = textwrap.dedent(inspect.getsource(W.read_heartbeat))
    start = source.index("    cycle = value['context']")
    end = source.index('    return value', start)
    namespace = dict(W.read_heartbeat.__globals__)
    exec(source[:start] + source[end:], namespace)
    monkeypatch.setattr(W, 'read_heartbeat', namespace['read_heartbeat'])
    # Remove the parallel successful-work age read too: otherwise missing/None
    # contexts are still refused by the independent observer's numeric operation.
    mutated_method(observer, 'heartbeat', "now - record['context']['last_successful_cycle_at']", '0')
    with pytest.raises(AssertionError, match='invalid heartbeat accepted fresh'):
        assert observer.heartbeat(hb.path, 'luffy', HeartbeatPolicy(10))['status'] == 'UNAVAILABLE', 'invalid heartbeat accepted fresh'


def test_pit_implicit_ledger_cannot_be_manually_omitted(pit_backup):
    root, cfg, sets = pit_backup
    cfg.pop('local_ledgers')
    (root / 'data/pit_population.json').write_text(json.dumps(cfg))
    inventory = critical_inventory(root)
    reduced = Inventory(tuple(a for a in inventory.assets if a.path != 'data/attention_learning.db'))
    with pytest.raises(Refused, match='pit_critical_inventory_incomplete'):
        BackupSets(root, sets.destination, reduced, repository_version='fixture')
