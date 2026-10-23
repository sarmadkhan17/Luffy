"""P2: durable bounded question consumer over the real Bank/Brain-intake
fixtures. Brain passages stay QUESTION_ONLY; nothing defaults to a family."""
import json
import sqlite3

import pytest

from tests.test_brain_research_intake import NOW, TEXT, setup  # noqa: F401  (fixtures)
from tests.test_predictive_research_bridge import population  # noqa: F401
from tests.test_investigation_state_feedback import paths  # noqa: F401
from trader.research import predictive_bridge as B
from trader.research.closure import consumer, families
from trader.research.closure.store import Store

VOLUME = ('Volume expansion confirms a breakout above the channel high. This unverified research hypothesis '
          'requires a registered baseline and out-of-sample falsification. ') * 2


def _bank(setup, add_texts):
    j, brain, bank, add, cycle = setup
    from trader.brain import ideas
    for i, (iid, text) in enumerate(add_texts):
        assert ideas.record(j, dict(idea_id=iid, title='Unverified research', text=text, source='fixture-paper',
                                    url='https://example.test/p'), streams=('research',))
        with j._tx() as db:
            from tests.test_brain_research_intake import stamp
            db.execute('UPDATE brain_events SET ts=? WHERE subject=?', (stamp(NOW - 1000 - i), iid))
    assert cycle()['brain_intake']['accepted'] == len(add_texts)
    return bank


def test_brain_passages_resolve_to_candidate_or_explicit_blocked_reason(setup, tmp_path):
    bank = _bank(setup, [('p-unrelated', TEXT), ('p-volume', VOLUME)])
    st = Store(tmp_path / 's')
    out = consumer.consume(st, bank, now_ms=NOW)
    assert out['status'] == 'OK' and out['compiled'] == 1 and out['unsupported'] == 1
    items = {json.loads(i['payload'])['provenance']['source_bank_id']: i for i in st.items()}
    compiled = [i for i in st.items() if i['status'] == 'COMPILED']
    assert len(compiled) == 1
    prov = json.loads(compiled[0]['payload'])['provenance']
    assert prov['source_type'] == 'BRAIN' and prov['source_authority'] == 'QUESTION_ONLY'
    assert prov['evidence_class'] == 'IDEA_PROVENANCE' and 'result_id' not in prov
    cand = st.get_once('candidates', compiled[0]['candidate_id'])
    assert cand['family_id'] == 'volume_breakout_context.v1'
    unsupported = [i for i in st.items() if i['status'] == 'UNSUPPORTED_FAMILY']
    assert len(unsupported) == 1 and unsupported[0]['candidate_id'] is None      # NOT defaulted to volume breakout
    assert len(st.all_once('candidates')) == 1
    # the Brain source was never rewritten into an investigation chain
    with sqlite3.connect(bank) as db:
        assert db.execute('SELECT COUNT(*) FROM bridge_sources').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM bridge_hypotheses').fetchone()[0] == 0


def test_duplicate_delivery_and_restart_produce_at_most_one_child(setup, tmp_path, monkeypatch):
    bank = _bank(setup, [('p-volume', VOLUME)])
    st = Store(tmp_path / 's')
    real = consumer._finish
    calls = []

    def crash_once(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('process died before commit')
        return real(*a, **k)
    monkeypatch.setattr(consumer, '_finish', crash_once)
    with pytest.raises(RuntimeError):
        consumer.consume(st, bank, now_ms=NOW)
    assert st.items()[0]['status'] == 'PENDING' and not st.all_once('candidates')    # nothing half-written
    consumer.consume(st, bank, now_ms=NOW + 1)
    consumer.consume(st, bank, now_ms=NOW + 2)
    consumer.consume(st, bank, now_ms=NOW + 3)
    assert len(st.items()) == 1 and len(st.all_once('candidates')) == 1 and st.items()[0]['status'] == 'COMPILED'


def test_transient_failures_exhaust_a_bounded_attempt_budget(setup, tmp_path, monkeypatch):
    bank = _bank(setup, [('p-volume', VOLUME)])
    st = Store(tmp_path / 's')
    monkeypatch.setattr(consumer, 'verify_source', lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    for i in range(consumer.MAX_ATTEMPTS + 1):
        consumer.consume(st, bank, now_ms=NOW + i)
    it, = st.items()
    assert it['status'] == 'BLOCKED' and it['reason'] == 'ATTEMPT_BUDGET_EXHAUSTED' and not st.all_once('candidates')


def test_incompatible_source_types_are_isolated(setup, tmp_path):
    bank = _bank(setup, [('p-volume', VOLUME)])
    data = consumer.read_bank(bank)
    q = dict(data['questions'][0])
    sources = {s['bank_id']: s for s in data['sources']}
    # a Brain question dressed as an investigation question is refused, not re-typed
    forged = dict(q, classification='SUPPORTED', prior=[])
    with pytest.raises(consumer._Blocked, match='source_type_mismatch'):
        consumer.verify_source(forged, sources)
    # a question whose text was altered is not derivable from its source
    with pytest.raises(consumer._Blocked, match='brain_question_not_from_source'):
        consumer.verify_source(dict(q, question_id='0' * 64), sources)
    with pytest.raises(consumer._Blocked, match='source_missing'):
        consumer.verify_source(dict(q, source_bank_id='nope'), sources)
    # an investigation-shaped source cannot be used for a Brain-authority question marked with a passage
    fake = {'bank_id': 'x', 'retained_chain_json': '{}'}
    with pytest.raises(consumer._Blocked, match='source_type_mismatch'):
        consumer.verify_source(dict(q, source_bank_id='x'), {'x': fake})


def test_investigation_questions_resolve_with_source_verification(population, tmp_path):
    source_db, bank, candles, now, _end, s = population
    B.cycle(source_db, bank, candles, {'research': {'referee': False, 'handoff': False, 'enabled': False}},
            now_ms=now, submit=False)
    data = consumer.read_bank(bank)
    assert data['questions'] and any('retained_chain_json' in x for x in data['sources'])
    st = Store(tmp_path / 's')
    out = consumer.consume(st, bank, now_ms=now)
    assert out['status'] == 'OK' and out['blocked'] == 0
    for it in st.items():
        prov = json.loads(it['payload'])['provenance']
        assert it['status'] in ('COMPILED', 'UNSUPPORTED_FAMILY', 'WAITING_DATA', 'NO_CHANGE_JUSTIFIED')
        assert prov['source_type'] == 'INVESTIGATION' and prov['intent'] in set(consumer.INTENTS.values())
        if it['status'] == 'COMPILED':
            assert st.get_once('candidates', it['candidate_id'])['family_id'] in families.FAMILIES
        else:
            assert it['candidate_id'] is None
    # a tampered investigation source is blocked, not trusted
    tampered = json.loads(json.dumps(data))
    src = next(x for x in tampered['sources'] if 'retained_chain_json' in x)
    src['symbol'] = 'XXX'
    q = next(q for q in data['questions'] if q['source_bank_id'] == src['bank_id'])
    with pytest.raises(consumer._Blocked, match='investigation_source_unverified'):
        consumer.verify_source(q, {src['bank_id']: src})


def test_unsupported_expressibility_stays_visible():
    from trader.cognition import predictive as P
    q = dict(question_id='q', text='Can the exact target and predictor be expressed by a registered experiment shape?')
    bank = dict(results=[dict(result_id='r', hypothesis_id='h', next_questions=[dict(question_id='q')])],
                hypotheses=[dict(hypothesis=dict(hypothesis_id='h', transformation='exact_volume_path.v1'))])
    status, reason, cand, _ = consumer._resolve(q, 'INVESTIGATION', None, bank)
    assert (status, cand) == ('UNSUPPORTED_FAMILY', None) and 'volume_forward_path' in reason
    bank['hypotheses'][0]['hypothesis']['transformation'] = 'volume_breakout_context.v1'
    status, _, cand, _ = consumer._resolve(q, 'INVESTIGATION', None, bank)
    assert status == 'COMPILED' and cand['family_id'] == 'volume_breakout_context.v1'
    assert consumer._resolve(dict(question_id='z', text='something unregistered'), 'BRAIN', None, bank)[:2] == \
        ('BLOCKED', 'UNREGISTERED_QUESTION_TEMPLATE')
