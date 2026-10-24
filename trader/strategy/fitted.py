"""Fitted-model artifacts and their ONE deterministic inference path.

An artifact is plain JSON, content-addressed (`artifact_id` = sha256 of the whole
body, nested artifacts included). The registry stores canonical TEXT, so a caller
cannot mutate a verified artifact; every `get` re-verifies. Types: ridge, lasso,
bounded tree, and `schedule` - a walk-forward set of fitted segments, each valid
from a stated time, whose training window ends at least `purge_bars` bars (the
target horizon) before that time. That boundary is checked in `verify`, so a
segment can neither be built from nor selected for data it was not allowed to
see. Inference for ridge/lasso/tree is `predict`; for a schedule it is
`predict_schedule` (segment chosen by the bar's own timestamp; no segment yet ->
NaN -> abstain). Missing input -> NaN; nothing is imputed.

Artifacts persist as `<id>.json` under $LUFFY_FITTED_DIR and are re-verified on
load, so a fresh process resolves the identical model. No generated Python runs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .features import register

SCHEMA = 'fitted-model.v1'
MAX_DEPTH = 4
MAX_SEGMENTS = 64
BASE_TYPES = ('ridge', 'lasso', 'tree')
MODEL_TYPES = BASE_TYPES + ('schedule',)


class ArtifactError(ValueError):
    pass


_REGISTRY: dict = {}          # id -> canonical JSON text (immutable once verified)


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def artifact_id(body: dict) -> str:
    return hashlib.sha256(_canon({k: v for k, v in body.items() if k != 'artifact_id'}).encode()).hexdigest()


def _finite(vals, what):
    a = np.asarray(vals, dtype=float)
    if not np.isfinite(a).all():
        raise ArtifactError(f'nonfinite:{what}')


def _check_tree(node, k, depth=0):
    if depth > MAX_DEPTH:
        raise ArtifactError('tree_exceeds_bound')
    if 'leaf' in node:
        _finite([node['leaf']], 'leaf')
        return 1
    if not (isinstance(node.get('f'), int) and 0 <= node['f'] < k) or set(node) != {'f', 't', 'lo', 'hi'}:
        raise ArtifactError('tree_structure')
    _finite([node['t']], 'threshold')
    return _check_tree(node['lo'], k, depth + 1) + _check_tree(node['hi'], k, depth + 1)


def verify(art: dict, _seen=()) -> dict:
    if not isinstance(art, dict) or art.get('schema') != SCHEMA or art.get('model_type') not in MODEL_TYPES:
        raise ArtifactError('artifact_schema')
    if art.get('artifact_id') != artifact_id(art):
        raise ArtifactError('artifact_integrity')
    if art['artifact_id'] in _seen:
        raise ArtifactError('artifact_cycle')
    inputs = art.get('inputs')
    if not inputs or not all(isinstance(e, str) for e in inputs):
        raise ArtifactError('inputs_shape')
    from . import dsl
    for e in inputs:                                  # every input must parse; nested fitted ids must resolve (acyclic)
        for aid in _fitted_ids(dsl.parse(e)):
            verify(get(aid), _seen + (art['artifact_id'],))
    k = len(inputs)
    t = art['model_type']
    if t in ('ridge', 'lasso'):
        for key in ('mean', 'scale', 'coef'):
            if len(art.get(key, ())) != k:
                raise ArtifactError(f'shape:{key}')
            _finite(art[key], key)
        _finite([art['intercept']], 'intercept')
        if (np.asarray(art['scale']) <= 0).any():
            raise ArtifactError('scale_nonpositive')
    elif t == 'tree':
        if _check_tree(art['tree'], k) > 2 ** MAX_DEPTH:
            raise ArtifactError('tree_exceeds_bound')
    else:
        segs = art.get('segments') or []
        if not segs or len(segs) > MAX_SEGMENTS or int(art.get('bar_ms', 0)) <= 0 or int(art.get('horizon', 0)) < 1:
            raise ArtifactError('schedule_shape')
        last = None
        for s in segs:
            if last is not None and s['valid_from_ms'] <= last:
                raise ArtifactError('schedule_order')
            last = s['valid_from_ms']
            sub = s['artifact']
            if sub.get('model_type') not in BASE_TYPES or sub['inputs'] != inputs:
                raise ArtifactError('segment_model')
            verify(sub, _seen + (art['artifact_id'],))
            # the enforceable fit boundary: the last training TARGET must be fully observed before the segment is valid
            if s['train_hi_ms'] + art['horizon'] * art['bar_ms'] > s['valid_from_ms'] or s['train_lo_ms'] > s['train_hi_ms']:
                raise ArtifactError('fit_boundary_violated')
            if s.get('thr') is not None:
                _finite([s['thr']], 'thr')
    return art


def _fitted_ids(tree):
    import ast
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ('fitted', 'fitted_pass'):
            yield n.args[0].value


def register_artifact(art: dict) -> str:
    verify(art)
    _REGISTRY[art['artifact_id']] = _canon(art)
    return art['artifact_id']


def save(art: dict, directory) -> Path:
    verify(art)
    p = Path(directory) / f'{art["artifact_id"]}.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.read_text() != _canon(art):
        raise ArtifactError('artifact_file_conflict')
    p.write_text(_canon(art))
    return p


def get(aid: str) -> dict:
    text = _REGISTRY.get(aid)
    if text is None:
        d = os.environ.get('LUFFY_FITTED_DIR')
        f = Path(d) / f'{aid}.json' if d else None
        if f is not None and f.is_file():
            art = json.loads(f.read_text())
            if art.get('artifact_id') != aid:
                raise ArtifactError('artifact_file_identity')
            register_artifact(art)
            text = _REGISTRY[aid]
    if text is None:
        raise ArtifactError(f'artifact_unregistered:{aid[:12]}')
    art = json.loads(text)
    if art.get('artifact_id') != artifact_id(art):
        raise ArtifactError('artifact_integrity')
    return art


def predict(art: dict, X: np.ndarray) -> np.ndarray:
    """Scores for rows of X (input order = art['inputs']). Any non-finite input -> NaN."""
    X = np.asarray(X, dtype=float)
    ok = np.isfinite(X).all(axis=1)
    out = np.full(len(X), np.nan)
    if art['model_type'] in ('ridge', 'lasso'):
        z = (X[ok] - np.asarray(art['mean'])) / np.asarray(art['scale'])
        out[ok] = z @ np.asarray(art['coef']) + float(art['intercept'])
    elif art['model_type'] == 'tree':
        for i in np.flatnonzero(ok):
            node = art['tree']
            while 'leaf' not in node:
                node = node['lo'] if X[i, node['f']] <= node['t'] else node['hi']
            out[i] = node['leaf']
    else:
        raise ArtifactError('schedule_needs_timestamps')
    return out


def predict_schedule(art: dict, X: np.ndarray, ts_ms: np.ndarray, *, as_pass: bool = False) -> np.ndarray:
    """Score (or 1/0 pass of the segment's own training-derived threshold) with the segment valid at each row's time."""
    X = np.asarray(X, dtype=float)
    ts = np.asarray(ts_ms, dtype='int64')
    starts = np.array([s['valid_from_ms'] for s in art['segments']], dtype='int64')
    idx = np.searchsorted(starts, ts, side='right') - 1
    out = np.full(len(X), np.nan)
    for j, s in enumerate(art['segments']):
        rows = np.flatnonzero(idx == j)
        if not len(rows):
            continue
        sc = predict(s['artifact'], X[rows])
        if as_pass:
            if s.get('thr') is None:
                raise ArtifactError('segment_without_threshold')
            hit = sc >= s['thr'] if s['dir'] == 'above' else sc <= s['thr']
            sc = np.where(np.isfinite(sc), hit.astype(float), np.nan)
        out[rows] = sc
    return out


def input_matrix(art: dict, ctx) -> np.ndarray:
    from . import dsl
    cols = []
    for expr in art['inputs']:
        v = dsl.evaluate(dsl.parse(expr), ctx)
        cols.append(v.to_numpy(dtype=float, na_value=np.nan) if isinstance(v, pd.Series)
                    else np.full(len(ctx.index), float(v)))
    return np.column_stack(cols)


def _ts_ms(ctx) -> np.ndarray:
    from ..data import market_provenance as mp
    return np.asarray(mp.ms(ctx.df['ts']), dtype='int64')


@register('fitted', arg_specs=((str, None, None),), requires=('ohlcv',))
def _fitted(ctx, aid: str) -> pd.Series:
    art = get(aid)
    X = input_matrix(art, ctx)
    if art['model_type'] == 'schedule':
        return pd.Series(predict_schedule(art, X, _ts_ms(ctx)), index=ctx.index)
    return pd.Series(predict(art, X), index=ctx.index)


@register('fitted_pass', arg_specs=((str, None, None),), requires=('ohlcv',))
def _fitted_pass(ctx, aid: str) -> pd.Series:
    art = get(aid)
    if art['model_type'] != 'schedule':
        raise ArtifactError('fitted_pass_needs_schedule')
    return pd.Series(predict_schedule(art, input_matrix(art, ctx), _ts_ms(ctx), as_pass=True), index=ctx.index)


def requires_of(aid: str) -> tuple:
    """Data an artifact's inputs need (the DSL derives requirements from this)."""
    from . import dsl
    return dsl.data_requires(*[dsl.parse(e) for e in get(aid)['inputs']])
