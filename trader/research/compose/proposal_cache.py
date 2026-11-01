"""Reuse source-intake replies, never fitted models or financial results."""
import fcntl
import json

from ..closure import ClosureRefused, digest

SCHEMA = 'frozen-proposal-batch.v1'


def reply(cache, semantic_id, attempt, prompt, llm, *, allow_calls=True):
    key = digest([semantic_id, attempt])
    with open(str(cache.path) + '.proposal.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None, None, 'PROPOSAL_CACHE_BUSY'
        prior = cache.get_once('proposal_cache', key)
        if prior is not None:
            body = {k: v for k, v in prior.items() if k != 'record_id'}
            if (prior.get('record_id') != digest(body) or prior['semantic_id'] != semantic_id
                    or prior['attempt'] != attempt or prior['prompt_hash'] != digest(prompt)):
                raise ClosureRefused('proposal_cache_identity_mismatch')
            return prior['response'], prior['record_id'], prior['error']
        if not allow_calls:
            return None, None, 'FROZEN_PROPOSAL_CACHE_MISS'
        if not llm or not llm.available:
            return None, None, 'PROPOSER_UNAVAILABLE'
        if cache.get_once('proposal_cache_reservations', key) is not None:
            response, error = None, 'PROPOSAL_INTERRUPTED_AFTER_RESERVATION'
        else:
            cache.put_once('proposal_cache_reservations', key, dict(semantic_id=semantic_id, attempt=attempt))
            response = llm.chat_json(prompt, purpose='research', deep=False)
            error = None if response is not None else 'PROVIDER_RESPONSE_UNAVAILABLE'
        body = dict(semantic_id=semantic_id, attempt=attempt, prompt_hash=digest(prompt), response=response, error=error)
        record = dict(body, record_id=digest(body))
        cache.put_once('proposal_cache', key, record)
        return response, record['record_id'], error


def batch(store, study_id, *, tf, provider_id):
    """Bind all intake attempts for this study, including compile refusals."""
    proposals = [p for p in store.all_once('proposals') if p.get('study_id') == study_id]
    if not proposals or any(not p.get('cache_record_id') for p in proposals):
        return None
    body = dict(schema=SCHEMA, tf=tf, provider_id=provider_id,
                member_ids=sorted({p['cache_record_id'] for p in proposals}))
    return dict(body, batch_id=digest(body))


def for_envelope(store, env):
    matches = []
    for row in store.studies():
        study = json.loads(row['payload'])
        if study.get('envelope_id') == env['envelope_id'] and study.get('bank_path'):
            units = [u for u in store.units(row['study_id']) if u['kind'] == 'intake']
            if len(units) == 1 and units[0]['status'] == 'DONE':
                matches.append(json.loads(units[0]['result'] or '{}').get('proposal_batch'))
            else:
                return None
    if not matches or any(m is None for m in matches):
        return None
    if len({verify(m)['batch_id'] for m in matches}) != 1:
        raise ClosureRefused('proposal_batch_ambiguous_for_envelope')
    return matches[0]


def verify(value):
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or not value.get('member_ids') or value['member_ids'] != sorted(set(value['member_ids']))
            or value.get('batch_id') != digest({k: v for k, v in value.items() if k != 'batch_id'})):
        raise ClosureRefused('proposal_batch_identity_invalid')
    return value
