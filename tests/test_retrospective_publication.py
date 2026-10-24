"""Smaller retrospective inputs preserve actual reader output; replay keeps history."""
import sqlite3

import pandas as pd
import pytest

from trader.data import market_provenance as mp
from trader.research.closure.publish import publish_exact_cut

STEP = 14400000


def source(path, tied=False):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,tf,ts))')
        db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)', [('A','4h',i*STEP,10,12,9,11,100) for i in range(8)])
        mp.init(db)
        for version in range(3):
            frame = pd.DataFrame(dict(ts=pd.to_datetime([i*STEP for i in range(8)],unit='ms',utc=True),
                                      open=10.,high=12.,low=9.,close=10.+version/2,volume=100.))
            receipt = mp.annotate(frame,instrument_id='binance:future:A',source='fixture',kind='candle',
                                  received_ms=20*STEP+(0 if tied else version),timeframe='4h')
            mp.append(db,'candle:A:4h',receipt)


def test_retrospective_copy_matches_reader_but_avoids_unused_versions(tmp_path):
    src=tmp_path/'source.db';source(src)
    before=src.read_bytes()
    latest=tmp_path/'latest.db';full=tmp_path/'full.db'
    small=publish_exact_cut(src,latest,tf='4h',window_bars=8,now_ms=9*STEP,retrospective=True)
    normal=publish_exact_cut(src,full,tf='4h',window_bars=8,now_ms=9*STEP)
    assert small['revision_scope']=='RETROSPECTIVE_LATEST_PER_EVENT'
    assert normal['revision_scope']=='FULL_REPLAY_HISTORY'
    assert small['revisions']==8 and normal['revisions']==24
    with sqlite3.connect(src) as a,sqlite3.connect(latest) as b,sqlite3.connect(full) as c:
        expected=mp.retrospective_load(a,'candle:A:4h',cut_ms=8*STEP,tf='4h')
        for db in (b,c):
            actual=mp.retrospective_load(db,'candle:A:4h',cut_ms=8*STEP,tf='4h')
            pd.testing.assert_frame_equal(actual,expected)
    assert src.read_bytes()==before


def test_corrupt_latest_receipt_is_not_replaced_with_older_valid_data(tmp_path):
    src=tmp_path/'source.db';source(src)
    with sqlite3.connect(src) as db:
        db.execute("UPDATE market_revisions SET record_json='{}' WHERE rowid=(SELECT MAX(rowid) FROM market_revisions)")
    dst=tmp_path/'latest.db'
    publish_exact_cut(src,dst,tf='4h',window_bars=8,now_ms=9*STEP,retrospective=True)
    # Transport preserves even a malformed selected body. The authoritative
    # reader rejects it rather than substituting an earlier valid revision.
    with sqlite3.connect(dst) as db,pytest.raises((KeyError,ValueError)):
        mp.retrospective_load(db,'candle:A:4h',cut_ms=8*STEP,tf='4h')


def test_latest_selection_matches_rowid_tie_break(tmp_path):
    from trader.research.closure.publish import Bounds,_copy_revisions
    src=tmp_path/'source.db';source(src)
    with sqlite3.connect(src) as db,sqlite3.connect(tmp_path/'copy.db') as target:
        # Force an index/body disagreement on the last version. Selection must
        # retain that exact bad winner so the normal reader can reject it.
        db.execute('UPDATE market_revisions SET observed_ms=?',(30*STEP,))
        mp.init(target)
        count={'revisions':0}
        _copy_revisions(db,target,'candle:A:4h',0,7*STEP,None,count,Bounds(10000,10000000,None),True)
        expected=db.execute('SELECT revision_id FROM market_revisions WHERE rowid IN (SELECT MAX(rowid) FROM market_revisions GROUP BY event_ms) ORDER BY event_ms').fetchall()
        assert target.execute('SELECT revision_id FROM market_revisions ORDER BY event_ms').fetchall()==expected
        with pytest.raises(ValueError,match='index_corrupt'):
            mp.retrospective_load(target,'candle:A:4h',cut_ms=8*STEP,tf='4h')


@pytest.mark.parametrize('mode,expected',[('RETROSPECTIVE',True),('REPLAY',False)])
def test_scheduled_publisher_selects_only_the_declared_read_mode(tmp_path,monkeypatch,mode,expected):
    from trader.research.closure import auto,publish
    captured={}
    def publisher(*args,**kwargs):
        captured.update(kwargs)
        return dict(path='private.db',bytes=1,consistency='fixture',rows=1,revisions=1,as_of_ms=1,derivs=None,
                    revision_scope='RETROSPECTIVE_LATEST_PER_EVENT' if kwargs['retrospective'] else 'FULL_REPLAY_HISTORY')
    monkeypatch.setattr(publish,'publish_exact_cut',publisher)
    a=dict(auto.DEFAULTS,development_mode=mode)
    result=auto.publish_job(dict(auto=a,cfg={},now_ms=1,snapshot_dir=str(tmp_path),deadline_s=1,market_db='not-opened'))
    assert captured['retrospective'] is expected
    assert result['candles']['revision_scope']==('RETROSPECTIVE_LATEST_PER_EVENT' if expected else 'FULL_REPLAY_HISTORY')


def test_final_test_reserve_keeps_full_receipt_history(tmp_path):
    src=tmp_path/'source.db';source(src)
    dst=tmp_path/'mixed.db'
    result=publish_exact_cut(src,dst,tf='4h',window_bars=8,now_ms=9*STEP,retrospective=True,
                            coverage_filter=dict(fit_bars=4,reserve_bars=3,max_gap_bars=0))
    assert result['full_revision_history_from_ms']==5*STEP
    with sqlite3.connect(src) as a,sqlite3.connect(dst) as b:
        assert b.execute('SELECT COUNT(*) FROM market_revisions WHERE event_ms<?',(5*STEP,)).fetchone()[0]==5
        expected=a.execute('SELECT * FROM market_revisions WHERE event_ms>=? ORDER BY event_ms,revision_id',(5*STEP,)).fetchall()
        assert b.execute('SELECT * FROM market_revisions WHERE event_ms>=? ORDER BY event_ms,revision_id',(5*STEP,)).fetchall()==expected
        pd.testing.assert_frame_equal(mp.retrospective_load(a,'candle:A:4h',cut_ms=8*STEP,tf='4h'),
                                      mp.retrospective_load(b,'candle:A:4h',cut_ms=8*STEP,tf='4h'))


def test_reserved_replay_preserves_valid_equal_observation_order(tmp_path):
    src=tmp_path/'source.db';source(src,tied=True)
    dst=tmp_path/'mixed.db'
    publish_exact_cut(src,dst,tf='4h',window_bars=8,now_ms=9*STEP,retrospective=True,
                      coverage_filter=dict(fit_bars=4,reserve_bars=3,max_gap_bars=0))
    with sqlite3.connect(src) as a,sqlite3.connect(dst) as b:
        pd.testing.assert_frame_equal(mp.retrospective_load(a,'candle:A:4h',cut_ms=8*STEP,tf='4h'),
                                      mp.retrospective_load(b,'candle:A:4h',cut_ms=8*STEP,tf='4h'))
