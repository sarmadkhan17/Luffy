"""Memory tables share the investigation transaction, bounds and retention.

Measured investigations and separately typed price observations enrich questions.
Execution accounting, owner preferences and doctrine remain distinct types.
"""
import json
from dataclasses import asdict

from trader.cognition import investigation as I
from trader.cognition import memory as M
from . import outcomes as typed


def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS memory_cases(source_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS memory_contexts(case_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS memory_reasoning(event_id TEXT PRIMARY KEY, case_id TEXT, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS memory_unassessable(source_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    ''')


def retain(db):
    for table, key in (('memory_cases','source_id'), ('memory_contexts','case_id'), ('memory_reasoning','case_id'),
                       ('memory_unassessable','source_id')):
        db.execute(f'DELETE FROM {table} WHERE {key} NOT IN (SELECT id FROM cases)')


def ingest(db, now):
    added, refused = 0, []
    rows = db.execute('SELECT id,payload FROM cases WHERE terminal_ms IS NOT NULL AND id NOT IN (SELECT source_id FROM memory_cases) ORDER BY created_ms,id LIMIT 256').fetchall()
    for iid, payload in rows:
        if added + len(refused) >= 32: break
        inv = I.investigation_from_dict(json.loads(payload))
        row = db.execute('SELECT payload FROM updates WHERE case_id=? ORDER BY observed_ms DESC,rowid DESC LIMIT 1',(iid,)).fetchone()
        if not row: continue
        update = I.update_from_dict(json.loads(row[0]))
        if update.evidence.status != 'measured': continue
        bars = [I.InputBar(d['version_id'], I.Candle(**d['candle'])) for (p,) in db.execute(
            'SELECT payload FROM inputs JOIN case_inputs ON inputs.id=case_inputs.input_id WHERE case_id=?',(iid,)) for d in [json.loads(p)]]
        try:
            case = M.verified_case(inv, update, bars, now)
        except ValueError as exc:
            refused.append({'investigation_id': iid, 'reason': str(exc)})
            continue
        db.execute('INSERT INTO memory_cases VALUES (?,?)',(iid,I.encode(asdict(case))))
        added += 1
    return {'added': added, 'refused': refused, 'unassessable': ingest_unassessable(db, now)}


def _bars(db, iid):
    return [I.InputBar(d['version_id'], I.Candle(**d['candle'])) for (p,) in db.execute(
        'SELECT payload FROM inputs JOIN case_inputs ON inputs.id=case_inputs.input_id WHERE case_id=?',(iid,)) for d in [json.loads(p)]]


def ingest_unassessable(db, now):
    """Terminal not_testable closures only; same bounds as measured ingestion.
    The source_id key makes retry and restart idempotent."""
    added, refused = 0, []
    rows = db.execute('SELECT id,payload FROM cases WHERE terminal_ms IS NOT NULL AND id NOT IN (SELECT source_id FROM memory_unassessable) ORDER BY created_ms,id LIMIT 256').fetchall()
    for iid, payload in rows:
        if added + len(refused) >= 32: break
        row = db.execute('SELECT payload FROM updates WHERE case_id=? ORDER BY observed_ms DESC,rowid DESC LIMIT 1',(iid,)).fetchone()
        if not row: continue
        update = I.update_from_dict(json.loads(row[0]))
        if update.evidence.status != 'not_testable': continue
        try:
            record = M.verified_unassessable(I.investigation_from_dict(json.loads(payload)), update, _bars(db, iid), now)
        except (ValueError, KeyError, TypeError) as exc:
            refused.append({'investigation_id': iid, 'reason': str(exc) if isinstance(exc, ValueError) else 'unassessable_malformed_source'})
            continue
        db.execute('INSERT INTO memory_unassessable VALUES (?,?)',(iid,I.encode(asdict(record))))
        added += 1
    return {'added': added, 'refused': refused}


def register(db, inv):
    cases = [M.case_from_dict(json.loads(p)) for (p,) in db.execute('SELECT payload FROM memory_cases ORDER BY source_id LIMIT 256')]
    context = M.retrieve(inv, cases)
    extra = typed.register(db, inv)
    context['source_archives'] = [source_archive(db, c['source_id']) for c in context['cases']]
    context['typed_outcomes'] = extra
    context['counter_tests'].extend(extra['counter_tests'])
    records = [M.unassessable_from_dict(json.loads(p)) for (p,) in db.execute(
        'SELECT payload FROM memory_unassessable ORDER BY source_id LIMIT 256')]
    if records:
        # Absent when no records exist, so such contexts stay byte-identical.
        closures = M.retrieve_unassessable(inv, records)
        closures['source_archives'] = [source_archive(db, r['source_id']) for r in closures['records']]
        context['unassessable_closures'] = closures
    context['context_id'] = M.stable_id('memory_context', {k:v for k,v in context.items() if k != 'context_id'})
    db.execute('INSERT INTO memory_contexts VALUES (?,?)',(inv.investigation_id,I.encode(context)))
    return context


def append_reasoning(db, update):
    row = db.execute('SELECT payload FROM memory_contexts WHERE case_id=?',(update.investigation_id,)).fetchone()
    if row:
        payload = M.reasoning(update, json.loads(row[0]))
        db.execute('INSERT INTO memory_reasoning VALUES (?,?,?)',(update.event_id,update.investigation_id,I.encode(payload)))


def owner_context(db, iid):
    # Older ledgers remain readable before first upgraded consumer invocation.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_contexts'").fetchone():
        return {'status': 'pre_memory_registration', 'context': None, 'reasoning': []}
    row = db.execute('SELECT payload FROM memory_contexts WHERE case_id=?',(iid,)).fetchone()
    return {'status': 'frozen_at_registration' if row else 'pre_memory_registration',
            'context': json.loads(row[0]) if row else None,
            'reasoning': [json.loads(p) for (p,) in db.execute('SELECT payload FROM memory_reasoning WHERE case_id=? ORDER BY rowid',(iid,))]}


def source_archive(db, iid):
    """Embed exact raw versions for each matched source before retention removes it."""
    row=db.execute('SELECT payload FROM cases WHERE id=?',(iid,)).fetchone()
    if not row: raise ValueError('memory_archive_source_missing')
    inv=json.loads(row[0])
    updates=[json.loads(p) for (p,) in db.execute(
        'SELECT payload FROM updates WHERE case_id=? ORDER BY observed_ms,rowid',(iid,))]
    inputs=[json.loads(p) for (p,) in db.execute(
        'SELECT payload FROM inputs JOIN case_inputs ON inputs.id=case_inputs.input_id WHERE case_id=? ORDER BY inputs.id',(iid,))]
    return {'investigation':inv,'updates':updates,'inputs':inputs}


def replay_source(archive, recorded_ms):
    inv=I.investigation_from_dict(archive['investigation'])
    update=I.update_from_dict(archive['updates'][-1])
    bars=[I.InputBar(b['version_id'],I.Candle(**b['candle'])) for b in archive['inputs']]
    return M.verified_case(inv,update,bars,recorded_ms)


def replay_unassessable(archive, recorded_ms):
    inv=I.investigation_from_dict(archive['investigation'])
    update=I.update_from_dict(archive['updates'][-1])
    bars=[I.InputBar(b['version_id'],I.Candle(**b['candle'])) for b in archive['inputs']]
    return M.verified_unassessable(inv,update,bars,recorded_ms)


def export_case(db, iid):
    body={'schema_version':'investigation-archive.v1', 'source':source_archive(db,iid),
          'memory':owner_context(db,iid)}
    if len(I.encode(body).encode()) > 2*1024**2:
        raise ValueError('memory_archive_capacity')
    return dict(body,sha256=typed.O.digest(body))


def replay_export(archive):
    """Replay baseline, updates and frozen reasoning from self-contained evidence."""
    body={k:archive[k] for k in ('schema_version','source','memory')}
    if (body['schema_version']!='investigation-archive.v1' or typed.O.digest(body)!=archive['sha256'] or
            len(I.encode(archive).encode())>2*1024**2):
        raise ValueError('memory_archive_integrity')
    source=body['source']; inv=I.investigation_from_dict(source['investigation'])
    bars=[I.InputBar(b['version_id'],I.Candle(**b['candle'])) for b in source['inputs']]
    baseline=[b for b in bars if b.version_id in inv.state.input_versions]
    if I.open_investigation(inv.state,inv.primary_trigger,baseline,inv.registered_ms)!=inv:
        raise ValueError('memory_archive_registration_mismatch')
    previous=None
    for raw in source['updates']:
        update=I.update_from_dict(raw)
        evidence=I.measure(inv,bars,update.evidence.as_of_ms,update.evidence.observed_ms)
        if evidence!=update.evidence or I.advance(inv,evidence,previous)!=update:
            raise ValueError('memory_archive_update_mismatch')
        previous=update
    ctx=body['memory']['context']
    if ctx:
        sources=ctx.get('source_archives',[])
        if len(sources)!=len(ctx['cases']): raise ValueError('memory_archive_missing_prior_source')
        for c,raw in zip(ctx['cases'],sources):
            if json.loads(I.encode(asdict(replay_source(raw,c['recorded_ms']))))!=c:
                raise ValueError('memory_archive_prior_case_mismatch')
        for c in ctx.get('typed_outcomes',{}).get('cases',[]): typed.O.replay(c)
        closures=ctx.get('unassessable_closures')
        if closures:
            sources=closures.get('source_archives',[])
            if len(sources)!=len(closures['records']): raise ValueError('memory_archive_missing_prior_source')
            for r,raw in zip(closures['records'],sources):
                if json.loads(I.encode(asdict(replay_unassessable(raw,r['recorded_ms']))))!=r:
                    raise ValueError('memory_archive_prior_unassessable_mismatch')
        for reasoning in body['memory']['reasoning']:
            update=next(I.update_from_dict(u) for u in source['updates'] if u['event_id']==reasoning['event_id'])
            if M.reasoning(update,ctx)!=reasoning: raise ValueError('memory_archive_reasoning_mismatch')
    return body
