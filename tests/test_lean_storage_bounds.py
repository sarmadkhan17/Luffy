"""Exact current-window selection and trade-only live outcomes, offline only."""
import json
import sqlite3
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd

from trader.data import market_provenance as mp
from trader.core.journal import Journal
from trader.core.types import Action, Decision, Snapshot
from trader.scheduling.maintenance import Maintenance
from trader.engine.outcomes import resolve_pending

IID='fixture:futures:BTCUSDT'
SOURCE='https://offline.example/klines'
STEP=900000
CUT=1800000000000
KEY='fixture:BTC:15m'


def annotated(events, *, received=CUT, iid=IID, partial=False, price=10):
    n=len(events)
    raw=pd.DataFrame(dict(ts=pd.to_datetime(events,unit='ms',utc=True),
        open=np.full(n,price),high=np.full(n,price+1),low=np.full(n,price-1),
        close=np.full(n,price),volume=np.full(n,100)))
    return mp.annotate(raw,instrument_id=iid,source=SOURCE,kind='candle',
                       received_ms=received,timeframe='15m')


def store():
    db=sqlite3.connect(':memory:');mp.init(db)
    events=np.arange(CUT-420*STEP,CUT-20*STEP,STEP,dtype=np.int64)
    frame=annotated(events)
    mp.append(db,KEY,frame)
    db.commit()
    return db,frame


def test_small_current_read_is_independent_of_large_prior_history():
    db,frame=store()
    raw=json.dumps(dict(instrument_id=IID,source=SOURCE,bar_state='FINAL'))
    rows=[('old:'+str(i),KEY,CUT-(120000-i)*STEP,0,0,raw) for i in range(100000)]
    db.executemany('INSERT INTO market_revisions VALUES(?,?,?,?,?,?)',rows);db.commit()
    steps=[0]
    db.set_progress_handler(lambda:steps.__setitem__(0,steps[0]+1) or 0,1000)
    result=mp.load(db,KEY,as_of_ms=CUT,limit=400,instrument_id=IID,source=SOURCE)
    db.set_progress_handler(None,0)
    assert len(result)==400
    assert result['revision_id'].tolist()==frame['revision_id'].tolist()
    # This measures SQLite work, not an environment-dependent wall threshold.
    # The old rank-entire-history query exceeds thousands of these callbacks.
    assert steps[0]<300, steps[0]


def test_event_first_selection_matches_original_revision_query_with_corrections():
    db,frame=store()
    target=int(mp.ms(frame['ts'])[-1])
    corrected=annotated([target],received=CUT+1,price=20)
    foreign=annotated([CUT-STEP],iid='fixture:futures:OTHER',price=30)
    mp.append(db,KEY,corrected);mp.append(db,KEY,foreign);db.commit()
    for cut in [CUT,CUT+1]:
        for limit in [1,5,400,500]:
            result=mp.load(db,KEY,as_of_ms=cut,limit=limit,instrument_id=IID,source=SOURCE)
            rows=db.execute('''SELECT revision_id FROM (
                SELECT revision_id,event_ms,ROW_NUMBER() OVER(PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) ordinal
                FROM market_revisions WHERE series_key=? AND available_ms<=? AND observed_ms<=?
                AND json_extract(record_json,'$.instrument_id')=?
                AND json_extract(record_json,'$.source') IN (?,?)
                AND json_extract(record_json,'$.bar_state')!='PARTIAL')
                WHERE ordinal=1 ORDER BY event_ms DESC LIMIT ?''',
                (KEY,cut,cut,IID,SOURCE,mp.text_reference(SOURCE),limit)).fetchall()
            assert result['revision_id'].tolist()==[r[0] for r in reversed(rows)]
    assert mp.load(db,KEY,as_of_ms=CUT+1,limit=1,instrument_id=IID,source=SOURCE)['close'].iloc[-1]==20


def test_trade_only_outcomes_leave_old_unexecuted_records_untouched(tmp_path):
    j=Journal(tmp_path/'j.db')
    for ident,executed in [('trade',True),('skip',False)]:
        snap=Snapshot('BTC/USDT','2020-01-01T00:00:00+00:00',10,{})
        d=Decision(ident,ident,snap.symbol,Action.BUY,1,.2,.6,[],[],ts=snap.ts)
        d.executed=executed
        j.log_cycle(snap,ident,'fixture');j.log_decision(d)
        j.schedule_outcome(ident,ident,snap.symbol,snap.ts,'BUY',10)
    n=resolve_pending(j,NS(),now_ms=CUT,executed_only=True)
    assert n==1
    rows={r['decision_id']:r for r in j.query('SELECT * FROM outcomes')}
    assert rows['trade']['resolved_at'] is not None
    assert rows['skip']['resolved_at'] is None
    assert len(rows)==2  # historical evidence was not deleted


def test_live_maintenance_does_not_read_or_write_old_nontrade_backlog(tmp_path,monkeypatch):
    j=Journal(tmp_path/'j.db')
    snap=Snapshot('BTC/USDT','2026-01-01T00:00:00+00:00',10,{})
    d=Decision('skip','skip',snap.symbol,Action.BUY,1,.2,.6,[],[],ts=snap.ts)
    j.log_cycle(snap,'skip','fixture');j.log_decision(d)
    j.schedule_outcome('skip','skip',snap.symbol,snap.ts,'BUY',10)
    feed=NS(fetch_ohlcv=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('legacy nontrade fetch')))
    m=Maintenance(j,{},feed)
    queries=[];j._conn().set_trace_callback(queries.append)
    m.outcomes()
    assert not any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE')) for sql in queries)
    assert j.query('SELECT resolved_at FROM outcomes')[0]['resolved_at'] is None


def test_actual_617_bar_strategy_window_uses_single_transport(monkeypatch):
    db=sqlite3.connect(':memory:');mp.init(db)
    events=np.arange(CUT-820*STEP,CUT-20*STEP,STEP,dtype=np.int64)
    frame=annotated(events);mp.append(db,KEY,frame);db.commit()
    calls=[];original=mp._selected_revision_rows
    def read(*a,**kw):
        calls.append(kw.get('batch'))
        return original(*a,**kw)
    monkeypatch.setattr(mp,'_selected_revision_rows',read)
    result=mp.load(db,KEY,as_of_ms=CUT,limit=617,instrument_id=IID,source=SOURCE)
    assert calls==[True]
    assert result['revision_id'].tolist()==frame['revision_id'].tolist()[-617:]
