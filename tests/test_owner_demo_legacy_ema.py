import json,time
import pytest
from trader.core.journal import Journal
from trader.strategy import owner_demo as D,factory_handoff as F
from trader.strategy.library import build_seed_population

@pytest.fixture
def setup(tmp_path,monkeypatch):
 monkeypatch.setenv('BINANCE_DEMO','true')
 j=Journal(tmp_path/'j.db');st,g=next((s,g) for s,g in build_seed_population() if g.family=='ema_trend')
 for key in ('hypothesis','invalidation','regime_filter','markets'):setattr(st,key,getattr(g,key))
 j.upsert_strategy(st)
 with j._tx() as db:db.execute("UPDATE strategies SET state='demoted' WHERE id=?",(st.id,))
 j.kv_set('control_state','FROZEN');j.kv_set('macro_guard_operator_hold','1')
 return j,st,{'mode':{'market':'futures','execution':'live'}}

def grant(j,st,cfg):return D.authorize(j,cfg,st.id,actor='operator',reason='TEST-ONLY owner EMA Demo reactivation',at_ms=int(time.time()*1000))

def test_owner_can_restore_exact_demoted_ema_without_forging_validation(setup):
 j,st,cfg=setup;before=j.query('SELECT * FROM strategies WHERE id=?',(st.id,))[0]
 record=grant(j,st,cfg)
 assert record['validation']=='NOT_ASSERTED_OWNER_AUTHORIZED'
 assert record['scope']=='BINANCE_DEMO_ONLY' and 'legacy_definition' in record and 'spec' not in record
 after=j.query('SELECT * FROM strategies WHERE id=?',(st.id,))[0]
 assert {k:v for k,v in before.items() if k not in ('state','state_changed_at')}=={k:v for k,v in after.items() if k not in ('state','state_changed_at')}
 assert after['state']=='active' and D.block(j,st.id,cfg) is None
 assert F.live_entry_block(j,st.id,cfg=cfg) is None
 assert D.startup_allowed(j,cfg) is True
 assert grant(j,st,cfg)==record

@pytest.mark.parametrize('column,value',[('params',json.dumps({'adx_min':25.,'pullback_atr':.8,'trend_tf':'15m'})),('regime_filter','["RANGING"]'),('markets','["spot"]')])
def test_changed_trading_definition_is_refused(setup,column,value):
 j,st,cfg=setup;grant(j,st,cfg)
 with j._tx() as db:db.execute(f'UPDATE strategies SET {column}=? WHERE id=?',(value,st.id))
 assert D.block(j,st.id,cfg)=='OWNER_DEMO_DEFINITION_CHANGED'

def test_real_money_and_unfrozen_grant_remain_refused(setup,monkeypatch):
 j,st,cfg=setup;grant(j,st,cfg)
 monkeypatch.setenv('BINANCE_DEMO','false')
 assert F.live_entry_block(j,st.id,cfg=cfg)=='OWNER_DEMO_ENVIRONMENT_REQUIRED'
 monkeypatch.setenv('BINANCE_DEMO','true');j.kv_set('control_state','ACTIVE')
 with pytest.raises(ValueError):grant(j,st,cfg)

def test_evaluator_change_invalidates_permission(setup,monkeypatch):
 import trader.strategy.library as lib
 j,st,cfg=setup;grant(j,st,cfg)
 def changed(g,snap):return None
 monkeypatch.setattr(lib,'eval_ema_trend',changed)
 assert D.block(j,st.id,cfg)=='OWNER_DEMO_DEFINITION_CHANGED'
