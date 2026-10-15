"""DA-08 downstream coverage: preview limits cannot select runtime evidence."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader import attention_admission as A
from trader.portfolio import current, opportunity_live as L
from trader.portfolio.allocator import Source
from scripts import opportunity_context_shadow as shadow
from tests.test_hierarchical_admission import observation
from tests.test_stage6_normal_sources import publications, cfg  # noqa: F401


def test_runtime_keeps_all_authenticated_contexts_and_observations(publications, tmp_path):
    j, config, version, old, _er, market, _cap = publications
    raw = json.loads(old.payload_json)
    for path in (tmp_path/'contexts').glob('*.json'):
        path.unlink()
    ids = [f'admitted-{i:02}' for i in range(12)]
    for did in ids:
        sources = []
        for s in raw['sources']:
            body = json.loads(s['payload_json'])
            if s['source_id'] == 'analysts':
                body['data']['decision_id'] = did
            sources.append(Source.freeze(s['source_id'], body))
        args = {k: raw[k] for k in ('as_of_ms', 'symbol', 'instrument_id', 'cycle_id', 'required_roles')}
        receipt = L.produce(**args, candidate_id=did+':'+version['version_id'],
                            sources=tuple(sources), decision_grade=True)
        L.persist(receipt, tmp_path/'contexts')
    for path in (tmp_path/'economics').glob('*.json'):
        path.unlink()  # original economics belongs to the original exact context only
    paths = (Path(j.db_path), tmp_path/'attention.db', tmp_path/'investigation.db')
    preview, *_ = shadow.capture(*paths, config)
    complete, *_ = shadow.capture(*paths, config, max_contexts=None)
    assert len(preview) == 4 and len(complete) == 12
    from tests.admission_cycle_fixture import bind_market
    admission = bind_market(market, config, raw['as_of_ms'])
    inputs, detail = current.freeze(*paths, config,
        market_snapshots={did: market for did in ids}, admission_receipt=admission)
    assert detail['normal_persisted_contexts'] == 12
    assert detail['context_count'] == 12
    retained = next(s for s in inputs.sources if s.source_id == 'admitted-market-observations')
    body = json.loads(retained.payload_json)
    assert set(body['observations']) == set(ids)
    assert body == detail['admitted_market_observations']
    row = body['coverage'][market.symbol]
    assert row['status'] == 'REQUEST_BOUND'
    assert set(row['decision_ids']) == set(ids) and len(row['request_ids']) == 12
    # Every missing exact economics source is still explicitly blocked.
    assert not inputs.candidates
    assert len(detail['blocked_decisions']) == 12
    assert len(inputs.portfolio.positions) == 0  # the fixture's verified empty book
    from trader.portfolio.runtime import Consumer, replay
    ledger = tmp_path/'coverage-ledger.db'
    result = Consumer(ledger).consume(inputs, processed_at=detail['as_of_ms'])
    assert result['real_order_submissions'] == 0 and replay(result) == result
    again = Consumer(ledger).consume(inputs, processed_at=detail['as_of_ms'])
    assert again['duplicate']
    with Consumer(ledger)._db() as db:
        stored = json.loads(db.execute('SELECT journal_detail_v1(inputs_json) FROM state').fetchone()[0])
    source = next(s for s in stored['sources'] if s['source_id'] == retained.source_id)
    assert source['sha256'] == retained.sha256 and source['payload_json'] == retained.payload_json


def test_missing_context_and_snapshot_remain_explicit_and_identity_bound():
    receipt = A.admit(observation(30), A.AdmissionPolicy(12, 2, 2, 0), A.initial_state())
    first = receipt['admitted_symbols'][0]
    market = SimpleNamespace(symbol=first, ts='2027-01-15T08:00:00+00:00', price=10,
                             admission_context={'receipt_id': receipt['receipt_id']})
    result = current.admission_observations({'d': market}, receipt, [])
    assert len(result['coverage']) == 12
    assert result['coverage'][first]['status'] == 'CONTEXT_REQUEST_UNAVAILABLE'
    assert sum(r['status'] == 'SNAPSHOT_UNAVAILABLE' for r in result['coverage'].values()) == 11
    assert set(result['observations']) == {'d'}
    market.admission_context['receipt_id'] = 'other-cut'
    with pytest.raises(ValueError, match='PORTFOLIO_ADMISSION_SNAPSHOT_MISMATCH'):
        current.admission_observations({'d': market}, receipt, [])
