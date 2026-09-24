"""SDD-STAGE-3-ATTENTION-NEW-DATA-INCREMENTAL-CURSOR-V1. Synthetic stores only; no network."""
from contextlib import closing
import hashlib
import re
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from trader.observability import new_data as N
from trader.observability.attention import digest, settings
from trader.observability.store import Store, append_state

from tests.test_attention_store_append_sequence import changed, make_legacy
from tests.test_attention_telemetry import event, frames

TF = 14_400_000


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*a, **kw):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def world(tmp_path):
    now = int(time.time() * 1000)
    data = frames(2, now=now)
    store = Store(tmp_path / 'attention.db', settings())
    store.write(event('s1', now, data))
    yield tmp_path, store, data, now
    store.close()


def run(tmp_path, **kw):
    return N.process(tmp_path / 'attention.db', tmp_path / 'events.db', 'attention', **kw)


def ledger_rows(tmp_path, sql, *args):
    with closing(sqlite3.connect(tmp_path / 'events.db')) as db:
        return db.execute(sql, args).fetchall()


def cursor(tmp_path):
    got = ledger_rows(tmp_path, 'SELECT last_seq,last_version_id,observed_high_water FROM cursor')
    return got[0] if got else None


def event_ids(tmp_path):
    return sorted(r[0] for r in ledger_rows(tmp_path, 'SELECT event_id FROM events'))


def by_seq(store):
    return dict(store.db.execute('SELECT seq,version_id FROM version_append_receipts'))


def prune(store, seqs):
    """What Store retention does: the version goes, its receipt cascades."""
    ids = by_seq(store)
    for seq in seqs:
        store.db.execute('DELETE FROM scan_versions WHERE version_id=?', (ids[seq],))
        store.db.execute('DELETE FROM versions WHERE id=?', (ids[seq],))
    store.db.commit()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_bounded_batches_advance_continue_and_report_more(world):
    tmp_path, store, _, _ = world
    first = run(tmp_path, batch_size=20)
    assert (first['emitted'], first['last_seq'], first['high_water']) == (20, 20, 52)
    assert first['more_available'] is True and first['status'] == 'ok'
    assert cursor(tmp_path) == (20, by_seq(store)[20], 52)
    second = run(tmp_path, batch_size=20)
    assert (second['emitted'], second['last_seq'], second['more_available']) == (20, 40, True)
    third = run(tmp_path, batch_size=20)
    assert (third['emitted'], third['last_seq'], third['more_available']) == (12, 52, False)
    idle = run(tmp_path, batch_size=20)
    assert idle['emitted'] == 0 and idle['last_seq'] == 52 and idle['more_available'] is False
    # Emitted in append-sequence order, one per version.
    emitted = [r[0] for r in ledger_rows(tmp_path, 'SELECT version_id FROM events ORDER BY rowid')]
    assert emitted == [by_seq(store)[s] for s in range(1, 53)]


def test_batched_events_equal_single_pass_events(world, tmp_path_factory):
    tmp_path, store, data, now = world
    store.write(event('s2', now + 1, changed(data)))  # one value_changed version
    other = tmp_path_factory.mktemp('one')
    shutil.copy(tmp_path / 'attention.db', other / 'attention.db')
    while run(tmp_path, batch_size=7, now_ms=now)['more_available']:
        pass
    assert run(other, now_ms=now + 5)['last_seq'] == 53
    assert N.events(tmp_path / 'events.db', 10_000) == N.events(other / 'events.db', 10_000)
    got = N.events(tmp_path / 'events.db', 10_000)
    assert sorted(e['reason'] for e in got).count('value_changed') == 1
    for e in got:  # attention-new-data-event.v1 identity unchanged
        body = {k: v for k, v in e.items() if k != 'event_id'}
        assert e['schema'] == N.EVENT_SCHEMA and e['event_id'] == digest(body)
        assert set(e['store']) == {'schema', 'label'}


def test_restart_resumes_from_durable_cursor(world):
    tmp_path, store, data, now = world
    run(tmp_path, batch_size=30)
    store.close()
    reopened = Store(tmp_path / 'attention.db', settings())
    reopened.write(event('s2', now + 1, changed(data)))
    result = run(tmp_path, batch_size=30)
    assert (result['emitted'], result['last_seq'], result['high_water']) == (23, 53, 53)
    assert len(event_ids(tmp_path)) == 53
    reopened.close()


def test_crash_before_commit_changes_nothing_and_retry_is_exact(world, monkeypatch):
    tmp_path, store, _, _ = world
    prune(store, [3])
    vid = by_seq(store)[7]
    store.db.execute('UPDATE versions SET first_seen_ms=open_ms+? WHERE id=?', (TF - 1, vid))
    store.db.commit()
    real = N._advance
    def crash(*a, **k):
        raise RuntimeError('crash before commit')
    monkeypatch.setattr(N, '_advance', crash)
    with pytest.raises(RuntimeError):
        run(tmp_path)
    for table in ('events', 'append_gaps', 'append_refusals', 'cursor', 'cursor_binding',
                  'retired'):
        assert ledger_rows(tmp_path, f'SELECT COUNT(*) FROM {table}') == [(0,)], table
    monkeypatch.setattr(N, '_advance', real)
    result = run(tmp_path)
    assert result['emitted'] == 50 and result['refused'] == {'bar_not_closed': 1}
    assert result[N.GAP] == 1 and result['last_seq'] == 52
    again = run(tmp_path)
    assert again['emitted'] == 0 and again['refused'] == {} and again[N.GAP] == 0
    assert len(event_ids(tmp_path)) == 50
    assert ledger_rows(tmp_path, 'SELECT COUNT(*) FROM append_refusals') == [(1,)]
    assert ledger_rows(tmp_path, 'SELECT COUNT(*) FROM append_gaps') == [(1,)]


def test_missing_committed_sequence_is_pruned_before_observed(world):
    tmp_path, store, _, _ = world
    prune(store, [5])
    assert append_state(store.db)['high_water'] == 52
    result = run(tmp_path)
    assert result[N.GAP] == 1 and result['emitted'] == 51 and result['status'] == 'degraded'
    instance = append_state(store.db)['store_instance_id']
    assert ledger_rows(tmp_path, 'SELECT store_instance_id,first_seq,last_seq,store_label,reason '
                                 'FROM append_gaps') == [(instance, 5, 5, 'attention',
                                                          'pruned_before_observed')]


@pytest.mark.parametrize('batch', [1, 3, 8, 4096])
def test_consecutive_and_tail_gaps_are_deterministic(world, batch):
    tmp_path, store, _, _ = world
    prune(store, [1, 5, 6, 7, 10, 51, 52])
    total = 0
    while True:
        result = run(tmp_path, batch_size=batch)
        total += result[N.GAP]
        if not result['more_available']:
            break
    assert total == 7 and result['last_seq'] == 52 and cursor(tmp_path)[1] is None
    assert ledger_rows(tmp_path, 'SELECT first_seq,last_seq FROM append_gaps ORDER BY first_seq'
                       ) == [(1, 1), (5, 7), (10, 10), (51, 52)]
    assert len(event_ids(tmp_path)) == 45
    with closing(sqlite3.connect(tmp_path / 'events.db')) as db:
        for sql in ('UPDATE append_gaps SET last_seq=99', 'DELETE FROM append_gaps'):
            with pytest.raises(sqlite3.IntegrityError, match='append_only'):
                db.execute(sql)


def test_gap_is_not_confused_with_uncommitted_future(world):
    tmp_path, store, data, now = world
    run(tmp_path)
    assert run(tmp_path)[N.GAP] == 0  # nothing beyond high-water is a gap
    store.write(event('s2', now + 1, changed(data)))
    prune(store, [53])
    result = run(tmp_path)
    assert result[N.GAP] == 1 and result['last_seq'] == 53 and result['emitted'] == 0


def test_refused_row_recorded_once_and_does_not_block(world):
    tmp_path, store, _, _ = world
    vid = by_seq(store)[1]
    store.db.execute('UPDATE versions SET first_seen_ms=open_ms+? WHERE id=?', (TF - 1, vid))
    store.db.commit()
    first = run(tmp_path, batch_size=1)
    assert first['refused'] == {'bar_not_closed': 1} and first['last_seq'] == 1
    assert first['emitted'] == 0 and first['more_available'] is True
    instance = append_state(store.db)['store_instance_id']
    assert ledger_rows(tmp_path, 'SELECT store_instance_id,append_seq,version_id,reason '
                                 'FROM append_refusals') == [(instance, 1, vid, 'bar_not_closed')]
    nxt = run(tmp_path, batch_size=1)
    assert nxt['emitted'] == 1 and nxt['refused'] == {} and nxt['last_seq'] == 2
    rest = run(tmp_path)
    assert rest['emitted'] == 50 and rest['refused'] == {}
    assert vid not in {r[0] for r in ledger_rows(tmp_path, 'SELECT version_id FROM events')}
    assert ledger_rows(tmp_path, 'SELECT COUNT(*) FROM append_refusals') == [(1,)]
    with closing(sqlite3.connect(tmp_path / 'events.db')) as db:
        for sql in ('UPDATE append_refusals SET reason=1', 'DELETE FROM append_refusals',
                    'UPDATE cursor_binding SET store_instance_id=1'):
            with pytest.raises(sqlite3.IntegrityError, match='append_only'):
                db.execute(sql)
        for sql in ('UPDATE cursor SET last_seq=0', 'DELETE FROM cursor'):
            with pytest.raises(sqlite3.IntegrityError, match='cursor_forward_only'):
                db.execute(sql)


def test_store_instance_mismatch_fails_closed(world, tmp_path_factory):
    tmp_path, store, _, now = world
    run(tmp_path)
    before = (cursor(tmp_path), event_ids(tmp_path))
    store.close()
    other = tmp_path_factory.mktemp('b')
    replacement = Store(other / 'attention.db', settings())
    replacement.write(event('s1', now, frames(3, now=now)))  # a different Store, more data
    replacement.close()
    shutil.copy(other / 'attention.db', tmp_path / 'attention.db')
    for _ in range(2):
        result = run(tmp_path)
        assert result['status'] == 'blocked' and result['reason'] == 'store_instance_mismatch'
        assert result['emitted'] == 0 and result['more_available'] is None
    assert (cursor(tmp_path), event_ids(tmp_path)) == before
    faults = ledger_rows(tmp_path, 'SELECT fault,cursor_last_seq,observed_high_water '
                                   'FROM cursor_faults')
    assert faults == [('store_instance_mismatch', 52, 78)]  # recorded once
    # Stale events are never retired on another Store's absence evidence.
    late = run(tmp_path, now_ms=now + N.RETENTION_MS + 10)
    assert late['pruned'] == 0 and event_ids(tmp_path) == before[1]


def test_high_water_regression_fails_closed(world):
    tmp_path, store, data, now = world
    backup = (tmp_path / 'attention.db').read_bytes()
    store.write(event('s2', now + 1, changed(data)))
    assert run(tmp_path)['last_seq'] == 53
    store.close()
    (tmp_path / 'attention.db').write_bytes(backup)  # same instance, high-water 52
    result = run(tmp_path)
    assert result['status'] == 'blocked' and result['reason'] == 'store_regression'
    assert result['high_water'] == 52 and result['last_seq'] == 53
    assert cursor(tmp_path)[0] == 53  # never reset automatically


def test_anchor_divergence_fails_closed(world):
    tmp_path, store, data, now = world
    backup = (tmp_path / 'attention.db').read_bytes()
    store.write(event('s2', now + 1, changed(data)))
    assert run(tmp_path)['last_seq'] == 53
    store.close()
    (tmp_path / 'attention.db').write_bytes(backup)
    fork = Store(tmp_path / 'attention.db', settings())
    fork.write(event('s2', now + 1, changed(data, 'S1/USDT')))  # a different seq 53
    fork.close()
    result = run(tmp_path)
    assert result['status'] == 'blocked' and result['reason'] == 'store_divergence'
    assert cursor(tmp_path)[0] == 53 and len(event_ids(tmp_path)) == 53


def test_gap_anchored_cursor_detects_resurrected_sequence(world):
    tmp_path, store, _, _ = world
    backup = (tmp_path / 'attention.db').read_bytes()
    prune(store, [52])
    assert run(tmp_path)['last_seq'] == 52 and cursor(tmp_path)[1] is None
    store.close()
    (tmp_path / 'attention.db').write_bytes(backup)  # seq 52 exists again
    assert run(tmp_path)['reason'] == 'store_divergence'


def test_restored_pre_anchor_gap_is_an_unresolved_operator_boundary(world):
    """Documents the boundary, not a guarantee: only the cursor's own anchor is
    checked, so a same-instance restore that resurrects a gap recorded behind
    it is not detected. It is not read as new data either: the cursor never
    rewinds and nothing is emitted. Operator restore policy remains required."""
    tmp_path, store, _, _ = world
    backup = (tmp_path / 'attention.db').read_bytes()
    prune(store, [5])
    first = run(tmp_path)
    assert first[N.GAP] == 1 and first['last_seq'] == 52 and cursor(tmp_path)[1] is not None
    before = (cursor(tmp_path), event_ids(tmp_path))
    store.close()
    (tmp_path / 'attention.db').write_bytes(backup)  # seq 5 exists again, same instance
    for _ in range(2):
        result = run(tmp_path)
        assert result['status'] == 'ok' and result['emitted'] == 0 and result[N.GAP] == 0
        assert result['last_seq'] == 52 and result['more_available'] is False
    assert (cursor(tmp_path), event_ids(tmp_path)) == before
    assert ledger_rows(tmp_path, 'SELECT first_seq,last_seq FROM append_gaps') == [(5, 5)]
    assert ledger_rows(tmp_path, 'SELECT COUNT(*) FROM cursor_faults') == [(0,)]


def capacity_world(world, monkeypatch):
    tmp_path, store, _, now = world
    assert run(tmp_path, batch_size=10)['last_seq'] == 10
    monkeypatch.setattr(N, 'MAX_EVENTS', 12)
    return tmp_path, store


def test_capacity_exhaustion_is_blocked_and_retries_halt_identically(world, monkeypatch):
    tmp_path, store = capacity_world(world, monkeypatch)
    results = [run(tmp_path) for _ in range(3)]
    first = results[0]
    assert first['status'] == 'blocked' and first['reason'] == 'event_capacity'
    assert first['blocked'] == {'reason': 'event_capacity', 'append_seq': 13}
    assert first['emitted'] == 2 and first['refused'] == {} and first['last_seq'] == 12
    assert first['more_available'] is True and first['high_water'] == 52
    for again in results[1:]:
        assert again['status'] == 'blocked' and again['reason'] == 'event_capacity'
        assert again['blocked'] == first['blocked'] and again['emitted'] == 0
        assert again['last_seq'] == 12 and again['more_available'] is True
    assert cursor(tmp_path) == (12, by_seq(store)[12], 52)  # before the blocked sequence
    assert ledger_rows(tmp_path, 'SELECT COUNT(*) FROM append_refusals') == [(0,)]
    assert len(event_ids(tmp_path)) == 12
    # Room restored (policy unresolved; simulated here): the same sequence proceeds.
    monkeypatch.setattr(N, 'MAX_EVENTS', 1000)
    resumed = run(tmp_path)
    assert resumed['status'] == 'ok' and resumed['emitted'] == 40 and resumed['last_seq'] == 52


def test_capacity_block_makes_cli_exit_nonzero(world, monkeypatch, capsys):
    import json
    import sys
    import trader.core.config as config
    from trader.observability import declared
    tmp_path, store = capacity_world(world, monkeypatch)
    (tmp_path / 'data').mkdir()
    shutil.move(tmp_path / 'events.db', tmp_path / 'data' / 'attention_new_data.db')
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setattr(declared, 'source_path', lambda data: tmp_path / 'attention.db')
    monkeypatch.setattr(sys, 'argv', ['new_data', '--once', '--enable'])
    for _ in range(2):
        assert N.main() == 1
        out = json.loads(capsys.readouterr().out)
        assert out['status'] == 'blocked' and out['reason'] == 'event_capacity'
        assert out['blocked'] == {'reason': 'event_capacity', 'append_seq': 13}
    monkeypatch.setattr(N, 'MAX_EVENTS', 1000)
    assert N.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ok'


def test_store_without_append_sequence_is_blocked(world):
    tmp_path, store, _, _ = world
    for name in ('store_meta_immutable', 'store_meta_undeletable'):
        store.db.execute(f'DROP TRIGGER {name}')
    store.db.execute('DROP TABLE store_meta')
    store.db.commit()
    result = run(tmp_path)
    assert result['status'] == 'blocked' and result['reason'] == 'append_sequence_unavailable'
    assert result['emitted'] == 0


def test_legacy_migration_processes_in_append_order_without_chronology(tmp_path):
    now = int(time.time() * 1000)
    make_legacy(tmp_path / 'attention.db', now, frames(2, now=now))
    store = Store(tmp_path / 'attention.db', settings())
    state = append_state(store.db)
    assert state['append_origin'] == 'legacy_migration'
    result = run(tmp_path, batch_size=10)
    assert result['last_seq'] == 10 and result['emitted'] == 10
    emitted = [r[0] for r in ledger_rows(tmp_path, 'SELECT version_id FROM events ORDER BY rowid')]
    assert emitted == [by_seq(store)[s] for s in range(1, 11)]
    while run(tmp_path, batch_size=10)['more_available']:
        pass
    assert len(event_ids(tmp_path)) == 52
    assert ledger_rows(tmp_path, 'SELECT append_origin,migration_receipts_through_seq '
                                 'FROM cursor_binding') == [('legacy_migration', 52)]
    # Event payloads carry no append order or insertion-chronology claim.
    assert all(set(e) == {'schema', 'event_id', 'reason', 'version_id', 'symbol', 'timeframe',
                          'open_ms', 'value_hash', 'previous_value_hash', 'first_seen_ms',
                          'source', 'store'} for e in N.events(tmp_path / 'events.db', 100))
    store.close()


def test_retired_tombstone_blocks_reemission_through_the_cursor(world):
    tmp_path, store, data, now = world
    run(tmp_path, now_ms=now)
    s2 = Store(tmp_path / 'attention.db', settings({'max_scans': 1}))
    s2.write(event('s2', now + TF, frames(2, now=now + TF)))
    late = run(tmp_path, now_ms=now + N.RETENTION_MS + 1)
    assert late['pruned'] > 0 and late[N.GAP] == 0
    tomb = {r[0] for r in ledger_rows(tmp_path, 'SELECT version_id FROM retired')}
    s3 = Store(tmp_path / 'attention.db', settings({'max_scans': 2}))
    s3.write(event('s1', now, frames(2, now=now)))  # recreates retired ids at new seqs
    again = run(tmp_path, now_ms=now + N.RETENTION_MS + 2)
    assert again['emitted'] == 0 and again['last_seq'] == again['high_water']
    assert again['retired'] + again['refused'].get('event_conflict', 0) == len(tomb)
    assert not tomb & {r[0] for r in ledger_rows(tmp_path, 'SELECT version_id FROM events')}
    for s in (s2, s3):
        s.close()


def traced(monkeypatch):
    sql = []
    real = N._connect
    def connect(path):
        db = real(path)
        db.set_trace_callback(sql.append)
        return db
    monkeypatch.setattr(N, '_connect', connect)
    return sql


def full_scans(path, statements):
    """Tables a traced statement would scan rather than search."""
    allowed = {'sqlite_master', 'store_meta', 'version_append_high_water'}
    out = set()
    with closing(sqlite3.connect(path)) as db:
        for sql in statements:
            if not sql.lstrip().upper().startswith('SELECT'):
                continue
            for row in db.execute('EXPLAIN QUERY PLAN ' + sql):
                m = re.match(r'SCAN (\w+)', row[-1])
                if m and m.group(1) not in allowed:
                    out.add((m.group(1), sql))
    return out


def test_pass_and_bounded_cleanup_never_scan_versions(world, monkeypatch):
    tmp_path, store, data, now = world
    sql = traced(monkeypatch)
    run(tmp_path, now_ms=now, batch_size=16)
    s2 = Store(tmp_path / 'attention.db', settings({'max_scans': 1}))
    s2.write(event('s2', now + TF, frames(2, now=now + TF)))
    monkeypatch.setattr(N, 'CLEANUP_BATCH', 5)
    late = now + N.RETENTION_MS + 1
    pruned = 0
    for i in range(40):
        result = run(tmp_path, now_ms=late + i, batch_size=16)
        pruned += result['pruned']
    assert sql and full_scans(tmp_path / 'attention.db', sql) == set()
    # Point lookups only, and exactly the retirement candidates were asked about.
    assert not any('NOT IN' in s for s in sql)
    retained = {r[0] for r in s2.db.execute('SELECT id FROM versions')}
    tomb = {r[0] for r in ledger_rows(tmp_path, 'SELECT version_id FROM retired')}
    assert pruned == len(tomb) > 0 and not tomb & retained
    assert {r[0] for r in ledger_rows(tmp_path, 'SELECT version_id FROM events')} <= retained
    s2.close()


def test_cleanup_rotates_past_still_retained_old_events(world, monkeypatch):
    tmp_path, store, _, now = world
    run(tmp_path, now_ms=now)
    # Every old event but the one ordered last is still retained, so a fixed
    # head-of-queue window would never reach it; the rotating position does.
    last_vid = ledger_rows(tmp_path, 'SELECT version_id FROM events '
                                     'ORDER BY recorded_ms DESC,event_id DESC LIMIT 1')[0][0]
    prune(store, [s for s, v in by_seq(store).items() if v == last_vid])
    monkeypatch.setattr(N, 'CLEANUP_BATCH', 4)
    passes = []
    for i in range(14):
        passes.append(run(tmp_path, now_ms=now + N.RETENTION_MS + 1 + i)['pruned'])
    assert sum(passes) == 1 and passes.index(1) == 12  # 13th window of 4 over 52
    assert ledger_rows(tmp_path, 'SELECT version_id FROM retired') == [(last_vid,)]
    assert len(event_ids(tmp_path)) == 51


def test_max_versions_no_longer_bounds_processing(world, monkeypatch):
    tmp_path, store, data, now = world
    assert not hasattr(N, 'MAX_VERSIONS')
    for i in range(2, 6):  # grow well past any tiny batch
        data = changed(data, f'S{i % 2}/USDT')
        store.write(event(f's{i}', now + i, data))
    total, high = 0, append_state(store.db)['high_water']
    while True:
        result = run(tmp_path, batch_size=5)
        total += result['emitted']
        if not result['more_available']:
            break
    assert total == high == 56 and result['last_seq'] == high


def test_read_deadline_commits_nothing_and_never_reports_nothing_new(world, monkeypatch):
    tmp_path, store, data, now = world
    run(tmp_path, batch_size=10)
    before = (cursor(tmp_path), event_ids(tmp_path))
    monkeypatch.setattr(N, 'READ_DEADLINE_S', -1)
    with pytest.raises(sqlite3.OperationalError, match='interrupt'):
        run(tmp_path)
    assert (cursor(tmp_path), event_ids(tmp_path)) == before
    monkeypatch.setattr(N, 'READ_DEADLINE_S', 1.0)
    assert run(tmp_path)['last_seq'] == 52


def test_store_bytes_unchanged_by_incremental_passes(world):
    tmp_path, store, data, now = world
    prune(store, [4])
    store.close()
    before = sha(tmp_path / 'attention.db')
    while run(tmp_path, batch_size=9)['more_available']:
        pass
    run(tmp_path, now_ms=now + N.RETENTION_MS + 1)
    assert sha(tmp_path / 'attention.db') == before


@pytest.mark.parametrize('size', [0, -1, 1.5, None])
def test_invalid_batch_size_refused(world, size):
    tmp_path, _, _, _ = world
    with pytest.raises(ValueError, match='batch_size_invalid'):
        run(tmp_path, batch_size=size)
