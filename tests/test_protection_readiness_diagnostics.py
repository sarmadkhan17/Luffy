"""Offline diagnostics: retain refusal causes without changing admission."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.test_bootstrap_contract import protection, phase_b
from tests.test_dashboard_readiness import ready
from trader.observability import preflight as P
from trader.engine.protection_snapshot import ProtectionMonitor, SnapshotStore


@pytest.mark.parametrize('case,reason', [
    ('missing', 'protection_missing'),
    ('stale', 'protection_stale_or_future'),
    ('future', 'protection_stale_or_future'),
    ('unverified', 'protection_unverified'),
    ('book_changed', 'protection_current_book_changed'),
    ('prior_boot', 'protection_prior_boot'),
])
def test_exact_rejection_and_same_receipt_are_retained(ready, case, reason):
    snap = protection(ready)
    if case in ('stale', 'future'):
        offset = -121 if case == 'stale' else 1
        stamp = datetime.fromtimestamp(ready.now + offset, timezone.utc).isoformat()
        snap.update(checked_at=stamp, completed_at=stamp)
    if case == 'unverified':
        snap['checks']['venue_positions'] = False
    if case == 'prior_boot':
        snap['generation']['boot'] = 1
    raw = json.dumps(snap)
    with sqlite3.connect(ready.root / 'data/luffy.db') as db:
        db.execute('UPDATE protection_evidence SET boot=?,value=?', (snap['generation']['boot'], raw))
        if case == 'missing':
            db.execute('DELETE FROM protection_evidence')
        if case == 'book_changed':
            db.execute("INSERT INTO trades VALUES ('t','BTC/USDT','long',1,10,'stop','open')")
    health_before = (ready.root / 'data/safety_health.json').read_bytes()
    result = phase_b(ready)
    assert not result['allow'] and not result['trading_authority']
    assert result['reasons'] == ['fresh_protection_reconciliation_failed:ValueError']
    failure = result['facts']['protection_failure']
    assert failure['reason_code'] == reason
    receipt = failure['receipt']
    assert receipt['record_present'] is (case != 'missing')
    assert receipt['checked_by_gate_at'] == ready.now
    if case != 'missing':
        assert receipt['record_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
        assert receipt['position_count'] == 0
        assert receipt['seq'] == 1
    assert (ready.root / 'data/safety_health.json').read_bytes() == health_before
    with sqlite3.connect(ready.root / 'data/luffy.db') as db:
        assert dict(db.execute('SELECT key,value FROM state_kv'))['control_state'] == 'FROZEN'


def test_zero_positions_still_pass_and_diagnostics_do_not_change_result(ready):
    protection(ready)
    assert phase_b(ready)['allow']
    with sqlite3.connect(ready.root / 'data/luffy.db') as db:
        db.row_factory = sqlite3.Row
        metadata = {}
        assert P._protection(db, ready.now) == P._protection(db, ready.now, diagnostics=metadata)
    assert metadata['position_count'] == 0


def test_unknown_exception_text_is_not_exposed(ready, monkeypatch):
    protection(ready)
    def fail(*args, **kwargs):
        raise ValueError('secret-credential-do-not-log')
    monkeypatch.setattr(P, '_protection', fail)
    result = phase_b(ready)
    assert not result['allow']
    assert result['facts']['protection_failure']['reason_code'] == 'unclassified'
    assert 'secret-credential' not in json.dumps(result)


def test_real_sqlite_contention_is_identified_without_publishing(tmp_path, caplog):
    store = SnapshotStore(tmp_path / 'evidence.db', timeout_s=.01)
    monitor = ProtectionMonitor(SimpleNamespace(), store)
    original = {'fixture': 'unchanged'}
    assert store.publish(original, boot=monitor._boot, seq=1, max_seq=1)
    with sqlite3.connect(store.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        assert not monitor._publish({'fixture': 'new'}, (monitor._boot, 2))
        blocker.rollback()
    assert store.read()['value'] == original
    assert monitor.stats['publish_failed'] == 1
    assert 'sqlite_errorcode=5' in caplog.text
    assert f'boot={monitor._boot} seq=2' in caplog.text
    assert str(tmp_path) not in caplog.text
