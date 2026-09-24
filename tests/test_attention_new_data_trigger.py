"""SDD-STAGE-3-ATTENTION-NEW-DATA-TRIGGER-V1. Synthetic stores only; no network."""
from contextlib import closing
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

from trader.observability import new_data as N
from trader.observability.attention import digest, settings
from trader.observability.store import Store

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
    return tmp_path, store, data, now


def run(tmp_path, **kw):
    return N.process(tmp_path / 'attention.db', tmp_path / 'events.db', 'attention', **kw)


def rows(tmp_path):
    return N.events(tmp_path / 'events.db', limit=10_000)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_first_closed_versions_emit_one_event_each(world):
    tmp_path, store, _, now = world
    result = run(tmp_path)
    count = store.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0]
    assert result['emitted'] == count == 2 * 26 and result['status'] == 'ok'
    assert result['dispatch'] == 'DOWNSTREAM_DISPATCH_PREREQUISITE_REQUIRED'
    got = rows(tmp_path)
    assert all(e['reason'] == 'new_version' and e['previous_value_hash'] is None for e in got)
    e = got[0]
    assert set(e) == {'schema', 'event_id', 'reason', 'version_id', 'symbol', 'timeframe',
                      'open_ms', 'value_hash', 'previous_value_hash', 'first_seen_ms',
                      'source', 'store'}
    assert e['schema'] == 'attention-new-data-event.v1' and e['first_seen_ms'] == now
    assert e['source'] and e['store'] == {'schema': 'attention.telemetry.v1', 'label': 'attention'}
    assert e['open_ms'] + TF <= e['first_seen_ms']


def test_changed_value_emits_one_linked_event_and_keeps_history(world):
    tmp_path, store, data, now = world
    run(tmp_path)
    before = {e['event_id']: e for e in rows(tmp_path)}
    last = data['S0/USDT']['4h'].index[-1]  # newest closed bar at `now`
    data['S0/USDT']['4h'].loc[last, 'close'] *= 1.001
    store.write(event('s2', now + 1, data))
    result = run(tmp_path, now_ms=now + 2)
    assert result['emitted'] == 1 and result['existing'] == 52
    after = {e['event_id']: e for e in rows(tmp_path)}
    changed = [e for e in after.values() if e['reason'] == 'value_changed']
    assert len(changed) == 1
    prior = [e for e in before.values() if e['symbol'] == 'S0/USDT'
             and e['open_ms'] == changed[0]['open_ms']][0]
    assert changed[0]['previous_value_hash'] == prior['value_hash']
    assert changed[0]['first_seen_ms'] == now + 1
    got = dict(store.db.execute('SELECT version_id,origin_scan_id FROM version_origins'))
    assert got[changed[0]['version_id']] == 's2' and list(got.values()).count('s2') == 1
    # Earlier event unchanged and still present.
    assert all(after[k] == v for k, v in before.items())


def test_unchanged_reobservation_repeat_and_restart_emit_nothing(world):
    tmp_path, store, data, now = world
    first = run(tmp_path)
    store.write(event('s2', now + 1, data))  # identical values
    assert store.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 52
    assert store.db.execute("SELECT COUNT(*) FROM version_origins WHERE origin_scan_id='s1'"
                            ).fetchone()[0] == 52  # no receipt for a re-observation
    again = run(tmp_path)
    assert again['emitted'] == 0 and again['existing'] == first['emitted']
    store.close()
    restarted = N.process(tmp_path / 'attention.db', tmp_path / 'events.db', 'attention')
    assert restarted['emitted'] == 0
    assert len(rows(tmp_path)) == 52


def test_identical_evidence_gives_identical_ids_and_order(world, tmp_path_factory):
    tmp_path, store, _, now = world
    run(tmp_path, now_ms=now)
    other = tmp_path_factory.mktemp('other')
    N.process(tmp_path / 'attention.db', other / 'events.db', 'attention', now_ms=now + 99_999)
    a, b = rows(tmp_path), N.events(other / 'events.db', limit=10_000)
    assert [e['event_id'] for e in a] == [e['event_id'] for e in b]
    assert a == b
    keys = [(e['first_seen_ms'], e['symbol'], e['timeframe'], e['open_ms'], e['event_id']) for e in a]
    assert keys == sorted(keys)


def tamper(store, sql, *args):
    store.db.execute(sql, args)
    store.db.commit()


def test_forming_bar_emits_nothing(world):
    tmp_path, store, _, now = world
    vid = store.db.execute('SELECT id FROM versions ORDER BY open_ms DESC LIMIT 1').fetchone()[0]
    tamper(store, 'UPDATE versions SET first_seen_ms=open_ms+? WHERE id=?', TF - 1, vid)
    result = run(tmp_path)
    assert result['refused'] == {'bar_not_closed': 1} and result['status'] == 'degraded'
    assert vid not in {e['version_id'] for e in rows(tmp_path)}
    assert not any(e['open_ms'] + TF > e['first_seen_ms'] for e in rows(tmp_path))


def test_missing_version_id_fails_closed(world):
    tmp_path, store, _, _ = world
    vid = store.db.execute('SELECT id FROM versions LIMIT 1').fetchone()[0]
    tamper(store, 'PRAGMA foreign_keys=OFF')
    tamper(store, 'UPDATE versions SET id=NULL WHERE id=?', vid)
    assert run(tmp_path)['refused'] == {'version_identity_missing': 1}
    assert len(rows(tmp_path)) == 51


def test_unverifiable_version_id_fails_closed(world):
    tmp_path, store, _, _ = world
    vid = store.db.execute('SELECT id FROM versions LIMIT 1').fetchone()[0]
    # A receipt naming a scan that did not create the version is corruption.
    tamper(store, 'DELETE FROM version_origins WHERE version_id=?', vid)
    tamper(store, 'INSERT INTO version_origins VALUES (?,?)', vid, 's_other')
    assert run(tmp_path)['refused'] == {'version_identity_unverifiable': 1}
    assert vid not in {e['version_id'] for e in rows(tmp_path)}


def origins(store):
    return dict(store.db.execute('SELECT version_id,origin_scan_id FROM version_origins'))


def test_origin_receipt_is_written_with_the_version_and_immutable(world):
    tmp_path, store, _, _ = world
    got = origins(store)
    ids = {r[0] for r in store.db.execute('SELECT id FROM versions')}
    assert set(got) == ids and set(got.values()) == {'s1'}
    with pytest.raises(sqlite3.IntegrityError, match='origin_immutable'):
        store.db.execute("UPDATE version_origins SET origin_scan_id='s2'")
    assert run(tmp_path)['refused'] == {}


def test_creator_pruned_version_still_verifies_and_emits_once(world):
    tmp_path, store, data, now = world
    # Unchanged re-observations; the store prunes s1 before writing s3, while
    # s2/s3 still reference the versions s1 created.
    s2 = Store(tmp_path / 'attention.db', settings({'max_scans': 2}))
    s2.write(event('s2', now + 1, data))
    s2.write(event('s3', now + 2, data))
    assert {r[0] for r in s2.db.execute('SELECT scan_id FROM scans')} == {'s2', 's3'}
    assert s2.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 52
    assert set(origins(s2).values()) == {'s1'}  # creator, not the later referrer
    first = run(tmp_path)
    assert first['refused'] == {} and first['emitted'] == 52 and first['status'] == 'ok'
    assert all(e['first_seen_ms'] == now for e in rows(tmp_path))
    again = run(tmp_path)
    assert again['emitted'] == 0 and again['existing'] == 52


def test_later_scan_cannot_impersonate_the_creator(world):
    tmp_path, store, data, now = world
    store.write(event('s2', now + 1, data))
    row = store.db.execute('SELECT rowid,id,symbol,tf,open_ms,first_seen_ms,value_hash,'
                           'previous_value_hash,payload FROM versions LIMIT 1').fetchone()
    assert N.verify(list(row), None, ['s1', 's2'], 'attention', 's1')[1] is None
    assert N.verify(list(row), None, ['s1', 's2'], 'attention', 's2') == (
        None, 'version_identity_unverifiable')
    # A receipt is authoritative: a referencing scan cannot rescue a bad one.
    assert N.verify(list(row), None, ['s1'], 'attention', 's2')[1] == 'version_identity_unverifiable'


def test_legacy_version_without_receipt_is_explicit(world):
    tmp_path, store, data, now = world
    tamper(store, 'DELETE FROM version_origins')  # as written before receipts existed
    # Creator still retained: the digest reproduces, an exact proof.
    assert run(tmp_path, now_ms=now)['emitted'] == 52
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    (legacy / 'attention.db').write_bytes((tmp_path / 'attention.db').read_bytes())
    s2 = Store(legacy / 'attention.db', settings({'max_scans': 2}))
    s2.write(event('s2', now + 1, data))
    s2.write(event('s3', now + 2, data))  # creator pruned, receipts absent
    assert s2.db.execute("SELECT COUNT(*) FROM scans WHERE scan_id='s1'").fetchone()[0] == 0
    result = run(legacy)
    assert result['refused'] == {'origin_unavailable': 52} and result['status'] == 'degraded'
    assert result['emitted'] == 0 and rows(legacy) == []
    assert origins(s2) == {}  # nothing was manufactured from s2/s3


def test_store_without_receipt_table_reads_as_legacy(tmp_path):
    now = int(time.time() * 1000)
    store = Store(tmp_path / 'attention.db', settings())
    store.write(event('s1', now, frames(1, now=now)))
    store.db.execute('DROP TABLE version_origins')
    store.db.commit()
    assert run(tmp_path)['emitted'] == 26
    store.db.execute('DELETE FROM scan_versions')
    store.db.commit()
    assert N.process(tmp_path / 'attention.db', tmp_path / 'e2.db', 'attention')['refused'] == {
        'origin_unavailable': 26}


@pytest.mark.parametrize('sql,reason', [
    ('UPDATE versions SET value_hash=NULL WHERE rowid=1', 'value_hash_unverifiable'),
    ("UPDATE versions SET value_hash='" + '0' * 64 + "' WHERE rowid=1", 'value_hash_unverifiable'),
    ("UPDATE versions SET previous_value_hash='" + '1' * 64 + "' WHERE rowid=1", 'hash_chain_unverifiable'),
    ("UPDATE versions SET previous_value_hash='bad' WHERE rowid=1", 'previous_hash_invalid'),
])
def test_invalid_hash_provenance_fails_closed(world, sql, reason):
    tmp_path, store, _, _ = world
    tamper(store, sql)
    assert run(tmp_path)['refused'] == {reason: 1}
    assert len(rows(tmp_path)) == 51


def test_broken_chain_link_fails_closed(world):
    tmp_path, store, data, now = world
    last = data['S0/USDT']['4h'].index[-1]
    data['S0/USDT']['4h'].loc[last, 'close'] *= 1.001
    store.write(event('s2', now + 1, data))
    tamper(store, "UPDATE versions SET previous_value_hash=? WHERE previous_value_hash IS NOT NULL",
           '2' * 64)
    assert run(tmp_path)['refused'] == {'hash_chain_mismatch': 1}


def test_missing_source_identity_fails_closed(world):
    tmp_path, store, _, _ = world
    vid, payload, tf = store.db.execute('SELECT id,payload,tf FROM versions LIMIT 1').fetchone()
    content = json.loads(payload)
    del content['source']
    # Consistent hash, so only the missing source can refuse it.
    tamper(store, 'UPDATE versions SET payload=?,value_hash=? WHERE id=?',
           json.dumps(content), digest(content), vid)
    result = run(tmp_path)
    assert set(result['refused']) <= {'source_identity_missing', 'version_identity_unverifiable'}
    assert sum(result['refused'].values()) == 1 and len(rows(tmp_path)) == 51
    with pytest.raises(ValueError, match='store_identity_missing'):
        N.process(tmp_path / 'attention.db', tmp_path / 'events.db', '')


def test_source_missing_even_with_verifiable_identity(world):
    tmp_path, store, _, _ = world
    row = store.db.execute('SELECT rowid,id,symbol,tf,open_ms,first_seen_ms,value_hash,'
                           'previous_value_hash,payload FROM versions LIMIT 1').fetchone()
    content = json.loads(row['payload']); del content['source']
    forged = [row[0], digest(['s1', row['tf'], content]), *list(row)[2:6], digest(content),
              None, json.dumps(content)]
    assert N.verify(forged, None, ['s1'], 'attention') == (None, 'source_identity_missing')


def test_events_are_append_only(world):
    tmp_path, _, _, _ = world
    run(tmp_path)
    with sqlite3.connect(tmp_path / 'events.db') as db:
        with pytest.raises(sqlite3.IntegrityError, match='append_only'):
            db.execute("UPDATE events SET payload='{}'")


def test_store_rows_hash_replay_and_retention_unchanged(world):
    tmp_path, store, data, now = world
    store.close()
    before = sha(tmp_path / 'attention.db')
    run(tmp_path)
    run(tmp_path)
    assert sha(tmp_path / 'attention.db') == before
    # Store behaviour after the trigger ran is the same as without it.
    store = Store(tmp_path / 'attention.db', settings({'max_scans': 1}))
    store.write(event('s2', now + 1, data))
    assert store.db.execute('SELECT COUNT(*) FROM scans').fetchone()[0] == 1
    assert store.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 52


def test_pruned_versions_events_retire_only_after_retention(world):
    tmp_path, store, data, now = world
    run(tmp_path, now_ms=now)
    shifted = frames(2, now=now + TF)
    s2 = Store(tmp_path / 'attention.db', settings({'max_scans': 1}))
    s2.write(event('s2', now + TF, shifted))
    live = run(tmp_path, now_ms=now + TF)
    assert live['pruned'] == 0
    late = run(tmp_path, now_ms=now + N.RETENTION_MS + 1)
    retained = {r[0] for r in s2.db.execute('SELECT id FROM versions')}
    assert late['pruned'] > 0
    assert {e['version_id'] for e in rows(tmp_path)} <= retained
    with sqlite3.connect(tmp_path / 'events.db') as db:
        tomb = dict(db.execute('SELECT version_id,event_id FROM retired'))
        assert len(tomb) == late['pruned'] and not set(tomb) & retained
        for sql in ("UPDATE retired SET retired_ms=0", "DELETE FROM retired"):
            with pytest.raises(sqlite3.IntegrityError, match='append_only'):
                db.execute(sql)
    assert run(tmp_path, now_ms=now + N.RETENTION_MS + 2)['emitted'] == 0
    # The store does not forbid reusing a pruned scan id: rewriting s1 recreates
    # the exact retired version ids. Identical re-derivations hit a tombstone;
    # ones now chained to s2's values differ and are refused as conflicts.
    s3 = Store(tmp_path / 'attention.db', settings({'max_scans': 2}))
    s3.write(event('s1', now, frames(2, now=now)))
    back = {r[0] for r in s3.db.execute('SELECT id FROM versions')} & set(tomb)
    assert back == set(tomb)
    again = run(tmp_path, now_ms=now + N.RETENTION_MS + 3)
    assert again['emitted'] == 0 and again['retired'] >= 1
    assert again['retired'] + again['refused'].get('event_conflict', 0) == len(tomb)
    assert set(again['refused']) <= {'event_conflict'}
    assert not {e['version_id'] for e in rows(tmp_path)} & set(tomb)


def test_retired_identities_count_toward_capacity(world, monkeypatch):
    tmp_path, store, _, now = world
    with closing(N.ledger(tmp_path / 'events.db')) as db, db:
        db.executemany('INSERT INTO retired VALUES (?,?,?)',
                       [(f'{i:064x}', f'{i + 1000:064x}', now) for i in range(50)])
    monkeypatch.setattr(N, 'MAX_EVENTS', 52)
    result = run(tmp_path)
    assert result['emitted'] == 2 and result['refused'] == {'event_capacity': 50}


def test_capacity_refusal_is_deterministic_and_never_reemits_retired(world, monkeypatch,
                                                                     tmp_path_factory):
    tmp_path, store, _, now = world
    full = run(tmp_path, now_ms=now)
    order = [e['version_id'] for e in rows(tmp_path)]
    assert full['emitted'] == len(order) == 52
    # A fresh ledger holding three tombstones for real identities, bound = 3 + 5.
    other = tmp_path_factory.mktemp('cap')
    (other / 'attention.db').write_bytes((tmp_path / 'attention.db').read_bytes())
    tomb = {e['version_id']: e['event_id'] for e in rows(tmp_path)[:3]}
    with closing(N.ledger(other / 'events.db')) as db, db:
        db.executemany('INSERT INTO retired VALUES (?,?,?)',
                       [(v, e, now) for v, e in tomb.items()])
    monkeypatch.setattr(N, 'MAX_EVENTS', 3 + 5)
    result = run(other, now_ms=now)
    # Tombstones consume capacity and still block re-emission ahead of the bound.
    assert result['retired'] == 3 and result['emitted'] == 5 and result['status'] == 'degraded'
    assert result['refused'] == {'event_capacity': 52 - 8}
    emitted = [e['version_id'] for e in rows(other)]
    assert emitted == [v for v in order if v not in tomb][:5]  # canonical order
    # Exhaustion is permanent: the identical refusal on every later pass.
    again = run(other, now_ms=now + 1)
    assert again['refused'] == result['refused'] and again['emitted'] == 0


def test_store_connection_enforces_foreign_keys(world):
    _, store, _, _ = world
    assert store.db.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_version_prune_cascades_receipt_and_exact_id_reuse_is_fresh(world):
    tmp_path, store, data, now = world
    first = origins(store)
    s2 = Store(tmp_path / 'attention.db', settings({'max_scans': 1}))
    s2.write(event('s2', now + TF, frames(2, now=now + TF)))  # prunes s1
    live = {r[0] for r in s2.db.execute('SELECT id FROM versions')}
    gone = set(first) - live
    assert gone and set(origins(s2)) == live  # receipts died with their versions
    assert s2.db.execute('SELECT COUNT(*) FROM version_origins WHERE version_id NOT IN '
                         '(SELECT id FROM versions)').fetchone()[0] == 0
    assert s2.db.execute('PRAGMA foreign_key_check').fetchall() == []
    # Reusing the pruned scan id recreates the exact version ids; each gets a
    # fresh receipt instead of colliding with a stale one.
    s3 = Store(tmp_path / 'attention.db', settings({'max_scans': 2}))
    s3.write(event('s1', now, data))
    back = origins(s3)
    assert gone <= set(back) and {back[v] for v in gone} == {'s1'}
    assert s3.db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_no_store_and_no_forbidden_imports(tmp_path):
    assert run(tmp_path)['reason'] == 'no_store'
    assert not (tmp_path / 'events.db').exists()
    import ast
    tree = ast.parse(Path(N.__file__).read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {'__future__', 'argparse', 'contextlib', 'json', 'pathlib', 're', 'sqlite3',
                    'time', 'trader.cognition.contracts', 'attention', 'trader.core.config',
                    'declared'}


def test_no_dispatch_is_performed(world, monkeypatch):
    tmp_path, _, _, _ = world
    import trader.observability.investigation as inv
    monkeypatch.setattr(inv, 'step', lambda *a, **k: pytest.fail('dispatch'))
    assert run(tmp_path)['dispatch'] == N.DISPATCH
