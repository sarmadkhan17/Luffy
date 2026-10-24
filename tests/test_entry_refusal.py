"""Synthetic probes: a canonical entry whose exit geometry is unexecutable is
refused alone (no fill, cost or exposure) and later valid entries still run,
identically in `simulate` and the `trade_table`/`walk_table` rotation path."""
import numpy as np
import pandas as pd
import pytest

from trader.strategy import exit_policy as E, vector_backtest as V
from trader.strategy.spec import ExitSpec
from tests.test_compile import _spec

RISK = dict(risk_per_trade_pct=1., taker_fee_pct=.05, slippage_atr_frac=.06,
            funding_rate_8h=.0001, bar_minutes=15)
WIDE, NARROW = 215, 245        # signal bars: ATR 50 (geometry unexecutable) / ATR 1 (valid)


def frame(n=260):
    """Reference price 100 throughout; true range 50 before bar 220, 1 after."""
    wide = np.arange(n) < 220
    return pd.DataFrame(dict(ts=pd.date_range('2026-01-01', periods=n, freq='15min', tz='UTC'),
        open=100., close=100., volume=1000.,
        high=np.where(wide, 125., 100.5), low=np.where(wide, 75., 99.5)))


def short_rr():       # short: stop 3 ATR above, target 3 x that below -> 100-450 at ATR 50
    return ExitSpec(stop={'kind': 'atr', 'mult': 3.}, target={'kind': 'rr', 'v': 3.}, time={'max_bars': 4})


def long_stop():      # long: stop 3 ATR below -> 100-150 at ATR 50
    return ExitSpec(stop={'kind': 'atr', 'mult': 3.}, target={'kind': 'none'}, time={'max_bars': 4})


def canonical(ex):
    return E.bind_research(_spec(exit=ex)).exit


def signals(df, side, bars):
    on, off = np.zeros(len(df), bool), np.zeros(len(df), bool)
    on[list(bars)] = True
    return (on, off) if side == 'long' else (off, on)


def run(df, ex, side, bars, **kw):
    lo, sh = signals(df, side, bars)
    fills, trace = [], []
    res = V.simulate(lo, sh, df, ex, RISK, fills_out=fills, trace_out=trace, **kw)
    walked = V.walk_table(V.trade_table(df, ex, RISK), lo, sh, RISK)
    return res, fills, trace, walked


@pytest.mark.parametrize('side,make', [('short', short_rr), ('long', long_stop)])
def test_unexecutable_geometry_is_refused_with_no_fill_cost_or_exposure(side, make):
    df, ex = frame(), canonical(make())
    with pytest.raises(ValueError, match='exit_geometry_nonpositive'):
        E.initialize(ex, 100., 50., side, 0, 900_000)       # the policy itself still refuses
    evidence = []
    res, fills, trace, walked = run(df, ex, side, [WIDE], exit_evidence_out=evidence)
    assert (res.trades, res.pnl_usdt, res.gross_win, res.gross_loss, res.max_dd_pct) == (0, 0.0, 0.0, 0.0, 0.0)
    assert res.censored_position_count == 0 and not res.exit_reasons
    assert not fills and not trace and not evidence and walked == []
    assert res.errors == [f'entry_refused:exit_geometry_nonpositive:{side}:{WIDE}']


@pytest.mark.parametrize('side,make', [('short', short_rr), ('long', long_stop)])
def test_later_valid_entry_is_still_evaluated_and_both_paths_agree(side, make):
    df, ex = frame(), canonical(make())
    res, fills, trace, walked = run(df, ex, side, [WIDE, NARROW])
    assert res.trades == len(fills) == len(walked) == 1
    assert [t['entry_i'] for t in trace] == [NARROW] and trace[0]['side'] == side
    assert walked[0][:2] == (NARROW, NARROW + 4)
    assert walked[0][2] == pytest.approx(fills[0].r_multiple)
    assert len(res.errors) == 1 and res.errors[0].endswith(f'{side}:{WIDE}')
    only_valid = run(df, ex, side, [NARROW])
    assert only_valid[0].pnl_usdt == res.pnl_usdt        # the refusal changed nothing downstream


def test_mixed_sides_agree_between_simulate_and_walk_table():
    df, ex = frame(), canonical(short_rr())
    lo, sh = np.zeros(len(df), bool), np.zeros(len(df), bool)
    lo[[WIDE, NARROW, NARROW + 10]] = True      # 255 + 4 stays inside the 260 bars
    sh[[WIDE + 1, NARROW + 5]] = True
    fills = []
    res = V.simulate(lo, sh, df, ex, RISK, fills_out=fills)
    walked = V.walk_table(V.trade_table(df, ex, RISK), lo, sh, RISK)
    assert res.trades == len(fills) == len(walked) >= 2
    assert [(f.entry_i, f.exit_i) for f in fills] == [w[:2] for w in walked]
    assert [w[2] for w in walked] == pytest.approx([f.r_multiple for f in fills])


@pytest.mark.parametrize('canon', [True, False])
def test_valid_entries_keep_exact_accounting(canon, monkeypatch):
    df = frame()
    ex = canonical(short_rr()) if canon else short_rr()
    atr = V.atr_series(df, 14).to_numpy(float)
    closes, highs, lows = (df[c].to_numpy(float) for c in ('close', 'high', 'low'))
    args = (NARROW, 'short', df, closes, highs, lows, atr, ex, .06, .0005, 4, 0., 0., None, None, 1e-4, 15)
    assert V._try_trade(*args, fill=V.fill_timing.fills(df)[0]) == V._trade(*args, fill=V.fill_timing.fills(df)[0])
    res, fills, _, walked = run(df, ex, 'short', [NARROW])
    assert res.trades == 1 and walked[0][2] == pytest.approx(fills[0].r_multiple)


def test_other_errors_propagate(monkeypatch):
    df, ex = frame(), canonical(short_rr())
    # unusable reference price: initialize's price-validity check is untouched
    bad = df.copy()
    bad.loc[NARROW + 1, 'open'] = 0.
    with pytest.raises(ValueError, match='exit_entry_evidence_unavailable'):
        run(bad, ex, 'short', [NARROW])

    def boom(*a, **k):
        raise ValueError('something else')
    monkeypatch.setattr(V, '_trade', boom)
    lo, sh = signals(df, 'short', [NARROW])
    with pytest.raises(ValueError, match='something else'):
        V.simulate(lo, sh, df, ex, RISK)
    with pytest.raises(ValueError, match='something else'):
        V.trade_table(df, ex, RISK)
