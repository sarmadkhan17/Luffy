"""Detached AllocationProposal -> typed exposure/action for a future Risk consumer.

No Risk/Execution call or approval occurs here. COMPLETE means the proposed
contract is complete, never permission to trade. Reduction/replacement context
cannot silently become an exit or release capital. Existing approved plans stay
in force. Current inputs must come from trusted readers, not embedded artifacts.
"""
from dataclasses import asdict, dataclass
from enum import Enum
from decimal import Decimal
import json

from .allocator import Proposal, Inputs, canonical, digest, number, verify, _immutable
from trader.core.instrument_registry import is_canonical_instrument_id

SCHEMA = 'portfolio-trade-intent.v1'


class Action(str, Enum):
    OPEN = 'OPEN'
    KEEP = 'KEEP'
    NO_ACTION = 'NO_ACTION'


class IntentStatus(str, Enum):
    COMPLETE = 'COMPLETE'
    INCOMPLETE = 'INCOMPLETE'
    REFUSED = 'REFUSED'


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    payload_json: str

    def __post_init__(self):
        _immutable(self)
        raw = json.loads(self.payload_json)
        if digest(raw) != self.intent_id or raw.get('schema') != SCHEMA:
            raise ValueError('TRADE_INTENT_INTEGRITY_REFUSED')
        try:
            action, status = Action(raw['requested_action']), IntentStatus(raw['status'])
            identities = ('allocation_proposal_id', 'opportunity_id', 'strategy_id', 'version_id',
                          'spec_hash', 'portfolio_snapshot_id', 'portfolio_sha256')
            if (any(not isinstance(raw[k], str) or not raw[k] for k in identities)
                    or not is_canonical_instrument_id(raw['instrument'])
                    or raw['direction'] not in ('LONG', 'SHORT', 'FLAT')
                    or raw['market_type'] != raw['instrument'].split(':')[1]
                    or type(raw['as_of_ms']) is not int or raw['as_of_ms'] < 0
                    or raw['risk_final_authority'] is not True or raw['execution_routed'] is not False
                    or raw['risk_approval'] != 'NOT_REQUESTED' or raw['side_effects'] != 'NONE'
                    or not isinstance(raw['provenance']['source_ids'], list)):
                raise ValueError('TRADE_INTENT_BINDING_INVALID')
            if action == Action.OPEN and status == IntentStatus.COMPLETE:
                size = number(raw['requested_size'])
                if size is None or size <= 0 or not raw['size_unit'] or not raw['context_id']:
                    raise ValueError('TRADE_INTENT_OPEN_INCOMPLETE')
            if action != Action.OPEN and (raw['requested_size'] is not None or raw['size_unit'] is not None):
                raise ValueError('TRADE_INTENT_UNSUPPORTED_RESIZE')
        except (KeyError, TypeError) as exc:
            raise ValueError('TRADE_INTENT_FIELDS_MISSING') from exc

    @property
    def requested_action(self) -> Action:
        return Action(json.loads(self.payload_json)['requested_action'])

    @property
    def status(self) -> IntentStatus:
        return IntentStatus(json.loads(self.payload_json)['status'])

    @property
    def requested_size(self) -> Decimal | None:
        return number(json.loads(self.payload_json)['requested_size'])

    @property
    def allocation_proposal_id(self) -> str:
        return json.loads(self.payload_json)['allocation_proposal_id']

    def payload(self):
        return dict(intent_id=self.intent_id, **json.loads(self.payload_json))


def build(proposal: Proposal, current_inputs: Inputs) -> tuple[TradeIntent, ...]:
    if not verify(proposal, current_inputs):
        raise ValueError('CURRENT_ALLOCATION_PROPOSAL_REFUSED')
    result = json.loads(proposal.result_json)
    candidates = {c.identity: c for c in current_inputs.candidates}
    selected = {tuple(s['primary_candidate']): s for s in result['selected']}
    intents = []
    for row in result['candidates']:
        c = candidates[tuple(row['identity'])]
        selection = selected.get(c.identity)
        action = (Action.OPEN if selection else Action.KEEP if row['interaction'] == 'SUPPORTS_EXISTING'
                  else Action.NO_ACTION)
        reasons = list(row['refusal_reasons'])
        size, unit = (selection['proposed_size'], selection['size_unit']) if selection else (None, None)
        missing = []
        if action == Action.OPEN:
            if number(size) is None or number(size) <= 0 or not unit:
                missing.append('REQUESTED_SIZE_UNAVAILABLE')
            if not row['context_id']:
                missing.append('OPPORTUNITY_CONTEXT_UNAVAILABLE')
        # Exact same whole book as the verified proposal, not a second snapshot.
        raw = dict(schema=SCHEMA, allocation_proposal_id=proposal.proposal_id,
            opportunity_id=c.opportunity_id, context_id=row['context_id'], candidate_id=row['candidate_id'],
            strategy_id=c.strategy_id, version_id=c.version_id, spec_hash=c.spec_hash,
            instrument=c.instrument, market_type=c.market_type, direction=c.direction,
            requested_action=action.value, requested_size=size, size_unit=unit,
            existing_position_interaction=row['interaction'], as_of_ms=current_inputs.as_of_ms,
            portfolio_snapshot_id=current_inputs.portfolio.snapshot_id,
            portfolio_sha256=digest(asdict(current_inputs.portfolio)),
            status='INCOMPLETE' if missing else 'REFUSED' if action == Action.NO_ACTION and reasons else 'COMPLETE',
            reason=result['reason'], refusal_reasons=reasons + missing,
            economic_evidence=asdict(c.economics),
            replacement_context=[v for v in result['opportunity_cost'] if v['candidate'] == list(c.identity)],
            provenance=dict(source_ids=sorted(set(c.source_ids + current_inputs.portfolio.source_ids)),
                            proposal_inputs_sha256=digest(json.loads(proposal.inputs_json))),
            boundary='PROPOSED_TO_RISK_ONLY', risk_approval='NOT_REQUESTED',
            risk_final_authority=True, execution_routed=False, side_effects='NONE')
        typed = TradeIntent(digest(raw), canonical(raw))
        capabilities = []
        for source in current_inputs.sources:
            value = json.loads(source.payload_json)
            if isinstance(value, dict) and value.get('schema') == 'entry-capability.v1' and value.get('instrument_id') == c.instrument:
                capabilities.append(value)
        if len(capabilities) == 1:
            typed = bind_capability(typed, capabilities[0])
        elif len(capabilities) > 1:
            raise ValueError('TRADE_INTENT_CAPABILITY_AMBIGUOUS')
        intents.append(typed)
    return tuple(intents)


def verify_intents(intents, proposal, current_inputs):
    try:
        return tuple(intents) == build(proposal, current_inputs)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def bind_capability(intent: TradeIntent, capability: dict) -> TradeIntent:
    """Freeze the exact existing registry receipt for the final Risk consumer.

    This adds provenance, never approval. Risk rereads the trusted capability
    at authorization and execution; hashes alone cannot confer permission.
    """
    raw = json.loads(intent.payload_json)
    from trader.engine.entry_authority import digest as capability_digest
    if (capability.get('instrument_id') != raw['instrument']
            or capability.get('receipt_id') != capability_digest({k:v for k,v in capability.items() if k!='receipt_id'})):
        raise ValueError('TRADE_INTENT_CAPABILITY_MISMATCH')
    if raw.get('capability_receipt_id') and raw['capability_receipt_id'] != capability['receipt_id']:
        raise ValueError('TRADE_INTENT_CAPABILITY_REBIND_REFUSED')
    raw.update(capability_receipt_id=capability['receipt_id'], capability=capability)
    return TradeIntent(digest(raw), canonical(raw))
