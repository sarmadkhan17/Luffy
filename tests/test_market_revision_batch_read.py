import sqlite3
import pytest
from trader.data import market_provenance as P


def test_packet_preserves_every_selected_value_and_order():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE revisions(record_json, revision_id, event_ms, available_ms, observed_ms)')
    values=[('{"text":"café ☃ \\n quote \\\""}', 'a', 2**62, None, 10),
            (b'{"text":"bytes"}', 'b', 123, 124, 10),
            ('{"text":"\\u0000"}', 'c', 125, 126, 11)]
    db.executemany('INSERT INTO revisions VALUES (?,?,?,?,?)',values)
    query='SELECT record_json,revision_id,event_ms,available_ms,observed_ms FROM revisions WHERE event_ms>=? ORDER BY observed_ms DESC,rowid DESC LIMIT ?'
    for args in [(0,1),(0,10),(2**62,10),(2**62+1,10)]:
        expected=db.execute(query,args).fetchall()
        before=db.total_changes
        assert P._selected_revision_rows(db,query,args,batch=True)==expected
        assert P._selected_revision_rows(db,query,args,batch=False)==expected
        assert db.total_changes==before
    db.close()


@pytest.mark.parametrize('kwargs,batched', [
    ({'limit':153},True), ({'limit':400},True),
    ({'limit':513},False), ({'limit':0},False),
    ({'limit':-1},False), ({'limit':True},False),
    ({'limit':400,'replay_tf':'4h'},False),
    ({'limit':400,'revision_stream':True},False),
    ({'limit':400,'window_start_ms':1},False)])
def test_specialized_reads_keep_original_transport(monkeypatch,kwargs,batched):
    db=sqlite3.connect(':memory:');P.init(db)
    selected=P._selected_revision_rows;seen=[]
    def record(*args,**options):
        seen.append(options['batch'])
        return selected(*args,**options)
    monkeypatch.setattr(P,'_selected_revision_rows',record)
    assert P.load(db,'missing',as_of_ms=100,instrument_id='known',**kwargs) is None
    assert seen==[batched]
    db.close()


def test_shared_text_is_read_once_but_every_reference_is_verified():
    import json
    db=sqlite3.connect(':memory:');P.init(db)
    text='immutable source ' * 30;marker=P.text_reference(text);key=marker[len(P.TEXT_REF):]
    db.execute('INSERT INTO market_raw_sources VALUES (?,?,?)',(key,'immutable-text.v1',text))
    raw=json.dumps({'source':marker,'raw_json':'{}'})
    statements=[];db.set_trace_callback(statements.append)
    held={}
    assert P.read_record(db,raw,text_cache=held)==P.read_record(db,raw,text_cache=held)
    assert sum('SELECT raw_json' in q for q in statements)==1
    held[key]='corrupt held value'
    with pytest.raises(ValueError,match='market_source_reference_missing_or_corrupt'):
        P.read_record(db,raw,text_cache=held)
    db.execute('UPDATE market_raw_sources SET raw_json=?',('corrupt stored value',))
    with pytest.raises(ValueError,match='market_source_reference_missing_or_corrupt'):
        P.read_record(db,raw,text_cache={})
    db.execute('DELETE FROM market_raw_sources')
    with pytest.raises(ValueError,match='market_source_reference_missing_or_corrupt'):
        P.read_record(db,raw,text_cache={})
    db.close()
