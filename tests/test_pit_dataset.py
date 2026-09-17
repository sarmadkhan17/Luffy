"""Synthetic engineering evidence for the point-in-time dataset builder.

Offline fixtures only: no network, no live ledger, no research evaluation, no
economic or predictive claim. Every assertion here is about the dataset's
honesty contracts, not about any edge.
"""
import copy
from datetime import datetime, timezone
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from trader.cognition import dataset as D, outcomes as O
from trader.observability import investigation as C, learning as Lrn, outcomes as S
from scripts import build_pit_dataset as CLI
from tests.test_attention_learning import publish
from tests.test_market_investigation import prefix          # noqa: F401  (fixture)
from tests.test_memory_producers import archived

HOUR = 3_600_000
# Synthetic stand-in for the CLI's real source hashes: names the fixture protocol.
FIXTURE_MANIFEST = {'builder': 'fixture:trader.cognition.dataset.synthetic.v1',
                    'protocol': 'pit-dataset-test-fixture.v1'}


# --- fixtures --------------------------------------------------------------

@pytest.fixture
def rounds(tmp_path):
    """Two resolved forecast rounds, so a symbol can have a chronological pair."""
    src, dst = tmp_path/'attention.db', tmp_path/'attention_learning.db'
    now = 1_789_000_000_000//Lrn.TF*Lrn.TF + 60_000
    publish(src, now, 'r1')
    Lrn.step(src, dst, now)
    end1 = _max_deadline(dst)
    publish(src, end1+1, 'r2', 120)
    Lrn.step(src, dst, end1+1)
    end2 = _max_deadline(dst)
    publish(src, end2+1, 'r3', 130)
    Lrn.step(src, dst, end2+1)
    return tmp_path, _resolved(dst), end2+2


def _max_deadline(path):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT MAX(deadline_ms) FROM episodes').fetchone()[0]


def _resolved(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM episodes WHERE status='resolved' ORDER BY id")]
    for r in rows:
        r['prediction'], r['outcome'] = json.loads(r['prediction']), json.loads(r['outcome'])
    return rows


def receipts(path, rows, now, local=None):
    out = []
    for r in rows:
        record = S._forecast(path/'attention.db', r, now)
        out.append({'source_key': 'forecast:'+r['id'],
                    'local_imported_ms': now if local is None else local,
                    'record': record})
    return out


def iso(ms):
    return datetime.fromtimestamp(ms/1000, timezone.utc).isoformat()


def journal(now, symbol, pnl=12345.678):
    """A closed trade and a skip: both first observed at import, both unknown P&L."""
    decision = {'id': 'd1', 'cycle_id': 'c1', 'ts': iso(now-8*HOUR), 'symbol': symbol,
                'action': 'long', 'executed': 1, 'skip_reason': None,
                'strategy_ids': '["spec:alpha"]', 'scan_id': 'sc1'}
    trade = {'id': 't1', 'decision_id': 'd1', 'symbol': symbol, 'status': 'closed',
             'closed_at': iso(now-4*HOUR), 'opened_at': iso(now-7*HOUR),
             'realized_pnl': pnl, 'exec_mode': 'live', 'strategy_id': 'spec:alpha'}
    skip = {'id': 'd2', 'cycle_id': 'c1', 'ts': iso(now-6*HOUR), 'symbol': symbol,
            'action': 'none', 'executed': 0, 'skip_reason': 'risk_cap',
            'strategy_ids': '[]', 'scan_id': 'sc1'}
    return [{'source_key': 'trade:t1', 'local_imported_ms': now,
             'record': O.linked_journal(decision, trade, now)},
            {'source_key': 'skip:d2', 'local_imported_ms': now,
             'record': O.linked_journal(skip, None, now)}]


def _bar(symbol, open_ms, close, tag):
    return {'symbol': symbol, 'open_ms': open_ms, 'open': 100.0, 'high': max(100.0, close),
            'low': min(100.0, close), 'close': close, 'volume': 10.0,
            'available_ms': open_ms+Lrn.TF, 'version_id': 'v_%s_%d' % (tag, open_ms)}


def counterfactual(symbol, base_open, close=101.0, kind='skip'):
    """A frozen non-trade simulation: explicit clocks, never an executable claim."""
    baseline = _bar(symbol, base_open, 100.0, 'base')
    registered = base_open + Lrn.TF + 60_000
    target_open = registered//Lrn.TF*Lrn.TF + Lrn.TF
    target = _bar(symbol, target_open, close, 'target')
    registration = {'schema_version': 'close-counterfactual.v1', 'decision_kind': kind,
                    'direction': 1, 'registered_ms': registered, 'target_open_ms': target_open,
                    'baseline': baseline, 'cost_bps': 5.0, 'notional': 100.0}
    record = O.counterfactual(registration, target, target_open+Lrn.TF)
    return {'source_key': 'counterfactual:%s:%d' % (symbol, base_open),
            'local_imported_ms': record['imported_ms'], 'record': record}


def chained(symbol='CF/USDT', base_open=None):
    """Two non-trade cases where the first outcome lands strictly before the second."""
    first = counterfactual(symbol, base_open or 1_789_000_000_000//Lrn.TF*Lrn.TF)
    second = counterfactual(symbol, first['record']['imported_ms'])
    assert first['record']['available_ms'] < second['record']['registered_ms']
    return [first, second]


def delayed_pair(path, rows, now, late):
    """A resolved forecast plus a later case on its symbol, the forecast imported late.

    Both variants carry identical labels and label-availability clocks; only when
    the prior was imported changes, which is exactly what sequence eligibility
    must depend on.
    """
    row = min(rows, key=lambda r: (r['prediction']['registered_ms'], r['id']))
    available = S._forecast(path/'attention.db', row, now)['available_ms']
    current = counterfactual(row['symbol'], (available//Lrn.TF + 1)*Lrn.TF)
    imported = current['record']['registered_ms'] + 1 if late else available
    prior = {'source_key': 'forecast:prior', 'local_imported_ms': imported,
             'record': S._forecast(path/'attention.db', row, imported)}
    return row['symbol'], [prior, current]


def declaration(symbols, cut, start=0, mode='retrospective_snapshot', **over):
    d = dict(schema_version=D.DECLARATION_SCHEMA, dataset_id='m31-engineering-fixture',
             collection_mode=mode, start_ms=start, discovery_cut_ms=cut,
             universe=sorted(symbols),
             dependence=dict(symbol_groups={}, window_pad_ms=0),
             coverage=dict(expected_kinds={}, expected_symbols=[], min_rows=1,
                           max_sequence_gap_ms=10**12),
             limits=dict(max_records=512, max_rows=1024))
    d.update(over)
    return d


def known_of(item):
    r = item['record']
    return max(r['resolved_ms'], r['available_ms'], r['imported_ms'], item['local_imported_ms'])


def built(decl, items, frozen_ms=0, meta=None, capture_ms=None, manifest=None):
    receipt = D.freeze(decl, frozen_ms, code_manifest=manifest or FIXTURE_MANIFEST)
    if capture_ms is None:
        capture_ms = max([frozen_ms, 1] + [known_of(i) for i in items])
    capture = D.capture_receipt(capture_ms, code_manifest=FIXTURE_MANIFEST)
    return D.build(decl, receipt, capture, items, meta)


def symbols_of(items):
    return {i['record']['source']['symbol'] for i in items}


# --- declaration and freeze ------------------------------------------------

def test_declaration_carries_no_self_asserted_freeze_time():
    assert 'frozen_ms' not in D.DECLARED_FIELDS
    decl = declaration(['A/USDT'], 10)
    assert D.declaration_version(decl) == D.digest(json.loads(Lrn.encode(decl)))
    # An extra field cannot smuggle a caller-asserted freeze in.
    with pytest.raises(ValueError, match='declaration_unknown_or_missing_field'):
        D.validate_declaration(dict(decl, frozen_ms=5))


@pytest.mark.parametrize('field,value,reason', [
    ('schema_version', 'other.v1', 'declaration_schema_or_id'),
    ('collection_mode', 'forward_ish', 'declaration_invalid_collection_mode'),
    ('start_ms', 10, 'declaration_start_not_before_cut'),
    ('start_ms', -1, 'declaration_invalid_clock'),
    ('universe', [], 'declaration_invalid_universe'),
    ('universe', ['A/USDT', 'A/USDT'], 'declaration_invalid_universe'),
    ('dependence', {'symbol_groups': {}}, 'declaration_invalid_dependence'),
    ('dependence', {'symbol_groups': {'g': ['Z/USDT']}, 'window_pad_ms': 0},
     'declaration_invalid_symbol_groups'),
    ('coverage', {'expected_kinds': {'not_a_kind': 1}, 'expected_symbols': [], 'min_rows': 1,
                  'max_sequence_gap_ms': 0}, 'declaration_invalid_expected_kinds'),
    ('coverage', {'expected_kinds': {}, 'expected_symbols': ['Z/USDT'], 'min_rows': 1,
                  'max_sequence_gap_ms': 0}, 'declaration_expected_symbols_outside_universe'),
    ('limits', {'max_records': 10**9, 'max_rows': 8}, 'declaration_invalid_limits'),
])
def test_declaration_refusals(field, value, reason):
    decl = declaration(['A/USDT'], 10)
    with pytest.raises(ValueError, match=reason):
        D.validate_declaration(dict(decl, **{field: value}))


def test_freeze_receipt_is_the_only_evidence_of_freezing():
    decl = declaration(['A/USDT'], 1000)
    receipt = D.freeze(decl, 500, code_manifest=FIXTURE_MANIFEST)
    assert D.validate_freeze(receipt, decl) == receipt
    with pytest.raises(ValueError, match='freeze_receipt_declaration_mismatch'):
        D.validate_freeze(receipt, dict(decl, dataset_id='renamed'))
    with pytest.raises(ValueError, match='freeze_receipt_declaration_mismatch'):
        D.validate_freeze(dict(receipt, declaration_version='forged'), decl)
    with pytest.raises(ValueError, match='freeze_receipt_unknown_or_missing_field'):
        D.validate_freeze(dict(receipt, extra=1), decl)
    with pytest.raises(ValueError, match='code_manifest_invalid'):
        D.validate_freeze(dict(receipt, code_manifest={}), decl)
    # A capture that precedes its own freeze cannot have been collected after it.
    with pytest.raises(ValueError, match='capture_precedes_declaration_freeze'):
        D.build(decl, receipt, D.capture_receipt(499, code_manifest=FIXTURE_MANIFEST), [])
    with pytest.raises(ValueError, match='code_manifest_required'):
        D.freeze(decl, 500, code_manifest=None)


def test_code_manifests_are_carried_not_rehashed(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    decl = declaration(symbols_of(items), now+HOUR)
    ds = built(decl, items)
    assert ds['freeze']['code_manifest'] == FIXTURE_MANIFEST
    assert ds['capture']['code_manifest'] == FIXTURE_MANIFEST
    assert ds['code_manifest'] == {'freeze': FIXTURE_MANIFEST, 'capture': FIXTURE_MANIFEST}
    assert D.replay(ds) == ds                     # preserved, never recomputed locally
    forged = {k: v for k, v in ds.items() if k != 'dataset_version'}
    forged['code_manifest'] = {'freeze': {'builder': 'other'}, 'capture': FIXTURE_MANIFEST}
    with pytest.raises(ValueError, match='dataset_code_manifest_mismatch'):
        D.replay(dict(forged, dataset_version=D.digest(forged)))
    drifted = built(decl, items, manifest={'builder': 'fixture:older-build.v0'})
    assert 'code_version_changed_between_freeze_and_capture' in drifted['coverage']['reason_codes']


def test_retrospective_declaration_cannot_claim_forward_collection():
    late = declaration(['A/USDT'], 1000, mode='forward')
    frozen = lambda decl, ms: D.freeze(decl, ms, code_manifest=FIXTURE_MANIFEST)
    with pytest.raises(ValueError, match='declaration_frozen_after_discovery_cut'):
        D.validate_freeze(frozen(late, 1000), late)
    assert D.validate_freeze(frozen(late, 999), late)
    # The explicitly labelled retrospective snapshot is the honest alternative.
    snapshot = declaration(['A/USDT'], 1000, mode='retrospective_snapshot')
    assert D.validate_freeze(frozen(snapshot, 5000), snapshot)


# --- sampling honesty ------------------------------------------------------

def test_forward_mode_requires_registration_and_import_after_the_freeze(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    syms, cut = symbols_of(items), now + HOUR
    decl = declaration(syms, cut, mode='forward')
    registrations = [i['record']['registered_ms'] for i in items]

    # Already retained when the declaration was frozen.
    stale = built(decl, items, frozen_ms=now+1)
    assert not stale['rows']
    assert set(stale['coverage']['exclusion_reasons']) == {'retained_before_declaration_freeze'}
    p = stale['coverage']['prospective_sampling']
    assert p['status'] == 'absent' and p['complete_sampling_claim'] is False
    assert 'no_rows_registered_and_imported_after_declaration_freeze' in p['reason_codes']

    # Registered before the freeze but restored after it: not forward collection.
    restored = built(decl, receipts(path, rows, now, local=now+5),
                     frozen_ms=max(registrations)+1)
    assert not restored['rows']
    assert set(restored['coverage']['exclusion_reasons']) == {'registered_before_declaration_freeze'}

    # Registered and imported after the freeze: the only forward case.
    fresh = built(decl, items, frozen_ms=min(registrations)-1)
    assert fresh['rows']
    assert {r['sampling_mode'] for r in fresh['rows']} == {'forward_after_declared_freeze'}
    assert {r['provenance'] for r in fresh['rows']} == {'original_after_freeze'}
    q = fresh['coverage']['prospective_sampling']
    assert q['status'] == 'forward_declared_incomplete' and q['complete_sampling_claim'] is False
    assert q['rows_retained_before_freeze'] == 0 and q['rows_restored_after_freeze'] == 0
    assert 'no_complete_sampling_claim' in fresh['coverage']['reason_codes']


def test_retrospective_snapshot_is_labelled_and_reports_absent_prospective_sampling(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    decl = declaration(symbols_of(items), now+HOUR, mode='retrospective_snapshot')
    ds = built(decl, items, frozen_ms=now+1000)
    assert ds['rows']
    assert {r['sampling_mode'] for r in ds['rows']} == {'retrospective_retained_snapshot'}
    p = ds['coverage']['prospective_sampling']
    assert p['status'] == 'absent' and p['complete_sampling_claim'] is False
    assert 'retrospective_engineering_snapshot' in p['reason_codes']
    assert {'prospective_sampling_absent', 'retrospective_engineering_snapshot',
            'retention_bias_bounded_retained_store', 'no_complete_sampling_claim'} <= \
        set(ds['coverage']['reason_codes'])
    assert 'never establish complete prospective sampling' in ds['sampling']
    # Rows registered and imported after the freeze stay labelled retrospective too.
    after = built(decl, items, frozen_ms=0)
    assert {r['sampling_mode'] for r in after['rows']} == {'retrospective_retained_snapshot'}
    assert {r['provenance'] for r in after['rows']} == {'original_after_freeze'}


def test_cut_reached_is_a_clock_fact_not_search_readiness(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    syms, known = symbols_of(items), max(known_of(i) for i in items)
    early = built(declaration(syms, now+HOUR), items, capture_ms=known)
    assert early['status']['cut_reached'] is False and early['status']['search_ready'] is False
    assert early['status']['readiness'] == 'partial_not_search_ready'
    assert 'partial_not_search_ready' in early['coverage']['reason_codes']
    late = built(declaration(syms, known+1), items, capture_ms=known+10)
    assert late['status']['cut_reached'] is True and late['status']['search_ready'] is False
    assert late['status']['readiness'] == 'cut_reached'
    assert late['status']['search_ready_reason'].startswith('pending_separate_search_protocol')
    assert 'cut_reached_not_search_ready' in late['coverage']['reason_codes']


def test_receipts_known_after_the_capture_clock_are_refused(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    known = max(known_of(i) for i in items)
    ds = built(declaration(symbols_of(items), now+HOUR), items, capture_ms=known-1)
    assert not ds['rows']
    assert set(ds['coverage']['exclusion_reasons']) == {'known_after_observed_capture'}
    assert 'receipts_known_after_capture_refused' in ds['coverage']['reason_codes']


def test_retention_bias_is_reported_from_measured_source_meta(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    decl = declaration(symbols_of(items), now+HOUR)
    assert 'retention_unmeasured' in built(decl, items)['coverage']['reason_codes']
    evicted = built(decl, items, meta={'retention': {'evicted_total': 4, 'store_rows': 2}})
    assert 'retention_evicted_records' in evicted['coverage']['reason_codes']
    clean = built(decl, items, meta={'retention': {'evicted_total': 0, 'store_rows': 2}})
    assert 'retention_no_evictions_observed' in clean['coverage']['reason_codes']


# --- cutoff, clocks and tampering -----------------------------------------

def test_discovery_cut_and_restored_local_clock_exclusions(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    syms = symbols_of(items)
    known = max(i['record']['available_ms'] for i in items)

    early = built(declaration(syms, known), items)
    assert not early['rows']
    assert set(early['coverage']['exclusion_reasons']) == {'not_known_before_discovery_cut'}

    # A restored archive is learned at restore: a later LOCAL clock crosses the cut.
    restored = receipts(path, rows, now, local=now+10*HOUR)
    late = built(declaration(syms, now+HOUR), restored)
    assert not late['rows']
    assert set(late['coverage']['exclusion_reasons']) == {'not_known_before_discovery_cut'}

    # A local clock before the source's own import is impossible; never accepted.
    backdated = receipts(path, rows, now, local=0)
    forged = built(declaration(syms, now+HOUR), backdated)
    assert set(forged['coverage']['exclusion_reasons']) == {'local_import_precedes_source_import'}


def test_before_start_and_outside_universe_are_separate_reason_codes(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    syms = sorted(symbols_of(items))
    late_start = built(declaration(syms, now+HOUR, start=now), items)
    assert set(late_start['coverage']['exclusion_reasons']) == {'before_declared_start'}
    narrow = built(declaration(syms[:1], now+HOUR), items)
    outside = sum(1 for i in items if i['record']['source']['symbol'] != syms[0])
    assert narrow['coverage']['exclusion_reasons']['outside_declared_universe'] == outside
    assert narrow['coverage']['observed_symbols'] == syms[:1]


def test_replay_tampering_is_refused_per_record_and_per_dataset(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    decl = declaration(symbols_of(items), now+HOUR)
    good = built(decl, items)

    tampered = copy.deepcopy(items)
    tampered[0]['record']['observation']['price_change_bps'] += 1
    ds = built(decl, tampered)
    assert ds['coverage']['exclusion_reasons'] == {'replay_failed': 1}
    assert ds['excluded'][0]['detail'] == 'typed_outcome_replay_mismatch'

    assert D.replay(good) == good
    with pytest.raises(ValueError, match='dataset_integrity_mismatch'):
        D.replay(dict(good, rows=[]))
    resealed = {k: v for k, v in good.items() if k != 'dataset_version'}
    resealed['rows'] = resealed['rows'][:1]
    with pytest.raises(ValueError, match='dataset_replay_mismatch'):
        D.replay(dict(resealed, dataset_version=D.digest(resealed)))


def test_duplicate_underlying_case_is_not_a_second_observation(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    decl = declaration(symbols_of(items), now+HOUR)
    doubled = items + [dict(items[0], source_key='copy:'+items[0]['source_key'])]
    ds = built(decl, doubled)
    assert ds['coverage']['exclusion_reasons'] == {'duplicate_case_id': 1}
    assert len(ds['coverage']['observed_symbols']) == len(symbols_of(items))


# --- features, labels and leakage -----------------------------------------

def test_features_never_carry_a_label_and_predate_the_outcome(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now) + journal(now, sorted(symbols_of(receipts(path, rows, now)))[0])
    decl = declaration(symbols_of(receipts(path, rows, now)), now+HOUR)
    ds = built(decl, items)
    assert ds['rows']
    for row in ds['rows']:
        assert not D._leaf_keys(row['features'], set()) & D.LABEL_KEYS
        assert set(row['labels']) & {'case_kind', 'available_ms', 'actual_status'}
        assert row['labels']['available_ms'] < decl['discovery_cut_ms']
        if row['point_in_time_features']:
            assert row['features']['as_of_ms'] < row['labels']['available_ms']
        else:
            assert row['features']['as_of_ms'] == row['labels']['available_ms']


def test_forecast_rows_keep_selection_as_state_and_outcome_as_label(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    ds = built(declaration(symbols_of(items), now+HOUR), items)
    state = [r for r in ds['rows'] if r['producer'] == 'forecast']
    assert {r['labels']['case_kind'] for r in state} == {'selected_forecast', 'ignored_forecast'}
    assert {r['row_type'] for r in state} == {'state'}
    for row in state:
        assert type(row['features']['selected']) is bool          # treatment, known at as_of
        assert row['labels']['supports'] in ('persistence', 'reversal', 'unresolved')
        assert row['features']['source_available_ms'] <= row['features']['as_of_ms']
        assert row['features']['source_versions'] and row['record_source_version']


def test_journal_rows_are_observation_only_with_honest_unknown_accounting(rounds):
    path, rows, now = rounds
    symbol = sorted(symbols_of(receipts(path, rows, now)))[0]
    items = journal(now, symbol)
    decl = declaration([symbol], now+HOUR, coverage=dict(
        expected_kinds={'skip': 1, 'executed_trade': 1}, expected_symbols=[symbol],
        min_rows=2, max_sequence_gap_ms=10**12))
    ds = built(decl, items)
    assert len(ds['rows']) == 2 and ds['accounting']['sequence_rows'] == 0
    for row in ds['rows']:
        f = row['features']
        assert row['point_in_time_features'] is False
        assert row['feature_availability_reason'] == 'journal_features_first_observed_at_import'
        assert f['feature_availability'] == 'first_observed_at_import'
        assert f['registration_time_availability'] == 'unverified_no_registration_receipt'
        assert f['claimed_registration_ms'] < f['as_of_ms']
        assert row['labels']['actual_status'] == 'unknown'
        assert row['labels']['actual_net_pnl'] is None
        assert row['labels']['actual_reason'] == 'missing_verified_fill_fee_funding_attribution'
    a = ds['accounting']
    assert a['skip_rows'] == 1 and a['trade_rows'] == 1
    assert a['unknown_actual_rows'] == 2 and a['verified_actual_rows'] == 0
    assert a['point_in_time_rows'] == 0 and a['observation_only_rows'] == 2
    assert 'journal_features_first_observed_at_import' in ds['coverage']['reason_codes']
    assert all(g['reason'] == 'observation_only_row_excluded_from_sequences'
               for g in ds['coverage']['sequence_gaps'])
    # The unverified journal default never reaches a feature or a label.
    assert '12345.678' not in json.dumps(ds['rows'])
    assert '12345.678' in json.dumps(ds['receipts'])          # the raw receipt is preserved


def test_no_inferred_historical_strategy_definition(rounds):
    path, rows, now = rounds
    symbol = sorted(symbols_of(receipts(path, rows, now)))[0]
    ds = built(declaration([symbol], now+HOUR), journal(now, symbol))
    attributions = [a for r in ds['rows'] for a in r['features']['strategy_attribution']]
    assert attributions and all(a['definition_version'] is None for a in attributions)
    assert {a['definition_status'] for a in attributions} == {'unknown_no_retained_strategy_definition'}
    assert {a['strategy_id'] for a in attributions} == {'spec:alpha'}


@pytest.mark.parametrize('kind,family,row_type', [
    ('false_signal', 'volume_anomaly', 'failure'),
    ('regime_transition', 'volatility_transition', 'transition'),
])
def test_investigation_cases_become_failure_and_transition_rows(prefix, kind, family, row_type):
    archive, inv, update = archived(prefix, family)
    now = update.observed_ms + 1
    record = O.investigation_case(archive, kind, now)
    items = [{'source_key': kind+':case', 'local_imported_ms': now, 'record': record}]
    cohort = sorted(set(inv.state.cohort) | {inv.state.symbol})
    ds = built(declaration(cohort, now+1), items)
    assert len(ds['rows']) == 1
    row = ds['rows'][0]
    assert row['row_type'] == row_type and row['producer'] == 'investigation_case'
    assert row['features']['family'] == family and row['features']['symbol'] == inv.state.symbol
    assert row['point_in_time_features'] is True
    assert row['features']['as_of_ms'] < row['labels']['available_ms']
    # The resolved assessment is a label; the registered state is not.
    assert 'winner' not in row['features'] and 'case_kind' not in row['features']
    assert row['labels']['case_kind'] == kind and row['labels']['winner']
    assert row['labels']['measurement'] == 'observed_path_not_pnl'
    assert row['labels']['actual_status'] == 'unavailable'
    assert row['features']['dimensions'] and row['features']['membership_version']
    assert row['features']['source_versions'] == list(inv.state.input_versions)
    # Cohort membership travels with the row as a cross-symbol dependence unit.
    assert row['dependence_symbols'] == cohort
    # This producer emits a row only for cases it already classified.
    assert 'investigation_case_labels_are_classification_conditioned' in \
        ds['coverage']['reason_codes']
    assert ds['coverage']['selection_bias'] == D.CASE_SELECTION_BIAS
    assert D.CASE_SELECTION_BIAS in ds['limitations']


# --- sequences and dependence ---------------------------------------------

def _chain_declaration(items, **over):
    cut = max(i['record']['imported_ms'] for i in items) + 1
    return declaration({i['record']['source']['registration']['baseline']['symbol'] for i in items},
                       cut, **over)


def test_sequence_rows_use_only_priors_fully_known_before_the_current_state():
    items = chained()
    ds = built(_chain_declaration(items), items)
    seq = [r for r in ds['rows'] if r['row_type'] == 'sequence']
    assert len(seq) == 1
    f = seq[0]['features']
    assert f['prior_known_ms'] < f['as_of_ms'] and f['elapsed_ms'] > 0
    assert f['prior_observed']['known_ms'] == f['prior_known_ms']
    assert 'prior_price_change_bps' in f['prior_observed']
    assert seq[0]['labels']['available_ms'] > f['as_of_ms']
    assert seq[0]['parent_row_ids'][1] == f['current_row_id']
    assert not D._leaf_keys(f, set()) & D.LABEL_KEYS
    # Lineage back to the prior receipt's exact source version.
    prior = next(r for r in ds['rows'] if r['row_id'] == f['prior_row_id'])
    assert f['prior_record_source_version'] == prior['record_source_version']
    assert f['prior_case_id'] == prior['case_id']
    assert ds['accounting']['by_row_type'] == {'state': 2} and ds['accounting']['sequence_rows'] == 1


def test_a_prior_imported_late_is_hindsight_not_history(rounds):
    """Same labels and label clocks; only the import clock differs."""
    path, rows, now = rounds
    symbol, prompt = delayed_pair(path, rows, now, late=False)
    _, late = delayed_pair(path, rows, now, late=True)
    assert [i['record']['available_ms'] for i in prompt] == \
        [i['record']['available_ms'] for i in late]
    assert late[0]['record']['imported_ms'] > prompt[0]['record']['imported_ms']

    linked = built(declaration([symbol], max(known_of(i) for i in prompt)+1), prompt)
    seq = [r for r in linked['rows'] if r['row_type'] == 'sequence']
    assert len(seq) == 1 and seq[0]['features']['prior_producer'] == 'forecast'

    hindsight = built(declaration([symbol], max(known_of(i) for i in late)+1), late)
    assert not [r for r in hindsight['rows'] if r['row_type'] == 'sequence']
    assert [g['reason'] for g in hindsight['coverage']['sequence_gaps']] == \
        ['prior_outcome_not_known_before_features']


def test_counterfactual_rows_keep_the_retrospective_class_out_of_features():
    items = chained()
    ds = built(_chain_declaration(items), items)
    base = [r for r in ds['rows'] if r['producer'] == 'counterfactual']
    assert len(base) == 2
    for row in base:
        assert 'decision_kind' not in row['features']
        assert row['labels']['case_kind'] == 'skip'
        assert row['labels']['simulated'] is True
        assert row['labels']['executable_fill_claim'] is False
        assert row['labels']['actual_status'] == 'unknown'
        assert row['labels']['actual_net_pnl'] is None
        assert row['labels']['actual_reason'] == 'simulated_close_fill_is_not_execution'
    assert ds['accounting']['simulated_rows'] == 2
    assert ds['accounting']['verified_actual_rows'] == 0


def test_sequence_gap_beyond_the_declared_maximum_is_refused_and_counted():
    items = chained()
    ds = built(_chain_declaration(items, coverage=dict(
        expected_kinds={}, expected_symbols=[], min_rows=1, max_sequence_gap_ms=1)), items)
    assert not [r for r in ds['rows'] if r['row_type'] == 'sequence']
    gaps = ds['coverage']['sequence_gaps']
    assert [g['reason'] for g in gaps] == ['sequence_gap_exceeds_declared']
    assert gaps[0]['gap_ms'] > 1
    assert 'missing_sequence' in ds['coverage']['reason_codes']


def test_same_instant_resolution_and_registration_does_not_chain(rounds):
    """Forecast rounds resolve and register in one step: not strictly ordered."""
    path, rows, now = rounds
    items = receipts(path, rows, now)
    assert any(sum(1 for i in items if i['record']['source']['symbol'] == s) > 1
               for s in symbols_of(items))
    ds = built(declaration(symbols_of(items), now+HOUR), items)
    assert not [r for r in ds['rows'] if r['row_type'] == 'sequence']
    assert {g['reason'] for g in ds['coverage']['sequence_gaps']} == \
        {'prior_outcome_not_known_before_features'}
    assert 'missing_sequence' in ds['coverage']['reason_codes']


def test_dependence_defaults_to_market_wide_calendar_grouping(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    syms = sorted(symbols_of(items))
    ds = built(declaration(syms, now+HOUR), items)
    base = [r for r in ds['rows'] if r['row_type'] != 'sequence']
    assert len(base) > 1 and len({r['symbol'] for r in base}) > 1
    # Simultaneous episodes across symbols are one unit; no symbol_groups opt-out.
    assert len({r['dependence_group'] for r in ds['rows']}) == 1
    assert ds['accounting']['dependence_groups'] == 1
    assert ds['accounting']['independent_units_upper_bound'] == 1
    assert ds['accounting']['rows'] > 1           # rows are not independent observations

    # Disjoint calendar windows are not merged, and declaring a group changes nothing.
    far = chained('CF/USDT', base_open=(now//Lrn.TF + 100)*Lrn.TF)
    universe = syms + ['CF/USDT']
    cut = max(known_of(i) for i in far) + 1
    apart = built(declaration(universe, cut), items + far)
    assert len({r['dependence_group'] for r in apart['rows']}) > 1
    declared = built(declaration(universe, cut, dependence=dict(
        symbol_groups={'all': universe}, window_pad_ms=0)), items + far)

    def partition(ds):        # row ids are declaration-scoped; compare the shape
        groups = {}
        for row in ds['rows']:
            groups.setdefault(row['dependence_group'], set()).add((row['row_type'], row['case_id']))
        return sorted(sorted(members) for members in groups.values())
    assert partition(declared) == partition(apart)


def test_sequence_rows_share_their_parents_dependence_group():
    items = chained()
    ds = built(_chain_declaration(items), items)
    index = {r['row_id']: r for r in ds['rows']}
    seq = [r for r in ds['rows'] if r['row_type'] == 'sequence']
    assert seq
    for row in seq:
        assert {index[p]['dependence_group'] for p in row['parent_row_ids']} == {row['dependence_group']}
    assert ds['accounting']['dependence_groups'] == 1        # one symbol, one linked episode chain


# --- bounded resources -----------------------------------------------------

def test_bounded_record_and_row_limits(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    syms = symbols_of(items)
    with pytest.raises(ValueError, match='dataset_record_capacity'):
        built(declaration(syms, now+HOUR, limits=dict(max_records=1, max_rows=8)), items)
    capped = built(declaration(syms, now+HOUR, limits=dict(max_records=512, max_rows=2)), items)
    assert len([r for r in capped['rows'] if r['row_type'] != 'sequence']) == 2
    assert capped['coverage']['exclusion_reasons']['row_capacity'] == len(items)-2
    assert 'row_capacity_truncated' in capped['coverage']['reason_codes']
    with pytest.raises(ValueError, match='dataset_duplicate_source_key'):
        built(declaration(syms, now+HOUR), items + [items[0]])
    with pytest.raises(ValueError, match='receipt_envelope_invalid'):
        built(declaration(syms, now+HOUR), [{'source_key': 'k', 'record': {}}], capture_ms=now)


def test_row_cap_counts_sequence_rows_and_truncates_explicitly():
    items = chained()
    full = built(_chain_declaration(items), items)
    assert len(full['rows']) == 3            # two base rows plus their link
    capped = built(_chain_declaration(items, limits=dict(max_records=512, max_rows=2)), items)
    assert len(capped['rows']) == 2
    assert not [r for r in capped['rows'] if r['row_type'] == 'sequence']
    assert capped['coverage']['exclusion_reasons']['row_capacity_total'] == 1
    assert [g['reason'] for g in capped['coverage']['sequence_gaps']] == ['total_row_capacity']
    assert 'row_capacity_truncated' in capped['coverage']['reason_codes']


def test_coverage_reports_shortfalls_against_the_declaration(rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    syms = sorted(symbols_of(items))
    decl = declaration(syms + ['NEVER/USDT'], now+HOUR, coverage=dict(
        expected_kinds={'selected_forecast': 99, 'false_signal': 1},
        expected_symbols=['NEVER/USDT'], min_rows=10**6, max_sequence_gap_ms=10**12))
    ds = built(decl, items)
    status = ds['coverage']['kind_status']
    assert status['selected_forecast']['status'] == 'below_expected'
    assert status['false_signal'] == {'expected': 1, 'observed': 0, 'status': 'absent'}
    assert ds['coverage']['missing_symbols'] == ['NEVER/USDT']
    assert {'declared_symbol_absent', 'rows_below_declared_minimum',
            'kind_absent:false_signal', 'kind_below_expected:selected_forecast'} <= \
        set(ds['coverage']['reason_codes'])


# --- CLI -------------------------------------------------------------------

def _store(path, items):
    with C.ledger(path) as db:
        for item in items:
            S.put(db, item['source_key'], item['record'])
    return path


def _files(tmp_path, decl, name='decl.json'):
    p = tmp_path/name
    p.write_text(json.dumps(decl, indent=2, sort_keys=True))
    return p


def test_cli_freeze_receipt_is_immutable_and_dates_the_declaration(tmp_path, rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    decl = declaration(symbols_of(items), now+10*HOUR)
    dpath, rpath = _files(tmp_path, decl), tmp_path/'freeze.json'
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.OK
    first = json.loads(rpath.read_text())
    assert first['observed_frozen_ms'] > 0 and first['clock_source'] == 'cli_wall_clock_utc'
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.OK
    assert json.loads(rpath.read_text()) == first          # observed clock never rewritten
    other = _files(tmp_path, dict(decl, dataset_id='different'), 'decl2.json')
    assert CLI.main(['freeze', '--declaration', str(other), '--receipt', str(rpath)]) == CLI.CONFLICT
    assert json.loads(rpath.read_text()) == first


def test_cli_build_is_idempotent_and_refuses_conflicting_output(tmp_path, rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    store = _store(tmp_path/'ledger.db', items)
    decl = declaration(symbols_of(items), now+10*HOUR)
    dpath, rpath, out = _files(tmp_path, decl), tmp_path/'freeze.json', tmp_path/'dataset.json'
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.OK
    argv = ['build', '--declaration', str(dpath), '--receipt', str(rpath),
            '--store', str(store), '--output', str(out)]
    assert CLI.main(argv) == CLI.OK
    first = out.read_text()
    ds = json.loads(first)
    assert ds['rows'] and D.replay(ds) == ds
    assert ds['source_meta']['local_clock'] == 'store_imported_ms'
    assert ds['source_meta']['retention']['store_rows'] == len(items)
    assert CLI.main(argv) == CLI.OK
    assert out.read_text() == first                        # rerun rewrites nothing

    changed = _files(tmp_path, dict(decl, dataset_id='other'), 'decl2.json')
    r2 = tmp_path/'freeze2.json'
    assert CLI.main(['freeze', '--declaration', str(changed), '--receipt', str(r2)]) == CLI.OK
    assert CLI.main(['build', '--declaration', str(changed), '--receipt', str(r2),
                     '--store', str(store), '--output', str(out)]) == CLI.CONFLICT
    assert out.read_text() == first
    # A receipt for a different declaration is not evidence for this one.
    assert CLI.main(['build', '--declaration', str(dpath), '--receipt', str(r2),
                     '--store', str(store), '--output', str(tmp_path/'x.json')]) == CLI.USAGE


def _clock(monkeypatch, *values):
    """Drive the CLI's wall clock without touching the real one."""
    seq = list(values)
    monkeypatch.setattr(CLI, 'time', SimpleNamespace(
        time=lambda: (seq.pop(0) if len(seq) > 1 else seq[0]) / 1000,
        monotonic=time.monotonic))


def test_cli_archive_is_stamped_with_the_read_clock_not_the_original_import(tmp_path, rounds,
                                                                           monkeypatch):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    with C.ledger(tmp_path/'ledger.db') as db:
        for item in items:
            S.put(db, item['source_key'], item['record'])
        archive = S.export(db)
    apath = tmp_path/'archive.json'
    apath.write_text(Lrn.encode(archive))
    restore_ms = now + 30*24*HOUR                     # restored a month after the originals
    decl = declaration(symbols_of(items), now+10*HOUR)
    dpath, rpath, out = _files(tmp_path, decl), tmp_path/'freeze.json', tmp_path/'dataset.json'
    _clock(monkeypatch, now+HOUR, restore_ms)
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.OK
    assert CLI.main(['build', '--declaration', str(dpath), '--receipt', str(rpath),
                     '--archive', str(apath), '--output', str(out)]) == CLI.OK
    ds = json.loads(out.read_text())
    meta = ds['source_meta']
    assert meta['local_clock'] == 'archive_read_wall_clock'
    assert meta['trustworthy_local_receipt'] is False
    assert meta['observed_source_read_ms'] == restore_ms
    assert meta['retention']['diagnostic']
    # The original import clock survives inside each receipt but is never the local one.
    assert {r['local_imported_ms'] for r in ds['receipts']} == {restore_ms}
    assert all(r['record']['imported_ms'] < restore_ms for r in ds['receipts'])
    # Learned at restore, so nothing is usable before a cut that predates it.
    assert not ds['rows']
    assert set(ds['coverage']['exclusion_reasons']) == {'not_known_before_discovery_cut'}
    assert D.replay(ds) == ds

    # With a cut after the restore the rows appear, labelled as restored.
    later = _files(tmp_path, declaration(symbols_of(items), restore_ms+1), 'decl2.json')
    r2, out2 = tmp_path/'freeze2.json', tmp_path/'dataset2.json'
    _clock(monkeypatch, now+HOUR, restore_ms)
    assert CLI.main(['freeze', '--declaration', str(later), '--receipt', str(r2)]) == CLI.OK
    assert CLI.main(['build', '--declaration', str(later), '--receipt', str(r2),
                     '--archive', str(apath), '--output', str(out2)]) == CLI.OK
    ds2 = json.loads(out2.read_text())
    assert ds2['rows']
    assert {r['provenance'] for r in ds2['rows']} == {'restored_after_freeze_registered_before'}
    assert 'restored_rows_registered_before_freeze' in \
        ds2['coverage']['prospective_sampling']['reason_codes']


def test_cli_refuses_a_foreign_archive_schema_or_integrity(tmp_path, rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    with C.ledger(tmp_path/'ledger.db') as db:
        for item in items:
            S.put(db, item['source_key'], item['record'])
        archive = S.export(db)
    assert archive['schema_version'] == O.SCHEMA
    decl = declaration(symbols_of(items), now+10*HOUR)
    dpath, rpath = _files(tmp_path, decl), tmp_path/'freeze.json'
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.OK

    def refuse(payload, name):
        p = tmp_path/name
        p.write_text(json.dumps(payload))
        return CLI.main(['build', '--declaration', str(dpath), '--receipt', str(rpath),
                         '--archive', str(p), '--output', str(tmp_path/(name+'.out'))])
    foreign = {k: v for k, v in archive.items()}
    foreign['schema_version'] = 'some-other-archive.v9'
    assert refuse(foreign, 'foreign.json') == CLI.USAGE
    assert refuse(dict(archive, sha256='0'*64), 'broken.json') == CLI.USAGE


def test_cli_rerun_on_a_different_wall_clock_is_still_unchanged(tmp_path, rounds, monkeypatch):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    store = _store(tmp_path/'ledger.db', items)
    decl = declaration(symbols_of(items), now+10*HOUR)
    dpath, rpath, out = _files(tmp_path, decl), tmp_path/'freeze.json', tmp_path/'dataset.json'
    argv = ['build', '--declaration', str(dpath), '--receipt', str(rpath),
            '--store', str(store), '--output', str(out)]
    _clock(monkeypatch, now+HOUR)
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.OK
    _clock(monkeypatch, now+2*HOUR)
    assert CLI.main(argv) == CLI.OK
    first = out.read_text()
    assert json.loads(first)['capture']['observed_capture_ms'] == now+2*HOUR
    # A later run reads a different clock; the receipts and their clocks are identical.
    _clock(monkeypatch, now+5*HOUR)
    assert CLI.main(argv) == CLI.OK
    assert out.read_text() == first


def test_cli_refuses_a_corrupted_existing_receipt_or_output(tmp_path, rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    store = _store(tmp_path/'ledger.db', items)
    decl = declaration(symbols_of(items), now+10*HOUR)
    dpath, rpath, out = _files(tmp_path, decl), tmp_path/'freeze.json', tmp_path/'dataset.json'
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.OK
    argv = ['build', '--declaration', str(dpath), '--receipt', str(rpath),
            '--store', str(store), '--output', str(out)]
    assert CLI.main(argv) == CLI.OK
    good_receipt, good_output = rpath.read_text(), out.read_text()

    # A tampered output is a conflict, not a silent "unchanged".
    ds = json.loads(good_output)
    ds['rows'] = ds['rows'][:1]
    out.write_text(json.dumps(ds))
    assert CLI.main(argv) == CLI.CONFLICT
    out.write_text(good_output)
    assert CLI.main(argv) == CLI.OK

    # A freeze clock edited after the fact no longer matches the stored dataset.
    forged = json.loads(good_receipt)
    forged['observed_frozen_ms'] += 1
    rpath.write_text(json.dumps(forged))
    assert CLI.main(argv) == CLI.CONFLICT
    assert out.read_text() == good_output
    # A receipt whose declaration hash is forged is refused before any source is read.
    rpath.write_text(json.dumps(dict(forged, declaration_version='0'*64)))
    assert CLI.main(argv) == CLI.USAGE
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.CONFLICT
    rpath.write_text('{"not": "a receipt"}')
    assert CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)]) == CLI.CONFLICT


def test_cli_never_writes_the_source_ledger(tmp_path, rounds):
    path, rows, now = rounds
    items = receipts(path, rows, now)
    store = _store(tmp_path/'ledger.db', items)
    before = store.read_bytes()
    decl = declaration(symbols_of(items), now+10*HOUR)
    dpath, rpath = _files(tmp_path, decl), tmp_path/'freeze.json'
    CLI.main(['freeze', '--declaration', str(dpath), '--receipt', str(rpath)])
    CLI.main(['build', '--declaration', str(dpath), '--receipt', str(rpath),
              '--store', str(store), '--output', str(tmp_path/'d.json')])
    assert store.read_bytes() == before
