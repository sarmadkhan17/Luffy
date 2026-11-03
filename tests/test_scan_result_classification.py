"""Backend metadata and the legacy UI fallback share a branch contract."""
import copy
import json
from pathlib import Path

import pytest

from trader.dashboard.scan_result import classify_scan_result, SCHEMA
from trader.dashboard import overview_desk
from tests.test_current_scan_dashboard import runtime

CASES = json.loads((Path(__file__).resolve().parents[1] /
                   'frontend/tests/fixtures/scan-result-cases.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=[c['name'] for c in CASES])
def test_canonical_classification_contract(case):
    row = dict(action='HOLD',score=0,evaluated=True,executed=False)
    row.update(case['coin'])
    original = copy.deepcopy(row)
    result = classify_scan_result(row)
    assert result['schema'] == SCHEMA
    for field in ('category','label','show_metrics'):
        assert result[field] == case[field]
    assert isinstance(result['description'],str) and result['description']
    assert result['tone'] in ('neutral','warning','positive')
    assert row == original


def test_current_serializer_adds_metadata_without_trusting_or_changing_runtime_props(runtime):
    root, data, write = runtime
    coin = data['latest_scan']['coins'][0]
    coin['strategy_checks'] = [dict(strategy='test',reasons=['emitted_signal'])]
    coin['classification'] = dict(schema=SCHEMA,label='spoofed')
    write()
    exact = (root/'data/cycle_runtime.json').read_bytes()
    scan = overview_desk.current_scan(root,1010)
    assert scan['coins'][0]['classification']['label'] == 'Signal emitted'
    assert scan['coins'][1]['classification']['category'] == 'not_applicable'
    assert scan['coins'][0]['action'] == 'HOLD' and scan['coins'][0]['score'] == 0
    assert (root/'data/cycle_runtime.json').read_bytes() == exact


def test_historical_serializer_retains_props_and_adds_classification():
    class History:
        def query(self, sql, args=()):
            if 'SELECT scan_id, ts' in sql:
                return [dict(scan_id='old',ts='2026-01-01')]
            return [dict(id='d',symbol='BTC/USDT',action='BUY',score=.5,threshold=.2,
                         confidence=.8,executed=0,skip_reason='state=FROZEN',ts='2026-01-01')]
    result = overview_desk.latest_scan(History())['coins'][0]
    assert result['classification']['label'] == 'Blocked'
    assert result['skip_reason'] == 'state=FROZEN' and result['action'] == 'BUY'


def test_desk_http_response_exposes_canonical_field(runtime, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import make_app
    from tests.test_trade_history import H
    root, data, write = runtime
    app, _, _ = make_app(root,monkeypatch)
    data['latest_scan']['observed_at'] = time.time()
    write()
    response = TestClient(app).get('/owner-api/v1/desk',headers=H)
    assert response.status_code == 200
    coins = response.json()['scan']['coins']
    assert coins[0]['classification']['schema'] == SCHEMA
    assert coins[1]['classification']['label'] == 'Not applicable'
