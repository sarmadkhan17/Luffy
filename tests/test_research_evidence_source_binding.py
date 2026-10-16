"""RP-04 retirement preserves opaque historical evidence, never new authority.

The deleted suite built evidence with the retired source registry. Its current
equivalent is refusal of that source and exact preservation of its old records;
RP-03's Bank authentication suites cover the surviving source-binding path.
All rows below are explicitly synthetic, never reconstructed historical data.
"""
import hashlib

import pytest

from trader.core.journal import Journal
from trader.owner import approvals as A, queries as Q


@pytest.mark.parametrize('table', [
    'research_questions', 'research_plans', 'research_evidence', 'research_results',
    'research_runs', 'research_bank_objects', 'research_next_questions',
    'research_unreadable_questions', 'research_unreadable_plans',
    'research_unreadable_evidence', 'research_unreadable_results',
    'research_unreadable_runs', 'research_unreadable_bank_objects',
    'research_registrations',
])
def test_retired_source_bytes_survive_restart_and_current_readers(tmp_path, table):
    path = tmp_path / 'synthetic-legacy.db'
    j = Journal(path)
    columns = j.query(f'PRAGMA table_info({table})')
    assert columns
    opaque = ' { "TEST_ONLY": "opaque retained source", "value": 7 }\n'
    values = [1 if 'INT' in r['type'].upper() else
              opaque if r['name'].endswith('_json') else
              hashlib.sha256((table + ':' + r['name']).encode()).hexdigest()
              for r in columns]
    with j._tx() as db:
        db.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in values)})', values)
    before = [tuple(r) for r in j.query(f'SELECT * FROM {table}')]
    reopened = Journal(path)
    assert Q.query(reopened, 'research')['status'] == 'UNAVAILABLE'
    with pytest.raises(A.RetiredSource, match='^retired_source:research_bank$'):
        A.source(reopened, 'research_bank', 'TEST-ONLY')
    assert [tuple(r) for r in reopened.query(f'SELECT * FROM {table}')] == before
    assert [tuple(r) for r in Journal(path).query(f'SELECT * FROM {table}')] == before
