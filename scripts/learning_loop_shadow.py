"""Read-only shadow of the Stage 7 normal learning loop over REAL retained data.

SQLite is opened read-only/query-only. No schema creation, no migration, no
receipts, no queue rows, no Governor/owner writes, no venue calls, no backfill.
An empty captured population is reported as empty; nothing is fabricated.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.learning import dispatch as D, runtime as R, foundation as L


class Reader:
    def __init__(self, db):
        self.db = db

    def query(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params)]


def table(journal, name):
    return bool(journal.query("SELECT 1 FROM sqlite_master WHERE name=?", (name,)))


def run(path, cfg, *, as_of_ms, max_work=64):
    counts = dict(decisions_registered=0, decisions_with_complete_source_manifest=0, stage_events_registered=0,
                  stage_events_with_complete_source_manifest=0, outcomes_resolved=0, outcomes_unresolved=0,
                  replay_complete=0, replay_incomplete=0, generic_registered_rule_matches=0,
                  dispatch_no_proposal_unregistered=0, dispatch_no_proposal_other=0, proposals=0,
                  queued_proposals=0, would_apply=0, policy_unavailable=0, incomplete=0, conflicts=0, stale=0,
                  insufficient=0)
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        journal = Reader(db)
        if table(journal, 'learning_registrations') and table(journal, 'learning_decision_source_manifests'):
            for row in journal.query('SELECT r.event_key, m.payload FROM learning_registrations r '
                                     'JOIN learning_decision_source_manifests m ON m.event_key=r.event_key '
                                     "WHERE r.event_key LIKE 'decision:%'"):
                manifest = json.loads(row['payload'])
                complete = all(d['status'] == 'AVAILABLE' for d in manifest['dependencies'] if d['required'])
                stage = '#' in row['event_key']
                counts['stage_events_registered' if stage else 'decisions_registered'] += 1
                counts['stage_events_with_complete_source_manifest' if stage
                       else 'decisions_with_complete_source_manifest'] += complete
        if table(journal, 'learning_outcome_captures'):
            for row in journal.query("SELECT json_extract(payload,'$.boundary') b FROM learning_outcome_captures"):
                counts['outcomes_unresolved' if row['b'] == 'UNRESOLVED' else 'outcomes_resolved'] += 1
        if table(journal, 'learning_produced_chains'):
            for row in journal.query("SELECT json_extract(payload,'$.replay_gate') g FROM learning_produced_chains"):
                counts['replay_complete' if row['g'] == 'REPLAY_COMPLETE' else 'replay_incomplete'] += 1
        dispatch = D.dispatch_pending(journal, cfg, at_ms=as_of_ms, max_work=min(max_work, 64), shadow=True)
        for receipt in dispatch:
            for r in receipt['results']:
                if r['result'] == 'PROPOSED':
                    counts['generic_registered_rule_matches'] += 1
                elif r['reason'] == 'UNREGISTERED':
                    counts['dispatch_no_proposal_unregistered'] += 1
                else:
                    counts['dispatch_no_proposal_other'] += 1
        if table(journal, R.TABLE):
            counts['proposals'] = journal.query(f'SELECT COUNT(*) n FROM {R.TABLE}')[0]['n']
            applied = "WHERE NOT EXISTS(SELECT 1 FROM learning_application_receipts a WHERE a.proposal_id=q.proposal_id)" \
                if table(journal, 'learning_application_receipts') else ''
            counts['queued_proposals'] = journal.query(f'SELECT COUNT(*) n FROM {R.TABLE} q {applied}')[0]['n']
        for receipt in R.checkpoint(journal, cfg, at_ms=as_of_ms, max_work=64, shadow=True):
            result = receipt['result']
            counts['would_apply'] += result == 'APPLIED'
            counts['policy_unavailable'] += result == 'UNREGISTERED_RULE'
            counts['incomplete'] += result == 'INCOMPLETE_REPLAY'
            counts['conflicts'] += result == 'CONFLICT'
            counts['stale'] += result == 'STALE'
            counts['insufficient'] += result == 'INSUFFICIENT_EVIDENCE'
        return dict(status='PASS', mode='READ_ONLY_SHADOW', source=str(Path(path).resolve()), as_of_ms=as_of_ms,
                    counts=counts, production_registry_rules=sorted(D.production_registry()),
                    production_mutations=db.total_changes, venue_calls=0, real_order_submissions=0,
                    trading_behavior_changed=False,
                    evidence_status='NO_REPLAY_COMPLETE_EVIDENCE' if not counts['replay_complete']
                    else 'EXACT_CAPTURED_EVIDENCE_ONLY',
                    limitation='Counts only prospectively captured, replay-verifiable evidence. Earlier decisions '
                               'are not backfilled; zero does not prove that any real update is eligible.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--as-of-ms', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.journal, yaml.safe_load(args.config.read_text()), as_of_ms=args.as_of_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(L.canonical(result) + '\n')
    print(L.canonical(result))
