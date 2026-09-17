"""scan_overlap() is the CLI's own read-only, malformed/truncation-aware
journal scan feeding emergency_whole_accounting.capture()'s overlap_scan
attestation. An empty overlap-id list must never be trusted on its own; these
tests exercise the scan directly against a real sqlite fixture."""
import json
import sqlite3
import subprocess

import pytest

import scripts.capture_emergency_whole_accounting as cli
from trader.engine.recovery import KEY as RECOVERY_KEY


def make_intent(iid='intent', symbol='BTC/USDT', created_ms=1000, flat_verified_ms=2000):
    return dict(id=iid, symbol=symbol, created_ms=created_ms, flat_verified_ms=flat_verified_ms)


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path/'journal.db')
    conn.execute('CREATE TABLE trades (id TEXT PRIMARY KEY, symbol TEXT, opened_at TEXT, closed_at TEXT)')
    conn.execute('CREATE TABLE execution_accounting (id TEXT PRIMARY KEY, payload TEXT)')
    conn.execute('CREATE TABLE state_kv (key TEXT PRIMARY KEY, value TEXT)')
    conn.commit()
    return conn


def test_no_conflicts_reports_clean_scan_with_counts(db):
    intent = make_intent()
    trades, intents, scan = cli.scan_overlap(db, intent)
    assert trades == [] and intents == []
    assert scan == dict(trades_scanned=0, intents_scanned=0, truncated=False,
                        malformed_intent_ids=[], malformed_trade_ids=[], active_recovery_conflict=False)


def test_overlapping_journal_trade_detected(db):
    db.execute("INSERT INTO trades VALUES ('t1','BTC/USDT','1970-01-01T00:00:00.5+00:00',NULL)")
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert trades == ['t1']
    assert scan['trades_scanned'] == 1


def test_trade_opened_and_closed_entirely_before_intent_began_is_not_a_conflict(db):
    db.execute("INSERT INTO trades VALUES ('t1','BTC/USDT','1970-01-01T00:00:00.1+00:00','1970-01-01T00:00:00.2+00:00')")
    trades, intents, scan = cli.scan_overlap(db, make_intent(created_ms=1000, flat_verified_ms=2000))
    assert trades == []


def test_trade_reopened_after_flat_verified_is_still_a_conflict(db):
    """The old (buggy) query bounded overlap detection to opened_at <=
    flat_verified_ms, which silently missed a symbol reopened afterwards —
    exactly the window the whole-trade capture's own fill/funding history
    still extends through (it queries out to "now", not to flat_verified_ms).
    """
    db.execute("INSERT INTO trades VALUES ('t1','BTC/USDT','1970-01-01T00:00:05+00:00','1970-01-01T00:00:06+00:00')")
    trades, intents, scan = cli.scan_overlap(db, make_intent(created_ms=1000, flat_verified_ms=2000))
    assert trades == ['t1']


def test_trade_still_open_is_a_conflict_regardless_of_when_it_started(db):
    db.execute("INSERT INTO trades VALUES ('t1','BTC/USDT','1970-01-01T00:00:00.1+00:00',NULL)")
    trades, intents, scan = cli.scan_overlap(db, make_intent(created_ms=1000, flat_verified_ms=2000))
    assert trades == ['t1']


def test_malformed_trade_timestamp_is_refused_not_ignored(db):
    db.execute("INSERT INTO trades VALUES ('t1','BTC/USDT','not-a-timestamp',NULL)")
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert trades == []  # not silently treated as a conflict...
    assert scan['malformed_trade_ids'] == ['t1']  # ...but explicitly flagged for refusal

    db.execute("INSERT INTO trades VALUES ('t2','BTC/USDT','1970-01-01T00:00:00.1+00:00','also-not-a-timestamp')")
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert 't2' in scan['malformed_trade_ids']


def test_trade_chronology_violation_is_malformed_not_silently_no_conflict(db):
    """opened_at parses to AFTER closed_at — an internally corrupt row. The
    old code only ever checked closed_ms against intent.created_ms, so this
    row would have silently read as "closed well before this intent began,
    no conflict" instead of being refused as undecidable."""
    db.execute("INSERT INTO trades VALUES ('t1','BTC/USDT','1970-01-01T00:00:05+00:00','1970-01-01T00:00:01+00:00')")
    trades, intents, scan = cli.scan_overlap(db, make_intent(created_ms=2000, flat_verified_ms=3000))
    assert trades == []
    assert scan['malformed_trade_ids'] == ['t1']


def test_overlapping_emergency_intent_detected(db):
    other = dict(id='other', symbol='BTC/USDT', created_ms=1500, flat_verified_ms=2500)
    db.execute("INSERT INTO execution_accounting VALUES ('other', ?)", (json.dumps(other),))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert intents == ['other']
    assert scan['intents_scanned'] == 1
    assert scan['malformed_intent_ids'] == []


def test_intent_created_entirely_after_this_intents_flat_is_still_detected(db):
    other = dict(id='other', symbol='BTC/USDT', created_ms=2500, flat_verified_ms=3000)
    db.execute("INSERT INTO execution_accounting VALUES ('other', ?)", (json.dumps(other),))
    trades, intents, scan = cli.scan_overlap(db, make_intent(created_ms=1000, flat_verified_ms=2000))
    assert intents == ['other']


def test_intent_flat_before_this_intent_began_is_not_a_conflict(db):
    other = dict(id='other', symbol='BTC/USDT', created_ms=100, flat_verified_ms=200)
    db.execute("INSERT INTO execution_accounting VALUES ('other', ?)", (json.dumps(other),))
    trades, intents, scan = cli.scan_overlap(db, make_intent(created_ms=1000, flat_verified_ms=2000))
    assert intents == []


def test_malformed_archived_row_is_refused_not_ignored(db):
    db.execute("INSERT INTO execution_accounting VALUES ('bad', 'not json')")
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert intents == []  # not silently treated as a conflict...
    assert scan['malformed_intent_ids'] == ['bad']  # ...but explicitly flagged for refusal

    db.execute("INSERT INTO execution_accounting VALUES ('bad2', ?)",
              (json.dumps(dict(id='bad2', symbol='BTC/USDT', created_ms='not-an-int', flat_verified_ms=2000)),))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert 'bad2' in scan['malformed_intent_ids']


def test_archived_intent_malformed_symbol_form_is_flagged(db):
    other = dict(id='other', symbol='BTCUSDT', created_ms=1500, flat_verified_ms=2500)  # venue form, not canonical
    db.execute("INSERT INTO execution_accounting VALUES ('other', ?)", (json.dumps(other),))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert intents == []
    assert 'other' in scan['malformed_intent_ids']


def test_archived_intent_clock_ordering_violation_is_malformed(db):
    other = dict(id='bad3', symbol='BTC/USDT', created_ms=2000, flat_verified_ms=1000)  # created after flat
    db.execute("INSERT INTO execution_accounting VALUES ('bad3', ?)", (json.dumps(other),))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert intents == []
    assert 'bad3' in scan['malformed_intent_ids']


def test_archived_payload_id_mismatch_with_row_key_is_malformed(db):
    other = dict(id='not-the-row-key', symbol='BTC/USDT', created_ms=1500, flat_verified_ms=2500)
    db.execute("INSERT INTO execution_accounting VALUES ('other', ?)", (json.dumps(other),))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert intents == []
    assert 'other' in scan['malformed_intent_ids']


def test_scan_truncation_is_flagged(db, monkeypatch):
    monkeypatch.setattr(cli, 'MAX_OVERLAP_SCAN', 1)
    db.execute("INSERT INTO trades VALUES ('t1','BTC/USDT','1970-01-01T00:00:00.5+00:00',NULL)")
    db.execute("INSERT INTO trades VALUES ('t2','BTC/USDT','1970-01-01T00:00:00.6+00:00',NULL)")
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert scan['truncated'] is True


def test_active_recovery_intent_for_same_symbol_conflicts(db):
    db.execute("INSERT INTO state_kv VALUES (?, ?)",
              (RECOVERY_KEY, json.dumps(dict(id='pending', symbol='BTC/USDT'))))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert scan['active_recovery_conflict'] is True


def test_active_recovery_with_this_intents_own_id_still_conflicts(db):
    """Archival plus an active recovery entry cannot both be true of a
    genuinely released, flat position — even if the ids happen to match."""
    db.execute("INSERT INTO state_kv VALUES (?, ?)",
              (RECOVERY_KEY, json.dumps(dict(id='intent', symbol='BTC/USDT'))))
    trades, intents, scan = cli.scan_overlap(db, make_intent(iid='intent'))
    assert scan['active_recovery_conflict'] is True


def test_active_recovery_non_string_symbol_is_malformed_and_conflicts(db):
    """A truthy non-string symbol (e.g. an integer) must not silently
    compare unequal to intent['symbol'] and fall through as "no conflict" —
    it's a malformed pending record, treated the same as unparseable JSON."""
    db.execute("INSERT INTO state_kv VALUES (?, ?)",
              (RECOVERY_KEY, json.dumps(dict(id='pending', symbol=12345))))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert scan['active_recovery_conflict'] is True


def test_active_recovery_different_symbol_does_not_conflict(db):
    db.execute("INSERT INTO state_kv VALUES (?, ?)",
              (RECOVERY_KEY, json.dumps(dict(id='pending', symbol='ETH/USDT'))))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert scan['active_recovery_conflict'] is False


def test_malformed_active_recovery_value_conflicts_rather_than_ignored(db):
    db.execute("INSERT INTO state_kv VALUES (?, ?)", (RECOVERY_KEY, 'not json'))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert scan['active_recovery_conflict'] is True


def test_null_recovery_value_is_not_a_conflict(db):
    db.execute("INSERT INTO state_kv VALUES (?, 'null')", (RECOVERY_KEY,))
    trades, intents, scan = cli.scan_overlap(db, make_intent())
    assert scan['active_recovery_conflict'] is False


def test_cli_refuses_when_archived_payload_id_does_not_match_lookup_key(tmp_path):
    """The row's own primary key is the source of truth for --intent-id; a
    payload whose embedded id disagrees with it must refuse outright. This
    never reaches the exchange, so it needs no live DB or network access."""
    journal = tmp_path/'journal.db'
    conn = sqlite3.connect(journal)
    conn.execute('CREATE TABLE execution_accounting (id TEXT PRIMARY KEY, payload TEXT)')
    conn.execute('CREATE TABLE trades (id TEXT PRIMARY KEY, symbol TEXT, opened_at TEXT, closed_at TEXT)')
    conn.execute('CREATE TABLE state_kv (key TEXT PRIMARY KEY, value TEXT)')
    mismatched = dict(id='different-id', symbol='BTC/USDT', created_ms=100, flat_verified_ms=200)
    conn.execute("INSERT INTO execution_accounting VALUES ('row-key', ?)", (json.dumps(mismatched),))
    conn.commit()
    conn.close()
    out_path = tmp_path/'out.json'
    cmd = ['./venv/bin/python', '-m', 'scripts.capture_emergency_whole_accounting',
          '--journal', str(journal), '--intent-id', 'row-key', '--output', str(out_path)]
    result = subprocess.run(cmd, cwd='/home/sarmad/trader', capture_output=True, text=True)
    assert result.returncode != 0
    assert 'does not match' in result.stderr
    assert not out_path.exists()
