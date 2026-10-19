"""D5 offline Brain -> maintained bridge -> verified Bank question evidence."""
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from trader.brain import ideas
from trader.core.journal import Journal
from trader.research.ledger import Ledger
from trader.research import predictive_bridge as B, brain_intake as Q
from trader.cognition import predictive as P, predictive_bank

NOW = 1_790_000_000_000
TEXT = ('Intraday order flow autocorrelation predicts daily volatility decay. '
        'This unverified research hypothesis requires a registered baseline and out-of-sample falsification. ')


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


@pytest.fixture
def setup(tmp_path):
    brain, source, bank, candles = [tmp_path / n for n in ('brain.db', 'source.db', 'bank.db', 'candles.db')]
    j = Journal(brain)
    Ledger(Journal(tmp_path / 'luffy.db')).ensure()
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE investigation_research_records(record_id,investigation_id,recorded_at_ms,canonical_json,canonical_sha256,record_type)')
    def add(iid='passage-1', streams=('research', 'strategy'), url='https://example.test/paper', ts=NOW-1000):
        assert ideas.record(j, dict(idea_id=iid, title='Unverified research', text=TEXT, source='fixture-paper', url=url), streams=streams)
        with j._tx() as db:
            db.execute('UPDATE brain_events SET ts=? WHERE subject=?', (stamp(ts), iid))
    def cycle(at=NOW, **kw):
        def forbidden(*a, **k):
            pytest.fail('question-only input reached quantitative execution')
        return B.cycle(source, bank, candles, {'research': {'referee': False, 'handoff': False, 'enabled': False}},
                       now_ms=at, brain_source_path=brain, run=forbidden, **kw)
    return j, brain, bank, add, cycle


def rows(bank, table):
    with B.store(bank) as db:
        return B.records(db, table)


def test_end_to_end_question_handoff_and_restart(setup):
    j, brain, bank, add, cycle = setup
    add()
    ideas.mark_consumed(j, 'passage-1', 'strategy-refused', stream=ideas.STRATEGY)
    original = brain.read_bytes()
    result = cycle()
    assert result['brain_intake'] == dict(accepted=1, refused=0, deferred=0)
    source, = rows(bank, 'bridge_sources')
    attached = predictive_bank.predictive_extensions(bank, source['bank_id'])
    q, = attached['next_questions']
    assert attached['question_source'] == source
    assert q == Q.question(source) and q['authority'] == 'QUESTION_ONLY'
    assert q['passage']['text'] == TEXT.strip() and q['available_ms'] == NOW-1000
    assert source['idea_id'] == 'passage-1'
    assert json.loads(source['consumption_at_intake'][0]['detail'])['stream'] == 'strategy'
    assert source['queue_row']['ts'] == stamp(NOW-1000)
    assert q['passage']['url'] == 'https://example.test/paper'
    assert not attached['hypotheses'] and not attached['experiments'] and not attached['results']
    consumed, = rows(bank, 'bridge_idea_consumption')
    assert json.loads(consumed['detail'])['stream'] == 'research'
    before = {t: rows(bank, t) for t in B.TABLES}
    assert cycle(NOW+1000)['brain_intake'] == dict(accepted=0, refused=0, deferred=0)
    assert before == {t: rows(bank, t) for t in B.TABLES}
    assert brain.read_bytes() == original
    assert result['source_mutations'] == 0 and result['order_submissions'] == 0
    assert ideas.pending(j, stream='research', now_ms=NOW) # source consumption untouched
    assert ideas.pending(j, stream='strategy', now_ms=NOW) == []


def test_refused_and_deferred_are_persistent_and_retry_exact_source(setup):
    _, _, bank, add, cycle = setup
    add('bad-provenance', url='')
    add('not-available', ts=NOW+1000)
    assert cycle()['brain_intake'] == dict(accepted=0, refused=1, deferred=1)
    assert len(rows(bank, 'bridge_idea_consumption')) == 1
    assert {r['status'] for r in rows(bank, 'bridge_intake_outcomes')} == {'REFUSED', 'DEFERRED'}
    cycle()
    assert len(rows(bank, 'bridge_intake_outcomes')) == 2
    assert cycle(NOW+1000)['brain_intake'] == dict(accepted=1, refused=0, deferred=0)
    assert len(rows(bank, 'bridge_intake_outcomes')) == 3
    assert len(rows(bank, 'bridge_idea_consumption')) == 2
    assert len(rows(bank, 'bridge_questions')) == 1
    assert all(not rows(bank, t) for t in ('bridge_hypotheses', 'bridge_tests', 'bridge_experiments', 'bridge_bank_results'))


@pytest.mark.parametrize('stream', [None, 'research'])
def test_existing_research_or_legacy_consumption_suppresses_handoff(setup, stream):
    j, _, bank, add, cycle = setup
    add()
    ideas.mark_consumed(j, 'passage-1', 'already-used', stream=stream)
    assert cycle()['brain_intake']['accepted'] == 0
    assert rows(bank, 'bridge_questions') == []


def test_duplicate_delivery_and_stream_isolation(setup):
    j, _, bank, add, cycle = setup
    add()
    add('strategy-only', streams=('strategy',))
    with j._tx() as db:
        db.execute('INSERT INTO brain_events(ts,kind,subject,detail) SELECT ts,kind,subject,detail FROM brain_events WHERE subject=?', ('passage-1',))
    assert cycle()['brain_intake']['accepted'] == 1
    assert cycle()['brain_intake']['accepted'] == 0
    assert len(rows(bank, 'bridge_questions')) == 1


def test_atomic_rollback_before_consumption_and_replay(setup, monkeypatch):
    _, _, bank, add, cycle = setup
    add()
    original = B.append
    def crash(db, table, key, value):
        if table == 'bridge_idea_consumption':
            raise RuntimeError('isolated crash')
        return original(db, table, key, value)
    monkeypatch.setattr(B, 'append', crash)
    with pytest.raises(RuntimeError, match='isolated crash'):
        cycle()
    assert not rows(bank, 'bridge_questions') and not rows(bank, 'bridge_intake_outcomes')
    monkeypatch.setattr(B, 'append', original)
    assert cycle()['brain_intake']['accepted'] == 1


def test_provenance_tampering_and_quantitative_authority_refused(setup):
    _, _, bank, add, cycle = setup
    add()
    cycle()
    s, = rows(bank, 'bridge_sources')
    with pytest.raises((TypeError, ValueError, KeyError)):
        P.source_from_dict(s)
    with pytest.raises(ValueError, match='requires_supported'):
        B.factory_source(dict(classification='UNTESTED'), bank)
    changed = json.loads(P.canonical(s))
    changed['queue_row']['detail'] = '{}'
    with pytest.raises(ValueError, match='integrity'):
        Q.question(changed)
    changed = json.loads(P.canonical(s))
    changed['consumption_at_intake'] = [dict(kind=ideas.CONSUMED, subject=s['idea_id'], detail='{}')]
    changed['bank_id'] = P.digest({k: v for k, v in changed.items() if k != 'bank_id'})
    with pytest.raises(ValueError, match='already_consumed'):
        Q.question(changed)
    with B.store(bank) as db:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM bridge_sources')


def test_missing_source_is_durable_deferred(setup):
    _, brain, bank, _, cycle = setup
    brain.unlink()
    assert cycle()['brain_intake']['deferred'] == 1
    assert cycle()['brain_intake']['deferred'] == 1
    assert len(rows(bank, 'bridge_intake_outcomes')) == 1


def test_real_scraper_harvest_to_verified_question(setup):
    from trader.brain.scraper import Scraper
    j, _, bank, _, cycle = setup
    cfg = dict(scraper=dict(enabled=True, ideas_per_cycle=4, tv_pages=0, tv_tags=[], rss_feeds=[]),
               brain=dict(daily_token_budget=0, base_url='', model_fast='', model_deep='',
                          max_tokens_per_call=1, max_tokens_deep=1), strategies={}, risk={})
    scraper = Scraper(j, cfg, feed=None, notifier=None)
    scraper.tags = []
    scraper.scrape_tv_scripts = lambda tag: []
    scraper.scrape_feeds = lambda: [dict(idea_id='scraped-passage', title='Research hypothesis',
        text=TEXT, source='fixture-feed', url='https://example.test/research')]
    assert scraper.harvest_once()['queued_research'] == 1
    with j._tx() as db:
        db.execute('UPDATE brain_events SET ts=?', (stamp(NOW-1),))
    assert cycle()['brain_intake']['accepted'] == 1
    source, = rows(bank, 'bridge_sources')
    attachment = predictive_bank.predictive_extensions(bank, source['bank_id'])
    assert attachment['question_source']['idea_id'] == 'scraped-passage'
    assert attachment['next_questions'][0]['passage']['text'] == TEXT.strip()
    assert attachment['next_questions'][0]['classification'] == 'UNTESTED'
    (bank.parent / 'handoff-evidence.json').write_text(P.canonical(attachment))


def test_budget_lock_and_source_alias(setup):
    import fcntl
    _, brain, bank, add, cycle = setup
    add('one')
    add('two')
    assert cycle(max_sources=1)['brain_intake']['accepted'] == 1
    assert cycle(max_sources=1)['brain_intake']['accepted'] == 1
    assert len(rows(bank, 'bridge_questions')) == 2
    with open(str(bank)+'.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            cycle()
    with pytest.raises(ValueError, match='source_destination_alias'):
        B.cycle(brain, brain, brain, {}, now_ms=NOW, brain_source_path=brain)


def test_foreign_source_schema_is_persistent_refusal(setup):
    _, brain, bank, _, cycle = setup
    with sqlite3.connect(brain) as db:
        db.execute('DROP TABLE brain_events')
    assert cycle()['brain_intake']['refused'] == 1
    cycle()
    assert rows(bank, 'bridge_intake_outcomes')[0]['reason'] == 'brain_source_schema'
    assert len(rows(bank, 'bridge_intake_outcomes')) == 1


def test_fresh_process_restart_keeps_consumption(setup):
    import subprocess
    import sys
    _, brain, bank, add, cycle = setup
    add()
    cycle()
    code = """
import sys
from trader.research import predictive_bridge as B, brain_intake as Q
with B.store(sys.argv[2]) as db:
    print(Q.intake(sys.argv[1], db, B.append, now_ms=int(sys.argv[3]), limit=4))
"""
    out = subprocess.check_output([sys.executable, '-c', code, str(brain), str(bank), str(NOW+1)], text=True)
    assert out.strip() == "{'accepted': 0, 'refused': 0, 'deferred': 0}"
    assert len(rows(bank, 'bridge_questions')) == 1
