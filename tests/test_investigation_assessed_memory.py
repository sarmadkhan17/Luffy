"""SDD-STAGE-3-INVESTIGATION-ASSESSED-MEMORY-V1: terminal assessed descriptive
registration-evidence results are remembered in their exact original vocabulary
as context only, never as usefulness, success, prediction, edge, direction,
priority or registration authority. Synthetic; no network, LLM or trading."""
import json
import shutil
from dataclasses import asdict, replace

import pytest

from trader.cognition import investigation as I, memory as M
from trader.observability import investigation as C, memory as S
from tests.test_market_investigation import prefix, targets  # noqa: F401 (fixture)
from tests.test_investigation_memory import resolved
from tests import test_positioning_investigation_family as PF
from tests import test_correlation_investigation_family as CF
from tests.test_investigation_unassessable_memory import (FORBIDDEN, consistent, expired, reopen,
                                                          positioning_closure, store)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def positioning_assessed(tmp_path):
    snap, state = PF._state(tmp_path)
    inv = I.open_investigation(state, I.POSITIONING_FAMILY, snap.bars, PF.NOW)
    return snap, inv, I.advance(inv, I.measure(inv, (), PF.NOW, PF.NOW))


def correlation_assessed(tmp_path):
    snap, state = CF._state(tmp_path)
    inv = I.open_investigation(state, I.CORRELATION_FAMILY, snap.bars, CF.NOW)
    return snap, inv, I.advance(inv, I.measure(inv, (), CF.NOW, CF.NOW))


def successor(inv, bars):
    """A genuinely later registration (next anchor) of the same family/protocol/config/symbol."""
    state = replace(inv.state, as_of_ms=inv.state.as_of_ms + I.TF, observed_ms=inv.state.observed_ms + I.TF)
    return I.open_investigation(state, inv.primary_trigger, bars, inv.registered_ms + I.TF)


def record(tmp_path, family='positioning'):
    snap, inv, update = (positioning_assessed if family == 'positioning' else correlation_assessed)(tmp_path)
    return snap, inv, update, M.verified_assessed(inv, update, snap.bars, update.observed_ms + 1)


# 1, 3, 4 ── exact vocabulary, full provenance, deterministic ───────────────

@pytest.mark.parametrize('family', ['positioning', 'correlation'])
def test_assessed_result_is_one_record_with_exact_vocabulary_and_provenance(tmp_path, family):
    snap, inv, update, rec = record(tmp_path, family)
    assert update.evidence.status == 'assessed'
    [result] = [n for n, s in update.assessment if s == 'compatible']
    assert rec.result == result and result in [a.name for a in inv.alternatives]
    assert (rec.status, rec.reason, rec.reason_codes, rec.assessment) == (
        'assessed', update.evidence.reason, update.reason_codes, update.assessment)
    assert rec.outcome_type == 'assessed_description' and rec.schema_version == M.ASSESSED_SCHEMA
    assert (rec.source_id, rec.source_event_id, rec.source_evidence_id, rec.source_episode_id) == (
        inv.investigation_id, update.event_id, update.evidence.evidence_id, inv.episode_id)
    assert (rec.source_scan_id, rec.source_state_id, rec.catalog_id, rec.config_id, rec.family, rec.symbol) == (
        inv.state.scan_id, inv.state.state_id, M.ASSESSED_CATALOGS[inv.primary_trigger], inv.state.config_id,
        inv.primary_trigger, inv.state.symbol)
    assert (rec.registered_ms, rec.resolved_ms, rec.recorded_ms) == (
        inv.registered_ms, update.observed_ms, update.observed_ms + 1)
    assert (rec.source_evidence_ids, rec.source_input_versions) == (inv.state.evidence_ids, inv.state.input_versions)
    assert list(rec.registration_evidence) == [json.loads(I.encode(asdict(d))) for d in inv.state.dimensions]
    assert rec.evidence == json.loads(I.encode(asdict(update.evidence)))
    for absent in ('winner', 'score', 'sign', 'probability', 'direction', 'useful', 'success', 'count'):
        assert absent not in asdict(rec)
    assert M.assessed_from_dict(json.loads(I.encode(asdict(rec)))) == rec
    assert M.verified_assessed(inv, update, snap.bars, update.observed_ms + 1) == rec


def test_correlation_descriptors_are_kept_verbatim(tmp_path):
    _, inv, update, rec = record(tmp_path, 'correlation')
    assert rec.reason_codes == (update.evidence.reason, *I.correlation_description(inv.state))
    assert rec.result in I.CORRELATION_CATALOG['alternatives']


# 10, 11 ── not_testable closures and v2 cases are not this type ───────────

def test_not_testable_and_v2_closures_and_measurements_are_refused(tmp_path, prefix):
    (tmp_path / 'p').mkdir()
    snap, inv, closure = positioning_closure(tmp_path / 'p')
    with pytest.raises(ValueError, match='assessed_requires_assessed_result'):
        M.verified_assessed(inv, closure, snap.bars, PF.NOW + 1)
    _, snap2, v2 = prefix
    with pytest.raises(ValueError, match='assessed_protocol_mismatch'):
        M.verified_assessed(v2, expired(v2), snap2.bars, v2.measurement.expires_ms + 2)
    bars, measured = resolved(v2)
    with pytest.raises(ValueError, match='assessed_protocol_mismatch'):
        M.verified_assessed(v2, measured, (*snap2.bars, *bars), measured.observed_ms)


@pytest.mark.parametrize('change', ['result', 'codes', 'action', 'rationale', 'case', 'event', 'chronology',
                                    'state', 'catalog', 'family', 'baseline', 'status', 'evidence'])
def test_forged_or_mismatched_results_are_refused(tmp_path, change):
    snap, inv, update = positioning_assessed(tmp_path)
    bars, e = snap.bars, update.evidence
    [result] = [n for n, s in update.assessment if s == 'compatible']
    other = next(n for n, _ in update.assessment if n != result)
    if change == 'result':
        update = replace(update, assessment=tuple((n, 'compatible' if n == other else 'contradicted')
                                                  for n, _ in update.assessment))
    if change == 'codes': update = replace(update, reason_codes=update.reason_codes + ('extra',))
    if change == 'action': update = replace(update, next_action=replace(update.next_action, kind='WAIT'))
    if change == 'rationale': update = replace(update, rationale='useful')
    if change == 'case': update = replace(update, investigation_id='other')
    if change == 'event': update = replace(update, event_id='update_forged')
    if change == 'chronology': update = replace(update, evidence=replace(e, as_of_ms=inv.registered_ms - 1))
    if change == 'state': inv = replace(inv, state=replace(inv.state, symbol='X/USDT'))
    if change == 'catalog': inv = replace(inv, measurement=replace(inv.measurement, catalog_id=I.CATALOG_ID))
    if change == 'family': inv = replace(inv, primary_trigger=I.CORRELATION_FAMILY)
    if change == 'baseline': bars = bars[1:]
    if change == 'status': update = replace(update, evidence=replace(e, status='measured'))
    if change == 'evidence': update = replace(update, evidence=replace(e, reason='other_reason'))
    with pytest.raises(ValueError, match='^assessed_'):
        M.verified_assessed(inv, update, bars, update.observed_ms + 1)


def test_evaluator_disagreement_with_frozen_registration_is_refused(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    tampered = consistent(PF._with(inv.state, lambda d: replace(d, value=0.0) if d.name in I.FAMILIES else d))
    with pytest.raises(ValueError, match='^assessed_'):
        M.verified_assessed(replace(inv, state=tampered), update, snap.bars, update.observed_ms + 1)


# 1, 5, 10, 11 ── ledger ingestion keeps the three types apart ──────────────

def test_ledger_ingests_only_assessed_and_leaves_other_memory_types_alone(tmp_path, prefix):
    (tmp_path / 'p').mkdir(); (tmp_path / 'c').mkdir()
    snap, inv, update = positioning_assessed(tmp_path / 'p')
    csnap, cinv, cupdate = positioning_closure(tmp_path / 'c')
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        report = S.ingest(db, now)
        assert report['assessed'] == {'added': 1, 'refused': []}
        assert report['added'] == 0 and report['unassessable'] == {'added': 0, 'refused': []}
        assert db.execute('SELECT COUNT(*) FROM memory_cases').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable').fetchone()[0] == 0
        [(sid, payload)] = db.execute('SELECT source_id,payload FROM memory_assessed').fetchall()
        assert sid == inv.investigation_id
        assert json.loads(payload) == json.loads(I.encode(asdict(M.verified_assessed(inv, update, snap.bars, now))))
    with C.ledger(tmp_path / 'closure.db') as db:
        store(db, cinv, csnap.bars, [cupdate])
        report = S.ingest(db, PF.NOW + 1)
        assert report['assessed'] == {'added': 0, 'refused': []} and report['unassessable']['added'] == 1
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 0
    _, snap2, v2 = prefix
    bars, measured = resolved(v2)
    with C.ledger(tmp_path / 'measured.db') as db:
        store(db, v2, (*snap2.bars, *bars), [measured])
        report = S.ingest(db, measured.observed_ms + 1)
        assert report['added'] == 1 and report['assessed'] == {'added': 0, 'refused': []}
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 0
        case = json.loads(db.execute('SELECT payload FROM memory_cases').fetchone()[0])
        assert case == json.loads(I.encode(asdict(M.verified_case(
            v2, measured, (*snap2.bars, *bars), measured.observed_ms + 1))))


def test_forged_ledger_source_is_refused_not_stored(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    forged = replace(update, reason_codes=update.reason_codes + ('extra',))
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [forged])
        report = S.ingest(db, update.observed_ms + 1)['assessed']
        assert report == {'added': 0, 'refused': [{'investigation_id': inv.investigation_id,
                                                   'reason': 'assessed_assessment_mismatch'}]}
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 0
        assert [tuple(r) for r in db.execute('SELECT * FROM memory_assessed_refused')] == [
            (inv.investigation_id, 'assessed_assessment_mismatch')]
        assert S.ingest(db, update.observed_ms + 2)['assessed'] == {'added': 0, 'refused': []}   # terminal disposition
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 0


# Bounded progress ── ineligible or refused prefixes never starve a later valid case

def _clone(db, inv, update, i, created_ms):
    """Copy a genuine terminal case under a new identity; its content stays verbatim."""
    iid = f'{inv.investigation_id}_{i:04d}'
    db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)', (iid, f'ep_{iid}', inv.state.symbol, created_ms,
               update.observed_ms, I.encode(asdict(replace(inv, investigation_id=iid)))))
    db.execute('INSERT INTO updates VALUES (?,?,?,?)', (f'ev_{iid}', iid, update.observed_ms,
               I.encode(asdict(replace(update, investigation_id=iid)))))


def test_large_ineligible_terminal_prefix_does_not_starve_a_valid_case(tmp_path, prefix):
    (tmp_path / 'p').mkdir(); (tmp_path / 'c').mkdir()
    snap, inv, update = positioning_assessed(tmp_path / 'p')
    _, pinv, pclosure = positioning_closure(tmp_path / 'c')
    _, _, v2 = prefix
    v2_closed = expired(v2)
    _, v2_measured = resolved(v2)
    with C.ledger(tmp_path / 'm.db') as db:
        for i in range(300):                            # older terminal non-assessed cases
            src = [(pinv, pclosure), (v2, v2_closed), (v2, v2_measured)][i % 3]
            _clone(db, *src, i, created_ms=i)
        store(db, inv, snap.bars, [update])             # created_ms = registered_ms: last
        report = S.ingest(db, update.observed_ms + 1)
        assert report['assessed'] == {'added': 1, 'refused': []}
        assert [tuple(r) for r in db.execute('SELECT source_id FROM memory_assessed')] == [(inv.investigation_id,)]


def test_refused_assessed_prefix_does_not_starve_a_valid_case(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    forged = replace(update, reason_codes=update.reason_codes + ('extra',))
    with C.ledger(tmp_path / 'm.db') as db:
        for i in range(40):
            _clone(db, inv, forged, i, created_ms=i)
        store(db, inv, snap.bars, [update])
        now = update.observed_ms + 1
        first = S.ingest(db, now)['assessed']
        assert (first['added'], len(first['refused'])) == (0, 32)                  # per-run bound kept
        assert {r['reason'] for r in first['refused']} == {'assessed_case_mismatch'}
        second = S.ingest(db, now + 1)['assessed']
        assert (second['added'], len(second['refused'])) == (1, 8)
        assert S.ingest(db, now + 2)['assessed'] == {'added': 0, 'refused': []}
        assert db.execute('SELECT COUNT(*) FROM memory_assessed_refused').fetchone()[0] == 40
        assert [tuple(r) for r in db.execute('SELECT source_id FROM memory_assessed')] == [(inv.investigation_id,)]


def test_unrelated_stored_records_never_crowd_out_a_compatible_one(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    new = successor(inv, snap.bars)
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        S.ingest(db, update.observed_ms + 1)
        [(real,)] = db.execute('SELECT payload FROM memory_assessed').fetchall()
        rec = M.assessed_from_dict(json.loads(real))
        noise = []
        for i in range(300):                            # every source_id sorts before the real one
            edit = [dict(symbol=f'OTHER{i}/USDT'), dict(config_id=f'cfg{i}'), dict(family=I.CORRELATION_FAMILY),
                    dict(catalog_id=I.CORRELATION_CATALOG_ID),
                    dict(recorded_ms=new.registered_ms + i)][i % 5]  # last: same key, known too late
            noise.append(replace(rec, source_id=f'a{i:04d}', case_id=f'assessed_memory_a{i:04d}', **edit))
        for r in noise:
            db.execute('INSERT INTO memory_assessed VALUES (?,?,?,?,?,?,?,?,?)', (
                r.source_id, r.family, r.catalog_id, r.config_id, r.symbol,
                max(r.resolved_ms, r.available_ms, r.recorded_ms), r.available_ms, r.case_id, I.encode(asdict(r))))
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 301
        assert min(r.source_id for r in noise) < rec.source_id
        ctx = S.register(db, new)
        db.execute('DELETE FROM memory_contexts'); db.execute('DELETE FROM typed_contexts')
        again = S.register(db, new)
    prior = ctx['assessed_descriptions']
    assert [r['source_id'] for r in prior['records']] == [inv.investigation_id]
    assert prior['audit'] == [{'case_id': rec.case_id, 'reason': 'compatible_prior_assessed_description'}]
    assert again == ctx


# 5 ── retry and restart never duplicate ────────────────────────────────────

def test_retry_and_restart_do_not_duplicate(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        assert S.ingest(db, now)['assessed']['added'] == 1
        first = db.execute('SELECT source_id,payload FROM memory_assessed').fetchall()
        assert S.ingest(db, now + 5)['assessed']['added'] == 0
    with C.ledger(tmp_path / 'm.db') as db:                                    # restart
        assert S.ingest(db, now + 9)['assessed']['added'] == 0
        assert db.execute('SELECT source_id,payload FROM memory_assessed').fetchall() == first


# 6, 7, 8 ── exact-key recall, bounded and deterministic, context only ──────

def test_later_matching_investigation_recalls_exact_result_as_context(tmp_path):
    snap, inv, update, rec = record(tmp_path)
    new = reopen(inv, snap.bars, PF.NOW + I.TF)
    ctx = M.retrieve_assessed(new, [rec])
    assert ctx['records'] == [asdict(rec)] and ctx['audit'] == [
        {'case_id': rec.case_id, 'reason': 'compatible_prior_assessed_description'}]
    [note] = ctx['notes']
    assert note['kind'] == 'prior_assessed_description' and note['authority'] == 'context_only'
    assert (note['result'], note['reason_codes']) == (rec.result, list(update.reason_codes))
    template = note['text'].replace(rec.result, '').lower()
    assert not [w for w in FORBIDDEN if w in template]
    assert not [w for w in FORBIDDEN if w in ctx['limitation'].lower()]
    assert 'counter_tests' not in ctx and 'cautions' not in ctx


@pytest.mark.parametrize('change,reason', [
    ('self', 'self_excluded'), ('later_known', 'not_known_before_registration'),
    ('late_import', 'not_known_before_registration'), ('same_time', 'not_known_before_registration'),
    ('family', 'incompatible_family'), ('catalog', 'incompatible_protocol_or_config'),
    ('config', 'incompatible_protocol_or_config'), ('symbol', 'incompatible_symbol'),
    ('type', 'incompatible_version_or_outcome_type'), ('schema', 'incompatible_version_or_outcome_type')])
def test_retrieval_exclusions(tmp_path, change, reason):
    snap, inv, update, rec = record(tmp_path)
    new = reopen(inv, snap.bars, PF.NOW + I.TF)
    edits = {'self': dict(source_id=new.investigation_id), 'later_known': dict(available_ms=new.registered_ms + 1),
             'late_import': dict(recorded_ms=new.registered_ms + 1), 'same_time': dict(resolved_ms=new.registered_ms),
             'family': dict(family=I.CORRELATION_FAMILY), 'catalog': dict(catalog_id=I.CORRELATION_CATALOG_ID),
             'config': dict(config_id='other'), 'symbol': dict(symbol='OTHER/USDT'),
             'type': dict(outcome_type='unassessable_closure'),
             'schema': dict(schema_version=M.UNASSESSABLE_SCHEMA)}
    ctx = M.retrieve_assessed(new, [replace(rec, **edits[change])])
    assert ctx['records'] == [] and ctx['notes'] == [] and ctx['audit'][0]['reason'] == reason


def test_capacity_is_bounded_deterministic_and_synthesizes_nothing(tmp_path):
    snap, inv, update, rec = record(tmp_path)
    new = reopen(inv, snap.bars, PF.NOW + I.TF)
    names = [a.name for a in inv.alternatives]
    recs = [replace(rec, case_id=f'c{i}', source_id=f's{i}', result=names[i % len(names)],
                    available_ms=rec.available_ms - i) for i in range(5)]
    ctx = M.retrieve_assessed(new, recs)
    assert ctx == M.retrieve_assessed(new, list(reversed(recs)))
    assert [n['case_id'] for n in ctx['notes']] == ['c0', 'c1', 'c2'][:M.MAX_RETRIEVED]
    assert [n['result'] for n in ctx['notes']] == [names[i % len(names)] for i in range(M.MAX_RETRIEVED)]
    assert [a['reason'] for a in ctx['audit']].count('retrieval_capacity') == len(recs) - M.MAX_RETRIEVED
    assert set(ctx) == {'schema_version', 'records', 'audit', 'notes', 'limitation'}


def test_positioning_record_is_not_recalled_by_correlation_or_v2(tmp_path, prefix):
    (tmp_path / 'p').mkdir(); (tmp_path / 'c').mkdir()
    snap, inv, _, rec = record(tmp_path / 'p')
    _, cinv, _, _ = record(tmp_path / 'c', 'correlation')
    later = PF.NOW + I.TF + 60_000
    corr = replace(cinv, investigation_id='corr_new', registered_ms=later,
                   state=replace(cinv.state, symbol=inv.state.symbol, config_id=inv.state.config_id))
    assert M.retrieve_assessed(corr, [rec])['audit'][0]['reason'] == 'incompatible_family'
    _, _, v2 = prefix
    other = replace(v2, registered_ms=later,
                    state=replace(v2.state, symbol=inv.state.symbol, config_id=inv.state.config_id))
    assert M.retrieve_assessed(other, [rec])['audit'][0]['reason'] == 'incompatible_family'


# 9, 11 ── consumer: ingestion, registration context, no other effect ──────

def test_consumer_ingests_and_registration_context_is_additive_only(tmp_path):
    path, dest, first = PF.run(tmp_path, PF.FUND_UP + PF.LS_UP)
    assert first['registered'] >= 1
    inv, [update] = PF.case(dest)
    assert update.evidence.status == 'assessed'
    detail = C.step(path, dest, PF.NOW + 60_000)
    assert detail['memory']['assessed'] == {'added': 1, 'refused': []}
    assert detail.get('reason') != 'memory_source_refused'
    assert C.step(path, dest, PF.NOW + 120_000)['memory']['assessed'] == {'added': 0, 'refused': []}
    snap = C.adapt(C.source_snapshot(path), PF.NOW)
    new = successor(inv, snap.bars)
    assert new.episode_id != inv.episode_id
    bare = tmp_path / 'bare.db'
    shutil.copy(dest, bare)
    with C.ledger(dest) as db:
        [(stored,)] = db.execute('SELECT payload FROM memory_assessed').fetchall()
        assert db.execute('SELECT COUNT(*) FROM memory_unassessable').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM memory_cases').fetchone()[0] == 0
        with_mem = S.register(db, new)
    with C.ledger(bare) as db:                         # comparator: identical ledger, no assessed memory
        db.execute('DELETE FROM memory_assessed')
        without = S.register(db, new)
    prior = with_mem['assessed_descriptions']
    assert json.loads(I.encode(prior['records'])) == [json.loads(stored)] and prior['notes'][0]['authority'] == 'context_only'
    assert 'assessed_descriptions' not in without
    assert {k: v for k, v in with_mem.items() if k not in ('assessed_descriptions', 'context_id')} == \
           {k: v for k, v in without.items() if k != 'context_id'}
    assert not with_mem['counter_tests']
    initial = I.advance(new, I.measure(new, (), new.registered_ms, new.registered_ms))
    assert M.reasoning(initial, with_mem) == dict(M.reasoning(initial, without), context_id=with_mem['context_id'])
    assert not M.reasoning(initial, with_mem)['changed']


def test_export_replays_prior_description_and_rejects_forgery(tmp_path):
    path, dest, _ = PF.run(tmp_path, PF.FUND_UP + PF.LS_UP)
    inv, _ = PF.case(dest)
    C.step(path, dest, PF.NOW + 60_000)
    snap = C.adapt(C.source_snapshot(path), PF.NOW)
    new = successor(inv, snap.bars)
    with C.ledger(dest) as db:
        db.execute('INSERT INTO cases VALUES (?,?,?,?,NULL,?)', (new.investigation_id, new.episode_id,
                   new.state.symbol, new.registered_ms, I.encode(asdict(new))))
        C._save_inputs(db, new.investigation_id, snap.bars, set(new.state.input_versions))
        S.register(db, new)
        archive = S.export_case(db, new.investigation_id)
    body = S.replay_export(json.loads(I.encode(archive)))
    assert body['memory']['context']['assessed_descriptions']['records'][0]['source_id'] == inv.investigation_id
    forged = json.loads(I.encode(archive))
    records = forged['memory']['context']['assessed_descriptions']['records']
    records[0]['result'] = next(a.name for a in inv.alternatives if a.name != records[0]['result'])
    forged['sha256'] = S.typed.O.digest({k: forged[k] for k in ('schema_version', 'source', 'memory')})
    with pytest.raises(ValueError, match='memory_archive_prior_assessed_mismatch'):
        S.replay_export(forged)


def test_retention_removes_records_with_their_case(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        S.ingest(db, update.observed_ms + 1)
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 1
        db.execute('DELETE FROM cases'); S.retain(db)
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 0


def test_contexts_without_records_are_byte_identical(prefix, tmp_path):
    _, _, inv = prefix
    with C.ledger(tmp_path / 'm.db') as db:
        ctx = S.register(db, inv)
    assert 'assessed_descriptions' not in ctx


# Clock state defers; it is never a permanent refusal ──────────────────────

def later_assessed(snap, inv, k):
    """A distinct genuine registration k anchors after inv, on the same exact key."""
    at, state = inv.registered_ms + k * I.TF, inv.state
    state = consistent(replace(state, as_of_ms=state.as_of_ms + k * I.TF, observed_ms=state.observed_ms + k * I.TF))
    new = I.open_investigation(state, inv.primary_trigger, snap.bars, at)
    return new, I.advance(new, I.measure(new, (), at, at))


def test_future_observed_case_is_deferred_then_ingested_across_restart(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    early = update.evidence.observed_ms - 1
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        assert S.ingest(db, early)['assessed'] == {'added': 0, 'refused': []}
        assert S.ingest(db, early)['assessed'] == {'added': 0, 'refused': []}
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM memory_assessed_refused').fetchone()[0] == 0
    with C.ledger(tmp_path / 'm.db') as db:                                    # restart, clock still behind
        assert S.ingest(db, early)['assessed'] == {'added': 0, 'refused': []}
    now = update.observed_ms + 1
    with C.ledger(tmp_path / 'm.db') as db:                                    # restart, clock recovered
        assert S.ingest(db, now)['assessed'] == {'added': 1, 'refused': []}
        [(payload,)] = db.execute('SELECT payload FROM memory_assessed').fetchall()
        assert json.loads(payload) == json.loads(I.encode(asdict(M.verified_assessed(inv, update, snap.bars, now))))
        assert db.execute('SELECT COUNT(*) FROM memory_assessed_refused').fetchone()[0] == 0
        assert S.ingest(db, now + 1)['assessed'] == {'added': 0, 'refused': []}


def test_future_observed_prefix_does_not_occupy_the_bound(tmp_path):
    snap, inv, update = positioning_assessed(tmp_path)
    future, fupdate = later_assessed(snap, inv, 4)
    now = update.observed_ms + 1
    assert fupdate.evidence.observed_ms > now
    with C.ledger(tmp_path / 'm.db') as db:
        for i in range(40):                              # older, all observed after now
            _clone(db, future, fupdate, i, created_ms=i)
        store(db, inv, snap.bars, [update])
        assert S.ingest(db, now)['assessed'] == {'added': 1, 'refused': []}
        assert [tuple(r) for r in db.execute('SELECT source_id FROM memory_assessed')] == [(inv.investigation_id,)]
        assert db.execute('SELECT COUNT(*) FROM memory_assessed_refused').fetchone()[0] == 0


@pytest.mark.parametrize('observed', ['missing', 'text', 'real'])
def test_malformed_observed_ms_is_refused_not_deferred(tmp_path, observed):
    snap, inv, update = positioning_assessed(tmp_path)
    raw = json.loads(I.encode(asdict(update)))
    if observed == 'missing': del raw['evidence']['observed_ms']
    if observed == 'text': raw['evidence']['observed_ms'] = '9' * 20
    if observed == 'real': raw['evidence']['observed_ms'] = float(10 ** 15)
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [])
        db.execute('UPDATE cases SET terminal_ms=? WHERE id=?', (update.observed_ms, inv.investigation_id))
        db.execute('INSERT INTO updates VALUES (?,?,?,?)', (update.event_id, inv.investigation_id,
                   update.observed_ms, json.dumps(raw)))
        [refusal] = S.ingest_assessed(db, update.observed_ms + 1)['refused']
        assert refusal['investigation_id'] == inv.investigation_id and refusal['reason'].startswith('assessed_')
        assert db.execute('SELECT COUNT(*) FROM memory_assessed_refused').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM memory_assessed').fetchone()[0] == 0


# Replay reproduces the receiving case's recall, not only each record ─────

def _recompute(a):
    ctx = a['memory']['context']
    ctx['context_id'] = M.stable_id('memory_context', {k: v for k, v in ctx.items() if k != 'context_id'})
    a['sha256'] = S.typed.O.digest({k: a[k] for k in ('schema_version', 'source', 'memory')})
    return a


def forge(archive, new, recs, sources):
    """A self-consistent archive whose included records bypass recall: every record
    reconstructs from its source, notes are canonical, and hashes/context_id are recomputed."""
    a = json.loads(I.encode(archive))
    d = a['memory']['context']['assessed_descriptions']
    open_to = lambda r: replace(new, investigation_id='permissive', registered_ms=10 ** 15, primary_trigger=r.family,
                                measurement=replace(new.measurement, catalog_id=r.catalog_id),
                                state=replace(new.state, config_id=r.config_id, symbol=r.symbol))
    d['records'] = [json.loads(I.encode(asdict(r))) for r in recs]
    d['notes'] = [json.loads(I.encode(M.retrieve_assessed(open_to(r), [r])['notes'][0])) for r in recs]
    d['audit'] = [{'case_id': r.case_id, 'reason': 'compatible_prior_assessed_description'} for r in recs]
    d['source_archives'] = sources
    return _recompute(a)


@pytest.fixture
def exported(tmp_path):
    (tmp_path / 'p').mkdir(); (tmp_path / 'c').mkdir()
    snap, inv, update = positioning_assessed(tmp_path / 'p')
    csnap, cinv, cupdate = correlation_assessed(tmp_path / 'c')
    others = {k: later_assessed(snap, inv, k) for k in (1, 2, 3)}
    for k, edit, shift in (('config', dict(config_id='cfg_other'), 4), ('symbol', dict(symbol='S9/USDT'), 0)):
        others[k] = later_assessed(snap, replace(inv, state=consistent(replace(inv.state, **edit))), shift)
    new, _ = later_assessed(snap, inv, 5)                   # the receiving registration
    with C.ledger(tmp_path / 'm.db') as db:
        store(db, inv, snap.bars, [update])
        store(db, cinv, csnap.bars, [cupdate])
        for o, u in others.values():
            store(db, o, snap.bars, [u])
        now = PF.NOW + 4 * I.TF + 1
        assert S.ingest(db, now)['assessed']['refused'] == []
        db.execute('INSERT INTO cases VALUES (?,?,?,?,NULL,?)', (new.investigation_id, new.episode_id,
                   new.state.symbol, new.registered_ms, I.encode(asdict(new))))
        C._save_inputs(db, new.investigation_id, snap.bars, set(new.state.input_versions))
        S.register(db, new)
        archive = json.loads(I.encode(S.export_case(db, new.investigation_id)))
        srcs = {k: S.source_archive(db, o.investigation_id) for k, (o, _) in others.items()}
        srcs['base'] = S.source_archive(db, inv.investigation_id)
        srcs['family'] = S.source_archive(db, cinv.investigation_id)
    recs = {k: S.replay_assessed(raw, now + 1) for k, raw in srcs.items()}
    return archive, new, recs, srcs


def test_untouched_export_replays_and_forge_helper_is_faithful(exported):
    archive, new, recs, srcs = exported
    d = archive['memory']['context']['assessed_descriptions']
    assert len(d['records']) == M.MAX_RETRIEVED and len(d['audit']) == 4        # one beyond capacity, no archive
    assert S.replay_export(archive)['memory'] == archive['memory']
    ordered = [M.assessed_from_dict(r) for r in d['records']]
    by_id = {raw['investigation']['investigation_id']: raw for raw in srcs.values()}
    faithful = forge(archive, new, ordered, [by_id[r.source_id] for r in ordered])
    faithful['memory']['context']['assessed_descriptions']['audit'] = d['audit']
    assert _recompute(faithful) == archive


@pytest.mark.parametrize('case', ['after', 'same_time', 'family', 'catalog', 'config', 'symbol',
                                  'duplicate', 'capacity', 'order'])
def test_replay_rejects_records_recall_would_not_include(exported, case):
    archive, new, recs, srcs = exported
    base, src = recs['base'], srcs['base']
    if case in ('after', 'same_time'):
        at = new.registered_ms + (1 if case == 'after' else 0)
        chosen = [(S.replay_assessed(src, at), src)]
    elif case == 'catalog':
        raw = json.loads(json.dumps(src))
        raw['investigation']['measurement']['catalog_id'] = I.CORRELATION_CATALOG_ID
        chosen = [(replace(base, catalog_id=I.CORRELATION_CATALOG_ID), raw)]
    elif case == 'duplicate':
        chosen = [(base, src), (base, src)]
    elif case == 'capacity':
        chosen = [(recs[k], srcs[k]) for k in (3, 2, 1, 'base')]
    elif case == 'order':
        chosen = [(recs[k], srcs[k]) for k in (1, 2, 3)]
    else:
        chosen = [(recs[case], srcs[case])]
    forged = forge(archive, new, [r for r, _ in chosen], [s for _, s in chosen])
    # A catalog foreign to its family cannot even be reconstructed from its source.
    with pytest.raises(ValueError, match='^assessed_protocol_mismatch$' if case == 'catalog'
                       else '^memory_archive_prior_assessed_'):
        S.replay_export(forged)


@pytest.mark.parametrize('edit', [
    dict(authority='trading_evidence'),
    dict(text='An earlier case predicts this case will be useful; its success suggests a long edge.')])
def test_replay_rejects_notes_given_authority_or_predictive_text(exported, edit):
    archive, *_ = exported
    forged = json.loads(json.dumps(archive))
    forged['memory']['context']['assessed_descriptions']['notes'][0].update(edit)
    with pytest.raises(ValueError, match='memory_archive_prior_assessed_ineligible'):
        S.replay_export(_recompute(forged))
