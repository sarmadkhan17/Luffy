"""Read-only application decisions over exact real retained LearningEvidence.

No migrations, Governor writes, production receipts, venue calls or backfill.
An empty captured population is reported as empty, never invented proposals.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.learning import application as A, foundation as L


class Reader:
    def __init__(self, db):
        self.db = db

    def query(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]


def run(path, cfg, *, as_of_ms):
    counts=dict(proposals=0, replay_complete=0, registered_rule_eligible=0,
                applicable=0, would_apply=0, insufficient=0, stale_conflict=0,
                unregistered=0, incomplete_replay=0)
    records=[]
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        journal=Reader(db)
        exists=journal.query("SELECT 1 FROM sqlite_master WHERE name='learning_outcome_captures'")
        if exists:
            for row in journal.query('SELECT outcome_id FROM learning_outcome_captures ORDER BY outcome_id'):
                ev,p,req=A.prepare_captured(journal,cfg,row['outcome_id'])
                receipt=A.apply(journal,cfg,ev,p,req,at_ms=as_of_ms,shadow=True)
                counts['proposals']+=1
                complete=L.verified_history(ev)
                counts['replay_complete']+=complete
                counts['registered_rule_eligible']+=bool(complete and p.rule in L.REGISTERED_RULES and p.target in ev.eligible_targets)
                result=receipt['result']
                counts['applicable']+=result=='APPLIED'
                counts['would_apply']+=result=='APPLIED'
                key={'INSUFFICIENT_EVIDENCE':'insufficient','STALE':'stale_conflict','CONFLICT':'stale_conflict',
                     'UNREGISTERED_RULE':'unregistered','INCOMPLETE_REPLAY':'incomplete_replay'}.get(result)
                if key: counts[key]+=1
                records.append(receipt)
        return dict(status='PASS',mode='READ_ONLY_SHADOW',source=str(Path(path).resolve()),
                    as_of_ms=as_of_ms,counts=counts,records=records,
                    production_mutations=db.total_changes,venue_calls=0,
                    trading_behavior_changed=False,
                    evidence_status='NO_REPLAY_COMPLETE_EVIDENCE' if not counts['replay_complete'] else 'EXACT_CAPTURED_EVIDENCE_ONLY',
                    limitation='Unregistered legacy decisions are not LearningEvidence or proposals; no backfill or sample aggregation.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--as-of-ms',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.journal,yaml.safe_load(args.config.read_text()),as_of_ms=args.as_of_ms)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(L.canonical(result)+'\n')
    print(L.canonical({k:v for k,v in result.items() if k!='records'}))
