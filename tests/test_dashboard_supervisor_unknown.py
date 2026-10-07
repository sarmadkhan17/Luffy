"""Read-only projection regressions: absent checks are not mismatches."""
import json
from datetime import datetime, timedelta, timezone
from trader.dashboard.owner_api import read_supervisor

class JournalRead:
    def __init__(self, record):
        self.record = record
    def kv_get(self, key):
        assert key == 'supervisor_status'
        return json.dumps(self.record)

def test_current_missing_checks_remain_unknown():
    now = datetime.now(timezone.utc)
    value, error = read_supervisor(JournalRead({'updated_at': now.isoformat(), 'checks': {}}), now)
    assert error is None
    assert value['checks_are_current'] is True
    assert value['reconciliation'] is None
    assert value['last_reported_checks']['reconciliation'] is None

def test_explicit_current_match_and_mismatch():
    now = datetime.now(timezone.utc)
    for match in (True, False):
        value, _ = read_supervisor(JournalRead({'updated_at': now.isoformat(), 'checks': {'reconciliation': match}}), now)
        assert value['reconciliation'] is match

def test_46_hour_old_proof_is_only_historical():
    now = datetime.now(timezone.utc)
    value, _ = read_supervisor(JournalRead({'updated_at': (now - timedelta(hours=46)).isoformat(), 'checks': {'reconciliation': True}, 'reasons': ['heartbeat_recovery']}), now)
    assert value['freshness'] == 'stale'
    assert value['age_s'] == 46 * 3600
    assert value['reconciliation'] is None
    assert value['last_reported_checks']['reconciliation'] is True
