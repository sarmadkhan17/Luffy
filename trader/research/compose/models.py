"""Deterministic fitting of ridge / lasso / bounded-tree artifacts.

Fitting sees ONLY the rows handed in (the caller passes the development
window), standardisation statistics are computed from those rows and stored in
the artifact, and the prediction target is frozen in the artifact with its
outcome-availability interval: row i's target (forward return over `horizon`
bars) is usable only when bar i+horizon lies inside the fit window, so no
overlapping outcome crosses the fit boundary (purge). Inference is
`strategy.fitted.predict`; nothing here is used at inference time.
"""
from __future__ import annotations

import numpy as np

from ...strategy import fitted as F


def forward_return(close: np.ndarray, horizon: int) -> np.ndarray:
    """Target: close[t+h]/close[t]-1, NaN where bar t+h does not exist yet."""
    c = np.asarray(close, dtype=float)
    y = np.full(len(c), np.nan)
    if len(c) > horizon:
        y[:-horizon] = c[horizon:] / c[:-horizon] - 1.0
    return y


def _usable(X, y):
    return np.isfinite(X).all(axis=1) & np.isfinite(y)


def _lasso(Z, yc, alpha, iters=500, tol=1e-9):
    n, k = Z.shape
    w = np.zeros(k)
    col = (Z ** 2).sum(axis=0) / n
    r = yc.copy()
    for _ in range(iters):
        shift = 0.0
        for j in range(k):
            if col[j] == 0:
                continue
            rho = (Z[:, j] @ r) / n + col[j] * w[j]
            new = np.sign(rho) * max(abs(rho) - alpha, 0.0) / col[j]
            if new != w[j]:
                r -= Z[:, j] * (new - w[j])
                shift = max(shift, abs(new - w[j]))
                w[j] = new
        if shift < tol:
            break
    return w


def _grow(X, y, depth, max_depth, min_leaf):
    if depth >= max_depth or len(y) < 2 * min_leaf or np.ptp(y) == 0:
        return {'leaf': float(y.mean())}
    best = None
    base = ((y - y.mean()) ** 2).sum()
    for f in range(X.shape[1]):
        for t in np.unique(np.quantile(X[:, f], np.linspace(0.05, 0.95, 19))):
            m = X[:, f] <= t
            nl = int(m.sum())
            if nl < min_leaf or len(y) - nl < min_leaf:
                continue
            sse = ((y[m] - y[m].mean()) ** 2).sum() + ((y[~m] - y[~m].mean()) ** 2).sum()
            if best is None or sse < best[0] - 1e-15:
                best = (sse, f, float(t), m)
    if best is None or best[0] >= base:
        return {'leaf': float(y.mean())}
    _, f, t, m = best
    return {'f': f, 't': t, 'lo': _grow(X[m], y[m], depth + 1, max_depth, min_leaf),
            'hi': _grow(X[~m], y[~m], depth + 1, max_depth, min_leaf)}


def fit(model_type: str, inputs: list, X: np.ndarray, y: np.ndarray, *, horizon: int, fit_window: list,
        alpha: float = 1.0, max_depth: int = 2, min_leaf: int = 50, target: str = 'forward_return') -> dict:
    """Fit on rows (X: n x k, y aligned). Returns a verified artifact."""
    if model_type not in F.MODEL_TYPES:
        raise F.ArtifactError('model_type_unsupported')
    if max_depth > F.MAX_DEPTH:
        raise F.ArtifactError('tree_exceeds_bound')
    X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    ok = _usable(X, y)
    if int(ok.sum()) < max(30, 5 * X.shape[1]):
        raise F.ArtifactError(f'fit_rows_insufficient:{int(ok.sum())}')
    Xt, yt = X[ok], y[ok]
    body = dict(schema=F.SCHEMA, model_type=model_type, inputs=list(inputs), target=target, horizon=int(horizon),
                fit_window=list(fit_window), n_fit=int(ok.sum()), n_rows=int(len(X)),
                hyper=dict(alpha=float(alpha), max_depth=int(max_depth), min_leaf=int(min_leaf)))
    if model_type == 'tree':
        body['tree'] = _grow(Xt, yt, 0, max_depth, min_leaf)
    else:
        mean, scale = Xt.mean(axis=0), Xt.std(axis=0)
        scale = np.where(scale > 0, scale, 1.0)
        Z, yc = (Xt - mean) / scale, yt - yt.mean()
        if model_type == 'ridge':
            w = np.linalg.solve(Z.T @ Z + alpha * len(Z) * np.eye(Z.shape[1]), Z.T @ yc)
        else:
            w = _lasso(Z, yc, alpha)
        body.update(mean=[float(v) for v in mean], scale=[float(v) for v in scale],
                    coef=[float(v) for v in w], intercept=float(yt.mean()))
    body['artifact_id'] = F.artifact_id(body)
    return F.verify(body)


# ── chronological walk-forward ───────────────────────────────────────────
def _rank(a):
    """Tie-aware (average) ranks: tied values share one rank, so constant input has zero rank variance and its
    correlation is undefined, never an arbitrary perfect/zero ordering."""
    import pandas as pd
    return pd.Series(np.asarray(a, dtype=float)).rank(method='average').to_numpy()


def spearman(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 20:
        return None
    ra, rb = _rank(a[ok]), _rank(b[ok])
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _rows(data, hi_ms, lo_ms, horizon, bar_ms):
    """Pooled rows whose TARGET is fully observed by hi_ms: ts + horizon*bar <= hi_ms (purge), ts >= lo_ms."""
    X, y, t = [], [], []
    for d in data:
        m = (d['ts'] >= lo_ms) & (d['ts'] + horizon * bar_ms <= hi_ms)
        X.append(d['X'][m]); y.append(d['y'][m]); t.append(d['ts'][m])
    return np.vstack(X), np.concatenate(y), np.concatenate(t)


def _fit_selected(model_type, inputs, X, y, t, *, horizon, bar_ms, window, grid, base):
    """Fit on (X, y); a declared grid is chosen by an INNER chronological split of the TRAINING rows only
    (the outer validation fold is never seen). Returns (artifact, selection record)."""
    key = 'max_depth' if model_type == 'tree' else 'alpha'
    values = list(grid.get(key) or [base[key]])
    sel = dict(grid={key: values}, chosen=values[0], scores=None, inner_split_ms=None)
    if len(values) > 1:
        cut = int(np.quantile(t, 0.75))
        tr = t + horizon * bar_ms <= cut
        va = t >= cut
        scores = {}
        for v in values:
            try:
                a = fit(model_type, inputs, X[tr], y[tr], horizon=horizon, fit_window=window, **dict(base, **{key: v}))
                p = F_predict(a, X[va])
                ok = np.isfinite(p) & np.isfinite(y[va])
                scores[str(v)] = float(np.mean((p[ok] - y[va][ok]) ** 2)) if ok.sum() > 10 else None
            except F.ArtifactError:
                scores[str(v)] = None
        good = {k: v for k, v in scores.items() if v is not None}
        if good:
            best = min(good, key=good.get)
            sel.update(chosen=values[[str(v) for v in values].index(best)], scores=scores, inner_split_ms=cut)
    art = fit(model_type, inputs, X, y, horizon=horizon, fit_window=window, **dict(base, **{key: sel['chosen']}))
    return art, sel


def F_predict(art, X):
    return F.predict(art, X)


def walk_forward(model_type, inputs, data, *, horizon, bar_ms, fit_start_ms, dev_end_ms, n_folds=4, grid=None,
                 alpha=1.0, max_depth=2, min_leaf=50, p=None, direction='above', target='forward_return') -> tuple:
    """Predeclared expanding-window walk-forward over the DEVELOPMENT window on a shared calendar.

    edges = fit_start .. dev_end in n_folds+1 equal spans. Segment k is valid from edge k and trained ONLY on rows
    whose target was observed by edge k (purged); the final segment is valid from dev_end and trained on the whole
    development window - it is the frozen model. `data`: [{'X','y','ts'}] per symbol. Returns (schedule artifact,
    record with out-of-fold quality per fold). Scaling statistics, inner hyperparameter selection and the
    score->action threshold all come from the segment's own training rows.
    """
    grid = grid or {}
    edges = [int(e) for e in np.linspace(fit_start_ms, dev_end_ms, n_folds + 2)[1:-1]] + [int(dev_end_ms)]
    base = dict(alpha=alpha, max_depth=max_depth, min_leaf=min_leaf, target=target)
    segs, rec = [], dict(folds=[], skipped=[], horizon=horizon, edges_ms=edges)
    for k, vf in enumerate(edges):
        X, y, t = _rows(data, vf, fit_start_ms, horizon, bar_ms)
        ok = _usable(X, y)
        try:
            if ok.sum() < max(60, 10 * X.shape[1]):
                raise F.ArtifactError(f'fit_rows_insufficient:{int(ok.sum())}')
            art, sel = _fit_selected(model_type, inputs, X[ok], y[ok], t[ok], horizon=horizon, bar_ms=bar_ms,
                                     window=[fit_start_ms, vf], grid=grid, base=base)
        except F.ArtifactError as exc:
            rec['skipped'].append(dict(valid_from_ms=vf, reason=str(exc)))      # this span abstains: no model, no entry
            continue
        thr = None
        if p is not None:
            sc = F.predict(art, X[ok])
            thr = round(float(np.quantile(sc, float(p) if direction == 'above' else float(p))), 8)
        seg = dict(valid_from_ms=vf, train_lo_ms=int(t[ok].min()), train_hi_ms=int(t[ok].max()), thr=thr, dir=direction,
                   p=p, artifact=art)
        segs.append(seg)
        rec['folds'].append(dict(valid_from_ms=vf, n_train=int(ok.sum()), selection=sel, model=art['model_type'],
                                 artifact_id=art['artifact_id'], thr=thr))
    if not segs:
        raise F.ArtifactError('walk_forward_no_segment')
    body = dict(schema=F.SCHEMA, model_type='schedule', inputs=list(inputs), target=target, horizon=int(horizon),
                bar_ms=int(bar_ms), fit_window=[int(fit_start_ms), int(dev_end_ms)], n_folds=int(n_folds),
                hyper=dict(alpha=alpha, max_depth=max_depth, min_leaf=min_leaf, grid=grid), segments=segs)
    body['artifact_id'] = F.artifact_id(body)
    sched = F.verify(body)
    # out-of-fold quality: rows scored by the segment valid at their own time (never one that saw their target)
    for k, f in enumerate(rec['folds']):
        hi = edges[edges.index(f['valid_from_ms']) + 1] if f['valid_from_ms'] != edges[-1] else None
        if hi is None:
            f['oof'] = None
            continue
        sc, yy = [], []
        for d in data:
            m = (d['ts'] >= f['valid_from_ms']) & (d['ts'] < hi)
            sc.append(F.predict_schedule(sched, d['X'][m], d['ts'][m])); yy.append(d['y'][m])
        a, b = np.concatenate(sc), np.concatenate(yy)
        f['oof'] = dict(rank_ic=spearman(a, b), n=int((np.isfinite(a) & np.isfinite(b)).sum()))
    return sched, rec
