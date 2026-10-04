"""Frozen numerical research retains exact PIT revisions and stable bytes."""
import hashlib,sqlite3,json,pytest
import pandas as pd
from trader.research import predictive_experiment as E
from trader.cognition import predictive as P
from trader.data.feed import DataFeed
from trader.research.universe import NoExchange,DISCOVERY
from tests.test_investigation_state_feedback import paths  # noqa: F401
from tests.test_predictive_research_bridge import population,CFG  # noqa: F401
from tests.retained_candle_fixtures import qualify


def test_qualified_freeze_preserves_exact_receipts_and_reader_does_not_mutate(population,tmp_path,monkeypatch):
    _,_,source,created,later,s=population
    qualify(source)
    monkeypatch.setattr('trader.data.feed.time.time',lambda:later/1000)
    h=P.propose(s,'volume_breakout_context.v1',created)
    experiment=E.translate(h,source,CFG,later)
    from trader.data import market_provenance as MP
    with sqlite3.connect(source) as db:
        r=json.loads(db.execute('SELECT record_json FROM market_revisions LIMIT 1').fetchone()[0])
        r.update(observed_at_ms=later+1,available_at_ms=later+1,request_started_ms=later+1,request_id='TEST_ONLY_FUTURE')
        r['revision_id']=MP.digest({k:r[k] for k in MP.META if k not in ('revision_id','supersedes')})
        db.execute('INSERT INTO market_revisions VALUES (?,?,?,?,?,?)',
            (r['revision_id'],DataFeed._series_key(DISCOVERY[0],'4h'),r['event_time_ms'],later+1,later+1,MP.encode(r)))
    original=DataFeed(exchange=NoExchange(),db_path=source).cached_ohlcv(DISCOVERY[0],'4h')
    assert original is not None and len(original)==1800
    target=tmp_path/'frozen.db'
    E.freeze_candles(source,target,experiment)
    initial=hashlib.sha256(target.read_bytes()).hexdigest()
    frozen=DataFeed(exchange=NoExchange(),db_path=target).cached_ohlcv(DISCOVERY[0],'4h')
    assert frozen is not None
    pd.testing.assert_frame_equal(frozen,original)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==initial
    with sqlite3.connect(source) as src,sqlite3.connect(target) as dst:
        assert dst.execute('SELECT record_json FROM market_revisions ORDER BY revision_id').fetchall()==src.execute('SELECT record_json FROM market_revisions WHERE available_ms<=? AND observed_ms<=? ORDER BY revision_id',(later,later)).fetchall()
    E.freeze_candles(source,target,experiment)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==initial


def test_legacy_unqualified_projection_stays_unknown_without_mutating_snapshot(population,tmp_path,monkeypatch):
    _,_,source,created,later,s=population
    monkeypatch.setattr('trader.data.feed.time.time',lambda:later/1000)
    experiment=E.translate(P.propose(s,'volume_breakout_context.v1',created),source,CFG,later)
    # Deliberately discard all artificial receipts to reproduce a legacy projection.
    with sqlite3.connect(source) as db: db.execute('DELETE FROM market_revisions')
    target=tmp_path/'legacy-frozen.db';E.freeze_candles(source,target,experiment)
    initial=hashlib.sha256(target.read_bytes()).hexdigest()
    assert DataFeed(exchange=NoExchange(),db_path=target).cached_ohlcv(DISCOVERY[0],'4h') is None
    assert hashlib.sha256(target.read_bytes()).hexdigest()==initial


def test_existing_pre_pit_snapshot_refuses_reuse_without_rewriting(population,tmp_path):
    _,_,source,created,later,s=population
    experiment=E.translate(P.propose(s,'volume_breakout_context.v1',created),source,CFG,later)
    target=tmp_path/'old.db'
    with sqlite3.connect(target) as db:db.execute('CREATE TABLE candles(symbol TEXT)')
    before=target.read_bytes()
    with pytest.raises(ValueError,match='frozen_candle_provenance_unavailable'):
        E.freeze_candles(source,target,experiment)
    assert target.read_bytes()==before
