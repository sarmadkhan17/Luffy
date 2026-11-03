"""Ancestry reads happen before the writer lock; a concurrent commit forces a re-plan."""
import sqlite3
import pandas as pd
from trader.data import market_provenance as mp


def _records(value, observed, event=1_700_000_000_000):
    df = pd.DataFrame({'ts': pd.to_datetime([event], unit='ms', utc=True), 'value': [value]})
    df = mp.annotate(df, instrument_id='binanceusdm:futures:BTCUSDT', source='src', kind='derivative',
                     received_ms=observed)
    return mp.prepare(df)


def _db(path):
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    mp.init(conn)
    conn.commit()
    return conn


def _supersedes(conn):
    return [r[0] for r in conn.execute(
        "SELECT json_extract(record_json,'$.supersedes') FROM market_revisions ORDER BY rowid")]


def test_planned_append_matches_inline_append(tmp_path):
    a, b = _db(tmp_path / 'a.db'), _db(tmp_path / 'b.db')
    for conn, planned in ((a, False), (b, True)):
        for value, observed in ((1.0, 1000), (2.0, 2000)):
            rows = _records(value, observed)
            plan = mp.plan_append(conn, 'k', rows) if planned else None
            conn.execute('BEGIN IMMEDIATE')
            mp.append(conn, 'k', prepared=rows, plan=plan)
            conn.commit()
    for conn in (a, b):   # revision ids are per-call; compare the linkage shape
        ids = [r[0] for r in conn.execute('SELECT revision_id FROM market_revisions ORDER BY rowid')]
        assert len(ids) == 2 and _supersedes(conn) == [None, ids[0]]


def test_concurrent_commit_between_plan_and_write_forces_replan(tmp_path):
    path = tmp_path / 'c.db'
    mine, other = _db(path), sqlite3.connect(path)
    mine.execute('BEGIN IMMEDIATE'); mp.append(mine, 'k', prepared=_records(1.0, 1000)); mine.commit()
    stale = _records(2.0, 2000)
    plan = mp.plan_append(mine, 'k', stale)
    # Another producer commits a newer revision of the same event after the plan.
    other.execute('BEGIN IMMEDIATE'); mp.append(other, 'k', prepared=_records(3.0, 1500)); other.commit()
    mine.execute('BEGIN IMMEDIATE')
    mp.append(mine, 'k', prepared=stale, plan=plan)
    mine.commit()
    ids = [r[0] for r in mine.execute('SELECT revision_id FROM market_revisions ORDER BY rowid')]
    last_parent = mine.execute("SELECT json_extract(record_json,'$.supersedes') FROM market_revisions "
                               "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    # The stale plan would have linked to the first row; the re-plan links to the 1500 one.
    assert last_parent == ids[1] and last_parent != ids[0]
