"""Regression: the study snapshot must replay the source schema in dependency order.

Incident (compositional research, PREP_FAILED at 2026-10-25 06:00 .. 2026-10-26 18:00 UTC):
    sqlite3.OperationalError: no such table: main.market_revisions
    raised by `dst.execute(sql)` in publish_exact_cut.

Root cause: `_ddl` ordered the schema it replays with `ORDER BY type DESC`. Descending, the
types sort 'trigger' > 'table' > 'index', so triggers were created BEFORE their tables.
Until 9bccd678 (2026-10-25) the market store had no triggers, so the order was harmless;
once `market_witness_*` triggers existed on `market_revisions`, every publication failed.
The source schema is replayed from the real initialiser here, not hand-written, so the
fixture cannot drift away from what production creates.
"""
import sqlite3

import pandas as pd
import pytest

from trader.data import market_provenance as mp
from trader.research.closure.publish import _ddl, publish_exact_cut

STEP = 14400000
TABLES = ('candles', 'candle_floor', 'refs', 'market_revisions', 'market_raw_sources')


def _source(path):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE candles(symbol TEXT NOT NULL,tf TEXT NOT NULL,ts INTEGER NOT NULL,open REAL,high REAL,'
                   'low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,tf,ts))')
        db.execute('CREATE TABLE candle_floor(symbol TEXT NOT NULL,tf TEXT NOT NULL,first_ts INTEGER NOT NULL,'
                   'window_version TEXT,PRIMARY KEY(symbol,tf))')
        db.execute('CREATE TABLE refs(key TEXT NOT NULL,ts INTEGER NOT NULL,open REAL,high REAL,low REAL,close REAL,'
                   'volume REAL,PRIMARY KEY(key,ts))')
        db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)',
                       [('A', '4h', i * STEP, 10, 12, 9, 11, 100) for i in range(8)])
        mp.init(db)
        frame = pd.DataFrame(dict(ts=pd.to_datetime([i * STEP for i in range(8)], unit='ms', utc=True),
                                  open=10., high=12., low=9., close=10., volume=100.))
        receipt = mp.annotate(frame, instrument_id='binance:future:A', source='fixture', kind='candle',
                              received_ms=20 * STEP, timeframe='4h')
        mp.append(db, 'candle:A:4h', receipt)


def _names(db, kind, table=None):
    sql = 'SELECT name FROM sqlite_master WHERE type=?'
    args = [kind]
    if table:
        sql += ' AND tbl_name=?'
        args.append(table)
    return {r[0] for r in db.execute(sql, args)}


@pytest.fixture
def src(tmp_path):
    path = tmp_path / 'source.db'
    _source(path)
    return path


def test_fixture_carries_the_production_witness_triggers(src):
    """Guard: without triggers on market_revisions this file would test nothing."""
    with sqlite3.connect(src) as db:
        assert {'market_witness_ins2', 'market_witness_upd2', 'market_witness_del2'} <= _names(db, 'trigger', 'market_revisions')
        assert 'market_revision_cut' in _names(db, 'index', 'market_revisions')


def test_schema_replay_creates_every_table_before_any_dependent_object(src):
    with sqlite3.connect(src) as db:
        statements = _ddl(db, [t for t in TABLES if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()])
    kinds = [s.split(None, 2)[1].upper() for s in statements]            # CREATE <TABLE|INDEX|TRIGGER|VIEW> ...
    assert 'TRIGGER' in kinds, 'fixture lost its triggers'
    last_table = max(i for i, k in enumerate(kinds) if k == 'TABLE')
    first_dependent = min(i for i, k in enumerate(kinds) if k != 'TABLE')
    assert last_table < first_dependent, kinds
    with sqlite3.connect(':memory:') as empty:                              # replaying in order must not raise
        for sql in statements:
            empty.execute(sql)


def test_publish_succeeds_from_a_store_with_triggers_and_keeps_them(src, tmp_path):
    dst = tmp_path / 'published.db'
    out = publish_exact_cut(src, dst, tf='4h', window_bars=8, now_ms=9 * STEP)
    assert out['revisions'] == 8 and out['rows'] == 8
    with sqlite3.connect(dst) as db:
        assert {'market_witness_ins2', 'market_witness_upd2', 'market_witness_del2'} <= _names(db, 'trigger', 'market_revisions')
        assert db.execute('SELECT COUNT(*) FROM market_revisions').fetchone()[0] == 8
        # the copied rows fired the witness trigger, so the series is registered in the snapshot
        assert db.execute("SELECT COUNT(*) FROM market_series_meta WHERE series_key='candle:A:4h'").fetchone()[0] == 1


def test_the_old_type_desc_ordering_fails_with_the_production_error(src):
    """Documents the mechanism: the replaced `ORDER BY type DESC` runs triggers first."""
    with sqlite3.connect(src) as db:
        marks = ','.join('?' * len(TABLES))
        old = [r[0] for r in db.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name IN (%s) AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%%' "
            "ORDER BY type DESC" % marks, TABLES)]
    assert old[0].lstrip().upper().startswith('CREATE TRIGGER')
    with sqlite3.connect(':memory:') as empty:
        with pytest.raises(sqlite3.OperationalError, match='no such table: main.market_revisions'):
            for sql in old:
                empty.execute(sql)
