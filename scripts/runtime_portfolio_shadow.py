#!/usr/bin/env python3
"""One bounded read-only runtime checkpoint; proposal ledger only, no requests."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trader.portfolio.current import freeze
from trader.portfolio.runtime import Consumer, replay
from trader.portfolio.allocator import canonical

PACKAGE='LUFFY-STAGE6-RUNTIME-PORTFOLIO-INTEGRATION-R1'


def inventory(journal,now):
    deadline=time.monotonic()+5
    with sqlite3.connect(Path(journal).resolve().as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        kv=dict(db.execute("SELECT key,value FROM state_kv WHERE key IN ('venue_position_snapshot','control_state',"
            "'account_margin_observation','risk_state','risk_assessment')"))
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts={t:db.execute('SELECT COUNT(*) FROM (SELECT 1 FROM '+t+' LIMIT 65)').fetchone()[0]
            if t in names else None for t in ('strategy_versions','strategy_capacity_receipts','strategy_probation_receipts')}
    snap=json.loads(kv.get('venue_position_snapshot','null'))
    return dict(as_of_ms=now,control_state=kv.get('control_state','UNKNOWN'),source_counts=counts,
        portfolio_snapshot_id=snap.get('snapshot_id') if snap else None,
        portfolio_as_of_ms=snap.get('observed_at_ms') if snap else None,
        holdings=snap.get('positions') if snap else None,
        account_evidence=json.loads(kv.get('account_margin_observation','null')),
        risk_assessment=json.loads(kv.get('risk_assessment','null')),
        current_opportunities='NO_EXACT_VERSIONS' if counts['strategy_versions']==0 else 'SEE_CURRENT_READER',
        economics='UNAVAILABLE' if counts['strategy_probation_receipts']==0 else 'SEE_EXACT_RECEIPTS',
        capacity='UNAVAILABLE' if counts['strategy_capacity_receipts']==0 else 'SEE_EXACT_RECEIPTS')


def run(journal,attention,investigation,config,output):
    output=Path(output).resolve()
    for source in (journal,attention,investigation):
        if output==Path(source).resolve().parent or Path(source).resolve().parent in output.parents:
            raise ValueError('SHADOW_LEDGER_MUST_BE_OUTSIDE_PRODUCTION_STORAGE')
    now=time.time_ns()//1_000_000
    observed=inventory(journal,now)
    base=dict(package=PACKAGE,read_only_sources=True,authenticated_requests=0,
              real_order_submissions=0,production_mutations=0,trading_behavior_changed='NO_NEW_EXECUTION_AUTHORITY',
              actual_inventory=observed)
    try:
        inputs,detail=freeze(journal,attention,investigation,config)
        result=Consumer(output/'runtime.db').consume(inputs)
        replay(result)
        proposal=result['proposal']
        answer=dict(base,status='PASS',replay='PASS',runtime=result,source_detail=detail,
            real_shadow_result=proposal['result']['decision'] if proposal else 'CASH / NO_ALLOCATION / NO_EXACT_TRIGGER',
            trade_intents=len(result['trade_intents']),risk_approvals=sum(
                json.loads(r['payload_json'])['result']=='APPROVE' for r in result['risk_decisions']))
    except (ValueError,TypeError,KeyError,sqlite3.Error) as exc:
        answer=dict(base,status='BLOCKED',blocker=str(exc),
                    real_shadow_result='CASH / NO_ALLOCATION; CURRENT_CUT_UNAVAILABLE',
                    trade_intents=0,risk_approvals=0)
    output.mkdir(parents=True,exist_ok=True)
    target=output/('shadow-'+str(now)+'.json')
    with target.open('x') as f:f.write(canonical(answer)+'\n')
    return answer


def main():
    import yaml
    p=argparse.ArgumentParser()
    for name in ('journal','attention','investigation','config','output'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();full=yaml.safe_load(args.config.read_text())
    safe={k:full.get(k,{}) for k in ('risk','attention','strategies','portfolio')}
    answer=run(args.journal,args.attention,args.investigation,safe,args.output)
    print(canonical({k:v for k,v in answer.items() if k not in ('actual_inventory','runtime','source_detail')}))
    return 0 if answer['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
