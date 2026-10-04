"""R1: deterministic, proposal-only whole-book capital priority.

Callers supply frozen authoritative evidence, never scores. This module has
no venue, execution, Journal, activation, control, or LLM dependency. R1
selects at most one NEW_POSITION so independent bounds are never silently
summed across proposals. Cash is the no-deployment baseline, not an invented
cash yield. Evidence hashes give integrity, not authenticity: a trusted
reader must compare source records at verification time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from collections import OrderedDict
import hashlib
import json
import os
import threading
from pathlib import Path

from ..core.instrument_registry import is_canonical_instrument_id

VERSION = 'LUFFY-PORTFOLIO-ALLOCATOR-R2'
UNAVAILABLE = 'UNAVAILABLE'


def _immutable(value):
    if value is None or isinstance(value, (str, int, float, Enum)):
        return
    if isinstance(value, tuple):
        for item in value:
            _immutable(item)
        return
    if is_dataclass(value) and value.__dataclass_params__.frozen:
        for field in fields(value):
            _immutable(getattr(value, field.name))
        return
    raise ValueError('MUTABLE_CONTRACT_FIELD_REFUSED')


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


# Pure verification results scoped to exact immutable serialized content.
# A changed payload cannot reuse a digest; identities and supplied hashes are
# still checked by every Source. Keep retained text bounded across cycles.
_SOURCE_DIGESTS = OrderedDict()
_SOURCE_DIGEST_LOCK = threading.Lock()
_SOURCE_DIGEST_BYTES = 0
_SOURCE_DIGEST_LIMIT = 64 * 1024**2


def _remember_source(text, sha, size):
    global _SOURCE_DIGEST_BYTES
    with _SOURCE_DIGEST_LOCK:
        if text not in _SOURCE_DIGESTS:
            _SOURCE_DIGESTS[text] = (sha, size)
            _SOURCE_DIGEST_BYTES += size
        _SOURCE_DIGESTS.move_to_end(text)
        while _SOURCE_DIGEST_BYTES > _SOURCE_DIGEST_LIMIT or len(_SOURCE_DIGESTS) > 64:
            _, (_, cost) = _SOURCE_DIGESTS.popitem(last=False)
            _SOURCE_DIGEST_BYTES -= cost
    return sha


def _source_digest(text):
    with _SOURCE_DIGEST_LOCK:
        hit = _SOURCE_DIGESTS.get(text)
        if hit is not None:
            _SOURCE_DIGESTS.move_to_end(text)
            return hit[0]
    return _remember_source(text, digest(json.loads(text)), len(text.encode()))


class Status(str, Enum):
    ESTABLISHED = 'ESTABLISHED'
    UNKNOWN = 'UNKNOWN'
    UNAVAILABLE = 'UNAVAILABLE'
    INVALID = 'INVALID'
    INCOMPATIBLE = 'INCOMPATIBLE'


class Feasibility(str, Enum):
    COMPARABLE = 'COMPARABLE'
    INCOMPLETE = 'INCOMPLETE'
    CONFLICTED = 'CONFLICTED'
    BLOCKED = 'BLOCKED'


@dataclass(frozen=True)
class Source:
    source_id: str
    sha256: str
    payload_json: str

    def __post_init__(self):
        _immutable(self)
        if not self.source_id or _source_digest(self.payload_json) != self.sha256:
            raise ValueError('SOURCE_INTEGRITY_REFUSED')

    @classmethod
    def freeze(cls, source_id, payload):
        text = canonical(payload)
        data = text.encode()
        # Canonical serialization has already verified JSON validity. Hash
        # those exact bytes once; the regular constructor verifies this hash
        # against the identical immutable text, including on later replays.
        sha = _remember_source(text, hashlib.sha256(data).hexdigest(), len(data))
        return cls(source_id, sha, text)


@dataclass(frozen=True)
class Evidence:
    status: Status
    source_ids: tuple[str, ...]
    detail_json: str = '{}'

    def __post_init__(self):
        object.__setattr__(self, 'status', Status(self.status))
        object.__setattr__(self, 'source_ids', tuple(self.source_ids))
        _immutable(self)
        json.loads(self.detail_json)
        if self.status == Status.ESTABLISHED and not self.source_ids:
            raise ValueError('ESTABLISHED_EVIDENCE_REQUIRES_PROVENANCE')


@dataclass(frozen=True)
class Economics:
    evidence: Evidence
    expected_net_value: str | None
    # All dimensions must match. A return per capital over a horizon and
    # a currency P&L at a particular size are not interchangeable.
    currency: str | None
    horizon: str | None
    quantity_basis: str | None
    capital_basis: str | None
    estimator_contract: str | None
    cost_basis: str | None
    receipt_json: str | None = None

    def __post_init__(self):
        _immutable(self)

    def key(self):
        return (self.currency, self.horizon, self.quantity_basis,
                self.capital_basis, self.estimator_contract, self.cost_basis,
                self._receipt_key())


    def _receipt_key(self):
        if not self.receipt_json:
            return None
        from .economics import comparison_key, from_payload
        try:
            return comparison_key(from_payload(json.loads(self.receipt_json)))
        except (ValueError, KeyError, TypeError):
            return None


@dataclass(frozen=True)
class Bound:
    authority: str
    evidence: Evidence
    maximum: str | None
    unit: str | None

    def __post_init__(self):
        _immutable(self)


@dataclass(frozen=True)
class Candidate:
    opportunity_id: str
    instrument: str
    market_type: str
    direction: str
    strategy_id: str
    version_id: str
    spec_hash: str
    as_of_ms: int
    horizon: str | None
    validation: Evidence
    probation: Evidence
    economics: Economics
    costs: Evidence
    capacity: Evidence
    account_instrument: Evidence
    risk_compatibility: Evidence
    evidence_quality: Evidence
    regime_world: Evidence
    existing_position_interaction: Evidence
    relationships: Evidence
    # Reader-authorized expiry; no allocator-invented TTL.
    freshness: Evidence
    valid_until_ms: int | None
    bounds: tuple[Bound, ...]
    source_ids: tuple[str, ...]
    opportunity_context_json: str | None = None

    @property
    def context_id(self):
        if self.opportunity_context_json is None:
            return None
        from trader.cognition.opportunity_context import OpportunityContext
        return OpportunityContext.from_json(self.opportunity_context_json).context_id

    def __post_init__(self):
        object.__setattr__(self, 'bounds', tuple(self.bounds))
        object.__setattr__(self, 'source_ids', tuple(self.source_ids))
        _immutable(self)
        if not all((self.opportunity_id, self.instrument, self.market_type,
                    self.strategy_id, self.version_id, self.spec_hash)):
            raise ValueError('EXACT_IDENTITY_REQUIRED')
        if not is_canonical_instrument_id(self.instrument) or self.instrument.split(':')[1] != self.market_type:
            raise ValueError('CANONICAL_INSTRUMENT_REQUIRED')
        if self.direction not in ('LONG', 'SHORT', 'FLAT'):
            raise ValueError('DIRECTION_UNSUPPORTED')
        if type(self.as_of_ms) is not int or self.as_of_ms < 0:
            raise ValueError('AS_OF_INVALID')

    @property
    def candidate_id(self):
        receipt = json.loads(self.economics.receipt_json) if self.economics.receipt_json else {}
        return 'candidate:' + digest(dict(opportunity_id=self.opportunity_id,
            context_id=self.context_id, strategy_id=self.strategy_id,
            version_id=self.version_id, spec_hash=self.spec_hash,
            economics_receipt_id=receipt.get('receipt_id'), as_of_ms=self.as_of_ms))

    @property
    def identity(self):
        return (self.opportunity_id, self.version_id)


@dataclass(frozen=True)
class Position:
    instrument: str
    market_type: str
    direction: str
    quantity: str
    approved_plan_source_id: str | None
    economics: Economics | None = None

    def __post_init__(self):
        _immutable(self)
        if not is_canonical_instrument_id(self.instrument) or self.instrument.split(':')[1] != self.market_type:
            raise ValueError('CANONICAL_INSTRUMENT_REQUIRED')


@dataclass(frozen=True)
class Portfolio:
    snapshot_id: str
    as_of_ms: int
    valid_until_ms: int | None
    evidence: Evidence
    positions: tuple[Position, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(self, 'positions', tuple(self.positions))
        object.__setattr__(self, 'source_ids', tuple(self.source_ids))


@dataclass(frozen=True)
class Inputs:
    as_of_ms: int
    candidates: tuple[Candidate, ...]
    portfolio: Portfolio
    relationships: Evidence
    risk_policy: Evidence
    control_state: str
    sources: tuple[Source, ...]
    exposure_source_id: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'candidates', tuple(self.candidates))
        object.__setattr__(self, 'sources', tuple(self.sources))
        _immutable(self)


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    inputs_json: str
    result_json: str
    allocator_version: str = VERSION

    def payload(self):
        return dict(proposal_id=self.proposal_id, allocator_version=self.allocator_version,
                    inputs=json.loads(self.inputs_json), result=json.loads(self.result_json))


def number(value):
    if not isinstance(value, str):
        return None
    try:
        n = Decimal(value)
        return n if n.is_finite() else None
    except InvalidOperation:
        return None


def _references(inputs):
    sources = {s.source_id: s for s in inputs.sources}
    if len(sources) != len(inputs.sources):
        raise ValueError('DUPLICATE_SOURCE_ID')
    # Recursively require every provenance reference to resolve in frozen set.
    def visit(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == 'source_ids':
                    if any(ref not in sources for ref in v):
                        raise ValueError('SOURCE_REFERENCE_UNRESOLVED')
                elif k == 'approved_plan_source_id' and v is not None:
                    if v not in sources:
                        raise ValueError('PLAN_REFERENCE_UNRESOLVED')
                else:
                    visit(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                visit(v)
    visit(asdict(inputs))
    for s in inputs.sources:
        if digest(json.loads(s.payload_json)) != s.sha256:
            raise ValueError('SOURCE_INTEGRITY_REFUSED')


def _ordered(inputs):
    if len({c.identity for c in inputs.candidates}) != len(inputs.candidates):
        raise ValueError('DUPLICATE_OPPORTUNITY_VERSION')
    positions = inputs.portfolio.positions
    if len({(p.instrument, p.market_type) for p in positions}) != len(positions):
        raise ValueError('PORTFOLIO_OFFSETTING_OR_DUPLICATE_POSITION')
    for p in positions:
        if p.direction not in ('LONG', 'SHORT') or number(p.quantity) is None or number(p.quantity) <= 0:
            raise ValueError('PORTFOLIO_POSITION_INVALID')
    frozen = json.loads(canonical(asdict(inputs)))
    frozen['candidates'].sort(key=lambda c: (c['opportunity_id'], c['version_id']))
    frozen['sources'].sort(key=lambda s: s['source_id'])
    frozen['portfolio']['positions'].sort(key=lambda p: (p['instrument'], p['market_type']))
    return frozen


def _economic_reasons(c, inputs):
    e = c.economics
    reasons = []
    if e.evidence.status != Status.ESTABLISHED or number(e.expected_net_value) is None:
        reasons.append('EXPECTED_ECONOMICS_UNAVAILABLE')
    if not all(isinstance(v, str) and v for v in e.key()[:-1]) or c.horizon != e.horizon:
        reasons.append('ECONOMIC_COMPARISON_BASIS_UNAVAILABLE')
    from .economics import from_payload, from_inputs, to_allocator, verify
    try:
        if not e.receipt_json:
            raise ValueError('MISSING_RECEIPT')
        receipt = from_payload(json.loads(e.receipt_json))
        frozen = from_inputs(json.loads(receipt.inputs_json))
        b = frozen.binding
        expected, source = to_allocator(receipt)
        if (not verify(receipt, frozen, inputs.as_of_ms) or e != expected
                or source not in inputs.sources
                or any(s not in inputs.sources for s in frozen.context)
                or (b.opportunity_id, b.strategy_id, b.version_id, b.spec_hash,
                    b.instrument, b.market_type, b.direction, b.horizon, b.as_of_ms) !=
                   (c.opportunity_id, c.strategy_id, c.version_id, c.spec_hash,
                    c.instrument, c.market_type, c.direction, c.horizon, c.as_of_ms)
                or b.context_json != (c.opportunity_context_json if c.opportunity_context_json is not None else
                    canonical({k: asdict(getattr(c, k)) for k in
                    ('validation', 'probation', 'evidence_quality', 'regime_world')}))):
            raise ValueError('RECEIPT_OR_CONTEXT_DIFFERS')
        if c.opportunity_context_json is not None:
            from .opportunity_live import receipt_from_source, verify_candidate
            live_sources = [s for s in frozen.context if s.source_id.startswith('live-context:')]
            if len(live_sources) != 1 or not verify_candidate(c, receipt_from_source(live_sources[0]), receipt):
                raise ValueError('FROZEN_CONTEXT_CANDIDATE_DIFFERS')
    except (ValueError, TypeError, KeyError, AttributeError):
        reasons.append('EXPECTED_ECONOMICS_RECEIPT_REFUSED')
    return reasons


def _size(c):
    required = {'strategy_requested', 'risk_permitted', 'account_funding',
                'capacity', 'portfolio_constraints'}
    bounds = {b.authority: b for b in c.bounds}
    if len(bounds) != len(c.bounds) or not required.issubset(bounds):
        return UNAVAILABLE, 'MANDATORY_SIZE_BOUND_UNAVAILABLE'
    values, units = [], set()
    for b in bounds.values():
        n = number(b.maximum)
        if b.evidence.status != Status.ESTABLISHED or n is None or n < 0 or not b.unit:
            return UNAVAILABLE, 'MANDATORY_SIZE_BOUND_UNAVAILABLE'
        values.append(n)
        units.add(b.unit)
    if len(units) != 1:
        return UNAVAILABLE, 'SIZE_BOUND_UNITS_INCOMPARABLE'
    if c.capacity.status != Status.ESTABLISHED:
        return UNAVAILABLE, 'CAPACITY_UNAVAILABLE'
    return str(min(values)), None


def _holding_economics_current(e, inputs):
    from .economics import from_payload, from_inputs, to_allocator, verify
    try:
        receipt = from_payload(json.loads(e.receipt_json))
        expected, source = to_allocator(receipt)
        return (e == expected and source in inputs.sources
                and e.evidence.status == Status.ESTABLISHED
                and verify(receipt, from_inputs(json.loads(receipt.inputs_json)), inputs.as_of_ms))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def _portfolio_context_reasons(c, inputs):
    """A live candidate cannot carry a different book into allocation."""
    if c.opportunity_context_json is None:
        return []  # Legacy detached contracts remain replayable.
    from .opportunity_live import receipt_from_source
    try:
        sources = [s for s in inputs.sources if s.source_id.startswith('live-context:')]
        receipts = [receipt_from_source(s) for s in sources]
        matching = [r for r in receipts if r.context.context_id == c.context_id]
        if len(matching) != 1:
            raise ValueError('CONTEXT_SOURCE_MISSING')
        body = json.loads(matching[0].payload_json)
        raw = next(json.loads(s['payload_json']) for s in body['sources'] if s['source_id'] == 'portfolio')
        book = raw['data']
        p = inputs.portfolio
        expected = sorted((v['instrument_id'], book['market_type'], v['side'].upper(), number(str(v['quantity'])))
                          for v in book['positions'])
        actual = sorted((v.instrument, v.market_type, v.direction, number(v.quantity)) for v in p.positions)
        if (body['portfolio_status'] != 'AVAILABLE' or raw['valid_until_ms'] is None
                or not raw['known_at_ms'] <= inputs.as_of_ms <= raw['valid_until_ms']
                or (book['snapshot_id'], book['observed_at_ms']) != (p.snapshot_id, p.as_of_ms)
                or expected != actual):
            raise ValueError('CONTEXT_BOOK_DIFFERS_OR_STALE')
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
        return ['OPPORTUNITY_PORTFOLIO_CUT_UNAVAILABLE_OR_DIFFERS']
    return []


def allocate(inputs: Inputs) -> Proposal:
    _references(inputs)
    frozen = _ordered(inputs)
    now, portfolio = inputs.as_of_ms, inputs.portfolio
    if type(now) is not int or now < 0:
        raise ValueError('AS_OF_INVALID')
    portfolio_ok = (portfolio.evidence.status == Status.ESTABLISHED
                    and portfolio.valid_until_ms is not None
                    and portfolio.as_of_ms <= now <= portfolio.valid_until_ms)
    exposure, exposure_blockers = None, []
    if inputs.exposure_source_id is None and any(
            any(ref.startswith('candidate-bridge:') for ref in c.source_ids) for c in inputs.candidates):
        exposure_blockers.append('PORTFOLIO_EXPOSURE_AUTHORITY_UNAVAILABLE')
    if inputs.exposure_source_id is not None:
        from .common_factor import from_allocator
        try:
            exposure = from_allocator(inputs)
        except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
            exposure_blockers.append('PORTFOLIO_EXPOSURE_AUTHORITY_REFUSED')
        else:
            if exposure['concentration']['status'] == 'EXCEEDS_EXISTING_LIMIT':
                exposure_blockers.append('EXISTING_CONCENTRATION_LIMIT_EXCEEDED')
            elif exposure['concentration']['status'] == 'UNAVAILABLE':
                exposure_blockers.append('CONCENTRATION_AUTHORITY_UNAVAILABLE')
            if not exposure['constraints']['new_position_count_permitted']:
                exposure_blockers.append('EXISTING_OPEN_POSITION_LIMIT_NO_HEADROOM')
    book = {(p.instrument, p.market_type): p for p in portfolio.positions}
    directions = {}
    for c in inputs.candidates:
        # Unvalidated/stale candidates cannot create a false opposing signal.
        if (c.validation.status == Status.ESTABLISHED and c.freshness.status == Status.ESTABLISHED
                and c.valid_until_ms is not None and c.as_of_ms <= now <= c.valid_until_ms
                and c.direction in ('LONG', 'SHORT')):
            directions.setdefault((c.instrument, c.market_type), set()).add(c.direction)
    conflicts = sorted(k for k, v in directions.items() if len(v) > 1)
    learned_source = next((s for s in inputs.sources if s.source_id == 'governed-allocation-context'), None)
    learned = json.loads(learned_source.payload_json) if learned_source else {}
    rows, comparable = [], []
    for c in sorted(inputs.candidates, key=lambda c: c.identity):
        held = book.get((c.instrument, c.market_type))
        interaction = ('NO_ACTION' if c.direction == 'FLAT' else 'NEW_POSITION' if not held
                       else 'SUPPORTS_EXISTING' if c.direction == held.direction else 'CONFLICTS_EXISTING')
        blocked, missing, conflict = list(exposure_blockers), [], []
        blocked.extend(_portfolio_context_reasons(c, inputs))
        if not portfolio_ok:
            blocked.append('PORTFOLIO_EVIDENCE_STALE_OR_UNAVAILABLE')
        if inputs.risk_policy.status != Status.ESTABLISHED:
            blocked.append('RISK_POLICY_UNAVAILABLE')
        if inputs.control_state != 'ACTIVE':
            blocked.append('CONTROL_STATE_DISALLOWS_NEW_ALLOCATION')
        for name, e in [('STRATEGY_VERSION', c.validation), ('PROBATION', c.probation),
                        ('COST_EVIDENCE', c.costs), ('CAPACITY', c.capacity),
                        ('ACCOUNT_INSTRUMENT', c.account_instrument), ('RISK', c.risk_compatibility),
                        ('FRESHNESS', c.freshness)]:
            if e.status in (Status.INVALID, Status.INCOMPATIBLE):
                blocked.append(name + '_INVALID_OR_INCOMPATIBLE')
            elif e.status != Status.ESTABLISHED:
                missing.append(name + '_UNAVAILABLE')
        if c.valid_until_ms is None:
            missing.append('REQUIRED_EVIDENCE_EXPIRY_UNAVAILABLE')
        elif not c.as_of_ms <= now <= c.valid_until_ms:
            blocked.append('REQUIRED_EVIDENCE_STALE')
        missing.extend(_economic_reasons(c, inputs))
        learned_context = learned.get(c.candidate_id)
        if learned_context and learned_context.get('value') is not None:
            from trader.learning import targets as learned_targets, consumers as learned_consumers, foundation as learning
            learned_targets.temporal_observation(learned_context, learning.Target.ALLOCATION,
                learned_consumers.allocation_context(c), now)
            # A zero bound denies only future new allocation. Nonzero bounds
            # remain context until units can be compared by a registered policy.
            if not held and learned_context['value']['max_share'] == 0:
                blocked.append('GOVERNED_ALLOCATION_BOUND_NO_NEW_RESOURCE')
        if (c.instrument, c.market_type) in conflicts:
            conflict.append('OPPOSING_STRATEGY_DIRECTIONS')
        if interaction == 'CONFLICTS_EXISTING':
            conflict.append('INCOMPATIBLE_EXISTING_POSITION')
        size, size_reason = _size(c)
        if size_reason:
            missing.append(size_reason)
        state = (Feasibility.BLOCKED if blocked else Feasibility.CONFLICTED if conflict
                 else Feasibility.INCOMPLETE if missing else Feasibility.COMPARABLE)
        reasons = blocked + conflict + missing
        try:
            context_id = c.context_id
            candidate_id = c.candidate_id
        except (ValueError, TypeError, KeyError, AttributeError):
            context_id = None
            candidate_id = None
        row = dict(identity=list(c.identity), candidate_id=candidate_id, context_id=context_id, instrument=c.instrument, market_type=c.market_type,
                   direction=c.direction, feasibility=state.value, refusal_reasons=reasons,
                   interaction=interaction, expression='KEEP_EXISTING' if held else 'FLAT',
                   proposed_size=UNAVAILABLE, size_bounds=[asdict(b) for b in c.bounds],
                   unresolved_evidence=missing, accepted=False,
                   relationship_context=asdict(c.relationships))
        if learned_context and learned_context.get('value') is not None:
            row['governed_allocation_context'] = learned_context
        rows.append(row)
        if state == Feasibility.COMPARABLE and interaction == 'NEW_POSITION':
            comparable.append((c, row, size))
        elif interaction == 'SUPPORTS_EXISTING' and not reasons:
            row['refusal_reasons'].append('EXISTING_APPROVED_PLAN_NO_RESIZE')
        elif interaction == 'NO_ACTION' and not reasons:
            row['refusal_reasons'].append('FLAT_NO_ACTION')
    keys = {c.economics.key() for c, _, _ in comparable}
    ordered = []
    reason = 'INSUFFICIENT_COMPARABLE_ECONOMICS'
    if len(keys) == 1:
        # Stable identity tie-break, then exact Decimal comparison (no
        # arithmetic or dependence on the process Decimal precision).
        ordered = sorted(comparable, key=lambda item: item[0].identity)
        ordered.sort(key=lambda item: number(item[0].economics.expected_net_value), reverse=True)
        reason = 'NO_POSITIVE_COMPARABLE_ECONOMIC_VALUE'
    selected = []
    if ordered and number(ordered[0][0].economics.expected_net_value) > 0:
        c, row, size = ordered[0]
        if number(size) > 0:
            contributors = [x.identity for x, _, _ in ordered if
                            (x.instrument, x.market_type, x.direction) == (c.instrument, c.market_type, c.direction)]
            confidence_context = None
            if exposure is not None:
                from .common_factor import evidence_groups, _lineages
                source = next(s for s in inputs.sources if s.source_id == inputs.exposure_source_id)
                authority = json.loads(source.payload_json)['inputs']
                ls = tuple(Source(**s) for s in authority['lineage_sources'])
                same_expression = [x for x, _, _ in ordered if x.identity in contributors]
                confidence_context = evidence_groups(same_expression, _lineages(ls, now))
                contributors = [tuple(x) for x in confidence_context['representatives']]
            row.update(accepted=True, expression=c.direction, proposed_size=size)
            selected.append(dict(instrument=c.instrument, market_type=c.market_type, expression=c.direction,
                                 primary_candidate=list(c.identity), context_id=c.context_id, evidence_contributors=contributors,
                                 proposed_size=size, size_unit=c.bounds[0].unit,
                                 duplicate_confidence=confidence_context, risk_final_gate_required=True))
            reason = 'POSITIVE_COMPARABLE_ECONOMIC_PRIORITY'
    for c, row, _ in comparable:
        if not row['accepted']:
            row['refusal_reasons'].append('ECONOMICS_INCOMPARABLE' if len(keys) > 1
                                         else 'CAPITAL_PRIORITY_NOT_SELECTED')
    from .opportunity_cost import compare
    observations = [compare(inputs, c, held, row) for c, row in zip(
        sorted(inputs.candidates, key=lambda c: c.identity), rows)
        for held in sorted(portfolio.positions, key=lambda p: (p.instrument, p.market_type))]
    global_blockers = list(exposure_blockers)
    if not portfolio_ok:
        global_blockers.append('PORTFOLIO_EVIDENCE_STALE_OR_UNAVAILABLE')
    if inputs.risk_policy.status != Status.ESTABLISHED:
        global_blockers.append('RISK_POLICY_UNAVAILABLE')
    if inputs.control_state != 'ACTIVE':
        global_blockers.append('CONTROL_STATE_DISALLOWS_NEW_ALLOCATION')
    result = dict(allocator_version=VERSION, as_of_ms=now, global_blockers=global_blockers,
                  decision='ALLOCATION_PROPOSAL' if selected else 'NO_ALLOCATION', reason=reason,
                  cash_candidate=dict(expression='CASH', action='NO_TRADE', always_available=True,
                                      selected=not selected, expected_yield=UNAVAILABLE),
                  candidates=rows, selected=selected,
                  economic_order=[list(c.identity) for c, _, _ in ordered],
                  conflicts=[list(k) for k in conflicts],
                  common_exposure_context=asdict(inputs.relationships),
                  portfolio_exposure_context=exposure,
                  relationship_policy='DESCRIPTIVE_ONLY_NO_CALIBRATED_PENALTY',
                  opportunity_cost=observations,
                  risk_final_authority=True, side_effects='NONE')
    pid = digest(dict(allocator_version=VERSION, inputs=frozen, result=result))
    return Proposal(pid, canonical(frozen), canonical(result))


def attach_learning(inputs, journal):
    from trader.learning.consumers import allocation_observations
    from dataclasses import replace
    values = {k:v for k,v in allocation_observations(journal, inputs.candidates, as_of_ms=inputs.as_of_ms).items() if v['value'] is not None}
    if not values: return inputs
    return replace(inputs, sources=tuple(s for s in inputs.sources if s.source_id != 'governed-allocation-context')
                   +(Source.freeze('governed-allocation-context', values),))


def verify(proposal: Proposal, current_inputs: Inputs) -> bool:
    """Trusted caller must reread all current authorities; never verify against
    the proposal's embedded inputs alone. Changed inputs require recompute."""
    try:
        return proposal == allocate(current_inputs)
    except (ValueError, TypeError, KeyError):
        return False


def persist(proposal: Proposal, directory: Path) -> Path:
    """Append-only content-addressed artifact, outside the production journal.
    Atomic hard-link publication prevents partial artifacts or overwrites."""
    body = proposal.payload()
    if digest({k: body[k] for k in ('allocator_version', 'inputs', 'result')}) != proposal.proposal_id:
        raise ValueError('PROPOSAL_INTEGRITY_REFUSED')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (proposal.proposal_id + '.json')
    data = canonical(body) + '\n'
    import tempfile
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.proposal-')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_text() != data:
                raise ValueError('IMMUTABLE_PROPOSAL_COLLISION')
    finally:
        os.unlink(temporary)
    return target


def inputs_from_payload(body: dict) -> Inputs:
    """Strict reconstruction of persisted contracts for offline replay."""
    def evidence(d):
        return Evidence(Status(d['status']), tuple(d['source_ids']), d['detail_json'])
    def economics(d):
        return Economics(evidence(d['evidence']), **{k: v for k, v in d.items() if k != 'evidence'})
    candidates = []
    for d in body['candidates']:
        fields = dict(d)
        for k in ('validation', 'probation', 'costs', 'capacity', 'account_instrument',
                  'risk_compatibility', 'evidence_quality', 'regime_world',
                  'existing_position_interaction', 'relationships', 'freshness'):
            fields[k] = evidence(d[k])
        fields['economics'] = economics(d['economics'])
        fields['bounds'] = tuple(Bound(b['authority'], evidence(b['evidence']), b['maximum'], b['unit']) for b in d['bounds'])
        fields['source_ids'] = tuple(d['source_ids'])
        candidates.append(Candidate(**fields))
    p = body['portfolio']
    positions = tuple(Position(**{**v, 'economics': economics(v['economics']) if v['economics'] else None}) for v in p['positions'])
    portfolio = Portfolio(p['snapshot_id'], p['as_of_ms'], p['valid_until_ms'],
                          evidence(p['evidence']), positions, tuple(p['source_ids']))
    return Inputs(body['as_of_ms'], tuple(candidates), portfolio,
                  evidence(body['relationships']), evidence(body['risk_policy']),
                  body['control_state'], tuple(Source(**s) for s in body['sources']), body.get('exposure_source_id'))
