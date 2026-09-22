"""Regression: ``record`` must name its columns and leave ``status`` defaulted.

The opportunities table carries 13 columns while ``record`` supplies 12 values,
so a positional ``INSERT`` raises and the shadow ledger stays empty.
"""
import argparse
import datetime as dt
import json
import sqlite3

import pytest

from scripts import exit_ab_shadow as S
from trader.cognition import dataset as D

DECISION_MS = S.read_json(S.PROTOCOL)['window']['start_ms'] + 7_200_000
OPEN_MS = DECISION_MS - S.TF_MS - 1_000


def _ts(ms):
    # ``record`` parses the journal timestamp as a naive local datetime.
    return dt.datetime.fromtimestamp(ms / 1000).isoformat()


def _journal(path, symbol):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE decisions (id TEXT, ts TEXT, symbol TEXT, signals_json TEXT, scan_id TEXT)')
    db.execute('INSERT INTO decisions VALUES(?,?,?,?,?)', ('cursor', _ts(DECISION_MS - 60_000), symbol, '[]', 'scan-0'))
    db.execute('INSERT INTO decisions VALUES(?,?,?,?,?)', (
        'dec-1', _ts(DECISION_MS), symbol,
        json.dumps([{'strategy_id': S.read_json(S.PROTOCOL)['strategy_id'], 'action': 'BUY'}]), 'scan-1'))
    db.commit(); db.close()


def _receipts(path, universe):
    db = sqlite3.connect(path)
    db.executescript('''
      CREATE TABLE scans (scan_id TEXT PRIMARY KEY, as_of_ms INTEGER NOT NULL, payload TEXT, causes_complete INTEGER DEFAULT 0);
      CREATE TABLE versions (id TEXT PRIMARY KEY, symbol TEXT, tf TEXT, open_ms INTEGER,
        first_seen_ms INTEGER, value_hash TEXT, previous_value_hash TEXT, payload TEXT NOT NULL);
      CREATE TABLE scan_versions (scan_id TEXT, version_id TEXT, PRIMARY KEY(scan_id, version_id));
    ''')
    payload = {'scope': {
        'declaration_version': D.declaration_version(S.read_json(S.DECLARATION)),
        'availability_receipts': [{'symbol': s, 'status': 'available', 'retry_required': False} for s in universe]}}
    db.execute('INSERT INTO scans VALUES(?,?,?,1)', ('declared-1', DECISION_MS, json.dumps(payload)))
    for s in universe:
        vid = f'v-{s}'
        bar = {'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5}
        db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)',
                   (vid, s, '4h', OPEN_MS, OPEN_MS, f'hash-{s}', None, json.dumps(bar)))
        db.execute('INSERT INTO scan_versions VALUES(?,?)', ('declared-1', vid))
    db.commit(); db.close()


@pytest.fixture
def staged(tmp_path, monkeypatch):
    universe = S.read_json(S.DECLARATION)['universe']
    journal, receipt_db, ledger = tmp_path / 'j.db', tmp_path / 'r.db', tmp_path / 'ab.db'
    _journal(journal, universe[0])
    _receipts(receipt_db, universe)
    monkeypatch.setattr(S, 'JOURNAL', journal)
    db = S.init_db(ledger)
    db.execute("INSERT INTO meta(key,value) VALUES('activation',?)", (json.dumps({'decision_id': 'cursor'}),))
    db.commit(); db.close()
    return argparse.Namespace(ledger=ledger, receipt_db=receipt_db), universe[0]


def test_record_names_columns_and_defaults_status(staged, capsys):
    args, symbol = staged

    S.record(args)

    assert json.loads(capsys.readouterr().out)['opportunities_added'] == 1
    with sqlite3.connect(args.ledger) as db:
        rows = db.execute('SELECT key,symbol,side,signal_bar_open_ms,entry_price,status FROM opportunities').fetchall()
    assert rows == [(f'{symbol}|BUY|{OPEN_MS}', symbol, 'BUY', OPEN_MS, 1.5, 'recorded')]


def test_insert_omits_status_so_the_schema_supplies_it(tmp_path):
    db = S.init_db(tmp_path / 'schema.db')
    table = [c[1] for c in db.execute('PRAGMA table_info(opportunities)')]
    db.close()
    assert 'status' in table
    assert list(S.OPPORTUNITY_COLUMNS) == [c for c in table if c != 'status']
