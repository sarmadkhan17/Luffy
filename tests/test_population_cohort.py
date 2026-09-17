"""Neutral cohorts retain successes, failures and missing labels alike."""
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from trader.cognition import dataset as D
from trader.observability import population as P, learning as L, investigation as C
from tests.test_attention_learning import publish
from tests.test_pit_population import config, NOW


def test_neutral_investigation_cohort_includes_unclassified_terminals(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'investigation.db'
    publish(src, NOW); C.step(src, dst, NOW, cfg)
    before = P.capture(cfg, NOW+1)
    assert before['cohort']['rows']
    assert all(r['status'] == 'unresolved' for r in before['cohort']['rows'])
    end = C.dossiers(dst)[0]['investigation']['measurement']['deadline_ms']
    publish(src, end+1, 'complete'); C.step(src, dst, end+1, cfg)
    artifact = P.capture(cfg, end+2)
    cohort = artifact['cohort']
    original = {r['episode_id'] for r in before['cohort']['rows']}
    completed = [r for r in cohort['rows'] if r['episode_id'] in original]
    assert len(completed) == len(original)
    assert all(r['status'] == 'measured' and r['terminal'] for r in completed)
    assert any(not r['terminal'] for r in cohort['rows'])
    assert any(dict(r['assessment']).get('same_direction') != 'contradicted' for r in completed)
    assert not artifact['dataset']['rows']
    assert not cohort['orphan_results']
    assert not cohort['scan_coverage']['investigation']['unmatched_registration_claims']
    assert cohort['dependence_groups'] == 1
    assert not cohort['search_ready']
    src.unlink(); dst.unlink()
    assert P.replay_capture(artifact) == artifact['coverage']
    bad = copy.deepcopy(artifact)
    bad['cohort']['rows'].pop()
    bad['sha256'] = D.digest({k: v for k, v in bad.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='cohort_replay'):
        P.replay_capture(bad)


def test_forecast_selected_ignored_retries_and_unavailable(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); first = L.step(src, dst, NOW, cfg)
    end = first['recent'][0]['deadline_ms']
    # Same prices at a fresh scan with no exact target for registered symbols.
    publish(src, end+1, 'missing')
    with sqlite3.connect(src) as db:
        db.execute('DELETE FROM scan_versions')
    L.step(src, dst, end+1, cfg)
    late = end+L.PROTOCOL['grace_ms']+1
    L.step(src, dst, late, cfg)
    cohort = P.capture(cfg, late+1)['cohort']
    assert cohort['missing_target_receipts']
    assert cohort['selection_counts'] == {'ignored': 5, 'selected': 1}
    assert len(cohort['rows']) == 6
    assert all(r['status'] == 'unavailable' for r in cohort['rows'])
    assert all(r['evidence'].get('price_change_bps') is None for r in cohort['rows'])
    assert cohort['scan_coverage']['forecast']['registration_reasons']['registered'] == 6


def test_expired_and_orphan_results_stay_explicit(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'investigation.db'
    publish(src, NOW); C.step(src, dst, NOW, cfg)
    end = max(d['investigation']['measurement']['expires_ms'] for d in C.dossiers(dst))
    C.step(src, dst, end+1, cfg)
    artifact = P.capture(cfg, end+2)
    assert all(r['status'] == 'not_testable' for r in artifact['cohort']['rows'])
    for path in Path(cfg['export_directory']).glob('*/*.json'):
        if json.loads(path.read_text())['kind'] == 'registration':
            path.unlink()
    orphan = P.capture(cfg, end+3)['cohort']
    assert not orphan['rows'] and orphan['orphan_results']
    assert orphan['scan_coverage']['investigation']['unmatched_registration_claims']


def test_index_reconciliation_detects_lost_and_pending_exports(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); L.step(src, dst, NOW, cfg)
    cfg['local_ledgers'] = {'forecast': str(dst)}
    artifact = P.capture(cfg, NOW+1)
    assert artifact['cohort']['receipt_reconciliation']['forecast']['status'] == 'matched'
    path = next(p for p in Path(cfg['export_directory']).glob('*/*.json')
                if json.loads(p.read_text())['kind'] == 'registration')
    key = json.loads(path.read_text())['id']; path.unlink()
    cohort = P.capture(cfg, NOW+2)['cohort']
    assert cohort['receipt_reconciliation']['forecast']['missing_exports'] == [key]
    assert len(cohort['rows']) == 5
    with sqlite3.connect(dst) as db:
        P.Producer(db, cfg, 'forecast', NOW+3).emit('gap:test', 'gap', {'reason': 'test'})
    cohort = P.capture(cfg, NOW+4)['cohort']
    assert len(cohort['receipt_reconciliation']['forecast']['pending_exports']) == 1


def test_cut_and_restoration_clocks_do_not_backdate_labels(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); first = L.step(src, dst, NOW, cfg)
    end = first['recent'][0]['deadline_ms']
    publish(src, end+1, 'resolved', 120); L.step(src, dst, end+1, cfg)
    now = NOW+86400001
    copied = P.capture(cfg, now)
    assert copied['cohort']['window_status'] == 'cut_reached_review_required'
    assert not any(r['terminal_known_by_cut'] for r in copied['cohort']['rows'])
    cfg['local_ledgers'] = {'forecast': str(dst)}
    local = P.capture(cfg, now)
    assert all(r['terminal_known_by_cut'] for r in local['cohort']['rows'] if r['terminal'])
    assert any(r['terminal'] for r in local['cohort']['rows'])
    assert not local['cohort']['search_ready']
    assert P.replay_capture(local) == local['coverage']


def test_old_frozen_capture_still_replays():
    path = Path('docs/superpowers/artifacts/pit-dataset/2026-09-16-population-initial-capture.json')
    artifact = json.loads(path.read_text())
    assert artifact['schema_version'] == 'pit-population-capture.v1'
    assert P.replay_capture(artifact) == artifact['coverage']


def test_no_observations_leave_full_window_gap(tmp_path):
    cfg = config(tmp_path)
    artifact = P.capture(cfg, NOW+86400001)
    for stream in ('forecast', 'investigation'):
        coverage = artifact['cohort']['scan_coverage'][stream]
        assert coverage['observation_gap_intervals_ms'] == [[NOW, NOW+86400000]]
        assert not coverage['activation_receipts']
    assert not artifact['cohort']['complete_sampling_claim']


def test_repeated_scans_are_not_additional_episodes(tmp_path):
    from trader.observability.cohort import review
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); L.step(src, dst, NOW, cfg)
    L.step(src, dst, NOW+1, cfg)
    artifact = P.capture(cfg, NOW+2)
    coverage = artifact['cohort']['scan_coverage']['forecast']
    assert coverage['observations'] == 2 and coverage['unique_scan_ids'] == 1
    assert coverage['declared_eligible_rows'] == 12
    assert len(artifact['cohort']['rows']) == 6
    assert coverage['registration_reasons'] == {'existing_episode': 6, 'registered': 6}
    assert review(list(reversed(artifact['events'])), artifact['dataset']['declaration'],
                  artifact['observed_capture_ms'], artifact['local_indices']) == artifact['cohort']
