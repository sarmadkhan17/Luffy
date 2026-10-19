"""Verified predictive attachments to retained Research Bank sources.

Separate schema; original v1 Bank objects remain immutable.
"""
import json


def predictive_extensions(bridge_path, bank_object_id):
    """Read immutable predictive attachments without rewriting a v1 Bank object.

    Research questions, proposals, experiments and measured/referee receipts
    retain separate roles. The source object is never predictive evidence.
    """
    from contextlib import closing
    from trader.research.predictive_experiment import readonly
    from trader.research.predictive_experiment import validate as validate_experiment
    from trader.research.predictive_bridge import replay_feedback
    from trader.cognition import predictive as P
    with closing(readonly(bridge_path)) as db:
        db.execute('BEGIN')
        def read(table):
            records=[]
            for identity,payload in db.execute(f'SELECT id,payload FROM {table} ORDER BY rowid'):
                r=json.loads(payload)
                expected=(r['hypothesis']['hypothesis_id'] if table=='bridge_hypotheses' else
                          r['experiment_id'] if table=='bridge_experiments' else
                          r['question_id'] if table=='bridge_questions' else
                          r['translation_id'] if table=='bridge_translations' else
                          r['result_id'] if r.get('schema')=='predictive-research-bank-result.v2' else P.digest(r))
                if identity!=expected:
                    raise ValueError('predictive_bank_attachment_integrity')
                records.append(r)
            return records
        hyps=[r['hypothesis'] for r in read('bridge_hypotheses') if r['hypothesis']['bank_id']==bank_object_id]
        for h in hyps:
            P.hypothesis_from_dict(h)
        ids={h['hypothesis_id'] for h in hyps}
        experiments=[r for r in read('bridge_experiments') if r['source_hypothesis_id'] in ids]
        for ex in experiments:
            validate_experiment(ex)
        questions=read('bridge_questions')
        from trader.research import brain_intake
        question_source = None
        for identity, payload in db.execute('SELECT id,payload FROM bridge_sources'):
            source = json.loads(payload)
            if source.get('schema') == brain_intake.SCHEMA:
                expected = brain_intake.question(source)
                if identity != source['bank_id'] or [q for q in questions if q['source_bank_id'] == identity] != [expected]:
                    raise ValueError('brain_question_binding')
                accepted = [json.loads(p) for p, in db.execute(
                    "SELECT payload FROM bridge_intake_outcomes WHERE json_extract(payload,'$.source_bank_id')=? "
                    "AND json_extract(payload,'$.status')='ACCEPTED'", (identity,))]
                if len(accepted) != 1 or accepted[0]['source'] != source or accepted[0]['observed_ms'] < expected['available_ms']:
                    raise ValueError('brain_source_acceptance')
                if identity == bank_object_id:
                    question_source = source
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        translations=read('bridge_translations') if 'bridge_translations' in names else []
        for translation in translations:
            if translation['translation_id']!=P.digest({k:v for k,v in translation.items() if k!='translation_id'}):
                raise ValueError('predictive_translation_integrity')
        results=[r for r in read('bridge_bank_results') if r['bank_id']==bank_object_id]
        for result in results:
            h=next(h for h in hyps if h['hypothesis_id']==result['hypothesis_id'])
            ex=next((ex for ex in experiments if ex['experiment_id']==result['experiment_id']),None)
            chain=dict(question=dict(hypothesis_id=h['hypothesis_id']),plan=ex,
                evidence=h,result=result,receipt=dict(measured=result['measured_result'],referee=result['referee_disposition']))
            replay_feedback(chain,result)
            if result.get('schema')=='predictive-research-bank-result.v2' and any(q not in questions for q in result['next_questions']):
                raise ValueError('predictive_result_next_question_missing')
        return dict(source_bank_id=bank_object_id,authority='RESEARCH_ONLY',
            question_source=question_source, hypotheses=hyps,experiments=experiments,results=results,
            next_questions=[r for r in questions if r['source_bank_id']==bank_object_id],
            translations=[r for r in translations if r['hypothesis_id'] in ids])
