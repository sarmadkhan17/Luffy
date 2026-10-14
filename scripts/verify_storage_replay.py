#!/usr/bin/env python3
"""Compare every retained historical portfolio replay on the two isolated copies."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.core import journal_evidence as E
from trader.portfolio.runtime import replay
from trader.portfolio.allocator import canonical


def verify(path):
    db=E.install(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True))
    results=[];failures=[];times=[]
    for cut_id,marker in db.execute('SELECT cut_id,payload FROM evaluations ORDER BY rowid'):
        raw=E.resolve(db,marker);body=json.loads(raw)
        start=time.perf_counter()
        try:
            value=replay(body)
            result={'cut_id':cut_id,'logical_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                    'replay_sha256':hashlib.sha256(canonical(value).encode()).hexdigest(),'status':'PASS'}
        except Exception as exc:
            result={'cut_id':cut_id,'logical_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                    'status':'REFUSED','exception_type':type(exc).__name__,'error':str(exc)}
            failures.append(result)
        results.append(result);times.append(time.perf_counter()-start)
        print(path,cut_id,result['status'],flush=True)
    # Actual SQL point reads: include payload fetch cost, no network.
    ids=[r['cut_id'] for r in results];query_times=[]
    for _ in range(3):
        for cut in ids:
            start=time.perf_counter();db.execute('SELECT payload FROM evaluations WHERE cut_id=?',(cut,)).fetchone();query_times.append(time.perf_counter()-start)
    db.close()
    return {'records':results,'replayed':len(results)-len(failures),'refusals':failures,
            'replay_median_seconds':statistics.median(times) if times else None,
            'query_median_seconds':statistics.median(query_times) if query_times else None,
            'bytes':Path(path).stat().st_size}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--before',required=True);p.add_argument('--after',required=True);p.add_argument('--report',required=True);a=p.parse_args()
    before=verify(a.before);after=verify(a.after)
    result={'before':before,'after':after,'exact_replay_parity':before['records']==after['records']}
    Path(a.report).write_text(json.dumps(result,indent=2))
    if not result['exact_replay_parity']:raise SystemExit('REPLAY_PARITY_FAILED')
