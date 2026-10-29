"""Primary first, conditional protection, ONE combined economic outcome."""
from dataclasses import asdict
import numpy as np
import pytest
from trader.research.closure import ClosureRefused
from trader.research.compose import hedge as H,managed_hedge as M
from tests.test_compose_hedge import spec


def policy(**changes):
    return dict(version=M.VERSION,entry='PRIMARY_FIRST',pnl_basis='COMBINED_NET_EQUITY',
        activate=dict(kind='combined_loss_pct',value=2.),stop_loss_pct=15.,take_profit_pct=5.,**changes)


def bar(*,p=100.,h=50.,equity=1000.,signal=0):
    return dict(p=p,h=h,equity=equity,signal=signal,exit=False,ratio=.5,corr=1.,
        prim_c=H.Contract('linear',1.),hedge_c=H.Contract('linear',1.))


def started():
    book=H.HedgeBook(spec(management=policy(),ratio=dict(method='fixed',value=.5)))
    orders=book.step(0,bar(signal=1))
    book.apply_fills({i.oid:i.qty for i in orders})
    return book,orders


def test_initial_signal_opens_only_main_position_and_neutral_or_profit_does_not_hedge():
    book,orders=started()
    assert len(orders)==1 and orders[0].leg=='primary' and orders[0].side==1
    assert book.step(1,bar(p=101.,equity=1010.))==[]
    assert book.pos['hedge']==0 and not book.management_activated


def test_opposite_leg_requires_frozen_risk_trigger_and_not_initial_rebalance_delay():
    book,_=started()
    orders=book.step(1,bar(p=97.,equity=970.))
    assert len(orders)==1 and orders[0].leg=='hedge' and orders[0].side==-1
    assert orders[0].reason=='risk_management_activation'
    assert book.management_activated


def test_profit_target_uses_combined_net_pnl_not_main_position_profit():
    book,_=started();orders=book.step(1,bar(p=97.,equity=970.))
    book.apply_fills({i.oid:i.qty for i in orders})
    # Main is profitable; opposite leg and costs can still make total negative.
    assert not any(i.kind=='UNWIND' for i in book.step(2,bar(p=120.,h=80.,equity=950.)))
    book.apply_fills({oid:0. for oid in book.pending})
    orders=book.step(3,bar(p=120.,h=80.,equity=1060.))
    assert len(orders)==2 and {i.reason for i in orders}=={'combined_target'}
    assert {i.leg for i in orders}=={'primary','hedge'}


def test_combined_stop_is_not_a_separate_hedge_leg_trade():
    book,_=started();orders=book.step(1,bar(p=97.,equity=970.))
    book.apply_fills({i.oid:i.qty for i in orders})
    orders=book.step(2,bar(equity=840.))
    assert len(orders)==2 and {i.reason for i in orders}=={'combined_stop'}


def test_margin_trigger_models_pressure_and_does_not_promise_liquidation_prevention():
    p=policy();p['activate']=dict(kind='maintenance_buffer_multiple',value=2.)
    decision=M.assess(p,entry_equity=1000.,equity=190.,gross=1000.,maintenance_fraction=.1)
    assert decision['activate'] and decision['liquidation_prevention_guaranteed'] is False


def test_primary_first_state_and_combined_entry_basis_survive_restart():
    book,_=started();restored=H.HedgeBook(**asdict(book))
    assert asdict(book)==asdict(restored)
    assert restored.step(1,bar(p=97.,equity=970.))[0].leg=='hedge'


def test_combined_net_curve_charges_costs_and_delays_hedge_entry():
    p=np.array([100.,100.,97.,97.,97.,97.]);h=np.full(6,50.)
    sp=spec(management=policy(),ratio=dict(method='fixed',value=.5),fee_bps=10.,slip_bps=0.)
    run=H.replay(sp,p,h,np.array([1,0,0,0,0,0]),np.zeros(6,bool),equity=1000.,opens=(p,h),
                 funding=(np.zeros(6),np.zeros(6)))
    hedge=[i for i in run['instructions'] if i.leg=='hedge' and i.kind=='ENTER']
    assert len(hedge)==1 and hedge[0].bar==2
    assert run['cost']['fees']>0 and run['final_equity']<970.
    assert run['entries']==1 # one logical trade, not two performance samples


def test_new_current_protective_candidates_cannot_use_legacy_automatic_joint_entry():
    with pytest.raises(ClosureRefused,match='primary_first_policy_required'):M.require_current(spec())
    M.require_current(spec(management=policy()))


@pytest.mark.parametrize('kind,value',[('combined_loss_pct',15.),('maintenance_buffer_multiple',1.),('unknown',2.)])
def test_missing_or_impossible_strategy_management_trigger_refuses(kind,value):
    p=policy();p['activate']=dict(kind=kind,value=value)
    with pytest.raises(ClosureRefused):M.validate(p)


def test_durable_paper_keeps_profitable_main_and_losing_hedge_as_one_losing_trade(tmp_path):
    from trader.core.journal import Journal
    from trader.engine.coordinated_paper import CoordinatedPaper
    j=Journal(tmp_path/'paper.db');runner=CoordinatedPaper(j)
    sp=spec(management=policy(),ratio=dict(method='fixed',value=.5),fee_bps=10.,slip_bps=0.)
    key=runner.start(spec=sp,equity=1000.,identity=dict(exec_mode='paper',version_id='fixture',
        spec_hash='fixture',install_id='fixture',admission_id='fixture'))
    events=[]
    for index,(p,h) in enumerate([(100.,50.),(100.,50.),(97.,50.),(97.,50.),(120.,80.),(120.,50.),(120.,50.)]):
        if index==3:runner=CoordinatedPaper(j) # real durable restart after requesting hedge
        events.append(runner.advance(key,index,dict(p=p,h=h,po=p,ho=h,signal=1 if index==0 else 0,
            exit=False,ratio=.5,corr=1.,funding=dict(primary=0.,hedge=0.)),allow_entry=True))
    assert len(events[0]['intents'])==1 and events[0]['intents'][0]['leg']=='primary'
    assert events[1]['positions']['primary']>0 and events[1]['positions']['hedge']==0
    assert events[2]['intents'][0]['reason']=='risk_management_activation'
    assert events[4]['equity']<1000. and not any(i['kind']=='UNWIND' for i in events[4]['intents'])
    assert {i['reason'] for i in events[5]['intents']}=={'combined_target'}
    assert events[6]['positions']==dict(primary=0.,hedge=0.)
    assert all(e['real_order_submissions']==0 and e['net_economics']=='MODELLED_PAPER_ONLY' for e in events)


@pytest.mark.parametrize('reason',['stop','target','time'])
def test_main_strategy_exit_closes_whole_managed_position(reason):
    from trader.strategy.spec import ExitSpec
    book=H.HedgeBook(spec(management=policy(),ratio=dict(method='fixed',value=.5)),
        primary_exit_definition=asdict(ExitSpec(stop={'kind':'pct','v':.10},
            target={'kind':'rr','v':2.},time={'max_bars':2 if reason=='time' else 20})))
    def observation(t,price=100.,low=100.,high=100.):
        return dict(bar_ms=1000+t*1000,step_ms=1000,open=100.,high=high,low=low,close=price,atr=1.)
    entry=book.step(0,dict(bar(signal=1),primary_exit_observation=observation(0)))
    book.apply_fills({i.oid:i.qty for i in entry})
    hedge=book.step(1,dict(bar(equity=970.),primary_exit_observation=observation(1)))
    book.apply_fills({i.oid:i.qty for i in hedge})
    assert book.pos['primary']>0 and book.pos['hedge']<0
    prices={'stop':dict(low=89.),'target':dict(high=121.),'time':{}}[reason]
    orders=book.step(2,dict(bar(equity=970.),primary_exit_observation=observation(2,**prices)))
    assert {i.leg for i in orders}=={'primary','hedge'}
    assert all(i.kind=='UNWIND' for i in orders)
    from trader.strategy import exit_policy as E
    assert E.decode(book.primary_exit_state)[1].reason==reason
    assert book.exit_input_gaps==0


def test_quote_main_exit_ignores_prefill_candle_extremes_and_checks_current_prices():
    from trader.strategy.spec import ExitSpec
    book=H.HedgeBook(spec(management=policy(),ratio=dict(method='fixed',value=.5)),
        primary_exit_definition=asdict(ExitSpec(stop={'kind':'pct','v':.10},target={'kind':'rr','v':2.})))
    def tick(t,price=100.,signal=0):
        return dict(bar(p=price,signal=signal),paper_quote=True,
            primary_exit_observation=dict(bar_ms=1000+t*1000,step_ms=1000,
                open=100.,high=200.,low=1.,close=100.,atr=1.))
    entry=book.step(0,tick(0,signal=1))
    book.apply_fills({i.oid:i.qty for i in entry})
    assert book.step(0,tick(0))==[]
    assert book.step(1,tick(1))==[]
    assert book.exit_input_gaps==0
    exit_orders=book.step(1,tick(1,price=89.))
    assert any(i.kind=='UNWIND' for i in exit_orders)
    from trader.strategy import exit_policy as E
    assert E.decode(book.primary_exit_state)[1].reason=='stop'
