"""Learning-owned contextual state. No fitted values or production policies.

Append-only revisions and CAS receipts share LearningApplication's transaction.
Exact context equality is mandatory; missing identity never means global scope.
"""
from dataclasses import dataclass, asdict
from enum import Enum
from types import MappingProxyType
import json
import math
from . import foundation as L

SCHEMA = 'adaptive-target-state.v1'
PRODUCTION_RULES = MappingProxyType({})
ADAPTIVE = frozenset(t for t in L.Target if t != L.Target.LIFECYCLE)


@dataclass(frozen=True)
class Context:
    asset_scope: str
    horizon: str
    regime: str
    evidence_type: str
    direction: str
    strategy_family: str
    subject_id: str

    def __post_init__(self):
        if any(type(v) is not str or not v.strip() or v.upper() in ('UNKNOWN','UNAVAILABLE','NOT_PROVEN','*','LATEST')
               for v in asdict(self).values()):
            raise ValueError('exact_context_identity_required')

    @property
    def identity(self):
        return L.digest(asdict(self))


class Priority(str, Enum):
    LOW = 'LOW'
    NORMAL = 'NORMAL'
    HIGH = 'HIGH'


def _unit(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('bounded_unit_state_required')


@dataclass(frozen=True)
class Reliability:
    reliability: float
    def __post_init__(self): _unit(self.reliability)


@dataclass(frozen=True)
class AllocationBound:
    max_share: float
    def __post_init__(self): _unit(self.max_share)


@dataclass(frozen=True)
class PriorityState:
    priority: Priority
    def __post_init__(self):
        if not isinstance(self.priority, Priority): raise ValueError('typed_priority_required')


@dataclass(frozen=True)
class EvidenceReliability:
    reliability: float
    def __post_init__(self): _unit(self.reliability)


@dataclass(frozen=True)
class ClaimConfidence:
    confidence: float
    def __post_init__(self): _unit(self.confidence)


TYPES = MappingProxyType({L.Target.CONFIDENCE: Reliability, L.Target.ALLOCATION: AllocationBound,
    L.Target.ATTENTION: PriorityState, L.Target.RESEARCH: PriorityState,
    L.Target.WEIGHTS: EvidenceReliability, L.Target.WORLD: ClaimConfidence})


def typed(target, raw):
    raw = dict(raw)
    if target in (L.Target.ATTENTION, L.Target.RESEARCH):
        raw['priority'] = Priority(raw['priority'])
    return TYPES[target](**raw)


def ensure(journal):
    with journal._tx() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS learning_target_revisions(
            target TEXT NOT NULL, context_id TEXT NOT NULL, revision INTEGER NOT NULL,
            payload TEXT NOT NULL, sha256 TEXT NOT NULL, application_id TEXT NOT NULL UNIQUE,
            PRIMARY KEY(target,context_id,revision),
            FOREIGN KEY(application_id) REFERENCES learning_application_receipts(application_id)
                DEFERRABLE INITIALLY DEFERRED);
        CREATE TRIGGER IF NOT EXISTS target_revision_authority BEFORE INSERT ON learning_target_revisions
          WHEN learning_target_write() != 1
          BEGIN SELECT RAISE(ABORT,'learning target authority required'); END;
        CREATE TRIGGER IF NOT EXISTS target_revision_no_update BEFORE UPDATE ON learning_target_revisions
          BEGIN SELECT RAISE(ABORT,'target revision immutable'); END;
        CREATE TRIGGER IF NOT EXISTS target_revision_no_delete BEFORE DELETE ON learning_target_revisions
          BEGIN SELECT RAISE(ABORT,'target revision immutable'); END;
        """)


def temporal_state(state, target, context, as_of_ms):
    """Validate the immutable application proof retained by a historical read."""
    if type(as_of_ms) is not int or as_of_ms < 0:
        raise ValueError('explicit_temporal_cut_required')
    body = {k:v for k,v in state.items() if k not in ('state_hash', 'application', 'application_sha256')}
    if (L.digest(body) != state['state_hash'] or body['context'] != asdict(context)
            or body['target'] != target.value):
        raise ValueError('target_state_corrupt')
    if body['revision'] == 0 and body['value'] is None:
        return state
    receipt = state.get('application')
    if (not isinstance(receipt, dict) or L.digest(receipt) != state.get('application_sha256')
            or receipt.get('result') != 'APPLIED' or receipt.get('target_type') != target.value
            or receipt.get('resulting') != dict(body, state_hash=state['state_hash'])
            or type(receipt.get('applied_at')) is not int or not 0 <= receipt['applied_at'] <= as_of_ms):
        raise ValueError('future_or_unqualified_temporal_target_state')
    typed(target, body['value'])
    return state


def read(journal, target, context, *, as_of_ms=None):
    if target not in ADAPTIVE or not isinstance(context, Context):
        raise ValueError('typed_adaptive_target_required')
    if as_of_ms is not None and (type(as_of_ms) is not int or as_of_ms < 0):
        raise ValueError('explicit_temporal_cut_required')
    if hasattr(journal, 'read_target'):
        return journal.read_target(target, context, as_of_ms=as_of_ms)
    if journal.query("SELECT 1 FROM sqlite_master WHERE name='learning_target_revisions'"):
        if as_of_ms is None:
            rows = journal.query('SELECT * FROM learning_target_revisions WHERE target=? AND context_id=? ORDER BY revision DESC LIMIT 1',
                                 (target.value, context.identity))
        else:
            rows = journal.query("""SELECT r.*, a.payload AS application_payload,
                    a.sha256 AS application_sha256
                FROM learning_target_revisions r JOIN learning_application_receipts a
                  ON a.application_id=r.application_id
                WHERE r.target=? AND r.context_id=? AND a.result='APPLIED'
                  AND json_type(a.payload,'$.applied_at')='integer'
                  AND json_extract(a.payload,'$.applied_at') BETWEEN 0 AND ?
                ORDER BY r.revision DESC LIMIT 1""", (target.value, context.identity, as_of_ms))
        if rows:
            row = rows[0]; body = json.loads(row['payload'])
            if (L.digest(body) != row['sha256'] or body['context'] != asdict(context)
                    or body['target'] != target.value or body['revision'] != row['revision']):
                raise ValueError('target_state_corrupt')
            typed(target, body['value'])
            state = dict(body, state_hash=row['sha256'])
            if as_of_ms is not None:
                receipt = json.loads(row['application_payload'])
                if receipt.get('application_id') != row['application_id']:
                    raise ValueError('temporal_application_identity_mismatch')
                state.update(application=receipt, application_sha256=row['application_sha256'])
                temporal_state(state, target, context, as_of_ms)
            return state
    body = dict(schema=SCHEMA, target=target.value, context=asdict(context),
                revision=0, value=None, previous_hash=None)
    return dict(body, state_hash=L.digest(body))


@dataclass(frozen=True)
class Rule:
    """A registered deterministic update rule.

    The optional fields are the generic dispatch contract (learning/dispatch.py):
    which outcome kinds it accepts, exact retained source roles/versions it
    needs, how the exact target context is derived from replayed evidence, and
    its sufficiency test. A rule missing any of them can still be applied by an
    explicit caller but is never dispatched automatically.
    """
    rule_id: str
    target: L.Target
    version: str
    evaluate: object
    kinds: tuple = ()
    required_sources: tuple = ()
    context: object = None
    sufficient: object = None
    requirements: tuple = ()


def registry(journal, supplied=None):
    if supplied is None: return PRODUCTION_RULES
    # Explicit isolated test injection. Runtime has no configuration or import
    # route to these rules, and never passes a registry.
    if not getattr(journal, 'learning_test_only', False):
        raise ValueError('isolated_test_registry_required')
    from trader.core.config import ROOT
    path = getattr(journal, 'db_path', None)
    if path is not None and str(path) != ':memory:' and path.resolve().is_relative_to(ROOT / 'data'):
        raise ValueError('production_test_registry_forbidden')
    if journal.query("SELECT 1 FROM sqlite_master WHERE name='trades'"):
        if journal.query("SELECT 1 FROM trades WHERE exec_mode='live' LIMIT 1"):
            raise ValueError('live_test_registry_forbidden')
    return supplied


def propose(ev, target, current, rule_id, rules):
    rule = rules.get(rule_id)
    status, value, reason = L.Status.UNREGISTERED, None, 'POLICY_UNAVAILABLE'
    if ev.quality != 'VERIFIED_REPLAY' or not L.verified_history(ev):
        status, reason = L.Status.INCOMPLETE, 'verified evidence required'
    elif rule is not None and rule.target == target:
        status, value, reason = rule.evaluate(ev, current)
        if value is not None:
            value = asdict(typed(target, value))
    return L.LearningUpdateProposal(ev.evidence_id, target, L.canonical(current),
        L.canonical(value) if value is not None else None, rule_id,
        rule.version if rule else None, status,
        (ev.outcome_id, ev.attribution_id, ev.replay_id), reason)


def compare_and_apply(connection, journal, target, expected, value, application_id, *, authorization=None):
    # The owner independently refuses a raw writer call or an unregistered rule.
    if authorization is None:
        raise ValueError('learning_application_authority_required')
    ev, proposal, req, supplied = authorization
    rules = registry(journal,supplied)
    rule = rules.get(proposal.rule)
    if (not connection.in_transaction or getattr(journal,'connection',None) is not connection
            or rule is None or rule.target != target
            or req.application_id != application_id or req.evidence_id != ev.evidence_id
            or req.proposal_id != proposal.proposal_id
            or proposal.status != L.Status.APPLICABLE
            or proposal.current_json != L.canonical(expected)
            or proposal.proposed_json != L.canonical(value)
            or propose(ev,target,expected,proposal.rule,rules) != proposal):
        raise ValueError('learning_application_authority_differs')
    context = Context(**expected['context'])
    if read(journal, target, context) != expected:
        raise ValueError('target_compare_conflict')
    value = asdict(typed(target, value))
    body = dict(schema=SCHEMA, target=target.value, context=asdict(context),
        revision=expected['revision']+1, value=value, previous_hash=expected['state_hash'])
    journal._local.learning_target_write = True
    try:
        connection.execute('INSERT INTO learning_target_revisions VALUES(?,?,?,?,?,?)',
            (target.value, context.identity, body['revision'], L.canonical(body), L.digest(body), application_id))
    finally:
        journal._local.learning_target_write = False
    return dict(body, state_hash=L.digest(body))


def observation(journal, target, context, *, as_of_ms=None):
    state = read(journal, target, context, as_of_ms=as_of_ms)
    return dict(target=target.value, context_id=context.identity, revision=state['revision'],
                state_hash=state['state_hash'], value=state['value'],
                **({k:state[k] for k in ('application','application_sha256')} if 'application' in state else {}))



def temporal_observation(item, target, context, as_of_ms):
    """A frozen projection must match its full immutable application state."""
    receipt = item.get('application')
    if not isinstance(receipt, dict) or not isinstance(receipt.get('resulting'), dict):
        raise ValueError('temporal_target_projection_unqualified')
    state = dict(receipt['resulting'], application=receipt,
                 application_sha256=item.get('application_sha256'))
    temporal_state(state, target, context, as_of_ms)
    if (item.get('context_id') != context.identity or item.get('target') != target.value
            or any(item.get(key) != state[key] for key in ('state_hash', 'revision', 'value'))):
        raise ValueError('temporal_target_projection_mismatch')
    return item

def priority_order(journal, target, items, context_for):
    # Ordinal preference only. Existing eligibility/evidence gates remain owned
    # by the calling scheduler; stable ties preserve its existing ordering.
    order = {Priority.HIGH.value: 0, Priority.NORMAL.value: 1, Priority.LOW.value: 2}
    def key(item):
        state = read(journal, target, context_for(item))
        return order[(state['value'] or {}).get('priority', Priority.NORMAL.value)]
    return sorted(items, key=key)
