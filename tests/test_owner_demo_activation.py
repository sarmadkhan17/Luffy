"""Owner-authorized demo execution; no statistical PASS or real-money grant."""
import json,time
import pytest
from trader.core.journal import Journal
from trader.strategy import owner_demo as D, factory_handoff as F

@pytest.fixture
def setup(tmp_path,monkeypatch):
 from tests.test_universal_strategy_authority import proposal
 monkeypatch.setenv('BINANCE_DEMO','true')
 j=Journal(tmp_path/'journal.db');s=proposal('demo_test','Demo test');j.upsert_spec(s)
 j.kv_set('control_state','FROZEN');j.kv_set('macro_guard_operator_hold','1')
 return j,s,{'mode':{'market':'futures','execution':'live'}}

def grant(j,cfg):
 return D.authorize(j,cfg,'demo_test',actor='operator',reason='TEST-ONLY explicit owner demo activation without validation',at_ms=int(time.time()*1000))

def test_exact_unvalidated_demo_version_and_active_projection(setup):
 j,s,cfg=setup
 assert F.live_entry_block(j,s.id,cfg=cfg)=='VERSIONED_AUTHORITY_REQUIRED'
 r=grant(j,cfg)
 assert r['validation']=='NOT_ASSERTED_OWNER_AUTHORIZED'
 assert j.query('SELECT state FROM strategies WHERE id=?',(s.id,))[0]['state']=='active'
 assert F.live_entry_block(j,s.id,cfg=cfg) is None
 assert F.versioned(j,s.id) is False
 assert F.live_allocation(j,s.id,cfg) is None
 assert not j.query("SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_versions'")
 assert grant(j,cfg)==r
 with pytest.raises(Exception):
  with j._tx() as db:db.execute(f'DELETE FROM {D.TABLE}')

def test_production_environment_and_missing_explicit_demo_are_refused(setup,monkeypatch):
 j,s,cfg=setup;grant(j,cfg)
 for value in ('false',''):
  monkeypatch.setenv('BINANCE_DEMO',value)
  assert F.live_entry_block(j,s.id,cfg=cfg)=='OWNER_DEMO_ENVIRONMENT_REQUIRED'
  with pytest.raises(ValueError):grant(j,cfg)

def test_changed_spec_and_changed_lifecycle_refuse(setup):
 j,s,cfg=setup;grant(j,cfg)
 with j._tx() as db:db.execute("UPDATE strategies SET state='paper' WHERE id=?",(s.id,))
 assert F.live_entry_block(j,s.id,cfg=cfg)=='OWNER_DEMO_STRATEGY_NOT_ACTIVE'
 with j._tx() as db:
  changed=s.to_dict();changed['entry_long']='close > 100'
  db.execute("UPDATE strategies SET state='active',spec_json=? WHERE id=?",(json.dumps(changed),s.id))
 assert F.live_entry_block(j,s.id,cfg=cfg)=='OWNER_DEMO_DEFINITION_CHANGED'

def test_config_and_nonowner_or_uncontained_creation_refuse(setup):
 j,s,cfg=setup;grant(j,cfg)
 assert F.live_entry_block(j,s.id,cfg={'mode':{'market':'futures','execution':'paper'}})=='OWNER_DEMO_EXECUTION_CONFIGURATION_REQUIRED'
 with pytest.raises(ValueError):D.authorize(j,cfg,s.id,actor='research',reason='TEST',at_ms=int(time.time()*1000))
 j.kv_set('control_state','ACTIVE')
 with pytest.raises(ValueError):grant(j,cfg)

def test_actual_production_venue_refused_despite_demo_environment(setup):
 from trader.data.feed import make_exchange
 from trader.observability.portfolio_observation import trading_source
 j,s,cfg=setup;grant(j,cfg)
 demo=make_exchange('futures',demo=True,with_keys=False);prod=make_exchange('futures',demo=False,with_keys=False)
 assert trading_source(demo)[0]=='demo'
 assert D.venue_block(j,s.id,demo) is None
 assert D.venue_block(j,s.id,prod)=='OWNER_DEMO_ACTUAL_VENUE_REQUIRED'

def test_executor_checks_actual_venue_and_retains_frozen_control(setup,monkeypatch):
 from trader.engine.executor import Executor
 from trader.core.config import load_config
 from trader.core.types import Decision,Action,MarketType
 from trader.data.feed import make_exchange
 j,s,cfg=setup;grant(j,cfg)
 config=load_config();config['mode']=cfg['mode']
 ex=Executor(make_exchange('futures',demo=False,with_keys=False),j,config,MarketType.FUTURES)
 monkeypatch.setattr(ex,'recovery_pending',lambda:False)
 calls=[]
 monkeypatch.setattr(ex,'_open_serialized',lambda *a,**kw:calls.append(True) or 'DEMO_SUBMIT_BOUNDARY')
 def decision():return Decision('d','c','BTC/USDT',Action.BUY,1.,.5,.8,[],[])
 frozen=decision()
 assert ex.open(frozen,1,1,99,102,strategy_id=s.id,strategy_name=s.name,exec_mode='live') is None
 assert not calls
 j.kv_set('control_state','ACTIVE')
 prod=decision()
 assert ex.open(prod,1,1,99,102,strategy_id=s.id,strategy_name=s.name,exec_mode='live') is None
 assert 'OWNER_DEMO_ACTUAL_VENUE_REQUIRED' in prod.skip_reason
 assert not calls
 ex.ex=make_exchange('futures',demo=True,with_keys=False)
 assert ex.open(decision(),1,1,99,102,strategy_id=s.id,strategy_name=s.name,exec_mode='live')=='DEMO_SUBMIT_BOUNDARY'
 assert len(calls)==1


def test_frozen_startup_requires_every_active_strategy_exactly_authorized(setup,monkeypatch):
 from tests.test_universal_strategy_authority import proposal
 j,s,cfg=setup
 assert D.startup_allowed(j,cfg) is False
 grant(j,cfg)
 assert D.startup_allowed(j,cfg) is True
 other=proposal('other','Other')
 j.upsert_spec(other,state='active')
 assert D.startup_allowed(j,cfg) is False
 with j._tx() as db:db.execute("UPDATE strategies SET state='paper' WHERE id='other'")
 monkeypatch.setenv('BINANCE_DEMO','false')
 assert D.startup_allowed(j,cfg) is False
