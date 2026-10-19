"""D7 synthetic contract probes; no market history, venue or qualification claims."""
import numpy as np
import pandas as pd
import pytest

from trader.engine.exits import SpecExit
from trader.strategy import exit_policy as E
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import simulate, trade_table, walk_table
from tests.test_compile import _spec

STEP = 900_000


def frame(n=240):
    return pd.DataFrame(dict(ts=pd.date_range('2026-01-01', periods=n, freq='15min', tz='UTC'),
        open=100., high=101., low=99., close=100., volume=1000.))


@pytest.mark.parametrize('target', [{'kind': 'none'}, {'kind': 'rr', 'v': 2.},
    {'kind': 'atr', 'mult': 3.}, {'kind': 'pct', 'v': .04}])
@pytest.mark.parametrize('stop', [{'kind': 'atr', 'mult': 2.}, {'kind': 'pct', 'v': .04}])
@pytest.mark.parametrize('side', ['long', 'short'])
def test_fixed_policy_trade_table_simulation_and_costs_agree(stop, target, side):
    ex = ExitSpec(stop=stop, target=target, time={'max_bars': 4})
    spec = E.bind_research(_spec(exit=ex))
    df = frame()
    i = 210
    entry_ms = int(df.ts.iloc[i].timestamp()*1000)
    policy, state = E.initialize(spec.exit, 100., 2., side, entry_ms, STEP)
    for j in range(i+1, i+5):
        result = E.advance(policy, state, E.Observation(entry_ms+(j-i)*STEP,100,101,99,100),1.)
        state = result.state
    assert result.reason == 'time' and state.reference_exit == 100.
    assert policy.stop == (96 if side == 'long' else 104)
    assert bool(policy.target) == (target['kind'] != 'none')
    risk = dict(risk_per_trade_pct=1., taker_fee_pct=.05, slippage_atr_frac=.06,
                funding_rate_8h=.0001, bar_minutes=15)
    lo, sh = np.zeros(len(df),bool), np.zeros(len(df),bool)
    (lo if side == 'long' else sh)[i] = True
    funding = np.full(len(df), .0002)
    fills, evidence = [], []
    res = simulate(lo,sh,df,spec.exit,risk,funding=funding,fills_out=fills,exit_evidence_out=evidence)
    table = trade_table(df,spec.exit,risk,funding=funding)
    walked = walk_table(table,lo,sh,risk)
    assert res.trades == len(walked) == len(fills) == 1
    assert walked[0][:2] == (i,i+4)
    assert walked[0][2] == pytest.approx(fills[0].r_multiple)
    assert res.pnl_usdt < 0  # identical close still pays both fees/spreads
    assert evidence[0]['state'] == __import__('json').loads(E.encode(policy,state))


@pytest.mark.parametrize('side', ['long','short'])
def test_gap_collision_restart_and_terminal_remainder(side):
    ex = ExitSpec(stop={'kind':'pct','v':.02}, target={'kind':'pct','v':.04})
    p,s = E.initialize(ex,100,1,side,0,STEP)
    collision = E.advance(p,s,E.Observation(STEP,100,105,95,100),3)
    assert collision.reason == 'stop' and collision.state.ambiguous
    assert collision.state.reference_exit == p.stop
    p2,s2 = E.decode(E.encode(p,collision.state))
    retry = E.advance(p2,s2,E.Observation(STEP,0,0,0,0),1.25)
    assert retry.state == collision.state and retry.quantity == 1.25
    opening = 106 if side == 'long' else 96
    gap = E.advance(p,s,E.Observation(STEP,opening,107,95,100),3)
    assert gap.reason == 'target' and gap.state.gap and not gap.state.ambiguous
    assert gap.state.reference_exit == opening


@pytest.mark.parametrize('obs', [E.Observation(2*STEP,100,101,99,100),
    E.Observation(STEP,100,101,99,100,completed=False),
    E.Observation(STEP,100,np.nan,99,100), E.Observation(STEP,100,99,101,100)])
def test_missing_partial_invalid_observations_refuse_without_transition(obs):
    p,s = E.initialize(ExitSpec(),100,1,'long',0,STEP)
    with pytest.raises(ValueError):
        E.advance(p,s,obs,1)
    assert s == E.State(0)
    assert not E.advance(p,s,E.Observation(0,0,0,0,0),1).due


@pytest.mark.parametrize('ex', [ExitSpec(), ExitSpec(target={'kind':'none'}),
    ExitSpec(trail={'kind':'atr','mult':4.,'arm_at_r':1.}),
    ExitSpec(signal_exit='close < ema(5)'), ExitSpec(stop={'kind':'swing','lookback':20}),
    ExitSpec(stop={'kind':'atr','mult':2.,'extra':1}), ExitSpec(time={'max_bars':2.5})])
def test_runtime_preflight_is_exactly_the_initializer_contract(ex):
    spec = _spec(exit=ex)
    assert SpecExit.frozen_unsupported(spec) == E.unsupported(ex)
    if E.unsupported(ex):
        with pytest.raises(ValueError,match='unsupported_versioned_exit'):
            E.initialize(ex,100,1,'long',0,STEP)


@pytest.mark.xfail(strict=True, raises=TypeError, reason="D6 shared-file boundary pending: nullable boolean bars_since warmup")
def test_multiple_conditions_reversal_breakout_and_window_are_literal():
    df = frame(60)
    df.loc[35,'close'] = 103.
    df.loc[35,'high'] = 104.
    df.loc[36:,'close'] = 100.5
    compiled = compile_spec(_spec(direction='both', filters=['volume > 0'],
        entry_long='bars_since(close > donchian_hi(20)) <= 2 and close > 100',
        entry_short='prev(close, 1) > 101 and close < 101'))
    lo,sh = compiled.entries({'15m':df})
    assert np.flatnonzero(lo).tolist() == [35,37]  # 36 conflicts, so neither side fires
    assert not sh.any()
    assert not lo[38:].any()  # declared window expires after two bars
    lo,sh = compile_spec(_spec(direction='both', filters=[],entry_long='close > 200',
        entry_short='prev(close, 1) > 101 and close < 101')).entries({'15m':df})
    assert np.flatnonzero(sh).tolist() == [36]


def test_missing_indicator_remains_unknown_under_not():
    df = frame(20)
    lo,_ = compile_spec(_spec(filters=[],entry_long='not (rsi(100) < 30)')).entries({'15m':df})
    assert not lo.any()


@pytest.mark.parametrize('direction', ['long','short'])
@pytest.mark.xfail(strict=True, reason="D6 shared-file boundary pending: compiler ignores declared direction")
def test_populated_opposite_expression_never_enables_forbidden_direction(direction):
    df = frame(30)
    spec = _spec(direction=direction, filters=[],entry_long='close > 0',entry_short='close > 0')
    lo,sh = compile_spec(spec).entries({'15m':df})
    assert bool(lo.all()) == (direction == 'long')
    assert bool(sh.all()) == (direction == 'short')


@pytest.mark.xfail(strict=True, raises=TypeError, reason="D6 shared-file boundary pending: nullable boolean streak warmup")
def test_streak_preserves_unknown_warmup():
    df = frame(30)
    lo,_ = compile_spec(_spec(filters=[],entry_long='streak(close > donchian_hi(20)) > 0')).entries({'15m':df})
    assert not lo.any()


@pytest.mark.parametrize('side', ['long','short'])
@pytest.mark.parametrize('target', [{'kind':'none'}, {'kind':'rr','v':2.},
    {'kind':'atr','mult':3.}, {'kind':'pct','v':.04}])
def test_fixed_versioned_paper_replays_the_same_intent_after_restart(tmp_path, side, target):
    from trader.core.config import load_config
    from trader.core.types import Action, Decision
    from trader.engine.paper import PaperExecutor
    from trader.engine import paper_exit_evidence as X
    from trader.strategy import factory_handoff as F
    from tests.test_sol1_paper_cost import installed, bars, snapshot, H4
    from tests.test_sol1_strategy_lifecycle import _spec as version_spec
    from tests.authority_factory_fixtures import T0
    import json

    cfg = load_config()
    spec = version_spec(direction=side, entry_long='close > 0' if side=='long' else '',
        entry_short='close > 0' if side=='short' else '',
        exit=ExitSpec(stop={'kind':'pct','v':.04},target=target,time={'max_bars':3}))
    j,out,_ = installed(tmp_path,cfg,spec)
    version = F.load_version(j,out['version_id'])
    paper = PaperExecutor(j,cfg)
    paper.capture = None
    start = T0 // H4 * H4
    df = bars(220,start)
    snap = snapshot(df)
    frozen = __import__('trader.strategy.spec',fromlist=['StrategySpec']).StrategySpec.from_dict(version['spec'])
    signal = compile_spec(frozen).to_evaluator()(None,snap)
    assert signal is not None
    dec = Decision('d7','d7','BTC/USDT',signal.action,1.,.5,.8,[],[])
    dec.strategy_signals = [dict(strategy_id=spec.id,action=signal.action.value,params=signal.params)]
    tid = paper.enter(dec,snap,spec.id,reference_equity=10000.,state='ACTIVE')
    trade = j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    identity = json.loads(trade['entry_identity_json'])
    assert (identity['version_id'],identity['install_id'],identity['spec_sha256']) == (
        version['version_id'],trade['install_id'],version['spec_hash'])
    p,s = E.decode(trade['exit_state_json'])
    tail = [(110.,110.1,109.9,110.)]*3
    # One observed bar, then a fresh executor resumes the exact stored state.
    paper.manage(snapshot(bars(220,start,tail[:1])))
    paper = PaperExecutor(j,cfg)
    paper.capture = None
    assert paper.manage(snapshot(bars(220,start,tail))) == [tid]
    for k in range(1,4):
        s = E.advance(p,s,E.Observation(trade['entry_bar_ms']+k*H4,*tail[k-1]),trade['amount']).state
    closed = j.query('SELECT * FROM versioned_paper_trades WHERE id=?',(tid,))[0]
    assert closed['exit_state_json'] == E.encode(p,s)
    assert closed['close_reason'] == s.reason == 'time'
    assert closed['exit_price'] == s.reference_exit == 110.
    assert closed['stop_loss'] == p.stop and closed['take_profit'] == p.target
    assert X.verify(j,closed,version,F.verify_install(j,version,current=False))
    assert paper.manage(snapshot(bars(220,start,tail))) == []
    # A prior occurrence cannot be reused after the latest bar has changed.
    with pytest.raises(ValueError,match='paper_decision_signal_bar_differs'):
        paper.enter(dec,snapshot(bars(220,start,tail)),spec.id,reference_equity=10000.,state='ACTIVE')


@pytest.mark.xfail(strict=True, reason='D6 shared-file boundary pending: compile_version loses canonical exit route')
def test_exact_version_compile_keeps_qualified_exit_semantics(tmp_path):
    from trader.core.config import load_config
    from trader.strategy.compile import compile_version
    from tests.test_sol1_paper_cost import installed
    j,out,_ = installed(tmp_path,load_config())
    compiled = compile_version(j,out['version_id'])
    assert compiled.version_id == out['version_id']
    assert getattr(compiled.spec.exit,'_exit_semantics_id',None) == E.EXIT_SEMANTICS_ID


def test_original_donchian_geometry_is_preserved_and_refused_honestly():
    from trader.strategy.geometries import GEOS
    spec = _spec(name='Donchian Breakout Trail',timeframe='4h',direction='both',
        entry_long='close > donchian_hi(100)',entry_short='close < donchian_lo(100)',
        filters=[],exit=GEOS['trail'])
    original = spec.to_json()
    compiled = compile_spec(spec)
    assert compiled.spec.to_json() == original
    assert compiled.spec.exit.trail == {'kind':'atr','mult':4.,'arm_at_r':1.}
    assert compiled.spec.exit.stop == {'kind':'atr','mult':2.}
    assert compiled.spec.exit.target == {'kind':'none'}
    assert compiled.spec.exit.time == {'max_bars':500}
    assert E.unsupported(spec.exit) == 'trail=atr'
    with pytest.raises(ValueError,match='exit_semantics_unavailable'):
        compile_spec(spec,exit_semantics_id=E.EXIT_SEMANTICS_ID)
    assert spec.to_json() == original
