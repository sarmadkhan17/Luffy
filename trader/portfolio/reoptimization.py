"""Pure event gate over caller-delivered frozen cuts; no polling or scheduler.

New exact opportunities, complete-book exits and discrete control transitions
are established events. Other observations are retained without auto-triggering
unless a code-owned adapter proves materiality. The production registry is empty.
The caller checkpoints the returned cursor/processed IDs; this module does not
persist or change runtime state. Reread trusted sources for current use.
"""
from dataclasses import asdict, dataclass
import json

from .allocator import (Inputs, Source, Status, allocate, canonical, digest,
                        _ordered, _references, _portfolio_context_reasons)
from .trade_intent import build as build_intents

SCHEMA = 'portfolio-reoptimization.v1'
MATERIALITY_MODELS = {}


@dataclass(frozen=True)
class EventReceipt:
    receipt_id: str
    inputs_json: str
    result_json: str

    def payload(self):
        return dict(schema=SCHEMA, receipt_id=self.receipt_id,
                    inputs=json.loads(self.inputs_json), result=json.loads(self.result_json))


def _cut(inputs, *, require_current=True):
    _references(inputs)
    frozen = _ordered(inputs)
    p, now = inputs.portfolio, inputs.as_of_ms
    if type(now) is not int or now < 0:
        raise ValueError('EVENT_CUT_INVALID')
    if (p.evidence.status != Status.ESTABLISHED or p.valid_until_ms is None
            or not p.as_of_ms <= now <= p.valid_until_ms):
        raise ValueError('EVENT_PORTFOLIO_CUT_STALE_OR_UNAVAILABLE')
    if require_current and any(_portfolio_context_reasons(c, inputs) for c in inputs.candidates):
        raise ValueError('EVENT_OPPORTUNITY_PORTFOLIO_CUT_DIFFERS')
    venue = [s for s in inputs.sources if s.source_id == 'venue_position_snapshot']
    if venue:
        from .common_factor import snapshot
        b = snapshot(venue[0], now)
        if (b['snapshot_id'], b['observed_at_ms']) != (p.snapshot_id, p.as_of_ms) or sorted(
                (v['instrument'], v['direction'], v['quantity']) for v in b['positions']) != sorted(
                (v.instrument, v.direction, v.quantity) for v in p.positions):
            raise ValueError('EVENT_VENUE_BOOK_PROJECTION_DIFFERS')
    return frozen


def _state(inputs):
    # Ignore refresh clock and archive hash churn for exact automatic triggers.
    opportunities = sorted([list(c.identity) + [c.spec_hash] for c in inputs.candidates
        if c.validation.status == Status.ESTABLISHED and c.freshness.status == Status.ESTABLISHED
        and c.valid_until_ms is not None and c.as_of_ms <= inputs.as_of_ms <= c.valid_until_ms])
    holdings = sorted([p.instrument, p.market_type, p.direction] for p in inputs.portfolio.positions)
    world = sorted((c.identity, asdict(c.regime_world)) for c in inputs.candidates)
    confidence = sorted((c.identity, asdict(c.evidence_quality), asdict(c.validation), asdict(c.probation))
                        for c in inputs.candidates)
    # Retain measurements/context. No numerical distance/material threshold.
    relationships = dict(book=asdict(inputs.relationships), candidates=sorted(
        (c.identity, asdict(c.relationships)) for c in inputs.candidates))
    if inputs.exposure_source_id:
        from .common_factor import from_allocator
        exposure = from_allocator(inputs)
        relationships['exposure'] = {k: exposure[k] for k in ('pairs', 'measured_relationships', 'duplicate_confidence')}
    inventory = next((json.loads(s.payload_json) for s in inputs.sources if s.source_id == 'current-shadow-inventory'), {})
    world_context = dict(candidates=world, scan_world=inventory.get('scan', {}).get('world_model')
                         if inventory.get('scan') else None)
    capital = [asdict(s) for s in sorted(inputs.sources, key=lambda s: s.source_id)
               if s.source_id in inputs.portfolio.source_ids]
    metric_source = next((json.loads(s.payload_json) for s in inputs.sources
                          if s.source_id == 'runtime-portfolio-evidence'), {})
    risk_state = (metric_source.get('risk_assessment') or {}).get('risk_state', 'UNKNOWN')
    contexts = sorted((json.loads(s.payload_json)['context_id'], json.loads(s.payload_json)['opportunity_id'])
                      for s in inputs.sources if s.source_id.startswith('live-context:'))
    return dict(contexts=contexts, risk_state=risk_state, opportunities=opportunities, holdings=holdings, control_state=inputs.control_state,
                risk_policy=asdict(inputs.risk_policy), world=world_context, confidence=confidence,
                lifecycle={k:{role:v.get(role,[]) for role in ('events','governor')}
                           for k,v in inventory.get('strategy_authority_inventory', {}).items()},
                relationships=relationships, capital=capital)


def evaluate(previous: Inputs | None, current: Inputs, *, processed_ids=(), materiality_sources=()):
    current_raw = _cut(current)
    previous_raw = _cut(previous) if previous is not None else None
    if previous is not None and previous.as_of_ms > current.as_of_ms:
        raise ValueError('EVENT_CLOCK_REVERSED')
    sources = tuple(materiality_sources)
    if len({s.source_id for s in sources}) != len(sources):
        raise ValueError('DUPLICATE_MATERIALITY_SOURCE')
    for s in sources:
        Source(**asdict(s))
    before = _state(previous) if previous is not None else None
    after = _state(current)
    events = []
    # The immutable cuts are identical for every event in this evaluation.
    # Hash each once, retaining exactly the old event/cursor identities.
    previous_cut_sha256 = digest(previous_raw)
    current_cut_sha256 = digest(current_raw)

    def record(kind, left, right, exact=False):
        event_binding = dict(kind=kind, before=left, after=right,
                             previous_cut_sha256=previous_cut_sha256, current_cut_sha256=current_cut_sha256,
                             as_of_ms=current.as_of_ms)
        event_binding = json.loads(canonical(event_binding))
        eid = digest(event_binding)
        authority = None
        if not exact:
            matching = [s for s in sources if json.loads(s.payload_json).get('binding') == event_binding]
            if len(matching) == 1:
                s = matching[0]
                raw = json.loads(s.payload_json)
                validator = MATERIALITY_MODELS.get((kind, raw.get('method'), raw.get('version')))
                known, expiry = raw.get('captured_ms'), raw.get('valid_until_ms')
                if (validator and raw.get('provenance') and raw.get('material') is True
                        and type(known) is int and type(expiry) is int
                        and 0 <= known <= current.as_of_ms <= expiry and validator(raw) is True):
                    authority = s.source_id
        established = exact or authority is not None
        events.append(dict(event_id=eid, binding=event_binding, kind=kind,
            materiality='EXACT_EVENT' if exact else 'ESTABLISHED' if authority else 'UNAVAILABLE',
            materiality_source_id=authority, trigger=established and eid not in processed_ids,
            reason_codes=[] if exact or authority else ['MATERIALITY_POLICY_UNAVAILABLE'],
            reason='ALREADY_PROCESSED' if eid in processed_ids else 'EXACT_EVENT' if exact else
                   'REGISTERED_MATERIAL_EVENT' if authority else 'CONTEXT_ONLY_MATERIALITY_UNAVAILABLE'))

    old_contexts = {tuple(v) for v in before['contexts']} if before else set()
    for item in after['contexts']:
        if tuple(item) not in old_contexts:
            record('NEW_OPPORTUNITY_CONTEXT', None, item, True)
    old_opps = {tuple(v) for v in before['opportunities']} if before else set()
    for item in after['opportunities']:
        if tuple(item) not in old_opps:
            record('NEW_OPPORTUNITY', None, item, True)
    if before:
        known_risk = {'ok','corrupt','unreadable','uninitialized','equity_unusable'}
        if before['risk_state'] != after['risk_state']:
            record('RISK_BASELINE_STATE_CHANGE',before['risk_state'],after['risk_state'],
                   before['risk_state'] in known_risk and after['risk_state'] in known_risk)
        new_holdings = {tuple(v) for v in after['holdings']}
        old_holdings = {tuple(v) for v in before['holdings']}
        for item in after['holdings']:
            if tuple(item) not in old_holdings:
                record('POSITION_OPEN', None, item, True)
        if before['lifecycle'] != after['lifecycle']:
            record('STRATEGY_LIFECYCLE_CHANGE', before['lifecycle'], after['lifecycle'], True)
        for item in before['holdings']:
            if tuple(item) not in new_holdings:
                record('POSITION_EXIT', item, None, True)
        if before['control_state'] != after['control_state']:
            # Established discrete ACTIVE/FROZEN/HALTED states, not guessed Risk metrics.
            known_states = {'ACTIVE', 'FROZEN', 'HALTED'}
            record('RISK_STATE_CHANGE', before['control_state'], after['control_state'],
                   before['control_state'] in known_states and after['control_state'] in known_states)
        for kind, key in [('RISK_POLICY_CONTEXT_CHANGE', 'risk_policy'),
                          ('STRATEGY_RELIABILITY_CHANGE', 'confidence'),
                          ('WORLD_STATE_CHANGE', 'world'), ('RELATIONSHIP_CHANGE', 'relationships'),
                          ('CAPITAL_CHANGE', 'capital')]:
            if before[key] != after[key]:
                record(kind, before[key], after[key])
    elif not after['opportunities']:
        record('INITIAL_CONTEXT', None, after)
    events.sort(key=lambda e: (e['kind'], e['event_id']))
    frozen = dict(previous=previous_raw, current=current_raw, processed_ids=sorted(set(processed_ids)),
                  materiality_sources=[asdict(s) for s in sorted(sources, key=lambda s: s.source_id)])
    result = dict(events=events, trigger=any(e['trigger'] for e in events),
                  next_cursor=current_cut_sha256, side_effects='NONE', polling=False)
    return EventReceipt(digest(dict(schema=SCHEMA, inputs=frozen, result=result)), canonical(frozen), canonical(result))


def reoptimize(previous, current, **kwargs):
    """The sole event consumer. No event -> no allocator invocation."""
    event = evaluate(previous, current, **kwargs)
    if not json.loads(event.result_json)['trigger']:
        return event, None, ()
    proposal = allocate(current)
    return event, proposal, build_intents(proposal, current)


def replay(receipt):
    from .allocator import inputs_from_payload
    raw = json.loads(receipt.inputs_json)
    previous = inputs_from_payload(raw['previous']) if raw['previous'] else None
    current = inputs_from_payload(raw['current'])
    again = evaluate(previous, current, processed_ids=raw['processed_ids'],
                     materiality_sources=tuple(Source(**s) for s in raw['materiality_sources']))
    if again != receipt:
        raise ValueError('REOPTIMIZATION_REPLAY_REFUSED')
    return again
