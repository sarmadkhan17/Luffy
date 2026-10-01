"""Immutable outcome -> attribution -> exact replay -> evidence -> proposal.

References identify retained bytes, never 'latest'. Receipt completeness is
not causal proof, statistical independence, predictive edge or venue truth.
Only the registered recent-decay rule can propose retirement. Application is
separate and remains subject to the existing Strategy Governor.
"""
from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Kind(str, Enum):
    EXECUTED = 'EXECUTED_TRADE'
    REJECTED = 'REJECTED_TRADE'
    MISSED = 'MISSED_OPPORTUNITY'
    RISK_BLOCKED = 'RISK_BLOCKED_SIGNAL'
    CASH = 'SKIPPED_CASH'
    RESEARCH = 'RESEARCH_RESULT'
    INCIDENT = 'EXECUTION_DATA_INCIDENT'
    EXECUTION = 'EXECUTION_QUALITY'
    DATA = 'DATA_QUALITY_INCIDENT'


class Boundary(str, Enum):
    REALIZED = 'REALIZED'
    COUNTERFACTUAL = 'COUNTERFACTUAL'
    UNRESOLVED = 'UNRESOLVED'
    UNASSESSABLE = 'UNASSESSABLE'


class Target(str, Enum):
    CONFIDENCE = 'CONFIDENCE_CALIBRATION'
    ALLOCATION = 'STRATEGY_ALLOCATION'
    ATTENTION = 'ATTENTION_PRIORITY'
    RESEARCH = 'RESEARCH_PRIORITY'
    WEIGHTS = 'EVIDENCE_WEIGHTS'
    WORLD = 'WORLD_MODEL_PROBABILITIES'
    LIFECYCLE = 'STRATEGY_LIFECYCLE_STATE'


class Support(str, Enum):
    ESTABLISHED = 'ESTABLISHED'
    DESCRIPTIVE = 'DESCRIPTIVE'
    UNKNOWN = 'UNKNOWN'
    NA = 'NOT_APPLICABLE'


class Status(str, Enum):
    APPLICABLE = 'APPLICABLE'
    INSUFFICIENT = 'INSUFFICIENT_EVIDENCE'
    UNREGISTERED = 'UNREGISTERED_RULE'
    CONFLICTING = 'CONFLICTING_EVIDENCE'
    STALE = 'STALE'
    INCOMPLETE = 'INCOMPLETE_REPLAY'


@dataclass(frozen=True)
class Source:
    role: str
    source_id: str
    version: str
    sha256: str
    available_ms: int | None

    def __post_init__(self):
        if not self.source_id or not self.version or self.version.lower() == 'latest':
            raise ValueError('exact_source_identity_required')
        if len(self.sha256) != 64 or (self.available_ms is not None and (type(self.available_ms) is not int or self.available_ms < 0)):
            raise ValueError('source_hash_or_clock_required')


@dataclass(frozen=True)
class Lineage:
    cycle_id: str | None
    decision_id: str | None
    context_id: str | None
    opportunity_id: str | None = None
    strategy_id: str | None = None
    version_id: str | None = None
    spec_hash: str | None = None
    proposal_id: str | None = None
    intent_id: str | None = None
    risk_decision_id: str | None = None
    execution_ids: tuple = ()
    trade_ids: tuple = ()
    world_id: str | None = None
    regime: str | None = None

    def __post_init__(self):
        if type(self.execution_ids) is not tuple or type(self.trade_ids) is not tuple:
            raise ValueError('immutable_lineage_required')


@dataclass(frozen=True)
class Outcome:
    kind: Kind
    boundary: Boundary
    lineage: Lineage
    decision_ms: int
    observed_ms: int
    sources: tuple[Source, ...]
    observation_json: str
    label: str

    def __post_init__(self):
        if not isinstance(self.lineage, Lineage):
            raise ValueError('immutable_typed_lineage_required')
        if not isinstance(self.kind, Kind) or not isinstance(self.boundary, Boundary):
            raise ValueError('typed_outcome_required')
        if type(self.sources) is not tuple or not all(isinstance(s, Source) for s in self.sources):
            raise ValueError('immutable_sources_required')
        if any(type(t) is not int or t < 0 for t in (self.decision_ms, self.observed_ms)) or self.observed_ms < self.decision_ms:
            raise ValueError('outcome_chronology')
        if len({s.role for s in self.sources}) != len(self.sources):
            raise ValueError('duplicate_source_role')
        if canonical(json.loads(self.observation_json)) != self.observation_json:
            raise ValueError('canonical_observation_required')
        nontrade = self.kind in (Kind.REJECTED, Kind.MISSED, Kind.RISK_BLOCKED, Kind.CASH)
        if nontrade and self.boundary == Boundary.REALIZED:
            raise ValueError('nontrade_is_not_realized_money')
        if self.boundary == Boundary.COUNTERFACTUAL:
            if self.label != 'SIMULATED / UNREALIZED' or self.observed_ms <= self.decision_ms:
                raise ValueError('counterfactual_label_and_future_clock_required')
            if 'net_pnl' in json.loads(self.observation_json):
                raise ValueError('use_simulated_net_pnl_not_realized_pnl')

    @property
    def outcome_id(self):
        return digest(asdict(self))


DIMENSIONS = ('hypothesis_research', 'strategy_version', 'regime_world',
              'portfolio_interaction', 'sizing', 'execution_quality', 'costs',
              'risk_intervention', 'data_quality')


@dataclass(frozen=True)
class Attribution:
    outcome_id: str
    dimensions: tuple
    causal_claims: tuple = ()

    @property
    def attribution_id(self):
        return digest(asdict(self))


def attribute(outcome):
    # Lineage establishes identities only. It does not identify causes.
    l = outcome.lineage
    operational = outcome.kind in (Kind.EXECUTION, Kind.DATA, Kind.INCIDENT)
    present = dict(hypothesis_research=outcome.kind == Kind.RESEARCH,
        strategy_version=bool(l.version_id), regime_world=bool(l.world_id),
        portfolio_interaction=bool(l.proposal_id), sizing=bool(l.intent_id),
        execution_quality=bool(l.execution_ids), costs=bool(l.execution_ids),
        risk_intervention=bool(l.risk_decision_id), data_quality=bool(outcome.sources))
    return Attribution(outcome.outcome_id, tuple((d, Support.NA if operational and d in ('hypothesis_research', 'strategy_version', 'regime_world') else Support.DESCRIPTIVE if present[d]
        else Support.NA if d in ('execution_quality', 'costs') and outcome.kind != Kind.EXECUTED
        else Support.UNKNOWN, 'identity/observation only; causal effect unknown') for d in DIMENSIONS))


PRE_DECISION = ('data', 'world', 'context', 'strategy', 'risk_config', 'portfolio', 'decision', 'reasons')
REQUIRED = PRE_DECISION + ('action', 'outcome')


@dataclass(frozen=True)
class Replay:
    outcome_id: str
    status: str
    faults: tuple
    reconstructed_json: str

    @property
    def replay_id(self):
        return digest(asdict(self))


def replay(outcome, retained):
    """Reconstruct exact referenced snapshots; never substitute current state."""
    if any(s.role == 'capture_manifest' for s in outcome.sources):
        from trader.learning.capture import replay_manifest
        return replay_manifest(outcome, retained)
    faults, snapshots = [], {}
    refs = {s.role: s for s in outcome.sources}
    for role in tuple(dict.fromkeys(REQUIRED + tuple(refs))):
        ref = refs.get(role)
        if ref is None:
            faults.append('missing:' + role)
            continue
        value = retained.get((ref.source_id, ref.version))
        if value is None:
            faults.append('missing_source:' + role)
            continue
        if digest(value) != ref.sha256:
            faults.append('tampered:' + role)
            continue
        if ref.available_ms is None:
            faults.append('unknown_source_availability:' + role)
            snapshots[role] = value
            continue
        if ref.available_ms > outcome.observed_ms:
            faults.append('unavailable_at_outcome:' + role)
        if role in PRE_DECISION and ref.available_ms > outcome.decision_ms:
            faults.append('future_source:' + role)
        if role == 'outcome' and not outcome.decision_ms <= ref.available_ms <= outcome.observed_ms:
            faults.append('outcome_clock')
        if role == 'outcome' and outcome.boundary == Boundary.COUNTERFACTUAL and ref.available_ms <= outcome.decision_ms:
            faults.append('counterfactual_measurement_not_after_decision')
        snapshots[role] = value
    l = outcome.lineage
    for key in ('cycle_id', 'decision_id', 'context_id', 'world_id'):
        if not getattr(l, key):
            faults.append('missing_lineage:' + key)
    if 'data' in snapshots and snapshots['data'].get('cutoff_ms', outcome.decision_ms) > outcome.decision_ms:
        faults.append('future_data_cutoff')
    bindings = {'decision': ('decision_id', l.decision_id), 'context': ('context_id', l.context_id),
                'world': ('world_id', l.world_id), 'strategy': ('version_id', l.version_id)}
    for role, (key, expected) in bindings.items():
        if role in snapshots and (expected is None or snapshots[role].get(key) != expected):
            faults.append('lineage:' + role)
    if 'decision' in snapshots and snapshots['decision'].get('cycle_id') != l.cycle_id:
        faults.append('lineage:cycle')
    if 'strategy' in snapshots:
        if snapshots['strategy'].get('spec_hash') != l.spec_hash:
            faults.append('lineage:spec_hash')
        if snapshots['strategy'].get('strategy_id') != l.strategy_id:
            faults.append('lineage:strategy_id')
    if l.opportunity_id and snapshots.get('context', {}).get('opportunity_id') != l.opportunity_id:
        faults.append('lineage:opportunity')
    optional_bindings = (('proposal', 'proposal_id', l.proposal_id), ('intent', 'intent_id', l.intent_id),
                         ('risk_decision', 'risk_decision_id', l.risk_decision_id))
    for role, key, expected in optional_bindings:
        if expected and (role not in snapshots or snapshots[role].get(key) != expected):
            faults.append('lineage:' + role)
    for role, key, expected in (('execution', 'execution_ids', l.execution_ids), ('trade', 'trade_ids', l.trade_ids)):
        if expected and (role not in snapshots or tuple(snapshots[role].get(key, ())) != expected):
            faults.append('lineage:' + role)
    if 'outcome' in snapshots and snapshots['outcome'].get('observation') != json.loads(outcome.observation_json):
        faults.append('outcome_observation_differs')
    # Real money must use the existing verified execution contract.
    if outcome.boundary == Boundary.REALIZED and outcome.kind == Kind.EXECUTED and 'outcome' in snapshots:
        from trader.cognition import outcomes as O
        try:
            receipt = O.replay(snapshots['outcome']['typed_receipt'])
            if receipt['actual_execution']['status'] != 'verified_actual':
                raise ValueError('actual_not_verified')
            trade_id = receipt['source'].get('registration', {}).get('trade_id')
            if trade_id not in l.trade_ids:
                raise ValueError('trade_lineage_differs')
            if receipt['actual_execution']['net_pnl'] != json.loads(outcome.observation_json).get('net_pnl'):
                raise ValueError('money_differs')
        except (ValueError, KeyError, TypeError):
            faults.append('realized_accounting_unverified')
    if 'decision_manifest' not in snapshots:
        faults.append('decision_manifest_missing_no_backfill')
    else:
        from .decision_sources import verify
        wrapper=snapshots['decision_manifest']
        reg=wrapper['registration']
        if reg['decision_ms']!=outcome.decision_ms or reg['lineage']!=json.loads(canonical(asdict(outcome.lineage))) or (reg['profile']=='RESEARCH' and outcome.kind!=Kind.RESEARCH):
            faults.append('decision_manifest_outcome_binding_differs')
        declared={d['role']:d for d in reg['dependencies']}
        for source in outcome.sources:
            if source.role in PRE_DECISION:
                dep=declared.get(source.role,{})
                if any(dep.get(k)!=getattr(source,k) for k in ('source_id','version','sha256','available_ms')):
                    faults.append('decision_manifest_source_binding:'+source.role)
        faults.extend(verify(wrapper['manifest'],reg,snapshots))
    return Replay(outcome.outcome_id, 'INCOMPLETE' if faults else 'COMPLETE', tuple(faults), canonical(snapshots))


@dataclass(frozen=True)
class LearningEvidence:
    outcome_id: str
    attribution_id: str
    replay_id: str
    observed_json: str
    supported: tuple
    unknown: tuple
    scope_json: str
    quality: str
    provenance: tuple
    eligible_targets: tuple
    historical_json: str

    @property
    def evidence_id(self):
        return digest(asdict(self))


def evidence(outcome, attribution, reconstruction):
    snapshots = json.loads(reconstruction.reconstructed_json)
    retained = {(s.source_id, s.version): snapshots[s.role] for s in outcome.sources if s.role in snapshots}
    if attribution != attribute(outcome) or reconstruction != replay(outcome, retained):
        raise ValueError('learning_chain_differs')
    authoritative = reconstruction.status == 'COMPLETE' and outcome.boundary not in (Boundary.UNRESOLVED, Boundary.UNASSESSABLE)
    targets = (Target.LIFECYCLE,) if authoritative and outcome.kind == Kind.RESEARCH else ()
    return LearningEvidence(outcome.outcome_id, attribution.attribution_id, reconstruction.replay_id,
        outcome.observation_json, ('exact observed receipt only',) if authoritative else (),
        ('causality', 'predictive confidence', 'independent sample sufficiency'),
        canonical(asdict(outcome.lineage)), 'VERIFIED_REPLAY' if authoritative else 'NON_AUTHORITATIVE',
        tuple((s.source_id, s.version, s.sha256) for s in outcome.sources), targets,
        reconstruction.reconstructed_json)


DECAY_RULE = 'existing-recent-decay.v1'


@dataclass(frozen=True)
class RegisteredRule:
    rule_id: str
    target: Target
    evaluator: str
    required_policy: tuple
    proposed_states: tuple
    requirements: tuple


REGISTERED_RULES = MappingProxyType({DECAY_RULE: RegisteredRule(DECAY_RULE,
    Target.LIFECYCLE, 'trader.strategy.rolling.has_decayed',
    ('decay_recent_days', 'decay_min_trades', 'decay_floor_pf'), ('RETIRED',),
    ('exact historical snapshots and evaluator', 'complete declared universe',
     'existing configured sample minimum', 'recomputed matching evaluation',
     'same current strategy version', 'existing Strategy Governor authority'))})
# Version the actual evaluator dependency bytes. A mismatch refuses rather than
# executing today's implementation on yesterday's inputs.
DECAY_FILES = ('trader/strategy/rolling.py', 'trader/strategy/vector_backtest.py',
    'trader/strategy/compile.py', 'trader/strategy/spec.py', 'trader/strategy/dsl.py',
    'trader/strategy/features.py', 'trader/strategy/features_deriv.py',
    'trader/strategy/features_xs.py', 'trader/strategy/geometries.py')


def code_manifest():
    root = Path(__file__).resolve().parents[2]
    # Include the whole evaluator package and its indicator/type dependencies.
    files = sorted(set(DECAY_FILES) | {str(p.relative_to(root)) for p in (root / 'trader/strategy').glob('*.py')}
        | {'trader/agents/indicators.py', 'trader/core/types.py', 'trader/learning/foundation.py'})
    import sys, numpy, pandas
    return dict(files={p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in files},
                runtime=dict(python=sys.version, numpy=numpy.__version__, pandas=pandas.__version__))


@dataclass(frozen=True)
class LearningUpdateProposal:
    evidence_id: str
    target: Target
    current_json: str
    proposed_json: str | None
    rule: str | None
    rule_version: str | None
    status: Status
    basis: tuple
    reason: str

    @property
    def proposal_id(self):
        return digest(asdict(self))


def propose(ev, target, current, *, rule=None, conflicting=False):
    if not isinstance(target, Target):
        raise ValueError('unapproved_update_target')
    status, proposed, why = Status.UNREGISTERED, None, 'no registered deterministic rule for target'
    history = json.loads(ev.historical_json)
    if ev.quality != 'VERIFIED_REPLAY' or not verified_history(ev):
        status, why = Status.INCOMPLETE, 'authoritative historical replay unavailable'
    elif rule != DECAY_RULE or target != Target.LIFECYCLE:
        pass
    elif conflicting:
        status, why = Status.CONFLICTING, 'conflicting evidence requires authority review'
    elif target not in ev.eligible_targets:
        status, why = Status.INSUFFICIENT, 'execution and single-trade evidence are not lifecycle evaluation'
    else:
        status, why, proposed = evaluate_decay(history, current)
    return LearningUpdateProposal(ev.evidence_id, target, canonical(current),
        canonical(proposed) if proposed is not None else None, rule,
        digest(code_manifest()) if rule == DECAY_RULE else None, status,
        (ev.outcome_id, ev.attribution_id, ev.replay_id), why)


def verified_history(ev):
    """Every consumer re-verifies manifests; a quality string is not authority."""
    from . import capture as C, decision_sources as D
    try:
        history=json.loads(ev.historical_json)
        if 'capture_manifest' in history:
            m=history['capture_manifest']; b=m['capture']; reg=b['registration']
            lineage=Lineage(**dict(b['lineage'],execution_ids=tuple(b['lineage'].get('execution_ids',())),trade_ids=tuple(b['lineage'].get('trade_ids',()))))
            src=Source('capture_manifest',m['manifest_id'],C.SCHEMA,digest(m),b['observed_ms'])
            outcome=Outcome(Kind(b['kind']),Boundary(b['boundary']),lineage,reg['decision_ms'],b['observed_ms'],(src,),canonical(b['observation']),b['label'])
            r=C.replay_manifest(outcome,{(src.source_id,src.version):m})
            return r.status=='COMPLETE' and ev==evidence(outcome,attribute(outcome),r)
        wrapper=history['decision_manifest']; reg=wrapper['registration']
        if D.verify(wrapper['manifest'],reg,history): return False
        refs=wrapper['outcome_sources']
        sources=tuple(Source(**s) for s in refs)
        # The wrapper excludes its own reference to avoid a circular hash.
        sources += (Source('decision_manifest',wrapper['manifest']['manifest_id'],D.SCHEMA,digest(wrapper),reg['decision_ms']),)
        outcome=Outcome(Kind(wrapper['kind']),Boundary(wrapper['boundary']),Lineage(**dict(reg['lineage'],execution_ids=tuple(reg['lineage'].get('execution_ids',())),trade_ids=tuple(reg['lineage'].get('trade_ids',())))),reg['decision_ms'],wrapper['observed_ms'],sources,wrapper['observation_json'],wrapper['label'])
        retained={(s.source_id,s.version):history[s.role] for s in sources}
        r=replay(outcome,retained)
        return r.status=='COMPLETE' and ev==evidence(outcome,attribute(outcome),r)
    except (ValueError,KeyError,TypeError):
        return False


def evaluate_decay(history, current):
    from trader.strategy import rolling, factory_handoff as F
    from trader.strategy.compile import compile_spec
    from trader.strategy.spec import StrategySpec
    import pandas as pd
    from trader.core.types import TF_MS
    try:
        measurement = history['outcome']
        if measurement.get('protocol') != DECAY_RULE:
            return Status.INSUFFICIENT, 'no exact registered decay measurement', None
        if measurement['code_manifest'] != code_manifest():
            return Status.STALE, 'historical evaluator unavailable; current code cannot substitute', None
        version, config, data = history['strategy'], history['risk_config'], history['data']
        if F._jsha(version['spec']) != version['spec_hash']:
            return Status.STALE, 'historical spec hash differs', None
        policy = config['strategies']
        # No invented default sample threshold or policy values.
        days, count, floor = (policy[k] for k in ('decay_recent_days', 'decay_min_trades', 'decay_floor_pf'))
        if type(count) is not int or count <= 0 or days <= 0 or floor <= 0:
            raise ValueError('invalid_registered_policy')
        spec = StrategySpec.from_dict(version['spec'])
        frames = {s: pd.DataFrame(rows) for s, rows in data['frames'].items()}
        compiled = compile_spec(spec)
        if set(compiled.spec.data_requires) - {'ohlcv'}:
            return Status.INSUFFICIENT, 'required non-OHLCV sources not reconstructed', None
        for frame in frames.values():
            frame['ts'] = pd.to_datetime(frame['ts'], utc=True)
            if not frame['ts'].is_monotonic_increasing or frame['ts'].duplicated().any():
                raise ValueError('invalid_historical_bar_order')
            if (frame['ts'].astype('int64') // 1000000 + TF_MS[spec.timeframe] > data['cutoff_ms']).any():
                raise ValueError('bar_not_available_at_cutoff')
        declared = spec.universe.get('include', []) if isinstance(spec.universe, dict) else []
        if not declared or set(declared) != {s for s in frames if not s.startswith('_')}:
            return Status.INSUFFICIENT, 'complete declared universe required', None
        # This first adapter does not reconstruct derivative/ref provider state.
        if data.get('unsupported_inputs') or config['risk'].get('funding_source') or config['risk'].get('real_funding') is not False:
            return Status.INSUFFICIENT, 'external input replay not registered', None
        diag = {}
        dead, observed = rolling.has_decayed(compiled, frames, config['risk'], spec.timeframe,
            recent_days=days, min_trades=count, floor_pf=floor, diagnostics=diag)
        if observed != measurement['evaluation']:
            return Status.CONFLICTING, 'recomputed outcome differs', None
        if (diag.get('errored') or diag.get('no_frame') or diag.get('insufficient_history')
                or not diag.get('scored') or observed['trades'] < count
                or any(v.get('missing_context') or v.get('missing_context_error')
                       or v.get('scoring', {}).get('unavailable') or not v.get('scoring', {}).get('scorable_bars') for v in diag['scored'].values())):
            return Status.INSUFFICIENT, 'existing policy sample/coverage requirements unmet', None
        if set(current) != {'version_id', 'spec_hash', 'state'}:
            return Status.STALE, 'lifecycle target cannot contain Risk/spec/allocation mutations', None
        if current['version_id'] != version['version_id'] or current['spec_hash'] != version['spec_hash']:
            return Status.STALE, 'current exact version differs', None
        if current['state'] not in F.GOVERNOR_ALLOWED and current['state'] != F.SHADOW:
            return Status.STALE, 'current lifecycle is not governable', None
        if not dead:
            return Status.INSUFFICIENT, 'existing rule does not justify retirement', None
        return Status.APPLICABLE, 'existing recent decay predicate replayed; retirement only', dict(current, state=F.RETIRED)
    except (KeyError, TypeError, ValueError, AttributeError):
        return Status.INSUFFICIENT, 'registered decay inputs or sufficiency policy missing', None


def apply_to_isolated_journal(journal, outcome, sources, proposal, *, at_ms):
    """Exercise existing authority on an in-memory clone only; never live state.

    An application API for production is deliberately absent from R1.
    """
    from trader.strategy import factory_handoff as F
    if type(at_ms) is not int or at_ms < outcome.observed_ms:
        raise ValueError('application_clock_before_outcome')
    if any(row['file'] for row in journal.query('PRAGMA database_list')):
        raise ValueError('isolated_in_memory_journal_required')
    r = replay(outcome, sources)
    ev = evidence(outcome, attribute(outcome), r)
    current = json.loads(proposal.current_json)
    expected = propose(ev, proposal.target, current, rule=proposal.rule)
    if proposal != expected or proposal.status != Status.APPLICABLE:
        raise ValueError('proposal_not_applicable_or_verified')
    actual = dict(current, state=F.state_of(journal, current['version_id']))
    if actual != current:
        if (actual['state'] == F.RETIRED and any(e.get('reason_code') == proposal.proposal_id
                for e in F.events(journal, current['version_id']) + F.governor_events(journal, current['version_id']))):
            return 'DUPLICATE'
        raise ValueError('stale_authority_state')
    v = F.load_version(journal, current['version_id'])
    if v['spec_hash'] != current['spec_hash']:
        raise ValueError('authority_version_differs')
    F.retire_version(journal, current['version_id'], F.RETIRED,
        reason_code=proposal.proposal_id, actor='strategy_governor', at_ms=at_ms)
    return 'APPLIED'


def save_immutable(directory, record):
    """Detached artifacts only; retry is byte-equal, conflicts never overwrite."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    body = asdict(record)
    path = directory / (digest(body) + '.json')
    text = canonical(body) + '\n'
    try:
        with path.open('x') as f:
            f.write(text)
    except FileExistsError:
        if path.read_text() != text:
            raise ValueError('immutable_learning_collision')
    return path


def exact_failed_research(journal, bank_id, ev):
    """Recall one exact Research Bank identity, preserving context-only status."""
    from trader.cognition import research_bank as B
    if not any(identity == bank_id for identity, _, _ in ev.provenance):
        raise ValueError('bank_not_bound_to_learning_evidence')
    rows = journal.query('SELECT * FROM research_bank_objects WHERE bank_object_id=?', (bank_id,))
    if len(rows) != 1:
        raise ValueError('exact_bank_object_missing')
    if not any(identity == bank_id and sha == rows[0]['canonical_sha256'] for identity, _, sha in ev.provenance):
        raise ValueError('bank_evidence_hash_differs')
    rec, _ = B.verify_row(journal, rows[0])
    if rec['result_status'] not in ('REFUTED', 'INCONCLUSIVE', 'FAILED'):
        raise ValueError('not_a_failed_or_inconclusive_research_result')
    return dict(bank_object_id=bank_id, result_status=rec['result_status'],
                result_reason=rec['result_reason'], scope=rec['scope'], authority='context_only',
                same_claim='NOT_ESTABLISHED_WITHOUT_REGISTERED_CLAIM_IDENTITY')


def outcome_from_payload(raw):
    return Outcome(Kind(raw['kind']), Boundary(raw['boundary']),
        Lineage(**dict(raw['lineage'], execution_ids=tuple(raw['lineage']['execution_ids']),
                       trade_ids=tuple(raw['lineage']['trade_ids']))),
        raw['decision_ms'], raw['observed_ms'], tuple(Source(**s) for s in raw['sources']),
        raw['observation_json'], raw['label'])


def prior_failed_for_question(journal, question_id, *, max_objects):
    """Existing structured prior recall; equality is explicit, never fuzzy.

    Matching protocol, spec and simulation settings identifies a repeated
    structured question context; it does not prove a registered claim identity.
    """
    from trader.cognition import research_recall as R
    receipt = R.recall(journal, question_id, max_objects=max_objects)
    keys = ('source_health_fingerprint', 'source_spec_fingerprint',
            'source_spec_content_sha256', 'source_simulation_settings_sha256')
    prior = [dict(p, structured_context_equal=all(p['identity_vs_question'].get(k) == R.EQUAL for k in keys))
             for p in receipt['prior'] if p['result_status'] in ('REFUTED', 'INCONCLUSIVE', 'FAILED')]
    return dict(receipt, prior=prior, suppression_authority=False)


def refuse_legacy_learning(consumer):
    """Legacy statistics have no verified replay/proposal authority adapter.

    Even a complete receipt cannot authorize an unregistered weight/confidence
    rule. Keep these writers closed until an explicit integration is verified.
    Descriptive collection and existing stored state remain available.
    """
    raise ValueError(f'replay_complete_learning_authority_required:{consumer}')
