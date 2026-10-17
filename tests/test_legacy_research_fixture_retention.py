"""Retain the deleted pre-registry regression vectors byte for byte.

These are historical synthetic test vectors, not market evidence and not a
replacement for the retired collection/semantic-authentication contracts.
"""
import hashlib
import json
from pathlib import Path

import pytest

from trader.core.journal import Journal
from trader.owner import approvals as A, queries as Q

FIXTURE = Path(__file__).parent / 'fixtures/research_evidence_v1_pre_source_registry.json'
CASES = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize('case', sorted(CASES))
def test_original_registry_vectors_survive_restart_without_authority(tmp_path, case):
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == (
        '47c8ed7e8d2d3d32077c4b84dd331b941d6e4f06ee728ca6d89448465dad1c4b')
    j = Journal(tmp_path / 'legacy.db')
    for row in CASES[case]:
        canonical = row['evidence_canonical_json']
        assert hashlib.sha256(canonical.encode()).hexdigest() == row['evidence_sha256']
        assert json.loads(canonical)['evidence_id'] == row['evidence_id']
        columns = j.query('PRAGMA table_info(research_evidence)')
        values = {r['name']: 1 if 'INT' in r['type'].upper() else 'TEST_ONLY-' + r['name']
                  for r in columns}
        plan = json.loads(row['plan_canonical_json'])
        values.update(evidence_id=row['evidence_id'], canonical_json=canonical,
                      canonical_sha256=row['evidence_sha256'], plan_id=plan['plan_id'],
                      plan_sha256=hashlib.sha256(row['plan_canonical_json'].encode()).hexdigest())
        with j._tx() as db:
            db.execute('INSERT INTO research_evidence VALUES (' + ','.join('?' for _ in values) + ')',
                       tuple(values.values()))
    before = j.query('SELECT * FROM research_evidence ORDER BY evidence_id')
    restarted = Journal(j.db_path)
    assert Q.query(restarted, 'research')['status'] == 'UNAVAILABLE'
    with pytest.raises(A.RetiredSource, match='^retired_source:research_bank$'):
        A.source(restarted, 'research_bank', case)
    assert restarted.query('SELECT * FROM research_evidence ORDER BY evidence_id') == before
    assert j.query('SELECT * FROM research_evidence ORDER BY evidence_id') == before
