"""Isolated lifecycle/capture checks; fixture costs and capacity are TEST ONLY."""
import copy
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from trader.strategy import factory_handoff as F
from trader.observability import prospective_execution as P
from tests.test_strategy_factory_handoff import _journal, _approved, cfg, INPUTS, T0, DAY
from tests.stage5_test_support import paths


def rows(capture):
    with sqlite3.connect(capture.path) as db:
        return [(r[0],json.loads(r[1])) for r in db.execute('SELECT kind,body FROM observations ORDER BY seq')]


def test_governor_refuses_activation_without_capacity(tmp_path,cfg):
    j,_=_journal(tmp_path);v,_,_=_approved(j,cfg)
    with pytest.raises(F.HandoffRefused,match='capacity_receipt_not_asserted'):
        F.govern_version(j,cfg,v['version_id'],'ACTIVE',actor='operator',reason_code='owner',
                         at_ms=T0+32*DAY,allocation=.25,available_inputs=INPUTS)
    assert F.state_of(j,v['version_id'])==F.APPROVED_FIRST_LIVE


def test_governor_cycles_preserve_boundaries_and_live_fence(tmp_path,cfg,monkeypatch):
    # Only hypothetical established capacity; production has no such source.
    monkeypatch.setattr(F.cap,'check_current',lambda *a,**k:dict(status='ESTABLISHED',reasons=[],current=True))
    j,_=_journal(tmp_path);v,_,_=_approved(j,cfg);vid=v['version_id']
    at=T0+32*DAY
    def act(state,actor='strategy_governor',allocation=None):
        nonlocal at
        at+=1
        return F.govern_version(j,cfg,vid,state,actor=actor,reason_code='TEST_ONLY',at_ms=at,
            allocation=allocation,available_inputs=INPUTS,capacity_receipt_id='TEST_ONLY')
    with pytest.raises(F.HandoffRefused,match='owner_boundaries'):
        act('ACTIVE',allocation=.25)
    act('ACTIVE','operator',.25)
    assert F.live_entry_block(j,v['strategy_id']) is not None
    for state in ('PAUSED','REACTIVATED','DEGRADED','REACTIVATED','PAUSED','REACTIVATED'):
        act(state,allocation=.2 if state=='REACTIVATED' else None)
    act('PAUSED')
    with pytest.raises(F.HandoffRefused,match='boundaries_changed'):
        act('REACTIVATED',allocation=.3)
    changed=copy.deepcopy(cfg);changed['risk']['leverage']+=1
    with pytest.raises(F.HandoffRefused,match='boundaries_changed'):
        F.govern_version(j,changed,vid,'REACTIVATED',actor='operator',reason_code='test',at_ms=at+1,
            allocation=.2,available_inputs=INPUTS,capacity_receipt_id='TEST_ONLY')
    act('RETIRED')
    with pytest.raises(F.HandoffRefused,match='transition_not_allowed'):
        act('REACTIVATED',allocation=.2)
    assert len(F.governor_events(j,vid))==9
    with j._tx() as db,pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE strategy_governor_events SET to_state='ACTIVE'")


def test_governor_lost_costs_cannot_resume(tmp_path,cfg,monkeypatch):
    monkeypatch.setattr(F.cap,'check_current',lambda *a,**k:dict(reasons=[],current=True))
    j,_=_journal(tmp_path);v,_,_=_approved(j,cfg);at=T0+32*DAY
    F.govern_version(j,cfg,v['version_id'],'ACTIVE',actor='operator',reason_code='TEST_ONLY',at_ms=at,
        allocation=.25,available_inputs=INPUTS,capacity_receipt_id='TEST_ONLY')
    F.retire_version(j,v['version_id'],F.DEGRADED,actor='strategy_governor',reason_code='weak',at_ms=at+1)
    from trader.engine import paper_cost_evidence as C
    monkeypatch.setattr(C,'SOURCE_VALIDATORS',{})
    with pytest.raises(F.HandoffRefused,match='governor_evidence_unavailable'):
        F.govern_version(j,cfg,v['version_id'],'REACTIVATED',actor='strategy_governor',reason_code='test',
            at_ms=at+2,allocation=.2,available_inputs=INPUTS,capacity_receipt_id='TEST_ONLY')


def test_changed_version_has_no_governor_grant(tmp_path,cfg):
    j,_=_journal(tmp_path);v,_,_=_approved(j,cfg)
    from trader.strategy.spec import StrategySpec
    spec=StrategySpec.from_dict(F.load_version(j,v['version_id'])['spec']);spec.timeframe='1h'
    child=F.derive_version(j,v['version_id'],spec,at_ms=T0+32*DAY)
    with pytest.raises(F.HandoffRefused):
        F.govern_version(j,cfg,child['version_id'],'ACTIVE',actor='operator',reason_code='owner',
            at_ms=T0+33*DAY,allocation=.2,available_inputs=INPUTS)
    assert F.approval_request(j,child['version_id']) is None


class Venue:
    urls={'api':{'fapiPublic':'https://fapi.binance.com/fapi/v1'}}
    def __init__(self):self.calls=[]
    def create_order(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        return dict(id='o',status='open',filled=1,remaining=1,amount=2,side='buy',average=100)
    def fetch_my_trades(self,*args,**kwargs):
        self.calls.append(('fills',args,kwargs))
        return [dict(id='f',order='o',symbol='BTC/USDT',timestamp=123,side='buy',amount=1,price=100,
                     info={'commission':'0.1','commissionAsset':'USDT','apiKey':'NEVER_PERSIST'})]
    def cancel_order(self,*args,**kwargs):return {'status':'canceled'}


def depth(url,**kwargs):
    now=time.time_ns()//1_000_000
    return SimpleNamespace(status=200,body=json.dumps(dict(lastUpdateId=1,E=now,T=now,
        bids=[['99','2']],asks=[['100','2']])).encode())


def test_existing_order_calls_capture_exact_fields_and_late_books(tmp_path):
    cap=P.Capture(tmp_path/'e.db',fetch=depth);venue=Venue();ex=P.ObservedExchange(venue,cap)
    ex.context(dict(decision_id='d',strategy_identity={'version_id':'v','spec_sha256':'h'},
        reference={'price':100,'observed_at':time.time_ns()//1_000_000-50,'basis':'decision'}))
    result=ex.create_order('BTC/USDT','market','buy',2,params={'reduceOnly':False})
    assert result['id']=='o' and venue.calls[0]==(('BTC/USDT','market','buy',2),{'params':{'reduceOnly':False}})
    ex.fetch_my_trades('BTC/USDT',since=100);ex.cancel_order('o','BTC/USDT')
    cap.pool.shutdown(wait=True)
    observed=rows(cap)
    intent=next(b for k,b in observed if k=='submission_intent')
    reply=next(b for k,b in observed if k=='order_response')
    assert intent['requested_quantity']==2 and intent['decision_id']=='d'
    assert intent['strategy_identity']['version_id']=='v' and reply['intent_id']==intent['intent_id']
    assert reply['submitted_ms']>=intent['event_ms'] and reply['order']['remaining']==1
    books=[b for k,b in observed if k=='linked_book']
    assert len(books)==2 and all('NOT_EXACT_EVENT_TIME_BOOK' in b['authority'] for b in books)
    fill=next(b for k,b in observed if k=='venue_fill')['fill']
    assert fill['order']=='o' and fill['commission_asset']=='USDT'
    assert 'NEVER_PERSIST' not in cap.path.read_bytes().decode(errors='ignore')
    assert any(k=='cancel_response' for k,b in observed)


def test_submission_failure_is_not_fabricated_rejection(tmp_path):
    cap=P.Capture(tmp_path/'e.db');v=Venue()
    failure=TimeoutError('NEVER_PERSIST')
    def fail(*a,**k):raise failure
    v.create_order=fail;ex=P.ObservedExchange(v,cap)
    with pytest.raises(TimeoutError) as raised:ex.create_order('BTC/USDT','market','buy',2)
    assert raised.value is failure
    cap.pool.shutdown(wait=True)
    error=next(b for k,b in rows(cap) if k=='submission_error')
    assert error['outcome']=='AMBIGUOUS' and 'NEVER_PERSIST' not in json.dumps(error)


def test_funding_uses_event_mark_and_frozen_quantity(tmp_path):
    def fetch(*a,**k):return SimpleNamespace(status=200,body=json.dumps([
        dict(symbol='BTCUSDT',fundingTime=150,fundingRate='-0.01',markPrice='10')]).encode())
    cap=P.Capture(tmp_path/'e.db',fetch=fetch)
    exposure=dict(environment='production',quantity=2,opened_ms=100,instrument='BTC/USDT',side='long')
    trade=dict(id='p',symbol='BTC/USDT',amount=2,entry_price=999,
        entry_identity_json=json.dumps({'paper_exposure':exposure}))
    cap.funding(trade,200);cap.pool.shutdown(wait=True)
    event=next(b for k,b in rows(cap) if k=='paper_funding_event')
    assert event['position_notional']=='20' and event['event']['published_rate']=='-0.01'
    assert event['qualification']=='NOT_A_FINALIZED_COST_RECEIPT'


def test_storage_exhaustion_is_explicit(tmp_path,monkeypatch):
    cap=P.Capture(tmp_path/'e.db');cap.write('test',{'x':1})
    monkeypatch.setattr(P,'MAX_STORAGE_BYTES',1)
    assert cap.write('test',{'x':2}) is None
    assert json.loads(cap.path.with_suffix('.status.json').read_text())['reason']=='STORAGE_BOUND_REACHED'
    cap.pool.shutdown()


def test_paper_capture_is_automatic_and_environment_frozen(tmp_path):
    from tests.test_versioned_paper_execution import setup,frames,snap,decision,append
    from trader.engine.paper import PaperExecutor,TABLE
    from trader.core.types import ControlState
    import pandas as pd
    j,cfg,v=setup(tmp_path)
    runner=PaperExecutor(j,cfg,venue_environment='production')
    captured=[]
    runner.capture=SimpleNamespace(book=lambda e:captured.append(('book',e)),
        funding=lambda t,end:captured.append(('funding',t,end)))
    df=frames(v);df.ts+=pd.Timedelta(hours=4)
    s=snap(df);tid=runner.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    p=runner.open_positions()[0]
    frozen=json.loads(p['entry_identity_json'])['paper_exposure']
    assert frozen['environment']=='production' and frozen['quantity']==p['amount']
    assert p['venue_environment']=='production' and captured[0][1]['intent_id']==tid
    with j._tx() as db,pytest.raises(sqlite3.IntegrityError):
        db.execute(f"UPDATE {TABLE} SET venue_environment='demo'")
    df=append(df,p['take_profit'],high=max(p['entry_price'],p['take_profit'])+.1,
        low=min(p['entry_price'],p['take_profit'])-.1)
    assert runner.manage(snap(df))==[tid]
    assert any(x[0]=='funding' and x[1]['id']==tid for x in captured)
    costs=j.query('SELECT canonical_json FROM versioned_paper_cost_receipts')[0]
    assert json.loads(costs['canonical_json'])['net_pnl_status']=='UNAVAILABLE'


def test_sidecar_replay_is_exact_and_read_only(tmp_path):
    cap=P.Capture(tmp_path/'e.db',fetch=depth);ex=P.ObservedExchange(Venue(),cap)
    ex.context({'decision_id':'d','reference':{'price':100,'observed_at':100}})
    ex.create_order('BTC/USDT','market','buy',2);ex.fetch_my_trades('BTC/USDT')
    cap.pool.shutdown(wait=True)
    before=cap.path.read_bytes();out=P.replay_observations(cap.path)
    assert cap.path.read_bytes()==before and len(out)==1 and len(out[0]['fills'])==1
    assert out[0]['intent']['decision_id']=='d'
    with sqlite3.connect(cap.path) as db:
        db.execute('DROP TRIGGER obs_no_update')
        db.execute("UPDATE observations SET body='{}' WHERE kind='submission_intent'")
    with pytest.raises(ValueError,match='replay_differs'):P.replay_observations(cap.path)


def test_reported_order_fee_is_not_commission_authority(tmp_path):
    cap=P.Capture(tmp_path/'e.db');v=Venue()
    response=dict(id='o',status='closed',filled=2,fee={'currency':'USDT','cost':.2,'rate':.001})
    v.create_order=lambda *a,**k:response
    ex=P.ObservedExchange(v,cap)
    assert ex.create_order('BTC/USDT','market','buy',2) is response
    fee=next(b for k,b in rows(cap) if k=='reported_order_fee')
    assert fee['qualification']=='EXACT_ORDER_ONLY; NOT_ACCOUNT_COMMISSION_AUTHORITY'
    assert fee['fee']['cost']==.2
    response['fee']='malformed'
    assert ex.create_order('BTC/USDT','market','buy',2) is response
    assert any(k=='order_fee_unavailable' for k,b in rows(cap))
    cap.pool.shutdown()


def test_funding_producer_does_not_depend_on_strategy_snapshot(tmp_path):
    from trader.kernel import Kernel
    from tests.test_versioned_paper_execution import setup,frames,snap,decision
    from trader.engine.paper import PaperExecutor
    from trader.core.types import ControlState
    import pandas as pd
    j,cfg,v=setup(tmp_path);k=Kernel.__new__(Kernel);k.journal=j
    calls=[];capture=SimpleNamespace(funding=lambda t,end:calls.append(t),blocked=lambda r:None)
    k._paper_funding_once(capture)
    assert calls==[]
    runner=PaperExecutor(j,cfg,venue_environment='production')
    runner.capture=SimpleNamespace(book=lambda e:None)
    df=frames(v);df.ts+=pd.Timedelta(hours=4);s=snap(df)
    runner.enter(decision(v,s),s,v['strategy_id'],reference_equity=5000,state=ControlState.ACTIVE)
    k._paper_funding_once(capture)
    assert len(calls)==1 and calls[0]['venue_environment']=='production'
