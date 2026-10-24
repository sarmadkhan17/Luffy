"""Scheduled research consumes existing question contracts without mutating its source."""
import hashlib,time
from contextlib import closing
import pytest
from trader.brain import ideas
from trader.core.journal import Journal
from trader.research import brain_intake,predictive_bridge as B
from trader.research.closure import ClosureRefused,study


def test_current_question_is_retained_exactly_once_and_source_is_unchanged(tmp_path):
    source=tmp_path/'journal.db';bank=tmp_path/'bank.db';j=Journal(source)
    text=('Intraday order flow autocorrelation predicts daily volatility decay. '
          'This unverified hypothesis requires a baseline and independent falsification. ')
    assert ideas.record(j,dict(idea_id='retained-source',title='Unverified source',text=text,source='fixture',url='https://example.test/paper'),streams=('research',))
    j._conn().execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    now=int(time.time()*1000)+1000
    assert study.refresh_question_bank(source,bank,now_ms=now)==dict(accepted=1,refused=0,deferred=0)
    assert study.refresh_question_bank(source,bank,now_ms=now)==dict(accepted=0,refused=0,deferred=0)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert ideas.pending(j,stream='research',now_ms=now) # consumption belongs only to the Bank
    with closing(B.store(bank)) as db:
        [question]=B.records(db,'bridge_questions');[retained]=B.records(db,'bridge_sources')
        assert brain_intake.question(retained)==question
        assert question['authority']=='QUESTION_ONLY' and question['classification']=='UNTESTED'
        for table in ('bridge_hypotheses','bridge_experiments','bridge_bank_results'):
            assert not B.records(db,table)


def test_bank_cannot_alias_source_or_other_research_inputs(tmp_path):
    source=tmp_path/'journal.db';j=Journal(source)
    with pytest.raises(ClosureRefused,match='source_destination_alias'):
        study.refresh_question_bank(source,source,now_ms=1)
    protected=tmp_path/'snapshot.db';protected.write_bytes(b'unmodified')
    with pytest.raises(ClosureRefused,match='source_destination_alias'):
        study.refresh_question_bank(source,protected,now_ms=1,forbidden_paths=[protected])
    assert protected.read_bytes()==b'unmodified'
