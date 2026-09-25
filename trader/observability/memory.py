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
      CREATE TABLE IF NOT EXISTS memory_assessed(source_id TEXT PRIMARY KEY, family TEXT NOT NULL,
        catalog_id TEXT NOT NULL, config_id TEXT NOT NULL, symbol TEXT NOT NULL, known_ms INTEGER NOT NULL,
        available_ms INTEGER NOT NULL, case_id TEXT NOT NULL, payload TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS memory_assessed_key
        ON memory_assessed(family, catalog_id, config_id, symbol, available_ms);
      CREATE TABLE IF NOT EXISTS memory_assessed_refused(source_id TEXT PRIMARY KEY, reason TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS memory_measured_refused(source_id TEXT PRIMARY KEY, reason TEXT NOT NULL);
    ''')


def retain(db):
    for table, key in (('memory_cases','source_id'), ('memory_contexts','case_id'), ('memory_reasoning','case_id'),
                       ('memory_unassessable','source_id'), ('memory_assessed','source_id'),
                       ('memory_assessed_refused','source_id'), ('memory_measured_refused','source_id')):
        db.execute(f'DELETE FROM {table} WHERE {key} NOT IN (SELECT id FROM cases)')


def ingest(db, now):
    """Measured terminal closures. A malformed source (unparseable latest update,
    investigation or inputs, a missing/non-integer evidence.as_of_ms, observed_ms or
    registered_ms, or a
    target_versions entry not shaped (symbol, open_ms, version_id)) is a
    deterministic defect: refused once as measured_malformed_source, recorded, and
    never retried, so it can neither crash the run nor occupy later bounds.
    Verification refusals keep their original reason. Only memory_invalid_chronology
    can become valid as now advances, so it alone is re-examined on later runs; every
    other verification refusal is deterministic for an immutable source and is
    recorded once, like the malformed refusal. source_id keys keep retry idempotent."""
    added, refused = 0, []
    rows = db.execute('SELECT id,payload FROM cases WHERE terminal_ms IS NOT NULL AND id NOT IN (SELECT source_id FROM memory_cases)'
                      ' AND id NOT IN (SELECT source_id FROM memory_measured_refused) ORDER BY created_ms,id LIMIT 256').fetchall()
    for iid, payload in rows:
        if added + len(refused) >= 32: break
        row = db.execute('SELECT payload FROM updates WHERE case_id=? ORDER BY observed_ms DESC,rowid DESC LIMIT 1',(iid,)).fetchone()
        if not row: continue
        status = _status(row[0])
        if status is not None and status != 'measured': continue
        try:
            update = I.update_from_dict(json.loads(row[0]))
            for key in ('as_of_ms', 'observed_ms'):
                if type(getattr(update.evidence, key)) is not int: raise TypeError(key)
            _check_target_versions(update.evidence.target_versions)
            inv, bars = I.investigation_from_dict(json.loads(payload)), _bars(db, iid)
            if type(inv.registered_ms) is not int: raise TypeError('registered_ms')
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            _refuse_measured(db, refused, iid)
            continue
        try:
            case = M.verified_case(inv, update, bars, now)
        except ValueError as exc:
            if str(exc) == 'memory_invalid_chronology':
                refused.append({'investigation_id': iid, 'reason': str(exc)})
            else:
                _refuse_measured(db, refused, iid, str(exc))
            continue
        except (KeyError, TypeError, AttributeError):
            _refuse_measured(db, refused, iid)
            continue
        db.execute('INSERT INTO memory_cases VALUES (?,?)',(iid,I.encode(asdict(case))))
        added += 1
    return {'added': added, 'refused': refused, 'unassessable': ingest_unassessable(db, now),
            'assessed': ingest_assessed(db, now)}


def _status(raw):
    """Raw latest-update evidence status, or None when the payload cannot say."""
    try:
        status = json.loads(raw)['evidence']['status']
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    return status if isinstance(status, str) else None


def _check_target_versions(versions):
    """Deserialization shape only: (symbol, open_ms, version_id) entries, so verification
    never unpacks a malformed entry into a retryable semantic ValueError."""
    for v in versions:
        if (len(v) != 3 or not isinstance(v[0], str) or type(v[1]) is not int
                or not isinstance(v[2], str)):
            raise TypeError('target_versions')


def _refuse_measured(db, refused, iid, reason='measured_malformed_source'):
    refused.append({'investigation_id': iid, 'reason': reason})
    db.execute('INSERT INTO memory_measured_refused VALUES (?,?)',(iid,reason))


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
        # Other statuses and unreadable payloads belong to other ingestion paths.
        if _status(row[0]) != 'not_testable': continue
        update = I.update_from_dict(json.loads(row[0]))
        try:
            record = M.verified_unassessable(I.investigation_from_dict(json.loads(payload)), update, _bars(db, iid), now)
        except (ValueError, KeyError, TypeError) as exc:
            refused.append({'investigation_id': iid, 'reason': str(exc) if isinstance(exc, ValueError) else 'unassessable_malformed_source'})
            continue
        db.execute('INSERT INTO memory_unassessable VALUES (?,?)',(iid,I.encode(asdict(record))))
        added += 1
    return {'added': added, 'refused': refused}


def ingest_assessed(db, now):
    """Terminal assessed registration-evidence descriptions only; same bounds as
    measured ingestion. The candidate set holds only unexamined terminal cases of a
    supported family whose terminal update is assessed, so other terminal cases never
    occupy the bound. A well-formed integer evidence.observed_ms still after now is a
    clock state, not a source defect: it is deferred before the bound, never refused,
    and becomes eligible once now reaches it. A missing or malformed observed_ms is not
    deferred; it is verified and refused. Once temporally eligible, verification is
    deterministic, so a refusal is a terminal disposition: recorded once with its
    reason and never retried. The source_id keys make retry and restart idempotent."""
    added, refused = 0, []
    families = sorted(M.ASSESSED_CATALOGS)
    rows = db.execute(
        'WITH latest AS (SELECT id,payload,created_ms,'
        '  (SELECT u.payload FROM updates u WHERE u.case_id=cases.id'
        '   ORDER BY u.observed_ms DESC,u.rowid DESC LIMIT 1) AS up'
        '  FROM cases WHERE terminal_ms IS NOT NULL'
        # json_valid guards: a malformed payload is not a candidate here and cannot
        # raise out of ingest(); measured ingestion owns its refusal.
        "  AND CASE WHEN json_valid(payload) THEN json_extract(payload,'$.primary_trigger') END"
        f"      IN ({','.join('?' * len(families))})"
        '  AND id NOT IN (SELECT source_id FROM memory_assessed)'
        '  AND id NOT IN (SELECT source_id FROM memory_assessed_refused))'
        " SELECT id,payload,up FROM latest"
        " WHERE CASE WHEN json_valid(up) THEN json_extract(up,'$.evidence.status') END='assessed'"
        " AND NOT (CASE WHEN json_valid(up) THEN json_type(up,'$.evidence.observed_ms') END IS 'integer'"
        "          AND CASE WHEN json_valid(up) THEN json_extract(up,'$.evidence.observed_ms') END>?)"
        ' ORDER BY created_ms,id LIMIT 32', (*families, now)).fetchall()
    for iid, payload, up in rows:
        try:
            update = I.update_from_dict(json.loads(up))
            record = M.verified_assessed(I.investigation_from_dict(json.loads(payload)), update, _bars(db, iid), now)
        except (ValueError, KeyError, TypeError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else 'assessed_malformed_source'
            refused.append({'investigation_id': iid, 'reason': reason})
            db.execute('INSERT INTO memory_assessed_refused VALUES (?,?)',(iid,reason))
            continue
        db.execute('INSERT INTO memory_assessed VALUES (?,?,?,?,?,?,?,?,?)',(
            iid, record.family, record.catalog_id, record.config_id, record.symbol,
            max(record.resolved_ms, record.available_ms, record.recorded_ms), record.available_ms,
            record.case_id, I.encode(asdict(record))))
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
    # Bounded on the exact match key and known-before-registration, in recall order,
    # so unrelated records can never crowd a compatible one out of the bound.
    described = [M.assessed_from_dict(json.loads(p)) for (p,) in db.execute(
        'SELECT payload FROM memory_assessed WHERE family=? AND catalog_id=? AND config_id=? AND symbol=?'
        ' AND known_ms<? AND source_id!=? ORDER BY available_ms DESC,case_id LIMIT 256',
        (inv.primary_trigger, inv.measurement.catalog_id, inv.state.config_id, inv.state.symbol,
         inv.registered_ms, inv.investigation_id))]
    if described:
        # Absent when no compatible record exists, so such contexts stay byte-identical.
        prior = M.retrieve_assessed(inv, described)
        prior['source_archives'] = [source_archive(db, r['source_id']) for r in prior['records']]
        context['assessed_descriptions'] = prior
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


def replay_assessed(archive, recorded_ms):
    inv=I.investigation_from_dict(archive['investigation'])
    update=I.update_from_dict(archive['updates'][-1])
    bars=[I.InputBar(b['version_id'],I.Candle(**b['candle'])) for b in archive['inputs']]
    return M.verified_assessed(inv,update,bars,recorded_ms)


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
        described=ctx.get('assessed_descriptions')
        if described:
            records=described['records']; sources=described.get('source_archives',[])
            if len(sources)!=len(records): raise ValueError('memory_archive_missing_prior_source')
            if len(records)>M.MAX_RETRIEVED: raise ValueError('memory_archive_prior_assessed_capacity')
            if (len({r['source_id'] for r in records})!=len(records)
                    or len({r['case_id'] for r in records})!=len(records)):
                raise ValueError('memory_archive_prior_assessed_duplicate')
            rebuilt=[]
            for r,raw in zip(records,sources):
                rec=replay_assessed(raw,r['recorded_ms'])
                if json.loads(I.encode(asdict(rec)))!=r:
                    raise ValueError('memory_archive_prior_assessed_mismatch')
                rebuilt.append(rec)
            # Every included record must be one the receiving registration's own recall
            # would include, in its order, with its canonical context-only presentation.
            # The audit also names excluded candidates without archives, so only its
            # included entries can be proven here.
            canonical=json.loads(I.encode(M.retrieve_assessed(inv,rebuilt)))
            if ({k:described.get(k) for k in ('schema_version','records','notes','limitation')}
                    !={k:canonical[k] for k in ('schema_version','records','notes','limitation')}
                    or [a['case_id'] for a in described.get('audit',[])
                        if a.get('reason')=='compatible_prior_assessed_description']!=[r['case_id'] for r in records]):
                raise ValueError('memory_archive_prior_assessed_ineligible')
        for reasoning in body['memory']['reasoning']:
            update=next(I.update_from_dict(u) for u in source['updates'] if u['event_id']==reasoning['event_id'])
            if M.reasoning(update,ctx)!=reasoning: raise ValueError('memory_archive_reasoning_mismatch')
    return body
