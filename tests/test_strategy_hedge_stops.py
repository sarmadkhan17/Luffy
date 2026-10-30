"""A hedge's own frozen stop is a strategy choice, never a global threshold."""
from dataclasses import asdict
import pytest
from trader.research.compose import hedge as H,managed_hedge as M
from trader.research.closure import ClosureRefused
from tests.test_managed_hedge import policy,bar
from tests.test_compose_hedge import spec


def current(*,action='close_group',stop=None,trail=None):
    return dict(policy(),version=M.CURRENT_VERSION,hedge_stop=dict(
        stop=stop or {'kind':'pct','v':.10},trail=trail or {'kind':'none'},on_hit=action))


def observed(t,*,h=50.,signal=0,p=97.,equity=970.):
    return dict(bar(p=p,h=h,equity=equity,signal=signal),primary_exit_observation=dict(
        bar_ms=t*1000,step_ms=1000,open=p,high=p,low=p,close=p,atr=1.,
        hedge=dict(bar_ms=t*1000,step_ms=1000,open=h,high=h,low=h,close=h,atr=1.)))


def started(action='close_group'):
    book=H.HedgeBook(spec(management=current(action=action),ratio=dict(method='fixed',value=.5)))
    orders=book.step(0,observed(0,signal=1,p=100.,equity=1000.));book.apply_fills({i.oid:i.qty for i in orders})
    orders=book.step(1,observed(1));assert len(orders)==1 and orders[0].leg=='hedge'
    book.apply_fills({i.oid:i.qty for i in orders})
    return book


@pytest.mark.parametrize('action,legs',[('close_group',{'primary','hedge'}),('close_hedge',{'hedge'})])
def test_strategy_declared_hedge_stop_and_action_survive_restart(action,legs):
    book=H.HedgeBook(**asdict(started(action)))
    assert not book.step(2,observed(2,h=54.))
    orders=book.step(3,observed(3,h=56.))
    assert {i.leg for i in orders}==legs
    assert {i.reason for i in orders}=={'strategy_hedge_stop'}
    from trader.strategy import exit_policy as E
    p,state=E.decode(book.hedge_exit_state)
    assert p.side=='short' and p.stop==55. and state.reason=='stop'
    book.apply_fills({i.oid:i.qty*.5 for i in orders})
    retry=book.step(4,observed(4,h=56.))
    assert {i.leg for i in retry}==legs
    assert all(i.side*i.qty>0 for i in retry if i.leg=='hedge')  # reduce the short


def test_distinct_strategy_stops_produce_distinct_hedge_actions():
    tight=started('close_hedge');wide=started('close_hedge')
    # Different frozen book, initialized from its own declared stop before entry.
    wide=H.HedgeBook(spec(management=current(action='close_hedge',stop={'kind':'pct','v':.20}),
        ratio=dict(method='fixed',value=.5)))
    for t in range(2):
        orders=wide.step(t,observed(t,signal=1 if t==0 else 0,p=100. if t==0 else 97.,equity=1000. if t==0 else 970.))
        wide.apply_fills({i.oid:i.qty for i in orders})
    assert tight.step(2,observed(2,h=56.))
    assert wide.step(2,observed(2,h=56.))==[]


@pytest.mark.parametrize('change',['missing','stop_kind','stop_number','action','extra'])
def test_incomplete_strategy_hedge_stop_is_not_a_silent_default(change):
    p=current()
    if change=='missing':p.pop('hedge_stop')
    elif change=='stop_kind':p['hedge_stop']['stop']={'kind':'unknown','v':.1}
    elif change=='stop_number':p['hedge_stop']['stop']={'kind':'pct','v':1.}
    elif change=='action':p['hedge_stop']['on_hit']='guess'
    else:p['hedge_stop']['unbound_extra']=True
    with pytest.raises(ClosureRefused):M.validate(p)


def test_atr_hedge_stop_uses_hedge_volatility_not_primary_volatility():
    book=H.HedgeBook(spec(management=current(stop={'kind':'atr','mult':3.}),ratio=dict(method='fixed',value=.5)))
    for t in range(2):
        value=observed(t,signal=1 if t==0 else 0,p=100. if t==0 else 97.,equity=1000. if t==0 else 970.)
        value['primary_exit_observation']['atr']=20.
        value['primary_exit_observation']['hedge']['atr']=2.
        orders=book.step(t,value);book.apply_fills({i.oid:i.qty for i in orders})
    from trader.strategy import exit_policy as E
    assert E.decode(book.hedge_exit_state)[0].stop==56.


def packet_with_stop(packet,action):
    from copy import deepcopy
    from trader.research.compose import graph
    from trader.research.closure import coordinated_trial as T,digest
    packet=deepcopy(packet);old=packet['candidate']['hedge']
    hs=H.make_spec(**dict({k:v for k,v in old.items() if k!='spec_id'},management=current(action=action)))
    candidate=graph.make_candidate(dict({k:v for k,v in packet['candidate'].items() if k not in ('candidate_id','graph_id')},hedge=hs))
    result=packet['result'];combo=graph.realize(candidate,packet['envelope']['envelope_id'],entries_only=True)
    result.update(candidate_id=candidate['candidate_id'],combo=combo.as_dict(),combo_hash=combo.hash)
    per=next(iter(result['hedge']['per_symbol'].values()))
    per['effective_spec']=hs
    per['capital_policy_id']=H.K.POLICY_ID  # explicit synthetic numerical fixture, not requalified history
    result['result_id']=digest({k:v for k,v in result.items() if k!='result_id'})
    return T.make(candidate,packet['envelope'],result)


def test_hedge_stress_prices_also_change_native_stop_inputs_without_rewriting_source(monkeypatch):
    from copy import deepcopy
    import numpy as np
    n=64;p=np.r_[100.,np.full(n-1,97.)];h=np.linspace(50.,60.,n)
    bars=[observed(i,h=float(h[i]))['primary_exit_observation'] for i in range(n)]
    original=deepcopy(bars);seen=[];run=H.replay
    def capture(*args,**kwargs):
        seen.append((np.array(args[2]),deepcopy(kwargs['decision_bars'])))
        return run(*args,**kwargs)
    monkeypatch.setattr(H,'replay',capture)
    H.stress_cases(spec(management=current()),p,h,np.r_[1,np.zeros(n-1,int)],np.zeros(n,bool),
        dict(opens=(p,h),decision_bars=bars))
    shocked,inputs=seen[0]
    assert np.allclose([b['hedge']['close'] for b in inputs],shocked)
    assert inputs[0]['hedge']==original[0]['hedge']
    assert inputs[-1]['hedge']['atr']!=original[-1]['hedge']['atr']
    assert bars==original
