"""One economic learning row, not two separately scored hedge legs."""
from copy import deepcopy

import pytest

from tests.test_coordinated_accounting import component,assembled
from tests.test_pit_dataset import built,declaration
from trader.cognition import dataset as D,m32_evidence as E
from trader.engine import coordinated_accounting as G,trade_accounting as T


@pytest.fixture
def group(monkeypatch):
    return assembled([component(monkeypatch),
        component(monkeypatch,role='hedge',symbol='ETH/USDT',offset=1200)])


def envelope(record,key):
    return dict(source_key=key,local_imported_ms=record['imported_ms'],record=record)


def dataset(group,*,universe=('BTC/USDT','ETH/USDT'),extra=(),cut=10000):
    receipt=envelope(G.verified_outcome(group,9000),'combined')
    decl=declaration(universe,cut)
    return built(decl,[*extra,receipt]),decl,receipt


def test_combined_result_reaches_replayable_dataset_and_actual_audit_as_one_group(group):
    data,decl,receipt=dataset(group)
    assert D.replay(data)==data
    rows=[r for r in data['rows'] if r['row_type']!='sequence']
    assert len(rows)==1
    r=rows[0]
    assert r['underlying_id']=='primary-trade' and r['producer']=='verified_execution'
    assert r['labels']['actual_net_pnl']==-10. and r['labels']['actual_currency']=='USDT'
    assert r['labels']['economic_group_id']=='economic-group'
    assert r['dependence_symbols']==['BTC/USDT','ETH/USDT']
    assert 'economic_group_id' not in r['features']
    accepted,refused=E.eligible_actuals(rows,[receipt],decl)
    assert len(accepted)==1 and not refused


def test_retained_individual_legs_cannot_count_again_after_combined_accounting(group):
    legs=[envelope(T.verified_outcome(p['capture'],9000),'leg:'+str(i))
          for i,p in enumerate(group['components'])]
    data,decl,receipt=dataset(group,extra=legs)
    rows=[r for r in data['rows'] if r['row_type']!='sequence']
    assert len(rows)==1 and rows[0]['labels']['actual_net_pnl']==-10.
    assert len([x for x in data['excluded'] if x['reason']=='component_of_combined_economic_outcome'])==2
    assert D.replay(data)==data
    accepted,refused=E.eligible_actuals(data['rows'],[*legs,receipt],decl)
    assert len(accepted)==1 and refused=={'no_eligible_pit_execution_episode':2}


def test_future_combined_receipt_does_not_hide_a_leg_known_before_cut(group):
    leg=envelope(T.verified_outcome(group['components'][0]['capture'],8100),'leg')
    data,_,_=dataset(group,extra=[leg],cut=8500)
    rows=[r for r in data['rows'] if r['row_type']!='sequence']
    assert len(rows)==1 and rows[0]['source_key']=='leg'
    assert not any(x['reason']=='component_of_combined_economic_outcome' for x in data['excluded'])


def test_hidden_second_market_cannot_enter_a_single_market_declaration(group):
    data,decl,receipt=dataset(group,universe=['BTC/USDT'])
    assert not data['rows']
    assert any(x['reason']=='outside_declared_universe' for x in data['excluded'])
    # Even a caller supplying rows from another declaration cannot admit it.
    full,_,_=dataset(group)
    accepted,refused=E.eligible_actuals(full['rows'],[receipt],decl)
    assert not accepted and refused=={'accounting_outside_declaration':1}


def test_diagnostic_group_is_excluded_from_actual_evidence(monkeypatch):
    g=assembled([component(monkeypatch,strategy_id='diagnostic:fixture'),
        component(monkeypatch,role='hedge',symbol='ETH/USDT',offset=1200,strategy_id='diagnostic:fixture')])
    data,decl,receipt=dataset(g)
    accepted,refused=E.eligible_actuals(data['rows'],[receipt],decl)
    assert not accepted and refused=={'diagnostic_execution_excluded':1}


def test_corrupt_and_unrelated_receipts_do_not_break_combined_assembly(group):
    broken=deepcopy(G.verified_outcome(group,9000));broken.pop('source')
    data,_,_=dataset(group,extra=[envelope(broken,'corrupt')])
    assert len([r for r in data['rows'] if r['row_type']!='sequence'])==1
    assert any(x['reason']=='replay_failed' for x in data['excluded'])


def test_older_frozen_dataset_keeps_its_original_group_exclusion(group):
    current,_,_=dataset(group)
    old=D.build(current['declaration'],current['freeze'],current['capture'],
        current['receipts'],current['source_meta'],economic_outcome_policy=None)
    assert 'economic_outcome_policy' not in old
    assert not old['rows'] and old['excluded'][0]['reason']=='unsupported_record_kind'
    assert D.replay(old)==old
    assert current['economic_outcome_policy']==D.ECONOMIC_OUTCOME_POLICY
    altered=deepcopy(current);altered.pop('economic_outcome_policy')
    altered['dataset_version']=D.digest({k:v for k,v in altered.items() if k!='dataset_version'})
    with pytest.raises(ValueError,match='dataset_replay_mismatch'):D.replay(altered)
