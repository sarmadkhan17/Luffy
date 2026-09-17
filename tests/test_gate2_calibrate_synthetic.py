"""Synthetic-only checks: no production loaders, stores or runtime imports."""
import ast
import importlib.util
import math
from pathlib import Path
import sys

import numpy as np
import pytest

PATH = Path(__file__).resolve().parents[1]/'scripts/gate2_calibrate_synthetic.py'
spec = importlib.util.spec_from_file_location('gate2_calibrate_synthetic', PATH)
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


def test_whole_block_bootstrap_matches_literal_reference():
    c = fixture()
    seed, draws = 91, 133
    got = cal.bootstrap(c, draws, np.random.default_rng(seed))
    s, _ = cal.summaries(c)
    observed, _ = cal.contrasts(s.sum(axis=0))
    rng = np.random.default_rng(seed)
    extreme = np.zeros(2)
    for ix in rng.integers(12, size=(draws, 12)):
        # Direct trade concatenation, preserving multiplicity and both arms.
        rr = np.concatenate([c.r[(c.entry//30 == i) & c.complete] for i in ix])
        labels = np.concatenate([c.labels[(c.entry//30 == i) & c.complete] for i in ix])
        for h in range(2):
            a, b = rr[labels[:, h] == 1], rr[labels[:, h] == 0]
            extreme[h] += len(a) == 0 or len(b) == 0 or a.mean()-b.mean()-observed[h] >= observed[h]
    np.testing.assert_equal(got['p'], (1+extreme)/(draws+1))


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
