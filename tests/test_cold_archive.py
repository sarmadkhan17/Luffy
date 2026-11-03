"""Cold export and pruning on synthetic journals on the healthy /tmp disk."""
import json
import sqlite3

import pytest

from trader.core.journal import SCHEMA
from trader.core import journal_evidence as E
from trader.persistence import cold_archive as C

OLD = '2026-01-02T00:00:00+00:00'
CLOSED = '2026-01-04T00:00:00+00:00'
CUTOFF = '2026-02-01T00:00:00+00:00'


@pytest.fixture
def db(tmp_path):
    db = sqlite3.connect(tmp_path / 'journal.db')
    db.executescript(SCHEMA + E.SCHEMA)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA user_version=42')
    try:
        yield db
    finally:
        db.close()


def cycle(db, cid, *, action='BUY', trade='closed', complete=True, ts=OLD, executed=1):
    db.execute('INSERT INTO cycles(id,ts,symbol) VALUES (?,?,?)', (cid, ts, 'BTC/USDT'))
    db.execute('INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,executed) '
               'VALUES (?,?,?,?,?,1,1,1,?)', ('d'+cid, cid, ts, 'BTC/USDT', action, executed))
    if action != 'HOLD':
        db.execute('INSERT INTO outcomes(decision_id,cycle_id,symbol,ts,action,entry_price,resolved_at,'
                   'fwd_ret_1h,fwd_ret_4h,fwd_ret_24h) VALUES (?,?,?,?,?,100,?,.1,.2,?)',
                   ('d'+cid, cid, 'BTC/USDT', ts, action, CLOSED, .3 if complete else None))
    if executed:
        db.execute('INSERT INTO trades(id,decision_id,symbol,side,amount,entry_price,opened_at,closed_at,status) '
                   'VALUES (?,?,?,\'long\',1,100,?,?,?)',
                   ('t'+cid, 'd'+cid, 'BTC/USDT', ts, CLOSED if trade == 'closed' else None, trade))
    db.commit()


def vote(db, cid, *, ts=OLD, agent='agent', meta=' {"x": 1.00, "escaped": "\\u0041"} '):
    cursor = db.execute('INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,confidence,rationale,meta) '
        'VALUES (?,?,?,?,\'long\',.75,.8,?,?)', (cid, ts, 'BTC/USDT', agent, 'reason\x00 · 😀\n', meta))
    db.commit()
    return cursor.lastrowid


def blob(db, content):
    marker = E.store(db, content)
    db.commit()
    return marker, json.loads(marker[len(E.PREFIX):])['chunks'][0]


def test_votes_roundtrip_deterministic_and_only_closed_rows_export(db, tmp_path):
    cycle(db, 'closed')
    cycle(db, 'hold', action='HOLD', executed=0)
    cycle(db, 'active', trade='open')
    cycle(db, 'pending', complete=False)
    cycle(db, 'recent', ts='2026-03-01T00:00:00+00:00')
    eligible = [vote(db, 'closed', agent='z'), vote(db, 'hold', agent='a', meta=b'meta\x00bytes')]
    active = [vote(db, 'active'), vote(db, 'pending'), vote(db, 'recent', ts='2026-03-01')]
    before = list(db.execute('SELECT * FROM votes ORDER BY id'))
    a, b = tmp_path/'a', tmp_path/'b'
    manifest = C.export_votes(db, a, cutoff=CUTOFF)
    C.export_votes(db, b, cutoff=CUTOFF)
    assert manifest['user_version'] == 42 and manifest['count'] == 2
    assert {r['id'] for r in C.read_votes([a])} == set(eligible)
    names = [c[1] for c in db.execute('PRAGMA table_info(votes)')]
    expected = [dict(zip(names, r)) for r in before if r[0] in eligible]
    assert sorted(C.read_archive(a), key=lambda r:r['id']) == expected
    assert [r['agent'] for r in C.read_archive(a)] == ['a', 'z']
    assert {p.name:p.read_bytes() for p in a.iterdir()} == {p.name:p.read_bytes() for p in b.iterdir()}
    assert len(list(C.read_votes([a, b]))) == 2
    assert len(list(C.read_votes([a], agent='z', since='2026-01-01', until=CUTOFF))) == 1
    assert C.prune(db, a)['deleted'] == 0
    assert list(db.execute('SELECT * FROM votes ORDER BY id')) == before
    assert C.prune(db, a, dry_run=False, limit=1)['deleted'] == 1
    assert C.prune(db, a, dry_run=False, limit=1)['deleted'] == 1
    assert C.prune(db, a, dry_run=False)['deleted'] == 0
    assert {r[0] for r in db.execute('SELECT id FROM votes')} == set(active)


@pytest.mark.parametrize('table,column', [('brain_events','detail'), ('control_events','detail'),
    ('learning_produced_chains','payload'), ('decisions_extra','json'), ('trades_extra','entry_identity_json')])
def test_all_retained_columns_and_transitive_blob_references_are_roots(db, tmp_path, table, column):
    _, child = blob(db, 'child')
    parent, parent_sha = blob(db, json.dumps({'blob_sha256': child}))
    _, orphan_child = blob(db, 'orphan child')
    _, orphan_parent = blob(db, json.dumps({'blob_sha256': orphan_child}))
    # Unknown future tables and nested, escaped markers must work too.
    if table not in {'brain_events','control_events'}:
        db.execute(f'CREATE TABLE {table} ({column} TEXT)')
        db.execute(f'INSERT INTO {table} VALUES (?)', (json.dumps({'retained':parent}),))
    elif table == 'brain_events':
        db.execute('INSERT INTO brain_events(ts,kind,detail) VALUES (?,?,?)', (OLD,'test',parent))
    else:
        db.execute('INSERT INTO control_events(ts,event,actor,detail) VALUES (?,?,?,?)',
                   (OLD,'test','operator',parent))
    db.commit()
    roots = C.reachable(db)
    assert roots.blobs == {child, parent_sha}
    assert (table,column) in roots.scanned_columns
    archive = tmp_path/'blobs'
    manifest = C.export_blobs(db, archive)
    assert manifest['count'] == 2
    assert {r['sha256'] for r in C.read_archive(archive)} == {orphan_child, orphan_parent}
    assert C.prune(db, archive)['eligible'] == 2
    assert C.prune(db, archive, dry_run=False)['deleted'] == 2
    assert {r[0] for r in db.execute(f'SELECT sha256 FROM {C.BLOBS}')} == roots.blobs
    assert E.resolve(db, parent) == json.dumps({'blob_sha256': child})


def test_new_root_after_export_blocks_blob_pruning(db, tmp_path):
    marker, sha = blob(db, 'now retained')
    archive = tmp_path/'blobs'
    C.export_blobs(db, archive)
    db.execute('INSERT INTO brain_events(ts,kind,detail) VALUES (?,?,?)', (OLD,'test',marker))
    db.commit()
    assert C.prune(db, archive, dry_run=False)['deleted'] == 0
    assert db.execute(f'SELECT 1 FROM {C.BLOBS} WHERE sha256=?', (sha,)).fetchone()


@pytest.mark.parametrize('change', ['pending', 'open_trade', 'vote_pointer', 'foreign_key'])
def test_votes_becoming_active_or_referenced_after_export_are_retained(db, tmp_path, change):
    cycle(db, 'closed')
    identity = vote(db, 'closed')
    archive = tmp_path/'votes'
    C.export_votes(db, archive, cutoff=CUTOFF)
    if change == 'pending':
        db.execute("UPDATE outcomes SET fwd_ret_24h=NULL WHERE cycle_id='closed'")
    elif change == 'open_trade':
        db.execute("UPDATE trades SET status='open',closed_at=NULL")
    elif change == 'vote_pointer':
        db.execute('INSERT INTO brain_events(ts,kind,detail) VALUES (?,?,?)',
                   (OLD,'test',json.dumps({'lineage':{'vote_ids':[identity]}})))
    else:
        db.execute('CREATE TABLE external_pointer(vote_id INTEGER REFERENCES votes(id))')
        db.execute('INSERT INTO external_pointer VALUES (?)', (identity,))
    db.commit()
    assert C.prune(db, archive, dry_run=False)['deleted'] == 0
    assert db.execute('SELECT id FROM votes').fetchone() == (identity,)


def test_changed_source_row_refuses_whole_batch(db, tmp_path):
    cycle(db, 'closed')
    first, second = vote(db, 'closed'), vote(db, 'closed')
    archive = tmp_path/'votes'
    C.export_votes(db, archive, cutoff=CUTOFF)
    db.execute('UPDATE votes SET rationale=? WHERE id=?', ('changed',second))
    db.commit()
    with pytest.raises(C.Refused, match='source_row_changed'):
        C.prune(db, archive, dry_run=False)
    assert [r[0] for r in db.execute('SELECT id FROM votes ORDER BY id')] == [first, second]


def test_unknown_encoding_or_corrupt_manifest_refuses_pruning(db, tmp_path):
    _, sha = blob(db, 'orphan')
    archive = tmp_path/'blobs'
    C.export_blobs(db, archive)
    db.execute('CREATE TABLE future_evidence(payload BLOB)')
    for payload in [b'\xff\xfe', E.PREFIX+'{}']:
        db.execute('DELETE FROM future_evidence')
        db.execute('INSERT INTO future_evidence VALUES (?)', (payload,))
        db.commit()
        with pytest.raises(C.Refused):
            C.prune(db, archive, dry_run=False)
        assert db.execute(f'SELECT sha256 FROM {C.BLOBS}').fetchone() == (sha,)


@pytest.mark.parametrize('corruption', ['file', 'manifest', 'count', 'rows_digest'])
def test_archive_corruption_is_refused_before_any_row_or_delete(db, tmp_path, corruption):
    cycle(db, 'closed')
    vote(db, 'closed')
    archive = tmp_path/'votes'
    manifest = C.export_votes(db, archive, cutoff=CUTOFF)
    if corruption == 'file':
        path = archive/manifest['parts'][0]['file']
        path.write_bytes(path.read_bytes()+b'corrupt')
    else:
        if corruption == 'manifest':
            manifest['count'] += 1
        else:
            manifest['parts'][0]['count' if corruption == 'count' else 'rows_sha256'] = 99 if corruption == 'count' else '0'*64
            manifest['manifest_sha256'] = C._sha(C._json({k:v for k,v in manifest.items() if k!='manifest_sha256'}))
        (archive/'manifest.json').write_bytes(C._json(manifest))
    with pytest.raises(C.Refused):
        next(C.read_archive(archive))
    with pytest.raises(C.Refused):
        C.prune(db, archive, dry_run=False)
    assert db.execute('SELECT count(*) FROM votes').fetchone() == (1,)


def test_archive_file_changed_during_second_phase_is_not_pruned(db, tmp_path, monkeypatch):
    blob(db, 'orphan')
    archive = tmp_path/'blobs'
    manifest = C.export_blobs(db, archive)
    original = C.reachable
    def changed(db):
        (archive/manifest['parts'][0]['file']).write_bytes(b'changed after verification')
        return original(db)
    monkeypatch.setattr(C, 'reachable', changed)
    with pytest.raises(C.Refused, match='archive_changed'):
        C.prune(db, archive, dry_run=False)
    assert db.execute(f'SELECT count(*) FROM {C.BLOBS}').fetchone() == (1,)


def test_dry_run_has_no_write_statements_and_limit_is_enforced(db, tmp_path):
    for i in range(4):
        blob(db, str(i))
    archive = tmp_path/'blobs'
    C.export_blobs(db, archive)
    statements = []
    db.set_trace_callback(statements.append)
    assert C.prune(db, archive, limit=2)['eligible'] == 2
    db.set_trace_callback(None)
    assert not any(s.lstrip().upper().startswith(('INSERT','DELETE','UPDATE','CREATE','DROP','ALTER')) for s in statements)
    assert C.prune(db, archive, dry_run=False, limit=2)['deleted'] == 2
    assert db.execute(f'SELECT count(*) FROM {C.BLOBS}').fetchone() == (2,)
    with pytest.raises(C.Refused):
        C.prune(db, archive, dry_run=False, limit=0)


def test_retained_blob_nested_vote_id_pins_vote_and_target_trigger_is_not_bypassed(db, tmp_path):
    cycle(db, 'closed')
    identity = vote(db, 'closed')
    marker, _ = blob(db, json.dumps({'vote_id': identity}))
    db.execute('INSERT INTO brain_events(ts,kind,detail) VALUES (?,?,?)', (OLD,'test',marker))
    db.commit()
    assert C.export_votes(db, tmp_path/'pinned', cutoff=CUTOFF)['count'] == 0
    archive = tmp_path/'blobs'
    C.export_blobs(db, archive)
    db.execute(f"CREATE TRIGGER immutable BEFORE DELETE ON {C.BLOBS} BEGIN SELECT RAISE(ABORT,'immutable'); END")
    db.commit()
    with pytest.raises(C.Refused, match='target_trigger_scope'):
        C.prune(db, archive, dry_run=False)


def test_source_schema_version_changes_and_caller_transactions_are_refused(db, tmp_path):
    blob(db, 'orphan')
    archive = tmp_path/'blobs'
    C.export_blobs(db, archive)
    db.execute('PRAGMA user_version=43')
    with pytest.raises(C.Refused, match='source_schema_changed'):
        C.prune(db, archive, dry_run=False)
    db.execute('BEGIN')
    with pytest.raises(C.Refused, match='caller_transaction_open'):
        C.prune(db, archive)
    db.rollback()
    assert db.execute(f'SELECT count(*) FROM {C.BLOBS}').fetchone() == (1,)


def test_export_failure_does_not_publish_partial_archive_or_change_hot_rows(db, tmp_path):
    db.execute('INSERT INTO brain_events(ts,kind,detail) VALUES (?,?,?)', (OLD,'test',E.PREFIX+'{}'))
    db.commit()
    archive = tmp_path/'refused'
    with pytest.raises(C.Refused):
        C.export_votes(db, archive, cutoff=CUTOFF)
    assert not archive.exists()
    assert not list(tmp_path.glob('.cold-archive-*'))
    assert db.execute('SELECT count(*) FROM brain_events').fetchone() == (1,)


def test_retained_view_literal_and_plain_blob_sha_are_roots(db, tmp_path):
    marker, first = blob(db, 'constant view root')
    _, second = blob(db, 'plain future root')
    db.execute('CREATE VIEW retained_constant AS SELECT '+"'"+marker+"' AS detail")
    db.execute('CREATE TABLE future_roots(reference TEXT)')
    db.execute('INSERT INTO future_roots VALUES (?)', ('blob='+second,))
    db.commit()
    assert C.reachable(db).blobs == {first, second}


@pytest.mark.parametrize('case', ['no_decision','missing_outcome','partial_horizon','mixed_age','unknown_status','bad_chronology'])
def test_unsettled_cycles_do_not_export(db, tmp_path, case):
    cycle(db, 'c')
    identity = vote(db, 'c')
    if case == 'no_decision':
        db.execute("DELETE FROM outcomes WHERE cycle_id='c'")
        db.execute("DELETE FROM decisions WHERE cycle_id='c'")
    elif case == 'missing_outcome':
        db.execute("DELETE FROM outcomes WHERE cycle_id='c'")
    elif case == 'partial_horizon':
        db.execute("UPDATE outcomes SET fwd_ret_4h=NULL")
    elif case == 'mixed_age':
        vote(db, 'c', ts='2026-03-01')
    elif case == 'unknown_status':
        db.execute("UPDATE trades SET status='reconciling'")
    else:
        db.execute("UPDATE trades SET closed_at='2025-01-01'")
    db.commit()
    assert C.export_votes(db, tmp_path/'archive', cutoff=CUTOFF)['count'] == 0
    assert db.execute('SELECT 1 FROM votes WHERE id=?', (identity,)).fetchone()


def test_monthly_partitions_use_utc_order_and_keep_exact_cells(db, tmp_path):
    cycle(db, 'c', action='HOLD', executed=0)
    ids = [vote(db, 'c', ts='2026-01-03T01:00:00+03:00'),
           vote(db, 'c', ts='2026-01-02T23:30:00+00:00'),
           vote(db, 'c', ts='2026-02-01T01:00:00+03:00'),
           vote(db, 'c', ts='2026-02-02T00:00:00Z')]
    db.execute('UPDATE votes SET conviction=? WHERE id=?', (float('inf'),ids[0]))
    db.commit()
    archive = tmp_path/'months'
    manifest = C.export_votes(db, archive, cutoff='2026-03-01')
    assert [p['partition'] for p in manifest['parts']] == ['2026-01','2026-02']
    archived = list(C.read_archive(archive))
    assert [r['id'] for r in archived] == ids
    names = manifest['columns']
    for row in archived:
        original = db.execute('SELECT * FROM votes WHERE id=?', (row['id'],)).fetchone()
        assert C._line([row[n] for n in names]) == C._line(original)


def test_prune_writer_lock_excludes_new_concurrent_roots(db, tmp_path, monkeypatch):
    marker, _ = blob(db, 'orphan')
    archive = tmp_path/'blobs'
    C.export_blobs(db, archive)
    other = sqlite3.connect(tmp_path/'journal.db', timeout=0)
    original = C.reachable
    def locked(connection):
        result = original(connection)
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            other.execute('INSERT INTO brain_events(ts,kind,detail) VALUES (?,?,?)', (OLD,'test',marker))
        other.rollback()
        return result
    monkeypatch.setattr(C, 'reachable', locked)
    try:
        assert C.prune(db, archive, dry_run=False)['deleted'] == 1
    finally:
        other.close()


def test_production_path_guard_refuses_without_opening_any_database():
    class NotAnOpenDatabase:
        def execute(self, sql):
            assert sql == 'PRAGMA database_list'
            return [(0, 'main', '/mnt/luffy-data/LUFFY/data/luffy.db')]
    with pytest.raises(C.Refused, match='production_volume_refused'):
        C.reference_inventory(NotAnOpenDatabase())
