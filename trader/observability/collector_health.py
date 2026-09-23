"""Strict, scan-bound collector receipts. No trading authority or source writes."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

SCHEMA = 'attention-collector-health.v2'
IDENTITY_SCHEMA = 'attention-scan-identity.v1'
RECOVERY_SCANS = 2
QUARANTINE_MS = 300000
MAX_HEALTH_BYTES = 16384


def clock_ms():
    return int(time.time()*1000)


def code_hash():
    root = Path(__file__).resolve().parents[1]
    names = ('observability/collector.py', 'observability/collector_health.py',
             'observability/attention.py', 'observability/supplemental.py',
             'observability/_supplemental_child.py',
             'observability/store.py', 'observability/worker.py',
             'cognition/attention.py', 'cognition/contracts.py')
    return hashlib.sha256(b''.join(name.encode()+b'\0'+(root/name).read_bytes() for name in names)).hexdigest()


def integer(v, minimum=0):
    return type(v) is int and v >= minimum


def identity(v):
    return (isinstance(v, dict) and v.get('schema') == IDENTITY_SCHEMA
            and isinstance(v.get('instance_id'), str) and len(v['instance_id']) == 32
            and all(c in '0123456789abcdef' for c in v['instance_id'])
            and integer(v.get('seq'), 1))


class Refused(ValueError):
    def __init__(self, reason, evidence=None):
        super().__init__(reason)
        self.reason, self.evidence = reason, evidence or {}


def validate(h, now, fresh_ms):
    if h.get('health_schema') == 'declared-population-health.v1':
        return validate_declared(h, now, fresh_ms)
    def refuse(code):
        raise Refused(code, {k:h.get(k) for k in ('instance_id','failure_generation','errors','fence_seq','updated_ms')})
    if h.get('health_schema') != SCHEMA:
        refuse('collector_health_schema_old')
    if not identity(dict(schema=IDENTITY_SCHEMA, instance_id=h.get('instance_id'), seq=1)):
        refuse('collector_health_invalid')
    for k in ('updated_ms','failure_generation','fence_seq','errors','dropped','capture_errors','worker_errors','details_lost','process_started_ms'):
        if not integer(h.get(k)): refuse('collector_health_invalid')
    if h['errors'] != h['capture_errors']+h['worker_errors'] or h['failure_generation'] != h['errors']+h['dropped']:
        refuse('collector_health_invalid')
    if h['process_started_ms'] > h['updated_ms']: refuse('collector_health_invalid')
    if h['updated_ms'] > now: refuse('collector_health_future')
    if now-h['updated_ms'] > fresh_ms: refuse('collector_health_stale')
    if h.get('worker_alive') is not True: refuse('collector_worker_dead')
    if h.get('last_error') or h.get('status') not in ('ok','recovered'):
        refuse('collector_failing')
    complete = h.get('last_complete')
    if not isinstance(complete, dict) or not integer(complete.get('seq'),1) or not isinstance(complete.get('scan_id'),str):
        refuse('snapshot_incomplete')
    cert = h.get('certificate')
    if h['failure_generation']:
        if not integer(h.get('first_error_ms')) or not integer(h.get('last_error_ms')) or not h['process_started_ms'] <= h['first_error_ms'] <= h['last_error_ms'] <= h['updated_ms']:
            refuse('collector_health_invalid')
        if not isinstance(cert, dict): refuse('collector_recovery_pending')
        if any(cert.get(k)!=h[k] for k in ('instance_id','failure_generation','fence_seq')):
            refuse('collector_recovery_pending')
        scans=cert.get('scans')
        if not isinstance(scans,list) or len(scans)!=RECOVERY_SCANS: refuse('collector_recovery_pending')
        if any(not isinstance(s,dict) or not integer(s.get('seq'),1) or not isinstance(s.get('scan_id'),str) for s in scans):
            refuse('collector_recovery_pending')
        if not h['fence_seq'] < scans[0]['seq'] < scans[1]['seq'] <= complete['seq'] or scans[0]['scan_id']==scans[1]['scan_id']:
            refuse('collector_recovery_pending')
        if not integer(cert.get('issued_ms')) or not integer(h.get('last_error_ms')) or cert['issued_ms']>h['updated_ms'] or cert['issued_ms']-h['last_error_ms']<QUARANTINE_MS:
            refuse('collector_recovery_pending')
    elif cert is not None or h['fence_seq'] or h.get('first_error_ms') is not None or h.get('last_error_ms') is not None:
        refuse('collector_health_invalid')
    return {k:h.get(k) for k in ('instance_id','failure_generation','fence_seq','certificate','errors','first_error_ms','last_error_ms','details_lost','health_schema','last_complete')}


def read_health(path):
    try:
        with Path(path).open('rb') as f: raw=f.read(MAX_HEALTH_BYTES+1)
        if len(raw)>MAX_HEALTH_BYTES: raise ValueError()
        h=json.loads(raw)
        if not isinstance(h,dict): raise ValueError()
        return h
    except (OSError,ValueError,TypeError):
        raise Refused('collector_health_missing') from None


def bound_snapshot(path, *, now_ms=None, fresh_ms=300000):
    """Two health observations around a bounded coherent read of EXACT scan ID.

    Explicit clocks remain frozen for deterministic offline replay. Live observation
    clocks are sampled after reads; registration/forecast protocol clocks are unchanged.
    """
    path=Path(path).resolve()
    hp=path.with_name('attention_health.json')
    clock=(lambda:now_ms) if now_ms is not None else clock_ms
    h=read_health(hp); observed=clock(); ev=validate(h,observed,fresh_ms)
    sid=h['last_complete']['scan_id']
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
            deadline=time.monotonic()+1
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            db.execute('BEGIN')
            row=db.execute('SELECT payload,causes_complete FROM scans WHERE scan_id=?',(sid,)).fetchone()
            if not row or not row[0] or not row[1]: raise Refused('snapshot_incomplete',ev)
            if len(row[0])>2*1024**2: raise Refused('source_payload_bound',ev)
            scan=json.loads(row[0]); ident=scan.get('collector_identity')
            expected=dict(schema=IDENTITY_SCHEMA,instance_id=h['instance_id'],seq=h['last_complete']['seq'])
            if ident!=expected or scan.get('scan_id')!=sid: raise Refused('snapshot_identity_mismatch',ev)
            if h.get('health_schema') == 'declared-population-health.v1':
                scope=scan.get('scope',{})
                members=[m['symbol'] for m in scan.get('membership',[])]
                receipts=scope.get('availability_receipts',[])
                if (scope.get('protocol')!=h.get('protocol')
                        or scope.get('declaration_version')!=h.get('declaration_version')
                        or not 1<=len(members)<=64 or len(set(members))!=len(members)
                        or [r.get('symbol') for r in receipts]!=members
                        or scope.get('excluded_count')!=0):
                    raise Refused('declared_membership_mismatch',ev)
            count,size=db.execute('SELECT COUNT(*),MAX(length(v.payload)) FROM versions v JOIN scan_versions s ON s.version_id=v.id WHERE s.scan_id=?',(sid,)).fetchone()
            if count>4096 or (size or 0)>4096: raise Refused('input_bound_exceeded',ev)
            bars={}
            for vid,available,payload in db.execute('SELECT v.id,v.first_seen_ms,v.payload FROM versions v JOIN scan_versions s ON s.version_id=v.id WHERE s.scan_id=?',(sid,)):
                b=json.loads(payload); b.update(version_id=vid,available_ms=available)
                bars.setdefault(b['symbol'],[]).append(b)
            cause_rows=db.execute('SELECT payload FROM causes WHERE scan_id=? LIMIT 66',(sid,)).fetchall()
            if len(cause_rows)>65: raise Refused('input_bound_exceeded',ev)
            causes=[json.loads(p) for (p,) in cause_rows]
            if not causes or any(c.get('collector_identity')!=expected for c in causes) or not any(c.get('collector_completion') is True for c in causes):
                raise Refused('snapshot_identity_mismatch',ev)
            causes=[c for c in causes if not c.get('collector_completion')]
        snapshot_observed=clock()
        if not integer(scan.get('as_of_ms')) or scan['as_of_ms']>snapshot_observed:
            raise Refused('snapshot_future',ev)
        if snapshot_observed-scan['as_of_ms']>fresh_ms: raise Refused('snapshot_stale',ev)
        h2=read_health(hp); after=clock(); validate(h2,after,fresh_ms)
        if any(h.get(k)!=h2.get(k) for k in ('instance_id','failure_generation','fence_seq','certificate','last_complete')):
            raise Refused('health_changed_during_read',ev)
        ev.update(accepted=True,health_read_ms=observed,snapshot_observed_ms=snapshot_observed,
                  health_rechecked_ms=after,health_published_ms=h['updated_ms'])
        return (scan,bars,causes),ev
    except Refused:
        raise
    except (sqlite3.Error,ValueError,KeyError,TypeError):
        raise Refused('snapshot_unavailable',ev) from None


def record(db, pop, evidence, now):
    """Retain generation transitions; append gap receipts, never rewrite a scan."""
    db.execute('CREATE TABLE IF NOT EXISTS collector_receipts (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    key='last' if pop is None else 'last:'+pop.version
    prior=db.execute('SELECT payload FROM collector_receipts WHERE key=?',(key,)).fetchone()
    prior=json.loads(prior[0]) if prior else {}
    if evidence.get('instance_id'):
        if pop and prior.get('instance_id') and prior['instance_id']!=evidence['instance_id']:
            pop.emit('collector-instance:'+evidence['instance_id'],'gap',dict(reason='collector_instance_changed',observed_ms=now,previous=prior,evidence=evidence))
        cert=evidence.get('certificate')
        if pop and evidence.get('accepted') and cert and prior.get('certificate')!=cert:
            pop.emit('collector-recovery:'+evidence['instance_id']+':'+str(evidence['failure_generation']), 'gap',
                     dict(reason='collector_outage',observed_ms=now,interval_ms=[evidence['first_error_ms'],cert['issued_ms']],evidence=evidence))
        db.execute('INSERT OR REPLACE INTO collector_receipts VALUES (?,?)',(key,json.dumps(evidence,sort_keys=True)))


def validate_declared(h, now, fresh_ms):
    """Scheduled pass health: completed persistence, not a live daemon claim."""
    complete=h.get('last_complete')
    if (h.get('status')!='ok' or h.get('protocol')!='declared-population-public-4h.v1'
            or not integer(h.get('started_ms')) or not integer(h.get('updated_ms'))
            or not h['started_ms'] <= h['updated_ms'] <= now
            or now-h['updated_ms']>fresh_ms or not isinstance(complete,dict)
            or not isinstance(complete.get('scan_id'),str)
            or not identity(dict(schema=IDENTITY_SCHEMA,instance_id=h.get('instance_id'),seq=complete.get('seq')))):
        raise Refused('declared_collector_unavailable', dict(protocol=h.get('protocol')))
    return {k:h.get(k) for k in ('health_schema','protocol','last_complete','declaration_version')}
