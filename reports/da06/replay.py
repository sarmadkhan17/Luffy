"""Offline matched replay. All inputs are retained; networking is denied."""
import argparse, cProfile, gzip, hashlib, io, json, pstats, socket, statistics, time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from trader.agents.structure import StructureAnalyst
from trader.agents.momentum import MomentumAnalyst, ValueAnalyst, RotationAnalyst
from trader.agents.flow import FlowAnalyst
from trader.agents.positioning import PositioningAnalyst
from trader.agents.orderbook_depth import DepthScout
from trader.core.types import Snapshot
from trader.core.journal import Journal
from trader.engine.orchestrator import Orchestrator
from trader.learning.capture_runtime import restore_frame
from trader.learning.foundation import digest
from trader.cognition.attention import CognitionConfig, evaluate
from trader.cognition.contracts import INPUT_SCHEMA, load_input
from trader.observability.store import restore_market_receipts
from trader import attention_admission as A

ROOT=Path(__file__).parent
CLASSES=(StructureAnalyst,MomentumAnalyst,FlowAnalyst,ValueAnalyst,RotationAnalyst,PositioningAnalyst,DepthScout)
def denied(*a,**kw):raise AssertionError('DA06 network forbidden')
socket.socket.connect=denied
socket.create_connection=denied

def load():
    fixture=json.loads(gzip.decompress((ROOT/'retained.json.gz').read_bytes()))
    assert all(digest(raw)==sha for sha,raw in fixture['blobs'].items())
    return fixture

def restore(fixture):
    out=[]
    for entry in fixture['entries']:
        raw=fixture['blobs'][entry['data_sha']]
        dfs={k:restore_frame(fixture['blobs'][sha]) for k,sha in raw['chunks']['dfs'].items() if sha}
        snap=Snapshot(raw['symbol'],raw['original_snapshot_ts'],raw['price'],dfs,
            market_type=raw['market_type'],btc_ctx=raw['btc_context'])
        agents=[cls() for cls in CLASSES]
        aux=raw['additional_original_inputs']
        agents[-2].set_context(snap.symbol,aux.get('positioning_funding'),aux.get('positioning_open_interest'))
        agents[-1].set_context(snap.symbol,aux.get('order_book'))
        cfg=fixture['blobs'][entry['config_sha']]['config']
        out.append((snap,agents,cfg))
    return out

def clean(value):
    # Only wall-clock timestamps and generated correlation IDs vary by process.
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in ('ts','id','cycle_id')}
    if isinstance(value,(tuple,list)):return [clean(v) for v in value]
    return value

def run(label,repeat):
    fixture=load();items=restore(fixture)
    scan=fixture['attention']
    candles=[dict(b) for entries in fixture['attention_bars'].values() for b in entries]
    restore_market_receipts(candles,scan)
    raw=dict(schema=INPUT_SCHEMA,timeframe=scan['timeframe'],decision_times=[scan['as_of_ms']],
             candles=candles,membership=scan['membership'],correlation_history=scan['correlation_input'])
    a=evaluate(load_input(raw),scan['as_of_ms'],CognitionConfig(**scan['config']),scan['scan_id'],{},
               frozen_priorities=scan.get('governed_attention_state'),frozen_claim_states=scan.get('governed_world_claim_state'))
    selected=[r['symbol'] for r in a['universe'] if r.get('selected')]
    assert selected==[r['symbol'] for r in fixture['attention']['rows'] if r.get('selected')]
    row_diffs=[dict(symbol=old['symbol'],fields=[k for k in set(old)|set(new) if old.get(k)!=new.get(k)])
               for old,new in zip(scan['rows'],a['universe']) if old!=new]
    prior=json.loads(Path('docs/tracker/evidence/attention-admission-r1/replay-fixture.json').read_text())
    receipt=A.admit(prior['observation'],A.AdmissionPolicy(**prior['policy']),prior['prior_state'])
    assert A.canonical(receipt)==A.canonical(prior)
    def analysts():return [[clean(v.evaluate(s).as_dict()) for v in agents] for s,agents,cfg in items]
    expected=analysts()
    import pandas as pd
    csv_calls=0;csv_bytes=0;csv_ms=0
    original=pd.DataFrame.to_csv
    def counted(self,*args,**kw):
        nonlocal csv_calls,csv_bytes,csv_ms
        start=time.perf_counter();value=original(self,*args,**kw);csv_ms+=(time.perf_counter()-start)*1000
        csv_calls+=1;csv_bytes+=len(value.encode());return value
    with patch.object(pd.DataFrame,'to_csv',counted):assert analysts()==expected
    durations=[]
    per_symbol={s.symbol:[] for s,agents,cfg in items}
    for _ in range(repeat):
        start=time.perf_counter();assert analysts()==expected;durations.append((time.perf_counter()-start)*1000)
    for _ in range(repeat):
        for snap,agents,cfg in items:
            start=time.perf_counter()
            for analyst in agents: analyst.evaluate(snap)
            per_symbol[snap.symbol].append((time.perf_counter()-start)*1000)
    pr=cProfile.Profile();pr.enable();analysts();pr.disable()
    stream=io.StringIO();pstats.Stats(pr,stream=stream).sort_stats('cumulative').print_stats(25)
    (ROOT/(label+'-profile.txt')).write_text(stream.getvalue())
    # Use a local empty governance journal for both versions. Historical strategy
    # evaluators/weights are not fully retained: this is matched current-code replay.
    with __import__('tempfile').TemporaryDirectory(dir='.da06-tmp') as tmp:
        journal=Journal(Path(tmp)/'j.db')
        decisions=[]
        for snap,agents,cfg in items:
            engine=Orchestrator(agents,journal,cfg=cfg)
            d=engine.decide(snap,[],entry_allowed=False,blocked_reason='offline replay')
            decisions.append(dict(decision=clean(asdict(d)),causes=d.evaluation_causes,omitted=d.omitted_causes,
                gated_lean=str(d.gated_lean)))
            engine.journalize(snap,d,snap.market_type,'paper')
        persisted={table:[clean(dict(r)) for r in journal.query('select * from '+table+' order by rowid')]
                   for table in ('cycles','votes','decisions','outcomes')}
        # Shadow sampling is stochastic; keep it out of the journal parity claim.
        persisted.pop('outcomes')
        journal._conn().close()
    no_eligible=[];off_symbol=[];summary=[]
    refusals={'ineligible_state','ineligible_market','ineligible_regime','ineligible_symbol'}
    for entry in fixture['entries']:
        cause=entry['cause'];evaluations=cause['evaluations'];strats=[v for v in evaluations if v['component']=='strategy']
        none=bool(strats) and all(v['reason'] in refusals for v in strats)
        if none:no_eligible.append(cause['symbol'])
        if any(v['reason']=='ineligible_symbol' for v in strats):off_symbol.append(cause['symbol'])
        summary.append(dict(symbol=cause['symbol'],no_eligible_strategy=none,
            analyst_votes=sum(v['reason']=='emitted_vote' for v in evaluations if v['component']=='analyst'),
            strategy_evaluator_calls=sum(v['reason'] not in refusals for v in strats),
            action=cause['action'],entry_allowed=cause['entry_allowed']))
    evidence=dict(analyst_votes=expected,decisions=decisions,persisted=persisted,
                  attention_rows=a['universe'],attention_market=a['market'],
                  admission_receipt=receipt)
    (ROOT/(label+'-outputs.json')).write_text(json.dumps(evidence,sort_keys=True,indent=2))
    result=dict(label=label,repeat=repeat,analyst_batch_ms=durations,median_ms=statistics.median(durations),
        per_symbol_median_ms={s:statistics.median(v) for s,v in per_symbol.items()},
        csv_calls=csv_calls,csv_bytes=csv_bytes,csv_ms=csv_ms,analyst_evaluations=len(items)*7,
        historical_scan=fixture['attention']['scan_id'],historical_selected=selected,
        historical_no_eligible_strategy=no_eligible,historical_symbol_refusals=off_symbol,historical_summary=summary,
        historical_attention_row_differences=row_diffs,
        historical_market_equal=a['market']==scan['market'],exact_synthetic_12_slot_receipt=True,
        limits=['No historical per-stage wall timings retained; timestamp gaps are not CPU timings.',
                'Historical original decisions are evidence only. Current baseline/new replay uses empty population and empty local governance; strategy behavior is covered by matched regression suites.',
                'Historical scan uses the legacy 16-member diagnostic subset, not the later 12-slot policy. No historical 12-slot receipt retained.'])
    (ROOT/(label+'-measurements.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('historical_summary','analyst_batch_ms','limits')},indent=2))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('label');parser.add_argument('--repeat',type=int,default=7)
    args=parser.parse_args();run(args.label,args.repeat)
