"""Register one explicitly declared forward close-to-close non-trade experiment.

No defaults for costs or notional. No trading, admission or retrospective scan.
Example: python -m scripts.register_nontrade_experiment --declaration file.json
The declaration requires schema_version=nontrade-experiment.v1, id, decision_id,
direction (+1/-1), cost_bps, cost_source, notional and currency=USDT.
Cost source describes an assumption; it does not certify actual execution costs.
"""
import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import time

from trader.cognition import outcomes as O
from trader.observability import investigation as C, outcomes as S


def register(directory, declaration, now=None):
    directory = Path(directory)
    live_clock = now is None
    now = int(time.time()*1000) if live_clock else now
    started_ms = now
    required = {'schema_version','id','decision_id','direction','cost_bps','cost_source','notional','currency'}
    if (set(declaration) != required or declaration['schema_version'] != 'nontrade-experiment.v1'
            or not all(isinstance(declaration[k],str) and declaration[k].strip() for k in ('id','decision_id','cost_source'))
            or declaration['currency'] != 'USDT' or len(O.L.encode(declaration).encode()) > 4096):
        raise ValueError('explicit_nontrade_declaration_required')
    with closing(C.ledger(directory/'investigation.db')) as db, db:
        # Exact repeated declaration is idempotent even after the target opens.
        for key, payload in db.execute('SELECT id,payload FROM counterfactual_registrations'):
            old = json.loads(payload).get('declaration')
            if old and old['id'] == declaration['id']:
                if old != declaration:
                    raise ValueError('nontrade_declaration_conflict')
                return {'status':'already_registered','id':key}
        health = json.loads((directory/'attention_health.json').read_text())
        if (health.get('status') != 'ok' or not health.get('worker_alive') or
                health.get('errors') or health.get('last_error') or
                not 0 <= now-health.get('updated_ms',0) <= C.FRESH_MS):
            raise ValueError('collector_unhealthy')
        source = C.source_snapshot(directory/'attention.db')
        if source is None or not 0 <= now-source[0]['as_of_ms'] <= C.FRESH_MS:
            raise ValueError('fresh_exact_scan_required')
        snapshot = C.adapt(source,now)
        with closing(S._read(directory/'luffy.db')) as journal:
            row = journal.execute('SELECT id,ts,symbol,action,executed,skip_reason,strategy_ids,scan_id FROM decisions WHERE id=?',
                                  (declaration['decision_id'],)).fetchone()
        if row is None:
            raise ValueError('nontrade_decision_missing')
        decision = dict(row)
        if (decision['executed'] or not 0 <= now-O.timestamp(decision['ts']) <= C.FRESH_MS
                or decision['scan_id'] != snapshot.scan['scan_id']):
            raise ValueError('fresh_nontrade_exact_scan_required')
        anchor = snapshot.scan['as_of_ms']//O.L.TF*O.L.TF-O.L.TF
        bars = [dict(asdict(b.candle),version_id=b.version_id) for b in snapshot.bars
                if b.candle.symbol == decision['symbol'] and b.candle.open_ms == anchor]
        if len(bars) != 1:
            raise ValueError('exact_nontrade_baseline_missing_retry')
        if live_clock:
            now = int(time.time()*1000)
            if not 0 <= now-started_ms < 20000:
                raise ValueError('nontrade_registration_deadline')
        r = dict(schema_version='close-counterfactual.v1',decision_kind='skip',registered_ms=now,
                 target_open_ms=(now//O.L.TF+1)*O.L.TF,baseline=bars[0],
                 direction=declaration['direction'],cost_bps=declaration['cost_bps'],notional=declaration['notional'],
                 declaration=declaration,declaration_version=O.digest(declaration),decision=decision,
                 scan_id=snapshot.scan['scan_id'],code_manifest=snapshot.scan['code_manifest'])
        if r['target_open_ms']-now < 30000:
            raise ValueError('nontrade_registration_boundary_guard')
        key = S.register_counterfactual(db,r,now)
        return {'status':'registered','id':key,'registered_ms':now,'target_open_ms':r['target_open_ms'],
                'measurement':'simulated_close_to_close_not_actual_pnl'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--declaration',type=Path,required=True)
    parser.add_argument('--directory',type=Path,default=Path('data'))
    args = parser.parse_args()
    if args.declaration.stat().st_size > 4096:
        parser.error('declaration_capacity')
    try:
        result = register(args.directory,json.loads(args.declaration.read_text()))
    except (ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'refused','reason':str(exc) if isinstance(exc,ValueError) else 'malformed_declaration'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
