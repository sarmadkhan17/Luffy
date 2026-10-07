"""DEC-02: inspectable comparison view over the single allocator authority.

The allocator remains the only comparator. This module is a pure, read-only
derivation of its frozen Proposal (inputs + result): it decides nothing and
grants no capital, Risk, owner or order authority. Every candidate ends
PROPOSED or REJECTED with machine-readable reasons; CASH is an explicit
candidate with its own reasons. Confidence is a set of component STATES, never
a composite score: no weights are registered, so none are invented and an
unavailable component stays unavailable rather than neutral.
"""
import json

from .allocator import Proposal, allocate, canonical, digest, number

SCHEMA = 'portfolio-comparison.v1'
# Exact economic ties: the allocator's registered order is ascending
# (opportunity_id, version_id). Deterministic and independent of input order.
TIE_RULE = 'ASCENDING_OPPORTUNITY_ID_VERSION_ID'
# component -> (candidate field, whether the allocator gates on it)
COMPONENTS = (('evidence_strength', 'validation', True),
              ('strategy_reliability', 'probation', True),
              ('data_quality', 'evidence_quality', False),
              ('regime_context_fit', 'regime_world', False),
              ('execution_cost_confidence', 'costs', True))


def _components(c):
    out = {name: dict(status=c[field]['status'], source_ids=list(c[field]['source_ids']),
                      gating=gating, score=None) for name, field, gating in COMPONENTS}
    e = c['economics']
    out['economics_confidence'] = dict(status=e['evidence']['status'], source_ids=list(e['evidence']['source_ids']),
                                       gating=True, score=None, expected_net_value=e['expected_net_value'],
                                       units=e['currency'], horizon=e['horizon'])
    return out


def _analyst_evidence(inputs, c):
    """Supporting/opposing analyst evidence as frozen in the exact DEC-01 context."""
    if not c['opportunity_context_json']:
        return dict(status='NO_DECISION_CONTEXT')
    from .allocator import Source
    from .opportunity_live import receipt_from_source
    for s in inputs['sources']:
        if not s['source_id'].startswith('live-context:'):
            continue
        live = receipt_from_source(Source(**s))
        if live.context.context_id == _context_id(c):
            a = json.loads(live.payload_json).get('decision_inputs', {}).get('analysts')
            if not a:
                return dict(status='ANALYST_EVIDENCE_UNAVAILABLE')
            ev = a['evidence']
            return dict(status='AVAILABLE', supporting=sorted(x['analyst'] for x in ev['supporting']),
                        opposing=sorted(x['analyst'] for x in ev['opposing']), conflict=ev['conflict'],
                        decides_nothing=True)
    return dict(status='CONTEXT_SOURCE_MISSING')


def _context_id(c):
    from trader.cognition.opportunity_context import OpportunityContext
    return OpportunityContext.from_json(c['opportunity_context_json']).context_id


def compare(proposal: Proposal) -> dict:
    inputs, result = json.loads(proposal.inputs_json), json.loads(proposal.result_json)
    by_identity = {(c['opportunity_id'], c['version_id']): c for c in inputs['candidates']}
    order = [tuple(x) for x in result['economic_order']]
    nets = {i: number(by_identity[i]['economics']['expected_net_value']) for i in order}
    top = [i for i in order if nets[i] == nets[order[0]]] if order else []
    winners = [tuple(s['primary_candidate']) for s in result['selected']]
    tie = None
    if winners and len({(by_identity[i]['instrument'], by_identity[i]['direction']) for i in top}) > 1:
        tie = dict(rule=TIE_RULE, tied=[list(i) for i in top], winner=list(winners[0]))
    rows = []
    for r in sorted(result['candidates'], key=lambda r: tuple(r['identity'])):
        ident = tuple(r['identity'])
        c = by_identity[ident]
        if r['accepted']:
            outcome, reasons = 'PROPOSED', [result['reason']]
        else:
            outcome = 'REJECTED'
            reasons = list(r['refusal_reasons']) or ['REJECTED_NO_RECORDED_REASON']
            if tie and list(ident) in tie['tied']:
                reasons.append('EXACT_ECONOMIC_TIE_LOST_TO_REGISTERED_ORDER')
        rows.append(dict(identity=list(ident), candidate_id=r['candidate_id'], context_id=r['context_id'],
                         instrument=r['instrument'], direction=r['direction'], outcome=outcome,
                         reasons=reasons, feasibility=r['feasibility'],
                         economic_rank=(order.index(ident) + 1) if ident in order else None,
                         expected_net_value=c['economics']['expected_net_value'],
                         confidence_components=_components(c),
                         analyst_evidence=_analyst_evidence(inputs, c)))
    if winners:
        cash = dict(selected=False, reasons=['OUTRANKED_BY_POSITIVE_COMPARABLE_ECONOMICS'])
    else:
        why = ['NO_CANDIDATES'] if not rows else ['ALL_CANDIDATES_REJECTED']
        why.append(result['reason'])
        why += sorted({x for row in rows for x in row['reasons']})
        why += [b for b in result['global_blockers'] if b not in why]
        cash = dict(selected=True, reasons=why)
    body = dict(schema=SCHEMA, proposal_id=proposal.proposal_id, allocator_version=proposal.allocator_version,
                as_of_ms=result['as_of_ms'], decision=result['decision'],
                cash=dict(expression='CASH', action='NO_TRADE', **cash), candidates=rows,
                ranking=[list(i) for i in order], tie=tie, confidence_policy='COMPONENT_STATES_NO_WEIGHTS_NO_COMPOSITE',
                authority=dict(capital=False, risk_permission=False, owner_approval=False, order=False,
                               risk_final_gate_required=True),
                side_effects='NONE')
    body['comparison_id'] = digest(body)
    return json.loads(canonical(body))


def decide(inputs) -> dict:
    """Allocator is the only authority; this is its inspectable explanation."""
    return compare(allocate(inputs))
