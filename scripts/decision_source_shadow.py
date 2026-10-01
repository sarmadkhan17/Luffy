#!/usr/bin/env python3
"""Bounded read-only live journal audit. Never creates historical provenance."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.learning import capture as C, decision_sources as D, foundation as L


def run(journal, limit=100):
    if type(limit) is not int or not 1 <= limit <= 1000: raise ValueError('bounded_limit_required')
    db=sqlite3.connect(Path(journal).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    counts=dict(decisions=0,manifests=0,complete=0,incomplete=0)
    missing={}; records=[]
    try:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows=db.execute('SELECT id,cycle_id,ts FROM decisions ORDER BY ts DESC,id LIMIT ?', (limit,)).fetchall()
        for row in rows:
            counts['decisions']+=1
            event='decision:'+row['id']; faults=[]; identity=None
            try:
                if 'learning_registrations' not in tables: raise ValueError('original_registration_missing_no_backfill')
                _,reg=C.registration(db,event)
                identity=reg.get('decision_source_manifest_id')
                if not identity or D.TABLE not in tables: raise ValueError('decision_manifest_missing_no_backfill')
                m=D.load(db,identity); counts['manifests']+=1
                sources={}
                for d in reg['dependencies']:
                    if d['status']=='AVAILABLE':
                        try: sources[d['role']]=C.resolve(db,d)
                        except (ValueError,KeyError,TypeError,sqlite3.Error) as exc: faults.append(str(exc))
                faults.extend(D.verify(m,reg,sources))
            except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:
                faults.append(str(exc))
                faults.extend('missing:'+role for role in sorted(D.MANDATORY))
            complete=not faults
            counts['complete' if complete else 'incomplete']+=1
            for fault in sorted(set(faults)): missing[fault]=missing.get(fault,0)+1
            records.append(dict(decision_id=row['id'],cycle_id=row['cycle_id'],ts=row['ts'],manifest_id=identity,
                                status='COMPLETE' if complete else 'INCOMPLETE',faults=sorted(set(faults))))
    finally:
        db.rollback();db.close()
    return dict(schema='decision-source-shadow.v1',status='PASS',source=str(Path(journal).resolve()),
        scope='latest retained live decisions; no new decision producer invocation or historical source insertion',
        counts=counts,missing_dependencies=missing,decisions=records,production_mutations=0,
        authenticated_requests=0,trading_behavior_changed=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--journal',type=Path,required=True)
    p.add_argument('--limit',type=int,default=100);a=p.parse_args()
    print(L.canonical(run(a.journal,a.limit)))
