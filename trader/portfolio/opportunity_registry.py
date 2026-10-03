"""Reference-only SDD 15.7 registry. Exact occurrences, never inferred episodes."""
import json
import sqlite3
from .allocator import canonical, digest

RESOLUTIONS = ('TRADED', 'SKIPPED', 'EXPIRED', 'INVALIDATED')


def identity(context, cycle_id, candidate_id, strategy_version_id=None):
    body = context.to_dict()
    instrument = body['instrument']['canonical_id'] or body['instrument']['symbol_key']
    items = body['signal_occurrences']['items']
    # Only ONE exact occurrence proves a repeat. A new bar/version/action is
    # a separate setup; no cross-bar continuity or fuzzy episode inference.
    key = items[0]['key'] if len(items) == 1 and items[0]['status'] == 'AVAILABLE' else None
    lineage = dict(instrument=instrument, occurrence=key, strategy_version_id=strategy_version_id)
    if key is None:
        lineage.update(cycle_id=cycle_id, candidate_id=candidate_id)
    return 'opportunity:' + digest(lineage), lineage


class Registry:
    """Caller-owned registry; optional journal adapter captures exact resolutions."""
    def __init__(self, path, *, learning_journal=None):
        self.learning_journal = learning_journal
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS opportunities '
                        '(opportunity_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def close(self):
        self.db.close()

    def observe(self, context, cycle_id, candidate_id, strategy_version_id=None):
        from trader.cognition.opportunity_context import OpportunityContext
        context = OpportunityContext.from_json(context.canonical_json)
        if not all(isinstance(v, str) and v for v in (cycle_id, candidate_id)):
            raise ValueError('EXACT_CYCLE_CANDIDATE_REQUIRED')
        oid, lineage = identity(context, cycle_id, candidate_id, strategy_version_id)
        body = context.to_dict()
        cut = body['as_of_ms']
        row = self.db.execute('SELECT payload FROM opportunities WHERE opportunity_id=?', (oid,)).fetchone()
        if row:
            old = json.loads(row[0])
            if old['lineage'] != lineage or old['opportunity_id'] != oid:
                raise ValueError('REGISTRY_IDENTITY_CORRUPT')
            if cut < old['last_observed_at'] or old['status'] != 'OPEN':
                raise ValueError('REGISTRY_TIME_OR_TERMINAL_REFUSED')
        else:
            key = lineage['occurrence']
            old = dict(opportunity_id=oid, instrument=lineage['instrument'],
                       direction=({'BUY': 'LONG', 'SELL': 'SHORT'}.get(key['action']) if key else None),
                       horizon=key['signal_timeframe'] if key else None,
                       opened_at=cut, status='OPEN', related_cycle_ids=[], related_candidate_ids=[],
                       context_ids=[], context_jsons=[], investigation_id=None, resolution=None,
                       lineage=lineage, last_observed_at=cut)
        old['related_cycle_ids'] = sorted(set(old['related_cycle_ids'] + [cycle_id]))
        old['related_candidate_ids'] = sorted(set(old['related_candidate_ids'] + [candidate_id]))
        old['context_jsons'] = sorted(set(old.get('context_jsons', []) + [context.canonical_json]))
        old['context_ids'] = sorted(set(old['context_ids'] + [context.context_id]))
        iid = body['investigation'].get('investigation_id')
        if iid and old['investigation_id'] not in (None, iid):
            raise ValueError('REGISTRY_INVESTIGATION_LINEAGE_DIFFERS')
        old['investigation_id'] = iid or old['investigation_id']
        old['last_observed_at'] = cut
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO opportunities VALUES (?,?)', (oid, canonical(old)))
        return old

    def resolve(self, opportunity_id, resolution, evidence_id, at_ms):
        with self.db:
            body = resolve_row(self.db, opportunity_id, resolution, evidence_id, at_ms)
        if self.learning_journal is not None and resolution != 'TRADED':
            from trader.learning import capture as C, producers as P
            with self.learning_journal._tx() as db:
                rows=db.execute("SELECT event_key FROM learning_registrations WHERE json_extract(payload,'$.lineage.opportunity_id')=?",(opportunity_id,)).fetchall()
                for (event,) in rows:
                    C.safely(db,'missed:'+opportunity_id,P.missed,event,body)
        return body


def resolve_row(db, opportunity_id, resolution, evidence_id, at_ms):
    """Terminal exact resolution on the caller's connection and transaction."""
    if resolution not in RESOLUTIONS or not isinstance(evidence_id, str) or not evidence_id:
        raise ValueError('RESOLUTION_EVIDENCE_REQUIRED')
    row = db.execute('SELECT payload FROM opportunities WHERE opportunity_id=?', (opportunity_id,)).fetchone()
    if not row:
        raise ValueError('OPPORTUNITY_MISSING')
    body = json.loads(row[0])
    if type(at_ms) is not int or at_ms < body['last_observed_at']:
        raise ValueError('RESOLUTION_TIME_INVALID')
    result = dict(resolution=resolution, evidence_id=evidence_id, at_ms=at_ms)
    if body['resolution'] not in (None, result):
        raise ValueError('TERMINAL_RESOLUTION_IMMUTABLE')
    body.update(status='RESOLVED', resolution=result)
    db.execute('UPDATE opportunities SET payload=? WHERE opportunity_id=?', (canonical(body), opportunity_id))
    return body
