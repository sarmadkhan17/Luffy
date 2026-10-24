"""Actual bounded inference history: a >400-bar expression and a fitted artifact containing it (hidden input), the 210 cap,
holes inside the real window, higher-timeframe branches and explicit abstention. Synthetic data only."""
import json

import numpy as np
import pandas as pd
import pytest

from tests.market_receipt_fixtures import snapshot as Snapshot
from tests.test_compile import _spec
from trader.core.types import Action
from trader.research.compose import models
from trader.strategy import dsl, fill_timing, fitted as F
from trader.strategy.compile import compile_spec
from trader.strategy.features import FeatureCtx

N = 700


@pytest.fixture(scope='module')
def frame():
    rng = np.random.default_rng(11)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, N)))
    return pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=N, freq="15min", tz="UTC"), "open": close,
                         "high": close * 1.002, "low": close * 0.998, "close": close, "volume": rng.uniform(50, 500, N)})


def artifact(frame, expr='zscore(close, 450)'):
    ctx = FeatureCtx(frames={'15m': frame}, tf='15m')
    X = np.column_stack([dsl.evaluate(dsl.parse(expr), ctx).to_numpy(float), dsl.evaluate(dsl.parse('ret(6)'), ctx).to_numpy(float)])
    y = models.forward_return(frame['close'].to_numpy(float), 3)
    art = models.fit('ridge', [expr, 'ret(6)'], X, y, horizon=3, fit_window=[0, 1])
    F.register_artifact(art)
    return art


def plain(expr):
    return _spec(entry_long=expr, filters=[], regime_filter=[], markets=['futures'])


def test_requirement_follows_the_real_window_not_the_210_cap_and_includes_fitted_inputs(frame):
    assert fill_timing.history_requirements(plain('zscore(close, 450) > 0'))['base'] >= 451
    art = artifact(frame)
    need = fill_timing.history_requirements(plain(f'fitted("{art["artifact_id"]}") > 0'))
    assert need['base'] >= 451 and not need['exceeds_bound']                       # the hidden artifact input counts
    assert fill_timing.lookback_bars(plain(f'fitted("{art["artifact_id"]}") > 0')) >= 451       # and the gap check uses it
    nested = fill_timing.history_requirements(plain('zscore(rolling_std(close, 300), 400) > 0'))
    assert nested['base'] >= 700                                                    # nested windows add, not max
    assert fill_timing.history_requirements(plain('rsi(14) > 50'))['base'] < 100


def test_unavailable_artifact_is_an_unknown_requirement_that_abstains_not_zero(frame):
    spec = plain('fitted("' + 'a' * 64 + '") > 0')
    with pytest.raises(Exception):
        fill_timing.history_requirements(spec)


def test_higher_timeframe_branch_has_its_own_requirement(frame):
    need = fill_timing.history_requirements(plain("htf('1h', zscore(close, 300)) > 0"))
    assert need['htf'].get('1h', 0) >= 301 and need['base'] < 100


def test_a_long_expression_and_its_fitted_twin_agree_between_research_and_inference(frame):
    art = artifact(frame)
    for expr in ('zscore(close, 450) > 0', f'fitted("{art["artifact_id"]}") > 0'):
        c = compile_spec(plain(expr))
        lo, _ = c.entries({"15m": frame})
        snap = Snapshot(symbol="BTC/USDT", ts="", price=float(frame["close"].iloc[-1]), dfs={"15m": frame}, market_type="futures")
        sig = c.to_evaluator()(c.spec, snap)
        assert (sig is not None and sig.action == Action.BUY) == bool(lo[-1])


def test_insufficient_inference_history_abstains_explicitly_instead_of_reading_nan(frame):
    art = artifact(frame)
    for expr in ('zscore(close, 450) > -99', f'fitted("{art["artifact_id"]}") > -99'):
        c = compile_spec(plain(expr))
        full = Snapshot(symbol="BTC/USDT", ts="", price=1.0, dfs={"15m": frame}, market_type="futures")
        short = Snapshot(symbol="BTC/USDT", ts="", price=1.0, dfs={"15m": frame.iloc[-400:].reset_index(drop=True)}, market_type="futures")
        assert c.to_evaluator()(c.spec, full) is not None                           # enough history: the condition holds
        seen = []
        assert c.to_evaluator()(c.spec, short, diagnostic=lambda code, *a: seen.append(code)) is None
        assert seen and seen[0].startswith('insufficient_inference_history')        # 400 bars < 451: a named abstention


def close_ms(df):
    from trader.data import market_provenance as mp
    return np.asarray(mp.ms(df['ts'])) + 900_000                                    # real epoch ms, whatever the pandas datetime unit


def receipts(df, window, late_slot=None, late_at=None):
    out = df.copy()
    avail = close_ms(out).astype(float)
    if late_slot is not None:
        avail[late_slot] = late_at
    out['available_at_ms'] = avail
    out.attrs.update(timeframe='15m', read_mode='replay', signal_lookback_bars=window)
    return out


def test_clock_construction_uses_real_milliseconds_on_this_pandas(frame):
    ms = close_ms(frame)
    assert ms[0] == int(pd.Timestamp("2026-01-01T00:15:00Z").timestamp() * 1000)    # the old astype('int64')//10**6 gave microseconds/1000


def test_a_hole_older_than_210_but_inside_the_real_window_blocks_the_signal(frame):
    spec = plain('zscore(close, 450) > 0')
    window = fill_timing.lookback_bars(spec)
    assert window > 210
    df = receipts(frame.drop(index=N - 300).reset_index(drop=True), window)         # a missing bar 300 bars back
    fill, why = fill_timing.fills(df)
    assert why[-2] == 'history_gap' and fill[-2] == -1                              # inside the actual consumed window
    assert fill_timing.fills(df.assign())[1][-2] == 'history_gap'
    df.attrs['signal_lookback_bars'] = 210                                          # what the old cap would have checked
    assert fill_timing.fills(df)[1][-2] != 'history_gap' and fill_timing.fills(df)[0][-2] == len(df) - 1


def test_a_receipt_arriving_after_the_decision_inside_the_real_window_blocks_the_next_open_fill_only_with_the_real_window(frame):
    """Row N-350 is received AFTER the whole frame (10 bars past its last bar). Signal N-2 consumes it iff its window reaches
    350 back (450 does, the old 210 does not). The old assertion accepted the ordinary fill N-1 and proved nothing."""
    spec = plain('zscore(close, 450) > 0')
    window = fill_timing.lookback_bars(spec)
    last_open = int(close_ms(frame)[-1]) - 900_000
    late = last_open + 10 * 900_000
    new = receipts(frame, window, late_slot=N - 350, late_at=late)
    fill, why = fill_timing.fills(new)
    assert fill[-2] == -1 and why[-2] == 'no_executable_bar'                        # the decision cannot precede its input
    old = receipts(frame, 210, late_slot=N - 350, late_at=late)
    assert fill_timing.fills(old)[0][-2] == N - 1                                   # the 210 cap never looked 350 bars back
    # control: the same row received BEFORE the decision stays executable on the real window
    ok = receipts(frame, window, late_slot=N - 350, late_at=int(close_ms(frame)[N - 350]) + 1000)
    assert fill_timing.fills(ok)[0][-2] == N - 1
    # an earlier signal at its TRUE historical clock: input N-400 is received 90 minutes after its bar opened; signal N-398 would
    # decide at ts+45m, so it must wait for the receipt (fill N-394, 3 bars late) which is past interval+TTL: refused, not filled N-397
    stale = receipts(frame, window, late_slot=N - 400, late_at=int(close_ms(frame)[N - 400]) + 5 * 900_000)
    f2, w2 = fill_timing.fills(stale)
    assert f2[N - 398] == -1 and w2[N - 398] == 'signal_stale'                       # waiting for the receipt exceeds interval + TTL: refused
    untouched = receipts(frame, window)
    assert fill_timing.fills(untouched)[0][N - 398] == N - 397                       # control: no late receipt -> the ordinary next open


def htf_frames(hole=None, n_h=460):
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, 4 * n_h)))
    base = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=4 * n_h, freq="15min", tz="UTC"), "open": close, "high": close * 1.002,
                         "low": close * 0.998, "close": close, "volume": 100.0})
    h = base.iloc[::4].reset_index(drop=True).copy()
    h['ts'] = pd.date_range("2026-01-01", periods=n_h, freq="1h", tz="UTC")
    h['close'] = base['close'].to_numpy()[3::4]
    if hole is not None:
        h = h.drop(index=hole).reset_index(drop=True)                                # a missing 1h source interval
    return base, h


def test_an_htf_source_hole_inside_the_rolling_dependency_abstains_until_the_whole_window_is_valid():
    spec = plain("htf('1h', zscore(close, 300)) > -99")
    c = compile_spec(spec)
    base, clean = htf_frames()
    base2, holed = htf_frames(hole=100)
    lo_clean, _ = c.entries({"15m": base, "1h": clean})
    lo_hole, _ = c.entries({"15m": base2, "1h": holed})
    first_ok_clean = int(np.argmax(lo_clean))
    assert 4 * 299 <= first_ok_clean < 4 * 302                                      # needs a full 300-bar source window
    k_after = 100 + 300                                                              # source rows 100..399 contain/bridge the hole
    assert not lo_hole[4 * 100 + 8: 4 * k_after - 8].any()                           # abstains while the hole is inside the window
    assert lo_hole[4 * (k_after + 5):].all()                                         # and recovers only after it has left it
    assert lo_clean[4 * 400:].all()                                                  # the same bars are valid without the hole


def test_the_live_evaluator_refuses_a_gap_inside_the_consumed_tail_not_only_a_short_history(frame):
    spec = plain('zscore(close, 450) > -99')
    c = compile_spec(spec)
    holed = frame.drop(index=N - 300).reset_index(drop=True)
    seen = []
    snap = Snapshot(symbol="BTC/USDT", ts="", price=1.0, dfs={"15m": holed}, market_type="futures")
    assert c.to_evaluator()(c.spec, snap, diagnostic=lambda code, *a: seen.append(code)) is None
    assert seen and seen[0].startswith('history_gap_in_consumed_tail')
    clean = Snapshot(symbol="BTC/USDT", ts="", price=1.0, dfs={"15m": frame}, market_type="futures")
    assert c.to_evaluator()(c.spec, clean) is not None                               # control: same expression on the contiguous tail


def test_replay_and_inference_both_refuse_oversize_dependencies_instead_of_clamping_them(frame):
    expr = 'close'
    for _ in range(13):
        expr = f'zscore({expr}, 500)'
    for condition in (f'{expr} > 0', f"htf('1d', {expr}) > 0"):
        spec = plain(condition)
        assert fill_timing.history_requirements(spec)['exceeds_bound'] is True
        with pytest.raises(ValueError, match='history_exceeds_bound'):
            fill_timing.lookback_bars(spec)
        with pytest.raises(ValueError, match='history_exceeds_bound'):
            compile_spec(spec).entries({'15m': frame})
