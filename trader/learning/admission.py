"""Retained admission population for selection-bias and missed-mover review.

Observation categories are not profitable outcomes or strategy admission.
No target writes or learned priorities are produced by this reader.
"""
import json
from trader.attention_admission import verify_receipt
from trader.core.journal_evidence import resolve


def population(journal, receipt_id):
    rows=journal.query("SELECT detail FROM brain_events WHERE kind='attention_admission' AND subject=?",(receipt_id,))
    if len(rows)!=1: raise ValueError('admission_population_missing_or_ambiguous')
    raw=rows[0]['detail']
    with journal._tx() as db:
        receipt=verify_receipt(json.loads(resolve(db,raw)))
    if receipt['receipt_id']!=receipt_id: raise ValueError('admission_population_identity_mismatch')
    return dict(receipt_id=receipt_id,source_cut_ms=receipt['source_cut_ms'],
        peer_cohort_id=receipt['peer_cohort_id'],population=[dict(symbol=r['symbol'],broad_observed=True,
            deep_admitted=r['category']!='deferred',event_admitted=r['category']=='event',
            exploration_admitted=r['category']=='exploration',rejected_deferred=r['category']=='deferred',
            category=r['category'],missing_warmup=r['score_status'],features=r['features'],
            score=r['score'],reason=r['reason']) for r in receipt['rows']],
        exclusions=receipt['observation']['excluded'] if 'excluded' in receipt['observation'] else {},
        authority='OBSERVATION_ONLY; no profit labels, priority update or Risk permission')
