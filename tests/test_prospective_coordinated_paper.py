"""Prospective paper fills occur after the actual decision, never at a past open."""
from copy import deepcopy
import pytest

from trader.core.journal import Journal
from trader.engine.coordinated_paper import CoordinatedPaper,QUOTE_TIMING
from trader.research.compose import hedge as H
from tests.test_managed_hedge import policy


def setup(tmp_path,primary_exit=None,management=None):
    j=Journal(tmp_path/'paper.db')
    spec=H.make_spec(purpose='protective_beta',primary={'symbol':'A/USDT'},hedge={'symbol':'B/USDT'},
        ratio={'method':'fixed','value':1.,'lookback':20,'max_ratio':2.},management=management or policy())
    runner=CoordinatedPaper(j)
    key=runner.start(spec=spec,identity=dict(exec_mode='paper',version_id='v',spec_hash='s',
        install_id='i',admission_id='a'),timing=QUOTE_TIMING,primary_exit=primary_exit)
    return j,runner,key


def obs(tick,*,native=0,signal=0,pm=100.,hm=100.,exit_=False):
    quotes={leg:dict(symbol=symbol,contract='linear',instrument_id='binanceusdm:futures:'+symbol.replace('/',''),
                     source='public-venue-book',started_ms=tick-2,received_ms=tick-1,bid=mark-1.,ask=mark+1.)
            for leg,symbol,mark in (('primary','A/USDT',pm),('hedge','B/USDT',hm))}
    return dict(p=pm,h=hm,po=pm,ho=hm,signal=signal,exit=exit_,ratio=1.,corr=1.,
                funding={'primary':0.,'hedge':0.},signal_cut_ms=1000+native*1000,
                paper_quote=True,quotes=quotes,decision_ms=tick,native_bar=native,
                native_observation={'signal_cut_ms':1000+native*1000})


def test_primary_fills_at_following_ask_without_waiting_for_another_candle(tmp_path):
    j,r,key=setup(tmp_path)
    first=r.advance(key,0,obs(1100,signal=1),allow_entry=True)
    assert len(first['intents'])==1 and first['intents'][0]['leg']=='primary'
    assert not any(first['positions'].values())
    requested=first['intents'][0]['qty']
    second=r.advance(key,1,obs(2100),allow_entry=True)
    assert second['native_bar']==0 and second['positions']['primary']==requested
    assert second['positions']['hedge']==0
    expected=10000.-requested*(101.-100.)-second['costs']
    assert second['equity']==pytest.approx(expected)
    assert second['fill_basis']=='SIMULATED_FIRST_SUBSEQUENT_BID_ASK'
    assert second['quantity_basis']=='SIMULATED_FULL_REQUEST_NOT_VENUE_CAPACITY'
    assert not j.query('SELECT * FROM trades')
    restart=CoordinatedPaper(Journal(j.db_path))
    assert restart.advance(key,1,obs(2100),allow_entry=True)==second


def test_conditional_hedge_and_exit_use_new_quotes_within_same_native_bar(tmp_path):
    _,r,key=setup(tmp_path)
    r.advance(key,0,obs(1100,signal=1),allow_entry=True)
    r.advance(key,1,obs(2100),allow_entry=True)
    loss=r.advance(key,2,obs(3100,pm=90.),allow_entry=True)
    assert any(i['leg']=='hedge' and i['reason']=='risk_management_activation' for i in loss['intents'])
    hedged=r.advance(key,3,obs(4100,pm=90.),allow_entry=True)
    assert hedged['positions']['hedge']<0 and hedged['native_bar']==0
    # A combined target, after both legs' spread and declared fees.
    target=r.advance(key,4,obs(5100,pm=130.,hm=50.),allow_entry=True)
    assert any(i['kind']=='UNWIND' for i in target['intents'])
    r.advance(key,5,obs(6100,pm=130.,hm=50.),allow_entry=False)
    r.advance(key,6,obs(7100,pm=130.,hm=50.),allow_entry=False)
    assert not any(r.state(key)['book']['pos'].values())


@pytest.mark.parametrize('change',['old_quote','future_quote','wrong_contract','crossed_book','different_source','signal_repeat'])
def test_invalid_quote_or_repeated_native_signal_cannot_fill(tmp_path,change):
    _,r,key=setup(tmp_path)
    r.advance(key,0,obs(1100,signal=1),allow_entry=True)
    value=obs(2100)
    if change=='old_quote':value['quotes']['primary']['started_ms']=1099
    elif change=='future_quote':value['quotes']['primary']['received_ms']=2101
    elif change=='wrong_contract':value['quotes']['primary']['contract']='inverse'
    elif change=='crossed_book':value['quotes']['primary']['bid']=102.
    elif change=='different_source':value['quotes']['primary']['source']='other-venue'
    else:value['signal']=1
    before=deepcopy(r.state(key))
    with pytest.raises(ValueError,match='coordinated_paper_'):
        r.advance(key,1,value,allow_entry=True)
    assert r.state(key)==before


def test_hold_prevents_queued_entry_and_a_missed_interval_is_not_complete_evidence(tmp_path):
    _,r,key=setup(tmp_path)
    r.advance(key,0,obs(1100,signal=1),allow_entry=True)
    assert not any(r.advance(key,1,obs(2100),allow_entry=False)['positions'].values())
    # A second independently started book can mark a held gap, but never
    # represent its unobserved intervening path as complete paper economics.
    j=Journal(tmp_path/'gap.db');other=CoordinatedPaper(j)
    identity=dict(exec_mode='paper',version_id='v',spec_hash='s',install_id='i',admission_id='a')
    key2=other.start(spec=r.state(key)['spec'],identity=identity,timing=QUOTE_TIMING)
    other.advance(key2,0,obs(1100,signal=1),allow_entry=True)
    other.advance(key2,1,obs(2100),allow_entry=True)
    gap=other.advance(key2,2,obs(70_000),allow_entry=False)
    assert gap['incomplete_marks'] and gap['net_economics']=='UNAVAILABLE'


def test_a_late_first_quote_does_not_fill_an_expired_signal(tmp_path):
    _,r,key=setup(tmp_path)
    r.advance(key,0,obs(1100,signal=1),allow_entry=True)
    late=r.advance(key,1,obs(70_000),allow_entry=True)
    assert not any(late['fills'].values()) and not any(late['positions'].values())


def test_idle_cash_interval_does_not_expire_a_newly_calculated_signal(tmp_path):
    _,r,key=setup(tmp_path)
    r.advance(key,0,obs(1100),allow_entry=True)
    fresh=r.advance(key,1,obs(70_000,native=1,signal=1),allow_entry=True)
    assert any(i['leg']=='primary' and i['kind']=='ENTER' for i in fresh['intents'])
    filled=r.advance(key,2,obs(71_000,native=1),allow_entry=True)
    assert filled['positions']['primary']>0 and not filled['incomplete_marks']


def test_frequent_quotes_do_not_inflate_native_risk_or_statistical_episodes():
    import numpy as np
    from trader.research.closure.objective_admission import episode_values
    n=100;eq=np.linspace(100.,110.,n);gross=np.ones(n)
    native=np.zeros(n,dtype=int)
    assert episode_values(eq,start_equity=100.,gross=gross,metric='max_drawdown_pct',
                          episode_bars=64,native_bars=native)==[]
    assert len(episode_values(eq,start_equity=100.,gross=gross,metric='max_drawdown_pct',episode_bars=64))==1
    series=np.r_[np.linspace(100.,105.,50),np.linspace(105.,110.,50)]
    native=np.repeat([0,1],50)
    metrics=H.risk_metrics(dict(equity=series,start_equity=100.,gross=gross,net=gross,cost={},
        native_bars=native,primary_active=np.ones(n,dtype=bool),retained_net_fraction=np.ones(n),
        liquidated=False,entries=1,rebalances=0))
    assert metrics['primary_active_bars']==2
    assert metrics['worst_bar_pct']==pytest.approx(-100.*(110./105.-1))


@pytest.mark.parametrize('reason',['stop','target','time'])
def test_durable_quote_main_exit_closes_both_legs_as_one_trade(tmp_path,reason):
    from dataclasses import asdict
    from trader.strategy import exit_policy as E
    from trader.strategy.spec import ExitSpec
    j,r,key=setup(tmp_path,asdict(ExitSpec(stop={'kind':'pct','v':.10},target={'kind':'rr','v':2.},
        time={'max_bars':1 if reason=='time' else 20})))
    def tick(at,*,native=0,signal=0,pm=97.,hm=100.):
        value=obs(at,native=native,signal=signal,pm=pm,hm=hm)
        value['primary_exit_observation']=dict(bar_ms=1000+native*1000,step_ms=1000,
            open=100.,high=200.,low=1.,close=100.,atr=1.)
        return value
    r.advance(key,0,tick(1100,signal=1,pm=100.),allow_entry=True)
    r.advance(key,1,tick(2100,pm=100.),allow_entry=True)
    r.advance(key,2,tick(3100),allow_entry=True)
    r.advance(key,3,tick(4100),allow_entry=True)
    assert all(r.state(key)['book']['pos'].values())
    r=CoordinatedPaper(Journal(j.db_path))
    prices={'stop':dict(pm=89.),'target':dict(pm=121.,hm=130.),'time':dict(native=1)}[reason]
    result=r.advance(key,4,tick(5100,**prices),allow_entry=True)
    assert {i['leg'] for i in result['intents'] if i['kind']=='UNWIND'}=={'primary','hedge'}
    assert E.decode(r.state(key)['book']['primary_exit_state'])[1].reason==reason
    r.advance(key,5,tick(6100,**prices),allow_entry=False)
    assert not any(r.state(key)['book']['pos'].values())
    assert not j.query('SELECT * FROM trades')


def test_quote_episodes_keep_native_bounds_for_statistical_consumer():
    import numpy as np
    from trader.research.closure import objective_admission as OA
    native=np.repeat(np.arange(128),2)
    values=OA.episode_values(np.linspace(100.,110.,256),start_equity=100.,gross=np.ones(256),
        metric='net_return_pct',native_bars=native)
    assert [(v['start'],v['end']) for v in values]==[(0,64),(64,128)]
    assert [(v['sample_start'],v['sample_end']) for v in values]==[(0,128),(128,256)]
    episodes=[dict(start=v['start'],end=v['end'],hedged=v['value'],unhedged=0.,reduced=0.,cash=0.,active=True) for v in values]
    # This must be understood as two native episodes, not 256 independent bets.
    result=OA.evaluate(dict(alpha=.1,result=dict(objective='net_return_pct',
        objective_episodes=episodes,admission_protocol_id=OA.protocol_id())))
    assert not result['admitted']


@pytest.mark.parametrize('action,main_remains',[('close_group',False),('close_hedge',True)])
def test_strategy_hedge_stop_persists_and_executes_on_subsequent_quotes(tmp_path,action,main_remains):
    from dataclasses import asdict
    from tests.test_strategy_hedge_stops import current
    from trader.strategy.spec import ExitSpec
    management=dict(current(action=action),stop_loss_pct=80.)
    j,r,key=setup(tmp_path,asdict(ExitSpec(stop={'kind':'pct','v':.20},
        target={'kind':'none'},trail={'kind':'none'},time={'max_bars':100})),management=management)
    def tick(at,*,signal=0,pm=90.,hm=100.):
        value=obs(at,signal=signal,pm=pm,hm=hm)
        value['primary_exit_observation']=dict(bar_ms=1000,step_ms=1000,open=100.,high=100.,low=100.,
            close=100.,atr=1.,hedge=dict(bar_ms=1000,step_ms=1000,open=100.,high=100.,low=100.,close=100.,atr=1.))
        return value
    r.advance(key,0,tick(1100,signal=1,pm=100.),allow_entry=True)
    r.advance(key,1,tick(2100,pm=100.),allow_entry=True)
    r.advance(key,2,tick(3100),allow_entry=True)
    r.advance(key,3,tick(4100),allow_entry=True)
    assert all(r.state(key)['book']['pos'].values())
    r=CoordinatedPaper(Journal(j.db_path))
    result=r.advance(key,4,tick(5100,hm=112.),allow_entry=True)
    assert {i['reason'] for i in result['intents']}=={'strategy_hedge_stop'}
    expected={'hedge'} if main_remains else {'primary','hedge'}
    assert {i['leg'] for i in result['intents']}==expected
    # Exit intent at this quote has not become a past-price fill.
    assert result['positions']['hedge']<0
    filled=r.advance(key,5,tick(6100,hm=112.),allow_entry=False)
    assert filled['positions']['hedge']==0
    assert bool(filled['positions']['primary'])==main_remains
    assert filled['native_bar']==0
    assert not j.query('SELECT * FROM trades')
