"""Exact maturity reporting must retain retries without masking conflicts."""
import json
import sqlite3

import pytest

from scripts.verify_memory_outcomes import verify
from tests.test_typed_outcomes import forecast
from trader.observability import investigation as C


def prepare(path):
    data = path / 'data'
    data.mkdir()
    for name in ('attention.db', 'attention_learning.db'):
        (path / name).rename(data / name)
    with C.ledger(data / 'investigation.db'):
        pass
    return data


@pytest.mark.parametrize('missing', ['baseline', 'target'])
def test_missing_exact_version_reports_retry_and_continues(forecast, missing):
    path, rows, now = forecast
    data = prepare(path)
    bar = (rows[0]['prediction']['input_window'][0] if missing == 'baseline'
           else rows[0]['outcome']['target'])
    with sqlite3.connect(data / 'attention.db') as db:
        db.execute('DELETE FROM scan_versions WHERE version_id=?', (bar['version_id'],))
        db.execute('DELETE FROM versions WHERE id=?', (bar['version_id'],))
    result = verify(path, now)
    by_id = {r['id']: r for r in result['forecasts']}
    assert by_id[rows[0]['id']]['verification'] == 'exact_source_version_missing_retry'
    assert by_id[rows[0]['id']]['status'] == 'resolved'
    assert bool(by_id[rows[0]['id']]['missing_versions_retry']) == (missing == 'baseline')
    assert result['retry_required']
    assert sum(r['verification'] == 'exact_frozen_outcome_verified'
               for r in result['forecasts']) == len(rows) - 1


def test_exact_source_conflict_still_fails(forecast):
    path, rows, now = forecast
    data = prepare(path)
    bar = rows[0]['outcome']['target']
    with sqlite3.connect(data / 'attention.db') as db:
        raw = json.loads(db.execute('SELECT payload FROM versions WHERE id=?',
                                    (bar['version_id'],)).fetchone()[0])
        raw['close'] += 1
        db.execute('UPDATE versions SET payload=? WHERE id=?',
                   (json.dumps(raw), bar['version_id']))
    with pytest.raises(ValueError, match='exact_source_version_conflict'):
        verify(path, now)
