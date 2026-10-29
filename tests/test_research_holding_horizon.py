"""The declared research holding horizon reaches the shared live/replay exit."""
from copy import deepcopy

import pytest

from tests.test_compose_flow import spec
from trader.research.closure import ClosureRefused
from trader.research.compose import graph, search
from trader.strategy import exit_policy as E
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec


def candidate():
    return graph.make_candidate(spec(
        {'cmp':'gt','a':{'f':'ret24'},'b':{'lit':0}}, None, structure='long_only',
        exit=dict(stop=dict(kind='pct',v=.1),target=dict(kind='pct',v=1.),
                  trail=dict(kind='none'),time=dict(max_bars=500))))


def test_search_revision_above_legacy_limit_compiles_and_exits_at_exact_bar():
    revised=search.apply_change(candidate(),dict(kind='holding_period',to=1000))
    spec_=graph.realize(revised).to_spec()
    assert not StrategySpec.validate(spec_)
    compiled=compile_spec(spec_)
    policy,state=E.initialize(compiled.spec.exit,100.,1.,'long',0,60_000)
    for i in range(1,1001):
        transition=E.advance(policy,state,E.Observation(i*60_000,100.,100.,100.,100.),1.)
        assert transition.due is (i==1000)
        state=transition.state
    assert transition.reason=='time' and state.bars_held==1000
    assert E.decode(E.encode(policy,state))==(policy,state)


def test_declared_capability_bound_is_shared_and_not_infinite():
    c=candidate();decl=deepcopy(c['exit'])
    assert graph.MAX_HOLD_BARS==E.MAX_HOLD_BARS==5000
    for bars in (501,1000,5000):
        decl['time']['max_bars']=bars
        made=graph.make_candidate(spec(c['long'],None,structure='long_only',exit=deepcopy(decl)))
        assert compile_spec(graph.realize(made).to_spec()).spec.exit.time['max_bars']==bars
    decl['time']['max_bars']=5001
    with pytest.raises(ClosureRefused,match='BAD_EXIT'):
        graph.make_candidate(spec(c['long'],None,structure='long_only',exit=decl))
