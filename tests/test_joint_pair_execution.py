"""A relative-value pair is one economic position, not a conditional hedge.

Offline venue/qualification fixtures exercise the real Risk/Executor and
durable caller; no live approval or statistical qualification is claimed.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import time

import numpy as np
import pytest
from ccxt import RequestTimeout

from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_venue import prepared
from tests.test_coordinated_two_instruments import transport,snapshot
from tests.entry_authority_fixtures import bind
from tests.test_strategy_hedge_stops import observed
from trader.core.journal import Journal
from trader.engine import coordinated_venue as V
from trader.engine.coordinated_live import LiveBooks
from trader.engine.coordinated_paper import CoordinatedPaper
from trader.research.compose import hedge as H,managed_hedge as M
from trader.research.closure import ClosureRefused


def policy():
    return dict(version=M.PAIR_VERSION,entry='JOINT',pnl_basis='COMBINED_NET_EQUITY',
        stop_loss_pct=15.,take_profit_pct=20.,hedge_stop=dict(
            stop={'kind':'pct','v':.1},trail={'kind':'none'},on_hit='close_group'))


def spec():
    return H.make_spec(purpose='relative_value',account_mode='netting',
        primary={'symbol':'BTC/USDT'},hedge={'symbol':'ETH/USDT'},
        ratio={'method':'fixed','value':.5},management=policy())


def test_pair_requests_both_legs_and_owns_each_stop_after_restart():
    b=H.HedgeBook(spec())
    orders=b.step(0,observed(0,p=100.,signal=1))
    assert [(i.leg,i.reason) for i in orders]==[('primary','signal'),('hedge','joint_entry')]
    assert b.hedge_exit_state and b.management_entry_equity==970.
    b.apply_fills({i.oid:i.qty for i in orders});b=H.HedgeBook(**asdict(b))
    orders=b.step(1,observed(1,p=100.,h=56.))
    assert {i.leg for i in orders}=={'primary','hedge'}
    assert {i.reason for i in orders}=={'strategy_hedge_stop'}


@pytest.mark.parametrize('change', ['activation','entry','one_leg_stop','purpose'])
def test_pair_cannot_be_reinterpreted_as_a_protective_hedge(change):
    hs=deepcopy(spec())
    if change=='activation':hs['management']['activate']={'kind':'combined_loss_pct','value':2.}
    elif change=='entry':hs['management']['entry']='PRIMARY_FIRST'
    elif change=='one_leg_stop':hs['management']['hedge_stop']['on_hit']='close_hedge'
    else:hs['purpose']='protective_beta'
    with pytest.raises(ClosureRefused):H.validate(hs)


def test_missing_second_leg_stop_input_prevents_both_pair_entries():
    b=H.HedgeBook(spec());bar=observed(0,p=100.,signal=1)
    bar['primary_exit_observation'].pop('hedge')
    assert b.step(0,bar)==[] and not b.pending


def test_pair_cannot_open_opposite_sides_of_the_same_instrument():
    hs=spec();hs['account_mode']='hedge';hs['hedge']['symbol']=hs['primary']['symbol']
    with pytest.raises(ClosureRefused,match='pair_distinct_instruments_required'):H.validate(hs)


@pytest.mark.parametrize('price,signal_exit',[(94.,False),(111.,False),(100.,True)])
def test_main_strategy_stop_target_and_signal_close_both_pair_legs(price,signal_exit):
    from trader.strategy.spec import ExitSpec
    b=H.HedgeBook(spec(),primary_exit_definition=asdict(ExitSpec(
        stop={'kind':'pct','v':.05},target={'kind':'pct','v':.1},time={'max_bars':100})))
    orders=b.step(0,observed(0,p=100.,signal=1))
    b.apply_fills({i.oid:i.qty for i in orders})
    bar=observed(1,p=price);bar['exit']=signal_exit
    orders=b.step(1,bar)
    assert len(orders)==2 and {i.leg for i in orders}=={'primary','hedge'}
    assert all(i.kind=='UNWIND' for i in orders)


@pytest.mark.parametrize('primary_qty,expected',[(0.,0.),(.5,.5),(2.,2.)])
def test_second_leg_size_follows_actual_primary_not_intended_quantity(primary_qty,expected):
    result=H.pair_increase_quantity(spec(),{'primary':primary_qty,'hedge':0.},
        {'primary':100.,'hedge':50.},'hedge',2.,.5)
    assert result==pytest.approx(expected)


@pytest.mark.parametrize('lost_response',[False,True])
@pytest.mark.parametrize('primary_fraction',[1.,.25])
def test_durable_pair_dispatch_owns_both_instruments_and_never_duplicates(setup,monkeypatch,lost_response,primary_fraction):
    ex,j,e,b,ins,identity,ref=prepared(setup,monkeypatch,purpose='relative_value',management=policy())
    bind(j,ex,e.cfg,'ETH/USDT');transport(ex,monkeypatch)
    send=ex.create_order
    def partial(symbol,kind,side,amount,params=None):
        if symbol=='BTC/USDT' and not (params or {}).get('reduceOnly'):
            amount*=primary_fraction
        return send(symbol,kind,side,amount,params)
    monkeypatch.setattr(ex,'create_order',partial)
    live=LiveBooks(e);live.register(b,identity,prepared_ms=identity['coordinated_instruction_observed_ms'])
    first=live.drain('g',references={'primary':ref},atrs={'primary':2.})
    assert first['filled_quantity']==2.*primary_fraction
    state=LiveBooks(e).load('g');assert len(state['book']['pending'])==1
    snapshot(ex,j)
    cap=json.loads(V.A.proposal_binding(j,'ETH/USDT'))
    second=dict(ref,price=50.,instrument_id=cap['instrument_id'],observed_ms=int(time.time()*1000))
    if lost_response:ex.entry_error=RequestTimeout('synthetic accepted reply lost')
    result=LiveBooks(e).drain('g',references={'hedge':second},atrs={'hedge':1.})
    count=len(ex.sent)
    entries=lambda:len([s for s in ex.sent if s[1]=='market' and not s[4].get('reduceOnly')])
    before_entries=entries()
    if lost_response:
        assert result['filled_quantity'] is None and e.recovery_pending()
        ex.entry_error=None;e.recover_entries();e.recover_entries()
        result=LiveBooks(e).drain('g',references={},atrs={})
        assert entries()==before_entries
        count=len(ex.sent)  # recovery also installs the missing venue stop
    assert result['filled_quantity']==2.*primary_fraction
    state=LiveBooks(e).load('g')
    assert state['book']['pos']=={'primary':2.*primary_fraction,'hedge':-2.*primary_fraction} and not state['book']['pending']
    assert len(j.open_trades())==2 and len(ex.stops)==2
    assert LiveBooks(e).drain('g',references={},atrs={})['status']=='IDLE'
    assert len(ex.sent)==count


def test_replay_and_durable_paper_pair_have_identical_combined_economics(tmp_path):
    n=8;p=np.full(n,100.);h=np.array([50.,50.,49.,49.,48.,48.,48.,48.])
    signal=np.r_[1,np.zeros(n-1,int)];exits=np.zeros(n,bool)
    bars=[observed(i,p=float(p[i]),h=float(h[i]))['primary_exit_observation'] for i in range(n)]
    funding=(np.full(n,.0001),np.full(n,-.0001));hs=spec()
    replay=H.replay(hs,p,h,signal,exits,opens=(p,h),equity=970.,
        ratios=np.full(n,.5),correlations=np.ones(n),funding=funding,decision_bars=bars)
    paper=CoordinatedPaper(Journal(tmp_path/'pair.db'))
    k=paper.start(spec=hs,equity=970.,identity=dict(exec_mode='paper',version_id='v',spec_hash='s',install_id='i',admission_id='a'))
    events=[]
    for i in range(n):
        events.append(paper.advance(k,i,dict(p=float(p[i]),h=float(h[i]),po=float(p[i]),ho=float(h[i]),
            signal=int(signal[i]),exit=False,ratio=.5,corr=1.,primary_exit_observation=bars[i],
            funding={'primary':funding[0][i],'hedge':funding[1][i]}),allow_entry=True))
    assert np.allclose(replay['equity'],[v['equity'] for v in events])
    assert np.allclose(replay['gross'],[v['gross_notional'] for v in events])
    assert all(v['real_order_submissions']==0 for v in events)
