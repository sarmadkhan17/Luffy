import json
import sqlite3

import pytest

from trader.cognition import dataset as D, outcomes as O
from trader.cognition.historical_pit import build_artifact, historical_sufficiency, _manifest
from scripts.build_historical_pit_dataset import load_receipts


def _decl(cut=2_000_000_000_000):
    return {
        'schema_version': D.DECLARATION_SCHEMA,
        'dataset_id': 'historical-test',
        'collection_mode': 'retrospective_snapshot',
        'start_ms': 0,
        'discovery_cut_ms': cut,
        'universe': ['AAA/USDT'],
        'dependence': {'symbol_groups': {}, 'window_pad_ms': 0},
        'coverage': {'expected_kinds': {}, 'expected_symbols': [], 'min_rows': 1,
                     'max_sequence_gap_ms': 10**12},
        'limits': {'max_records': 32, 'max_rows': 64},
    }


def _cf(base, imported=None):
    tf = 14_400_000
    registered = base + tf + 1_000
    target = (registered // tf + 1) * tf
    bar = lambda open_ms, close, tag: {
        'symbol': 'AAA/USDT', 'open_ms': open_ms, 'open': 100.0,
        'high': max(100.0, close), 'low': min(100.0, close), 'close': close,
        'volume': 10.0, 'available_ms': open_ms + tf, 'version_id': f'{tag}-{open_ms}'
    }
    rec = O.counterfactual(
        {'schema_version': 'close-counterfactual.v1', 'decision_kind': 'skip',
         'direction': 1, 'registered_ms': registered, 'target_open_ms': target,
         'baseline': bar(base, 100.0, 'base'), 'cost_bps': 5.0, 'notional': 100.0},
        bar(target, 101.0, 'target'), target + tf)
    return {'source_key': f'cf:{base}', 'local_imported_ms': imported or rec['imported_ms'],
            'record': rec}


def _freeze(decl):
    return D.freeze(decl, 3_000_000_000_000, code_manifest=_manifest())


def test_missing_local_clock_is_rejected_not_reconstructed(tmp_path):
    path = tmp_path / 'receipts.json'
    path.write_text(json.dumps({'receipts': [{'source_key': 'x', 'record': {'kind': 'skip'}}]}))
    accepted, meta = load_receipts(artifacts=[path])
    assert accepted == []
    assert meta['adapter_rejections'][0]['reason'] == 'missing_local_imported_ms'


def test_historical_artifact_replays_deterministically(tmp_path):
    first = _cf(1_440_000_000)
    second = _cf(first['record']['imported_ms'])
    decl, freeze = _decl(), _freeze(_decl())
    meta = {'source_hashes': {'fixture': 'sha256:fixture'}, 'adapter_rejections': [],
            'retention': {'evicted_total': 0}}
    a = build_artifact(decl, freeze, [first, second], meta)
    b = build_artifact(decl, freeze, [first, second], meta)
    assert a == b
    assert a['replay_passed'] is True
    assert a['replay_hash'] == a['dataset_version']
    assert a['dataset']['accounting']['sequence_rows'] == 1
    assert a['sufficiency']['population_sampling_claim'] is False


def test_late_local_import_cannot_create_historical_sequence():
    first = _cf(1_440_000_000)
    second = _cf(first['record']['imported_ms'])
    late = dict(first, local_imported_ms=second['record']['registered_ms'] + 1)
    decl = _decl()
    meta = {'source_hashes': {}, 'adapter_rejections': [], 'retention': {'evicted_total': 0}}
    prompt = build_artifact(decl, _freeze(decl), [first, second], meta)
    hindsight = build_artifact(decl, _freeze(decl), [late, second], meta)
    assert prompt['dataset']['accounting']['sequence_rows'] == 1
    assert hindsight['dataset']['accounting']['sequence_rows'] == 0
    assert any(g['reason'] == 'prior_outcome_not_known_before_features'
               for g in hindsight['dataset']['coverage']['sequence_gaps'])


def test_sufficiency_reports_missing_types_and_coverage_without_population_claim():
    report = historical_sufficiency({
        'rows': [{'row_type': 'state', 'producer': 'counterfactual', 'symbol': 'AAA/USDT',
                  'labels': {'case_kind': 'skip', 'resolved_ms': 10},
                  'features': {'as_of_ms': 1, 'selected': False},
                  'dependence_group': 'g0000'}],
        'excluded': [{'reason': 'missing_local_imported_ms'}],
    }, {'adapter_rejections': [{'reason': 'missing_local_imported_ms'}]})
    assert report['status'] == 'insufficient'
    assert report['population_sampling_claim'] is False
    assert report['counts']['skips'] == 1
    assert 'missing_sequence_episodes' in report['reason_codes']
    assert report['evidence']['adapter_rejected']['missing_local_imported_ms'] == 1
