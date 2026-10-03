"""Bounded read-only retained registrations, exact replay faults and claim queries."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.learning import capture as C, foundation as F


def run(path, limit=64):
    if not 1 <= limit <= 256: raise ValueError('shadow_bound_invalid')
    counts=dict(scan_registrations=0,allocation_registrations=0,risk_linked_registrations=0,
        resolved_outcomes=0,replay_complete=0,replay_partial=0,replay_incomplete=0,
        unassessable=0,world_model_claims_queried=0,governed_overlays_applicable=0)
    missing={}
    attention=Path(path).parent/'attention.db'
    attention_samples=0
    if attention.is_file():
        with sqlite3.connect(attention.resolve().as_uri()+'?mode=ro',uri=True) as scans:
            scans.execute('PRAGMA query_only=ON');scans.execute('BEGIN')
            if scans.execute("SELECT 1 FROM sqlite_master WHERE name='scans'").fetchone():
                for payload, in scans.execute('SELECT payload FROM scans WHERE payload IS NOT NULL ORDER BY as_of_ms DESC LIMIT ?',(limit,)):
                    attention_samples+=1
                    for row in json.loads(payload).get('rows',[]):
                        for claim in row.get('world_model',{}).get('claims',[]):
                            counts['world_model_claims_queried']+=1
                            counts['governed_overlays_applicable']+=claim.get('learned') is not None
            if scans.total_changes: raise ValueError('shadow_mutated_attention')
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'learning_registrations' in tables:
            for row in db.execute("SELECT event_key,payload FROM learning_registrations WHERE event_key LIKE 'decision:%' ORDER BY rowid DESC LIMIT ?",(limit,)):
                reg=json.loads(row['payload'])
                counts['allocation_registrations' if reg.get('chain') else 'scan_registrations']+=1
                if 'learning_actions' in tables:
                    counts['risk_linked_registrations']+=bool(db.execute("SELECT 1 FROM learning_actions WHERE event_key=? AND json_extract(payload,'$.dependency.role')='risk_decision' LIMIT 1",(row['event_key'],)).fetchone())
        samples=[]
        if 'learning_outcome_captures' in tables:
            rows=db.execute("SELECT outcome_id,payload FROM learning_outcome_captures WHERE json_extract(payload,'$.boundary')!='UNRESOLVED' ORDER BY rowid DESC LIMIT ?",(limit,)).fetchall()
            for row in rows:
                counts['resolved_outcomes']+=1
                try:
                    m=C.manifest(db,row['outcome_id'])
                    status=m['status']
                    key={'REPLAY_COMPLETE':'replay_complete','REPLAY_PARTIAL':'replay_partial','UNASSESSABLE':'unassessable'}.get(status,'replay_incomplete')
                    counts[key]+=1
                    faults=[d['role'] for d in m['dependencies'] if d['required'] and d['status']!='AVAILABLE']
                    for role in faults: missing[role]=missing.get(role,0)+1
                    samples.append(dict(outcome_id=row['outcome_id'],status=status,missing_roles=faults,decision_faults=m['decision_source_faults']))
                except (ValueError,KeyError,TypeError) as exc:
                    counts['replay_incomplete']+=1
                    samples.append(dict(outcome_id=row['outcome_id'],status='REFUSED',reason=str(exc)))
        return dict(status='PASS',mode='READ_ONLY_SHADOW',source=str(Path(path).resolve()),limit=limit,
            counts=counts,attention_scans_inspected=attention_samples,exact_missing_roles=missing,samples=samples,production_mutations=db.total_changes,
            real_order_submissions=0,trading_behavior_changed=False,
            world_claim_query_basis='Retained normal Attention queries; no production WorldClaim emitter or production WorldModel learning rule exists.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--limit',type=int,default=64)
    a=p.parse_args();result=run(a.journal,a.limit)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(F.canonical(result)+'\n')
    print(F.canonical(result))
