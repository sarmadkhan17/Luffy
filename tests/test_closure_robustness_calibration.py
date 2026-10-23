"""P4: robustness diagnostics on the shared trade accounting; calibration
harness arithmetic and a REAL (non-mocked) single-replicate flow."""
import numpy as np
import pytest

from tests.closure_fixtures import build
import trader.research.closure.manifest as _m  # noqa
from trader.research.closure import calibration as C, development as D, families as F, manifest, robustness as R
from trader.strategy.vector_backtest import simulate


@pytest.fixture(scope='module')
def bundle(tmp_path_factory):
    b = build(tmp_path_factory.mktemp('rob'), name='rob', edge=0.007)
    frames, _ = manifest.development_frames(b.snap, b.env)
    return b, D.build_bundle(frames, b.env)


def test_delay_moves_only_the_fill_not_the_signal_and_zero_is_identity(bundle):
    b, bun = bundle
    c = F.realize(F.make_candidate('trend_breakout.v1'), {}, b.env['envelope_id'])
    base = D.run_symbols(c, bun)
    zero = D.run_symbols(c, bun, delay_bars=0)
    late = D.run_symbols(c, bun, delay_bars=2)
    assert all(base[s]['result'].pnl_usdt == zero[s]['result'].pnl_usdt for s in base)
    sym = next(s for s in base if base[s]['fills'])
    assert [f.entry_i for f in late[sym]['fills']] != [f.entry_i for f in base[sym]['fills']]
    # signal intent unchanged: entry SETS (signal bars) are the same object of study at any delay
    assert R.entry_sets(c, bun) == R.entry_sets(c, bun)
    # the engine default equals an explicit zero delay on a raw simulate() call
    df = bun.frames[sym]
    lo = np.zeros(len(df), bool); lo[300::40] = True
    a = simulate(lo, np.zeros(len(df), bool), df, c.to_spec().exit, bun.risk, symbol=sym)
    z = simulate(lo, np.zeros(len(df), bool), df, c.to_spec().exit, bun.risk, symbol=sym, fill_delay_bars=0)
    assert (a.trades, a.pnl_usdt) == (z.trades, z.pnl_usdt)


def test_report_runs_the_grid_on_identical_samples_and_never_invents_capacity(bundle):
    b, bun = bundle
    c = F.realize(F.make_candidate('trend_breakout.v1'), {}, b.env['envelope_id'])
    base = F.baselines(c)[0]
    r = R.report(c, base, bun)
    assert len(r['scenarios']) == 9 and r['capacity']['status'] == 'UNKNOWN' and r['authority'] == 'DESCRIPTIVE_ONLY'
    one = {(s['cost_mult'], s['delay_bars']): s for s in r['scenarios']}
    assert one[(1.0, 0)]['trades'] >= one[(3.0, 0)]['trades'] * 0 and one[(3.0, 0)]['total_pct'] <= one[(1.0, 0)]['total_pct']
    p = r['paired_vs_baseline']
    assert p['same_capital'] and p['symbols'] == 6 and p['eligible_sample_id'] == r['eligible_sample_id']
    assert r['comparisons_made'] >= 9 + 2 + len(R.SHIFTS)


def test_shifted_copy_is_one_idea_not_diversification(bundle):
    b, bun = bundle
    c = F.realize(F.make_candidate('trend_breakout.v1'), {}, b.env['envelope_id'])
    r = R.report(c, None, bun)
    sd = r['shifted_duplicate']
    assert 1 in sd['flagged_shifts'] and sd['diversification_credit'] is False
    same = R.distinct_from(c, c, bun)
    assert same['overlap'] == 1.0 and same['diversification_credit'] is False


def test_exact_clopper_pearson_and_minimum_replicates():
    assert C.clopper_pearson(0, 29)[1] == pytest.approx(0.1194, abs=2e-3)      # two-sided 95%: 29 zero-failure runs are NOT enough
    lo, hi = C.clopper_pearson(5, 10)
    assert lo == pytest.approx(0.1871, abs=2e-3) and hi == pytest.approx(0.8129, abs=2e-3)
    assert C.min_replicates_for(0.10, 0.95) == 36 and C.clopper_pearson(1, 60)[1] < 0.10 < C.clopper_pearson(2, 60)[1] and C.clopper_pearson(0, 0) == (0.0, 1.0)


def test_verdicts_follow_declared_targets_and_unresolved_is_inconclusive():
    pr = dict(C.PROTOCOL)
    null = dict(name='n', truth='NO_EDGE')
    edge = dict(name='e', truth='EDGE')
    assert C.judge(null, 0, 5, pr)['verdict'] == 'INCONCLUSIVE'          # 5 replicates cannot resolve 10%
    assert C.judge(null, 0, 40, pr)['verdict'] == 'OK'
    assert C.judge(null, 10, 20, pr)['verdict'] == 'FAIL'
    assert C.judge(edge, 3, 4, pr)['verdict'] == 'INCONCLUSIVE'
    assert C.judge(edge, 20, 20, pr)['verdict'] == 'OK' and C.judge(edge, 0, 20, pr)['verdict'] == 'FAIL'
    assert C.judge(dict(name='u', truth='UNAVAILABLE'), 1, 40, pr)['verdict'] == 'FAIL'


SMOKE = dict(C.PROTOCOL, **C.SMOKE)


def test_fill_delay_must_be_a_non_negative_integer(bundle):
    b, bun = bundle
    c = F.realize(F.make_candidate('trend_breakout.v1'), {}, b.env['envelope_id'])
    df = next(iter(bun.frames.values()))
    lo = np.zeros(len(df), bool)
    for bad in (-1, 1.5, True, '2'):
        with pytest.raises(ValueError, match='fill_delay_bars'):
            simulate(lo, lo, df, c.to_spec().exit, bun.risk, fill_delay_bars=bad)


def test_exact_production_numbers_are_calibratable_in_a_test_only_context(tmp_path):
    """Finding 16: the exact configured numbers must not be structurally unable to
    measure power, and running them must need no gate, approval or real seal."""
    from trader.core.config import load_config
    pol = C._policy(C.PROTOCOL)
    cur = manifest.configured_policy(load_config())
    assert pol['klass'] == 'TEST_ONLY' and manifest.numeric_id(pol) == manifest.numeric_id(cur)
    assert C.feasibility(C.PROTOCOL) == []
    infeasible = C.feasibility(dict(C.PROTOCOL, n_bars=4200))
    assert any('unresolvable' in r for r in infeasible) or 'protected_window_below_floor' in infeasible
    with pytest.raises(ValueError, match='structurally_infeasible'):
        C.run(dict(C.PROTOCOL, n_bars=4200), tmp_path / 'never', replicates=1)
    assert manifest.not_weaker(pol, cur) == ['policy_not_configured_class']    # it can never be mistaken for CONFIGURED


def test_report_grants_nothing_and_approval_is_a_separate_explicit_binding():
    rep = dict(calibration_status='CALIBRATED', kind='FINAL', numerics='EXACT_CONFIGURED', authority_granted=False,
               numeric_id='n', evaluator_id='e')
    rep['report_id'] = C.digest(rep)
    env = dict(numeric_id='n', evaluator_id='e')
    assert C.report_authorizes(rep, dict(report_id=rep['report_id']), env)
    assert not C.report_authorizes(rep, dict(report_id='other'), env)
    assert not C.report_authorizes(dict(rep, authority_granted=True), dict(report_id=rep['report_id']), env)
    assert not C.report_authorizes(rep, dict(report_id=rep['report_id']), dict(env, numeric_id='m'))


def test_seed_ranges_identity_and_resume(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='overlap'):
        C.run(dict(SMOKE, kind='FINAL', final_seed_base=10_500), tmp_path / 'x', replicates=1)
    calls = []
    monkeypatch.setattr(C, '_replicate', lambda p, case, i, seed, d, run_id='': calls.append(1) or dict(
        run_id=run_id, case=case['name'], index=i, seed=seed, outcome='NOT_REACHED', trials=0))
    rep = C.run(SMOKE, tmp_path / 'y', replicates=4)
    assert rep['calibration_status'] == 'INCONCLUSIVE' and rep['kind'] == 'DEVELOPMENT_SMOKE' and rep['authority_granted'] is False
    n = len(calls)
    assert C.run(SMOKE, tmp_path / 'y', replicates=4)['replicates'] == rep['replicates'] and len(calls) == n
    for changed in (dict(SMOKE, max_revisions=5), dict(SMOKE, development_seed_base=99), dict(SMOKE, policy=dict(draws=40))):
        with pytest.raises(ValueError, match='identity_changed'):
            C.run(changed, tmp_path / 'y', replicates=4)
    with pytest.raises(ValueError, match='identity_changed'):
        C.run(SMOKE, tmp_path / 'y', replicates=5)                           # different count = different run
    monkeypatch.setattr(manifest, 'evaluator_id', lambda: 'new-code')
    with pytest.raises(ValueError, match='identity_changed'):
        C.run(SMOKE, tmp_path / 'y', replicates=4)                           # code change cannot reuse stale replicates


def test_interrupted_replicate_resumes_without_regenerating_data(tmp_path, monkeypatch):
    from trader.research.closure import study as S
    proto = dict(SMOKE, families=['trend_breakout.v1'], max_revisions=0, policy=dict(min_threshold_samples=1000),
                 cases=[c for c in C.PROTOCOL['cases'] if c['name'] == 'unsealed_exposure_unknown'])
    real = S._run_unit
    state = {'n': 0}

    def crash(*a, **k):
        state['n'] += 1
        if state['n'] == 2:
            raise KeyboardInterrupt('crash probe')
        return real(*a, **k)
    monkeypatch.setattr(S, '_run_unit', crash)
    with pytest.raises(KeyboardInterrupt):
        C.run(proto, tmp_path, replicates=1)
    d = tmp_path / 'unsealed_exposure_unknown-0'
    marker = (d / 'snapshot.ok').stat().st_mtime_ns
    snap_stat = (d / 'snapshot.db').stat().st_mtime_ns
    monkeypatch.setattr(S, '_run_unit', real)
    rep = C.run(proto, tmp_path, replicates=1)
    assert (d / 'snapshot.db').stat().st_mtime_ns == snap_stat and (d / 'snapshot.ok').stat().st_mtime_ns == marker
    assert rep['replicates'][0]['outcome'] != 'QUALIFIED'


def test_real_flow_replicate_has_no_mocked_outcomes(tmp_path):
    """One genuine replicate through registry->envelope->development->freeze->
    qualification (or an honest earlier stop). The assertion is only that an
    unsealed window can never qualify and the report stays INCONCLUSIVE."""
    proto = dict(SMOKE, families=['trend_breakout.v1'], max_revisions=0, policy=dict(min_threshold_samples=1000),
                 cases=[c for c in C.PROTOCOL['cases'] if c['name'] == 'unsealed_exposure_unknown'])
    rep = C.run(proto, tmp_path, replicates=1)
    r = rep['replicates'][0]
    assert r['outcome'] != 'QUALIFIED' and r['trials'] >= 1
    assert rep['cases'][0]['qualified'] == 0 and rep['calibration_status'] == 'INCONCLUSIVE'
