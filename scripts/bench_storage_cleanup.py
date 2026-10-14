#!/usr/bin/env python3
"""Offline DA-10 benchmark using actual retained market rows and fake retrievals."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time

p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--copies',required=True);p.add_argument('--output',required=True);p.add_argument('--scratch',required=True)
a=p.parse_args();sys.path.insert(0,str(Path(a.repo).resolve()))
from trader.data import market_provenance as mp
from trader.data import sqlite_tx
from trader.data.derivatives import DerivFeed
import pandas as pd

scratch=Path(a.scratch);scratch.mkdir(parents=True,exist_ok=False)
results={}
for filename,table,columns,keys in (
 ('candles.db','candles',('symbol','tf','ts','open','high','low','close','volume','taker_buy'),('symbol','tf','ts')),
 ('derivs.db','derivs',('symbol','series','ts','value'),('symbol','series','ts')),
 ('candles.db','refs',('key','ts','open','high','low','close','volume'),('key','ts'))):
 source=sqlite3.connect((Path(a.copies)/filename).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
 ddl=source.execute('SELECT sql FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
 rows=source.execute('SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+','.join(keys)+' LIMIT 1000').fetchall()
 expected=hashlib.sha256(repr(rows).encode()).hexdigest()
 db=sqlite3.connect(scratch/(table+'.db'));db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0');db.execute(ddl)
 sqlite_tx.insert_rows(db,'INSERT OR REPLACE INTO '+table,rows,len(columns));db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
 change_start=db.total_changes;statements=[];db.set_trace_callback(statements.append);start=time.perf_counter()
 for _ in range(5):
  if hasattr(sqlite_tx,'save_projection'):sqlite_tx.save_projection(db,table,columns,keys,rows)
  else:sqlite_tx.insert_rows(db,'INSERT OR REPLACE INTO '+table,rows,len(columns))
  db.commit()
 seconds=time.perf_counter()-start
 actual=db.execute('SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+','.join(keys)).fetchall()
 times=[]
 for _ in range(20):
  start=time.perf_counter();db.execute('SELECT * FROM '+table+' ORDER BY '+','.join(keys)+' LIMIT 100').fetchall();times.append(time.perf_counter()-start)
 results[table]={'actual_sample_rows':len(rows),'repetitions':5,'seconds':seconds,'row_writes':db.total_changes-change_start,
  'wal_bytes':Path(str(scratch/(table+'.db'))+'-wal').stat().st_size,'transactions':sum(s.startswith('COMMIT') for s in statements),
  'statements':len(statements),'query_median_seconds':statistics.median(times),'exact_value_parity':hashlib.sha256(repr(actual).encode()).hexdigest()==expected}
 db.close();source.close()
source=sqlite3.connect((Path(a.copies)/'derivs.db').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
key=source.execute('SELECT series_key FROM market_revisions LIMIT 1').fetchone()[0]
records=tuple(json.loads(row[0]) for row in source.execute('SELECT record_json FROM market_revisions WHERE series_key=? ORDER BY rowid LIMIT 128',(key,)))
db=sqlite3.connect(scratch/'receipts.db');mp.init(db);mp.append(db,key,prepared=records);db.commit()
encoded=[];original=mp.encode
mp.encode=lambda obj:(encoded.append(1),original(obj))[1]
statements=[];db.set_trace_callback(statements.append);before=db.total_changes;start=time.perf_counter()
for _ in range(5):mp.append(db,key,prepared=records);db.commit()
results['exact_receipt_retry']={'rows':len(records),'seconds':time.perf_counter()-start,'record_serializations':len(encoded),
 'row_writes':db.total_changes-before,'statements':len(statements),'retained_revisions':db.execute('SELECT count(*) FROM market_revisions').fetchone()[0]}
mp.encode=original;db.close();source.close()
feed=DerivFeed(db_path=scratch/'collection.db',clock_ms=lambda:2000)
feed._SERIES_FETCHERS=[('oi','open_interest')];calls=[]
def fetch(symbol):
 calls.append(symbol);return pd.DataFrame({'ts':pd.to_datetime([1000],unit='ms',utc=True),'value':[2.]})
feed.open_interest=fetch
feed.record_all(['BTC/USDT','BTC/USDT'],delay=0);first=len(calls)
feed.record_all(['BTC/USDT'],delay=0)
results['collection']={'first_pass_retrievals':first,'second_pass_retrievals':len(calls)-first,
 'retained_acquisitions':feed.db.execute('SELECT count(*) FROM market_revisions').fetchone()[0]}
feed.db.close()
Path(a.output).write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
