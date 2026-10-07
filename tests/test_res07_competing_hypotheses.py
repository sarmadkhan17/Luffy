"""RES-07 offline proposal contract proof; no market edge is asserted."""
from dataclasses import asdict, FrozenInstanceError, replace
import json
import sqlite3
import subprocess
import sys

import pytest

from trader.cognition import predictive as P
from trader.observability import investigation_research as R
from trader.research import predictive_bridge as B, predictive_experiment as E
from tests.test_predictive_research_bridge import population, consume, saved, NumericalFixture
from tests.test_investigation_state_feedback import paths
from tests.test_investigation_research_family import measured


@pytest.fixture
def proposal_source(paths):
    _, bank, _ = paths
    iid, end = measured(paths, "same_direction")
    assert R.run(bank, iid, recorded_at_ms=end+2)["status"] == "OK"
    chain = R.chain(bank, iid)
    return P.source_from_chain(chain, chain["runs"][-1], end+2)


def test_normal_chain_retains_genuine_competitors_and_exact_restart(population):
    source, bank, candles, created, later, _ = population
    original = source.read_bytes()
    consume(population, NumericalFixture(), created, submit=False)
    records = saved(bank, 'bridge_hypotheses')
    by = {r['hypothesis']['transformation']: P.hypothesis_from_dict(r['hypothesis']) for r in records}
    benefit = by['volume_breakout_context.v1']
    null = by['volume_breakout_no_benefit.v1']
    a, b = [json.loads(h.proposal_contract_json) for h in (benefit, null)]
    assert benefit.hypothesis_id != null.hypothesis_id
    assert benefit.predictor_definition == null.predictor_definition
    assert benefit.target_definition == null.target_definition
    assert benefit.asset_scope == null.asset_scope and benefit.horizon == null.horizon
    assert a['competition_group'] == b['competition_group']
    assert a['competing_transformation'] == null.transformation
    assert b['competing_transformation'] == benefit.transformation
    assert a['test']['metric'] == b['test']['metric']
    assert a['test']['baseline'] == b['test']['baseline']
    assert (a['test']['comparator'], b['test']['comparator']) == ('> 0', '<= 0')
    assert E.translate(null, candles, {}, later)['status'] == 'UNSUPPORTED_EXPERIMENT_SHAPE'
    assert all(json.loads(h.proposal_contract_json)['predictive_edge_status'] == 'UNTESTED'
               for h in by.values())
    consume(population, NumericalFixture(), later, submit=False)
    assert saved(bank, 'bridge_hypotheses') == records
    assert source.read_bytes() == original
    script = ('import sys; from dataclasses import asdict; '
              'from trader.cognition import predictive as P; '
              'from trader.research import predictive_bridge as B; '
              'db=B.store(sys.argv[1]); '
              'print(P.canonical([asdict(P.hypothesis_from_dict(r["hypothesis"])) '
              'for r in B.records(db,"bridge_hypotheses")]))')
    replayed = json.loads(subprocess.check_output([sys.executable, '-c', script, str(bank)], text=True))
    assert replayed == [r['hypothesis'] for r in records]
    with B.store(bank) as db:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute('DELETE FROM bridge_hypotheses')


def test_explicit_evidence_lineage_scope_and_unknown_cause(proposal_source):
    source = proposal_source
    retained = json.loads(source.retained_chain_json)
    state = json.loads(retained['evidence']['frozen']['case_payload'])['state']
    for transformation in P.TRANSFORMS:
        h = P.propose(source, transformation, source.available_ms)
        P.validate(h)
        c = json.loads(h.proposal_contract_json)
        assert c['authority'] == 'PROPOSAL_ONLY'
        assert c['causal_status'] == 'UNKNOWN'
        assert c['predictive_edge_status'] == 'UNTESTED'
        assert c['priority_authority'] == 'SCHEDULING_ONLY'
        assert c['support']['role'] == 'DESCRIPTIVE_SOURCE_ONLY'
        assert c['support']['evidence_ids'] == list(source.registration_evidence)
        assert c['support']['classification'] == source.classification
        assert c['opposition']['reasons'] == state['contradictions']
        assert c['support']['measured_paths'] == [p for p in retained['result']['paths'] if p['status'] == 'SUPPORTED']
        assert c['opposition']['measured_refuted_paths'] == [p for p in retained['result']['paths'] if p['status'] == 'REFUTED']
        assert c['unavailable_causality'] == retained['result']['unavailable']
        assert c['observables'] == list(h.predictor_definition)
        assert h.source_research_ids == (source.result_id, source.investigation_id)
        assert h.source_hash == source.provenance_hash and h.bank_id == source.bank_id
        assert h.asset_scope and h.regime_context_scope == source.context_json
        assert c['test']['metric'] and c['test']['baseline'] and c['test']['admission']
        with pytest.raises(FrozenInstanceError):
            h.proposal_contract_json = '{}'


@pytest.mark.parametrize('field,value', [
    ('authority', 'VALIDATED_EDGE'), ('causal_status', 'ESTABLISHED'),
    ('predictive_edge_status', 'SUPPORTED'), ('priority_authority', 'ADMISSION'),
    ('opposition', {}), ('test', {'metric': 'narrative proves edge'}),
])
def test_even_rehashed_narrative_priority_and_evidence_laundering_refused(proposal_source, field, value):
    source = proposal_source
    h = P.propose(source, 'volume_breakout_context.v1', source.available_ms)
    raw = asdict(h)
    contract = json.loads(raw['proposal_contract_json'])
    contract[field] = value
    raw['proposal_contract_json'] = P.canonical(contract)
    raw['hypothesis_id'] = raw['provenance_hash'] = P.digest({
        k: v for k, v in raw.items() if k not in ('hypothesis_id', 'provenance_hash', 'stage', 'schema')})
    with pytest.raises(ValueError, match='hypothesis_template_mismatch'):
        P.hypothesis_from_dict(raw)


def test_legacy_hypothesis_identity_and_payload_are_preserved(proposal_source):
    source = proposal_source
    for transformation in ('volume_breakout_context.v1', 'exact_volume_path.v1'):
        h = P._propose(source, transformation, source.available_ms, P.LEGACY_SCHEMA)
        raw = json.loads(P.canonical(asdict(h)))
        assert 'proposal_contract_json' not in raw
        replayed = P.hypothesis_from_dict(raw)
        assert replayed.hypothesis_id == h.hypothesis_id
        assert replayed.provenance_hash == h.provenance_hash
        assert P.canonical(asdict(replayed)) == P.canonical(raw)
        assert P.propose(source, transformation, source.available_ms).hypothesis_id != h.hypothesis_id


def test_supported_source_projection_required_at_generation(proposal_source):
    source = proposal_source
    forged = replace(source, classification='SUPPORTED', context_json='{"causal_edge":true}')
    with pytest.raises(ValueError, match='source_integrity'):
        P.propose(forged, 'volume_breakout_context.v1', source.available_ms)
    fields = asdict(forged)
    fields.pop('provenance_hash')
    forged = replace(forged, provenance_hash=P.digest(fields))
    with pytest.raises(ValueError, match='source_projection_integrity'):
        P.propose(forged, 'volume_breakout_context.v1', source.available_ms)
