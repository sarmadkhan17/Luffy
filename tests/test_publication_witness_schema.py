"""Snapshot receipts keep destination witnesses without source-local triggers."""
import sqlite3
from tests.test_retrospective_publication import source, STEP
from trader.research.closure.publish import publish_exact_cut
from trader.data import market_provenance as mp


def test_publication_rebuilds_own_witnesses_and_preserves_verified_receipts(tmp_path):
    src, dst = tmp_path/'source.db', tmp_path/'snapshot.db'
    source(src)
    with sqlite3.connect(src) as db:
        db.execute('CREATE TABLE source_private_counter(n INTEGER)')
        db.execute('INSERT INTO source_private_counter VALUES (0)')
        db.execute('CREATE TRIGGER source_private AFTER INSERT ON market_revisions '
                   'BEGIN UPDATE source_private_counter SET n=n+1; END')
    before=src.read_bytes()
    result=publish_exact_cut(src,dst,tf='4h',window_bars=8,now_ms=9*STEP,retrospective=True)
    assert result['revisions']==8
    with sqlite3.connect(src) as original, sqlite3.connect(dst) as copied:
        assert mp.initialised(copied)
        names={r[0] for r in copied.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert 'market_witness_ins2' in names
        assert 'source_private' not in names
        assert not copied.execute("SELECT 1 FROM sqlite_master WHERE name='source_private_counter'").fetchone()
        expected=mp.retrospective_load(original,'candle:A:4h',cut_ms=8*STEP,tf='4h')
        actual=mp.retrospective_load(copied,'candle:A:4h',cut_ms=8*STEP,tf='4h')
        assert actual.equals(expected)
        assert copied.execute('SELECT SUM(gen) FROM market_series_meta').fetchone()[0] > 0
    assert src.read_bytes()==before
