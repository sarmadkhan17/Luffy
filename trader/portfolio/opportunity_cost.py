"""Proposal-only replacement comparison. No production switching model exists.

Registered adapters must prove calibrated switching costs, portfolio impact,
capital constraint, and quantity/capital scope against the exact binding. A
payload claiming ESTABLISHED is never authority. Hashes only prove integrity;
the trusted reader must reread inputs before any consumer uses this result.
"""
from dataclasses import asdict
from decimal import localcontext
import json

from .allocator import (Source, Status, canonical, digest, number,
                        _economic_reasons, _holding_economics_current,
                        _portfolio_context_reasons)

SWITCH_MODELS = {}
SCHEMA = 'portfolio-switch-comparison.v1'


def binding(inputs, candidate, holding):
    return dict(as_of_ms=inputs.as_of_ms, portfolio_sha256=digest(asdict(inputs.portfolio)),
                candidate_sha256=digest(asdict(candidate)), holding_sha256=digest(asdict(holding)),
                exposure_source_id=inputs.exposure_source_id,
                risk_policy=asdict(inputs.risk_policy), control_state=inputs.control_state,
                comparison_key=candidate.economics.key())


def compare(inputs, candidate, holding, feasibility):
    """Positive net benefit can recommend only; it cannot release capital/size."""
    c, h, now = candidate, holding, inputs.as_of_ms
    bound = json.loads(canonical(binding(inputs, c, h)))
    result = dict(schema=SCHEMA, candidate=list(c.identity), holding=h.instrument,
                  binding=bound, status='SWITCH_ECONOMICS_UNAVAILABLE',
                  switching='SWITCH_ECONOMICS_UNAVAILABLE', action='KEEP_EXISTING',
                  recommendation='NO_ACTION', incremental_net_benefit=None,
                  source_ids=[], reason='COMPARABLE_CURRENT_ECONOMICS_UNAVAILABLE',
                  risk_final_gate_required=True, side_effects='NONE')
    e = h.economics
    if (not e or _economic_reasons(c, inputs) or not _holding_economics_current(e, inputs)
            or c.economics.key() != e.key()):
        return result
    # A holding receipt for a different asset/direction is never incumbent EV.
    from .economics import from_inputs, from_payload
    hb = from_inputs(json.loads(from_payload(json.loads(e.receipt_json)).inputs_json)).binding
    if (hb.instrument, hb.market_type, hb.direction) != (h.instrument, h.market_type, h.direction):
        return result
    cv, hv = number(c.economics.expected_net_value), number(e.expected_net_value)
    if cv is None or hv is None:
        return result
    if cv > hv:
        result['observation'] = 'BETTER_OPPORTUNITY_OBSERVED'
    result['reason'] = 'CALIBRATED_SWITCH_COST_AND_PORTFOLIO_IMPACT_UNAVAILABLE'
    matched = []
    for source in inputs.sources:
        raw = json.loads(source.payload_json)
        if isinstance(raw, dict) and raw.get('schema') == SCHEMA and raw.get('binding') == bound:
            Source(**asdict(source))
            matched.append((source, raw))
    if len(matched) != 1:
        return result
    source, raw = matched[0]
    result['source_ids'] = [source.source_id]
    validator = SWITCH_MODELS.get((raw.get('method'), raw.get('version')))
    captured, expiry = raw.get('captured_ms'), raw.get('valid_until_ms')
    cost, impact = number(raw.get('switching_cost')), number(raw.get('portfolio_impact'))
    if (validator is None or not raw.get('provenance') or not isinstance(raw.get('limitations'), list)
            or raw.get('units') != c.economics.currency or raw.get('capital_constrained') is not True
            or type(captured) is not int or type(expiry) is not int or not 0 <= captured <= now <= expiry
            or cost is None or cost < 0 or impact is None
            or raw.get('requested_action') not in ('REDUCE', 'CLOSE')
            or any(len(str(v)) > 80 or abs(v.adjusted()) > 80 for v in (cv, hv, cost, impact))
            or validator(raw) is not True):
        return result
    # Exact, bounded decimal arithmetic; precision does not depend on process state.
    with localcontext() as ctx:
        ctx.prec = 400
        net = cv - hv - cost + impact
    result.update(switching='ESTABLISHED', incremental_net_benefit=str(net),
                  units=c.economics.currency, status='NO_POSITIVE_INCREMENTAL_BENEFIT',
                  reason='SWITCH_COST_AND_PORTFOLIO_IMPACT_INCLUDED')
    p = inputs.portfolio
    permitted = (p.evidence.status == Status.ESTABLISHED and p.valid_until_ms is not None
                 and p.as_of_ms <= now <= p.valid_until_ms and inputs.control_state == 'ACTIVE'
                 and inputs.risk_policy.status == Status.ESTABLISHED
                 and not _portfolio_context_reasons(c, inputs)
                 and feasibility['feasibility'] == 'COMPARABLE'
                 and feasibility['interaction'] == 'NEW_POSITION'
                 and h.approved_plan_source_id is not None)
    if net > 0:
        result['status'] = 'POSITIVE_INCREMENTAL_BENEFIT' if permitted else 'SWITCH_RECOMMENDATION_REFUSED'
        if permitted:
            result['recommendation'] = raw['requested_action']
    return result
