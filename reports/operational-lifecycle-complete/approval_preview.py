"""Read-only exact Learning completion proofs; source mount must deny writes."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from trader.learning import capture as C
from trader.core import journal_evidence as E
DATA=Path('/mnt/luffy-data/luffy/production/data');OUT=Path(__file__).resolve().parent
assert os.statvfs(DATA).f_flag & os.ST_RDONLY
preview=dict(version='completed-lifecycle-approval.v1',production_apply_authorized=False,
    source_readonly_mount=True,production_writes=0,learning_records=[],refused=[],
    research_files=[],portfolio_blobs=[],
    research_requirement='No production Research Bank/work identity was established by supplied operational catalog. Approval needs exact Bank, terminal result, capsule hash and unchanged owned file proofs; dormant preparation files remain excluded.',
    portfolio_requirement='Approval needs a completed reference sweep over the entire applying private owner ledger, exact blob proof and immutable evaluation witness. A three-evaluation sample is not a whole-store deletion authorization.')
with closing(sqlite3.connect((DATA/'luffy.db').as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA temp_store=MEMORY');db.execute('BEGIN')
    E.install(db)
    keys=dict(learning_source_blobs='sha256',learning_registrations='event_key',learning_outcome_captures='outcome_key',learning_actions='action_id',learning_decision_source_manifests='manifest_id',learning_produced_chains='outcome_id')
    seen=set()
    for oid, in db.execute('SELECT outcome_id FROM learning_produced_chains ORDER BY rowid DESC LIMIT 8').fetchall():
        try:
            m=C.manifest(db,oid)
            if m['status']!='REPLAY_COMPLETE': raise ValueError('incomplete_replay_manifest')
            body=m['capture'];reg=body['registration']
            targets=[('learning_produced_chains',oid),('learning_outcome_captures',body['outcome_key']),('learning_registrations',reg['event_key']),('learning_decision_source_manifests',reg['decision_source_manifest_id'])]
            targets += [('learning_actions',row[0]) for row in db.execute('SELECT action_id FROM learning_actions WHERE event_key=? LIMIT 64',(reg['event_key'],))]
            shas={d['locator']['sha256'] for d in list(body['dependencies'])+list(reg['dependencies']) if d.get('storage')=='SNAPSHOT' and d.get('status')=='AVAILABLE'}|set(m['data_blobs'])
            targets += [('learning_source_blobs',sha) for sha in sorted(shas)]
            for table,key in targets:
                if (table,key) in seen: continue
                seen.add((table,key))
                row=db.execute('SELECT payload FROM '+table+' WHERE '+keys[table]+'=?',(key,)).fetchone()
                if not row or row[0].startswith(E.PREFIX) or len(row[0].encode())<1024: continue
                sha=hashlib.sha256(row[0].encode()).hexdigest()
                preview['learning_records'].append(dict(database='luffy.db',table=table,key=key,outcome_id=oid,
                    before_sha256=sha,logical_sha256=sha,inline_bytes=len(row[0].encode()),rule='ONLY_EXACT_LOGICALLY_IMMUTABLE_REPRESENTATION_REPLACEMENT; NO LOGICAL ROW DELETION'))
        except (ValueError,KeyError,TypeError,sqlite3.Error) as exc: preview['refused'].append(dict(outcome_id=oid,reason=str(exc)))
    db.rollback()
market=json.loads((OUT/'operational-dry-run.json').read_text())
preview['market_records']=market['approval_records']
preview['approval_conditions']='Owner authorizes first production purge separately; applying code must recheck exact identity, unchanged hashes, known schema/triggers, current references, transaction ownership and bounds. Offline evidence is not production authorization.'
(OUT/'first-production-purge-approval.json').write_text(json.dumps(preview,indent=2))
print(json.dumps(dict(learning=len(preview['learning_records']),market=len(preview['market_records']),learning_refused=len(preview['refused']),production_writes=0)))
