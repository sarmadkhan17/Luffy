"""Chronological walk-forward: purged train-only fitting, enforceable fit boundary, out-of-fold honesty, immutable
artifacts and a fresh-process reload. Synthetic data; mechanism tests, not edge claims."""
import copy
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from trader.research.compose import models
from trader.strategy import dsl, fitted as F

BAR = 14_400_000
N, H, T0 = 1600, 6, 1_700_000_000_000


def data(seed=0, planted=0.0, n_sym=3, perturb_from=None, perturb_scale=50.0):
    out = []
    for s in range(n_sym):
        rng = np.random.default_rng(seed * 31 + s)
        X = rng.normal(size=(N, 2))
        y = planted * X[:, 0] + rng.normal(0, 1.0, N) * 0.01
        ts = T0 + np.arange(N, dtype='int64') * BAR
        if perturb_from is not None:                       # alter FUTURE rows (features and targets) after an instant
            m = ts >= perturb_from
            X = X.copy(); y = y.copy()
            X[m] *= perturb_scale; y[m] = rng.normal(0, 5.0, m.sum())
        out.append(dict(X=X, y=y, ts=ts))
    return out


def wf(d, **kw):
    return models.walk_forward('ridge', ['rsi(14)', 'ret(6)'], d, horizon=H, bar_ms=BAR, fit_start_ms=T0,
                               dev_end_ms=T0 + N * BAR, n_folds=4, p=0.9, grid=dict(alpha=[0.01, 1.0, 100.0]), **kw)


def test_segments_are_purged_and_the_boundary_is_enforced_not_decorative():
    art, rec = wf(data(planted=1.0))
    for s in art['segments']:
        assert s['train_hi_ms'] + H * BAR <= s['valid_from_ms']
    bad = copy.deepcopy(art)
    bad['segments'][1]['train_hi_ms'] = bad['segments'][1]['valid_from_ms']      # claims it trained on unobserved targets
    bad['artifact_id'] = F.artifact_id(bad)
    with pytest.raises(F.ArtifactError, match='fit_boundary_violated'):
        F.verify(bad)
    assert rec['folds'][0]['selection']['inner_split_ms'] is not None            # inner selection ran inside training only


def test_future_perturbation_changes_no_earlier_fitted_segment_or_prediction():
    base, _ = wf(data(planted=1.0))
    edge = base['segments'][2]['valid_from_ms']
    pert, _ = wf(data(planted=1.0, perturb_from=edge))
    for a, b in zip(base['segments'], pert['segments']):
        if a['valid_from_ms'] + 0 <= edge:
            # a segment valid at or before the perturbation cannot depend on rows from the perturbation on
            assert a['artifact']['artifact_id'] == b['artifact']['artifact_id'] and a['thr'] == b['thr']
    d0 = data(planted=1.0)[0]
    m = d0['ts'] < edge
    p0 = F.predict_schedule(base, d0['X'][m], d0['ts'][m])
    p1 = F.predict_schedule(pert, d0['X'][m], d0['ts'][m])
    assert np.array_equal(np.isnan(p0), np.isnan(p1)) and np.allclose(p0[np.isfinite(p0)], p1[np.isfinite(p1)])
    assert base['segments'][-1]['artifact']['artifact_id'] != pert['segments'][-1]['artifact']['artifact_id']  # later fits DO see it


def test_outer_fold_outcomes_never_reach_that_folds_own_model():
    base, rec = wf(data(planted=1.0))
    k = 2
    lo, hi = base['segments'][k]['valid_from_ms'], base['segments'][k + 1]['valid_from_ms']
    d = data(planted=1.0)
    for s in d:
        m = (s['ts'] >= lo) & (s['ts'] < hi)
        s['y'] = s['y'].copy(); s['y'][m] = 99.0              # destroy the targets of the validation span itself
    alt, _ = wf(d)
    assert base['segments'][k]['artifact']['artifact_id'] == alt['segments'][k]['artifact']['artifact_id']
    assert base['segments'][k + 1]['artifact']['artifact_id'] != alt['segments'][k + 1]['artifact']['artifact_id']


def test_out_of_fold_quality_is_honest_on_noise_and_positive_when_planted():
    _, rn = wf(data(seed=4, planted=0.0))
    _, rp = wf(data(seed=4, planted=1.0))
    ics_n = [f['oof']['rank_ic'] for f in rn['folds'] if f['oof'] and f['oof']['rank_ic'] is not None]
    ics_p = [f['oof']['rank_ic'] for f in rp['folds'] if f['oof'] and f['oof']['rank_ic'] is not None]
    assert ics_n and abs(np.mean(ics_n)) < 0.05           # no skill claimed where none exists
    assert ics_p and np.mean(ics_p) > 0.5


def test_no_segment_before_enough_history_means_abstain():
    art, _ = wf(data(planted=1.0))
    d = data()[0]
    early = d['ts'] < art['segments'][0]['valid_from_ms']
    out = F.predict_schedule(art, d['X'][early], d['ts'][early], as_pass=True)
    assert early.sum() > 100 and np.isnan(out).all()


def test_registry_is_immutable_and_deep_validation_rejects_malformed():
    art, _ = wf(data(planted=1.0))
    F.register_artifact(art)
    got = F.get(art['artifact_id'])
    got['segments'][0]['thr'] = 123.0                        # caller-side mutation must not reach the registry
    assert F.get(art['artifact_id'])['segments'][0]['thr'] != 123.0
    sub = copy.deepcopy(art['segments'][0]['artifact'])
    sub['coef'] = [float('nan'), 1.0]
    with pytest.raises(Exception):
        F.artifact_id(sub)                                    # NaN is not even serialisable
    sub['coef'] = [1.0]
    sub['artifact_id'] = F.artifact_id(sub)
    with pytest.raises(F.ArtifactError, match='shape'):
        F.verify(sub)
    with pytest.raises(dsl.SpecError):
        dsl.parse(f'ref("spx", fitted("{art["artifact_id"]}"))')       # no model inside a ref/htf/xs wrapper
    with pytest.raises(dsl.SpecError):
        dsl.parse(f'htf("4h", fitted("{art["artifact_id"]}"))')


def test_fresh_process_reloads_identical_model_from_disk(tmp_path):
    art, _ = wf(data(planted=1.0))
    F.save(art, tmp_path)
    d = data()[0]
    np.save(tmp_path / 'X.npy', d['X']); np.save(tmp_path / 'ts.npy', d['ts'])
    ref = F.predict_schedule(art, d['X'], d['ts'])
    code = ("import sys,numpy as np;from trader.strategy import fitted as F;a=F.get(sys.argv[1]);"
            "X=np.load(sys.argv[2]+'/X.npy');t=np.load(sys.argv[2]+'/ts.npy');"
            "np.save(sys.argv[2]+'/out.npy',F.predict_schedule(a,X,t))")
    env = dict(os.environ, LUFFY_FITTED_DIR=str(tmp_path), PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.getcwd())
    subprocess.run([sys.executable, '-c', code, art['artifact_id'], str(tmp_path)], check=True, env=env, timeout=120, cwd=tmp_path)
    out = np.load(tmp_path / 'out.npy')
    assert np.array_equal(np.isnan(out), np.isnan(ref)) and np.allclose(out[np.isfinite(ref)], ref[np.isfinite(ref)])
