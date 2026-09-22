"""Strict historical PIT assembly and sufficiency auditing.

This is deliberately separate from forward collection and from the global
``search_ready`` field. It accepts only retained typed receipts whose local
knowledge/import clock is explicitly present. Archives without that clock are
reported as missing evidence, never dated at read time.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from trader.cognition import dataset as D

SCHEMA = 'historical-pit-artifact.v1'
MAX_JSON_BYTES = 8 * 1024 * 1024
REQUIRED_KINDS = ('selected_forecast', 'ignored_forecast', 'false_signal',
                  'skip', 'regime_transition')


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _manifest() -> dict:
    root = Path(__file__).resolve().parents[2]
    files = {
        'dataset': root / 'trader/cognition/dataset.py',
        'outcomes': root / 'trader/cognition/outcomes.py',
        'contracts': root / 'trader/cognition/contracts.py',
        'historical_pit': Path(__file__).resolve(),
    }
    cli = root / 'scripts/build_historical_pit_dataset.py'
    if cli.exists():
        files['cli'] = cli
    return {name: '%s:%s' % (str(path.relative_to(root)), _sha256(path))
            for name, path in sorted(files.items())}


def _immutable_write(path: Path, value) -> str:
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if path.exists():
        if path.read_text() != encoded:
            raise ValueError('historical_artifact_conflict')
        return 'unchanged'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded)
    return 'written'


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ValueError('source_not_found:%s' % path)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('source_payload_capacity:%s' % path)
    return json.loads(path.read_text())


def _utc_parts(ms):
    try:
        dt = datetime.fromtimestamp(ms / 1000, timezone.utc)
        return {'hour_utc': '%02d' % dt.hour, 'weekday_utc': str(dt.weekday()),
                'month_utc': dt.strftime('%Y-%m')}
    except (OverflowError, OSError, ValueError):
        return {'hour_utc': 'unknown', 'weekday_utc': 'unknown', 'month_utc': 'unknown'}


def _feature_regime(features):
    for key in ('regime', 'market_regime', 'regime_label', 'market_type'):
        value = features.get(key)
        if isinstance(value, str) and value:
            return value
    return 'unknown'


def _count(values):
    result = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def historical_sufficiency(dataset: dict, adapter_meta: dict | None = None) -> dict:
    """Return a deterministic sufficiency result, never a population claim."""
    rows = list(dataset.get('rows', []))
    base = [r for r in rows if r.get('row_type') != 'sequence']
    kinds = _count(r.get('labels', {}).get('case_kind', 'unknown') for r in base)
    selected = sum(1 for r in base if r.get('producer') == 'forecast' and r.get('features', {}).get('selected') is True)
    ignored = sum(1 for r in base if r.get('producer') == 'forecast' and r.get('features', {}).get('selected') is False)
    failures = sum(1 for r in base if r.get('row_type') == 'failure')
    skips = sum(1 for r in base if r.get('labels', {}).get('case_kind') == 'skip')
    transitions = sum(1 for r in base if r.get('row_type') == 'transition')
    sequences = [r for r in rows if r.get('row_type') == 'sequence']
    resolved = sum(1 for r in base if r.get('labels', {}).get('resolved_ms') is not None)
    unresolved_observed = sum(1 for r in base if r.get('labels', {}).get('resolved_ms') is None)
    symbols = _count(r.get('symbol', 'unknown') for r in base)
    regimes = _count(_feature_regime(r.get('features', {})) for r in base)
    calendar = {key: {} for key in ('hour_utc', 'weekday_utc', 'month_utc')}
    for r in base:
        for key, value in _utc_parts(r.get('features', {}).get('as_of_ms', -1)).items():
            calendar[key][value] = calendar[key].get(value, 0) + 1
    dependence = _count(r.get('dependence_group', 'unknown') for r in rows)
    excluded = _count(e.get('reason', 'unknown') for e in dataset.get('excluded', []))
    adapter_rejected = _count(e.get('reason', 'unknown') for e in (adapter_meta or {}).get('adapter_rejections', []))

    requirements = {
        'selected_cases': selected > 0,
        'ignored_cases': ignored > 0,
        'failure_cases': failures > 0,
        'skip_cases': skips > 0,
        'regime_transitions': transitions > 0,
        'sequence_episodes': len(sequences) > 0,
        'resolved_outcomes': resolved > 0,
    }
    reasons = ['historical_retained_snapshot_not_complete_population_sampling']
    reasons += ['missing_%s' % key for key, ok in requirements.items() if not ok]
    if adapter_rejected:
        reasons.append('source_evidence_rejected_or_missing')
    status = 'sufficient' if all(requirements.values()) and not adapter_rejected else 'insufficient'
    return {
        'schema_version': 'historical-sufficiency.v1',
        'status': status,
        'population_sampling_claim': False,
        'requirements': requirements,
        'counts': {
            'rows': len(rows), 'base_rows': len(base), 'typed_kind': dict(sorted(kinds.items())),
            'selected': selected, 'ignored': ignored, 'failures': failures, 'skips': skips,
            'transitions': transitions, 'sequences': len(sequences), 'resolved_outcomes': resolved,
            'unresolved_outcomes_observed': unresolved_observed,
        },
        'coverage': {'symbols': dict(sorted(symbols.items())),
                     'regimes': dict(sorted(regimes.items())),
                     'calendar_utc': {k: dict(sorted(v.items())) for k, v in calendar.items()},
                     'dependence_groups': dict(sorted(dependence.items()))},
        'evidence': {'dataset_excluded': dict(sorted(excluded.items())),
                     'adapter_rejected': dict(sorted(adapter_rejected.items()))},
        'unknowns': ['unobserved historical events', 'missing availability clocks',
                     'unretained source receipts', 'outcomes not represented by terminal receipts'],
        'reason_codes': sorted(set(reasons)),
    }


def build_artifact(declaration: dict, freeze: dict, receipts: list, adapter_meta: dict) -> dict:
    """Build and replay an immutable historical dataset artifact."""
    if declaration.get('collection_mode') != 'retrospective_snapshot':
        raise ValueError('historical_adapter_requires_retrospective_snapshot')
    manifest = _manifest()
    if freeze.get('code_manifest') != manifest:
        raise ValueError('code_manifest_changed_since_freeze')
    capture = D.capture_receipt(freeze['observed_frozen_ms'], code_manifest=manifest)
    dataset = D.build(declaration, freeze, capture, receipts,
                      source_meta=adapter_meta)
    D.replay(dataset)
    replay_body = {k: v for k, v in dataset.items() if k != 'dataset_version'}
    replay_hash = _hash_json(replay_body)
    return {
        'schema_version': SCHEMA,
        'declaration': declaration,
        'source_hashes': adapter_meta.get('source_hashes', {}),
        'code_manifest': manifest,
        'dataset_version': dataset['dataset_version'],
        'replay_hash': replay_hash,
        'replay_passed': True,
        'sufficiency': historical_sufficiency(dataset, adapter_meta),
        'limitations': [
            'Historical retained snapshot; no complete historical population claim.',
            'Missing local availability/import clocks are rejected and remain unknown.',
            'Retained-store eviction and source selection can bias the sample.',
            'Classification-conditioned investigation rows do not establish event rates.',
            'Observation-only journal rows cannot prove historical feature availability.',
            'Unknown execution accounting and missing outcomes are never imputed.',
            'This artifact has no strategy admission, live handoff, or trading authority.',
        ],
        'dataset': dataset,
    }
