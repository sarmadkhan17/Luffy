"""attention-version-append.v1: Store identity and version append sequence."""
from contextlib import closing
import copy
import json
import sqlite3
import time

import pytest

from trader.observability import store as S
from trader.observability.attention import digest, evaluate_snapshot, settings
from trader.observability.store import Store, append_state, export_scan

from tests.test_attention_telemetry import event, frames

TF = 14_400_000
APPEND_OBJECTS = ('version_append_on_insert', 'version_append_receipts_contiguous',
                  'version_append_receipts_immutable', 'version_append_receipts_undeletable',
                  'version_append_receipts',
                  'version_append_high_water_monotonic', 'version_append_high_water_undeletable',
                  'version_append_high_water', 'store_meta_immutable', 'store_meta_undeletable',
                  'store_meta')


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


def receipts(store):
    return dict(store.db.execute('SELECT version_id,seq FROM version_append_receipts'))


def seqs(store):
    return [r[0] for r in store.db.execute('SELECT seq FROM version_append_receipts ORDER BY seq')]


def versions(store):
    return {r[0] for r in store.db.execute('SELECT id FROM versions')}


def changed(data, symbol='S0/USDT'):
    out = copy.deepcopy(data)
    frame = out[symbol]['4h']
    frame.loc[frame.index[-1], 'close'] = float(frame['close'].iloc[-1]) * 1.05
    return out


def make_legacy(path, now, data):
    """A Store as written before this package: no append objects at all."""
    store = Store(path, settings())
    store.write(event('s1', now, data))
    for name in APPEND_OBJECTS:
        kind = 'TABLE' if name in ('version_append_receipts', 'version_append_high_water',
                                   'store_meta') else 'TRIGGER'
        store.db.execute(f'DROP {kind} {name}')
    store.db.commit()
    assert not store.db.execute("SELECT name FROM sqlite_master WHERE name IN (%s)" % ','.join(
        '?' * len(APPEND_OBJECTS)), APPEND_OBJECTS).fetchall()
    store.close()


def schema(path):
    with closing(sqlite3.connect(path)) as db:
        return sorted(db.execute('SELECT type,name,sql FROM sqlite_master'))


def dump(path):
    with closing(sqlite3.connect(path)) as db:
        return (sorted(db.execute('SELECT * FROM versions')),
                sorted(db.execute('SELECT * FROM version_origins')),
                sorted(db.execute('SELECT * FROM scan_versions')),
                sorted(db.execute('SELECT * FROM scans')))


# 1, 2
def test_instance_identity_created_once_stable_and_distinct(world, tmp_path_factory):
    tmp_path, store, data, now = world
    meta = append_state(store.db)
    assert meta['schema'] == S.META_SCHEMA and meta['append_schema'] == 'attention-version-append.v1'
    assert meta['append_origin'] == 'new_store' and meta['migration_order'] is None
    assert meta['migration_receipts_through_seq'] == 0
    ident = meta['store_instance_id']
    assert len(ident) == 32 and int(ident, 16) >= 0
    store.close()
    for _ in range(2):
        again = Store(tmp_path / 'attention.db', settings())
        again.write(event('s2', now + 1, data))
        assert append_state(again.db)['store_instance_id'] == ident
        again.close()
    other = Store(tmp_path_factory.mktemp('other') / 'attention.db', settings())
    assert append_state(other.db)['store_instance_id'] != ident
    other.close()
    world_store = Store(tmp_path / 'attention.db', settings())
    with pytest.raises(sqlite3.IntegrityError, match='store_meta_immutable'):
        world_store.db.execute("UPDATE store_meta SET store_instance_id='x'")
    with pytest.raises(sqlite3.IntegrityError, match='store_meta_immutable'):
        world_store.db.execute('DELETE FROM store_meta')
    world_store.db.rollback()
    world_store.close()


def test_identity_entropy_is_uuid_not_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: 1.0)
    a = Store(tmp_path / 'a.db', settings())
    b = Store(tmp_path / 'b.db', settings())
    assert append_state(a.db)['store_instance_id'] != append_state(b.db)['store_instance_id']
    a.close(); b.close()


# 3, 4, 5
def test_one_receipt_per_version_none_for_unchanged_next_for_changed(world):
    tmp_path, store, data, now = world
    assert set(receipts(store)) == versions(store)
    assert seqs(store) == list(range(1, 53))
    assert append_state(store.db)['high_water'] == 52
    store.write(event('s2', now + 1, data))  # unchanged re-observation
    assert seqs(store) == list(range(1, 53)) and len(versions(store)) == 52
    before = versions(store)
    store.write(event('s3', now + 2, changed(data)))
    new = versions(store) - before
    assert len(new) == 1 and receipts(store)[new.pop()] == 53
    assert append_state(store.db)['high_water'] == 53


def test_receipt_rows_are_immutable(world):
    _, store, _, _ = world
    with pytest.raises(sqlite3.IntegrityError, match='append_receipt_immutable'):
        store.db.execute('UPDATE version_append_receipts SET seq=999 WHERE seq=1')
    with pytest.raises(sqlite3.IntegrityError, match='append_high_water_monotonic'):
        store.db.execute('UPDATE version_append_high_water SET seq=1')
    with pytest.raises(sqlite3.IntegrityError, match='append_high_water_monotonic'):
        store.db.execute('DELETE FROM version_append_high_water')
    store.db.rollback()


def test_explicit_or_skipped_sequence_fails_closed(world):
    _, store, _, _ = world
    # Receipt-less version built inside a rolled-back transaction (DDL is
    # transactional), so the setup never deletes a receipt.
    store.db.execute('BEGIN')
    store.db.execute('DROP TRIGGER version_append_on_insert')
    store.db.execute("INSERT INTO versions (id,payload) VALUES ('x','{}')")
    for seq in (100, 52, 1):  # skipped, reused-by-high-water, reused-retained
        with pytest.raises(sqlite3.IntegrityError,
                           match='append_sequence_discontinuity|UNIQUE|PRIMARY'):
            store.db.execute("INSERT INTO version_append_receipts VALUES (?,'x')", (seq,))
    store.db.rollback()
    assert store.db.execute("SELECT 1 FROM sqlite_master WHERE name='version_append_on_insert'"
                            ).fetchone()
    assert append_state(store.db)['high_water'] == 52 and seqs(store) == list(range(1, 53))


def test_receipt_cannot_be_deleted_while_its_version_exists(world):
    _, store, _, _ = world
    before = receipts(store)
    vid = next(iter(before))
    for sql, args in (('DELETE FROM version_append_receipts WHERE version_id=?', (vid,)),
                      ('DELETE FROM version_append_receipts', ())):
        with pytest.raises(sqlite3.IntegrityError, match='append_receipt_undeletable'):
            store.db.execute(sql, args)
        store.db.rollback()
    assert receipts(store) == before and append_state(store.db)['pruned_committed'] == 0


def test_parent_delete_cascades_receipt_to_detectable_gap(world):
    _, store, _, _ = world
    before = receipts(store)
    vid = min(before, key=before.get)  # seq 1: gap strictly below high-water
    with store.db:
        store.db.execute('DELETE FROM scan_versions WHERE version_id=?', (vid,))
        store.db.execute('DELETE FROM versions WHERE id=?', (vid,))
    state = append_state(store.db)
    assert vid not in receipts(store) and state['high_water'] == 52
    assert set(range(1, 53)) - set(seqs(store)) == {before[vid]} == {1}
    assert state['pruned_committed'] == 1 and set(receipts(store)) == versions(store)
    assert store.db.execute('PRAGMA foreign_key_check').fetchall() == []


# 6, 9, 10
def test_pruning_never_reuses_and_leaves_detectable_gaps(world):
    tmp_path, store, data, now = world
    first = receipts(store)
    s2 = Store(tmp_path / 'attention.db', settings({'max_scans': 1}))
    s2.write(event('s2', now + TF, frames(2, now=now + TF)))  # prunes s1
    state = append_state(s2.db)
    gone = set(first) - versions(s2)
    assert gone and state['high_water'] > 52
    assert set(receipts(s2)) == versions(s2)  # receipts died with their versions
    # Every committed sequence is 1..high_water; a missing one was pruned.
    missing = set(range(1, state['high_water'] + 1)) - set(seqs(s2))
    assert missing == {first[v] for v in gone}
    assert state['pruned_committed'] == len(missing)
    assert max(seqs(s2)) == state['high_water']
    assert s2.db.execute('PRAGMA foreign_key_check').fetchall() == []
    # Recreating a pruned exact version id is a fresh append, not a reuse.
    s3 = Store(tmp_path / 'attention.db', settings({'max_scans': 2}))
    s3.write(event('s1', now, data))
    back = receipts(s3)
    assert all(back[v] > state['high_water'] for v in gone)
    assert append_state(s3.db)['high_water'] == state['high_water'] + len(gone)
    s2.close(); s3.close()


# 7, 11
def test_empty_then_refill_vacuum_and_reopen_keep_sequence(world):
    tmp_path, store, data, now = world
    with store.db:
        store.db.execute('DELETE FROM scans')
        store.db.execute('DELETE FROM versions')
    assert seqs(store) == [] and append_state(store.db)['high_water'] == 52
    assert store.db.execute('PRAGMA foreign_key_check').fetchall() == []
    store.write(event('s9', now + 1, data))
    assert seqs(store) == list(range(53, 105))
    kept = receipts(store)
    ident = append_state(store.db)['store_instance_id']
    store.db.execute('VACUUM')
    store.close()
    reopened = Store(tmp_path / 'attention.db', settings())
    assert receipts(reopened) == kept
    state = append_state(reopened.db)
    assert state['high_water'] == 104 and state['store_instance_id'] == ident
    reopened.write(event('s10', now + 2, changed(data)))
    assert max(seqs(reopened)) == 105
    reopened.close()


# 8
def test_rollback_commits_neither_version_nor_receipt(world, monkeypatch):
    tmp_path, store, data, now = world
    before = (versions(store), receipts(store), append_state(store.db)['high_water'])
    def boom(_):
        raise RuntimeError('mid-write failure')
    monkeypatch.setattr(S, 'evaluate_snapshot', boom)  # after version inserts
    with pytest.raises(RuntimeError):
        store.write(event('s2', now + 1, changed(data)))
    monkeypatch.undo()
    after = (versions(store), receipts(store), append_state(store.db)['high_water'])
    assert after == before
    store.write(event('s3', now + 2, changed(data)))
    assert max(seqs(store)) == 53  # next committed number follows the committed high-water


# 12
def test_legacy_store_migrates_without_changing_versions(tmp_path):
    now = int(time.time() * 1000)
    data = frames(2, now=now)
    path = tmp_path / 'attention.db'
    make_legacy(path, now, data)
    before = dump(path)
    store = Store(path, settings())
    assert dump(path) == before
    state = append_state(store.db)
    assert state['append_origin'] == 'legacy_migration'
    assert state['migration_order'] == 'migration_order_not_insertion_chronology'
    assert state['migration_receipts_through_seq'] == 52 == state['high_water']
    expected = [r[0] for r in store.db.execute('SELECT id FROM versions ORDER BY first_seen_ms,id')]
    assert [r[0] for r in store.db.execute(
        'SELECT version_id FROM version_append_receipts ORDER BY seq')] == expected
    ident = state['store_instance_id']
    store.write(event('s2', now + 1, changed(data)))
    assert max(seqs(store)) == 53  # post-migration appends are real creation order
    store.close()
    again = Store(path, settings())
    assert append_state(again.db)['store_instance_id'] == ident  # migrated once
    assert append_state(again.db)['migration_receipts_through_seq'] == 52
    again.close()


# 13
def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    now = int(time.time() * 1000)
    path = tmp_path / 'attention.db'
    make_legacy(path, now, frames(2, now=now))
    before, shape = dump(path), schema(path)
    real = S._legacy_version_ids
    # Fails late: after DDL, identity and some receipts were written.
    monkeypatch.setattr(S, '_legacy_version_ids', lambda db: real(db) + ['not-a-version'])
    with pytest.raises(sqlite3.IntegrityError):
        Store(path, settings())
    assert dump(path) == before and schema(path) == shape
    monkeypatch.undo()
    store = Store(path, settings())
    assert append_state(store.db)['high_water'] == 52
    store.close()


def test_interrupted_new_store_creation_is_still_new(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError('interrupted')
    monkeypatch.setattr(S, 'uuid4', boom)
    with pytest.raises(RuntimeError):
        Store(tmp_path / 'attention.db', settings())
    monkeypatch.undo()
    assert schema(tmp_path / 'attention.db') == []
    store = Store(tmp_path / 'attention.db', settings())
    assert append_state(store.db)['append_origin'] == 'new_store'
    store.close()


# 14
def test_scan_payload_hash_input_versions_and_replay_unchanged(tmp_path):
    from trader.cognition.contracts import INPUT_SCHEMA
    now = int(time.time() * 1000)
    data = frames(2, now=now)
    make_legacy(tmp_path / 'legacy.db', now, data)
    store = Store(tmp_path / 'new.db', settings())
    original = event('s1', now, data)
    store.write(copy.deepcopy(original))
    scan = lambda p: {k: v for k, v in json.loads(sqlite3.connect(p).execute(
        'SELECT payload FROM scans').fetchone()[0]).items() if k not in ('persisted_at_ms', 'capture_ms')}
    assert scan(tmp_path / 'new.db') == scan(tmp_path / 'legacy.db')
    assert dump(tmp_path / 'new.db')[0] == dump(tmp_path / 'legacy.db')[0]
    export_scan(store.path, 's1', tmp_path / 'export.json')
    out = json.loads((tmp_path / 'export.json').read_text())
    assert set(out) == {'scan', 'versions', 'causes'}
    assert set(out['versions'][0]) == {'version_id', 'first_seen_ms', 'previous_value_hash', 'payload'}
    assert 'append' not in json.dumps(out['scan'])
    by_id = {v['version_id']: v for v in out['versions']}
    candles = [{**json.loads(by_id[r['version_id']]['payload']),
                'available_ms': by_id[r['version_id']]['first_seen_ms']}
               for r in out['scan']['input_versions']]
    restored = {**original, 'input': {'schema': INPUT_SCHEMA, 'timeframe': out['scan']['timeframe'],
                'membership': out['scan']['membership'], 'candles': candles,
                'decision_times': [now], 'participation': [],
                'correlation_history': out['scan']['correlation_input']}}
    assert digest(restored['input']) == out['scan']['input_hash']
    assert evaluate_snapshot(restored)['rows'] == out['scan']['rows']
    store.close()


# 15
def test_declared_population_store_path_gets_receipts(tmp_path):
    from tests.test_declared_population import NOW, bars, decl
    from trader.observability import declared as C
    times = iter(range(NOW, NOW + 10))
    e = C.collect(decl(2), bars, lambda: next(times))
    store = Store(tmp_path / 'attention.db', e['capture_settings'])
    store.write(e)
    seen = dict(store.db.execute('SELECT symbol,MIN(first_seen_ms) FROM versions GROUP BY symbol'))
    assert seen == {r['symbol']: r['observed_ms'] for r in e['scope']['availability_receipts']}
    n = len(versions(store))
    assert n and set(receipts(store)) == versions(store) and seqs(store) == list(range(1, n + 1))
    store.close()


# 16
def test_version_origins_still_written_and_cascade_with_receipts(world):
    tmp_path, store, data, now = world
    origins = dict(store.db.execute('SELECT version_id,origin_scan_id FROM version_origins'))
    assert set(origins) == versions(store) == set(receipts(store))
    s2 = Store(tmp_path / 'attention.db', settings({'max_scans': 1}))
    s2.write(event('s2', now + TF, frames(2, now=now + TF)))
    live = versions(s2)
    assert set(dict(s2.db.execute('SELECT version_id,origin_scan_id FROM version_origins'))) == live
    assert set(receipts(s2)) == live
    s2.close()


# 17
def test_no_new_network_llm_or_trading_dependency():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(S))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert names <= {'__future__', 'contextlib', 'json', 'pathlib', 'sqlite3', 'time', 'uuid',
                     'attention', ''}, names
