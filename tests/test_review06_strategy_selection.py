"""SG-06 owner-authorized mechanics. Synthetic, offline, no edge/venue proof."""
import json
import socket
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from trader.core.types import Action, Decision, Snapshot, StrategySignal, Side
from trader.engine import orchestrator as mod
from trader.strategy import library
from trader.strategy.plan_owner import (VERSION, select, decision_owner, exit_score,
                                       historical_owner, verified_selection)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('network forbidden'))


def sig(sid='a', action='BUY', confidence=.6, **params):
    return StrategySignal(sid, sid, 'S', Action(action), confidence, 'fixture', params)


@pytest.fixture
def decide(tmp_path, monkeypatch):
    from trader.core.journal import Journal
    monkeypatch.setattr(mod, 'classify', lambda *a: {'regime':'RANGING', 'adx':20})
    monkeypatch.setattr(mod, 'htf_trend_score', lambda *a:0)
    counter = iter(range(1000))
    def run(signals=(), analysts=(), cfg=None, eligible=True, journal=None, symbol="S"):
        population=[]
        for n,s in enumerate(signals):
            family=f'fixture{n}'
            monkeypatch.setitem(library.EVALUATORS, family, lambda *a,s=s:s)
            population.append((NS(id=s.strategy_id,is_trade_eligible=eligible),
                               NS(family=family,markets=['futures'],params={})))
        j=journal or Journal(tmp_path / f'j{next(counter)}.db')
        o=mod.Orchestrator(list(analysts),j,cfg=cfg or {})
        snap=Snapshot(symbol,'2026-10-13T00:00:00+00:00',100,{})
        d=o.decide(snap,population)
        j.log_cycle(snap,d.cycle_id,"paper")
        return d,j
    return run


def test_one_strategy_owns_direction_and_retains_confidence(decide):
    d,_=decide([sig()])
    assert (d.action,d.score,d.threshold,d.confidence)==(Action.BUY,.6,.24,.6)
    assert decision_owner(d)=='a' and d.strategy_selection['version']==VERSION
    assert d.meta_size==1 and d.meta_p==0


@pytest.mark.parametrize('direction',['BUY','SELL'])
def test_identical_duplicate_signals_never_add_authority(decide,direction):
    one,_=decide([sig(action=direction)])
    many,_=decide([sig(action=direction)]*20)
    assert (one.action,one.score,one.threshold,one.confidence,one.strategy_selection)==(
        many.action,many.score,many.threshold,many.confidence,many.strategy_selection)
    assert len(many.strategy_signals)==20  # raw evidence survives


@pytest.mark.parametrize('confs',[(.6,.6),(.9,.3),(.3,.9),(.1,.05)])
def test_opposing_strategies_explicitly_hold_without_netting(decide,confs):
    d,_=decide([sig('bull','BUY',confs[0]),sig('bear','SELL',confs[1])])
    assert d.action==Action.HOLD and d.score==d.confidence==0
    assert 'OPPOSING_STRATEGIES' in d.skip_reason and decision_owner(d)==''
    assert len(d.strategy_signals)==2 and d.gated_lean is None


def test_correlated_majority_cannot_outvote_opposing_evidence(decide):
    d,_=decide([sig('clone'+str(n)) for n in range(20)]+[sig('bear','SELL',.1)])
    assert d.action==Action.HOLD and d.score==0


def test_different_agreeing_owners_refuse_unsettled_exit_choice(decide):
    d,_=decide([sig('z','BUY',.9,exit_plan='trail'),sig('a','BUY',.3,exit_plan='target')])
    assert d.action==Action.HOLD and 'PLAN_OWNER_UNRESOLVED' in d.skip_reason
    assert d.strategy_selection['contributors']==['a','z']


def test_reliability_defaults_and_identity_order_are_not_plan_evidence(decide):
    a,_=decide([sig('a'),sig('z')])
    z,_=decide([sig('z'),sig('a')])
    assert a.strategy_selection==z.strategy_selection
    assert a.action==z.action==Action.HOLD


def test_duplicate_identity_with_changed_contract_refuses(decide):
    d,_=decide([sig(spec_sha256='one'),sig(spec_sha256='two')])
    assert d.action==Action.HOLD and 'INCONSISTENT_SIGNALS' in d.skip_reason


@pytest.mark.parametrize('confidence',[float('nan'),float('inf'),-.1,1.1,True])
def test_invalid_signal_is_not_evidence(decide,confidence):
    d,_=decide([sig(confidence=confidence)])
    assert d.action==Action.HOLD and 'INVALID_STRATEGY_SIGNAL' in d.skip_reason


def analyst(name, strength):
    def evaluate(snap):
        v=NS(agent=name,conviction=strength,confidence=.8,meta={},measurement=NS(
            analyst=name,instrument=snap.symbol,observed_at=snap.ts,
            strength=strength,uncertainty={'confidence':.8}))
        v.as_dict=lambda:dict(agent=name,symbol=snap.symbol,side=v.side.value,
            conviction=v.conviction,confidence=v.confidence,rationale='fixture',meta=v.meta)
        return v
    return NS(name=name,evaluate=evaluate,evidence_timeframe='15m')


@pytest.mark.parametrize('strength',[-.9,0,.9])
def test_analyst_measurements_never_change_entry_authority(decide,strength):
    one,_=decide([sig(confidence=.25)])
    panel,_=decide([sig(confidence=.25)], [analyst(str(n),strength) for n in range(7)])
    assert (panel.action,panel.score,panel.threshold,panel.confidence,panel.meta_size)==(
        one.action,one.score,one.threshold,one.confidence,one.meta_size)
    assert len(panel.votes)==7
    assert all(v['meta']['evidence_authority']=='MEASUREMENT_ONLY' for v in panel.votes)


def test_no_strategy_holds_without_analyst_only_prediction(decide):
    d,_=decide([], [analyst('expert',.99)])
    assert d.action==Action.HOLD and d.score==d.confidence==0 and d.gated_lean is None
    assert 'NO_ELIGIBLE_STRATEGY' in d.skip_reason


def test_ineligible_strategy_cannot_propose(decide):
    d,_=decide([sig()],eligible=False)
    assert d.action==Action.HOLD and not d.strategy_signals


def test_obsolete_flags_cannot_restore_voting(decide):
    d,_=decide([], [analyst('expert',.99)],cfg={'scouts':{
        'strategy_leads':False,'require_strategy_signal':False,'meta':{'enabled':True}}})
    assert d.action==Action.HOLD and d.meta_size==1


def test_frozen_weights_and_base_do_not_influence_current_selection(decide,tmp_path):
    from trader.core.journal import Journal
    j=Journal(tmp_path/'frozen.db')
    for key,value in [('legacy_frozen_strategy_weights',{'RANGING':{'a':100}}),
                      ('legacy_frozen_adaptive_base',{'RANGING':.9}),
                      ('legacy_frozen_accuracy_multipliers',{'expert':100})]:
        j.kv_set(key,json.dumps(value))
    d,_=decide([sig(confidence=.25)],journal=j)
    assert d.action==Action.BUY and d.threshold==.24 and d.score==.25


def test_selection_receipt_survives_journal_restart_and_replay(decide):
    from trader.core.journal import Journal
    d,j=decide([sig(spec_sha256='frozen')])
    j.log_decision(d)
    row=Journal(j.db_path).query('select * from decisions where id=?',(d.id,))[0]
    signals=json.loads(row['signals_json'])
    receipt=json.loads(row['strategy_selection_json'])
    replay=NS(action=Action(row['action']),strategy_signals=signals,strategy_selection=receipt)
    assert receipt==d.strategy_selection and decision_owner(replay)==decision_owner(d)
    assert verified_selection(signals,receipt)==receipt


def test_tampered_owner_or_receipt_refuses(decide):
    d,j=decide([sig()])
    d.strategy_selection['owner']='invented'
    with pytest.raises(ValueError,match='receipt_mismatch'):decision_owner(d)
    with pytest.raises(ValueError,match='receipt_mismatch'):j.log_decision(d)


def test_historical_confidence_ownership_remains_original():
    signals=[vars(sig('quiet',confidence=.4)),vars(sig('loud',confidence=.8))]
    old=NS(action=Action.BUY,strategy_signals=signals)
    assert decision_owner(old)==historical_owner(signals,Action.BUY)=='loud'


def test_historical_rows_remain_null_and_unchanged(decide):
    from trader.core.journal import Journal
    d,j=decide([sig()])
    d.strategy_selection=None
    j.log_decision(d)
    before=j.query('select * from decisions where id=?',(d.id,))[0]
    after=Journal(j.db_path).query('select * from decisions where id=?',(d.id,))[0]
    assert before==after and after['strategy_selection_json'] is None


def test_capture_binds_new_owner_and_preserves_historical_owner(tmp_path):
    from trader.learning.capture_runtime import bind_signal_version
    import sqlite3
    c=sqlite3.connect(':memory:')
    c.execute('create table strategy_versions(strategy_id,spec_hash,canonical_json,recorded_at_ms)')
    for sid in ['a','z']:
        c.execute('insert into strategy_versions values(?,?,?,?)',(sid,'h-'+sid,json.dumps({'version_id':'v-'+sid}),1))
    signals=[vars(sig('a',confidence=.4,spec_sha256='h-a')),vars(sig('z',confidence=.9,spec_sha256='h-z'))]
    for s in signals:s['action']=s['action'].value
    row=dict(action='BUY',signals_json=json.dumps(signals),ts='2026-10-13T00:00:00+00:00')
    assert bind_signal_version(c,{},row)['strategy']['version_id']=='v-z'
    row['strategy_selection_json']=json.dumps(select(signals))
    assert bind_signal_version(c,{},row)=={}  # unresolved owner cannot bind a winner
    row['signals_json']=json.dumps(signals[:1]);row['strategy_selection_json']=json.dumps(select(signals[:1]))
    assert bind_signal_version(c,{},row)['strategy']['version_id']=='v-a'


def test_conflict_and_foreign_strategy_cannot_drive_held_plan_exit(decide):
    conflict,_=decide([sig(),sig('bear','SELL',.3)])
    foreign,_=decide([sig('other','SELL')])
    assert exit_score(conflict,{'strategy_id':'a'}) is None
    assert exit_score(foreign,{'strategy_id':'a'}) is None


def test_owning_strategy_flip_is_retained(decide):
    d,_=decide([sig('a','SELL')])
    assert exit_score(d,{'strategy_id':'a'})==-.6
    old=NS(score=-.3)
    assert exit_score(old,{'strategy_id':'a'})==-.3


def test_actual_legacy_exit_only_fires_for_owning_signal(decide,monkeypatch):
    from trader.engine.exits import ExitEngine
    from trader.strategy import factory_handoff as F
    from datetime import datetime,timezone
    monkeypatch.setattr(F,'versioned',lambda *a:False)
    trade=dict(id='t',status='open',strategy_id='a',entry_price=100,side='long',
               stop_loss=95,amount=1,initial_risk=5,opened_at=datetime.now(timezone.utc).isoformat())
    e=ExitEngine.__new__(ExitEngine);e.journal=NS(query=lambda *a:[trade.copy()])
    e.spec_exits={};e.genomes={};e.c=NS(max_hold_hours=100,flip_threshold=.18,
        tp1_r_mult=1.5,trail_after_r=1)
    calls=[];e.executor=NS(close=lambda *a,**kw:calls.append(kw) or True)
    foreign,_=decide([sig('other','SELL')])
    assert e.manage(trade,100,2,exit_score(foreign,trade)) is None and calls==[]
    own,_=decide([sig('a','SELL')])
    assert e.manage(trade,100,2,exit_score(own,trade)).startswith('flip_exit') and len(calls)==1


def test_risk_refuses_second_exposure(tmp_path):
    from trader.core.journal import Journal
    from trader.core.types import ControlState,Position
    from trader.engine.risk import RiskManager
    cfg={'risk':{'risk_per_trade_pct':1,'portfolio_heat_cap_pct':6,'per_symbol_risk_cap_pct':2,
        'max_open_positions':4,'max_daily_loss_pct':3,'halt_drawdown_pct':20,'leverage':3,
        'stop_loss_atr_mult':2,'min_notional_usdt':10,'drawdown_derisk_steps':[]}}
    r=RiskManager(cfg,Journal(tmp_path/'risk.db'));r.update_equity(10000)
    held=Position('p','S',Side.LONG,1,100,100,3,95)
    out=r.check_entry(ControlState.ACTIVE,'S',100,2,.05,[held],10000,50,'futures')
    assert not out.ok and out.reason=='already exposed here'


def test_different_instruments_are_separate_candidates(decide):
    a,b=sig(),sig()
    a.symbol,b.symbol='BTC/USDT','ETH/USDT'
    btc,_=decide([a],symbol=a.symbol);eth,_=decide([b],symbol=b.symbol)
    assert btc.symbol != eth.symbol and btc.action==eth.action==Action.BUY


def test_pre_receipt_schema_migration_does_not_rewrite_history(decide):
    from trader.core.journal import Journal
    import sqlite3
    d,j=decide([sig()])
    d.strategy_selection=None
    j.log_decision(d)
    # A genuine pre-receipt schema: existing row values survive additive upgrade.
    with sqlite3.connect(j.db_path) as c:
        c.execute('ALTER TABLE decisions DROP COLUMN strategy_selection_json')
        c.row_factory=sqlite3.Row
        before=dict(c.execute('select * from decisions where id=?',(d.id,)).fetchone())
    after=Journal(j.db_path).query('select * from decisions where id=?',(d.id,))[0]
    assert after.pop('strategy_selection_json') is None and after==before


def test_htf_guards_remain_strategy_constraints(decide,monkeypatch):
    monkeypatch.setattr(mod,'htf_trend_score',lambda *a:.9)
    d,_=decide([sig(action='SELL')])
    assert d.action==Action.HOLD and 'hard veto' in d.skip_reason
    monkeypatch.setattr(mod,'htf_trend_score',lambda *a:.5)
    d,_=decide([sig(action='SELL')])
    assert d.threshold==pytest.approx(.31)


def test_bad_receipt_refuses_entry_owner_without_disabling_plan_exits(decide):
    from trader.kernel import Kernel
    d,_=decide([sig()])
    d.strategy_selection['owner']='invented'
    assert Kernel._top_strategy(None,d)=='' and 'receipt_mismatch' in d.skip_reason
    assert exit_score(d,{'strategy_id':'a'}) is None


def test_historical_imports_remain_available_but_no_voting_methods_exist():
    from trader.engine.orchestrator import net_score
    from trader.strategy.legacy_blend import net_score as historical
    assert net_score is historical
    assert not hasattr(mod.Orchestrator,'_strategy_weights')
    assert not hasattr(mod.Orchestrator,'_adaptive_base')


def test_current_path_has_no_legacy_weight_or_meta_model_calls():
    import ast,inspect,textwrap
    from trader.kernel import Kernel
    sources=[inspect.getsource(mod.Orchestrator),inspect.getsource(Kernel._maybe_resolve_outcomes)]
    forbidden={'net_score','strategy_score','agreement_fraction','_strategy_weights',
        '_adaptive_base','_accuracy_multipliers','maybe_refit_calibration','maybe_update'}
    for source in sources:
        calls={n.func.id for n in ast.walk(ast.parse(textwrap.dedent(source))) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
        assert not calls & forbidden
    source=inspect.getsource(mod.Orchestrator.decide)
    assert 'meta_label' not in source and 'base_weights' not in source
