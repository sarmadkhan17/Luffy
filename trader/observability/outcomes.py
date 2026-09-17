"""Bounded typed memory import/export. Never writes the source ledgers."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from trader.cognition import outcomes as O
from trader.observability.learning import encode

MAX_CASES, MAX_PAYLOAD, MAX_IMPORT = 256, 65536, 32
RETENTION_MS = 90*86400000


def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS typed_outcomes(source_key TEXT PRIMARY KEY, imported_ms INTEGER, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS typed_outcome_meta(key TEXT PRIMARY KEY, value INTEGER);
      CREATE TABLE IF NOT EXISTS typed_contexts(case_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS counterfactual_registrations(id TEXT PRIMARY KEY, registered_ms INTEGER, terminal_ms INTEGER, payload TEXT NOT NULL);
    ''')


def put(db, key, record):
    O.replay(record)
    payload = encode(record)
    if len(payload.encode()) > MAX_PAYLOAD:
        raise ValueError('typed_outcome_payload_capacity')
    old = db.execute('SELECT payload FROM typed_outcomes WHERE source_key=?',(key,)).fetchone()
    if old:
        if old[0] != payload:
            raise ValueError('typed_outcome_terminal_conflict')
        return False
    # Frequent skips have a separate half-store cap; protect scarce forecast/trade evidence.
    skip_count=db.execute("SELECT COUNT(*) FROM typed_outcomes WHERE json_extract(payload,'$.kind')='skip'").fetchone()[0]
    full=db.execute('SELECT COUNT(*) FROM typed_outcomes').fetchone()[0]>=MAX_CASES
    if full or (record['kind']=='skip' and skip_count>=MAX_CASES//2):
        victim=db.execute("SELECT source_key FROM typed_outcomes ORDER BY (json_extract(payload,'$.kind')='skip') DESC, imported_ms,source_key LIMIT 1").fetchone()
        db.execute('DELETE FROM typed_outcomes WHERE source_key=?',(victim[0],))
        db.execute("INSERT INTO typed_outcome_meta VALUES ('evicted_total',1) ON CONFLICT(key) DO UPDATE SET value=value+1")
    db.execute('INSERT INTO typed_outcomes VALUES (?,?,?)',(key,record['imported_ms'],payload))
    return True


def retain(db, now):
    db.execute('DELETE FROM typed_outcomes WHERE imported_ms<?',(now-RETENTION_MS,))
    db.execute('DELETE FROM counterfactual_registrations WHERE terminal_ms IS NOT NULL AND terminal_ms<?',(now-RETENTION_MS,))
    db.execute('DELETE FROM typed_contexts WHERE case_id NOT IN (SELECT id FROM cases)')


def _read(path):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
    db.row_factory = sqlite3.Row
    started=time.monotonic()
    db.set_progress_handler(lambda: int(time.monotonic()-started>2),1000)
    db.execute('BEGIN')
    return db


def ingest(db, directory, now):
    directory = Path(directory)
    report = {'added':0,'refused':[],'retry':[]}
    # Journal imports are forward-only: no historical outcome mining on upgrade.
    db.execute("INSERT OR IGNORE INTO typed_outcome_meta VALUES ('activated_ms',?)",(now,))
    activated = db.execute("SELECT value FROM typed_outcome_meta WHERE key='activated_ms'").fetchone()[0]
    known = {k for (k,) in db.execute('SELECT source_key FROM typed_outcomes')}
    def save(key, build):
        if key in known or report['added']+len(report['refused']) >= MAX_IMPORT:
            return
        try:
            report['added'] += int(put(db,key,build()))
        except (ValueError,KeyError,TypeError) as exc:
            report['refused'].append({'source_key':key,'reason':str(exc) if isinstance(exc,ValueError) else 'malformed_source'})
    path = directory/'attention_learning.db'
    if path.exists():
        try:
            with closing(_read(path)) as src:
                for row in src.execute("SELECT * FROM episodes WHERE status='resolved' ORDER BY created_ms DESC,id LIMIT 256"):
                    e = dict(row)
                    e['prediction'],e['outcome'] = json.loads(e['prediction']),json.loads(e['outcome'])
                    if src.execute("SELECT 1 FROM sqlite_master WHERE name='forecast_sources'").fetchone():
                        receipts=dict(src.execute('SELECT phase,payload FROM forecast_sources WHERE episode_id=?',(e['id'],)))
                        if set(receipts)=={'registration','outcome'}:
                            e['source_receipt']=json.loads(receipts['registration'])+json.loads(receipts['outcome'])
                    save('forecast:'+e['id'],lambda e=e: _forecast(directory/'attention.db',e,now))
        except (sqlite3.Error,ValueError,TypeError):
            report['retry'].append('forecast_source_unavailable')
    # Measured investigations supply real producers, separately from link contracts.
    from .memory import source_archive
    for iid, payload in db.execute('SELECT id,payload FROM cases WHERE terminal_ms IS NOT NULL ORDER BY created_ms DESC,id LIMIT 256').fetchall():
        if report['added']+len(report['refused']) >= MAX_IMPORT:
            break
        if all(kind+':'+iid in known for kind in ('false_signal','regime_transition')):
            continue
        raw = source_archive(db, iid)
        if not raw['updates'] or raw['updates'][-1]['evidence']['status'] != 'measured':
            continue
        update = raw['updates'][-1]
        inv = raw['investigation']
        kinds = []
        if dict(update['assessment']).get('same_direction') == 'contradicted':
            kinds.append('false_signal')
        if inv['primary_trigger'] == 'volatility_transition':
            kinds.append('regime_transition')
        archive = dict(investigation=inv, update=update, inputs=raw['inputs'])
        for kind in kinds:
            save(kind+':'+iid, lambda kind=kind,archive=archive: O.investigation_case(archive,kind,now))
    path = directory/'luffy.db'
    if path.exists():
        try:
            with closing(_read(path)) as src:
                # Indexed bounded tail; registration, not close time, gates forward imports.
                for row in src.execute('SELECT * FROM decisions ORDER BY ts DESC LIMIT 256'):
                    d = dict(row)
                    if O.timestamp(d['ts']) < activated: continue
                    safe = {k:d.get(k) for k in ('id','cycle_id','ts','symbol','action','executed','skip_reason','strategy_ids','scan_id')}
                    if d['executed']:
                        for t in src.execute("SELECT * FROM trades WHERE decision_id=? AND status='closed' ORDER BY id LIMIT 8",(d['id'],)):
                            t = dict(t)
                            safe_t = {k:t.get(k) for k in ('id','decision_id','symbol','status','closed_at','opened_at','realized_pnl','exec_mode','strategy_id')}
                            save('trade:'+t['id'],lambda d=safe,t=safe_t: O.linked_journal(d,t,now))
                    else:
                        save('skip:'+d['id'],lambda d=safe: O.linked_journal(d,None,now))
        except (sqlite3.Error,ValueError,TypeError):
            report['retry'].append('journal_source_unavailable')
    row=db.execute("SELECT value FROM typed_outcome_meta WHERE key='evicted_total'").fetchone()
    report['evicted_total']=row[0] if row else 0
    return report


def _forecast(path, episode, now):
    if 'source_receipt' in episode:
        return O.forecast(episode,now)
    with closing(_read(path)) as src:
        receipt = []
        p,o = episode['prediction'],episode['outcome']
        for sid,bars in ((p['scan_id'],p['input_window']), (o['scan_id'],[o['target']])):
            for bar in bars:
                row=src.execute('SELECT v.payload,v.first_seen_ms FROM versions v JOIN scan_versions s ON s.version_id=v.id WHERE s.scan_id=? AND v.id=?',(sid,bar['version_id'])).fetchone()
                # Older registrations already froze exact IDs/full bars but did not
                # retain scan receipts. Exact IDs can still survive shared scan retention.
                if not row:
                    row=src.execute('SELECT payload,first_seen_ms FROM versions WHERE id=?',(bar['version_id'],)).fetchone()
                if not row: raise ValueError('exact_source_version_missing_retry')
                raw=json.loads(row[0])
                raw.update(version_id=bar['version_id'],available_ms=row[1])
                if raw != bar: raise ValueError('exact_source_version_conflict')
                receipt.append({'scan_id':sid,'bar':raw})
        episode=dict(episode,source_receipt=receipt)
        return O.forecast(episode,now)


def register(db, inv):
    """Freeze symbol-compatible observations; keep other types out of price analogues."""
    context = {'schema_version':O.SCHEMA,'cutoff_ms':inv.registered_ms,'cases':[],'audit':[], 'counter_tests':[], 'local_imports':{}}
    for local_imported, payload in db.execute('SELECT imported_ms,payload FROM typed_outcomes ORDER BY imported_ms DESC,source_key LIMIT 256'):
        c = json.loads(payload)
        reason = 'compatible_prior_symbol_price_observation'
        if max(c['resolved_ms'],c['available_ms'],c['imported_ms'],local_imported) >= inv.registered_ms:
            reason = 'not_known_before_registration'
        elif c['kind'] not in ('selected_forecast','ignored_forecast','false_signal','regime_transition'):
            reason = 'incompatible_outcome_type'
        elif (c['observation'].get('symbol') if c['kind'] in ('false_signal','regime_transition') else c['source']['symbol']) != inv.state.symbol:
            reason = 'incompatible_symbol'
        elif c['kind'] in ('false_signal','regime_transition') and (
                c['observation']['family'] != inv.primary_trigger or
                c['observation']['sign'] != inv.measurement.sign or
                O.unpack_case(c['source']['investigation_case'])['investigation']['state']['config_id'] != inv.state.config_id or
                O.unpack_case(c['source']['investigation_case'])['investigation']['state']['contradictions'] != list(inv.state.contradictions) or
                (inv.primary_trigger == 'relative_return_divergence' and
                 O.unpack_case(c['source']['investigation_case'])['investigation']['state']['cohort'] != list(inv.state.cohort))):
            reason = 'incompatible_trigger_or_context'
        elif len(context['cases']) >= 3:
            reason = 'retrieval_capacity'
        else:
            O.replay(c)
            context['cases'].append(c)
            context['local_imports'][c['case_id']]=local_imported
            if c['kind'] in ('false_signal','regime_transition'):
                context['counter_tests'].append({'case_id':c['case_id'], 'test':
                    f"Prior {c['kind']} measured {c['observation']['winner']} "
                    f"(score {c['observation']['score']:.6g}); compare persistence with normalization and reversal "
                    "over the complete frozen window. This is an observable path, not cause or profit."})
                context['audit'].append({'case_id':c['case_id'],'reason':'compatible_prior_observable_path'})
                continue
            p = c['source']['prediction']
            context['counter_tests'].append({'case_id':c['case_id'], 'test':
                f"Compare this investigation's path with the prior {'selected' if p['selected'] else 'ignored'} "
                f"price forecast ({c['observation']['supports']}, {c['observation']['price_change_bps']:.6g} bps); "
                "check whether the different target horizon explains the difference. Price movement does not establish cause or profit."})
        context['audit'].append({'case_id':c['case_id'],'reason':reason})
    db.execute('INSERT INTO typed_contexts VALUES (?,?)',(inv.investigation_id,encode(context)))
    return context


def export(db):
    """Self-contained bounded archive; embedded raw sources survive source expiry."""
    records = [{'source_key':k,'record':json.loads(p)} for k,p in db.execute(
        'SELECT source_key,payload FROM typed_outcomes ORDER BY source_key')]
    if len(records)>MAX_CASES: raise ValueError('archive_capacity')
    for item in records: O.replay(item['record'])
    body = {'schema_version':O.SCHEMA,'records':records}
    return dict(body,sha256=O.digest(body))


def restore(db, archive, now):
    """Atomic archive replay. A new installation learns records at restore time."""
    body = {k:archive[k] for k in ('schema_version','records')}
    if (body['schema_version'] != O.SCHEMA or O.digest(body) != archive['sha256'] or
            len(body['records'])>MAX_CASES or len(encode(archive).encode())>MAX_CASES*MAX_PAYLOAD):
        raise ValueError('archive_integrity_or_capacity')
    db.execute('SAVEPOINT outcome_restore')
    try:
        for item in body['records']:
            c = O.replay(item['record'])
            if now < c['imported_ms']: raise ValueError('archive_future_import')
            # Preserve original source clocks in archive; re-adapt at actual local import.
            old = db.execute('SELECT payload FROM typed_outcomes WHERE source_key=?',(item['source_key'],)).fetchone()
            if old:
                existing=json.loads(old[0])
                if existing != c:
                    raise ValueError('typed_outcome_terminal_conflict')
                continue
            put(db,item['source_key'],c)
            db.execute('UPDATE typed_outcomes SET imported_ms=? WHERE source_key=?',(now,item['source_key']))
        db.execute('RELEASE outcome_restore')
    except Exception:
        db.execute('ROLLBACK TO outcome_restore'); db.execute('RELEASE outcome_restore')
        raise


def register_counterfactual(db, registration, now):
    """Explicit caller freezes a non-trade protocol now; no retrospective scheduling."""
    r=json.loads(encode(registration))
    if (r['registered_ms']!=now or r['target_open_ms']<=now or
            r['target_open_ms']%O.L.TF or r['target_open_ms']>now+7*86400000):
        raise ValueError('counterfactual_registration_not_forward')
    # Validate the entire contract without storing a synthetic outcome.
    target=dict(r['baseline'],open_ms=r['target_open_ms'],available_ms=r['target_open_ms']+O.L.TF)
    O.counterfactual(r,target,r['target_open_ms']+O.L.TF)
    key=O.digest(r)
    if db.execute('SELECT 1 FROM counterfactual_registrations WHERE id=?',(key,)).fetchone(): return key
    if (len(encode(r).encode())>MAX_PAYLOAD or
            db.execute('SELECT COUNT(*) FROM counterfactual_registrations').fetchone()[0]>=MAX_CASES):
        raise ValueError('counterfactual_registration_capacity')
    db.execute('INSERT INTO counterfactual_registrations VALUES (?,?,NULL,?)',(key,now,encode(r)))
    return key


def resolve_counterfactuals(db, snapshot, now):
    """Exact-window retry; missing evidence expires explicitly, never becomes zero."""
    from dataclasses import asdict
    report={'added':0,'retry':[],'expired':[]}
    for key,registered,payload in db.execute('SELECT id,registered_ms,payload FROM counterfactual_registrations WHERE terminal_ms IS NULL ORDER BY registered_ms,id LIMIT 32').fetchall():
        r=json.loads(payload);deadline=r['target_open_ms']+O.L.TF
        if now<deadline: continue
        if now>deadline+86400000:
            db.execute('UPDATE counterfactual_registrations SET terminal_ms=? WHERE id=?',(now,key))
            report['expired'].append({'id':key,'reason':'exact_target_unavailable_terminal'})
            continue
        targets=[] if snapshot is None else [dict(asdict(b.candle),version_id=b.version_id) for b in snapshot.bars
                  if b.candle.symbol==r['baseline']['symbol'] and b.candle.open_ms==r['target_open_ms']]
        if len(targets)!=1:
            report['retry'].append({'id':key,'reason':'exact_counterfactual_target_missing_retry'});continue
        record=O.counterfactual(r,targets[0],now)
        put(db,'counterfactual:'+key,record)
        db.execute('UPDATE counterfactual_registrations SET terminal_ms=? WHERE id=?',(now,key))
        report['added']+=1
    return report
