"""SDD-STAGE-3-MEASURED-MEMORY-MALFORMED-SOURCE-HARDENING-V1: a malformed measured
source fails closed as a recorded refusal instead of crashing memory ingestion, and
never starves later valid measured cases. Valid measured records are unchanged.
Synthetic; no network, LLM or trading."""
import json
from dataclasses import asdict, replace

import pytest

from trader.cognition import investigation as I, memory as M
from trader.observability import investigation as C, memory as S
from tests.test_market_investigation import prefix, targets  # noqa: F401 (fixture)
from tests.test_investigation_memory import resolved
from tests.test_investigation_unassessable_memory import store

MALFORMED = 'measured_malformed_source'
EMPTY = {'added': 0, 'refused': []}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def measured(prefix):
    _, snap, inv = prefix
    bars, update = resolved(inv)
    return inv, (*snap.bars, *bars), update


def _raw(update, change):
    raw = json.loads(I.encode(asdict(update)))
    if change == 'invalid_json': return '{"evidence": {"status": "measured"'
    if change == 'top_level_list': return json.dumps([raw])
    if change == 'no_next_action': del raw['next_action']
    if change == 'evidence_text': raw['evidence'] = 'measured'
    if change == 'target_versions_int': raw['evidence']['target_versions'] = 7
    if change == 'extra_evidence_key': raw['evidence']['extra'] = 1
    if change == 'observed_missing': del raw['evidence']['observed_ms']
    if change == 'observed_null': raw['evidence']['observed_ms'] = None
    if change == 'observed_text': raw['evidence']['observed_ms'] = str(update.observed_ms)
    if change == 'observed_real': raw['evidence']['observed_ms'] = float(update.observed_ms)
    if change == 'observed_bool': raw['evidence']['observed_ms'] = True
    if change == 'as_of_null': raw['evidence']['as_of_ms'] = None
    if change == 'as_of_missing': del raw['evidence']['as_of_ms']
    if change == 'as_of_text': raw['evidence']['as_of_ms'] = str(update.evidence.as_of_ms)
    if change == 'as_of_real': raw['evidence']['as_of_ms'] = float(update.evidence.as_of_ms)
    if change == 'as_of_bool': raw['evidence']['as_of_ms'] = True
    return json.dumps(raw)


def _put(db, iid, inv_payload, update, raw, created_ms):
    db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)', (iid, f'ep_{iid}', 'X/USDT', created_ms,
               update.observed_ms, inv_payload))
    db.execute('INSERT INTO updates VALUES (?,?,?,?)', (f'ev_{iid}', iid, update.observed_ms, raw))


CHANGES = ['invalid_json', 'top_level_list', 'no_next_action', 'evidence_text', 'target_versions_int',
           'extra_evidence_key', 'observed_missing', 'observed_null', 'observed_text', 'observed_real',
           'observed_bool', 'as_of_null', 'as_of_missing', 'as_of_text', 'as_of_real', 'as_of_bool']


# 1, 2, 3, 5 ── malformed latest update: no crash, one recorded refusal, never retried

@pytest.mark.parametrize('change', CHANGES)
def test_malformed_latest_update_is_refused_once_not_raised(tmp_path, prefix, change):
    inv, bars, update = measured(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, bars, [])
        db.execute('UPDATE cases SET terminal_ms=? WHERE id=?', (update.observed_ms, inv.investigation_id))
        db.execute('INSERT INTO updates VALUES (?,?,?,?)', (update.event_id, inv.investigation_id,
                   update.observed_ms, _raw(update, change)))
        report = S.ingest(db, now)
        assert (report['added'], report['refused']) == (0, [{'investigation_id': inv.investigation_id,
                                                             'reason': MALFORMED}])
        assert report['unassessable'] == EMPTY and report['assessed'] == EMPTY
        assert [tuple(r) for r in db.execute('SELECT * FROM memory_measured_refused')] == [
            (inv.investigation_id, MALFORMED)]
        assert db.execute('SELECT COUNT(*) FROM memory_cases').fetchone()[0] == 0
        again = S.ingest(db, now + 1)                                   # terminal disposition
        assert (again['added'], again['refused']) == (0, [])
        assert db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0] == 1


@pytest.mark.parametrize('change', ['no_state', 'bad_dimensions', 'no_alternatives', 'registered_real',
                                    'registered_text', 'registered_bool', 'registered_null'])
def test_malformed_investigation_payload_is_refused_not_raised(tmp_path, prefix, change):
    inv, bars, update = measured(prefix)
    raw = json.loads(I.encode(asdict(inv)))
    if change == 'no_state': del raw['state']
    if change == 'bad_dimensions': raw['state']['dimensions'] = [1, 2]
    if change == 'no_alternatives': del raw['alternatives']
    if change == 'registered_real': raw['registered_ms'] = float(raw['registered_ms'])
    if change == 'registered_text': raw['registered_ms'] = str(raw['registered_ms'])
    if change == 'registered_bool': raw['registered_ms'] = True
    if change == 'registered_null': raw['registered_ms'] = None
    payload = json.dumps(raw)
    with C.ledger(tmp_path / 'm.db') as db:
        _put(db, inv.investigation_id, payload, update, I.encode(asdict(update)), created_ms=0)
        report = S.ingest(db, update.observed_ms + 1)
        assert report['refused'] == [{'investigation_id': inv.investigation_id, 'reason': MALFORMED}]
        assert db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0] == 1


def test_malformed_input_bar_is_refused_not_raised(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, bars, [update])
        db.execute("UPDATE inputs SET payload='{\"version_id\": 1}'")
        report = S.ingest(db, update.observed_ms + 1)
        assert report['refused'] == [{'investigation_id': inv.investigation_id, 'reason': MALFORMED}]


# 4, 5, 6 ── a malformed prefix beyond the per-run bound never starves a valid case

def test_malformed_prefix_does_not_starve_a_valid_measured_case(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    bad = _raw(update, 'no_next_action')
    with C.ledger(tmp_path / 'm.db') as db:
        for i in range(40):
            iid = f'{inv.investigation_id}_{i:04d}'
            _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update, bad, created_ms=i)
        store(db, inv, bars, [update])                                  # created_ms = registered_ms: last
        now = update.observed_ms + 1
        first = S.ingest(db, now)
        assert (first['added'], len(first['refused'])) == (0, 32)      # per-run bound kept
        assert {r['reason'] for r in first['refused']} == {MALFORMED}
        second = S.ingest(db, now + 1)
        assert (second['added'], len(second['refused'])) == (1, 8)
        third = S.ingest(db, now + 2)
        assert (third['added'], third['refused']) == (0, [])
        assert db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0] == 40
        [(sid, payload)] = db.execute('SELECT * FROM memory_cases').fetchall()
        assert sid == inv.investigation_id
        assert payload == I.encode(asdict(M.verified_case(inv, update, bars, now + 1)))


# Retry policy ── only chronology stays retryable; deterministic verification
# refusals keep their original reason and are recorded once

def test_wellformed_future_observation_is_still_retried_not_recorded(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, bars, [update])
        early = S.ingest(db, update.observed_ms - 1)
        assert early['refused'] == [{'investigation_id': inv.investigation_id,
                                     'reason': 'memory_invalid_chronology'}]
        assert db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0] == 0
        assert S.ingest(db, update.observed_ms)['added'] == 1


def test_forged_wellformed_source_keeps_its_original_reason_and_is_recorded_once(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    forged = replace(update, assessment=tuple((n, 'incompatible') for n, _ in update.assessment))
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, bars, [forged])
        assert S.ingest(db, update.observed_ms + 1)['refused'] == [{'investigation_id': inv.investigation_id,
                                                                    'reason': 'memory_assessment_mismatch'}]
        again = S.ingest(db, update.observed_ms + 2)
        assert (again['added'], again['refused']) == (0, [])
        assert [tuple(r) for r in db.execute('SELECT * FROM memory_measured_refused')] == [
            (inv.investigation_id, 'memory_assessment_mismatch')]


def test_retention_clears_measured_refusals(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        _put(db, inv.investigation_id, I.encode(asdict(inv)), update, '{', created_ms=0)
        S.ingest(db, update.observed_ms + 1)
        db.execute('DELETE FROM cases'); S.retain(db)
        assert db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0] == 0


# Query safety ── a malformed cases.payload never crashes ingest(), including the
# assessed candidate query; the measured path still refuses it deterministically.

@pytest.mark.parametrize('bad_payload', ['{', '{"primary_trigger": ', 'not json'])
def test_malformed_case_payload_cannot_crash_ingest(tmp_path, prefix, bad_payload):
    inv, bars, update = measured(prefix)
    bad = f'{inv.investigation_id}_bad'
    with C.ledger(tmp_path / 'm.db') as db:
        _put(db, bad, bad_payload, update, I.encode(asdict(update)), created_ms=0)   # first in order
        store(db, inv, bars, [update])                                  # later valid measured case
        now = update.observed_ms + 1
        report = S.ingest(db, now)
        assert report['added'] == 1
        assert report['refused'] == [{'investigation_id': bad, 'reason': MALFORMED}]
        assert report['unassessable'] == EMPTY and report['assessed'] == EMPTY
        assert [tuple(r) for r in db.execute('SELECT * FROM memory_measured_refused')] == [(bad, MALFORMED)]
        [(sid, payload)] = db.execute('SELECT * FROM memory_cases').fetchall()
        assert sid == inv.investigation_id
        assert payload == I.encode(asdict(M.verified_case(inv, update, bars, now)))
        for later in (now + 1, now + 2):                                # retry / restart bounded
            again = S.ingest(db, later)
            assert (again['added'], again['refused'], again['assessed']) == (0, [], EMPTY)
        assert db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM memory_assessed_refused').fetchone()[0] == 0


def test_malformed_case_and_update_payloads_cannot_crash_assessed_query(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        _put(db, 'bad_both', '{', update, '{', created_ms=0)
        report = S.ingest(db, update.observed_ms + 1)
        assert report['refused'] == [{'investigation_id': 'bad_both', 'reason': MALFORMED}]
        assert report['assessed'] == EMPTY


# Astra blocker 1 ── a malformed target_versions entry is a deserialization defect,
# refused once inside the guarded boundary, never a retried semantic ValueError.

def _targets(update, versions):
    raw = json.loads(I.encode(asdict(update)))
    raw['evidence']['target_versions'] = versions
    return json.dumps(raw)


def _reopen(path, fn):
    with C.ledger(path) as db:
        result = fn(db)
    db.close()
    if isinstance(result, list):
        result = [tuple(r) for r in result]
    return result


@pytest.mark.parametrize('versions', [[['bad']], [['X/USDT', 1]], [['X/USDT', 1, 'v', 'extra']],
                                      [['X/USDT', '1', 'v']], [['X/USDT', True, 'v']], [[1, 2, 3]],
                                      [['X/USDT', 1, ['v']]], ['abc']])
def test_malformed_target_versions_entry_is_refused_once_across_restart(tmp_path, prefix, versions):
    inv, bars, update = measured(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1

    def seed(db):
        _put(db, inv.investigation_id, I.encode(asdict(inv)), update, _targets(update, versions), created_ms=0)
        return S.ingest(db, now)
    report = _reopen(path, seed)
    assert (report['added'], report['refused']) == (0, [{'investigation_id': inv.investigation_id,
                                                         'reason': MALFORMED}])
    for later in (now + 1, now + 2):                                    # restart never retries
        again = _reopen(path, lambda db: S.ingest(db, later))
        assert (again['added'], again['refused']) == (0, [])
    assert _reopen(path, lambda db: db.execute('SELECT * FROM memory_measured_refused').fetchall()) == [
        (inv.investigation_id, MALFORMED)]


def test_malformed_target_versions_prefix_cannot_starve_valid_case_across_restart(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1
    bad = _targets(update, [['bad']])

    def seed(db):
        for i in range(40):
            iid = f'{inv.investigation_id}_{i:04d}'
            _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update, bad, created_ms=i)
        store(db, inv, bars, [update])                                  # created last
        early = S.ingest(db, update.observed_ms - 1)                    # valid case too early here
        return early
    first = _reopen(path, seed)
    assert (first['added'], len(first['refused'])) == (0, 32)
    assert {r['reason'] for r in first['refused']} == {MALFORMED}
    second = _reopen(path, lambda db: S.ingest(db, now))
    assert second['added'] == 1
    assert second['refused'] == [{'investigation_id': f'{inv.investigation_id}_{i:04d}', 'reason': MALFORMED}
                                 for i in range(32, 40)]
    third = _reopen(path, lambda db: S.ingest(db, now + 1))
    assert (third['added'], third['refused']) == (0, [])
    rows = _reopen(path, lambda db: (db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0],
                                     [tuple(r) for r in db.execute('SELECT source_id,payload FROM memory_cases')]))
    assert rows[0] == 40
    assert rows[1] == [(inv.investigation_id, I.encode(asdict(M.verified_case(inv, update, bars, now))))]


def test_temporary_chronology_refusal_stays_retryable_across_restart(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    path = tmp_path / 'm.db'

    def seed(db):
        store(db, inv, bars, [update])
        return S.ingest(db, update.observed_ms - 1)
    assert _reopen(path, seed)['refused'] == [{'investigation_id': inv.investigation_id,
                                               'reason': 'memory_invalid_chronology'}]
    assert _reopen(path, lambda db: db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0]) == 0
    assert _reopen(path, lambda db: S.ingest(db, update.observed_ms))['added'] == 1


def test_wellformed_wrong_target_version_keeps_semantic_reason_and_is_recorded_once(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    [(sym, t, _), *rest] = update.evidence.target_versions
    wrong = [[sym, t, 'not_a_version'], *map(list, rest)]
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, bars, [])
        db.execute('UPDATE cases SET terminal_ms=? WHERE id=?', (update.observed_ms, inv.investigation_id))
        db.execute('INSERT INTO updates VALUES (?,?,?,?)', (update.event_id, inv.investigation_id,
                   update.observed_ms, _targets(update, wrong)))
        assert S.ingest(db, update.observed_ms + 1)['refused'] == [{'investigation_id': inv.investigation_id,
                                                                    'reason': 'memory_terminal_replay_mismatch'}]
        again = S.ingest(db, update.observed_ms + 2)
        assert (again['added'], again['refused']) == (0, [])
        assert [tuple(r) for r in db.execute('SELECT * FROM memory_measured_refused')] == [
            (inv.investigation_id, 'memory_terminal_replay_mismatch')]


# Astra blocker 2 ── numeric overflow in InputBar/Candle construction is a malformed
# source: refused once, never raised, never retried, and later valid cases still ingest.

@pytest.mark.parametrize('field', ['open', 'high', 'volume'])
def test_overflowing_input_bar_is_refused_once_and_valid_case_ingests(tmp_path, prefix, field):
    inv, bars, update = measured(prefix)
    path, now, bad = tmp_path / 'm.db', update.observed_ms + 1, f'{inv.investigation_id}_overflow'
    raw_bar = json.loads(I.encode(asdict(bars[0])))
    raw_bar['version_id'] = 'overflow_version'
    raw_bar['candle'][field] = 10 ** 400                                # float() overflows

    def seed(db):
        _put(db, bad, I.encode(asdict(replace(inv, investigation_id=bad))), update,
             I.encode(asdict(update)), created_ms=0)                    # first in order
        db.execute('INSERT INTO inputs VALUES (?,?)', ('overflow_version', json.dumps(raw_bar)))
        db.execute('INSERT INTO case_inputs VALUES (?,?)', (bad, 'overflow_version'))
        store(db, inv, bars, [update])                                  # later valid measured case
        return S.ingest(db, now)
    report = _reopen(path, seed)
    assert report['added'] == 1
    assert report['refused'] == [{'investigation_id': bad, 'reason': MALFORMED}]
    for later in (now + 1, now + 2):                                    # restart never retries
        again = _reopen(path, lambda db: S.ingest(db, later))
        assert (again['added'], again['refused']) == (0, [])
    refusals, cases = _reopen(path, lambda db: ([tuple(r) for r in db.execute('SELECT * FROM memory_measured_refused')],
                                                [tuple(r) for r in db.execute('SELECT source_id,payload FROM memory_cases')]))
    assert refusals == [(bad, MALFORMED)]
    assert cases == [(inv.investigation_id, I.encode(asdict(M.verified_case(inv, update, bars, now))))]


# Astra blocker 3 ── a non-int evidence.as_of_ms (a float passes comparisons, then
# replay raises invalid_observation_time) is malformed, refused once, never retried.

def test_malformed_as_of_prefix_cannot_starve_valid_case_across_restart(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1
    bad = _raw(update, 'as_of_real')

    def seed(db):
        for i in range(40):
            iid = f'{inv.investigation_id}_{i:04d}'
            _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update, bad, created_ms=i)
        store(db, inv, bars, [update])                                  # created last
        return S.ingest(db, now)
    first = _reopen(path, seed)                                         # bounded batch
    assert first['added'] == 0
    assert first['refused'] == [{'investigation_id': f'{inv.investigation_id}_{i:04d}', 'reason': MALFORMED}
                                for i in range(32)]
    second = _reopen(path, lambda db: S.ingest(db, now + 1))
    assert second['added'] == 1
    assert second['refused'] == [{'investigation_id': f'{inv.investigation_id}_{i:04d}', 'reason': MALFORMED}
                                 for i in range(32, 40)]
    for later in (now + 2, now + 3):                                    # restart never retries
        again = _reopen(path, lambda db: S.ingest(db, later))
        assert (again['added'], again['refused']) == (0, [])
    count, cases = _reopen(path, lambda db: (db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0],
                                             [tuple(r) for r in db.execute('SELECT source_id,payload FROM memory_cases')]))
    assert count == 40
    assert cases == [(inv.investigation_id, I.encode(asdict(M.verified_case(inv, update, bars, now + 1))))]


# Astra blocker 4 ── a non-int investigation registered_ms (a float survives
# deserialization, then replay raises invalid_registration) is malformed, refused
# once in bounded batches, never retried, and never starves a later valid case.

def test_malformed_registered_prefix_cannot_starve_valid_case_across_restart(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1

    def seed(db):
        for i in range(40):
            iid = f'{inv.investigation_id}_{i:04d}'
            raw = json.loads(I.encode(asdict(replace(inv, investigation_id=iid))))
            raw['registered_ms'] = float(raw['registered_ms'])
            _put(db, iid, json.dumps(raw), update, I.encode(asdict(update)), created_ms=i)
        store(db, inv, bars, [update])                                  # created last
        return S.ingest(db, now)
    first = _reopen(path, seed)                                         # bounded batch
    assert first['added'] == 0
    assert first['refused'] == [{'investigation_id': f'{inv.investigation_id}_{i:04d}', 'reason': MALFORMED}
                                for i in range(32)]
    second = _reopen(path, lambda db: S.ingest(db, now + 1))
    assert second['added'] == 1
    assert second['refused'] == [{'investigation_id': f'{inv.investigation_id}_{i:04d}', 'reason': MALFORMED}
                                 for i in range(32, 40)]
    for later in (now + 2, now + 3):                                    # restart never retries
        again = _reopen(path, lambda db: S.ingest(db, later))
        assert (again['added'], again['refused']) == (0, [])
    count, cases = _reopen(path, lambda db: (db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0],
                                             [tuple(r) for r in db.execute('SELECT source_id,payload FROM memory_cases')]))
    assert count == 40
    assert cases == [(inv.investigation_id, I.encode(asdict(M.verified_case(inv, update, bars, now + 1))))]


# Astra approved retry policy ── the 14 type mutations that verification refuses
# deterministically are recorded once with their original reason, not retried.

TARGET_KEYS = [('measurement', 'target_keys', i, 1) for i in range(5)]
MUTATIONS = [
    ('inv', ('measurement', 'baseline_mean'), True, 'memory_frozen_registration_mismatch'),
    ('inv', ('measurement', 'baseline_scale'), True, 'memory_frozen_registration_mismatch'),
    ('inv', ('measurement', 'deadline_ms'), True, 'memory_frozen_registration_mismatch'),
    ('inv', ('measurement', 'expires_ms'), True, 'memory_frozen_registration_mismatch'),
    *[('inv', k, True, 'memory_frozen_registration_mismatch') for k in TARGET_KEYS],
    ('inv', ('measurement', 'threshold'), True, 'memory_frozen_registration_mismatch'),
    ('inv', ('state', 'as_of_ms'), float, 'memory_frozen_registration_mismatch'),
    ('inv', ('state', 'as_of_ms'), True, 'invalid_baseline_prefix'),
    ('update', ('evidence', 'available_ms'), True, 'memory_terminal_replay_mismatch'),
    ('update', ('evidence', 'score'), True, 'memory_terminal_replay_mismatch'),
]


def _mutate(obj, path, value):
    raw = json.loads(I.encode(asdict(obj)))
    node = raw
    for p in path[:-1]: node = node[p]
    node[path[-1]] = float(node[path[-1]]) if value is float else value
    return json.dumps(raw)


@pytest.mark.parametrize('which,path,value,reason', MUTATIONS, ids=lambda v: str(v))
def test_type_mutation_is_recorded_once_with_original_reason_across_restart(tmp_path, prefix, which, path,
                                                                           value, reason):
    inv, bars, update = measured(prefix)
    path_db, now = tmp_path / 'm.db', update.observed_ms + 1
    assert len(MUTATIONS) == 14

    def seed(db):
        inv_raw = _mutate(inv, path, value) if which == 'inv' else I.encode(asdict(inv))
        up_raw = _mutate(update, path, value) if which == 'update' else I.encode(asdict(update))
        _put(db, inv.investigation_id, inv_raw, update, up_raw, created_ms=0)
        C._save_inputs(db, inv.investigation_id, bars, {b.version_id for b in bars})
        return S.ingest(db, now)
    report = _reopen(path_db, seed)
    assert (report['added'], report['refused']) == (0, [{'investigation_id': inv.investigation_id, 'reason': reason}])
    for later in (now + 1, now + 2):                                    # restart never retries
        again = _reopen(path_db, lambda db: S.ingest(db, later))
        assert (again['added'], again['refused']) == (0, [])
    assert _reopen(path_db, lambda db: db.execute('SELECT * FROM memory_measured_refused').fetchall()) == [
        (inv.investigation_id, reason)]


def test_mixed_malformed_and_forged_prefix_cannot_starve_valid_case_across_restart(tmp_path, prefix):
    inv, bars, update = measured(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1
    malformed = _raw(update, 'as_of_real')
    ids = [f'{inv.investigation_id}_{i:04d}' for i in range(40)]
    forged = lambda iid: I.encode(asdict(replace(update, investigation_id=iid)))   # well-formed, forged join
    reason = lambda i: MALFORMED if i % 2 else 'memory_frozen_registration_mismatch'

    def seed(db):
        for i, iid in enumerate(ids):
            _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update,
                 malformed if i % 2 else forged(iid), created_ms=i)
            C._save_inputs(db, iid, bars, {b.version_id for b in bars})
        store(db, inv, bars, [update])                                  # created last
        return S.ingest(db, update.observed_ms - 1)                     # valid case not yet due
    early = lambda iid: {'investigation_id': iid, 'reason': 'memory_invalid_chronology'}
    disposed = lambda i: {'investigation_id': ids[i], 'reason': reason(i)}
    first = _reopen(path, seed)                                         # bounded batch, not yet due
    assert first['added'] == 0                                          # malformed: recorded; forged: retryable
    assert first['refused'] == [disposed(i) if i % 2 else early(ids[i]) for i in range(32)]
    second = _reopen(path, lambda db: S.ingest(db, update.observed_ms - 1))
    assert second['added'] == 0
    assert second['refused'] == ([early(ids[i]) for i in range(0, 32, 2)]
                                 + [disposed(i) if i % 2 else early(ids[i]) for i in range(32, 40)]
                                 + [early(inv.investigation_id)])
    third = _reopen(path, lambda db: S.ingest(db, now))                 # chronology eventually succeeds
    assert third['added'] == 1
    assert third['refused'] == [disposed(i) for i in range(0, 40, 2)]   # forged: recorded once, original reason
    for later in (now + 1, now + 2):                                    # restart never retries
        again = _reopen(path, lambda db: S.ingest(db, later))
        assert (again['added'], again['refused']) == (0, [])
    refusals, cases = _reopen(path, lambda db: ([tuple(r) for r in db.execute(
        'SELECT * FROM memory_measured_refused ORDER BY source_id')],
        [tuple(r) for r in db.execute('SELECT source_id,payload FROM memory_cases')]))
    assert refusals == [(iid, reason(i)) for i, iid in enumerate(ids)]
    assert cases == [(inv.investigation_id, I.encode(asdict(M.verified_case(inv, update, bars, now))))]
