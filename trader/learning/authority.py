"""Learning target contracts and owner boundaries. No calibration policy.

Adapters retain no target state. Owners supply current reads and atomic writes;
receipts live in LearningApplication's existing append-only journal. Missing
adaptive policy is a refusal, never inferred from ordinary configuration bounds.
"""
from dataclasses import asdict, dataclass
from enum import Enum
import json
from types import MappingProxyType

from . import foundation as L
from trader.strategy import factory_handoff as F


class Refused(ValueError):
    def __init__(self, result, reason):
        self.result, self.reason = result, reason
        super().__init__(reason)


class MutationShape(str, Enum):
    REPLACE_DIMENSION = 'REPLACE_DIMENSION'
    RETIRE = 'RETIRE'


@dataclass(frozen=True)
class Boundary:
    """Explicit owner approval, scoped to this one target dimension.

    Values are an exact allowlist, not invented numeric default bounds. A future
    numeric policy can supply its own owner validator behind this same contract.
    """
    policy_id: str
    policy_version: str
    dimension: str
    shape: MutationShape
    allowed_values_json: str

    def __post_init__(self):
        if not self.policy_id or not self.policy_version or not self.dimension:
            raise ValueError('explicit_policy_required')
        if not isinstance(self.shape, MutationShape):
            raise TypeError('typed_mutation_shape_required')
        values = json.loads(self.allowed_values_json)
        if not isinstance(values, list) or not values:
            raise ValueError('approved_values_required')


@dataclass(frozen=True)
class LearningTargetRef:
    target_category: L.Target
    target_id: str
    owner_authority: str
    current_json: str
    state_version: str
    state_hash: str
    allowed_mutation: Boundary | None
    provenance_json: str
    availability: str = 'AVAILABLE'

    def __post_init__(self):
        if not isinstance(self.target_category, L.Target):
            raise TypeError('typed_target_category_required')
        if self.owner_authority != OWNERS[self.target_category] or not self.target_id:
            raise ValueError('target_owner_binding_invalid')
        current = json.loads(self.current_json)
        json.loads(self.provenance_json)
        if not self.state_version or self.state_hash != L.digest(current):
            raise ValueError('target_state_binding_invalid')
        if self.allowed_mutation is not None:
            if not isinstance(self.allowed_mutation, Boundary):
                raise TypeError('typed_boundary_required')
            if self.target_category != L.Target.LIFECYCLE:
                coordinate = json.loads(self.target_id)
                if (set(coordinate) != {'subject', 'dimension', 'context'}
                        or not all(isinstance(v, str) and v for v in coordinate.values())
                        or coordinate['dimension'] != self.allowed_mutation.dimension):
                    raise ValueError('approved_dimension_coordinate_required')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, raw):
        raw = dict(raw)
        raw['target_category'] = L.Target(raw['target_category'])
        if raw['allowed_mutation'] is not None:
            b = dict(raw['allowed_mutation'])
            b['shape'] = MutationShape(b['shape'])
            raw['allowed_mutation'] = Boundary(**b)
        return cls(**raw)


@dataclass(frozen=True)
class Mutation:
    shape: MutationShape
    dimension: str
    value_json: str

    def __post_init__(self):
        if not isinstance(self.shape, MutationShape) or not self.dimension:
            raise TypeError('typed_dimension_mutation_required')
        json.loads(self.value_json)

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict) or set(raw) != {'shape', 'dimension', 'value_json'}:
            raise ValueError('exact_mutation_shape_required')
        return cls(MutationShape(raw['shape']), raw['dimension'], raw['value_json'])


@dataclass(frozen=True)
class AuthorityReceipt:
    learning_application_id: str
    target_category: str
    target_id: str | None
    owner_authority: str | None
    prior_state_json: str
    prior_version: str | None
    requested_mutation_json: str
    resulting_state_json: str
    resulting_version: str | None
    rule_id: str | None
    rule_version: str | None
    evidence_id: str
    timestamp: int
    provenance_json: str



@dataclass(frozen=True)
class AuthorityApplied:
    resulting: LearningTargetRef
    receipt: AuthorityReceipt
    owner_receipt_json: str


OWNERS = MappingProxyType({
    L.Target.CONFIDENCE: 'AgentCalibration',
    L.Target.ALLOCATION: 'PortfolioAllocator',
    L.Target.ATTENTION: 'Attention',
    L.Target.RESEARCH: 'ResearchScheduler',
    L.Target.WEIGHTS: 'ContextualEvidenceWeights',
    L.Target.WORLD: 'WorldModel',
    L.Target.LIFECYCLE: 'StrategyGovernor',
})


_APPLICATION_GATE = object()


class AuthorityAdapter:
    """Stateless owner API. Calls run inside the application's locked transaction.

    _write is implemented by an owner, never by a generic state store. Future
    owners must use the supplied transaction for both the write and subsequent
    read. File-backed legacy calibration cannot be written until it provides an
    atomic receipt protocol; R1 deliberately publishes no such mutable policy.
    """
    def read(self, journal, cfg, target_id):
        raise NotImplementedError

    def verify_precondition(self, journal, cfg, expected):
        actual = self.read(journal, cfg, expected.target_id)
        if actual != expected:
            raise Refused('STALE', 'target state/version/policy changed')
        return actual

    def validate_request(self, target, mutation):
        if target.owner_authority != OWNERS[target.target_category]:
            raise Refused('CONFLICT', 'wrong owning authority')
        if target.availability != 'AVAILABLE':
            raise Refused(target.availability, 'owner has no supported mutable target')
        boundary = target.allowed_mutation
        if boundary is None:
            raise Refused('POLICY_UNAVAILABLE', 'no approved adaptive mutation policy')
        if (mutation.shape, mutation.dimension) != (boundary.shape, boundary.dimension):
            raise Refused('CONFLICT', 'mutation escapes approved target dimension')
        if mutation.value_json not in tuple(L.canonical(v) for v in json.loads(boundary.allowed_values_json)):
            raise Refused('CONFLICT', 'requested value outside approved policy')

    def compare_and_apply(self, journal, cfg, expected, mutation, *, evidence,
                          proposal, at_ms, application_id, connection, application_gate=None, provenance_json='{}'):
        """Cannot bypass exact registration, replay, eligibility or proposal binding."""
        from .dispatch import verify_proposal
        verify_proposal(evidence, proposal, expected, mutation, at_ms)
        actual = self.verify_precondition(journal, cfg, expected)
        self.validate_request(actual, mutation)
        if getattr(journal, 'connection', None) is not connection or not connection.in_transaction:
            raise Refused('CONFLICT', 'owning authority transaction required')
        if application_gate is not _APPLICATION_GATE:
            raise Refused('CONFLICT', 'LearningApplication receipt transaction required')
        owner_receipt = self._write(journal, cfg, actual, mutation,
            at_ms=at_ms, application_id=application_id, connection=connection)
        resulting = self.read(journal, cfg, actual.target_id)
        state = json.loads(actual.current_json)
        if not isinstance(state, dict) or mutation.dimension not in state:
            raise Refused('CONFLICT', 'owner target has no approved dimension')
        expected_state = dict(state, **{mutation.dimension: json.loads(mutation.value_json)})
        result_state = json.loads(resulting.current_json)
        if actual.target_category == L.Target.LIFECYCLE:
            expected_state.pop('history_sha256')
            result_state.pop('history_sha256')
        if (L.canonical(result_state) != L.canonical(expected_state)
                or resulting.state_version == actual.state_version
                or (resulting.target_category, resulting.target_id, resulting.owner_authority,
                    resulting.allowed_mutation, resulting.provenance_json) !=
                   (actual.target_category, actual.target_id, actual.owner_authority,
                    actual.allowed_mutation, actual.provenance_json)):
            raise Refused('CONFLICT', 'owner update escaped approved dimension/version boundary')
        lifecycle = actual.target_category == L.Target.LIFECYCLE
        receipt = AuthorityReceipt(application_id, actual.target_category, actual.target_id,
            actual.owner_authority,
            actual.current_json if lifecycle else L.canonical(actual.to_dict()),
            actual.state_version, L.canonical(asdict(mutation)),
            resulting.current_json if lifecycle else L.canonical(resulting.to_dict()),
            resulting.state_version, proposal.rule, proposal.rule_version,
            evidence.evidence_id, at_ms, provenance_json)
        return AuthorityApplied(resulting, receipt, L.canonical(owner_receipt))

    def _write(self, *args, **kwargs):
        raise Refused('POLICY_UNAVAILABLE', 'owner has no approved writer')


class PolicyUnavailableAuthority(AuthorityAdapter):
    """Explicit read boundary around an existing owner; no second state store.

    target_id is an exact dimension/context coordinate encoded as canonical JSON.
    A global key cannot authorize contextual confidence or evidence updates.
    Current value may be null: that means no adaptive state, not a fabricated zero.
    """
    def __init__(self, category):
        self.category = category

    def read(self, journal, cfg, target_id):
        try:
            coordinate = json.loads(target_id)
            if set(coordinate) != {'subject', 'dimension', 'context'} or not all(
                isinstance(v, str) and v for v in coordinate.values()):
                raise ValueError('exact contextual coordinate required')
        except (ValueError, TypeError):
            raise Refused('TARGET_NOT_SUPPORTED', 'exact subject/dimension/context required')
        category = self.category
        source, state = None, None
        if category == L.Target.CONFIDENCE:
            from trader.agents import calibration
            source = 'trader.agents.calibration.load'
            if coordinate['dimension'] == 'conviction_calibration' and coordinate['context'] == 'legacy-agent':
                state = calibration.load().get(coordinate['subject'])
        elif category == L.Target.WEIGHTS:
            # Existing legacy global EWA has no contextual learning authority.
            # Do not relabel it as a calibrated contextual evidence weight.
            source = 'trader.agents.weights_online; trader.strategy.blend'
        elif category == L.Target.ALLOCATION:
            source = 'trader.portfolio.allocator.Inputs/allocate/verify'
            state = cfg.get('portfolio')
        elif category == L.Target.ATTENTION:
            source = 'trader.cognition.attention.CognitionConfig; trader.observability.attention.settings'
            state = cfg.get('attention')
        elif category == L.Target.RESEARCH:
            source = 'trader.research.runner.ResearchRunner; trader.research.planner'
            state = cfg.get('research')
        elif category == L.Target.WORLD:
            source = 'trader.world.model.WorldModel'
        unavailable = 'TARGET_NOT_SUPPORTED' if category == L.Target.WORLD else 'AVAILABLE'
        provenance = dict(source=source, coordinate=coordinate,
            adaptive_state='UNAVAILABLE', policy='UNAVAILABLE')
        return LearningTargetRef(category, target_id, OWNERS[category], L.canonical(state),
            L.digest(dict(state=state, provenance=provenance)), L.digest(state), None,
            L.canonical(provenance), unavailable)


class LifecycleAuthority(AuthorityAdapter):
    def read(self, journal, cfg, target_id):
        state = F.lifecycle_target(journal, target_id)
        boundary = Boundary('strategy-governor.retirement', L.digest({k: sorted(v) for k, v in F.GOVERNOR_ALLOWED.items()}),
                            'state', MutationShape.RETIRE, L.canonical([F.RETIRED]))
        return LearningTargetRef(L.Target.LIFECYCLE, target_id, OWNERS[L.Target.LIFECYCLE],
            L.canonical(state), state['history_sha256'], L.digest(state), boundary,
            L.canonical(dict(spec_hash=state['spec_hash'], source='StrategyGovernor')))

    def validate_request(self, target, mutation):
        super().validate_request(target, mutation)
        state = json.loads(target.current_json)
        if F.RETIRED not in F.GOVERNOR_ALLOWED.get(state['state'], set()):
            raise Refused('CONFLICT', 'Governor does not authorize retirement')

    def _write(self, journal, cfg, target, mutation, *, at_ms, application_id, connection):
        return F.govern_version(journal, cfg, target.target_id, F.RETIRED,
            actor='strategy_governor', reason_code=application_id, at_ms=at_ms,
            expected_target=json.loads(target.current_json), _connection=connection)


AUTHORITIES = MappingProxyType({category: LifecycleAuthority() if category == L.Target.LIFECYCLE
                               else PolicyUnavailableAuthority(category) for category in L.Target})


def read_target(journal, cfg, category, target_id):
    if not isinstance(category, L.Target):
        raise Refused('TARGET_NOT_SUPPORTED', 'target category outside learning authority')
    return AUTHORITIES[category].read(journal, cfg, target_id)
