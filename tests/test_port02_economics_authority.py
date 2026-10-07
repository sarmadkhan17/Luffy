"""PORT-02: calibrated expected-net economics authority. Fixtures are TEST-ONLY."""
from dataclasses import replace
from decimal import Decimal
import json

import pytest
from trader.portfolio import economics as e
from trader.engine import paper_cost_evidence as pc
from trader.portfolio.allocator import Source
from tests.economics_fixtures import binding, frozen, install_models


@pytest.fixture(autouse=True)
def adapters(monkeypatch):
    install_models(monkeypatch)


def changed(source, **changes):
    raw = json.loads(source.payload_json)
    raw.update(changes)
    return Source.freeze(source.source_id, raw)


def res(i):
    return json.loads(e.build(i).result_json)


def test_production_registries_are_empty_and_only_funding_adapter_is_registered():
    import subprocess
    import sys
    from trader.observability.funding_events import METHOD
    # A fresh interpreter (no fixtures) is the production registry state.
    out = subprocess.check_output([sys.executable, '-c',
        'from trader.portfolio import economics as e; from trader.engine import paper_cost_evidence as p;'
        'print(len(e.GROSS_MODELS),len(e.RESERVE_MODELS),len(e.COST_SCOPE_MODELS),sorted(p.SOURCE_VALIDATORS))'],
        text=True).strip()
    assert out == f"0 0 0 ['{METHOD}']"


@pytest.mark.parametrize('field,value', [
    ('instrument', 'venue:futures:ETHUSDT'), ('direction', 'SHORT'), ('horizon', '8h'),
    ('quantity_basis', 'base_quantity:2'), ('capital_basis', 'other'), ('units', 'EUR'),
    ('opportunity_id', 'other'), ('as_of_ms', 1100)])
@pytest.mark.parametrize('source', ['gross', 'uncertainty'])
def test_model_source_bound_to_a_different_context_is_incompatible(field, value, source):
    i = frozen()
    other = replace(i.binding, **{field: value})
    if field == 'instrument':
        other = replace(other, market_type='futures')
    r = res(replace(i, binding=other))
    assert r['economic_status'] in ('INCOMPATIBLE_CONTEXT', 'UNAVAILABLE')
    net = r['components']['EXPECTED_NET_VALUE']
    assert net['status'] == 'UNAVAILABLE' and net['value'] is None


@pytest.mark.parametrize('field', ['gross', 'uncertainty', 'costs'])
@pytest.mark.parametrize('change', [{'version': '2'}, {'method': 'UNREGISTERED'}, {'units': 'EUR'},
                                    {'captured_ms': 5000}, {'valid_until_ms': 899}])
def test_revision_mismatch_unit_or_freshness_refuses_net(field, change):
    i = frozen()
    r = res(replace(i, **{field: changed(getattr(i, field), **change)}))
    assert r['components']['EXPECTED_NET_VALUE']['status'] == 'UNAVAILABLE'
    assert r['components']['EXPECTED_NET_VALUE']['value'] is None
    assert r['economic_status'] != 'POSITIVE'


@pytest.mark.parametrize('field', ['gross', 'uncertainty', 'costs'])
def test_material_source_change_changes_receipt_identity(field):
    i = frozen()
    base = e.build(i).receipt_id
    other = replace(i, **{field: changed(getattr(i, field), provenance={'authority': 'REVISED'})})
    try:
        assert e.build(other).receipt_id != base
    except ValueError:
        pass  # a refusal is also not the same receipt


def test_model_revision_registered_later_does_not_rewrite_historical_receipt(monkeypatch):
    i = frozen()
    old = e.build(i)
    monkeypatch.setitem(e.GROSS_MODELS, ('TEST-ONLY-distribution', '2'), lambda raw: True)
    revised = replace(i, gross=changed(i.gross, version='2', value='99'))
    new = e.build(revised)
    assert new.receipt_id != old.receipt_id
    assert e.build(e.from_inputs(json.loads(old.inputs_json))) == old
    assert e.verify(old, i, 1500)


def test_unregistering_a_model_does_not_alter_stored_bytes_but_blocks_reverification(monkeypatch):
    i = frozen()
    old = e.build(i)
    stored = old.result_json
    monkeypatch.setitem(e.GROSS_MODELS, ('TEST-ONLY-distribution', '1'), lambda raw: False)
    assert old.result_json == stored
    assert not e.verify(old, i, 1500)


def test_positive_gross_cannot_hide_negative_net_and_reserve_cannot_be_omitted():
    i = frozen('-1')  # gross 2.1; costs 2.1; reserve 1 -> net -1
    r = res(i)
    assert Decimal(r['components']['EXPECTED_GROSS_VALUE']['value']) > 0
    assert Decimal(r['components']['EXPECTED_NET_VALUE']['value']) < 0
    assert r['economic_status'] == 'NON_POSITIVE'
    no_reserve = res(replace(i, uncertainty=None))
    assert no_reserve['components']['UNCERTAINTY_RESERVE']['value'] is None
    assert no_reserve['components']['EXPECTED_NET_VALUE']['value'] is None
    assert no_reserve['economic_status'] == 'UNAVAILABLE'


def test_unavailable_and_non_positive_never_beat_cash():
    from tests.test_portfolio_allocator import candidate, inputs, result as allocated
    a = candidate()
    for receipt in (e.build(e.Inputs(binding())), e.build(frozen('-1')), e.build(frozen('0'))):
        econ, _ = e.to_allocator(receipt)
        r = allocated(inputs(replace(a, economics=econ)))
        assert r['decision'] == 'NO_ALLOCATION' and r['cash_candidate']['selected']


def test_zero_only_from_authority_never_from_absence():
    r = res(e.Inputs(binding()))
    for k in ('EXPECTED_GROSS_VALUE', 'COMMISSION', 'SLIPPAGE', 'FUNDING/BORROW',
              'UNCERTAINTY_RESERVE', 'EXPECTED_NET_VALUE'):
        assert r['components'][k]['value'] is None and r['components'][k]['status'] == 'UNAVAILABLE'
    assert res(frozen())['components']['SLIPPAGE']['value'] == '0'  # authoritative zero


def test_cost_scope_for_other_instrument_or_trade_quantity_refuses():
    i = frozen()
    raw = json.loads(i.costs.payload_json)
    raw['trade']['symbol'] = 'venue:futures:ETHUSDT'
    with pytest.raises(ValueError):
        e.build(replace(i, costs=Source.freeze(i.costs.source_id, raw)))


def test_economics_receipt_grants_no_allocation_risk_owner_or_order_authority():
    r = e.build(frozen('50'))
    text = r.result_json + r.inputs_json
    payload = json.loads(r.result_json)
    assert payload['side_effects'] == 'NONE'
    for forbidden in ('allocation', 'approved', 'risk_approval', 'owner_approval', 'order_permission', 'quantity_authorized'):
        assert forbidden not in set(payload) | set(payload['components'])
    assert 'order' not in payload
    econ, _ = e.to_allocator(r)
    assert not hasattr(econ, 'allocation') and not hasattr(econ, 'approved')
    assert text


def test_funding_adapter_alone_cannot_establish_net(monkeypatch):
    for reg in (e.GROSS_MODELS, e.RESERVE_MODELS, e.COST_SCOPE_MODELS):
        for k in list(reg):
            monkeypatch.delitem(reg, k)
    r = res(frozen())
    assert r['economic_status'] == 'UNAVAILABLE'
    assert r['components']['EXPECTED_NET_VALUE']['value'] is None
