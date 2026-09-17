"""Synthetic-only checks: no production loaders, stores or runtime imports."""
import ast
import importlib.util
import math
from pathlib import Path
import sys

import numpy as np
import pytest

PATH = Path(__file__).resolve().parents[1]/'scripts/gate2_calibrate_stationary.py'
spec = importlib.util.spec_from_file_location('gate2_calibrate_stationary', PATH)
cal = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cal
spec.loader.exec_module(cal)


def test_import_surface_is_offline():
    tree = ast.parse(PATH.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)
    assert set(imports) <= {'__future__', 'argparse', 'dataclasses', 'hashlib',
                            'json', 'math', 'pathlib', 'sys', 'numpy',
                            'trader.research.fdr', 'concurrent.futures'}


def test_exact_bounds_known_values_and_zero_precision():
    lo, hi = cal.binomial_bounds(0, 100, .05)
    assert lo == 0
    assert hi == pytest.approx(1-.05**.01)
    assert cal.binomial_bounds(100, 100, .05)[0] == pytest.approx(.05**.01)
    assert cal.binomial_bounds(1, 2, .05) == pytest.approx((1-math.sqrt(.95), math.sqrt(.95)))
    for k in (0, 1, 17, 99, 100):
        a = cal.binomial_bounds(k, 100, .001)
        b = cal.binomial_bounds(100-k, 100, .001)
        assert a == pytest.approx((1-b[1], 1-b[0]), abs=1e-10)
    n = cal.zero_failure_n(.00018, .00005)
    assert cal.binomial_bounds(0, n, .00005)[1] <= .00018
    assert cal.binomial_bounds(0, n-1, .00005)[1] > .00018
    assert cal.rate(0, 0, .05, .01)['assessment'] == 'inconclusive'


def test_frozen_family_levels_and_precision():
    levels = cal.family_levels(2, [1])
    assert levels == pytest.approx([.003046424052297288, .001007637299531772])
    assert levels[1] < cal.alpha_at(3, [1, 2])
    n = cal.draws_for(levels[1], 10000)
    assert 1/(n+1) <= levels[1] < 1/n
    assert cal.draws_for(levels[1], n-1) is None
    with pytest.raises(ValueError):
        cal.make_plan(cap=100)


def fixture():
    rng = np.random.default_rng(14)
    return cal.generate(rng, 'iid')


def test_unknown_and_incomplete_are_neither_arm():
    c = fixture()
    c.labels[:30] = -1
    c.complete[-10:] = False
    s, counts = cal.summaries(c)
    for co in counts:
        assert sum(co['arms'])+co['unknown']+co['incomplete'] == len(c.r)
        assert co['unknown'] == 30 and co['incomplete'] == 10
    assert s[:, 0, :, 1].sum() == pytest.approx(c.r[30:-10].sum())


def test_entry_block_ignores_exit_and_keeps_empty_calendar_blocks():
    c = fixture()
    c.exit += 100
    before, _ = cal.summaries(c)
    c.exit += 100
    after, _ = cal.summaries(c)
    np.testing.assert_equal(before, after)
    c.labels[(c.entry >= 30) & (c.entry < 60)] = -1
    s, _ = cal.summaries(c)
    assert len(s) == 12
    assert not s[1].any()


def test_floors_and_zero_variance_fail_closed():
    c = fixture()
    c.labels[:, 0] = 0
    c.labels[:19, 0] = 1
    b = cal.bootstrap(c, 99, np.random.default_rng(1))
    assert b['p'][0] == 1
    c = fixture()
    c.labels[c.entry >= 330] = -1
    assert all(not co['eligible'] for co in cal.summaries(c)[1])
    c = fixture()
    c.r[:] = 1
    b = cal.bootstrap(c, 99, np.random.default_rng(1))
    np.testing.assert_equal(b['p'], [1, 1])
    np.testing.assert_equal(b['degenerate'], [99, 99])


def literal_moments(c, ix, h, run_blocks=4.):
    arms = [[c.r[(c.entry//30 == i) & c.complete & (c.labels[:, h] == a)]
             for i in ix] for a in (0, 1)]
    counts = [sum(map(len, aa)) for aa in arms]
    if min(counts) == 0:
        return float('nan'), float('nan')
    means = [np.concatenate(aa).mean() for aa in arms]
    u = []
    for j in range(len(ix)):
        u.append(sum(arms[1][j]-means[1])/counts[1]
                 -sum(arms[0][j]-means[0])/counts[0])
    q = sum(v*v for v in u)
    for lag in range(1, min(len(ix), math.ceil(run_blocks))):
        q += 2*(1-lag/run_blocks)*sum(u[j]*u[j-lag] for j in range(lag, len(ix)))
    return means[1]-means[0], math.sqrt(len(ix)/(len(ix)-1)*q)


def test_whole_block_bootstrap_matches_literal_reference():
    c = fixture()
    c.labels[(c.entry >= 30) & (c.entry < 60)] = -1
    # Use 24 blocks so a deliberately empty calendar block retains eligibility.
    c = cal.generate(np.random.default_rng(17), 'iid', 24)
    c.labels[(c.entry >= 30) & (c.entry < 60)] = -1
    seed, draws = 91, 133
    got = cal.bootstrap(c, draws, np.random.default_rng(seed))
    s, _ = cal.summaries(c)
    delta, se, _, valid = cal.studentized_moments(s)
    assert valid.all()
    observed = [literal_moments(c, np.arange(24), h) for h in range(2)]
    np.testing.assert_allclose(delta, [x[0] for x in observed])
    np.testing.assert_allclose(se, [x[1] for x in observed])
    extreme = np.zeros(2)
    rng = np.random.default_rng(seed)
    starts = rng.integers(24, size=(draws, 24))
    restart = rng.random((draws, 24)) < .25
    indices = []
    for row in range(draws):
        ix = [starts[row, 0]]
        for j in range(1, 24):
            ix.append(starts[row, j] if restart[row, j] else (ix[-1]+1)%24)
        indices.append(ix)
    for ix in indices:
        d, ss, _, _ = cal.studentized_moments(s[ix])
        for h in range(2):
            literal_d, literal_se = literal_moments(c, ix, h)
            assert d[h] == pytest.approx(literal_d)
            assert ss[h] == pytest.approx(literal_se)
            extreme[h] += (not np.isfinite(literal_se) or literal_se <= 1e-14
                           or (literal_d-delta[h])/literal_se >= delta[h]/se[h])
    np.testing.assert_equal(got['p'], (1+extreme)/(draws+1))


def test_nonzero_contrast_with_zero_cluster_se_refuses():
    c = cal.generate(np.random.default_rng(4), 'heavy_tail_floor', 12, 5)
    c.r = 2.*c.labels[:, 0]
    result = cal.bootstrap(c, 99, np.random.default_rng(4))
    np.testing.assert_allclose(result['delta'], [2., 2.])
    np.testing.assert_equal(result['p'], [1., 1.])
    np.testing.assert_equal(result['degenerate'], [99, 99])


def test_studentization_is_translation_and_positive_scale_invariant():
    c = fixture()
    before = cal.bootstrap(c, 299, np.random.default_rng(3))
    c.r = 7*c.r + 12
    after = cal.bootstrap(c, 299, np.random.default_rng(3))
    np.testing.assert_equal(before['p'], after['p'])
    np.testing.assert_allclose(after['delta'], before['delta']*7)
    np.testing.assert_allclose(after['se'], before['se']*7)


def test_required_missing_slice_and_sign_cannot_be_dropped():
    a = {'p': np.array([1., 1.]), 'delta': np.array([1., 1.])}
    b = {'p': np.array([.00001, .00001]), 'delta': np.array([1., -1.])}
    p, positive = cal.combine_required([a, b])
    np.testing.assert_equal(p, [1, 1])
    np.testing.assert_equal(positive, [True, False])
    assert (cal.combine_required([])[0] == 1).all()


@pytest.mark.parametrize('scenario', cal.SCENARIOS)
def test_generator_determinism_and_valid_partition(scenario):
    c = cal.generate(np.random.default_rng(5), scenario)
    d = cal.generate(np.random.default_rng(5), scenario)
    np.testing.assert_equal(c.r, d.r)
    np.testing.assert_equal(c.labels, d.labels)
    assert np.isfinite(c.r).all()
    assert (c.exit >= c.entry).all()
    assert (c.exit[c.complete] <= c.span_days).all()
    for co in cal.summaries(c)[1]:
        assert sum(co['arms']) + co['unknown'] + co['incomplete'] == len(c.r)


def test_no_existing_output_is_overwritten(tmp_path):
    out = tmp_path/'preserved'
    out.mkdir()
    sentinel = out/'file'
    sentinel.write_text('original')
    with pytest.raises(FileExistsError):
        cal.main(['--output-dir', str(out), '--smoke'])
    assert sentinel.read_text() == 'original'


def test_smoke_budget_is_not_precision_budget():
    p = cal.make_plan(smoke=True)
    assert p['repetitions'] < p['planned_repetitions']
    assert p['approval'].startswith('BLOCKED')


def test_irreversible_failure_shortcut_matches_full_decisions():
    for seed in range(20):
        c = cal.generate(np.random.default_rng(seed), 'shared_factor', 24)
        full = cal.bootstrap(c, 1431, np.random.default_rng(seed))
        short = cal.bootstrap(c, 1431, np.random.default_rng(seed), decision_alpha=.003)
        for alpha in (.0007, .001, .003):
            np.testing.assert_equal((full['p'] <= alpha) & (full['delta'] > 0),
                                    (short['p'] <= alpha) & (short['delta'] > 0))
        if short['p_is_lower_bound']:
            assert not ((short['p'] <= .003) & (short['delta'] > 0)).any()


def test_stress_rows_can_actually_be_eligible():
    for scenario in ('missingness', 'spillover_1', 'spillover_2'):
        c = cal.generate(np.random.default_rng(11), scenario, 24)
        assert all(co['eligible'] for co in cal.summaries(c)[1])
    c = cal.generate(np.random.default_rng(11), 'heavy_tail_floor', 12, 5)
    for co in cal.summaries(c)[1]:
        assert co['arms'] == [40, 20]
        assert co['eligible']


def test_fractional_filter_matches_direct_convolution():
    seed, n, memory, d = 9, 8, 16, .4
    rng = np.random.default_rng(seed)
    eps = rng.normal(size=n+memory-1)
    w = np.ones(memory)
    for k in range(1, memory):
        w[k] = w[k-1]*(k-1+d)/k
    expected = np.convolve(eps, w)[memory-1:memory-1+n]/np.linalg.norm(w)
    np.testing.assert_allclose(cal.fractional(np.random.default_rng(seed), n, d, memory), expected)


def test_invalid_resamples_keep_full_denominator_and_threshold(monkeypatch):
    c = cal.generate(np.random.default_rng(19), 'heavy_tail_floor', 12, 5)

    class FixedDraws:
        def __init__(self, invalid):
            self.invalid = invalid

        def integers(self, high, size):
            rows, k = size
            ix = np.tile(np.arange(k), (rows, 1))
            # Repeating one cluster gives zero influence variance even when
            # both arms exist and their contrast is nonzero.
            ix[:self.invalid] = 0
            return ix

    monkeypatch.setattr(cal, "stationary_indices", lambda rng, draws, k, run_blocks: rng.integers(k, size=(draws, k)))
    got = cal.bootstrap(c, 100, FixedDraws(20))
    np.testing.assert_equal(got['degenerate'], [20, 20])
    assert (got['p'] >= 21/101).all()
    refused = cal.bootstrap(c, 100, FixedDraws(21))
    np.testing.assert_equal(refused['p'], [1., 1.])


def test_shortcut_matches_full_decision_with_invalid_observed_se():
    c = cal.generate(np.random.default_rng(9), 'heavy_tail_floor', 12, 5)
    c.r = c.labels[:, 0].astype(float)
    full = cal.bootstrap(c, 1431, np.random.default_rng(1))
    short = cal.bootstrap(c, 1431, np.random.default_rng(1), decision_alpha=.003)
    np.testing.assert_equal(full['p'], short['p'])
    assert short['generated_draws'] < full['generated_draws']


def test_stationary_sampler_forced_paths_and_uniform_marginals():
    class Forced:
        def __init__(self, restart):
            self.restart = restart

        def integers(self, high, size):
            return np.full(size, high-1, dtype=int)

        def random(self, size):
            return np.full(size, 0. if self.restart else .999)

    np.testing.assert_equal(cal.stationary_indices(Forced(False), 2, 5),
                            [[4, 0, 1, 2, 3], [4, 0, 1, 2, 3]])
    np.testing.assert_equal(cal.stationary_indices(Forced(True), 2, 5),
                            np.full((2, 5), 4))
    ix = cal.stationary_indices(np.random.default_rng(43), 20000, 24)
    for col in range(24):
        assert np.max(np.abs(np.bincount(ix[:, col], minlength=24)/20000 - 1/24)) < .015
    continuing = np.mean(ix[:, 1:] == (ix[:, :-1]+1)%24)
    assert continuing == pytest.approx(.75+.25/24, abs=.005)


def test_lag_zero_reduces_to_paired_cluster_se_and_iid_sampler():
    s, _ = cal.summaries(fixture())
    total = s.sum(axis=0)
    n, sums = total[..., 0], total[..., 1]
    residual = (s[..., 1]-s[..., 0]*(sums/n))/n
    u = residual[..., 1]-residual[..., 0]
    _, se, _, _ = cal.studentized_moments(s, run_blocks=1)
    np.testing.assert_allclose(se**2, len(s)/(len(s)-1)*(u*u).sum(axis=0))
    expected = np.random.default_rng(23).integers(12, size=(99, 12))
    np.testing.assert_equal(cal.stationary_indices(np.random.default_rng(23), 99, 12, 1), expected)


def test_hac_matches_dense_bartlett_quadratic_form_and_is_psd():
    rng = np.random.default_rng(56)
    for k in (12, 24, 48):
        for bandwidth in (1., 4/3, 2., 4.):
            c = cal.generate(rng, 'iid', k)
            s, _ = cal.summaries(c)
            total = s.sum(axis=0)
            n, sums = total[..., 0], total[..., 1]
            residual = (s[..., 1]-s[..., 0]*(sums/n))/n
            u = residual[..., 1]-residual[..., 0]
            weights = np.maximum(0, 1-np.abs(np.arange(k)[:, None]-np.arange(k))/bandwidth)
            assert np.linalg.eigvalsh(weights).min() > -1e-12
            expected = np.array([u[:, h] @ weights @ u[:, h] for h in range(2)])*k/(k-1)
            _, se, _, valid = cal.studentized_moments(s, bandwidth)
            assert valid.all()
            np.testing.assert_allclose(se**2, expected)


def test_nonfinite_standard_error_refuses():
    c = fixture()
    c.r[:] = np.nan
    result = cal.bootstrap(c, 99, np.random.default_rng(8))
    np.testing.assert_equal(result['p'], [1., 1.])


def test_original_generator_outputs_preserved_exactly():
    old_spec = importlib.util.spec_from_file_location('previous_studentized_for_comparison',
                                                     PATH.with_name('gate2_calibrate_studentized.py'))
    old = importlib.util.module_from_spec(old_spec)
    sys.modules[old_spec.name] = old
    old_spec.loader.exec_module(old)
    for scenario in old.SCENARIOS:
        a = old.generate(np.random.default_rng(33), scenario, 24)
        b = cal.generate(np.random.default_rng(33), scenario, 24)
        for attr in ('entry', 'exit', 'r', 'labels', 'complete'):
            np.testing.assert_equal(getattr(a, attr), getattr(b, attr))
    assert cal.make_plan()['scenario_blocks']['regime_480_long'] == 48
