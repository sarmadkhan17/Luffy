"""Content-bound RES-08 receipts around the registered quantitative machinery.

No new statistical test or admission rule is implemented here. Unsupported
translations and unavailable power have no passing/failing quantitative verdict.
"""
from dataclasses import asdict
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from trader.cognition import predictive as P
from . import predictive_experiment as E

SCHEMA = 'predictive-research-bank-result.v2'
STEP_SCHEMA = 'predictive-quantitative-step.v2'


def binding(ex):
    return dict(hypothesis_id=ex['source_hypothesis_id'], hypothesis_schema=ex['hypothesis']['schema'],
                experiment_id=ex['experiment_id'], experiment_schema=ex['schema'],
                split=ex['split'], evaluation_version=ex.get('evaluation_version'),
                data_provenance=ex.get('data_provenance'))


def semantic(value):
    """Worker timing is not scientific evidence or a new result version."""
    if isinstance(value, dict):
        return {k:semantic(v) for k,v in value.items() if k != 'elapsed_s'}
    if isinstance(value, list):
        return [semantic(v) for v in value]
    return value


def step(ex, fn, payload, res):
    body = dict(schema=STEP_SCHEMA, binding=binding(ex), job=fn.__name__,
                payload=semantic(payload), ok=res.ok, value=semantic(res.value), error=res.error)
    return dict(body, measurement_id=P.digest(body))


def validate_step(ex, receipt):
    from .combo import Combination
    if (receipt['schema'] != STEP_SCHEMA or receipt['binding'] != binding(ex)
            or receipt['measurement_id'] != P.digest({k:v for k,v in receipt.items() if k!='measurement_id'})):
        raise ValueError('quantitative_receipt_binding')
    payload = receipt['payload']
    if payload['cfg']['research']['predictive_split'] != ex['split']:
        raise ValueError('quantitative_wrong_cut')
    name = receipt['job']
    if name not in ('measure_job', 'evaluate_job', 'select_job', 'referee_job'):
        raise ValueError('unregistered_quantitative_worker')
    if receipt.get('acceptance')=='REFUSED':
        return
    if not receipt['ok'] or (receipt.get('value') or {}).get('error'):
        return
    value = receipt['value'] or {}
    if name in ('measure_job', 'referee_job') and value.get('cut_ms') != ex['split']['cut_ms']:
        raise ValueError('quantitative_wrong_cut')
    if name == 'referee_job':
        c = Combination.from_dict(payload['combo'])
        if (payload['cut_ms'] != ex['split']['cut_ms'] or value.get('hash') != c.hash
                or c.trigger != ex['source_hypothesis_id'] or c.evaluation_scope != ex['experiment_id']):
            raise ValueError('quantitative_referee_identity')
    if name == 'evaluate_job':
        allowed = {Combination.from_dict(c).hash for c in payload['combos']}
        for r in value.get('results') or []:
            if r['hash'] not in allowed:
                raise ValueError('quantitative_measurement_identity')


def snapshot(db, ex):
    # Only the experiment's own bound registered ledger can supply evidence.
    bank_path = Path(db.execute('PRAGMA database_list').fetchone()[2])
    path = bank_path.parent/(bank_path.stem+'-experiments')/ex['experiment_id']/'quantitative.db'
    with closing(E.readonly(path)) as numerical:
        numerical.row_factory = sqlite3.Row
        if numerical.execute('SELECT id FROM predictive_experiment_binding').fetchall()[0][0] != ex['experiment_id']:
            raise ValueError('quantitative_store_binding')
        budget = [dict(r) for r in numerical.execute('SELECT * FROM research_budget ORDER BY id')]
        tests = [dict(r) for r in numerical.execute('SELECT * FROM research_tests ORDER BY seq')]
        rows = [dict(r) for r in numerical.execute('SELECT * FROM research_candidates ORDER BY hash')]
        combos = [dict(r) for r in numerical.execute('SELECT hash,result FROM research_combos ORDER BY hash')]
    return dict(schema='predictive-registered-evidence.v1', binding=binding(ex),
                registered_budget=budget, spent_looks=tests, candidates=rows, results=combos)


def check_measured(ex, measured, receipts):
    if measured is None:
        return
    if measured.get('measurement_kind') == 'window_control':
        if measured.get('predictive_target_assessed') is not False or measured['step_receipt'] not in receipts:
            raise ValueError('quantitative_control_receipt')
        return
    if (measured.get('source_hypothesis_id') != ex['source_hypothesis_id']
            or measured.get('experiment_id') != ex['experiment_id']):
        raise ValueError('quantitative_measurement_identity')
    from .combo import Combination
    c=Combination.from_dict(measured['predictive_combo'])
    h=P.hypothesis_from_dict(ex['hypothesis'])
    if (c.hash!=measured.get('hash') or c.trigger!=h.hypothesis_id
            or c.evaluation_scope!=ex['experiment_id'] or c.keys!=tuple(sorted(h.predictor_definition))
            or c.tf!='4h' or c.geo!='fixed'):
        raise ValueError('quantitative_measurement_expression')
    raw = {k:v for k,v in measured.items() if k not in ('predictive_combo','source_hypothesis_id','experiment_id')}
    if not any(r['job']=='evaluate_job' and r['ok'] and r.get('acceptance')!='REFUSED' and
               raw in (r.get('value') or {}).get('results', []) for r in receipts):
        raise ValueError('quantitative_measurement_missing')


def validate_quantitative(ex, classification, measured, referee, evidence, receipts):
    from . import referee as registered, fdr, portfolio_null
    for receipt in receipts:
        validate_step(ex, receipt)
    check_measured(ex, measured, receipts)
    if evidence['schema']!='predictive-registered-evidence.v1' or evidence['binding'] != binding(ex):
        raise ValueError('quantitative_evidence_binding')
    if measured and measured.get('measurement_kind')!='window_control':
        retained=[semantic(json.loads(r['result'])) for r in evidence['results'] if r['hash']==measured['hash']]
        if retained!=[semantic(measured)]:
            raise ValueError('quantitative_ledger_result_mismatch')
    if classification not in ('SUPPORTED','UNSUPPORTED'):
        return
    if (not measured or not measured.get('testable') or measured.get('outcome')=='UNTESTED'
            or measured.get('verdict') in ('empty','untested','untestable','error')):
        raise ValueError('quantitative_result_untested')
    candidates = [r for r in evidence['candidates'] if r['hash']==measured['hash']]
    looks = [r for r in evidence['spent_looks'] if r['hash']==measured['hash'] and r['gate']=='gate1']
    if len(candidates)!=1 or len(looks)!=1 or len(evidence['registered_budget'])!=1:
        raise ValueError('registered_referee_evidence_missing')
    cand, look = candidates[0], looks[0]
    detail = json.loads(look['detail'])
    g1, g3 = json.loads(cand['gate1']), json.loads(cand['gate3'])
    actual = registered.gate1(g1['a'],g1['b'],g1['rotation'])
    rows = evidence['spent_looks']
    pos = [r['seq'] for r in rows].index(look['seq'])+1
    budget = evidence['registered_budget'][0]
    rejects = [i for i,r in enumerate(rows,1) if r['rejected'] and i<pos]
    ceiling = fdr.alpha_at(pos,rejects,budget['alpha'],budget['w0'])
    if (str(actual['reason']).startswith('untestable:') or actual['p'] != look['p']
            or g1['p']!=look['p'] or g1['alpha']!=look['alpha_t']
            or look['alpha_t']>ceiling+1e-12 or detail['cut_ms']!=ex['split']['cut_ms']
            or portfolio_null.draws_for(look['alpha_t'],detail['draws']) is None):
        raise ValueError('registered_referee_cut_power_budget')
    if any(json.loads(r['detail']).get('cut_ms', ex['split']['cut_ms']) != ex['split']['cut_ms']
           for r in rows if r['tf']=='4h' and json.loads(r['detail']).get('cut_ms')):
        raise ValueError('registered_protected_cut_changed')
    ref_steps = [r for r in receipts if r['job']=='referee_job' and r['ok'] and r.get('acceptance')!='REFUSED' and
                 (r.get('value') or {}).get('hash')==measured['hash']]
    if not ref_steps:
        raise ValueError('registered_referee_measurement_missing')
    if not any(all(r['value'].get(k)==g1[k] for k in ('a','b','rotation')) and r['value'].get('gate3')==g3 for r in ref_steps):
        raise ValueError('registered_referee_measurement_mismatch')
    passed = bool(look['rejected']) and g3['passed'] is True
    expected = 'SUPPORTED' if passed else 'UNSUPPORTED'
    if classification!=expected or referee['state']!=cand['state']:
        raise ValueError('quantitative_conclusion_mismatch')
    if classification=='SUPPORTED':
        ev = referee.get('evidence')
        if not ev or ev['gate1_look']!=look or ev['gate1']!=g1 or ev['gate3']!=g3:
            raise ValueError('registered_referee_evidence_missing')


def build(db, h, experiment_id, classification, measured, referee):
    P.validate(h)
    ex = None
    receipts, quantitative = [], None
    if experiment_id:
        row = db.execute('SELECT payload FROM bridge_experiments WHERE id=?',(experiment_id,)).fetchone()
        if not row:
            raise ValueError('feedback_experiment_missing')
        ex = json.loads(row[0])
        E.validate(ex)
        if ex['schema']!=E.EXPERIMENT_SCHEMA:
            raise ValueError('legacy_experiment_unbound_for_new_result')
        if ex['source_hypothesis_id']!=h.hypothesis_id or ex['hypothesis']!=json.loads(P.canonical(asdict(h))):
            raise ValueError('feedback_hypothesis_identity')
        receipts = [json.loads(r[0]) for r in db.execute('SELECT payload FROM bridge_measurements ORDER BY id')
                    if json.loads(r[0]).get('binding',{}).get('experiment_id')==experiment_id]
        quantitative = snapshot(db, ex)
        validate_quantitative(ex,classification,measured,referee,quantitative,receipts)
    elif classification in ('SUPPORTED','UNSUPPORTED') or measured is not None:
        raise ValueError('registered_quantitative_evidence_required')
    ref = {k:v for k,v in referee.items() if k!='last_step'}
    body = dict(schema=SCHEMA, bank_kind='predictive_experiment', bank_id=h.bank_id,
                hypothesis_id=h.hypothesis_id, hypothesis_schema=h.schema,
                experiment_id=experiment_id, experiment_schema=ex['schema'] if ex else None,
                classification=classification, measured_result=semantic(measured), referee_disposition=ref,
                stage=P.SUPPORTED_RESULT if classification=='SUPPORTED' else 'MEASURED_RESULT',
                authority='RESEARCH_ONLY', predictive_strategy_validation=classification=='SUPPORTED',
                quantitative_outcome={'SUPPORTED':'PASS','UNSUPPORTED':'FAIL'}.get(classification,'UNTESTED'),
                measurement_id=P.digest(semantic(measured)) if measured is not None else None,
                quantitative_evidence_id=P.digest(quantitative) if quantitative else None,
                quantitative_evidence=quantitative, measurement_receipts=receipts,
                provenance=binding(ex) if ex else dict(hypothesis_id=h.hypothesis_id,hypothesis_schema=h.schema),
                source_provenance_hash=h.source_hash,
                conclusion=classification, limitations=E.translation_contract(h)['limitations'])
    body['conclusion_id'] = P.digest(body)
    source = P.source_from_dict(json.loads(h.provenance_json)['source'])
    questions = P.next_questions(source,classification,[dict(conclusion_id=body['conclusion_id'],
        hypothesis_id=h.hypothesis_id,experiment_id=experiment_id,classification=classification)])
    body['next_questions'] = questions
    body['result_id'] = P.digest(body)
    return body


def validate(h, ex, body):
    if (body['result_id'] != P.digest({k:v for k,v in body.items() if k!='result_id'})
            or body['hypothesis_id']!=h.hypothesis_id or body['hypothesis_schema']!=h.schema
            or body['source_provenance_hash']!=h.source_hash
            or body['bank_id']!=h.bank_id or body['schema']!=SCHEMA):
        raise ValueError('research_result_identity')
    core = {k:v for k,v in body.items() if k not in ('result_id','next_questions','conclusion_id')}
    if body['conclusion_id']!=P.digest(core):
        raise ValueError('research_conclusion_identity')
    if ex:
        E.validate(ex)
        if ex['schema']!=E.EXPERIMENT_SCHEMA or body['experiment_schema']!=ex['schema']:
            raise ValueError('research_result_experiment_version')
        if body['experiment_id']!=ex['experiment_id'] or body['provenance']!=binding(ex):
            raise ValueError('research_result_experiment_cut')
        quantitative=body['quantitative_evidence']
        if body['quantitative_evidence_id']!=P.digest(quantitative):
            raise ValueError('quantitative_evidence_identity')
        validate_quantitative(ex,body['classification'],body['measured_result'],
                             body['referee_disposition'],quantitative,body['measurement_receipts'])
    elif body['experiment_id'] or body['classification'] in ('SUPPORTED','UNSUPPORTED') or body['measured_result'] is not None:
        raise ValueError('registered_quantitative_evidence_required')
    if (body['measurement_id'] != (P.digest(body['measured_result']) if body['measured_result'] is not None else None)
            or body['quantitative_outcome']!={'SUPPORTED':'PASS','UNSUPPORTED':'FAIL'}.get(body['classification'],'UNTESTED')
            or body['conclusion']!=body['classification']
            or body['limitations']!=E.translation_contract(h)['limitations']):
        raise ValueError('research_result_semantics')
    source=P.source_from_dict(json.loads(h.provenance_json)['source'])
    expected=P.next_questions(source,body['classification'],[dict(conclusion_id=body['conclusion_id'],
        hypothesis_id=h.hypothesis_id,experiment_id=body['experiment_id'],classification=body['classification'])])
    if body['next_questions']!=expected:
        raise ValueError('research_result_next_question')
