"""P2 — durable, bounded question consumer and bounded revision.

`consume()` reads retained Bank questions READ-ONLY, re-verifies each against
its exact source with the EXISTING verifiers (`brain_intake.question` for Brain
passages, `cognition.predictive.source_from_dict` + `next_questions` for
investigation results), and resolves it to exactly one outcome:

  COMPILED             one typed executable candidate (family + params)
  UNSUPPORTED_FAMILY   no registered family expresses it (never defaulted)
  WAITING_DATA         needs new point-in-time data, not a rule change
  NO_CHANGE_JUSTIFIED  no registered change is justified by the question
  BLOCKED              unverifiable/incompatible source, or attempts exhausted

Brain passages stay QUESTION_ONLY: they may select a family by registered
keywords, but the candidate records `source_authority=QUESTION_ONLY` and no
investigation source chain is ever fabricated. Duplicate delivery and restarts
resolve to the same item id, so at most one candidate per question exists.

`revise()` turns a DEVELOPMENT result into a bounded next candidate. Final-test
feedback is refused: a qualification result can never feed development.
"""
from __future__ import annotations

from contextlib import closing
import sqlite3

from . import ClosureRefused, digest, families
from .store import Store

ITEM_SCHEMA = 'closure-work-item.v1'
MAX_ATTEMPTS = 3
MAX_REVISIONS = 6                            # economic revisions per root candidate
MAX_REPAIRS = 2                              # code-repair attempts per candidate

# Exact registered question texts (cognition.predictive.next_questions and the
# Brain intake template) -> what kind of change they could justify.
INTENTS = {
    'Does this finding replicate on untouched cases and survive its registered baseline?': 'REPLICATE',
    'Does normalization rather than persistence explain the mechanism in a materially different registered context?': 'ALTERNATIVE_MECHANISM',
    'Does a different registered horizon distinguish normalization from persistence?': 'ALTERNATIVE_HORIZON',
    'Which missing discriminator or powered data window can separate the competing mechanisms?': 'MISSING_DISCRIMINATOR',
    'Can the exact target and predictor be expressed by a registered experiment shape?': 'EXPRESSIBILITY',
    'Can new point-in-time data supply the existing evidence requirements without revisiting protected evidence?': 'NEW_DATA',
    'Which registered alternative explains the repeated failure; what materially different context would discriminate it?': 'ALTERNATIVE_MECHANISM',
    'Which registered discriminator can resolve the retained contradictory context observations?': 'CONTEXT_DISCRIMINATOR',
    'Which timestamped context measurement could distinguish the unexplained regime difference?': 'CONTEXT_DISCRIMINATOR',
    'What registered predictor, target, scope and falsifier would test this unverified passage?': 'BRAIN_PASSAGE',
}


class _Blocked(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def read_bank(bank_path) -> dict:
    """Questions, sources and results from a Bank file, opened read-only."""
    from .. import predictive_bridge as B, predictive_experiment as E
    with closing(E.readonly(bank_path)) as db:
        db.execute('BEGIN')
        return dict(questions=B.records(db, 'bridge_questions'), sources=B.records(db, 'bridge_sources'),
                    results=B.records(db, 'bridge_bank_results'), hypotheses=B.records(db, 'bridge_hypotheses'))


def verify_source(question: dict, sources: dict):
    """(source_type, verified source). Existing verifiers; no new trust."""
    from .. import brain_intake
    from ...cognition import predictive as P
    src = sources.get(question.get('source_bank_id'))
    if src is None:
        raise _Blocked('BLOCKED', 'source_missing')
    if src.get('schema') == brain_intake.SCHEMA:
        if 'classification' in question and question.get('classification') != 'UNTESTED':
            raise _Blocked('BLOCKED', 'source_type_mismatch')
        try:
            rebuilt = brain_intake.question(src)
        except (ValueError, KeyError, TypeError) as exc:
            raise _Blocked('BLOCKED', f'brain_source_unverified:{exc}')
        if rebuilt['question_id'] != question['question_id']:
            raise _Blocked('BLOCKED', 'brain_question_not_from_source')
        return 'BRAIN', rebuilt
    if 'retained_chain_json' not in src:
        raise _Blocked('BLOCKED', 'source_type_unknown')
    if question.get('authority') != 'QUESTION_ONLY' or 'passage' in question:
        raise _Blocked('BLOCKED', 'source_type_mismatch')
    try:
        s = P.source_from_dict(src)
        allowed = P.next_questions(s, question['classification'], tuple(question.get('prior') or ()))
    except (ValueError, KeyError, TypeError) as exc:
        raise _Blocked('BLOCKED', f'investigation_source_unverified:{exc}')
    if question not in allowed:
        raise _Blocked('BLOCKED', 'question_not_derivable_from_source')
    return 'INVESTIGATION', s


def _parent_result(question_id, results):
    for r in results:
        if any(q.get('question_id') == question_id for q in r.get('next_questions') or ()):
            return r
    return None


def _resolve(question, source_type, source, bank, *, proposer=None) -> tuple:
    """(status, reason, candidate|None, extra provenance)."""
    if proposer is not None:
        return proposer(question, source_type, source)
    from ...cognition import predictive as P
    intent = INTENTS.get(question.get('text'))
    if intent is None:
        return 'BLOCKED', 'UNREGISTERED_QUESTION_TEMPLATE', None, {}
    if source_type == 'BRAIN':
        passage = source['passage']
        verdict, ids = families.match_text(' '.join(str(passage.get(k, '')) for k in ('title', 'text')))
        if verdict == 'MATCHED':
            return 'COMPILED', 'family_selected_by_registered_keywords', families.make_candidate(ids[0]), dict(
                evidence_class='IDEA_PROVENANCE', source_authority='QUESTION_ONLY', intent=intent)
        if verdict == 'AMBIGUOUS':
            return 'BLOCKED', 'AMBIGUOUS_FAMILY:' + ','.join(ids), None, {}
        return 'UNSUPPORTED_FAMILY', 'no registered family matches the passage', None, {}
    result = _parent_result(question['question_id'], bank['results'])
    extra = dict(result_id=(result or {}).get('result_id'), source_authority='QUESTION_ONLY', intent=intent,
                 evidence_class='DESCRIPTIVE_SOURCE')
    if intent == 'REPLICATE':
        fams = [families.TRANSFORM_FAMILY[t] for t in P.generatable() if t in families.TRANSFORM_FAMILY]
        if len(fams) == 1:
            return 'COMPILED', 'replication_candidate_from_generatable_transformation', \
                families.make_candidate(fams[0]), extra
        return 'UNSUPPORTED_FAMILY', 'no executable family for the generatable transformation', None, extra
    if intent == 'EXPRESSIBILITY':
        hyp = next((h['hypothesis'] for h in bank['hypotheses']
                    if result and h['hypothesis']['hypothesis_id'] == result.get('hypothesis_id')), None)
        t = (hyp or {}).get('transformation')
        if t in families.TRANSFORM_FAMILY:
            return 'COMPILED', 'transformation_has_registered_family', families.make_candidate(
                families.TRANSFORM_FAMILY[t]), extra
        shape = P.TRANSFORMS.get(t, {}).get('shape')
        return 'UNSUPPORTED_FAMILY', f'transformation={t} shape={shape}', None, extra
    if intent in ('NEW_DATA', 'MISSING_DISCRIMINATOR'):
        return 'WAITING_DATA', 'needs new point-in-time data; no rule change is implied', None, extra
    if intent == 'ALTERNATIVE_HORIZON':
        return 'UNSUPPORTED_FAMILY', 'horizon change outside the 4h-only research configuration', None, extra
    return 'NO_CHANGE_JUSTIFIED', f'{intent}: no registered family or discriminator expresses it', None, extra


def consume(store: Store, bank_path, *, now_ms: int, limit: int = 8, max_attempts: int = MAX_ATTEMPTS, proposer=None,
            study_id=None) -> dict:
    """Resolve up to `limit` unresolved questions. Idempotent and resumable."""
    counts = dict(seen=0, compiled=0, unsupported=0, waiting=0, no_change=0, blocked=0, deferred=0, skipped=0)
    try:
        bank = read_bank(bank_path)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return dict(counts, status='DEFERRED', reason=f'bank_unavailable:{type(exc).__name__}')
    sources = {s['bank_id']: s for s in bank['sources'] if 'bank_id' in s}
    work = 0
    for q in bank['questions']:
        if q.get('stage') != 'QUESTION' or q.get('authority') != 'QUESTION_ONLY':
            continue
        item_id = digest(['question', q['question_id']])
        store.claim_item(item_id, 'question', q['question_id'],
                         dict(schema=ITEM_SCHEMA, question_id=q['question_id'], source_bank_id=q.get('source_bank_id'),
                              **({'study_id': study_id} if study_id else {})),
                         now_ms)
        item = store.item(item_id)
        if item['status'] != 'PENDING':
            counts['skipped'] += 1
            continue
        if work >= limit:
            counts['deferred'] += 1
            continue
        work += 1
        counts['seen'] += 1
        if item['attempts'] >= max_attempts:
            store.set_item(item_id, 'BLOCKED', 'ATTEMPT_BUDGET_EXHAUSTED', now_ms)
            counts['blocked'] += 1
            continue
        try:
            stype, source = verify_source(q, sources)
            status, reason, cand, extra = _resolve(q, stype, source, bank, proposer=proposer)
            extra = dict(extra, source_type=stype)
        except _Blocked as b:
            status, reason, cand, extra = b.status, b.reason, None, {}
        except (sqlite3.Error, OSError, TypeError, KeyError) as exc:
            store.set_item(item_id, 'PENDING', f'transient:{type(exc).__name__}', now_ms, bump=True)
            counts['deferred'] += 1
            continue
        _finish(store, item_id, status, reason, cand, dict(extra, question_id=q['question_id'],
                source_bank_id=q.get('source_bank_id')), now_ms)
        counts[{'COMPILED': 'compiled', 'UNSUPPORTED_FAMILY': 'unsupported', 'WAITING_DATA': 'waiting',
                'NO_CHANGE_JUSTIFIED': 'no_change', 'BLOCKED': 'blocked'}[status]] += 1
    return dict(counts, status='OK')


def _finish(store, item_id, status, reason, candidate, provenance, now_ms):
    """Atomic: candidate row and item status commit together, so a crash can
    leave a PENDING item (retried to the same ids) but never a half child."""
    import json
    with store._tx() as c:
        if candidate is not None:
            old = c.execute('SELECT payload FROM candidates WHERE id=?', (candidate['candidate_id'],)).fetchone()
            from . import canonical
            if old is None:
                c.execute('INSERT INTO candidates VALUES (?,?)', (candidate['candidate_id'], canonical(candidate)))
            elif old[0] != canonical(candidate):
                raise ClosureRefused('record_conflict', 'candidates')
        payload = json.loads(c.execute('SELECT payload FROM items WHERE item_id=?', (item_id,)).fetchone()[0])
        payload['provenance'] = provenance
        from . import canonical
        c.execute('UPDATE items SET status=?,reason=?,candidate_id=?,payload=?,updated_ms=? WHERE item_id=?',
                  (status, reason, candidate['candidate_id'] if candidate else None, canonical(payload),
                   now_ms, item_id))


def lineage(store: Store, candidate_id: str) -> list:
    """Every item that produced this candidate (question and revision links)."""
    import json
    return [dict(item_id=i['item_id'], kind=i['kind'], status=i['status'], provenance=json.loads(
        i['payload']).get('provenance')) for i in store.items() if i['candidate_id'] == candidate_id]


def revise(store: Store, parent_candidate_id: str, dev_result: dict, *, now_ms: int,
           max_revisions: int = MAX_REVISIONS, max_repairs: int = MAX_REPAIRS, study_id=None) -> dict:
    """The next step after one DEVELOPMENT result. Returns the work item."""
    import json
    from . import canonical
    if dev_result.get('stage') != 'DEVELOPMENT':
        raise ClosureRefused('final_test_feedback_barred', str(dev_result.get('stage')))
    if dev_result.get('candidate_id') != parent_candidate_id or dev_result.get('result_id') is None:
        raise ClosureRefused('result_candidate_mismatch')
    parent = store.get_once('candidates', parent_candidate_id)
    if parent is None:
        raise ClosureRefused('parent_candidate_unknown')
    item_id = digest(['revision', parent_candidate_id, dev_result['result_id']])
    store.claim_item(item_id, 'revision', None, dict(
        schema=ITEM_SCHEMA, parent_candidate_id=parent_candidate_id, result_id=dev_result['result_id'],
        outcome=dev_result['outcome'], **({'study_id': study_id} if study_id else {})), now_ms)
    item = store.item(item_id)
    if item['status'] != 'PENDING':
        return item
    outcome = dev_result['outcome']
    child, change = None, None
    if outcome == 'NEGATIVE_ECONOMICS':
        root_count = sum(1 for i in store.items(kind='revision') if i['status'] == 'COMPILED'
                         and json.loads(i['payload']).get('provenance', {}).get('root') == _root(store, parent_candidate_id))
        if root_count >= max_revisions:
            status, reason = 'BLOCKED', 'REVISION_BUDGET_EXHAUSTED'
        else:
            for change in families.mutations(parent):
                cand = families.mutate(parent, change)
                if store.get_once('candidates', cand['candidate_id']) is None:
                    child = cand
                    break
            status, reason = ('COMPILED', 'bounded_economic_revision') if child else (
                'NO_CHANGE_JUSTIFIED', 'mutation_space_exhausted')
    elif outcome in ('INCONCLUSIVE_POWER', 'BLOCKED_DATA'):
        status, reason = 'WAITING_DATA', 'power_or_data_shortfall_is_not_negative_evidence; no rule change'
    elif outcome == 'CODE_FAILURE':
        repairs = sum(1 for i in store.items(kind='revision')
                      if json.loads(i['payload']).get('parent_candidate_id') == parent_candidate_id
                      and json.loads(i['payload']).get('outcome') == 'CODE_FAILURE')
        status, reason = ('BLOCKED', 'CODE_REPAIR_BUDGET_EXHAUSTED') if repairs > max_repairs else (
            'BLOCKED', 'NEEDS_CODE_REPAIR')              # economic identity unchanged
    else:
        status, reason = 'NO_CHANGE_JUSTIFIED', 'development_positive_eligible_for_freeze'
    prov = dict(parent_candidate_id=parent_candidate_id, result_id=dev_result['result_id'],
                change=change if child else None, root=_root(store, parent_candidate_id),
                source_authority='DEVELOPMENT_RESULT', intent='REVISION')
    _finish(store, item_id, status, reason, child, prov, now_ms)
    return store.item(item_id)


def _root(store, candidate_id):
    """Root of the revision chain (first candidate with no revision parent)."""
    import json
    seen = set()
    while candidate_id not in seen:
        seen.add(candidate_id)
        parent = next((json.loads(i['payload']).get('provenance', {}).get('parent_candidate_id')
                       for i in store.items(kind='revision') if i['candidate_id'] == candidate_id), None)
        if not parent:
            return candidate_id
        candidate_id = parent
    return candidate_id
