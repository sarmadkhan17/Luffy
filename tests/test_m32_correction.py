"""Synthetic evidence-boundary tests; no search or venue calls."""
from copy import deepcopy
import json
import sqlite3

import pytest

from tests.test_whole_trade_accounting import artifact, seal
from tests.test_pit_dataset import declaration, built
from trader.engine import trade_accounting as A
from trader.cognition import dataset as D, outcomes as O
from trader.cognition.m32_correction import _complete_actual, _coverage, audit, build_independence_groups
from trader.cognition.m32_evidence import eligible_actuals, pit_regime
from trader.cognition.m32_protocol import pattern_id
from trader.cognition.m32_search import applicable_label_families


def row(i, lo=100, hi=200, group=None):
    return dict(row_id=str(i), case_id='case'+str(i), underlying_id='episode'+str(i),
                symbol='BTC/USDT', row_type='state', producer='forecast',
                dependence_group=group or 'g'+str(i), point_in_time_features=True,
                features=dict(as_of_ms=lo, source_versions=[]),
                clocks=dict(registered_ms=lo, known_ms=hi), labels=dict(available_ms=hi))


def test_overlapping_and_identical_windows_cannot_inflate_groups():
    rows = [row(i, 100+i, 200+i) for i in range(3)]
    for i, r in enumerate(rows):
        r['independence_provenance'] = dict(collection_period_id='period'+str(i),
            source_manifest_version='version', independent_of=['g0000'], start_ms=0, end_ms=300)
    result = build_independence_groups(rows, pad=0)
    assert result['effective_groups_after'] == 1
    assert result['additional_groups_built'] == 0
    assert build_independence_groups(rows+deepcopy(rows), pad=0)['effective_groups_after'] == 1


def test_distinct_period_names_do_not_split_existing_group():
    rows = [row(i, 100+i*1000, 200+i*1000, group='original') for i in range(3)]
    for i, r in enumerate(rows):
        r['independence_provenance'] = dict(collection_period_id=str(i),
                                          source_manifest_version='v', independent_of=['original'])
    assert build_independence_groups(rows, pad=0)['effective_groups_after'] == 1


def test_disjoint_components_are_upper_bounds_not_independence_certificates():
    result = build_independence_groups([row(1), row(2, 1000, 1100)], pad=0)
    assert result['effective_groups_after'] == 2
    assert result['independence_established'] is False


@pytest.mark.parametrize('link', ['parent', 'case', 'underlying', 'input', 'shared', 'prior'])
def test_cross_window_dependencies_merge_disjoint_rows(link):
    a, b = row(1), row(2, 1000, 1100)
    if link == 'parent': b['parent_row_ids'] = [a['row_id']]
    if link == 'case': b['case_id'] = a['case_id']
    if link == 'underlying': b['underlying_id'] = a['underlying_id']
    if link == 'input':
        a['features']['source_versions'] = ['exact']
        b['features']['source_versions'] = ['exact']
    if link == 'shared': b['shared_underlying_row_id'] = a['row_id']
    if link == 'prior': b['features']['prior_row_id'] = a['row_id']
    assert build_independence_groups([a,b], pad=0)['effective_groups_after'] == 1


def test_market_wide_transitive_overlap_and_padding():
    rows = [row(1,100,200), row(2,190,300), row(3,290,400)]
    rows[1]['symbol'] = 'ETH/USDT'
    assert build_independence_groups(rows, pad=0)['effective_groups_after'] == 1
    assert build_independence_groups([row(1),row(2,210,300)], pad=10)['effective_groups_after'] == 1


@pytest.mark.parametrize('mutation', ['missing_parent','conflict','clock','identity'])
def test_incomplete_or_conflicting_group_evidence_fails_closed(mutation):
    rows = [row(1)]
    if mutation == 'missing_parent': rows[0]['parent_row_ids'] = ['absent']
    if mutation == 'conflict': rows.append(dict(rows[0], symbol='ETH/USDT'))
    if mutation == 'clock': del rows[0]['clocks']['known_ms']
    if mutation == 'identity': del rows[0]['case_id']
    result = build_independence_groups(rows)
    assert result['effective_groups_after'] == 0 and result['refusal']


def regime_row(i, label='RANGING'):
    r = row(i,1000,2000)
    receipt = dict(schema_version='pit-regime-receipt.v1', symbol=r['symbol'], label=label,
        classifier_version='classifier-code-sha256', registered_ms=1000, recorded_ms=999,
        inputs=[dict(version_id='bar-version', symbol=r['symbol'], open_ms=0,close_ms=900,
                     available_ms=950,open=1,high=2,low=1,close=2,volume=3)])
    receipt['sha256'] = O.digest(receipt)
    r['features']['regime_receipt'] = receipt
    return r


def test_distinct_pit_regimes_not_rows():
    rows = [regime_row(i) for i in range(3)]
    assert _coverage(rows)['non_unknown_regime_count'] == 1
    assert _coverage(rows+[regime_row(4,'TRENDING_UP')])['non_unknown_regime_count'] == 2


@pytest.mark.parametrize('mutation', ['bare','future','classifier','inputs','late_input','tamper','observation','unknown'])
def test_unreceipted_or_future_regimes_remain_unknown(mutation):
    r = regime_row(1); receipt = r['features']['regime_receipt']
    if mutation == 'bare': r['features'] = dict(as_of_ms=1000,regime='RANGING')
    if mutation == 'future': receipt['recorded_ms'] = 1001
    if mutation == 'classifier': receipt['classifier_version'] = ''
    if mutation == 'inputs': receipt['inputs'] = []
    if mutation == 'late_input': receipt['inputs'][0]['available_ms'] = 1001
    if mutation == 'tamper': receipt['label'] = 'TRENDING_UP'
    if mutation == 'observation': r['point_in_time_features'] = False
    if mutation == 'unknown': receipt['label'] = 'UNKNOWN'
    if mutation != 'tamper': receipt['sha256'] = O.digest({k:v for k,v in receipt.items() if k!='sha256'})
    assert pit_regime(r) == 'unknown'


def execution(artifact):
    record = A.verified_outcome(artifact,6000)
    envelope = dict(source_key='execution:trade', local_imported_ms=6000, record=record)
    decl = declaration(['BTC/USDT'],cut=7000)
    dataset = built(decl,[envelope])
    return envelope,decl,dataset


def test_canonical_whole_trade_replays_through_dataset(artifact):
    envelope, decl, dataset = execution(artifact)
    assert _complete_actual(envelope['record'])
    assert len(dataset['rows']) == 1
    r = dataset['rows'][0]
    assert r['producer'] == 'verified_execution'
    assert r['labels']['actual_status'] == 'verified_actual'
    assert r['labels']['actual_net_pnl'] == 25
    D.replay(dataset)


def test_tampered_and_missing_accounting_cannot_promote(artifact):
    record = A.verified_outcome(artifact,6000)
    changed = deepcopy(record); changed['actual_execution']['net_pnl'] = 100
    assert not _complete_actual(changed)
    changed = deepcopy(record); changed['source']['whole_trade_capture']['funding_history'] = {}
    assert not _complete_actual(changed)
    assert not _complete_actual({'actual_execution': {'status':'unknown'}})
    assert not _complete_actual({'actual_execution': {'status':'verified_actual','net_pnl':1}})


def test_accounting_deduplicates_trade_across_rows_sequences_and_stores(artifact):
    envelope, decl, dataset = execution(artifact)
    rows = dataset['rows']
    sequence = dict(rows[0],row_id='sequence',row_type='sequence')
    accepted, refused = eligible_actuals(rows+[sequence], [envelope]*3, decl)
    assert len(accepted) == 1 and not refused


@pytest.mark.parametrize('mutation', ['cut','local','universe','start','symbol','identity','registration','sequence','nonpit'])
def test_accounting_must_join_eligible_pit_episode_before_cut(artifact,mutation):
    envelope, decl, dataset = execution(artifact)
    r = dataset['rows'][0]
    if mutation == 'cut': decl['discovery_cut_ms'] = 6000
    if mutation == 'local': envelope['local_imported_ms'] = 8000
    if mutation == 'universe': decl['universe'] = ['ETH/USDT']
    if mutation == 'start': decl['start_ms'] = 1001
    if mutation == 'symbol': r['symbol'] = 'ETH/USDT'
    if mutation == 'identity': r['underlying_id'] = 'other'
    if mutation == 'registration': r['clocks']['registered_ms'] += 1
    if mutation == 'sequence': r['row_type'] = 'sequence'
    if mutation == 'nonpit': r['point_in_time_features'] = False
    accepted, refused = eligible_actuals(dataset['rows'],[envelope],decl)
    assert not accepted and refused


def test_observation_labels_do_not_become_accounting(artifact):
    envelope,decl,dataset = execution(artifact)
    r = dataset['rows'][0]
    r['producer'] = 'forecast'; r['labels']['actual_status'] = 'unknown'
    before = deepcopy(r)
    assert not eligible_actuals([r],[envelope],decl)[0]
    assert r == before


def test_conflicting_receipts_refuse_trade(artifact):
    envelope,decl,dataset = execution(artifact)
    changed = deepcopy(artifact)
    changed['funding_history']['pages'][0]['rows'][0]['income'] = -3
    changed['assessment'] = A.assess(changed)
    seal(changed)
    second = dict(envelope,record=A.verified_outcome(changed,6000))
    accepted,refused = eligible_actuals(dataset['rows'],[envelope,second],decl)
    assert not accepted and refused['conflicting_accounting_receipts'] == 1


def test_forecast_persistence_path_and_pattern_identity_preserved():
    pattern = {'family':'state','atoms':[{'family':'forecast','feature':'direction','value':1}]}
    assert applicable_label_families(pattern) == ('case_kind','persistence')
    assert pattern_id(pattern) == pattern_id(json.loads(json.dumps(pattern,sort_keys=True)))


def test_current_artifact_still_fails_power_audit():
    report = audit()
    assert report['artifact_sha256'] == '3a5ea9bd5a8a36ccfb0c50f0f6a3b17fdcb618d27922f92b7028edef10a0f7a8'
    assert report['outcome_eligibility']['verified_outcome_count_after'] == 0
    assert report['dependence']['after'] == 1
    assert report['dependence']['effective_independent_sample_size'] is None
    assert report['coverage']['non_unknown_regime_count'] == 0
    assert not report['power_audit']['pass']


def test_forward_dataset_accepts_whole_trade_without_backdating(artifact):
    envelope, decl, _ = execution(artifact)
    decl.update(collection_mode='forward', start_ms=500)
    ds = built(decl, [envelope], frozen_ms=500)
    assert len(ds['rows']) == 1
    D.replay(ds)
    assert ds['rows'][0]['clocks']['known_ms'] == 6000
    assert ds['rows'][0]['sampling_mode'] == 'forward_after_declared_freeze'


def test_audit_joins_and_deduplicates_embedded_and_two_stores(artifact, tmp_path):
    envelope, decl, ds = execution(artifact)
    typed, accounting = tmp_path/'typed.db', tmp_path/'accounting.db'
    with sqlite3.connect(typed) as db:
        db.execute('create table typed_outcomes(payload, imported_ms)')
        db.execute('insert into typed_outcomes values (?,?)',
                   (json.dumps(envelope['record']),6000))
        db.execute('insert into typed_outcomes values (?,?)',
                   (json.dumps({'actual_execution':'malformed'}),6000))
    with sqlite3.connect(accounting) as db:
        db.execute('create table execution_accounting(payload)')
        db.execute('insert into execution_accounting values (?)',(json.dumps(envelope),))
    before = {p:p.read_bytes() for p in (typed,accounting)}
    report = audit({'dataset':ds},typed,accounting)
    assert report['outcome_eligibility']['verified_outcome_count_after'] == 1
    assert report['outcome_eligibility']['embedded_verified_actual'] == 1
    assert report['outcome_eligibility']['replayable_typed_receipts_before_join'] == 1
    assert report['outcome_eligibility']['replayable_accounting_receipts_before_join'] == 1
    assert before == {p:p.read_bytes() for p in (typed,accounting)}
    # Labels alone cannot certify accounting.
    ds['receipts'] = []
    ds['rows'][0]['features']['trade_id'] = 'unrelated'
    ds['rows'][0]['underlying_id'] = 'unrelated'
    report = audit({'dataset':ds},typed,accounting)
    assert report['outcome_eligibility']['verified_outcome_count_after'] == 0


def test_actual_receipt_requires_local_import_clock(artifact):
    envelope,decl,ds = execution(artifact)
    del envelope['local_imported_ms']
    assert not eligible_actuals(ds['rows'],[envelope],decl)[0]


def test_unknown_does_not_gain_actual_count_from_label_alone(artifact,tmp_path):
    envelope,decl,ds = execution(artifact)
    ds['receipts'] = []
    report = audit({'dataset':ds},tmp_path/'absent1',tmp_path/'absent2')
    assert report['outcome_eligibility']['embedded_verified_actual'] == 0
    assert report['outcome_eligibility']['verified_outcome_count_after'] == 0
