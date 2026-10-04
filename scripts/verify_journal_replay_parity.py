#!/usr/bin/env python3
"""Run actual registered replay code on every recorded outcome, offline."""
import dataclasses,hashlib,json,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.learning import capture as C,foundation as L
from trader.core import journal_evidence as E
from compact_journal import protect_source,dump

root=Path(__file__).resolve().parents[2];source=protect_source(Path('/mnt/luffy-recovery/recovery/journal-working-20261004T141639Z-c048bf08/journal/luffy.db'));target=protect_source(root/'candidate/luffy.db');E.install(target)
report={'status':'VERIFYING','recorded_learning_replays':0,'recorded_learning_replay_results':{},'source_replay_errors':{},'source_research_tables':{},'auxiliary_revision_cut_replays':0,'mismatches':[]}

def replay(db,oid):
    try:
        outcome,retained=C.learning_outcome(db,oid)
        replay=L.replay(outcome,retained)
        return {'status':replay.status,'replay_id':replay.replay_id,'content_sha256':L.digest(dataclasses.asdict(replay))}
    except Exception as error:
        return {'source_error':type(error).__name__+':'+str(error)}

for (oid,) in source.execute('SELECT outcome_id FROM learning_outcome_captures ORDER BY rowid'):
    original=replay(source,oid);new=replay(target,oid)
    if original!=new:report['mismatches'].append({'outcome_id':oid,'source':original,'candidate':new})
    report['recorded_learning_replays']+=1
    bucket=report['source_replay_errors'] if 'source_error' in original else report['recorded_learning_replay_results'];key=original.get('status') or original['source_error'];bucket[key]=bucket.get(key,0)+1
    if report['recorded_learning_replays']%50==0:
        dump(root/'evidence/replay-parity.json',report);print(json.dumps({'replays':report['recorded_learning_replays'],'mismatches':len(report['mismatches'])}),flush=True)
# Actual local receipt-at-cut auxiliary retrieval, retaining complete original
# payload, request/acquisition clocks, raw hashes and revision identity. Each
# recorded auxiliary revision supplies its original availability cut; no latest
# state substitution and no requests to external stores/providers.
for rev,key,event,available,observed,raw in source.execute('SELECT * FROM market_revisions ORDER BY rowid'):
    cut=max(available,observed)
    sql='SELECT revision_id,record_json FROM market_revisions WHERE series_key=? AND available_ms<=? AND observed_ms<=? ORDER BY observed_ms DESC,rowid DESC LIMIT 1'
    a=source.execute(sql,(key,cut,cut)).fetchone();b=target.execute(sql,(key,cut,cut)).fetchone()
    if a!=b:report['mismatches'].append({'revision_id':rev,'type':'auxiliary_revision_at_cut'})
    report['auxiliary_revision_cut_replays']+=1
for table in ['research_questions','research_plans','research_evidence','research_results','research_runs','research_bank_objects']:
    report['source_research_tables'][table]=source.execute('SELECT count(*) FROM '+table).fetchone()[0]
report['supported_readers']='Journal.query logical SQL; direct SQL brain_events_logical_v1 after journal_evidence.install; attached authorizer-limited research views/source_query; all production owner/dashboard/graphql callers use Journal.query. Source historical serialized hashes remain exact.'
report['status']='PASS' if not report['mismatches'] else 'FAIL';dump(root/'evidence/replay-parity.json',report)
if report['mismatches']:raise SystemExit('replay mismatch')
print(json.dumps(report),flush=True)
