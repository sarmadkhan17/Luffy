"""RES-05: existing question schema and normal producer, no providers."""
import copy
import json
import sqlite3

import pytest

from tests.test_investigation_state_feedback import paths  # noqa: F401
from tests.test_investigation_research_family import measured, registered, records
from tests.test_res01_question_producer import producer, root_for, retained
from tests.test_intelligence_spine import publish, table
from tests.test_attention_telemetry import frames
from tests.test_res05_bank_persistence import offline  # noqa: F401
from trader.observability import investigation_research as R


def assert_links(chain):
    q = chain['question']
    for run in chain['runs']:
        nexts = run['next_questions']
        for followup, ref in zip(nexts, run['bank']['next_questions']['items'], strict=True):
            assert followup['schema'] == q['schema'] == R.QUESTION_SCHEMA
            assert followup['question_kind'] == q['question_kind']
            assert followup['source'] == q['source']
            assert followup['scope'] == q['scope']
            assert followup['authority'] == 'QUESTION_ONLY'
            assert followup['lineage']['question_id'] == q['question_id']
            assert followup['lineage']['question_sha256'] == R.sha256(R.canonical(q))
            assert followup['lineage']['result_id'] == run['result']['result_id']
            assert followup['lineage']['result_sha256'] == R.sha256(R.canonical(run['result']))
            assert ref['question_id'] == followup['question_id']
            assert ref['canonical_sha256'] == R.sha256(R.canonical(followup))


def test_normal_producer_persists_same_linkage_after_fresh_interpreter_restart(paths):
    root, src, dest, now = root_for(paths)
    publish(src, now)
    assert producer(root, now)['research']['ok'] == 1
    iid = table(dest, 'SELECT id FROM cases')[0][0]
    chain = R.chain(dest, iid)
    assert_links(chain)
    assert chain['runs'][0]['result']['status'] == 'INCONCLUSIVE'
    assert any(q['followup_kind'] == 'MISSING_MEASUREMENT' for q in chain['runs'][0]['next_questions'])
    before = retained(dest)
    assert producer(root, now + 1)['research']['attempted'] == 0
    assert retained(dest) == before
    assert R.chain(dest, iid) == chain


def test_normal_completion_preserves_both_results_and_their_own_followups(paths):
    root, src, dest, now = root_for(paths)
    publish(src, now)
    producer(root, now)
    iid, payload = table(dest, 'SELECT id,payload FROM cases')[0]
    deadline = json.loads(payload)['measurement']['deadline_ms']
    data = frames(6, deadline + 1)
    data['S0/USDT']['4h'].loc[25:29, 'volume'] = 103
    publish(src, deadline + 1, sid='s2', spikes=(), data=data)
    assert producer(root, deadline + 1)['research']['ok'] == 1
    chain = R.chain(dest, iid)
    assert [run['result']['status'] for run in chain['runs']] == ['INCONCLUSIVE', 'SUPPORTED']
    assert_links(chain)
    assert len({q['question_id'] for run in chain['runs'] for q in run['next_questions']}) == 5
    before = retained(dest)
    assert producer(root, deadline + 2)['research']['attempted'] == 0
    assert retained(dest) == before
    assert R.chain(dest, iid) == chain


@pytest.mark.parametrize('path', R.PATHS)
def test_completed_refuted_alternatives_have_typed_followups_and_idempotent_retry(paths, path):
    _, dest, _ = paths
    iid, end = measured(paths, path)
    first = R.run(dest, iid, recorded_at_ms=end + 2)
    chain = R.chain(dest, iid)
    assert_links(chain)
    run = chain['runs'][-1]
    refuted = {p['hypothesis'] for p in run['result']['paths'] if p['status'] == 'REFUTED'}
    followups = [q for q in run['next_questions'] if q['followup_kind'] == 'REFUTED_ALTERNATIVE']
    assert {q['lineage']['hypothesis'] for q in followups} == refuted
    before = records(dest)
    again = R.run(dest, iid, recorded_at_ms=end + 99)
    assert again['next_question_ids'] == first['next_question_ids']
    assert set(again['outcomes'].values()) == {'duplicate'}
    assert records(dest) == before


def test_missing_measurement_and_participant_evidence_never_become_support(paths):
    _, dest, now = paths
    iid, _ = registered(paths)
    R.run(dest, iid, recorded_at_ms=now)
    run = R.chain(dest, iid)['runs'][0]
    assert run['result']['status'] == 'INCONCLUSIVE'
    assert {p['status'] for p in run['result']['paths']} == {'NOT_ASSESSED'}
    assert run['bank']['supporting_evidence']['status'] == 'NOT_CLASSIFIED'
    assert run['result']['unavailable'] == [{'hypothesis': R.PARTICIPANT, 'reason': R.PARTICIPANT_REASON}]
    assert all(q['authority'] == 'QUESTION_ONLY' for q in run['next_questions'])


@pytest.mark.parametrize('mutation', ['lineage', 'text', 'missing', 'extra', 'key'])
def test_rehashed_followup_tampering_or_missing_link_refuses_replay(paths, mutation):
    _, dest, now = paths
    iid, _ = registered(paths)
    R.run(dest, iid, recorded_at_ms=now)
    run = R.chain(dest, iid)['runs'][0]
    q = copy.deepcopy(run['next_questions'][0])
    with sqlite3.connect(dest) as db:
        db.execute('DROP TRIGGER investigation_research_records_no_update')
        db.execute('DROP TRIGGER investigation_research_records_no_delete')
        if mutation == 'missing':
            db.execute('DELETE FROM investigation_research_records WHERE record_id=?', (q['question_id'],))
        elif mutation == 'key':
            db.execute('UPDATE investigation_research_records SET record_key=? WHERE record_id=?', ('wrong', q['question_id']))
        else:
            previous = q.pop('question_id')
            if mutation == 'lineage': q['lineage']['result_id'] = 'f'*64
            else: q['question'] = 'Invented supported cause?'
            q = R._with_id(q, 'question_id')
            text = R.canonical(q)
            if mutation == 'extra':
                db.execute('INSERT INTO investigation_research_records VALUES (?,?,?,?,?,?,?)',
                    (R.QUESTION_SCHEMA, q['question_id'], iid, 'extra', R.sha256(text), text, now))
            else:
                db.execute('UPDATE investigation_research_records SET record_id=?,canonical_json=?,canonical_sha256=? WHERE record_id=?',
                    (q['question_id'], text, R.sha256(text), previous))
    with pytest.raises(R.ResearchRefused, match='followup_rederivation'):
        R.chain(dest, iid)


def test_conflicting_followup_rolls_back_whole_result_transaction(paths):
    _, dest, now = paths
    iid, _ = registered(paths)
    src = R.read_source(dest, iid)
    q = R.build_question(src); p = R.build_plan(q)
    e = R.build_evidence(p, q, src); r = R.build_result(e)
    bad = R.build_next_questions(q, r)[0]
    bad.pop('question_id'); bad['question'] = 'Conflicting next question'
    bad = R._with_id(bad, 'question_id')
    with sqlite3.connect(dest) as db:
        R.schema(db)
        R._store(db, bad, iid, 'followup:'+r['result_id']+':0', now)
    before = records(dest)
    out = R.run(dest, iid, recorded_at_ms=now+1)
    assert out['status'] == 'REFUSED' and 'research_record_conflict' in out['reason']
    assert records(dest) == before


def test_no_registered_followup_yields_none_and_unsupported_shape_refuses(paths):
    _, dest, _ = paths
    iid, _ = registered(paths)
    q = R.build_question(R.read_source(dest, iid))
    assert R.build_next_questions(q, dict(status='SUPPORTED', paths=[], unavailable=[], result_id='a'*64)) == []
    with pytest.raises(R.ResearchRefused, match='unregistered_followup_result'):
        R.build_next_questions(q, dict(status='invented'))


def test_legacy_bank_and_question_identity_remain_readable_and_unchanged(paths):
    _, dest, now = paths
    iid, _ = registered(paths)
    src = R.read_source(dest, iid)
    q = R.build_question(src); p = R.build_plan(q)
    e = R.build_evidence(p, q, src); r = R.build_result(e); run = R.build_run(q,p,e,r)
    bank = R.build_bank(q,p,e,r,run,builder_id=R.LEGACY_BUILDER_ID)
    with sqlite3.connect(dest) as db:
        R.schema(db)
        for rec,key in ((q,iid),(p,iid),(e,e['update_event_id']),(r,e['update_event_id']),
                        (run,e['update_event_id']),(bank,e['update_event_id'])):
            R._store(db,rec,iid,key,now)
    before = records(dest)
    assert R.chain(dest,iid)['runs'][0]['next_questions'] == []
    assert R.run(dest,iid,recorded_at_ms=now+1)['next_question_ids'] == []
    assert records(dest) == before
    assert R.chain(dest,iid)['question'] == q
    from dataclasses import asdict
    from trader.cognition import predictive as P
    legacy_chain = R.chain(dest, iid)
    legacy_run = {k: v for k, v in legacy_chain['runs'][0].items() if k != 'next_questions'}
    source = P.source_from_chain(legacy_chain, legacy_run, now)
    assert P.source_from_dict(asdict(source)) == source
