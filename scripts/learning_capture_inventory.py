#!/usr/bin/env python3
"""Read-only, bounded replay inventory. Never registers or repairs old sources."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.learning_shadow import run as legacy_shadow
from trader.learning import capture as C, foundation as L


def run(journal,as_of_ms,limit):
    # The legacy classifier retains observed records as context, without
    # manufacturing original registration or source availability timestamps.
    with tempfile.TemporaryDirectory(prefix='luffy-learning-inventory-') as tmp:
        legacy=legacy_shadow(journal,Path(tmp),as_of_ms,limit)
        events=json.loads(Path(legacy['events_path']).read_text())
    counts=dict(total=0,complete=0,partial=0,incomplete=0,unassessable=0)
    identities=[]
    for event in events:
        boundary=event['outcome']['boundary']
        state='unassessable' if boundary=='UNASSESSABLE' else 'incomplete' if boundary=='UNRESOLVED' else 'partial'
        counts[state]+=1;counts['total']+=1
        identities.append([event['outcome']['outcome_id'] if 'outcome_id' in event['outcome'] else L.digest(event['outcome']),state])
    db=sqlite3.connect(Path(journal).resolve().as_uri()+'?mode=ro',uri=True)
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    captured=0
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_outcome_captures'").fetchone():
            for oid,payload in db.execute('SELECT outcome_id,payload FROM learning_outcome_captures ORDER BY outcome_id LIMIT ?',(limit,)).fetchall():
                if json.loads(payload)['observed_ms']>as_of_ms: continue
                try:
                    m=C.manifest(db,oid)
                    state={'REPLAY_COMPLETE':'complete','REPLAY_PARTIAL':'partial','REPLAY_INCOMPLETE':'incomplete','UNASSESSABLE':'unassessable'}[m['status']]
                    if state=='complete':
                        o,s=C.learning_outcome(db,oid)
                        if L.replay(o,s).status!='COMPLETE': state='incomplete'
                except (ValueError,KeyError,TypeError,sqlite3.Error):
                    state='incomplete'
                counts[state]+=1;counts['total']+=1;captured+=1;identities.append([oid,state])
    finally:
        db.rollback();db.close()
    return dict(schema='learning-capture-inventory.v1',status='PASS',as_of_ms=as_of_ms,
        source=str(Path(journal).resolve()),counts=counts,
        scope='bounded legacy decision-outcome chains plus bounded prospective immutable captures; pending and final captures are separate records',
        limit_per_legacy_stratum=limit,limit_prospective=limit,legacy_chains=len(events),prospective_captures=captured,
        total_decisions=legacy['total_decisions'],selection_sha256=L.digest(identities),
        applicable_updates=0,production_mutations=0,
        legacy_policy='partial retains outcome/decision context only; unresolved incomplete; unverified closed trades unassessable; no source backfill')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal',type=Path,required=True)
    p.add_argument('--as-of-ms',type=int,required=True)
    p.add_argument('--limit',type=int,required=True)
    args=p.parse_args()
    print(L.canonical(run(args.journal,args.as_of_ms,args.limit)))
