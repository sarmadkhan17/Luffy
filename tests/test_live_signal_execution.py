"""Synthetic venue and version labels; no Factory admission/venue claim."""
import json
from dataclasses import replace
from unittest.mock import Mock
import pandas as pd
import pytest
from tests.test_entry_recovery import setup as recovery_setup
from tests.test_versioned_owned_close import opened
from tests.test_sol1_paper_cost import bars,snapshot,H4,iso
from tests.test_sol1_strategy_lifecycle import _spec
from trader.strategy import exit_policy as E,compile as compiler
from trader.strategy.spec import ExitSpec
from trader.engine import live_signal_execution as L,live_signal_evidence as S,versioned_close as V
from trader.data import market_provenance as P

setup=recovery_setup


def prepared(setup,monkeypatch,*,fraction=1.):
    start=1800000000000//H4*H4
    exit_=ExitSpec(stop={'kind':'pct','v':.04},target={'kind':'none'},time={'max_bars':10},signal_exit='close < 109')
    spec=_spec(direction='long',entry_long='close > 0',entry_short='',exit=exit_)
    ex,j,e,tid=opened(setup,monkeypatch,fraction=fraction,exit_spec=exit_,step_ms=H4,last_bar_ms=start+59*H4)
    read=ex.fetch_positions
    monkeypatch.setattr(ex,'fetch_positions',lambda:[dict(row,symbol=row['symbol']+':USDT',info={'symbol':row['symbol'].replace('/','')}) for row in read()])
    trade=j.open_trades()[0];root=json.loads(trade['entry_identity_json']);p,s=E.decode(root['exit_state'])
    first=snapshot(bars(60,start,[(110.,110.2,108.5,108.8)]));at=int(pd.Timestamp(first.ts).timestamp()*1000)
    contexts=[];compiler.compile_spec(spec).exit_signal_detail(first.dfs,context_out=contexts,symbol=trade['symbol'])
    observation=E.Observation(start+60*H4,110.,110.2,108.5,108.8,signal_exit=True)
    with j._tx() as db:
        S.ensure(db);S.retain(db,trade,root,spec,first,p,s,observation,context=contexts[0])
    continuing,_=S.advance(p,s,observation,float(trade['amount']))
    raw=pd.DataFrame([dict(ts=pd.Timestamp(at,unit='ms',tz='UTC'),open=108.8,high=109.,low=108.,close=108.9,volume=10.)])
    partial=P.annotate(raw,instrument_id=root['execution_binding']['instrument_id'],source='https://offline.example/klines',
        kind='candle',received_ms=at+1000,timeframe=spec.timeframe)
    frame=pd.concat([first.df(spec.timeframe),partial],ignore_index=True)
    snap=replace(first,ts=iso(at+1000),dfs={spec.timeframe:frame})
    monkeypatch.setattr(L.time,'time',lambda:(at+1000)/1000)
    quote=Mock(return_value=dict(symbol=trade['symbol'],contract='linear',instrument_id=root['execution_binding']['instrument_id'],
        source='TEST_ONLY_public_book',started_ms=at+1000,received_ms=at+1000,bid=108.7,ask=108.9))
    return ex,j,e,trade,spec,snap,p,continuing.state,quote


def test_verified_signal_quotes_then_actual_close_and_accounting(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    result=L.execute(e,t,spec,snap,p,state,quote_reader=quote)
    assert result['status']=='CLOSED' and not j.open_trades()
    proof=json.loads(j.query('SELECT payload FROM '+L.TABLE)[0]['payload'])
    assert proof['fill_claim'] is False and proof['financial_result'] is None
    assert proof['benchmark']['price']==108.8
    booking=json.loads(j.query('SELECT payload FROM trade_accounting_bookings ORDER BY id DESC LIMIT 1')[0]['payload'])
    assert booking['evidence']['strategy_exit']['decision_reference']['price']==108.7
    assert booking['evidence']['order_id'] and quote.call_count==1


@pytest.mark.parametrize('case',['wrong_instrument','old_quote','crossed_book','stale'])
def test_unknown_or_unexecutable_signal_cannot_submit(setup,monkeypatch,case):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch);count=len(ex.sent)
    if case=='wrong_instrument':quote.return_value['instrument_id']='another'
    if case=='old_quote':quote.return_value['started_ms']-=1
    if case=='crossed_book':quote.return_value['bid']=110.
    if case=='stale':monkeypatch.setattr(L.time,'time',lambda:(int(pd.Timestamp(snap.ts).timestamp()*1000)+3*H4)/1000)
    with pytest.raises(ValueError):L.execute(e,t,spec,snap,p,state,quote_reader=quote)
    assert len(ex.sent)==count and j.open_trades()


def test_eligibility_and_plan_commit_before_submission_recover_without_new_candles(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    request=V.request_close
    monkeypatch.setattr(V,'request_close',Mock(side_effect=RuntimeError('synthetic crash before submission')))
    count=len(ex.sent)
    with pytest.raises(RuntimeError):L.execute(e,t,spec,snap,p,state,quote_reader=quote)
    assert len(ex.sent)==count and j.query('SELECT * FROM '+L.TABLE) and j.query('SELECT * FROM '+V.TABLE)
    monkeypatch.setattr(V,'request_close',request)
    assert V.resume(e)[0]['status']=='CLOSED'


def test_damaged_consumed_receipts_cannot_be_replaced_by_signal_truth(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    with j._tx() as db:body=S.load(db,t['id'],json.loads(t['entry_identity_json']))
    from trader.engine import exit_signal_context as K
    bundle=body['context'];groups=K.unpack(bundle['groups'])
    # Alter price slightly while preserving the truth of close < 109. The
    # old revision hash no longer proves the consumed input.
    groups['frames'][spec.timeframe].loc[60,'close']=108.7
    bundle['groups']=K.pack(groups)
    with pytest.raises(ValueError,match='retained_sources_unavailable'):
        K.verify_sources(bundle,body['signal_decision']['bar_ms'])
    assert quote.call_count==0


def test_unused_unqualified_context_does_not_block_an_intact_signal(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    with j._tx() as db:body=S.load(db,t['id'],json.loads(t['entry_identity_json']))
    from trader.engine import exit_signal_context as K
    bundle=body['context'];groups=K.unpack(bundle['groups'])
    groups['market']={'btcdom':pd.DataFrame({'ts':[pd.Timestamp(0,unit='ms',tz='UTC')],'close':[1.]})}
    bundle['groups']=K.pack(groups)
    assert K.verify_sources(bundle,body['signal_decision']['bar_ms'])['frames']


def test_stale_or_missing_original_sources_preserve_protection_no_order(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    count=len(ex.sent)
    monkeypatch.setattr(L.K,'verify_sources',Mock(side_effect=ValueError('signal_retained_sources_unavailable')))
    with pytest.raises(ValueError,match='retained_sources_unavailable'):
        L.execute(e,t,spec,snap,p,state,quote_reader=quote)
    assert len(ex.sent)==count and quote.call_count==0 and j.open_trades()


def test_live_adapter_routes_the_verified_signal_to_real_owned_close(setup,monkeypatch):
    from trader.engine.versioned_exits import LiveExitAdapter
    from trader.strategy import factory_handoff as F
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    root=json.loads(t['entry_identity_json'])
    # TEST-ONLY version/installation responses; real retained sources, adapter,
    # original entry receipt, quote ordering and Executor booking follow them.
    version=dict(version_id=root['version_id'],strategy_id=t['strategy_id'],spec_hash=root['spec_sha256'],
                 spec=spec.to_dict(),lineage={})
    monkeypatch.setattr(F,'load_version',lambda *a:version)
    monkeypatch.setattr(F,'_expected_exit_semantics',lambda v:E.EXIT_SEMANTICS_ID)
    monkeypatch.setattr(F,'verify_exit_binding',lambda *a:None)
    monkeypatch.setattr(F,'verify_install',lambda *a,**k:dict(install_id=root['install_id']))
    monkeypatch.setattr('trader.engine.coordinated_trials.quote',quote)
    assert LiveExitAdapter(j,e).manage(t,snap)=='signal'
    assert not j.open_trades() and quote.call_count==1


def test_signal_accepted_response_loss_recovers_without_another_quote_or_order(setup,monkeypatch):
    from ccxt import RequestTimeout
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    ex.close_error=RequestTimeout('synthetic accepted response lost')
    assert L.execute(e,t,spec,snap,p,state,quote_reader=quote)['status']=='CLOSING'
    count=len(ex.sent);ex.close_error=None
    assert V.resume(e)[0]['status']=='CLOSED'
    assert len(ex.sent)==count and quote.call_count==1


def test_required_funding_receipt_is_checked_even_when_tampering_keeps_signal_true(setup,monkeypatch):
    from trader.engine import exit_signal_context as K
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    with j._tx() as db:body=S.load(db,t['id'],json.loads(t['entry_identity_json']))
    groups=K.unpack(body['context']['groups']);base=groups['frames'][spec.timeframe]
    cut=body['context']['as_of_ms'];bar=body['signal_decision']['bar_ms']
    funding=P.annotate(pd.DataFrame({'ts':[pd.Timestamp(bar,unit='ms',tz='UTC')],'value':[.01]}),
        instrument_id=json.loads(t['entry_identity_json'])['execution_binding']['instrument_id'],
        source='TEST_ONLY_funding_source',kind='derivative',received_ms=cut)
    signal_spec=replace(spec,exit=replace(spec.exit,signal_exit='funding > 0'))
    contexts=[]
    values,valid=compiler.compile_spec(signal_spec).exit_signal_detail({spec.timeframe:base},
        derivs={'funding':funding},symbol=t['symbol'],context_out=contexts)
    assert values[-1] and valid[-1]
    bundle=K.capture(contexts[0],signal_spec)
    assert K.verify_sources(bundle,bar)['frames']
    changed=K.unpack(bundle['groups']);changed['derivs']['funding']['value']=.02
    bundle['groups']=K.pack(changed)
    assert K.replay(bundle,bar)=={'signal':True,'available':True}
    with pytest.raises(ValueError,match='retained_sources_unavailable'):K.verify_sources(bundle,bar)


def test_live_signal_does_not_wait_for_a_future_candle_to_be_received(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    snap=replace(snap,dfs={spec.timeframe:snap.df(spec.timeframe).iloc[:-1]})
    assert L.execute(e,t,spec,snap,p,state,quote_reader=quote)['status']=='CLOSED'
    proof=json.loads(j.query('SELECT payload FROM '+L.TABLE)[0]['payload'])
    assert proof['benchmark'] is None and proof['execution_policy']==L.LIVE_POLICY_ID
    assert proof['fill_claim'] is False and quote.call_count==1


def test_received_quote_stop_precedes_signal_exit(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch)
    quote.return_value.update(bid=95.,ask=95.1)
    assert L.execute(e,t,spec,snap,p,state,quote_reader=quote)['status']=='CLOSED'
    plan=V.load(j.query('SELECT * FROM '+V.TABLE)[0])
    assert plan['reason']=='stop' and plan['decision']['state']['state']['gap']
    assert not j.open_trades()


def test_partial_signal_close_resumes_residual_with_original_source_and_one_quote(setup,monkeypatch):
    ex,j,e,t,spec,snap,p,state,quote=prepared(setup,monkeypatch,fraction=.5)
    assert L.execute(e,t,spec,snap,p,state,quote_reader=quote)['status']=='CLOSING'
    first=j.query('SELECT * FROM '+L.TABLE)[0]
    assert j.open_trades()[0]['amount']==1.
    # An already committed close is recovered even if no new signal inputs
    # arrive. It retains the first reference rather than quoting a new intent.
    assert L.execute(e,t,spec,None,p,state,quote_reader=quote)['status']=='CLOSING'
    assert j.open_trades()[0]['amount']==.5 and quote.call_count==1
    assert j.query('SELECT * FROM '+L.TABLE)[0]==first
