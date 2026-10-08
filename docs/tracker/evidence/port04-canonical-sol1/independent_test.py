"""Control-review probes run against pinned PORT-04 source, with offline fixtures only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from dataclasses import replace
import pytest
from tests.test_port04_relationship_sources import publications, cfg, held, persist_scan, read, exposure, REL
from trader.portfolio import allocator as A, common_factor as C, current


def test_actual_checkpoint_delivers_persisted_relationships(held, monkeypatch, tmp_path):
    persist_scan(held[0].db_path.parent, monkeypatch)
    j, config, _, _, _, market, _ = held
    from tests.admission_cycle_fixture import bind_market
    from tests.authority_capacity_fixtures import NOW
    admission = bind_market(market, config, NOW)
    result, detail = current.checkpoint(j.db_path, config, ledger=tmp_path/'review-ledger.db',
        market_snapshots={'normal-setup': market}, admission_receipt=admission)
    assert detail['relationship_source_ids'] == [REL]
    assert detail['common_factor']['measured_relationships']
    assert result['real_order_submissions'] == 0
    assert result['proposal']['result']['portfolio_exposure_context'] == detail['common_factor']


def test_parent_pairwise_counterexample_and_fixed_receipt(held, monkeypatch, tmp_path):
    persist_scan(held[0].db_path.parent, monkeypatch)
    i, detail = read(held)
    body = exposure(i)['inputs']
    path = tmp_path/'parent_common_factor.py'
    path.write_bytes(subprocess.check_output(['git','show','2ea4a95:trader/portfolio/common_factor.py']))
    name = 'trader.portfolio.review_parent_common_factor'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    def s(x): return A.Source(**x)
    old = module.build(i, s(body['venue_source']), s(body['policy_source']),
        tuple(map(s, body['lineage_sources'])), tuple(map(s, body['relationship_sources'])),
        tuple(map(s, body['measurement_sources'])))
    assert any(p['measurement_ids'] for p in json.loads(old.result_json)['pairs'])
    assert all(not p['measurement_ids'] for p in detail['common_factor']['pairs'])
    # Invalid old attribution is refused; old receipts are never silently rewritten.
    bad = A.Source.freeze('portfolio-common-factor:'+old.receipt_id, old.payload())
    historical = replace(i, exposure_source_id=bad.source_id,
        sources=tuple(s for s in i.sources if s.source_id != i.exposure_source_id)+(bad,))
    with pytest.raises(ValueError, match='REPLAY_REFUSED'):
        C.from_allocator(historical)


def test_unknown_pair_relationship_without_optional_evidence(held):
    i, detail = read(held)
    assert not detail['common_factor']['measured_relationships']
    assert all(not p['measurement_ids'] for p in detail['common_factor']['pairs'])
    # Exact same-instrument exposure remains exact even with no optional measurement.
    assert all(p['relationship'] == 'EXACT_SHARED_EXPOSURE' for p in detail['common_factor']['pairs'])
