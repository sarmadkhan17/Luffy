from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
import json
from pathlib import Path
import subprocess
import sys

import pytest
from trader.portfolio import economics as e
from trader.portfolio.allocator import Source, canonical, digest
from tests.economics_fixtures import binding, frozen, install_models, model


@pytest.fixture(autouse=True)
def adapters(monkeypatch):
    install_models(monkeypatch)


def result(i):
    return json.loads(e.build(i).result_json)


def changed(source, **changes):
    raw = json.loads(source.payload_json)
    raw.update(changes)
    return Source.freeze(source.source_id, raw)


@pytest.mark.parametrize('historical', [
    {'win_rate': .99}, {'profit_factor': 50}, {'research': 'SUPPORTED'},
    {'referee': 'PASS'}, {'attention': 100}, {'confidence': .99}, {'total_return': 1000}])
def test_historical_statistics_never_establish_ev(historical):
    i = e.Inputs(binding(), gross=Source.freeze('historical', historical))
    r = result(i)
    assert r['components']['EXPECTED_GROSS_VALUE']['status'] != 'ESTABLISHED'
    assert r['components']['EXPECTED_NET_VALUE']['value'] is None


def test_production_has_no_registered_models_even_complete_payload(monkeypatch):
    i = frozen()
    for registry in (e.GROSS_MODELS, e.RESERVE_MODELS, e.COST_SCOPE_MODELS):
        monkeypatch.setattr(e, next(k for k in ('GROSS_MODELS', 'RESERVE_MODELS', 'COST_SCOPE_MODELS')
                                   if getattr(e, k) is registry), {})
    r = result(i)
    assert r['economic_status'] == 'UNAVAILABLE'
    assert r['economic_model_status'] == 'INSUFFICIENT_EVIDENCE'


@pytest.mark.parametrize('missing', ['gross', 'costs', 'uncertainty'])
def test_missing_component_is_unknown_not_zero(missing):
    r = result(replace(frozen(), **{missing: None}))
    assert r['economic_status'] == 'UNAVAILABLE'
    assert r['components']['EXPECTED_NET_VALUE']['status'] == 'UNAVAILABLE'
    assert r['components']['EXPECTED_NET_VALUE']['value'] is None


@pytest.mark.parametrize('value,status', [('2', 'POSITIVE'), ('0', 'NON_POSITIVE'), ('-2', 'NON_POSITIVE')])
def test_exact_test_only_net_and_classification(value, status):
    i = frozen(value)
    with localcontext() as ctx:
        ctx.prec = 2
        r = result(i)
    c = r['components']
    assert Decimal(c['EXPECTED_NET_VALUE']['value']) == Decimal(value)
    assert Decimal(c['COMMISSION']['value']) == Decimal('2.1')
    assert c['SLIPPAGE']['value'] == '0'
    assert c['FUNDING/BORROW']['status'] == 'NOT_APPLICABLE'
    assert c['FUNDING/BORROW']['value'] is None
    assert c['UNCERTAINTY_RESERVE']['value'] == '1'
    assert r['economic_status'] == status


@pytest.mark.parametrize('dimension', ['units', 'horizon', 'horizon_interpretation', 'cost_treatment',
                                      'uncertainty_treatment', 'freshness_semantics', 'as_of_ms'])
def test_incompatible_comparison_dimensions(dimension):
    a = e.build(frozen())
    b = binding(**{dimension: 1100 if dimension == 'as_of_ms' else 'different'})
    assert e.compare(a, e.build(frozen(b=b)), 1500) == 'INCOMPARABLE'


def test_stale_receipt_refused():
    i = frozen()
    r = e.build(i)
    assert e.verify(r, i, 2000)
    assert not e.verify(r, i, 2001)
    assert e.compare(r, r, 2001) == 'INCOMPARABLE'
    old = replace(i, gross=changed(i.gross, valid_until_ms=999))
    assert result(old)['economic_status'] == 'STALE'
    assert not e.verify(e.build(old), old, 1500)


@pytest.mark.parametrize('field', ['strategy_id', 'version_id', 'spec_hash', 'horizon', 'context_json', 'as_of_ms'])
def test_changed_identity_context_or_timestamp_refuses_verification(field):
    i = frozen()
    r = e.build(i)
    value = 1100 if field == 'as_of_ms' else '{"regime":"new"}' if field == 'context_json' else 'changed'
    other = replace(i, binding=replace(i.binding, **{field: value}))
    assert not e.verify(r, other, 1500)
    assert result(other)['economic_status'] == 'INCOMPATIBLE_CONTEXT'


@pytest.mark.parametrize('field', ['gross', 'costs', 'uncertainty'])
def test_changed_or_tampered_source_refuses(field):
    i = frozen()
    r = e.build(i)
    source = getattr(i, field)
    other = replace(i, **{field: changed(source, value='999', captured_ms=901)})
    assert not e.verify(r, other, 1500)
    with pytest.raises(ValueError, match='SOURCE_INTEGRITY'):
        Source(source.source_id, source.sha256, '{}')


def test_cost_replay_not_outer_hash_alone():
    i = frozen()
    raw = json.loads(i.costs.payload_json)
    record = raw['paper_receipt_record']
    body = json.loads(record['canonical_json'])
    body['dimensions']['commission']['amount'] = 0
    from trader.engine.paper_exit_evidence import canonical as pc, digest as pd
    record.update(canonical_json=pc(body), canonical_sha256=pd(pc(body)))
    forged = replace(i, costs=Source.freeze(i.costs.source_id, raw))
    assert not e.verify(e.build(i), forged, 1500)
    with pytest.raises(ValueError, match='replay_differs'):
        e.build(forged)


def test_cost_protocol_missing_leg_remains_unavailable():
    from trader.engine import paper_cost_evidence as pc
    i = frozen()
    raw = json.loads(i.costs.payload_json)
    body = json.loads(raw['paper_receipt_record']['canonical_json'])
    # Replay the SAME existing cost protocol, removing the commission authority.
    body = pc.build(_DBReader(raw), body['binding'], {'slippage': 'TEST-ONLY-slippage', 'funding': 'TEST-ONLY-funding'})
    record = raw['paper_receipt_record']
    record.update(canonical_json=pc.canonical(body), canonical_sha256=pc.digest(pc.canonical(body)))
    costs = Source.freeze(i.costs.source_id, raw)
    uncertainty = changed(i.uncertainty, cost_source_sha256=costs.sha256)
    r = result(replace(i, costs=costs, uncertainty=uncertainty))
    assert r['components']['COMMISSION']['status'] == 'UNAVAILABLE'
    assert r['components']['EXPECTED_NET_VALUE']['status'] == 'UNAVAILABLE'


class _DBReader:
    def __init__(self, raw):
        self.raw = raw
    def execute(self, sql, params):
        rows = e._FrozenCostReader(self.raw).query(sql, params)
        class Result:
            def fetchone(self):
                return rows[0] if rows else None
        return Result()


@pytest.mark.parametrize('change', [{'costs_excluded': False}, {'evidence_period_ms': None},
                                    {'calibration_evidence': None}, {'cost_baseline': 'different'}])
def test_gross_requires_calibration_and_cost_exclusion(change):
    i = frozen()
    assert result(replace(i, gross=changed(i.gross, **change)))['components']['EXPECTED_GROSS_VALUE']['status'] == 'UNAVAILABLE'


def test_borrow_applicability_not_assumed():
    i = frozen()
    costs = changed(i.costs, borrow={})
    r = result(replace(i, costs=costs, uncertainty=changed(i.uncertainty, cost_source_sha256=costs.sha256)))
    assert r['components']['FUNDING/BORROW']['status'] == 'UNAVAILABLE'
    assert r['components']['EXPECTED_NET_VALUE']['status'] == 'UNAVAILABLE'


def test_quality_context_is_retained_never_a_multiplier():
    i = frozen()
    context = Source.freeze('quality', {'data_quality': 'UNKNOWN', 'evidence_strength': 'SUPPORTED',
                                      'strategy_reliability': None, 'limitations': ['small sample']})
    r = result(replace(i, context=(context,)))
    assert Decimal(r['components']['EXPECTED_NET_VALUE']['value']) == 2
    assert r['context_sources'][0]['sha256'] == context.sha256


def test_replay_immutable_publication_and_no_side_effects(tmp_path):
    i = frozen()
    r = e.build(i)
    assert e.build(e.from_inputs(json.loads(r.inputs_json))) == r
    assert e.from_payload(r.payload()) == r
    with pytest.raises(FrozenInstanceError):
        r.schema = 'changed'
    path = e.persist(r, tmp_path)
    assert e.persist(r, tmp_path) == path
    code = ('import json,sys; from tests.economics_fixtures import install_models; install_models(); '
            'from trader.portfolio.economics import build,from_inputs; '
            'b=json.load(open(sys.argv[1])); print(build(from_inputs(b["inputs"])).receipt_id)')
    assert subprocess.check_output([sys.executable, '-c', code, str(path)], text=True).strip() == r.receipt_id
    assert not e.verify(replace(r, result_json='{}'), i, 1500)
    path.write_text('{}')
    with pytest.raises(ValueError, match='IMMUTABLE_RECEIPT_COLLISION'):
        e.persist(r, tmp_path)
    assert result(i)['side_effects'] == 'NONE'


def test_allocator_rejects_unreceipted_scalar_and_changed_context():
    from tests.test_portfolio_allocator import candidate, inputs, result as allocated, economics
    from trader.portfolio.allocator import Evidence, Status
    a = candidate()
    original = inputs(a)
    for other in (replace(a, economics=replace(a.economics, receipt_json=None)),
                  replace(a, regime_world=Evidence(Status.UNKNOWN, (), '{"regime":"changed"}')),
                  replace(a, economics=replace(a.economics, expected_net_value='999'))):
        r = allocated(replace(original, candidates=(other,)))
        assert r['cash_candidate']['selected']
        assert 'EXPECTED_ECONOMICS_RECEIPT_REFUSED' in r['candidates'][0]['refusal_reasons']


def test_allocator_refuses_incompatible_receipt_horizons():
    from tests.test_portfolio_allocator import candidate, inputs, result as allocated
    a = candidate()
    b = candidate('b', 'venue:futures:ETHUSDT')
    econ, _ = e.to_allocator(e.build(frozen('999', binding('b', b.instrument, horizon='8h'))))
    b = replace(b, horizon='8h', economics=econ)
    r = allocated(inputs(a, b))
    assert not r['economic_order']
    assert r['cash_candidate']['selected']


def test_allocator_all_truthfully_unavailable_receipts_cash():
    from tests.test_portfolio_allocator import candidate, inputs, result as allocated
    a = candidate()
    econ, _ = e.to_allocator(e.build(e.Inputs(binding())))
    r = allocated(inputs(replace(a, economics=econ)))
    assert r['decision'] == 'NO_ALLOCATION'
    assert r['cash_candidate']['selected']
    assert not r['economic_order']
