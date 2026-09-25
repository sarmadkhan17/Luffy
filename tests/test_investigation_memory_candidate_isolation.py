"""SDD-STAGE-3-INVESTIGATION-MEMORY-CANDIDATE-ISOLATION-V1: unrelated terminal
investigations (no update, or a latest status owned by another path) are excluded
before the 256-row candidate bound, so they can never starve an eligible measured or
unassessable source. Ordering, bounds, refusals, chronology retry and outputs are
unchanged. Synthetic; no network, LLM or trading."""
import json
from dataclasses import asdict, replace

import pytest

from trader.cognition import investigation as I, memory as M
from trader.observability import investigation as C, memory as S
from tests.test_market_investigation import prefix, targets  # noqa: F401 (fixture)
from tests.test_investigation_unassessable_memory import store, v2_closure
from tests.test_investigation_measured_memory_hardening import measured, _put, _raw as _measured_raw
from tests.test_investigation_unassessable_memory_hardening import _future, _raw as _unassessable_raw

EMPTY = {'added': 0, 'refused': []}
NOISE = 300                                                             # > the 256-row candidate bound


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _noise(db, n=NOISE, statuses=('unresolved', 'assessed', None), start=0):
    """n unrelated terminal cases created before any real source: latest status owned by
    no memory path under test (None: no update at all). Neither path records them."""
    for i in range(n):
        iid, status = f'noise_{start + i:05d}', statuses[i % len(statuses)]
        db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)', (iid, f'ep_{iid}', 'N/USDT', start + i, 1, '{}'))
        if status is not None:
            db.execute('INSERT INTO updates VALUES (?,?,?,?)',
                       (f'ev_{iid}', iid, 1, json.dumps({'evidence': {'status': status}})))


def _count(db, table):
    return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def _rows(db, sql):
    return [tuple(r) for r in db.execute(sql)]


def _unassessable_record(inv, update, bars, now):
    return I.encode(asdict(M.verified_unassessable(inv, update, bars, now)))


# 1 ── >256 unrelated terminal rows before a valid measured source

def test_unrelated_prefix_cannot_starve_a_valid_measured_source(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:
        _noise(db)
        store(db, inv, bars, [update])                                  # created_ms = registered_ms: last
        result = S.ingest(db, now)
        assert (result['added'], result['refused']) == (1, [])
        assert result['unassessable'] == EMPTY
        assert _rows(db, 'SELECT * FROM memory_cases') == [
            (inv.investigation_id, I.encode(asdict(M.verified_case(inv, update, bars, now))))]
        for table in ('memory_measured_refused', 'memory_unassessable', 'memory_unassessable_refused'):
            assert _count(db, table) == 0                               # noise gets no disposition


# 2 ── >256 unrelated terminal rows before a valid unassessable source

def test_unrelated_prefix_cannot_starve_a_valid_unassessable_source(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'u.db') as db:
        _noise(db)
        store(db, inv, snap.bars, [update])
        result = S.ingest(db, now)
        assert (result['added'], result['refused'], result['unassessable']) == (0, [], {'added': 1, 'refused': []})
        assert _rows(db, 'SELECT * FROM memory_unassessable') == [
            (inv.investigation_id, _unassessable_record(inv, update, snap.bars, now))]
        for table in ('memory_cases', 'memory_measured_refused', 'memory_unassessable_refused'):
            assert _count(db, table) == 0


# 3 ── eligible ordering stays (created_ms, id), with ties, around interleaved noise

def _interleaved(db, inv, update, raw, n=40):
    """n eligible clones on shared/tied created_ms, each preceded by unrelated noise."""
    ids = []
    for i in range(n):
        _noise(db, n=9, start=i * 10)
        iid = f'{inv.investigation_id}_{(n - i):04d}'                   # id order opposes insertion order
        _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update, raw(iid), created_ms=(i // 2) * 10 + 9)
        ids.append(((i // 2) * 10 + 9, iid))
    return [iid for _, iid in sorted(ids)]


def test_measured_eligible_order_is_deterministic(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        expected = _interleaved(db, inv, update, lambda iid: _measured_raw(update, 'no_next_action'))
        first, second = S.ingest(db, update.observed_ms + 1), S.ingest(db, update.observed_ms + 2)
        assert [r['investigation_id'] for r in first['refused']] == expected[:32]
        assert [r['investigation_id'] for r in second['refused']] == expected[32:]


def test_unassessable_eligible_order_is_deterministic(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    with C.ledger(tmp_path / 'u.db') as db:
        expected = _interleaved(db, inv, update, lambda iid: I.encode(asdict(_future(update, iid, update.observed_ms))))
        first = S.ingest(db, update.observed_ms + 1)['unassessable']
        second = S.ingest(db, update.observed_ms + 2)['unassessable']
        assert [r['investigation_id'] for r in first['refused']] == expected[:32]
        assert [r['investigation_id'] for r in second['refused']] == expected[32:]


# 4 ── malformed/refused sources plus noise still cannot starve later valid sources

def test_noise_and_malformed_measured_prefix_cannot_starve_valid_case(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:
        _noise(db)
        bad = _measured_raw(update, 'invalid_json')                     # status unreadable: measured owns it
        for i in range(40):
            iid = f'{inv.investigation_id}_{i:04d}'
            _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update, bad, created_ms=NOISE + i)
        store(db, inv, bars, [update])
        first = S.ingest(db, now)
        assert (first['added'], len(first['refused'])) == (0, 32)
        assert first['unassessable'] == EMPTY                           # unreadable rows never unassessable
        second = S.ingest(db, now + 1)
        assert (second['added'], len(second['refused'])) == (1, 8)
        assert {r['reason'] for r in first['refused'] + second['refused']} == {'measured_malformed_source'}
        assert S.ingest(db, now + 2)['refused'] == []
        assert _count(db, 'memory_measured_refused') == 40 and _count(db, 'memory_unassessable_refused') == 0


def test_noise_and_malformed_unassessable_prefix_cannot_starve_valid_closure(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'u.db') as db:
        _noise(db)
        bad = _unassessable_raw(update, 'no_next_action')
        for i in range(40):
            iid = f'{inv.investigation_id}_{i:04d}'
            _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update, bad, created_ms=NOISE + i)
        for i in range(20):                                             # unreadable: measured refuses them
            iid = f'bad_json_{i:04d}'
            _put(db, iid, '{}', update, '{"evidence": {"status": "not_testable"', created_ms=NOISE + 40 + i)
        store(db, inv, snap.bars, [update])
        first = S.ingest(db, now)
        assert (first['added'], len(first['refused'])) == (0, 20)
        assert (first['unassessable']['added'], len(first['unassessable']['refused'])) == (0, 32)
        second = S.ingest(db, now + 1)['unassessable']
        assert (second['added'], len(second['refused'])) == (1, 8)
        assert S.ingest(db, now + 2)['unassessable'] == EMPTY
        assert _rows(db, 'SELECT source_id FROM memory_unassessable') == [(inv.investigation_id,)]
        assert _count(db, 'memory_unassessable_refused') == 40 and _count(db, 'memory_measured_refused') == 20


# 5 ── future (retryable) chronology behind noise becomes eligible after clock recovery

def test_measured_chronology_retry_behind_noise(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        _noise(db)
        store(db, inv, bars, [update])
        early = S.ingest(db, update.observed_ms - 1)
        assert early['refused'] == [{'investigation_id': inv.investigation_id, 'reason': 'memory_invalid_chronology'}]
        assert _count(db, 'memory_measured_refused') == 0
        assert S.ingest(db, update.observed_ms)['added'] == 1


def test_unassessable_future_observation_behind_noise(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    with C.ledger(tmp_path / 'u.db') as db:
        _noise(db)
        store(db, inv, snap.bars, [update])
        assert S.ingest(db, update.observed_ms - 1)['unassessable'] == EMPTY
        assert _count(db, 'memory_unassessable_refused') == 0
        assert S.ingest(db, update.observed_ms)['unassessable'] == {'added': 1, 'refused': []}


# 6 ── outputs are byte-identical with and without the unrelated rows

TABLES = ('memory_cases', 'memory_measured_refused', 'memory_unassessable', 'memory_unassessable_refused')


def _outputs(path, seed, now, noise):
    with C.ledger(path) as db:
        if noise: _noise(db)
        seed(db)
        result = S.ingest(db, now)
        tables = {t: _rows(db, f'SELECT * FROM {t} ORDER BY 1') for t in TABLES}
    db.close()
    return result, tables


@pytest.mark.parametrize('kind', ['measured', 'unassessable'])
def test_outputs_unchanged_by_unrelated_rows(tmp_path, prefix, kind):
    if kind == 'measured':
        inv, bars, update = measured(prefix)
    else:
        snap, inv, update = v2_closure(prefix)
        bars = snap.bars
    now = update.observed_ms + 1
    seed = lambda db: store(db, inv, bars, [update])                   # noqa: E731
    clean = _outputs(tmp_path / 'clean.db', seed, now, noise=False)
    noisy = _outputs(tmp_path / 'noisy.db', seed, now, noise=True)
    assert clean == noisy
    assert sum(len(rows) for rows in clean[1].values()) == 1


# Duplicate keys ── json.loads keeps the last value; the pre-bound predicate must read
# them the same way, or these undisposed rows would fill every later window

DUPLICATES = [
    '{"evidence":{"status":"measured","status":"unresolved"}}',
    '{"evidence":{"status":"not_testable","status":"unresolved"}}',
    '{"evidence":{"status":"measured"},"evidence":{"status":"unresolved"}}',
    '{"evidence":{"status":"not_testable"},"evidence":{"status":"assessed"}}',
    '{"evidence":{"status":"measured","st\\u0061tus":"unresolved"}}',
    '{"evidence":{"status":"not_testable","st\\u0061tus":"unresolved"}}',
    '{"evid\\u0065nce":{"status":"measured"},"evidence":{"status":"unresolved"}}',
]


def _duplicate_noise(db, n=NOISE):
    for i in range(n):
        iid = f'dup_{i:05d}'
        db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)', (iid, f'ep_{iid}', 'N/USDT', i, 1, '{}'))
        db.execute('INSERT INTO updates VALUES (?,?,?,?)', (f'ev_{iid}', iid, 1, DUPLICATES[i % len(DUPLICATES)]))


def test_duplicate_payloads_read_last_key_wins():
    assert [json.loads(p)['evidence']['status'] for p in DUPLICATES] == [
        'unresolved', 'unresolved', 'unresolved', 'assessed', 'unresolved', 'unresolved', 'unresolved']


def test_duplicate_key_prefix_cannot_starve_valid_measured_source_across_runs(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1

    def seed(db):
        _duplicate_noise(db)
        store(db, inv, bars, [update])
        return S.ingest(db, now)
    first = _reopen(path, seed)
    assert (first['added'], first['refused'], first['unassessable']) == (1, [], EMPTY)
    for t in (now + 1, now + 2):
        again = _reopen(path, lambda db: S.ingest(db, t))
        assert (again['added'], again['refused'], again['unassessable']) == (0, [], EMPTY)
    assert _reopen(path, lambda db: _rows(db, 'SELECT * FROM memory_cases')) == [
        (inv.investigation_id, I.encode(asdict(M.verified_case(inv, update, bars, now))))]
    for table in TABLES[1:]:
        assert _reopen(path, lambda db: _count(db, table)) == 0         # duplicates: no disposition


def test_duplicate_key_prefix_cannot_starve_valid_unassessable_source_across_runs(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    path, now = tmp_path / 'u.db', update.observed_ms + 1

    def seed(db):
        _duplicate_noise(db)
        store(db, inv, snap.bars, [update])
        return S.ingest(db, now)
    first = _reopen(path, seed)
    assert (first['added'], first['refused'], first['unassessable']) == (0, [], {'added': 1, 'refused': []})
    for t in (now + 1, now + 2):
        again = _reopen(path, lambda db: S.ingest(db, t))
        assert (again['added'], again['refused'], again['unassessable']) == (0, [], EMPTY)
    assert _reopen(path, lambda db: _rows(db, 'SELECT * FROM memory_unassessable')) == [
        (inv.investigation_id, _unassessable_record(inv, update, snap.bars, now))]
    for table in ('memory_cases', 'memory_measured_refused', 'memory_unassessable_refused'):
        assert _reopen(path, lambda db: _count(db, table)) == 0


def test_status_function_survives_repeat_calls_with_an_active_statement(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        _noise(db, n=5)
        store(db, inv, bars, [update])
        S.ingest(db, update.observed_ms - 1)                            # registers the function
        active = db.execute('SELECT id FROM cases ORDER BY id')
        active.fetchone()                                               # statement left open
        assert S.ingest(db, update.observed_ms)['added'] == 1           # no re-registration error
        assert active.fetchone() is not None


def _reopen(path, fn):
    with C.ledger(path) as db:
        result = fn(db)
    db.close()
    return result


# A status SQLite cannot encode (lone surrogate) is just another path's status: it
# neither interrupts ingestion nor blocks a later valid source

SURROGATE = '{"evidence":{"status":"\\ud800"}}'


def test_surrogate_status_parses_in_python_only_as_an_unrelated_status():
    assert json.loads(SURROGATE)['evidence']['status'] == '\ud800'
    assert S._status(SURROGATE) == '\ud800' and S._sql_status(SURROGATE) == 'other'


@pytest.mark.parametrize('kind', ['measured', 'unassessable'])
def test_surrogate_status_cannot_interrupt_ingestion_or_block_valid_source(tmp_path, prefix, kind):
    if kind == 'measured':
        inv, bars, update = measured(prefix)
    else:
        snap, inv, update = v2_closure(prefix)
        bars = snap.bars
    path, now = tmp_path / 'm.db', update.observed_ms + 1

    def seed(db):
        for i in range(NOISE):
            iid = f'sur_{i:05d}'
            db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)', (iid, f'ep_{iid}', 'N/USDT', i, 1, '{}'))
            db.execute('INSERT INTO updates VALUES (?,?,?,?)', (f'ev_{iid}', iid, 1, SURROGATE))
        store(db, inv, bars, [update])
        return S.ingest(db, now)
    first = _reopen(path, seed)
    added = (first['added'], first['unassessable']['added'])
    assert added == ((1, 0) if kind == 'measured' else (0, 1))
    assert first['refused'] == [] and first['unassessable']['refused'] == []
    for t in (now + 1, now + 2):
        again = _reopen(path, lambda db: S.ingest(db, t))
        assert (again['added'], again['refused'], again['unassessable']) == (0, [], EMPTY)
    table = 'memory_cases' if kind == 'measured' else 'memory_unassessable'
    assert _reopen(path, lambda db: _rows(db, f'SELECT source_id FROM {table}')) == [(inv.investigation_id,)]
    for other in ('memory_measured_refused', 'memory_unassessable_refused'):
        assert _reopen(path, lambda db: _count(db, other)) == 0
