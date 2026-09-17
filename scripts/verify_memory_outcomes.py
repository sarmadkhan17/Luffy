"""Read-only exact forecast/memory verification at the actual UTC clock.

Run with python -m scripts.verify_memory_outcomes --output /tmp/outcomes.json.
Never resolves a missing target from a nearby bar or changes safety controls.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

from trader.observability import outcomes as O, memory as M, learning as L
from trader.cognition import investigation as I


def ro(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row;db.execute('BEGIN');return db


def verify(root, now):
    result={'checked_utc':datetime.fromtimestamp(now/1000,timezone.utc).isoformat(),'forecasts':[], 'investigations':[]}
    with closing(ro(root/'data/attention_learning.db')) as db, closing(ro(root/'data/attention.db')) as source:
        for row in db.execute('SELECT * FROM episodes ORDER BY created_ms,id'):
            e=dict(row);p=json.loads(e['prediction']);o=json.loads(e['outcome']) if e['outcome'] else None
            assert e['created_ms']==p['registered_ms'] and e['protocol_id']==p['protocol_id']==L.PROTOCOL_ID
            assert p['target_open_ms']==(e['created_ms']//L.TF+1)*L.TF
            assert e['deadline_ms']==p['target_open_ms']+L.TF
            assert e['id']==O.O.digest([L.PROTOCOL_ID,e['symbol'],p['target_open_ms']])
            assert p['baseline_close']==p['input_window'][-1]['close']
            scan_row=source.execute('SELECT payload FROM scans WHERE scan_id=?',(p['scan_id'],)).fetchone()
            if scan_row:
                scan=json.loads(scan_row[0])
                sr=next(r for r in scan['rows'] if r['symbol']==e['symbol'])
                assert bool(sr.get('selected'))==p['selected'] and scan['as_of_ms']==p['observed_ms']
                assert scan['code_manifest']==p['source_code_manifest']
            missing=[]
            for b in p['input_window']:
                row=source.execute('SELECT v.payload,v.first_seen_ms FROM versions v JOIN scan_versions s ON s.version_id=v.id WHERE s.scan_id=? AND v.id=?',(p['scan_id'],b['version_id'])).fetchone()
                if row is None:
                    row=source.execute('SELECT payload,first_seen_ms FROM versions WHERE id=?',(b['version_id'],)).fetchone()
                if row is None:
                    missing.append(b['version_id']);continue
                assert dict(json.loads(row[0]),version_id=b['version_id'],available_ms=row[1])==b
                assert L.usable(b,p['observed_ms'])
            status='not_matured' if now<e['deadline_ms'] else 'exact_outcome_missing_retry'
            if e['status']=='resolved':
                try:
                    O._forecast(root/'data/attention.db',dict(e,prediction=p,outcome=o),now)
                except ValueError as exc:
                    if str(exc) != 'exact_source_version_missing_retry':
                        raise
                    status='exact_source_version_missing_retry'
                else:
                    status='exact_frozen_outcome_verified'
            result['forecasts'].append(dict(id=e['id'],symbol=e['symbol'],status=e['status'],verification=status,
                registered_ms=e['created_ms'],target_key=[e['symbol'],p['target_open_ms']],deadline_ms=e['deadline_ms'],
                registration_scan_retained=bool(scan_row),missing_versions_retry=missing,selected=p['selected'],protocol_id=e['protocol_id'],baseline_close=p['baseline_close'],
                baseline_versions=[b['version_id'] for b in p['input_window']],outcome=o))
    with closing(ro(root/'data/investigation.db')) as db:
        for iid,payload in db.execute('SELECT id,payload FROM cases ORDER BY created_ms,id'):
            inv=I.investigation_from_dict(json.loads(payload));archive=M.source_archive(db,iid)
            bars=[I.InputBar(b['version_id'],I.Candle(**b['candle'])) for b in archive['inputs']]
            baseline=[b for b in bars if b.version_id in inv.state.input_versions]
            assert I.open_investigation(inv.state,inv.primary_trigger,baseline,inv.registered_ms)==inv
            result['investigations'].append(dict(id=iid,registered_ms=inv.registered_ms,
                deadline_ms=inv.measurement.deadline_ms,target_keys=inv.measurement.target_keys,
                baseline_versions=inv.state.input_versions,memory_status=M.owner_context(db,iid)['status']))
    result['matured']=sum(now>=e['deadline_ms'] for e in result['forecasts'])
    result['retry_required']=any(e['verification'] in ('exact_outcome_missing_retry','exact_source_version_missing_retry') or e['missing_versions_retry'] for e in result['forecasts'])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[1]
    result=verify(root,int(time.time()*1000))
    with a.output.open('x') as out: json.dump(result,out,indent=2)
    print(L.encode({'checked_utc':result['checked_utc'],'forecasts':len(result['forecasts']),
                    'matured':result['matured'],'retry_required':result['retry_required']}))


if __name__=='__main__': main()
