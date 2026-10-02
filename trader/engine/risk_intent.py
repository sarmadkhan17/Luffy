"""Allocation intent evaluation ends here. No exchange or Execution capability.

RiskManager evaluates the frozen current book/policy/baseline in an isolated
in-memory journal. Its existing sizing/breaker formulas are the only authority.
An APPROVE is an auditable sizing answer, never an execution permission.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import json

from trader.core.types import ControlState, RiskError
from .risk import RiskManager, policy_from_config
from trader.portfolio.allocator import canonical, digest, number, inputs_from_payload
from trader.portfolio.trade_intent import build as build_intents, TradeIntent
from trader.portfolio.runtime_metrics import SOURCE_ID, positions, project

SCHEMA = 'portfolio-risk-decision.v1'


class Result(str, Enum):
    APPROVE = 'APPROVE'
    REFUSE = 'REFUSE'
    INCOMPLETE = 'INCOMPLETE'


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    payload_json: str

    def __post_init__(self):
        raw = json.loads(self.payload_json)
        if digest(raw) != self.decision_id or raw['schema'] != SCHEMA:
            raise ValueError('RISK_DECISION_INTEGRITY_REFUSED')
        Result(raw['result'])
        if raw['result'] == 'APPROVE' and (number(raw.get('permitted_size')) is None or number(raw['permitted_size']) <= 0):
            raise ValueError('RISK_DECISION_SIZE_UNAVAILABLE')
        if raw['execution_authority'] is not False:
            raise ValueError('RISK_DECISION_EXECUTION_AUTHORITY_REFUSED')

    def payload(self):
        return dict(risk_decision_id=self.decision_id, **json.loads(self.payload_json))


class FrozenJournal:
    """All Risk writes are confined to this private frozen-state copy."""
    def __init__(self, kv):
        self.kv = dict(kv)

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def log_control_event(self, *args, **kwargs):
        pass


def evaluate(intent, proposal, inputs):
    if intent not in build_intents(proposal, inputs):
        raise ValueError('RISK_INTENT_PROPOSAL_OR_CUT_DIFFERS')
    raw = intent.payload()
    sources = {s.source_id: s for s in inputs.sources}
    evidence = sources.get(SOURCE_ID)
    policy = sources.get('owner-risk-policy')
    venue = sources.get('venue_position_snapshot')
    frozen = json.loads(evidence.payload_json) if evidence else {}
    cfg = frozen.get('config')
    base = dict(schema=SCHEMA, trade_intent_id=intent.intent_id,
        allocation_proposal_id=proposal.proposal_id, portfolio_snapshot_id=inputs.portfolio.snapshot_id,
        portfolio_sha256=digest(asdict(inputs.portfolio)), strategy_id=raw['strategy_id'],
        version_id=raw['version_id'], spec_hash=raw['spec_hash'], instrument=raw['instrument'],
        direction=raw['direction'], requested_size=raw['requested_size'], requested_size_unit=raw['size_unit'],
        risk_policy_id=policy_from_config(cfg)['digest'] if cfg else None,
        risk_policy_version='RiskManager.effective-policy.v1',
        account_evidence_id=frozen.get('account_margin', {}).get('observation_id') if frozen.get('account_margin') else None,
        as_of_ms=inputs.as_of_ms, source_ids=sorted(sources),
        source_hashes={s.source_id:s.sha256 for s in inputs.sources},
        permitted_size=None, permitted_size_unit=raw['size_unit'], risk_sizing=None,
        execution_authority=False, execution_routed=False, side_effects='PRIVATE_RISK_STATE_ONLY',
        input_binding=dict(intent=asdict(intent), proposal=proposal.payload(), inputs=json.loads(proposal.inputs_json)))

    def finish(result, reasons, **extra):
        value = dict(base, result=result, reason_codes=list(reasons), **extra)
        return RiskDecision(digest(value), canonical(value))

    if raw['requested_action'] != 'OPEN':
        return finish('REFUSE', raw['refusal_reasons'] or ['NO_ACTIONABLE_OPEN_INTENT'])
    if raw['status'] != 'COMPLETE' or number(raw['requested_size']) is None or number(raw['requested_size']) <= 0:
        return finish('INCOMPLETE', ['REQUESTED_SIZE_UNAVAILABLE'])
    if raw['size_unit'] != 'base_quantity':
        return finish('INCOMPLETE', ['RISK_SIZE_UNIT_UNSUPPORTED'])
    if not all((evidence, policy, venue)):
        return finish('INCOMPLETE', ['CURRENT_RISK_ACCOUNT_PORTFOLIO_EVIDENCE_UNAVAILABLE'])
    try:
        metric = project(evidence, venue, policy, inputs.as_of_ms)
        if metric['risk_heat_pct']['status'] != 'ESTABLISHED':
            return finish('INCOMPLETE', [metric['risk_heat_pct']['reason']])
        base['account_evidence_id'] = metric['account_equity']['evidence_id']
        ps = positions(frozen['journal_positions'], json.loads(venue.payload_json))
        context = [v for v in frozen['entry_contexts'] if
            (v.get('opportunity_id'), v.get('version_id'), v.get('spec_hash'), v.get('instrument'), v.get('direction')) ==
            (raw['opportunity_id'], raw['version_id'], raw['spec_hash'], raw['instrument'], raw['direction'])]
        if len(context) != 1:
            return finish('INCOMPLETE', ['RISK_ENTRY_GEOMETRY_UNAVAILABLE'])
        context = context[0]
        if (context.get('portfolio_snapshot_id') != inputs.portfolio.snapshot_id
                or context.get('as_of_ms') != inputs.as_of_ms or not context.get('source_ids')
                or any(s not in sources for s in context['source_ids'])
                or any(number(str(context.get(k))) is None or float(context[k]) <= 0
                       for k in ('price','atr','side_risk_frac'))):
            return finish('INCOMPLETE', ['RISK_ENTRY_GEOMETRY_UNAVAILABLE'])
        if type(frozen['closed_count']) is not int or frozen['closed_count'] < 0:
            return finish('INCOMPLETE', ['RISK_CLOSED_COUNT_UNAVAILABLE'])
        assessment_state = (frozen.get('risk_assessment') or {}).get('risk_state')
        if assessment_state is not None and assessment_state != 'ok':
            return finish('REFUSE', [f'risk_state={assessment_state}: entries blocked'])
        manager = RiskManager(cfg, FrozenJournal(frozen['risk_kv']))
        if manager.baseline_status != 'ok':
            return finish('INCOMPLETE', ['risk_baseline_' + manager.baseline_status])
        # Preserve the running RiskManager's recorded memory baseline as well
        # as the durable baseline. Its existing update_equity chooses the
        # conservative peak; no new drawdown or breaker formula lives here.
        baseline = (frozen.get('risk_assessment') or {}).get('baseline')
        if baseline:
            peak = number(str(baseline.get('peak_equity')))
            if peak is None or peak <= 0:
                return finish('INCOMPLETE', ['CURRENT_RISK_MEMORY_BASELINE_UNAVAILABLE'])
            manager._peak_equity = float(peak)
            manager._day_start_equity = baseline.get('day_start_equity')
            manager._day_key = baseline.get('day_key')
        symbol = raw['instrument'].split(':')[-1][:-4] + '/USDT'
        answer = manager.check_entry(ControlState(inputs.control_state), symbol,
            float(context['price']), float(context['atr']), float(context['side_risk_frac']), ps,
            float(metric['account_equity']['value']), frozen['closed_count'],
            raw['market_type'], as_of=datetime.fromtimestamp(inputs.as_of_ms/1000, timezone.utc))
    except RiskError as exc:
        return finish('REFUSE', ['RISK_HALT'], risk_reason=str(exc))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return finish('INCOMPLETE', [str(exc)])
    if not answer.ok:
        return finish('REFUSE', [answer.reason], risk_reason=answer.reason, risk_sizing=asdict(answer))
    permitted = min(number(raw['requested_size']), number(str(answer.amount)))
    if permitted is None or permitted <= 0:
        return finish('INCOMPLETE', ['RISK_PERMITTED_SIZE_UNAVAILABLE'], risk_sizing=asdict(answer))
    return finish('APPROVE', ['RISK_APPROVED'], permitted_size=str(permitted),
                  risk_sizing=asdict(answer), risk_reason=answer.reason)


def replay(decision):
    from trader.portfolio.allocator import Proposal
    p = json.loads(decision.payload_json)['input_binding']
    proposal = p['proposal']
    result = evaluate(TradeIntent(**p['intent']),
        Proposal(proposal['proposal_id'], canonical(proposal['inputs']), canonical(proposal['result']), proposal['allocator_version']),
        inputs_from_payload(p['inputs']))
    if result != decision:
        raise ValueError('RISK_DECISION_REPLAY_REFUSED')
    return result
