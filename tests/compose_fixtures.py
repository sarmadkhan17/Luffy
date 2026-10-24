"""SYNTHETIC context snapshots for the compositional tests. Candles, daily
reference series (`spx`, `dxy`) and 8h funding carry first-close receipts
(observed when each bar became final), so the production point-in-time
stores serve them exactly like retained history. Planted structure is
labelled and exists only to prove mechanisms; it is never evidence of an edge.

modes
  null      returns independent of context
  spx       4h drift +e when the last KNOWN spx 30-day return > 0, -e otherwise
  xor       drift +e when sign(spx ret30) == sign(dxy ret30), -e otherwise:
            each context alone has ZERO marginal effect (joint-only)
"""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from tests.closure_fixtures import DEV_BARS, N_BARS, RISK, build  # noqa: F401
from trader.data import market_provenance as mp
from trader.data.derivatives import DerivFeed
from trader.data.references import DAY, RefStore
from trader.research.closure import synthetic as S

TF = S.TF_MS


def _ref_series(rng, n_days, vol=0.01):
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, vol, n_days)))
    return close


def _known_ret30(close, day_of_bar):
    """ret30 of the last daily bar already known at each 4h bar (day k-1)."""
    out = np.zeros(len(day_of_bar))
    for i, k in enumerate(day_of_bar):
        j = k - 1
        out[i] = close[j] / close[j - 30] - 1.0 if j >= 30 else 0.0
    return out


def write_context_snapshot(path, *, mode='null', n_symbols=10, n_bars=N_BARS, seed=5, edge=0.0035, vol=0.009,
                           spx_override=None, ref_until_bar=None):
    path = Path(path)
    if path.exists():
        path.unlink()
    rng0 = np.random.default_rng(seed)
    n_days = n_bars * TF // DAY + 2
    spx, dxy = _ref_series(rng0, n_days), _ref_series(rng0, n_days)
    if spx_override is not None:
        spx = spx_override(spx)
    day_of_bar = (np.arange(n_bars) * TF) // DAY
    a, b = _known_ret30(spx, day_of_bar), _known_ret30(dxy, day_of_bar)
    if mode == 'spx':
        drift = np.where(a > 0, edge, -edge)
    elif mode == 'xor':
        drift = np.where(np.sign(a) == np.sign(b), edge, -edge)
    else:
        drift = np.zeros(n_bars)
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL, open REAL, high REAL, '
                'low REAL, close REAL, volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))')
    names = S.symbols(n_symbols)
    for i, name in enumerate(names):
        rng = np.random.default_rng(seed * 1009 + i)
        px, rows = 100.0, []
        for k in range(n_bars):
            px *= 1.0 + drift[k] + rng.normal(0.0, vol)
            v = 10.0 * (1.0 + abs(rng.normal(0, 0.3)))
            rows.append((name, '4h', S.EPOCH_MS + k * TF, px, px * 1.004, px * 0.996, px, v, v / 2))
        con.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    con.close()
    S._receipts(path)
    store = RefStore(path)
    for key, close in (('spx', spx), ('dxy', dxy)):
        frames = []
        for d in range(n_days if ref_until_bar is None else ref_until_bar * TF // DAY):
            ts = S.EPOCH_MS // DAY * DAY + d * DAY
            row = pd.DataFrame(dict(ts=[pd.Timestamp(ts, unit='ms')], open=[close[d]], high=[close[d]], low=[close[d]],
                                    close=[close[d]], volume=[1.0]))
            frames.append(mp.annotate(row, instrument_id=f'offline:ref:{key}', source='offline:synthetic-context',
                                      kind='reference', received_ms=ts + DAY, timeframe='1d'))
        df = pd.concat(frames, ignore_index=True)
        df.attrs.update(frames[0].attrs)
        store.save(key, df, now_ms=int(S.EPOCH_MS + n_bars * TF))
    store.db.close()
    return names


def write_funding(path, names, n_bars=N_BARS, seed=7):
    """8h funding with receipts (observed at settlement)."""
    path = Path(path)
    if path.exists():
        path.unlink()
    feed = DerivFeed(db_path=str(path))
    rng = np.random.default_rng(seed)
    step = 8 * 3_600_000
    n = n_bars * TF // step
    for s in names:
        frames = []
        for k in range(n):
            ts = S.EPOCH_MS + k * step
            row = pd.DataFrame(dict(ts=[pd.Timestamp(ts, unit='ms')], value=[float(rng.normal(0.0001, 0.0002))]))
            frames.append(mp.annotate(row, instrument_id=f'offline:deriv:{s}', source='offline:synthetic-context',
                                      kind='derivative', received_ms=ts + step))
        df = pd.concat(frames, ignore_index=True)
        df.attrs.update(frames[0].attrs)
        feed.save(s, 'funding', df)
    feed.db.close()


def build_ctx(tmp, *, mode='null', name='ctx', n_sym=10, n_bars=N_BARS, funding=False, **kw):
    """closure_fixtures.build() equivalent over a context snapshot."""
    from types import SimpleNamespace
    from trader.research.closure import exposure, manifest
    from trader.research.closure.store import Store
    snap = Path(tmp) / f'{name}.db'
    names = write_context_snapshot(snap, mode=mode, n_symbols=n_sym, n_bars=n_bars, **kw)
    derivs = None
    if funding:
        derivs = Path(tmp) / f'{name}-derivs.db'
        write_funding(derivs, names, n_bars)
    store = Store(Path(tmp) / f'{name}.closure')
    st = S.EPOCH_MS
    exposure.seal(store, 'SYNTHETIC_DECLARED', st, st + n_bars * TF, at_ms=1)
    policy = manifest.test_policy(min_threshold_samples=1000)
    m = manifest.build_manifest(snap, '4h', names, exposure.events(store))
    env = manifest.make_envelope(
        m, snap, policy=policy, discovery_symbols=names[:6], heldout_symbols=names[6:], fit_start_ms=st,
        dev_end_ms=st + DEV_BARS * TF, embargo_bars=10, protected_start_ms=st + (DEV_BARS + 100) * TF,
        protected_end_ms=st + (n_bars - 1) * TF, risk=RISK, development_mode='REPLAY')
    return SimpleNamespace(store=store, snap=snap, derivs=derivs, names=names, manifest=m, env=env)
