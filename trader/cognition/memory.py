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
