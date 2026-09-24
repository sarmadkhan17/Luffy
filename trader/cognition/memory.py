"""Versioned investigation memory. Non-economic observations, never admission evidence."""
from dataclasses import asdict, dataclass
import json

from trader.cognition import investigation as I
from trader.cognition.contracts import stable_id

SCHEMA = 'investigation-memory.v1'
MAX_RETRIEVED = 3


@dataclass(frozen=True)
class CaseMemory:
    case_id: str
    source_id: str
    source_event_id: str
    source_schema: str
    catalog_id: str
    config_id: str
    family: str
    sign: int
    symbol: str
    cohort: tuple[str, ...]
    contradictions: tuple[str, ...]
    registered_ms: int
    resolved_ms: int
    available_ms: int
    recorded_ms: int
    winner: str
    measurement: dict
    evidence: dict
    outcome_type: str = 'non_economic_observation'
    schema_version: str = SCHEMA


def verified_case(inv, update, bars, recorded_ms):
    """Reconstruct baseline and exact terminal measurement; reject forged joins."""
    if inv.schema_version != I.SCHEMA or inv.measurement.catalog_id != I.CATALOG_ID:
        raise ValueError('memory_protocol_mismatch')
    e = update.evidence
    if e.status != 'measured' or update.investigation_id != inv.investigation_id:
        raise ValueError('memory_requires_measured_outcome')
    if not inv.registered_ms < e.as_of_ms <= e.observed_ms <= recorded_ms:
        raise ValueError('memory_invalid_chronology')
    baseline = tuple(b for b in bars if b.version_id in inv.state.input_versions)
    if set(b.version_id for b in baseline) != set(inv.state.input_versions):
        raise ValueError('memory_missing_baseline_versions')
    rebuilt = I.open_investigation(inv.state, inv.primary_trigger, baseline, inv.registered_ms)
    if rebuilt != inv:
        raise ValueError('memory_frozen_registration_mismatch')
    wanted = {v for _, _, v in e.target_versions}
    targets = tuple(b for b in bars if b.version_id in wanted)
    if len(targets) != len(wanted) or I.measure(inv, targets, e.as_of_ms, e.observed_ms) != e:
        raise ValueError('memory_terminal_replay_mismatch')
    assessment = I.advance(inv, e).assessment
    if update.assessment != assessment:
        raise ValueError('memory_assessment_mismatch')
    winner = next(name for name, status in assessment if status == 'compatible')
    fields = dict(source_id=inv.investigation_id, source_event_id=update.event_id,
                  source_schema=inv.schema_version, catalog_id=inv.measurement.catalog_id,
                  config_id=inv.state.config_id, family=inv.primary_trigger, sign=inv.measurement.sign,
                  symbol=inv.state.symbol, cohort=inv.state.cohort, contradictions=inv.state.contradictions,
                  registered_ms=inv.registered_ms, resolved_ms=e.observed_ms,
                  available_ms=max(e.available_ms or 0, e.as_of_ms, e.observed_ms), recorded_ms=recorded_ms,
                  winner=winner, measurement=json.loads(I.encode(asdict(inv.measurement))), evidence=json.loads(I.encode(asdict(e))))
    return CaseMemory(case_id=stable_id('memory', fields), **fields)


def retrieve(inv, cases):
    """Freeze at registration. No nearest-date substitutions or later resolution."""
    included, audit = [], []
    for case in sorted(cases, key=lambda c: (-c.available_ms, c.case_id)):
        reason = 'compatible_prior_observable_path'
        if case.source_id == inv.investigation_id:
            reason = 'self_excluded'
        elif max(case.resolved_ms, case.available_ms, case.recorded_ms) >= inv.registered_ms:
            reason = 'not_known_before_registration'
        elif (case.schema_version != SCHEMA or case.source_schema != inv.schema_version or
              case.catalog_id != inv.measurement.catalog_id or case.config_id != inv.state.config_id or
              case.outcome_type != 'non_economic_observation'):
            reason = 'incompatible_version_or_outcome_type'
        elif (case.family != inv.primary_trigger or case.sign != inv.measurement.sign or
              case.contradictions != inv.state.contradictions or
              (case.family == 'relative_return_divergence' and case.cohort != inv.state.cohort)):
            reason = 'incompatible_trigger_or_context'
        elif len(included) >= MAX_RETRIEVED:
            reason = 'retrieval_capacity'
        else:
            included.append(case)
        audit.append({'case_id': case.case_id, 'reason': reason})
    cautions, tests = [], []
    for case in included:
        if case.winner == 'same_direction':
            caution = 'Earlier persistence does not establish repeatability or cause.'
            test = 'Counter-test normalization against persistence over the entire frozen window.'
        else:
            caution = 'An earlier compatible trigger failed to persist; do not presume continuation.'
            test = f'Explicitly compare persistence with {case.winner} over the entire frozen window.'
        cautions.append({'case_id': case.case_id, 'text': caution})
        tests.append({'case_id': case.case_id, 'test': test})
    fields = dict(schema_version=SCHEMA, investigation_id=inv.investigation_id,
                  cutoff_ms=inv.registered_ms, cases=[asdict(c) for c in included], audit=audit,
                  cautions=cautions, counter_tests=tests,
                  limitation='Analogues change questions, not probabilities, measured outcomes or trading authority.')
    return dict(context_id=stable_id('memory_context', fields), **fields)


def reasoning(update, context):
    """Concrete later evidence request, with an explicit no-memory comparator."""
    base = asdict(update.next_action)
    effective = dict(base)
    tests = context['counter_tests']
    if tests:
        effective['test'] += ' Memory counter-test: ' + ' '.join(dict.fromkeys(t['test'] for t in tests))
    return {'schema_version': SCHEMA, 'event_id': update.event_id,
            'context_id': context['context_id'], 'case_ids': [c['case_id'] for c in context['cases']] +
            [c['case_id'] for c in context.get('typed_outcomes', {}).get('cases', [])],
            'without_memory': base, 'with_memory': effective, 'changed': bool(tests),
            'reason': 'prior_resolved_case_counter_test' if tests else 'no_eligible_prior_case'}


def case_from_dict(d):
    return CaseMemory(**dict(d, cohort=tuple(d['cohort']), contradictions=tuple(d['contradictions'])))


# SDD-STAGE-3-INVESTIGATION-UNASSESSABLE-MEMORY-V1. Failed-assessment memory:
# a terminal not_testable closure and its exact frozen reason. Context only; it
# carries no score, winner, direction, probability, usefulness or falsification.
UNASSESSABLE_SCHEMA = 'investigation-unassessable-memory.v1'
UNASSESSABLE_TYPE = 'unassessable_closure'
UNASSESSABLE_CATALOGS = (I.CATALOG_ID, I.POSITIONING_CATALOG_ID, I.CORRELATION_CATALOG_ID)
_STATE_FIELDS = ('scan_id', 'symbol', 'as_of_ms', 'observed_ms', 'available_ms', 'cohort', 'membership_json',
                 'evidence_ids', 'input_versions', 'dimensions', 'transitions', 'contradictions', 'missing',
                 'code_json', 'config_id')


@dataclass(frozen=True)
class UnassessableMemory:
    case_id: str
    source_id: str
    source_event_id: str
    source_evidence_id: str
    source_episode_id: str
    source_scan_id: str
    source_state_id: str
    source_schema: str
    catalog_id: str
    config_id: str
    family: str
    symbol: str
    registered_ms: int
    resolved_ms: int
    available_ms: int
    recorded_ms: int
    status: str
    next_action: str
    reason: str
    reason_codes: tuple[str, ...]
    evidence: dict
    outcome_type: str = UNASSESSABLE_TYPE
    schema_version: str = UNASSESSABLE_SCHEMA


def verified_unassessable(inv, update, bars, recorded_ms):
    """Verify a terminal not_testable closure against its frozen registration; never partial."""
    if inv.schema_version != I.SCHEMA or inv.measurement.catalog_id not in UNASSESSABLE_CATALOGS:
        raise ValueError('unassessable_protocol_mismatch')
    family_catalog = {I.POSITIONING_FAMILY: I.POSITIONING_CATALOG_ID, I.CORRELATION_FAMILY: I.CORRELATION_CATALOG_ID}
    if (family_catalog.get(inv.primary_trigger, I.CATALOG_ID) != inv.measurement.catalog_id
            or inv.measurement.family != inv.primary_trigger):
        raise ValueError('unassessable_protocol_mismatch')
    e = update.evidence
    if update.investigation_id != inv.investigation_id or e.investigation_id != inv.investigation_id:
        raise ValueError('unassessable_case_mismatch')
    if e.status != 'not_testable' or update.next_action.kind != 'UNASSESSABLE' or update.next_action.reason != e.reason:
        raise ValueError('unassessable_requires_not_testable_closure')
    if not inv.registered_ms <= e.as_of_ms <= e.observed_ms <= recorded_ms or update.observed_ms != e.observed_ms:
        raise ValueError('unassessable_invalid_chronology')
    if stable_id('state', {k: getattr(inv.state, k) for k in _STATE_FIELDS}) != inv.state.state_id:
        raise ValueError('unassessable_state_identity_mismatch')
    baseline = tuple(b for b in bars if b.version_id in inv.state.input_versions)
    if set(b.version_id for b in baseline) != set(inv.state.input_versions):
        raise ValueError('unassessable_missing_baseline_versions')
    try:
        rebuilt = I.open_investigation(inv.state, inv.primary_trigger, baseline, inv.registered_ms)
    except ValueError:
        rebuilt = None
    if rebuilt != inv:
        raise ValueError('unassessable_frozen_registration_mismatch')
    wanted = {v for _, _, v in e.target_versions}
    targets = tuple(b for b in bars if b.version_id in wanted)
    if len(targets) != len(wanted) or I.measure(inv, targets, e.as_of_ms, e.observed_ms) != e:
        raise ValueError('unassessable_terminal_replay_mismatch')
    expected = I.advance(inv, e)
    if (update.assessment != expected.assessment or update.next_action != expected.next_action
            or not update.reason_codes or update.reason_codes[0] != e.reason):
        raise ValueError('unassessable_assessment_mismatch')
    fields = dict(source_id=inv.investigation_id, source_event_id=update.event_id, source_evidence_id=e.evidence_id,
                  source_episode_id=inv.episode_id, source_scan_id=inv.state.scan_id,
                  source_state_id=inv.state.state_id, source_schema=inv.schema_version,
                  catalog_id=inv.measurement.catalog_id, config_id=inv.state.config_id,
                  family=inv.primary_trigger, symbol=inv.state.symbol, registered_ms=inv.registered_ms,
                  resolved_ms=e.observed_ms, available_ms=max(e.available_ms or 0, e.as_of_ms, e.observed_ms),
                  recorded_ms=recorded_ms, status=e.status, next_action=update.next_action.kind,
                  reason=e.reason, reason_codes=update.reason_codes, evidence=json.loads(I.encode(asdict(e))))
    return UnassessableMemory(case_id=stable_id('unassessable_memory', fields), **fields)


def retrieve_unassessable(inv, records):
    """Exact-key recall frozen at registration. Context only: no counter_test,
    no salience, rank, allocation or registration effect."""
    included, audit = [], []
    for rec in sorted(records, key=lambda r: (-r.available_ms, r.case_id)):
        reason = 'compatible_prior_unassessable_closure'
        if rec.source_id == inv.investigation_id:
            reason = 'self_excluded'
        elif max(rec.resolved_ms, rec.available_ms, rec.recorded_ms) >= inv.registered_ms:
            reason = 'not_known_before_registration'
        elif (rec.schema_version != UNASSESSABLE_SCHEMA or rec.outcome_type != UNASSESSABLE_TYPE
              or rec.source_schema != inv.schema_version):
            reason = 'incompatible_version_or_outcome_type'
        elif rec.family != inv.primary_trigger:
            reason = 'incompatible_family'
        elif rec.catalog_id != inv.measurement.catalog_id or rec.config_id != inv.state.config_id:
            reason = 'incompatible_protocol_or_config'
        elif rec.symbol != inv.state.symbol:
            reason = 'incompatible_symbol'
        elif len(included) >= MAX_RETRIEVED:
            reason = 'retrieval_capacity'
        else:
            included.append(rec)
        audit.append({'case_id': rec.case_id, 'reason': reason})
    cautions = [{'case_id': r.case_id, 'source_id': r.source_id, 'kind': 'prior_unassessable_closure',
                 'family': r.family, 'catalog_id': r.catalog_id, 'config_id': r.config_id, 'symbol': r.symbol,
                 'reason': r.reason, 'reason_codes': list(r.reason_codes), 'authority': 'context_only',
                 'text': f'An earlier {r.family} case on {r.symbol} under the same protocol and config '
                         f'could not be assessed: {r.reason}.'} for r in included]
    return dict(schema_version=UNASSESSABLE_SCHEMA, records=[asdict(r) for r in included], audit=audit,
                cautions=cautions,
                limitation='A prior closure that could not be assessed is not evidence for or against this '
                           'case and does not change registration, priority or later retry.')


def unassessable_from_dict(d):
    return UnassessableMemory(**dict(d, reason_codes=tuple(d['reason_codes'])))
