"""Offline reconciliation proof of the exact normal empty-registry receipt."""
import json
from pathlib import Path
from tests.test_dec01_opportunity_context import normal_checkpoint, packet
from tests.test_stage6_normal_sources import publications, NOW, SYMBOL
from tests.authority_factory_fixtures import cfg
from trader.portfolio import candidate_bridge as B, economics as E, opportunity_live as L
from trader.portfolio.allocator import Source, digest


def test_exact_production_unavailable_receipt_and_reload(publications, tmp_path, monkeypatch):
    for registry in (E.GROSS_MODELS, E.RESERVE_MODELS, E.COST_SCOPE_MODELS):
        registry.clear()  # fixture-only models removed; production registries are empty
    captured = []
    original = B.build
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append((args[1], result[1]))
        return result
    monkeypatch.setattr(B, 'build', observe)
    votes = [('structure', packet('structure', .8, SYMBOL, NOW)),
             ('momentum', packet('momentum', -.8, SYMBOL, NOW))]
    inputs, detail = normal_checkpoint(publications, tmp_path, votes=votes, required=('structure',))
    assert len(captured) == 1 and len(inputs.candidates) == 0 and detail['candidate_count'] == 0
    core, economics = captured[0]
    before = core.payload_json
    payload = detail['blocked_decisions'][0]
    decision = L.DecisionReceipt(digest(payload), json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False))
    assert payload['economics_receipt_id'] == economics.receipt_id
    assert payload['live_receipt_id'] == core.receipt_id
    assert payload['economics_status'] == 'UNAVAILABLE'
    assert payload['status'] == 'BLOCKED' and payload['block_reason'] == 'REQUIRED_COST_EVIDENCE_UNAVAILABLE'
    assert payload['authority'] == 'NONE'
    assert all(c['value'] is None for c in payload['cost_components'].values())
    assert all(c['value'] is None for c in json.loads(economics.result_json)['components'].values())
    assert L.verify_decision(decision, core, economics, payload['now_ms']) == decision
    core_path = L.persist(core, tmp_path / 'reload')
    decision_path = tmp_path / 'reload' / (decision.receipt_id + '.json')
    decision_path.write_text(decision.payload_json)
    economics_path = tmp_path / 'reload' / 'economics.json'
    economics_path.write_text(json.dumps(dict(receipt_id=economics.receipt_id, inputs=json.loads(economics.inputs_json),
                                             result=json.loads(economics.result_json), schema=economics.schema)))
    reloaded_core = L.load(core_path)
    reloaded_economics = E.from_payload(json.loads(economics_path.read_text()))
    reloaded_decision = L.DecisionReceipt(decision_path.stem, decision_path.read_text())
    assert L.verify_decision(reloaded_decision, reloaded_core, reloaded_economics, payload['now_ms']) == decision
    assert core.payload_json == before == reloaded_core.payload_json
    proof = dict(decision_receipt_id=decision.receipt_id, core_receipt_id=core.receipt_id,
                 economics_receipt_id=economics.receipt_id, decision=payload,
                 zero_allocator_candidates=True, exact_real_receipt_bound=True,
                 reload_identical=True, core_unchanged=True, production_registries_empty=True,
                 supporting_and_opposing= json.loads(core.payload_json)['decision_inputs']['analysts']['evidence'])
    (Path(__file__).parent / 'normal-receipt-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
