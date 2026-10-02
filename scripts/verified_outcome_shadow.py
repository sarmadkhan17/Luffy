"""Read-only exact retained outcome shadow; no migrations or venue calls."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.learning import foundation as L, capture as C


def run(path):
    counts=dict(realized=0,rejected=0,risk_blocked=0,cash=0,missed=0,research=0,
                execution_quality=0,data_quality=0,replay_complete=0,replay_incomplete=0,
                learning_evidence_created=0,applicable_updates=0)
    mapping={L.Kind.REJECTED:'rejected',L.Kind.RISK_BLOCKED:'risk_blocked',L.Kind.CASH:'cash',
             L.Kind.MISSED:'missed',L.Kind.RESEARCH:'research',L.Kind.EXECUTION:'execution_quality',
             L.Kind.DATA:'data_quality',L.Kind.INCIDENT:'data_quality'}
    records=[]
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing=0
        if 'decisions' in tables:
            if 'learning_registrations' in tables:
                missing=db.execute("SELECT count(*) FROM decisions WHERE 'decision:'||id NOT IN (SELECT event_key FROM learning_registrations)").fetchone()[0]
            else:
                missing=db.execute('SELECT count(*) FROM decisions').fetchone()[0]
        if 'learning_outcome_captures' in tables:
            for (oid,) in db.execute('SELECT outcome_id FROM learning_outcome_captures ORDER BY outcome_id').fetchall():
                o,s=C.learning_outcome(db,oid)
                if o.boundary==L.Boundary.UNRESOLVED: continue
                a=L.attribute(o);r=L.replay(o,s);e=L.evidence(o,a,r)
                current=dict(version_id=o.lineage.version_id,spec_hash=o.lineage.spec_hash,state='UNKNOWN')
                p=L.propose(e,L.Target.LIFECYCLE,current,rule=L.DECAY_RULE)
                if o.kind==L.Kind.EXECUTED and o.boundary==L.Boundary.REALIZED: counts['realized']+=1
                elif o.kind in mapping: counts[mapping[o.kind]]+=1
                counts['replay_complete' if r.status=='COMPLETE' else 'replay_incomplete']+=1
                counts['learning_evidence_created']+=1
                counts['applicable_updates']+=p.status==L.Status.APPLICABLE
                records.append(dict(outcome_id=oid,kind=o.kind.value,boundary=o.boundary.value,
                    replay_status=r.status,faults=r.faults,evidence_id=e.evidence_id,proposal_status=p.status.value))
        # Missing original registrations are inventory faults, never invented outcomes.
        counts['replay_incomplete']+=missing
        return dict(status='PASS',counts=counts,unregistered_decisions=missing,records=records,
                    production_mutations=0,venue_calls=0,live_learning_applications=0,
                    limitation='Counts classify exact captured outcomes only; unregistered history is replay-incomplete without backfill.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=run(args.journal)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(L.canonical(result)+'\n')
    print(L.canonical({k:v for k,v in result.items() if k!='records'}))
