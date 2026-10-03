"""Focused synthetic source integration; no production or venue mutations."""
from dataclasses import replace
from copy import deepcopy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
from trader.learning import producers as P, capture as C, capture_runtime as R, foundation as L
from tests.test_decision_sources import producer, chain, exact  # noqa: F401
from tests.test_historical_outcome_capture import prospective, CUT  # noqa: F401


def test_registered_cash_automatic_forward_and_chain(producer):
    j,d,_,_,_,_,ai,_,_=producer
    cut=ai.as_of_ms
    import pandas as pd
    declaration=dict(decision_id=d.id,cycle_id=d.cycle_id,symbol=d.symbol,ts=d.ts,action='BUY',entry_price=100)
    with j._tx() as db:
        C.record_action(db,'decision:'+d.id,dict(prediction=declaration),cut)
        targets={'1h':dict(ts=pd.Timestamp(cut+3600000,unit='ms',tz='UTC').isoformat(),close=110)}
        oid=R.forward(db,declaration,{'fwd_ret_1h':.1},targets,cut+3900000)
        assert R.forward(db,declaration,{'fwd_ret_1h':.1},targets,cut+4000000)==oid
    raw=P.materialize(j._conn(),oid)
    assert raw['authoritative']
    assert raw['outcome']['boundary']=='COUNTERFACTUAL'
    assert raw['proposal']['status']=='INSUFFICIENT_EVIDENCE'
    assert not raw['attribution']['causal_claims']
    code='import sqlite3,json,sys;from trader.learning.producers import materialize;db=sqlite3.connect(sys.argv[1]);print(json.dumps(materialize(db,sys.argv[2]),sort_keys=True))'
    replay=json.loads(subprocess.check_output([sys.executable,'-c',code,str(j.db_path),oid],text=True))
    assert replay==json.loads(L.canonical(raw))


def test_cash_without_registered_opportunity_has_nothing_to_measure(prospective):
    j,d,_=prospective
    declaration=dict(decision_id=d.id,cycle_id=d.cycle_id,symbol=d.symbol,ts=d.ts,action='BUY',entry_price=100)
    before=j.query('SELECT * FROM learning_outcome_captures')
    with j._tx() as db,pytest.raises(ValueError,match='exact_registered_opportunity_required'):
        R.forward(db,declaration,{'fwd_ret_1h':.1},{},CUT+3600000)
    assert j.query('SELECT * FROM learning_outcome_captures')==before


def test_missed_registry_resolves_automatically_without_missed_profit(producer):
    j=producer[0]
    rows=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'missed:%'")
    assert len(rows)==1
    raw=P.materialize(j._conn(),rows[0]['outcome_id'])
    assert raw['outcome']['kind']=='MISSED_OPPORTUNITY'
    obs=json.loads(raw['outcome']['observation_json'])
    assert obs['status']=='SKIPPED' and 'pnl' not in L.canonical(obs)
    assert raw['proposal']['proposed_json'] is None
    with j._tx() as db,pytest.raises(ValueError):
        P.missed(db,'decision:unknown',{})


def test_data_failure_never_market_conclusion(prospective):
    j,_,_=prospective
    j.log_brain_event('data_quality_incident','stale feed',{'required_input':'UNAVAILABLE'})
    row=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'incident:%'")[0]
    raw=P.materialize(j._conn(),row['outcome_id'])
    assert raw['outcome']['kind']=='DATA_QUALITY_INCIDENT'
    assert json.loads(raw['outcome']['observation_json'])['market_conclusion']=='NOT_APPLICABLE'
    assert raw['evidence']['quality']=='NON_AUTHORITATIVE'
    assert raw['proposal']['status']=='INCOMPLETE_REPLAY'


def test_execution_quality_is_separate_and_replay_bound(prospective):
    from trader.engine.booking import assess
    from trader.engine.accounting import digest
    j,d,_=prospective
    trade=dict(id='t',decision_id=d.id,status='closed')
    receipt=dict(schema_version='trade-booking.v1',trade_id='t',kind='close:test',observed_ms=CUT+100,
        before=None,after=trade,evidence=dict(basis='venue_order_fills',order_id='123',quantity=1,slippage=5,
            symbol='BTCUSDT',side='sell',since_ms=CUT,observed_ms=CUT+100,
            fills=[dict(id='fill',order='123',symbol='BTCUSDT',side='sell',timestamp=CUT+100,
                amount=1,price=101,commission=.1,commission_asset='USDT',realized_pnl=-1)]))
    receipt['assessment']=assess(receipt['evidence']);receipt['sha256']=digest(receipt)
    before=j.query('SELECT * FROM state_kv')
    with j._tx() as db:
        oid=P.execution_quality(db,trade,receipt)
        assert P.execution_quality(db,trade,receipt)==oid
    raw=P.materialize(j._conn(),oid)
    assert raw['outcome']['kind']=='EXECUTION_QUALITY'
    assert raw['authoritative']
    dims={d[0]:d[1] for d in raw['attribution']['dimensions']}
    assert dims['strategy_version']==L.Support.NA and dims['hypothesis_research']==L.Support.NA
    assert raw['proposal']['status']=='INSUFFICIENT_EVIDENCE'
    assert j.query('SELECT * FROM state_kv')==before
    o,s,r,e=chain(j,oid)
    forged=replace(e,quality='VERIFIED_REPLAY',historical_json='{}',eligible_targets=(L.Target.LIFECYCLE,))
    assert L.propose(forged,L.Target.LIFECYCLE,{},rule=L.DECAY_RULE).status==L.Status.INCOMPLETE


def test_real_shadow_is_read_only_with_no_backfill(prospective):
    from scripts.verified_outcome_shadow import run
    j,_,_=prospective
    before=list(j._conn().iterdump())
    report=run(j.db_path)
    assert report['production_mutations']==report['counts']['applicable_updates']==0
    assert list(j._conn().iterdump())==before


def test_trade_binding_refuses_absence_and_wrong_exact_version():
    reg=dict(lineage=dict(decision_id='d',cycle_id='c',version_id='v',spec_hash='h',strategy_id='s'))
    trade=dict(decision_id='d',strategy_id='s',exec_mode='live',status='closed')
    identity=dict(status='VERIFIED',version_id='v',spec_sha256='h',strategy_id='s',decision=dict(decision_id='d',cycle_id='c'))
    P.verify_trade_binding(reg,identity,trade)
    for mutation in (None,dict(identity,version_id='other'),dict(identity,status='UNKNOWN'),dict(identity,decision={'decision_id':'other'})):
        with pytest.raises(ValueError):P.verify_trade_binding(reg,mutation,trade)


def test_counterfactual_never_realized(prospective):
    j,d,_=prospective
    with j._tx() as db,pytest.raises(ValueError,match='nontrade_is_unrealized'):
        C.attach(db,'decision:'+d.id,'false-money','REJECTED_TRADE','REALIZED',{'net_pnl':10},CUT+100)


def test_worker_delivers_exact_trade_and_recovers_delivery_after_restart(producer,tmp_path,monkeypatch):
    from tests.test_whole_trade_accounting import source,Venue,seal
    from trader.engine import trade_accounting as A
    from trader.observability import accounting as W
    from datetime import datetime,timezone
    j,d,_,cfg,_,_,ai,_,v=producer
    cut=ai.as_of_ms
    src=source();venue=Venue()
    for t in [src['trade']]+[t for r in src['receipts'] for t in (r['before'],r['after']) if t]:
        t['decision_id']=d.id;t['strategy_id']=v['strategy_id']
        for key in ('opened_at','closed_at'):
            if t[key]:
                # Each snapshot is a separate dict, so clocks shift exactly once.
                from trader.cognition.outcomes import timestamp
                t[key]=datetime.fromtimestamp((timestamp(t[key])+cut)/1000,timezone.utc).isoformat()
    for r in src['receipts']:r['observed_ms']+=cut;seal(r)
    src['observed_ms']+=cut;seal(src)
    for rows,key in ((venue.fills,'time'),(venue.funding,'time'),(venue.events,'fundingTime')):
        for row in rows:row[key]+=cut
    identity=dict(status='VERIFIED',strategy_id=v['strategy_id'],version_id=v['version_id'],spec_sha256=v['spec_hash'],
                  decision=dict(decision_id=d.id,cycle_id=d.cycle_id))
    with j._tx() as db:
        t=src['trade'];columns=list(t)+['entry_identity_json']
        db.execute('INSERT INTO trades('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(t.values())+[json.dumps(identity)])
        for receipt in src['receipts']:
            db.execute('INSERT INTO trade_accounting_bookings(trade_id,payload) VALUES (?,?)',('trade',json.dumps(receipt)))
        C.record_action(db,'decision:'+d.id,dict(action='EXECUTED'),cut+1000)
        C.record_action(db,'decision:'+d.id,dict(ok=True,config_risk_sha256=L.digest(cfg['risk'])),cut+1,risk=True)
    before={table:j.query('SELECT * FROM '+table) for table in ('trades','decisions','state_kv','control_events','strategy_versions')}
    monkeypatch.setattr(A.time,'time',lambda:(cut+5000)/1000)
    directory=tmp_path/'worker'
    # Simulate crash after journal commit but before queue delivery marker.
    original=W.deliver_completed
    monkeypatch.setattr(W,'deliver_completed',lambda *args:dict(delivered=0,retry=1))
    first=W.step(j.db_path,directory,lambda:venue,now_ms=cut+5000)
    assert first['jobs']=={'complete':1}
    monkeypatch.setattr(W,'deliver_completed',original)
    report=W.step(j.db_path,directory,lambda:pytest.fail('complete receipt fetched after restart'),now_ms=cut+6000)
    assert report['learning_delivery']['delivered']==1
    rows=j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-trade:%'")
    assert len(rows)==1
    oid=rows[0]['outcome_id'];raw=P.materialize(j._conn(),oid)
    assert raw['authoritative'],[(x['role'],x['reason']) for x in C.manifest(j._conn(),oid)['dependencies'] if x['required'] and x['status']!='AVAILABLE']
    obs=json.loads(raw['outcome']['observation_json'])
    assert (obs['gross_pnl'],obs['fees'],obs['funding'],obs['net_pnl'])==(30,3,-2,25)
    assert raw['outcome']['boundary']=='REALIZED' and raw['proposal']['proposed_json'] is None
    assert before=={table:j.query('SELECT * FROM '+table) for table in before}
    assert W.step(j.db_path,directory,lambda:pytest.fail('duplicate fetched'),now_ms=cut+7000)['learning_delivery']['delivered']==0
    assert j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-trade:%'")==rows
    # Delivery itself is idempotent, even across a fresh process / new import clock.
    artifact=json.loads(next(directory.glob('*.json')).read_text())
    assert P.deliver_accounting(j.db_path,artifact)==oid
    assert j.query("SELECT outcome_id FROM learning_outcome_captures WHERE outcome_key LIKE 'verified-trade:%'")==rows


def test_rejection_is_frozen_and_no_action_refusal_is_not_a_trade_block(producer):
    import pandas as pd
    from trader.core.types import Action
    j,d,snap,cfg,receipt,proposal,ai,_,_=producer
    d=replace(d,id='original-rejection',action=Action.BUY,skip_reason='first-live refused')
    R.journalize_allocation(j,snap,d,cfg,receipt,proposal,ai)
    cut=ai.as_of_ms
    declaration=dict(decision_id=d.id,cycle_id=d.cycle_id,symbol=d.symbol,ts=d.ts,action='BUY',entry_price=100)
    with j._tx() as db:
        C.record_action(db,'decision:'+d.id,dict(prediction=declaration),cut)
        C.record_action(db,'decision:'+d.id,dict(ok=False,config_risk_sha256=L.digest(cfg['risk'])),cut+1,risk=True)
    import trader.core.journal as JM
    from datetime import datetime,timezone
    # Update time is part of the exact action receipt, so keep it before measurement.
    original_clock=JM.now_utc
    JM.now_utc=lambda:datetime.fromtimestamp((cut+2)/1000,timezone.utc)
    try:j.update_decision_outcome(d.id,False,skip_reason='later mutable text')
    finally:JM.now_utc=original_clock
    with j._tx() as db:
        target={'1h':dict(ts=pd.Timestamp(cut+3600000,unit='ms',tz='UTC').isoformat(),close=110)}
        oid=R.forward(db,declaration,{'fwd_ret_1h':.1},target,cut+3900000)
    raw=P.materialize(j._conn(),oid)
    # This fixture's actual TradeIntent is NO_ACTION, despite the root BUY
    # row. A later refusal must not fabricate a requested OPEN. The genuine
    # normal OPEN -> Risk REFUSE case is covered by the R2 end-to-end suite.
    assert raw['outcome']['kind']=='REJECTED_TRADE'
    assert raw['outcome']['boundary']=='COUNTERFACTUAL'
    obs=json.loads(raw['outcome']['observation_json'])
    assert obs['original_reasons']['skip_reason']=='first-live refused'
    assert 'net_pnl' not in obs and raw['proposal']['proposed_json'] is None


def test_outcome_sources_cannot_gain_authority_by_rehashing(prospective):
    from trader.engine.booking import assess
    from trader.engine.accounting import digest
    j,d,_=prospective
    trade=dict(id='t',decision_id=d.id,status='closed')
    receipt=dict(schema_version='trade-booking.v1',trade_id='t',kind='close:test',observed_ms=CUT+100,
        before=None,after=trade,evidence={'order_id':'123','quantity':1})
    receipt['assessment']=assess(receipt['evidence']);receipt['sha256']=digest(receipt)
    with j._tx() as db:oid=P.execution_quality(db,trade,receipt)
    o,s,r,e=chain(j,oid)
    m=next(iter(s.values()));m['sources']['execution']['trade_id']='other'
    ref=replace(o.sources[0],sha256=L.digest(m));o=replace(o,sources=(ref,))
    assert L.replay(o,{(ref.source_id,ref.version):m}).status=='INCOMPLETE'


def test_producers_have_no_learning_apply_or_trading_control_api():
    import ast
    banned={'create_order','cancel_order','upsert_spec','govern_version','retire_version',
            'apply_to_isolated_journal','set_control','check_entry'}
    for path in ('trader/learning/producers.py','trader/learning/capture_runtime.py','trader/observability/accounting.py'):
        tree=ast.parse(Path(path).read_text())
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in banned for n in ast.walk(tree))
