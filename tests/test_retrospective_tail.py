"""retrospective_load: bounded tail selection picks EXACTLY the revisions the whole-series window query picks, and does far less work."""
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.core.types import TF_MS
from trader.data import market_provenance as mp

TF = TF_MS['1h']
T0 = 1_700_000_000_000 // TF * TF
KEY = 'candle:A:1h'
N = 1500


def frame(idx, close_shift=0.0, bad=()):
    ts = pd.to_datetime([T0 + i * TF for i in idx], unit='ms', utc=True)
    close = np.array([100.0 + i * 0.01 + close_shift for i in idx])
    for k, i in enumerate(idx):
        if i in bad:
            close[k] = np.nan                                  # an INVALID revision (no close)
    return pd.DataFrame(dict(ts=ts, open=close, high=close * 1.001, low=close * .999, close=close, volume=np.full(len(idx), 5.0)))


def revise(conn, idx, received, **kw):
    df = frame(idx, **kw)
    out = mp.annotate(df, instrument_id='offline:A', source='offline', kind='candle', timeframe='1h', received_ms=received)
    mp.append(conn, KEY, out)


@pytest.fixture(scope='module')
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp('rt') / 'rev.db'
    conn = sqlite3.connect(path)
    mp.init(conn)
    base = T0 + (N + 5) * TF
    revise(conn, range(N), base)                                   # first revision of every bar
    revise(conn, range(0, N, 3), base + 1000, close_shift=1.0)     # later revision of every third bar
    revise(conn, range(0, N, 7), base + 2000, close_shift=2.0)     # a still later one for every seventh
    revise(conn, [N - 3, N - 10, 40], base + 3000, bad=(N - 3, N - 10, 40))   # the LATEST revision is invalid
    conn.commit()
    return conn


def oracle(conn, cut_ms, start, end, limit):
    """The previous whole-series statement (window rank over the entire bounded series), kept as the reference."""
    clauses = 'series_key=? AND event_ms>=? AND event_ms<=?'
    args = [KEY, start, cut_ms - TF]
    if end is not None:
        clauses += ' AND event_ms<?'
        args.append(end)
    return [r[1] for r in conn.execute(
        'SELECT record_json,revision_id,event_ms,available_ms,observed_ms FROM market_revisions WHERE rowid IN (SELECT selected_rowid FROM ('
        'SELECT rowid AS selected_rowid,event_ms,ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) ordinal '
        'FROM market_revisions WHERE ' + clauses + ') WHERE ordinal=1 ORDER BY event_ms DESC LIMIT ?) ORDER BY event_ms DESC', args + [limit])][::-1]


def usable(conn, ids):
    out = []
    for rid in ids:
        raw = conn.execute('SELECT record_json FROM market_revisions WHERE revision_id=?', (rid,)).fetchone()[0]
        r = mp.read_record(conn, raw)
        if r['bar_state'] == 'FINAL' and r['quality'] in ('VALID', 'UNKNOWN'):
            out.append(rid)
    return out


@pytest.mark.parametrize('limit,start,end,cut_off', [(5, 0, None, 0), (50, 0, None, 0), (200000, 0, None, 0), (30, T0 + 100 * TF, None, 0),
                                                     (30, 0, T0 + 900 * TF, 0), (30, T0 + 200 * TF, T0 + 260 * TF, 0), (40, 0, None, 700)])
def test_selected_revisions_equal_the_whole_series_window_query(db, limit, start, end, cut_off):
    cut = T0 + (N - cut_off) * TF
    got = mp.retrospective_load(db, KEY, cut_ms=cut, tf='1h', start_ms=start, end_ms=end, limit=limit)
    want = usable(db, oracle(db, cut, start, end, limit))
    assert (list(got['revision_id']) if got is not None else []) == want


def test_a_latest_invalid_revision_never_falls_back_to_an_older_valid_one(db):
    got = mp.retrospective_load(db, KEY, cut_ms=T0 + N * TF, tf='1h', limit=40)
    stamps = set(int(x) for x in got['event_time_ms'])
    assert T0 + (N - 3) * TF not in stamps and T0 + (N - 10) * TF not in stamps      # their latest revision is invalid: the bar is absent


def count_ops(conn, fn):
    n = [0]
    conn.set_progress_handler(lambda: n.__setitem__(0, n[0] + 1) or 0, 50)
    try:
        fn()
    finally:
        conn.set_progress_handler(None, 0)
    return n[0]


def test_a_bounded_tail_does_work_proportional_to_the_tail_not_the_history(db):
    cut = T0 + N * TF
    tail = count_ops(db, lambda: mp.retrospective_load(db, KEY, cut_ms=cut, tf='1h', limit=5))
    whole = count_ops(db, lambda: oracle(db, cut, 0, None, 5))
    assert tail * 5 < whole, (tail, whole)
    plan = ' '.join(r[3] for r in db.execute(
        "EXPLAIN QUERY PLAN SELECT DISTINCT event_ms FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=? ORDER BY event_ms DESC LIMIT ?",
        (KEY, 0, cut, 5)))
    assert 'market_revision_cut' in plan and 'TEMP B-TREE' not in plan.upper().replace('USE TEMP', 'TEMP')


def test_shared_text_is_read_once_but_a_new_load_detects_corruption():
    con=sqlite3.connect(':memory:');mp.init(con)
    source='retained venue description '*100
    df=mp.annotate(frame(range(5)),instrument_id='offline:A',source=source,kind='candle',timeframe='1h',received_ms=T0+10*TF)
    mp.append(con,KEY,df);con.commit()
    reads=[]
    con.set_trace_callback(lambda sql:reads.append(sql) if sql.startswith('SELECT raw_json FROM market_raw_sources') else None)
    out=mp.retrospective_load(con,KEY,cut_ms=T0+10*TF,tf='1h',limit=5)
    assert len(out)==5 and out['source'].eq(source).all()
    assert len(reads)==1 # one shared body, no repeated SQLite fetch per candle
    con.set_trace_callback(None)
    con.execute("UPDATE market_raw_sources SET raw_json='corrupt'")
    with pytest.raises(ValueError,match='missing_or_corrupt'):
        mp.retrospective_load(con,KEY,cut_ms=T0+10*TF,tf='1h',limit=5)
