"""SDD-STAGE-3-UNASSESSABLE-MEMORY-MALFORMED-SOURCE-HARDENING-V1: a malformed
not_testable source fails closed as a recorded refusal instead of crashing memory
ingestion, and never starves later valid unassessable closures. Valid unassessable
records, and measured/assessed ingestion, are unchanged.
Synthetic; no network, LLM or trading."""
import json
from dataclasses import asdict, replace

import pytest

from trader.cognition import investigation as I, memory as M
from trader.observability import investigation as C, memory as S
from tests.test_market_investigation import prefix, targets  # noqa: F401 (fixture)
from tests.test_investigation_unassessable_memory import store, v2_closure

MALFORMED = 'unassessable_malformed_source'
EMPTY = {'added': 0, 'refused': []}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def _raw(update, change):
    """A latest update whose evidence.status still reads not_testable, so the
    unassessable path (not the measured one) owns its disposition."""
    raw = json.loads(I.encode(asdict(update)))
    e = raw['evidence']
    if change == 'no_next_action': del raw['next_action']
    if change == 'next_action_text': raw['next_action'] = 'UNASSESSABLE'
    if change == 'assessment_int': raw['assessment'] = 7
    if change == 'extra_evidence_key': e['extra'] = 1
    if change == 'target_versions_int': e['target_versions'] = 7
    if change == 'target_versions_short': e['target_versions'] = [['X/USDT', 1]]
    if change == 'target_versions_text_ms': e['target_versions'] = [['X/USDT', '1', 'v']]
    if change == 'observed_missing': del e['observed_ms']
    if change == 'observed_null': e['observed_ms'] = None
    if change == 'observed_text': e['observed_ms'] = str(update.observed_ms)
    if change == 'observed_real': e['observed_ms'] = float(update.observed_ms)
    if change == 'observed_bool': e['observed_ms'] = True
    if change == 'as_of_null': e['as_of_ms'] = None
    if change == 'as_of_text': e['as_of_ms'] = str(update.evidence.as_of_ms)
    if change == 'as_of_real': e['as_of_ms'] = float(update.evidence.as_of_ms)
    if change == 'update_observed_real': raw['observed_ms'] = float(update.observed_ms)
    if change == 'update_observed_text': raw['observed_ms'] = str(update.observed_ms)
    return json.dumps(raw)


CHANGES = ['no_next_action', 'next_action_text', 'assessment_int', 'extra_evidence_key', 'target_versions_int',
           'target_versions_short', 'target_versions_text_ms', 'observed_missing', 'observed_null', 'observed_text',
           'observed_real', 'observed_bool', 'as_of_null', 'as_of_text', 'as_of_real', 'update_observed_real',
           'update_observed_text']


def _put(db, iid, inv_payload, update, raw, created_ms):
    db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)', (iid, f'ep_{iid}', 'X/USDT', created_ms,
               update.observed_ms, inv_payload))
    db.execute('INSERT INTO updates VALUES (?,?,?,?)', (f'ev_{iid}', iid, update.observed_ms, raw))


def _reopen(path, fn):
    with C.ledger(path) as db:
        result = fn(db)
    db.close()
    return result


def _rows(db, sql):
    return [tuple(r) for r in db.execute(sql)]


def _record(inv, update, bars, now):
    return I.encode(asdict(M.verified_unassessable(inv, update, bars, now)))


def _bad_prefix(db, inv, bars, update, raw_for, n=40):
    ids = [f'{inv.investigation_id}_{i:04d}' for i in range(n)]
    for i, iid in enumerate(ids):
        _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), update, raw_for(iid), created_ms=i)
        C._save_inputs(db, iid, bars, {b.version_id for b in bars})
    store(db, inv, bars, [update])                                      # created_ms = registered_ms: last
    return ids


# Malformed latest update ── no crash, one recorded refusal, never retried

@pytest.mark.parametrize('change', CHANGES)
def test_malformed_latest_update_is_refused_once_not_raised(tmp_path, prefix, change):
    snap, inv, update = v2_closure(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [])
        db.execute('UPDATE cases SET terminal_ms=? WHERE id=?', (update.observed_ms, inv.investigation_id))
        db.execute('INSERT INTO updates VALUES (?,?,?,?)', (update.event_id, inv.investigation_id,
                   update.observed_ms, _raw(update, change)))
        report = S.ingest(db, now)
        assert report['unassessable'] == {'added': 0, 'refused': [{'investigation_id': inv.investigation_id,
                                                                   'reason': MALFORMED}]}
        assert (report['added'], report['refused'], report['assessed']) == (0, [], EMPTY)   # not measured's
        assert _rows(db, 'SELECT * FROM memory_unassessable_refused') == [(inv.investigation_id, MALFORMED)]
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM memory_measured_refused').fetchone()[0] == 0
        again = S.ingest(db, now + 1)                                   # terminal disposition
        assert again['unassessable'] == EMPTY
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable_refused').fetchone()[0] == 1


@pytest.mark.parametrize('change', ['invalid_json', 'no_state', 'bad_dimensions', 'no_measurement',
                                    'no_alternatives', 'registered_real', 'registered_text',
                                    'registered_bool', 'registered_null'])
def test_malformed_case_payload_is_refused_not_raised(tmp_path, prefix, change):
    snap, inv, update = v2_closure(prefix)
    raw = json.loads(I.encode(asdict(inv)))
    if change == 'no_state': del raw['state']
    if change == 'bad_dimensions': raw['state']['dimensions'] = [1, 2]
    if change == 'no_measurement': del raw['measurement']
    if change == 'no_alternatives': del raw['alternatives']
    if change == 'registered_real': raw['registered_ms'] = float(raw['registered_ms'])
    if change == 'registered_text': raw['registered_ms'] = str(raw['registered_ms'])
    if change == 'registered_bool': raw['registered_ms'] = True
    if change == 'registered_null': raw['registered_ms'] = None
    payload = '{"state": ' if change == 'invalid_json' else json.dumps(raw)
    with C.ledger(tmp_path / 'm.db') as db:
        _put(db, inv.investigation_id, payload, update, I.encode(asdict(update)), created_ms=0)
        C._save_inputs(db, inv.investigation_id, snap.bars, {b.version_id for b in snap.bars})
        report = S.ingest(db, update.observed_ms + 1)
        assert report['unassessable']['refused'] == [{'investigation_id': inv.investigation_id, 'reason': MALFORMED}]
        assert report['assessed'] == EMPTY and report['refused'] == []
        assert S.ingest(db, update.observed_ms + 2)['unassessable'] == EMPTY
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable_refused').fetchone()[0] == 1


@pytest.mark.parametrize('bad', ['{"version_id": 1}', '{', 'overflow'])
def test_malformed_input_bar_is_refused_not_raised(tmp_path, prefix, bad):
    snap, inv, update = v2_closure(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        if bad == 'overflow':
            raw_bar = json.loads(I.encode(asdict(snap.bars[0])))
            raw_bar['candle']['open'] = 10 ** 400                       # float() overflows
            bad = json.dumps(raw_bar)
        db.execute('UPDATE inputs SET payload=? WHERE id=(SELECT MIN(id) FROM inputs)', (bad,))
        report = S.ingest(db, update.observed_ms + 1)
        assert report['unassessable']['refused'] == [{'investigation_id': inv.investigation_id, 'reason': MALFORMED}]
        assert S.ingest(db, update.observed_ms + 2)['unassessable'] == EMPTY


# >32 malformed sources never starve a later valid closure; restart idempotent

@pytest.mark.parametrize('change', ['no_next_action', 'as_of_real', 'target_versions_short'])
def test_malformed_prefix_cannot_starve_valid_closure_across_restart(tmp_path, prefix, change):
    snap, inv, update = v2_closure(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1
    bad = _raw(update, change)
    ids = []

    def seed(db):
        ids.extend(_bad_prefix(db, inv, snap.bars, update, lambda iid: bad))
        return S.ingest(db, now)
    first = _reopen(path, seed)['unassessable']                         # bounded batch
    assert first == {'added': 0, 'refused': [{'investigation_id': iid, 'reason': MALFORMED} for iid in ids[:32]]}
    second = _reopen(path, lambda db: S.ingest(db, now + 1))['unassessable']
    assert second == {'added': 1, 'refused': [{'investigation_id': iid, 'reason': MALFORMED} for iid in ids[32:]]}
    for later in (now + 2, now + 3):                                    # restart never retries
        assert _reopen(path, lambda db: S.ingest(db, later))['unassessable'] == EMPTY
    count, records = _reopen(path, lambda db: (
        db.execute('SELECT COUNT(*) FROM memory_unassessable_refused').fetchone()[0],
        _rows(db, 'SELECT source_id,payload FROM memory_unassessable')))
    assert count == 40
    assert records == [(inv.investigation_id, _record(inv, update, snap.bars, now + 1))]


# Retry policy ── a not-yet-observable closure is deferred (no disposition);
# deterministic verification refusals keep their reason and are recorded once

def test_not_yet_observable_closure_is_deferred_not_refused_across_restart(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    path = tmp_path / 'm.db'

    def seed(db):
        store(db, inv, snap.bars, [update])
        return S.ingest(db, update.observed_ms - 1)
    assert _reopen(path, seed)['unassessable'] == EMPTY
    assert _reopen(path, lambda db: db.execute('SELECT COUNT(*) FROM memory_unassessable_refused').fetchone()[0]) == 0
    assert _reopen(path, lambda db: S.ingest(db, update.observed_ms - 1))['unassessable'] == EMPTY
    assert _reopen(path, lambda db: S.ingest(db, update.observed_ms))['unassessable'] == {'added': 1, 'refused': []}
    assert _reopen(path, lambda db: _rows(db, 'SELECT source_id,payload FROM memory_unassessable')) == [
        (inv.investigation_id, _record(inv, update, snap.bars, update.observed_ms))]


def _future(update, iid, observed_ms):
    """Well-formed not_testable closure for iid, observable only at observed_ms."""
    return replace(update, investigation_id=iid, observed_ms=observed_ms,
                   evidence=replace(update.evidence, investigation_id=iid, observed_ms=observed_ms))


def test_future_prefix_cannot_starve_valid_closure_across_restart(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    path, now, later = tmp_path / 'm.db', update.observed_ms + 1, update.observed_ms + 10 ** 6
    ids = [f'{inv.investigation_id}_{i:04d}' for i in range(40)]

    def seed(db):
        for i, iid in enumerate(ids):
            fut = _future(update, iid, later)
            _put(db, iid, I.encode(asdict(replace(inv, investigation_id=iid))), fut, I.encode(asdict(fut)), created_ms=i)
            C._save_inputs(db, iid, snap.bars, {b.version_id for b in snap.bars})
        store(db, inv, snap.bars, [update])                             # created_ms = registered_ms: last
        return S.ingest(db, now)
    assert _reopen(path, seed)['unassessable'] == {'added': 1, 'refused': []}   # valid closure: first run
    for t in (now + 1, later - 1):                                      # still future: no starvation, no disposition
        assert _reopen(path, lambda db: S.ingest(db, t))['unassessable'] == EMPTY
    assert _reopen(path, lambda db: db.execute('SELECT COUNT(*) FROM memory_unassessable_refused').fetchone()[0]) == 0
    assert _reopen(path, lambda db: _rows(db, 'SELECT source_id,payload FROM memory_unassessable')) == [
        (inv.investigation_id, _record(inv, update, snap.bars, now))]
    first = _reopen(path, lambda db: S.ingest(db, later))['unassessable']       # clock advanced: eligible, bounded
    assert first['added'] == 0 and [r['investigation_id'] for r in first['refused']] == ids[:32]
    second = _reopen(path, lambda db: S.ingest(db, later + 1))['unassessable']
    assert second['added'] == 0 and [r['investigation_id'] for r in second['refused']] == ids[32:]
    assert _reopen(path, lambda db: S.ingest(db, later + 2))['unassessable'] == EMPTY
    refusals = _reopen(path, lambda db: _rows(db, 'SELECT * FROM memory_unassessable_refused ORDER BY source_id'))
    assert refusals == [(r['investigation_id'], r['reason']) for r in first['refused'] + second['refused']]
    assert all(reason != 'unassessable_invalid_chronology' for _, reason in refusals)


@pytest.mark.parametrize('change', ['observed_text', 'observed_real', 'observed_null', 'observed_missing',
                                    'dup_future_first', 'dup_future_last', 'dup_evidence'])
def test_malformed_future_observed_is_refused_not_deferred(tmp_path, prefix, change):
    snap, inv, update = v2_closure(prefix)
    now = update.observed_ms - 1                                        # the int value would be future
    raw = json.loads(I.encode(asdict(update)))
    ev = json.dumps(raw['evidence'])[:-1]
    if change.startswith('dup_future'):
        first, last = (update.observed_ms, '"x"') if change == 'dup_future_first' else ('"x"', update.observed_ms)
        del raw['evidence']['observed_ms']
        ev = json.dumps(raw['evidence'])[:-1]
        text = json.dumps({k: v for k, v in raw.items() if k != 'evidence'})[:-1] + \
            f', "evidence": {ev}, "observed_ms": {first}, "observed_ms": {last}}}}}'
    elif change == 'dup_evidence':
        text = json.dumps({k: v for k, v in raw.items() if k != 'evidence'})[:-1] + \
            f', "evidence": {ev}}}, "evidence": {ev}, "observed_ms": "x"}}}}'
    else:
        text = _raw(update, change)
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [])
        db.execute('UPDATE cases SET terminal_ms=? WHERE id=?', (update.observed_ms, inv.investigation_id))
        db.execute('INSERT INTO updates VALUES (?,?,?,?)', (update.event_id, inv.investigation_id, update.observed_ms, text))
        assert S.ingest(db, now)['unassessable'] == {'added': 0, 'refused': [{'investigation_id': inv.investigation_id,
                                                                             'reason': MALFORMED}]}
        assert S.ingest(db, now + 2)['unassessable'] == EMPTY
        assert _rows(db, 'SELECT * FROM memory_unassessable_refused') == [(inv.investigation_id, MALFORMED)]


def test_future_update_observed_mismatch_is_deferred_then_refused(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    forged = replace(update, observed_ms=update.observed_ms + 1)        # update.observed_ms != evidence.observed_ms
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [forged])
        assert S.ingest(db, update.observed_ms - 1)['unassessable'] == EMPTY
        assert S.ingest(db, update.observed_ms + 1)['unassessable']['refused'] == [
            {'investigation_id': inv.investigation_id, 'reason': 'unassessable_invalid_chronology'}]
        assert S.ingest(db, update.observed_ms + 2)['unassessable'] == EMPTY


def test_impossible_chronology_is_recorded_once_with_its_reason(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    forged = replace(update, evidence=replace(update.evidence, as_of_ms=inv.registered_ms - 1))
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [forged])
        assert S.ingest(db, update.observed_ms + 1)['unassessable']['refused'] == [
            {'investigation_id': inv.investigation_id, 'reason': 'unassessable_invalid_chronology'}]
        assert S.ingest(db, update.observed_ms + 2)['unassessable'] == EMPTY
        assert _rows(db, 'SELECT * FROM memory_unassessable_refused') == [
            (inv.investigation_id, 'unassessable_invalid_chronology')]


@pytest.mark.parametrize('change,reason', [
    ('assessment', 'unassessable_assessment_mismatch'),
    ('action', 'unassessable_requires_not_testable_closure'),
    ('case', 'unassessable_case_mismatch'),
    ('target', 'unassessable_terminal_replay_mismatch'),
])
def test_forged_wellformed_closure_keeps_its_reason_and_is_recorded_once(tmp_path, prefix, change, reason):
    snap, inv, update = v2_closure(prefix)
    if change == 'assessment': update = replace(update, assessment=(('same_direction', 'compatible'),))
    if change == 'action': update = replace(update, next_action=replace(update.next_action, kind='WAIT'))
    if change == 'case': update = replace(update, investigation_id='other')
    if change == 'target':
        update = replace(update, evidence=replace(update.evidence, target_versions=(('X/USDT', 1, 'nope'),)))
    with C.ledger(tmp_path / 'm.db') as db:
        _put(db, inv.investigation_id, I.encode(asdict(inv)), update, I.encode(asdict(update)), created_ms=0)
        C._save_inputs(db, inv.investigation_id, snap.bars, {b.version_id for b in snap.bars})
        assert S.ingest(db, update.observed_ms + 1)['unassessable']['refused'] == [
            {'investigation_id': inv.investigation_id, 'reason': reason}]
        assert S.ingest(db, update.observed_ms + 2)['unassessable'] == EMPTY
        assert _rows(db, 'SELECT * FROM memory_unassessable_refused') == [(inv.investigation_id, reason)]


def test_mixed_malformed_forged_and_early_prefix_cannot_starve_valid_closure(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    path, now = tmp_path / 'm.db', update.observed_ms + 1
    forged = lambda iid: I.encode(asdict(replace(update, investigation_id=iid,        # well-formed, forged join
                                                 evidence=replace(update.evidence, investigation_id=iid))))
    malformed = _raw(update, 'as_of_real')
    reason = lambda i: MALFORMED if i % 2 else 'unassessable_frozen_registration_mismatch'
    ids = []

    def seed(db):
        ids.extend(_bad_prefix(db, inv, snap.bars, update, lambda iid: malformed if int(iid[-4:]) % 2 else forged(iid)))
        return S.ingest(db, update.observed_ms - 1)                     # nothing observable yet
    disposed = lambda i: {'investigation_id': ids[i], 'reason': reason(i)}
    assert _reopen(path, seed)['unassessable'] == EMPTY                 # all deferred, none disposed
    assert _reopen(path, lambda db: db.execute('SELECT COUNT(*) FROM memory_unassessable_refused').fetchone()[0]) == 0
    second = _reopen(path, lambda db: S.ingest(db, now))['unassessable']
    assert second == {'added': 0, 'refused': [disposed(i) for i in range(32)]}      # bounded batch
    third = _reopen(path, lambda db: S.ingest(db, now + 1))['unassessable']
    assert third == {'added': 1, 'refused': [disposed(i) for i in range(32, 40)]}
    for later in (now + 2, now + 3):                                    # restart never retries
        assert _reopen(path, lambda db: S.ingest(db, later))['unassessable'] == EMPTY
    refusals, records = _reopen(path, lambda db: (
        _rows(db, 'SELECT * FROM memory_unassessable_refused ORDER BY source_id'),
        _rows(db, 'SELECT source_id,payload FROM memory_unassessable')))
    assert refusals == [(iid, reason(i)) for i, iid in enumerate(ids)]
    assert records == [(inv.investigation_id, _record(inv, update, snap.bars, now + 1))]


# Existing valid records and other paths unchanged

def test_valid_record_unchanged_and_malformed_neighbour_does_not_affect_it(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'clean.db') as db:
        store(db, inv, snap.bars, [update])
        assert S.ingest(db, now)['unassessable'] == {'added': 1, 'refused': []}
        clean = _rows(db, 'SELECT * FROM memory_unassessable')
    with C.ledger(tmp_path / 'mixed.db') as db:
        _put(db, 'bad', I.encode(asdict(replace(inv, investigation_id='bad'))), update,
             _raw(update, 'no_next_action'), created_ms=0)
        store(db, inv, snap.bars, [update])
        report = S.ingest(db, now)
        assert report['unassessable'] == {'added': 1, 'refused': [{'investigation_id': 'bad', 'reason': MALFORMED}]}
        assert _rows(db, 'SELECT * FROM memory_unassessable') == clean
        assert clean == [(inv.investigation_id, _record(inv, update, snap.bars, now))]


def test_retention_clears_unassessable_refusals(tmp_path, prefix):
    snap, inv, update = v2_closure(prefix)
    with C.ledger(tmp_path / 'm.db') as db:
        _put(db, inv.investigation_id, '{', update, I.encode(asdict(update)), created_ms=0)
        assert S.ingest(db, update.observed_ms + 1)['unassessable']['refused'] == [
            {'investigation_id': inv.investigation_id, 'reason': MALFORMED}]
        db.execute('DELETE FROM cases'); S.retain(db)
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable_refused').fetchone()[0] == 0
