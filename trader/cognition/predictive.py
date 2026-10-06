"""Proposal contracts for the Research Bank -> quantitative research bridge.

No LLM, statistical verdict, strategy admission or execution lives here.
The vocabulary names questions to test, never claims that a mechanism works.
"""
from dataclasses import asdict, dataclass
import hashlib
import json

from trader.cognition import investigation as I
from trader.observability import investigation_research as R
from trader.research import vocab
from trader.research.universe import DISCOVERY, HELDOUT

SCHEMA = 'predictive-hypothesis.v1'
QUESTION, HYPOTHESIS, SUPPORTED_RESULT, STRATEGY_CANDIDATE = (
    'QUESTION', 'HYPOTHESIS', 'SUPPORTED_RESULT', 'STRATEGY_CANDIDATE')
CLASSIFICATIONS = ('SUPPORTED', 'UNSUPPORTED', 'INCONCLUSIVE', 'NOT_TESTABLE', 'DATA_INSUFFICIENT')
MAX_HYPOTHESES = 2
# The first template tests a NEW proposed price relationship, not the source's
# volume-persistence outcome. The second retains that exact non-price question
# and explicitly cannot be expressed by the current trading-rule planner.
TRANSFORMS = {
    'volume_breakout_context.v1': {
        'mechanism': 'QUESTION: does unusually high registered volume context improve the registered breakout expression? Participant cause is unknown.',
        'predictor': ('volz96>p90', 'ev:donch100'),
        'target': 'net trade returns under registered fixed exit geometry',
        'horizon': 'fixed:max_bars=96', 'shape': 'combination',
    },
    'exact_volume_path.v1': {
        'mechanism': 'QUESTION: does the registered volume path repeat on new cases against an unconditional baseline?',
        'predictor': ('investigation:volume_anomaly:N20',),
        'target': 'registered volume path at five exact forward bars',
        'horizon': '4h:5', 'shape': 'volume_forward_path',
    },
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class ResearchSource:
    bank_id: str
    result_id: str
    investigation_id: str
    family: str
    catalog_id: str
    protocol_id: str
    symbol: str
    timeframe: str
    horizon: int
    as_of_ms: int
    available_ms: int
    classification: str
    registration_evidence: tuple[str, ...]
    source_versions: tuple[str, ...]
    source_manifest_json: str
    context_json: str
    prior_research_json: str
    retained_chain_json: str
    provenance_hash: str


def source_from_chain(chain, run, recorded_ms):
    q, e, result, bank = chain['question'], run['evidence'], run['result'], run['bank']
    inv = I.investigation_from_dict(json.loads(e['frozen']['case_payload']))
    manifest = json.loads(inv.state.code_json)
    if (q['source']['catalog_id'] != I.CATALOG_ID or result['predicates_id'] != R.PREDICATES_ID
            or result['schema'] != R.RESULT_SCHEMA or bank['schema'] != R.BANK_SCHEMA):
        raise ValueError('stale_or_incompatible_source')
    if (not inv.state.evidence_ids or not inv.state.input_versions or not manifest
            or any(not isinstance(v, str) or len(v) != 64 for v in manifest.values())
            or not q['source']['registration_context_id']):
        raise ValueError('missing_provenance')
    if result.get('predictive_edge_established') is not False or result.get('trading_authority') != 'NONE':
        raise ValueError('descriptive_result_claiming_predictive_authority')
    updates = e['frozen']['update_chain']
    last = json.loads(updates[-1]['payload'])
    if type(recorded_ms) is not int or not inv.registered_ms <= last['observed_ms'] <= recorded_ms:
        raise ValueError('source_registration_chronology')
    available = max(recorded_ms, last['observed_ms'], last['evidence']['available_ms'] or 0)
    fields = dict(bank_id=bank['bank_object_id'], result_id=result['result_id'],
        investigation_id=inv.investigation_id, family=R.FAMILY, catalog_id=I.CATALOG_ID,
        protocol_id=R.PREDICATES_ID, symbol=inv.state.symbol, timeframe='4h', horizon=I.H,
        as_of_ms=last['as_of_ms'], available_ms=available, classification=result['status'],
        registration_evidence=tuple(inv.state.evidence_ids),
        source_versions=tuple(sorted(set(inv.state.input_versions) | set(last['input_ids']))),
        source_manifest_json=canonical(manifest), context_json=canonical(q['context']),
        prior_research_json='[]', retained_chain_json=canonical({**run, 'question':q, 'plan':chain['plan']}))
    return ResearchSource(**fields, provenance_hash=digest(fields))


@dataclass(frozen=True)
class PredictiveHypothesis:
    hypothesis_id: str
    source_research_ids: tuple[str, ...]
    bank_id: str
    transformation: str
    mechanism_description: str
    predictor_definition: tuple[str, ...]
    target_definition: str
    direction: str | None
    horizon: str
    asset_scope: tuple[str, ...]
    regime_context_scope: str
    falsification_criteria: str
    required_data: tuple[str, ...]
    forbidden_leakage_boundary: str
    generation_end_ms: int
    created_at: int
    source_hash: str
    provenance_json: str
    semantic_hash: str
    provenance_hash: str
    stage: str = HYPOTHESIS
    schema: str = SCHEMA


def propose(source, transformation, now_ms):
    if transformation not in TRANSFORMS:
        raise ValueError('unregistered_transformation')
    if type(now_ms) is not int or now_ms < source.available_ms:
        raise ValueError('source_not_available')
    t = TRANSFORMS[transformation]
    context = json.loads(source.context_json)
    semantic = dict(transformation=transformation, predictor=t['predictor'], target=t['target'],
        horizon=t['horizon'], asset_scope=tuple(DISCOVERY + HELDOUT),
        context=context.get('world_model'), source_instrument=source.symbol,
        source_protocol=source.protocol_id)
    fields = dict(source_research_ids=(source.result_id, source.investigation_id), bank_id=source.bank_id,
        transformation=transformation, mechanism_description=t['mechanism'],
        predictor_definition=t['predictor'], target_definition=t['target'], direction=None,
        horizon=t['horizon'], asset_scope=tuple(DISCOVERY + HELDOUT),
        regime_context_scope=source.context_json,
        falsification_criteria='Existing discovery control, ablation, power, rotation-null and protected referee gates must support the registered expression; unavailable power is inconclusive.',
        required_data=('ohlcv',), forbidden_leakage_boundary='Generation bars and every value known at creation are excluded from tuning and protected scores. Thresholds use discovery only. Protected A/B are referee-only. No source result is a validation observation.',
        generation_end_ms=now_ms, created_at=now_ms, source_hash=source.provenance_hash,
        provenance_json=canonical(dict(generator='deterministic-registered-templates.v1',
            template_hash=digest(t), source=asdict(source), authority='PROPOSAL_ONLY', llm=None)),
        semantic_hash=digest(semantic))
    return PredictiveHypothesis(hypothesis_id=digest(fields), provenance_hash=digest(fields), **fields)


def validate(h):
    if h.stage != HYPOTHESIS or h.schema != SCHEMA:
        raise ValueError('proposal_stage_required')
    if h.transformation not in TRANSFORMS:
        raise ValueError('unregistered_transformation')
    fields = {k: v for k, v in asdict(h).items() if k not in ('hypothesis_id', 'provenance_hash', 'stage', 'schema')}
    if digest(fields) != h.provenance_hash or h.hypothesis_id != h.provenance_hash:
        raise ValueError('hypothesis_integrity')
    raw = json.loads(h.provenance_json)
    s = source_from_dict(raw['source'])
    if propose(s, h.transformation, h.created_at) != h:
        raise ValueError('hypothesis_template_mismatch')


def source_from_dict(raw):
    d = dict(raw)
    for k in ('registration_evidence', 'source_versions'):
        d[k] = tuple(d[k])
    s = ResearchSource(**d)
    if digest({k: v for k, v in asdict(s).items() if k != 'provenance_hash'}) != s.provenance_hash:
        raise ValueError('source_integrity')
    retained = json.loads(s.retained_chain_json)
    R.verify_evidence(retained['evidence'])
    q, plan, ev, result = (retained[k] for k in ('question','plan','evidence','result'))
    run = R.build_run(q,plan,ev,result)
    if (R.build_plan(q) != plan or R.build_result(ev) != result
            or run != retained['run'] or R.build_bank(q,plan,ev,result,run,
                builder_id=retained['bank']['builder_id']) != retained['bank']):
        raise ValueError('source_chain_integrity')
    for record in (q,plan,ev,result,run,retained['bank']):
        key = R._ID_KEY[record['schema']]
        if record[key] != digest({k:v for k,v in record.items() if k!=key}):
            raise ValueError('source_record_integrity')
    rebuilt = source_from_chain(retained,retained,s.available_ms)
    if replace_source_prior(rebuilt, s.prior_research_json) != s:
        raise ValueError('source_projection_integrity')
    return s


def replace_source_prior(source, prior_json):
    fields=asdict(source)
    fields['prior_research_json']=prior_json
    fields.pop('provenance_hash')
    return ResearchSource(**fields, provenance_hash=digest(fields))


def hypothesis_from_dict(raw):
    d = dict(raw)
    for k in ('source_research_ids', 'predictor_definition', 'asset_scope', 'required_data'):
        d[k] = tuple(d[k])
    h = PredictiveHypothesis(**d)
    validate(h)
    return h


def parts(h, gauges):
    validate(h)
    if TRANSFORMS[h.transformation]['shape'] != 'combination':
        raise ValueError('UNSUPPORTED_EXPERIMENT_SHAPE')
    known = {p.key: p for p in vocab.parts_for('4h', gauges)}
    if any(key not in known for key in h.predictor_definition):
        raise ValueError('DATA_INSUFFICIENT')
    return tuple(known[key] for key in h.predictor_definition)


def next_questions(source, classification=None, prior=()):
    """Bounded questions only; no failed-once suppression or recursive enqueue."""
    status = classification or source.classification
    templates = {
        'SUPPORTED': 'Does this finding replicate on untouched cases and survive its registered baseline?',
        'UNSUPPORTED': 'Does normalization rather than persistence explain the mechanism in a materially different registered context?',
        'REFUTED': 'Does a different registered horizon distinguish normalization from persistence?',
        'INCONCLUSIVE': 'Which missing discriminator or powered data window can separate the competing mechanisms?',
        'NOT_TESTABLE': 'Can the exact target and predictor be expressed by a registered experiment shape?',
        'DATA_INSUFFICIENT': 'Can new point-in-time data supply the existing evidence requirements without revisiting protected evidence?',
    }
    if status not in templates:
        raise ValueError('unregistered_result_classification')
    questions = [templates[status]]
    context = json.loads(source.context_json)
    if any(p.get('classification') == 'UNSUPPORTED' for p in prior):
        questions.append('Which registered alternative explains the repeated failure; what materially different context would discriminate it?')
    inv = json.loads(json.loads(source.retained_chain_json)['evidence']['frozen']['case_payload'])
    if inv['state']['contradictions']:
        questions.append('Which registered discriminator can resolve the retained contradictory context observations?')
    if context.get('world_model', {}).get('status') != 'OK':
        questions.append('Which timestamped context measurement could distinguish the unexplained regime difference?')
    frozen_prior=json.loads(source.prior_research_json)
    return [dict(question_id=digest([source.bank_id, status, text, prior, frozen_prior]), stage=QUESTION,
                 text=text, source_bank_id=source.bank_id, classification=status,
                 prior=list(prior), registration_prior_research=frozen_prior,
                 authority='QUESTION_ONLY', suppression=None)
            for text in questions[:MAX_HYPOTHESES]]
