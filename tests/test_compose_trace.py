"""Engine position trace and the marked-equity diagnostic: opt-in trace leaves the engine's results untouched,
the marks use the engine's actual side/entry/quantity, open-at-end positions are included and a missing price is UNKNOWN."""
import dataclasses

import numpy as np
import pytest

from tests import compose_fixtures as C
from trader.research import job
from trader.research.closure import development as D, manifest
from trader.research.compose import diagnostics as dg, graph


@pytest.fixture(scope='module')
def env(tmp_path_factory):
    b = C.build_ctx(tmp_path_factory.mktemp('tr'), mode='null', n_bars=1500)
    frames, _ = manifest.development_frames(b.snap, b.env)
    spec = dict(question='q', rationale='r', expected='e', failure_conditions='f', structure='long_short', tf='4h', geo='fixed',
                long={'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 60}}, short={'cmp': 'lt', 'a': {'f': 'rsi14'}, 'b': {'lit': 40}})
    cand = graph.make_candidate(spec)
    bundle = D.build_bundle(frames, b.env, {})
    return b, bundle, graph.realize(cand, 'x')


def test_trace_is_opt_in_and_changes_no_engine_result(env):
    b, bundle, c = env
    plain = D.run_symbols(c, bundle)
    traced = D.run_symbols(c, bundle, traces=True)
    for s in plain:
        assert dataclasses.asdict(plain[s]['result']) == dataclasses.asdict(traced[s]['result'])
        assert plain[s]['fills'] == traced[s]['fills']
        closed = [t for t in traced[s]['trace'] if not t['open_at_end']]
        assert len(closed) == len(plain[s]['fills'])
        for t, f in zip(closed, plain[s]['fills']):
            assert t['side'] in ('long', 'short') and t['entry_i'] == f.entry_i and t['exit_i'] == f.exit_i
            assert t['pnl_usdt'] / t['stake'] == pytest.approx(f.r_multiple)       # trace reconciles to the fill's own R


def test_mtm_uses_engine_side_includes_open_end_and_reconciles(env):
    b, bundle, c = env
    sims = D.run_symbols(c, bundle, traces=True)
    fills = dg.common_fills({s: r['fills'] for s, r in sims.items()}, bundle.frames)     # shared UTC clock
    traces = [t for r in sims.values() for t in r['trace']]
    assert any(t['open_at_end'] for t in traces)                                   # a censored position exists here
    rep = dg.mtm_report(traces, bundle.frames, fills, bundle.equity, bundle.risk_pct, bundle.max_open)
    assert rep['reconciled'] and rep['open_at_end_positions'] >= 1 and rep['unknown_mark_bars'] == 0
    assert rep['mtm_max_dd_pct'] >= rep['closed_max_dd_pct'] - 1e-9 or rep['mtm_max_dd_pct'] >= 0
    # a short and a long with identical prices must mark with OPPOSITE sign (side comes from the engine, not the outcome)
    frames = {'S': bundle.frames[next(iter(bundle.frames))]}
    px = frames['S']['close'].to_numpy(float)
    base = dict(symbol='S', entry_i=300, fill_i=300, exit_i=330, entry_px=float(px[300]), sl_dist=1.0, amount=1.0, stake=10.0,
                fee_rate=0.0, slip_frac=0.0, atr=1.0, open_at_end=False)
    clk, t0 = dg.clocks(frames)
    lg = dg._positions([dict(base, side='long', pnl_usdt=1.0)], frames, clk, t0)[0]['path']
    sh = dg._positions([dict(base, side='short', pnl_usdt=1.0)], frames, clk, t0)[0]['path']
    assert np.allclose(lg, -sh)


def test_missing_price_is_unknown_not_zero(env):
    b, bundle, c = env
    sym = next(iter(bundle.frames))
    df = bundle.frames[sym].copy()
    px = df['close'].to_numpy(float).copy()
    px[310] = np.nan
    df['close'] = px
    t = dict(symbol=sym, entry_i=300, fill_i=300, exit_i=330, side='long', entry_px=float(px[300]), sl_dist=1.0, amount=1.0,
             stake=10.0, fee_rate=0.0, slip_frac=0.0, atr=1.0, open_at_end=False, pnl_usdt=2.0)
    from trader.strategy.portfolio_evidence import Fill
    rep = dg.mtm_report([t], {sym: df}, dg.common_fills({sym: [Fill(300, 330, 0.2, sym)]}, {sym: df}), 1000.0, 1.0, 4)
    assert rep['unknown_mark_bars'] == 1 and rep['reconciled']


def test_nan_indicators_make_regime_state_unknown_and_claims_are_not_granted(env):
    b, bundle, c = env
    ctx = {x.symbol: x for x in job._ctxs(bundle)}
    sym = next(iter(ctx))
    st = dg._state_series(ctx[sym])
    assert (st[:20] == 'unknown').all()                                           # warmup is unknown, not 'mixed/normalvol'
    sims = D.run_symbols(c, bundle)
    rep = dg.regime_report({s: r['fills'] for s, r in sims.items()}, ctx)
    assert all(v['conditional_claim'] == 'NOT_ESTABLISHED' for v in rep['states'].values())
    assert rep['unseen_state_policy'].startswith('NOT_ENFORCED')


# ── actual fill price in the trace (gapped next-open, delay, both directions) ─────────────────────────────────
def _gapped_frame(n=420, seed=3):
    import pandas as pd
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.01, n))          # gaps: open far from the previous close
    hi = np.maximum(open_, close) * 1.004
    lo = np.minimum(open_, close) * 0.996
    ts = pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC')
    return pd.DataFrame(dict(ts=ts, open=open_, high=hi, low=lo, close=close, volume=np.full(n, 10.0)))


@pytest.mark.parametrize('side,delay', [('long', 0), ('short', 0), ('long', 2), ('short', 2)])
def test_trace_entry_is_the_engines_actual_slipped_fill_not_the_signal_close(side, delay):
    from trader.strategy import vector_backtest as V
    from trader.strategy.spec import ExitSpec
    df = _gapped_frame()
    n = len(df)
    sig = np.zeros(n, bool)
    sig[300] = True
    zero = np.zeros(n, bool)
    exit_spec = ExitSpec(stop=dict(kind='atr', mult=2.0), target=dict(kind='rr', v=2.0), trail=dict(kind='none'), time=dict(max_bars=20),
                         signal_exit='')
    risk = dict(risk_per_trade_pct=1.0, taker_fee_pct=0.05, slippage_atr_frac=0.1, funding_rate_8h=0.0001, bar_minutes=240)
    kw = dict(long=sig if side == 'long' else zero, short=sig if side == 'short' else zero)
    trace, fills = [], []
    plain = V.simulate(kw['long'], kw['short'], df, exit_spec, risk, symbol='G', fills_out=[], fill_delay_bars=delay)
    res = V.simulate(kw['long'], kw['short'], df, exit_spec, risk, symbol='G', fills_out=fills, trace_out=trace, fill_delay_bars=delay)
    assert dataclasses.asdict(plain) == dataclasses.asdict(res)                        # observation only: result unchanged
    [t] = trace
    sign = 1.0 if side == 'long' else -1.0
    atr = V.atr_series(df, 14).to_numpy(float)
    f = t['fill_i']
    assert f == 301 + delay
    expected = float(df['open'].iloc[f]) + sign * atr[300] * 0.1                      # _trade: OPEN of the fill bar +/- slippage
    assert t['entry_px'] == pytest.approx(expected) and t['entry_open_px'] == pytest.approx(float(df['open'].iloc[f]))
    assert t['signal_close_px'] == pytest.approx(float(df['close'].iloc[300]))
    assert abs(t['entry_px'] - t['signal_close_px']) > 1e-6                            # the gap is real; the old value was wrong
    # trace reconstructs the engine's own P&L: (exit-entry)*sign - fees - carry, per unit, times amount
    gross = (t['exit_px'] - t['entry_px']) * sign
    assert (gross - t['fees_per_unit'] - t['carry_per_unit']) * t['amount'] == pytest.approx(t['pnl_usdt'])
    assert t['funding_basis'] in ('flat_estimate', 'actual_signed', 'mixed_actual_and_flat')


def test_marks_charge_entry_slippage_once_and_open_marks_start_from_the_actual_fill():
    df = _gapped_frame()
    px = df['close'].to_numpy(float)
    base = dict(symbol='S', entry_i=300, fill_i=301, exit_i=310, side='long', entry_px=float(df['open'].iloc[301]) + 0.5,
                sl_dist=1.0, amount=2.0, stake=100.0, fee_rate=0.0005, slip_frac=0.1, atr=5.0, open_at_end=False, pnl_usdt=1.0)
    clk, t0 = dg.clocks({'S': df})
    path = dg._positions([base], {'S': df}, clk, t0)[0]['path']
    expect = ((px[301] - base['entry_px']) * 2.0 - 2.0 * base['entry_px'] * 0.0005) / 100.0
    assert path[0] == pytest.approx(expect)                                              # no second slippage term


def test_unequal_starts_and_holes_share_one_utc_clock_and_reconcile_with_the_portfolio():
    import pandas as pd
    from trader.strategy.portfolio_evidence import Fill, portfolio_curve
    a = _gapped_frame(400, 1)
    b = _gapped_frame(400, 2).iloc[37:].drop(index=[150, 151]).reset_index(drop=True)         # later start AND missing slots
    b['ts'] = pd.to_datetime(b['ts'], utc=True)
    frames = {'A': a, 'B': b}
    clk, t0 = dg.clocks(frames)
    # the same LOCAL index is a different UTC time in B (later start plus the hole)
    assert clk['A'][200] != clk['B'][200]
    fa = Fill(entry_i=100, exit_i=110, r_multiple=0.5, symbol='A')
    fb = Fill(entry_i=160, exit_i=170, r_multiple=-0.4, symbol='B')
    common = dg.common_fills({'A': [fa], 'B': [fb]}, frames)
    by_time = {f.symbol: (f.entry_i, f.exit_i) for f in common}
    assert by_time['A'] == (int(clk['A'][100] - t0), int(clk['A'][110] - t0))
    assert by_time['B'] == (int(clk['B'][160] - t0), int(clk['B'][170] - t0))
    assert by_time['B'][0] > by_time['A'][1]                        # real time order differs from coincident local indices
    traces = [dict(symbol='A', entry_i=100, fill_i=101, exit_i=110, side='long', entry_px=float(a['open'].iloc[101]), sl_dist=1.0,
                   amount=1.0, stake=100.0, fee_rate=0.0, slip_frac=0.0, atr=1.0, open_at_end=False, pnl_usdt=50.0),
              dict(symbol='B', entry_i=160, fill_i=161, exit_i=170, side='short', entry_px=float(b['open'].iloc[161]), sl_dist=1.0,
                   amount=1.0, stake=100.0, fee_rate=0.0, slip_frac=0.0, atr=1.0, open_at_end=False, pnl_usdt=-40.0)]
    rep = dg.mtm_report(traces, frames, common, 10_000.0, 1.0, 8)
    closed = portfolio_curve(common, 10_000.0, 1.0, 8)
    assert rep['reconciled'] and rep['taken'] == closed['taken'] == 2 and rep['closed_max_dd_pct'] == pytest.approx(round(closed['max_dd_pct'], 3))
    assert 'UTC' in rep['clock']


def test_a_hole_in_a_held_symbol_is_an_unknown_mark_never_flat_and_same_time_exits_settle_as_one_batch():
    """Parent probe: A bars at UTC [0,8h,12h], B at [0,4h,8h,12h]; A held 0 -> 12h. The 4h slot has no A bar while A is held."""
    import pandas as pd
    from trader.strategy.portfolio_evidence import Fill, portfolio_curve

    def frame(hours):
        n = len(hours)
        ts = pd.to_datetime([pd.Timestamp('2024-01-01', tz='UTC') + pd.Timedelta(hours=h) for h in hours])
        return pd.DataFrame(dict(ts=ts, open=np.full(n, 100.0), high=np.full(n, 101.0), low=np.full(n, 99.0),
                                 close=np.linspace(100, 103, n), volume=np.ones(n)))
    frames = {'A': frame([0, 8, 12]), 'B': frame([0, 4, 8, 12])}
    tr = dict(symbol='A', entry_i=0, fill_i=0, exit_i=2, side='long', entry_px=100.0, sl_dist=1.0, amount=1.0, stake=100.0,
              fee_rate=0.0, slip_frac=0.0, atr=1.0, open_at_end=False, pnl_usdt=1.0)
    common = dg.common_fills({'A': [Fill(0, 2, 0.01, 'A')]}, frames)
    rep = dg.mtm_report([tr], frames, common, 10_000.0, 1.0, 4)
    assert rep['unknown_mark_bars'] >= 1 and rep['mean_open'] == pytest.approx(0.75)        # held on 0,4h,8h of the 4 grid slots
    assert rep['mtm_max_dd_pct'] is None or np.isfinite(rep['mtm_max_dd_pct'])
    # two exits at the SAME timestamp settle together with the equity floor applied once
    fills = [Fill(0, 10, -1.0, 'A'), Fill(0, 10, -1.0, 'B'), Fill(20, 30, 0.5, 'A')]
    expect = portfolio_curve(fills, 1000.0, 60.0, 8)
    frames3 = {'A': frame(list(range(0, 40))), 'B': frame(list(range(0, 40)))}
    trs = [dict(tr, symbol='A', entry_i=0, fill_i=0, exit_i=10, pnl_usdt=-60.0 * 1.0),
           dict(tr, symbol='B', entry_i=0, fill_i=0, exit_i=10, pnl_usdt=-60.0),
           dict(tr, symbol='A', entry_i=20, fill_i=20, exit_i=30, pnl_usdt=0.0)]
    rep3 = dg.mtm_report(trs, frames3, dg.common_fills({'A': [fills[0], fills[2]], 'B': [fills[1]]}, frames3), 1000.0, 60.0, 8)
    assert rep3['taken'] == expect['taken'] and rep3['closed_max_dd_pct'] == pytest.approx(round(expect['max_dd_pct'], 3))

@pytest.mark.parametrize('side', ['long', 'short'])
def test_open_marks_include_the_same_signed_funding_accrual_as_the_engine(side):
    from trader.strategy import vector_backtest as V
    from trader.strategy.spec import ExitSpec
    df = _gapped_frame()
    df[['open', 'close']] = 100.
    df['high'], df['low'] = 100.1, 99.9
    signal = np.zeros(len(df), bool)
    signal[220] = True
    trace = []
    risk = dict(risk_per_trade_pct=.5, taker_fee_pct=0., slippage_atr_frac=0., funding_rate_8h=0., bar_minutes=240)
    exits = ExitSpec(stop=dict(kind='pct', v=.2), target=dict(kind='none'), time=dict(max_bars=20))
    V.simulate(signal if side == 'long' else np.zeros(len(df), bool),
               signal if side == 'short' else np.zeros(len(df), bool), df, exits, risk,
               symbol='S', funding=np.full(len(df), .001), trace_out=trace)
    assert len(trace) == 1
    clk, origin = dg.clocks({'S': df})
    path = dg._positions(trace, {'S': df}, clk, origin)[0]['path']
    sign = 1 if side == 'long' else -1
    assert path[0] == pytest.approx(-sign * .001 * .5 * 100 * trace[0]['amount'] / trace[0]['stake'])
    assert abs(path[-1]) > abs(path[0])
