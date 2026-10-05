"""Read-only source inventory; booking replay happens in disposable memory only.

No venue request, production write, historical reference repair or fuzzy match.
"""
import argparse
import json
import sqlite3
from pathlib import Path

from trader.engine import booking, trade_provenance as P
from trader.observability.execution_calibration import inventory
from trader.engine.paper_exit_evidence import canonical, digest


def inspect(journal_path, depth_path=None):
    source = sqlite3.connect(Path(journal_path).resolve().as_uri()+'?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    query=lambda sql, params=():[dict(r) for r in source.execute(sql,params)]
    source.execute('BEGIN')
    result=dict(source_path=str(Path(journal_path).resolve()), persisted=inventory(query))
    projected=sqlite3.connect(':memory:');projected.row_factory=sqlite3.Row
    projected.executescript(P.TABLES)
    projected.execute(source.execute("SELECT sql FROM sqlite_master WHERE name='trades'").fetchone()[0])
    for row in source.execute('SELECT * FROM trades'):
        projected.execute('INSERT INTO trades VALUES('+','.join('?' for _ in row)+')',tuple(row))
    receipts, rejected = [], []
    for row in query('SELECT * FROM trade_accounting_bookings ORDER BY id'):
        r=json.loads(row['payload'])
        try:
            booking.replay(r)
            if r['trade_id'] != row['trade_id']:
                raise ValueError('booking_trade_differs')
            P.record_booking(projected,row['trade_id'],r['kind'],r['before'],r['after'],r['evidence'],row['id'],
                source='backfill_receipt',recorded_ms=r['observed_ms'])
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append(dict(booking_id=row['id'],reason=str(exc)))
            continue
        receipts.append(dict(booking_id=row['id'],sha256=r['sha256'],payload_sha256=digest(row['payload']),
            symbol=r['after']['symbol'],direct_fill_rows=len(r['evidence'].get('fills') or []),
            requested_quantity_present=r['evidence'].get('requested_quantity') is not None,
            reference_present=r['evidence'].get('reference') is not None))
    pq=lambda sql, params=():[dict(r) for r in projected.execute(sql,params)]
    result['booking_receipt_replay']=dict(authority='EXISTING_RECEIPTS_REPLAYED_IN_MEMORY_ONLY',
        receipts=receipts,rejected=rejected,inventory=inventory(pq))
    result['journal_only_trades_used_as_fill_evidence']=False
    result['depth']=None
    if depth_path:
        with sqlite3.connect(Path(depth_path).resolve().as_uri()+'?mode=ro',uri=True) as depth:
            depth.row_factory=sqlite3.Row
            rows=[dict(r) for r in depth.execute('SELECT symbol,environment,count(*) snapshots,min(received_ms) first_ms,max(received_ms) last_ms FROM depth_observations GROUP BY symbol,environment')]
            result['depth']=dict(path=str(Path(depth_path).resolve()),by_instrument=rows,
                event_linked_snapshots=0,reason='No decision/intent/order IDs in depth schema; no time-window joins')
    result['slippage_model']='NOT_VALIDATED'
    result['commission']=dict(status='UNAVAILABLE',reason='AUTHORIZED_SOURCE_MISSING',
        collector_found='scripts/read_prod_fee.py; not invoked',
        missing='Applicable connected-account rate receipt, time scope and paper execution classification')
    result['first_live']=False
    result['production_economic_probation']='INCOMPLETE'
    result['inventory_sha256']=digest(canonical(result))
    source.close();projected.close()
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--journal',required=True);p.add_argument('--depth');p.add_argument('--out',required=True)
    a=p.parse_args()
    body=inspect(a.journal,a.depth)
    Path(a.out).write_text(json.dumps(body,indent=2)+'\n')


if __name__=='__main__':main()
