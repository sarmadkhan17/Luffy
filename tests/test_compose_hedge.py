"""§3A hedge mechanisms on SYNTHETIC series: the structure, accounting, partial
fills, stress and replay/adapter parity - not evidence that any hedge pays."""
import numpy as np
import pytest

from trader.research.closure import ClosureRefused
from trader.research.compose import hedge as H


def market(n=1500, beta=1.5, idio=0.002, seed=3):
    rng = np.random.default_rng(seed)
    m = rng.normal(0.0, 0.01, n)
    m[400:430] -= 0.012                                    # a sustained market drawdown
    m[900:925] -= 0.012
    a = beta * m + rng.normal(0, idio, n)
    return 100 * np.cumprod(1 + a), 100 * np.cumprod(1 + m)


def signals(n, every=60, hold=40):
    s, x = np.zeros(n, int), np.zeros(n, bool)
    for i in range(130, n - hold - 2, every):
        s[i], x[i + hold] = 1, True
    return s, x


def spec(**kw):
    return H.make_spec(primary=dict(symbol='ALT/USDT'), hedge=dict(symbol='BTC/USDT'), purpose='protective_beta', **kw)


def test_netting_same_symbol_hedge_is_refused_but_hedge_mode_and_cross_symbol_pass():
    with pytest.raises(ClosureRefused) as e:
        H.make_spec(primary=dict(symbol='ALT/USDT'), hedge=dict(symbol='ALT/USDT'), purpose='protective_beta',
                    account_mode='netting')
    assert e.value.code == 'NETTING_COLLAPSES_HEDGE'
    H.make_spec(primary=dict(symbol='ALT/USDT'), hedge=dict(symbol='ALT/USDT'), purpose='protective_beta', account_mode='hedge')
    H.make_spec(primary=dict(symbol='ALT/USDT'), hedge=dict(symbol='BTC/USDT'), purpose='protective_beta', account_mode='netting')
    with pytest.raises(ClosureRefused):
        H.make_spec(primary=dict(symbol='A', contract='inverse'), hedge=dict(symbol='B'), purpose='protective_beta')


def test_inverse_contract_pnl_is_in_coin_and_differs_from_linear():
    lin, inv = H.Contract('linear'), H.Contract('inverse', 100.0)
    assert lin.pnl_usd(2.0, 100.0, 110.0) == pytest.approx(20.0)
    # 10 inverse contracts of $100: long profits (1/100 - 1/110) coin per $ notional, valued at 110
    assert inv.pnl_usd(10, 100.0, 110.0) == pytest.approx(10 * 100 * (1 / 100 - 1 / 110) * 110)
    assert inv.pnl_usd(10, 100.0, 110.0) < 10 * 100 * 0.10 + 1e-9          # convexity: less than linear-equivalent
    assert inv.pnl_usd(-10, 100.0, 110.0) == pytest.approx(-inv.pnl_usd(10, 100.0, 110.0))


def test_next_open_fill_does_not_capture_a_gap_before_the_position_exists():
    p = np.array([100., 130., 140.])
    h = np.array([100., 100., 100.])
    signals_ = np.array([1, 0, 0])
    sp = spec(ratio=dict(method='fixed', value=0.), fee_bps=0., slip_bps=0.)
    # 100 units requested at the signal price, filled at125 next open; the initial25-point gap is not earned.
    out = H.replay(sp, p, h, signals_, np.zeros(3, bool),
                   opens=(np.array([100., 125., 130.]), h.copy()))
    assert out['equity'][1] == pytest.approx(10500.)
    assert out['final_equity'] == pytest.approx(11500.)
    assert out['fill_price_basis'] == 'NEXT_BAR_OPEN'


def test_unknown_held_mark_and_invalid_fills_never_look_like_zero_exposure():
    p, h = market(500)
    s, x = signals(500, every=400, hold=300)
    h[200:205] = np.nan
    out = H.replay(spec(unwind=dict(max_hold_bars=400)), p, h, s, x)
    assert np.isnan(out['equity'][200:205]).all()
    assert np.isnan(out['gross'][200:205]).all()
    metrics = H.risk_metrics(out)
    assert not metrics['risk_complete'] and metrics['unknown_mark_bars'] == 5
    assert metrics['net_return_pct'] is None
    for bad in (-1., float('nan'), 1000000.):
        book = H.HedgeBook(spec())
        book.pending['x'] = ('primary', 1, 1., 0)
        with pytest.raises(ClosureRefused, match='hedge_fill_outside_authorized_quantity'):
            book.apply_fills({'x': bad})


def test_ratio_is_estimated_on_development_prefix_only():
    p, h = market()
    pr, hr = np.r_[np.nan, np.diff(p) / p[:-1]], np.r_[np.nan, np.diff(h) / h[:-1]]
    sp = spec()
    r_early = H.estimate_ratio(dict(sp, ratio=dict(sp['ratio'], fit_end=1000)), pr, hr, 1400)
    pr2 = pr.copy()
    pr2[1100:] *= 5.0                                      # future perturbation beyond the fit prefix
    assert H.estimate_ratio(dict(sp, ratio=dict(sp['ratio'], fit_end=1000)), pr2, hr, 1400) == pytest.approx(r_early)
    assert 1.2 < r_early < 1.8                              # recovers the planted beta of 1.5


def test_hedged_beats_unhedged_and_reduced_on_declared_objective_with_costs_and_stress():
    p, h = market()
    s, x = signals(len(p))
    out = H.evaluate(spec(), p, h, s, x, fit_end=1000)
    c = out['comparators']
    assert out['verdict'] == 'HEDGE_SUPPORTED', out['reason']
    assert c['hedged']['max_drawdown_pct'] < c['unhedged']['max_drawdown_pct']
    assert c['hedged']['max_drawdown_pct'] < c['reduced']['max_drawdown_pct']
    assert c['hedged']['costs']['fees'] > c['unhedged']['costs']['fees'] > 0          # carry/turnover cost is charged
    assert {'correlation_break', 'basis_divergence', 'funding_spike', 'liquidity_loss', 'one_leg_fill',
            'margin_pressure'} <= set(out['stress'])
    # the hedge's benefit is not unconditional: a correlation break removes it
    assert out['stress']['correlation_break']['max_drawdown_pct'] > c['hedged']['max_drawdown_pct']
    assert out['stress']['one_leg_fill']['max_drawdown_pct'] >= c['hedged']['max_drawdown_pct'] - 1e-9
    assert out['stress']['margin_pressure']['liquidated'] is True                      # low net exposure != no liquidation risk
    assert out['runtime_support']['coin_m'] is False and out['runtime_support']['hedge_mode'] is False


def test_useless_hedge_is_reported_as_unsupported_not_forced():
    p, _ = market()
    rng = np.random.default_rng(9)
    h = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(p)))        # uncorrelated "hedge"
    s, x = signals(len(p))
    out = H.evaluate(spec(), p, h, s, x, fit_end=1000)
    assert out['verdict'] != 'HEDGE_SUPPORTED'


def test_partial_fill_leaves_residual_and_rebalance_completes_it():
    p, h = market(800)
    s, x = signals(800, every=300, hold=200)
    part = H.replay(spec(), p, h, s, x, venue=H.Venue(fill_frac=dict(primary=1.0, hedge=0.4)))
    assert sum(i.kind == 'REBALANCE' for i in part['instructions']) >= 2        # shortfall detected, re-attempted
    assert not any(i.kind == 'REDUCE_PRIMARY' for i in part['instructions'])    # converging, so no forced reduction
    none = H.replay(spec(), p, h, s, x, venue=H.Venue(fill_frac=dict(primary=1.0, hedge=0.0)))
    assert any(i.kind == 'REDUCE_PRIMARY' for i in none['instructions'])        # never carry the naked remainder
    full = H.replay(spec(), p, h, s, x)
    assert not any(i.kind == 'REDUCE_PRIMARY' for i in full['instructions'])


def test_hedge_leg_data_loss_triggers_alert_and_unwind():
    p, h = market(600)
    s, x = signals(600, every=500, hold=450)
    h2 = h.copy()
    h2[250:] = np.nan
    res = H.replay(spec(unwind=dict(max_hold_bars=400)), p, h2, s, np.zeros_like(x))
    ins = res['instructions']
    assert any(i.kind == 'ALERT' and i.reason == 'LEG_DATA_UNAVAILABLE' for i in ins)
    assert any(i.kind == 'UNWIND' and i.reason == 'leg_data_unavailable' for i in ins)


def test_account_mode_changes_position_side_in_instructions():
    p, h = market(500)
    s, x = signals(500, every=400, hold=100)
    hm = H.replay(spec(account_mode='hedge'), p, h, s, x)['instructions']
    nm = H.replay(spec(account_mode='netting'), p, h, s, x)['instructions']
    assert {i.position_side for i in hm if i.kind == 'ENTER'} == {'LONG', 'SHORT'}
    assert {i.position_side for i in nm} == {'BOTH'}
    closes = [i for i in hm if i.kind == 'UNWIND']
    assert closes and all(i.position_side == ('LONG' if i.leg == 'primary' else 'SHORT') for i in closes)


def test_replay_and_incremental_runtime_adapter_emit_identical_instructions():
    p, h = market(700)
    s, x = signals(700, every=200, hold=80)
    sp = spec()
    ref = H.replay(sp, p, h, s, x, venue=H.Venue(fill_frac=dict(primary=0.7, hedge=1.0)))
    # runtime view: the same bars one at a time, fills reported back from the same paper venue
    ad = H.RuntimeAdapter(sp)
    pc, hc = (H.Contract(sp[k]['contract'], sp[k]['multiplier']) for k in ('primary', 'hedge'))
    pr, hr = np.r_[np.nan, np.diff(p) / p[:-1]], np.r_[np.nan, np.diff(h) / h[:-1]]
    got, pending, venue = [], [], H.Venue(fill_frac=dict(primary=0.7, hedge=1.0))
    for t in range(len(p) - 1):
        fills = venue.fill(pending, t) if pending else None
        lo = max(0, t - 95)
        a, b = pr[lo:t + 1], hr[lo:t + 1]
        ok = np.isfinite(a) & np.isfinite(b)
        corr = float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 20 else None
        bar = H._bar(sp, t, p[t], h[t], int(s[t]), bool(x[t]), H.estimate_ratio(sp, pr, hr, t), corr,
                     ref['equity'][t], pc, hc)
        ins = ad.on_bar(t, bar, fills)
        got += ins
        pending = ins
    key = lambda L: [(i.kind, i.leg, i.side, round(i.qty, 6), i.reason, i.position_side, i.bar) for i in L]   # noqa: E731
    assert key(got) == key(ref['instructions'])
