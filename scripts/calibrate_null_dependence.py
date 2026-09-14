"""Calibrate consistency_p_dependent: shared-regime no-edge, independent
no-edge, and a planted per-symbol edge, all at 16 symbols."""
import sys, time
import numpy as np, pandas as pd
from trader.research import portfolio_null as pn, referee as rf
from trader.strategy.geometries import GEOS

RISK = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
        "slippage_atr_frac": 0.015, "bar_minutes": 240, "real_funding": False}
N, K = 2000, 16

def frame(close):
    ts = pd.date_range("2021-01-01", periods=len(close), freq="4h", tz="UTC")
    prev = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": ts, "open": prev,
                         "high": np.maximum(prev, close) * 1.004,
                         "low": np.minimum(prev, close) * 0.996,
                         "close": close, "volume": 1.0})

def market(seed, beta, idio):
    rng = np.random.default_rng(seed)
    # a factor with volatility regimes, so trend "episodes" cluster
    vol = np.exp(np.cumsum(rng.normal(0, 0.03, N)) * 0.5)
    vol = 0.01 * vol / vol.mean()
    m = rng.normal(0, 1, N) * vol
    closes = [100 * np.exp(np.cumsum(beta * m + rng.normal(0, idio, N)))
              for _ in range(K)]
    return m, closes

def legs_shared_rule(seed, beta, idio):
    """Donchian-like breakout on each symbol's OWN price; symbols share a
    factor, returns are a random walk -> no edge, dependent percentiles."""
    _m, closes = market(seed, beta, idio)
    legs = []
    for i, c in enumerate(closes):
        s = pd.Series(c)
        hi = s.rolling(100).max().shift(1); lo = s.rolling(100).min().shift(1)
        legs.append(pn.Leg(f"S{i}", (s > hi).to_numpy(), (s < lo).to_numpy(),
                           frame(c), None))
    return legs

def legs_shared_signal(seed, beta, idio):
    """The same entries on every symbol, from the shared factor's past."""
    m, closes = market(seed, beta, idio)
    past = pd.Series(np.cumsum(m)).diff(48).to_numpy()
    fire = np.zeros(N, bool); fire[::24] = True
    lo = fire & (past > 0.02); sh = fire & (past < -0.02)
    return [pn.Leg(f"S{i}", lo, sh, frame(c), None) for i, c in enumerate(closes)]

def legs_planted(seed, beta, idio):
    _m, closes = market(seed, beta, idio)
    legs = []
    for i, c in enumerate(closes):
        fwd = np.r_[c[30:] / c[:-30] - 1, np.zeros(30)]
        fire = np.zeros(N, bool); fire[::12] = True
        r = np.random.default_rng(seed * 100 + i).uniform(size=N) < 0.5
        legs.append(pn.Leg(f"S{i}", fire & (((fwd > 0.02) & r) | (~r & (np.roll(fwd,-7)>0.02))),
                           fire & (((fwd < -0.02) & r) | (~r & (np.roll(fwd,-7)<-0.02))),
                           frame(c), None))
    return legs

def one(kind, seed, beta, idio):
    legs = {"null": legs_shared_rule, "signal": legs_shared_signal, "edge": legs_planted}[kind](seed, beta, idio)
    r = rf.consistency(legs, GEOS["trail"], RISK, "4h", seed=seed)
    return r["consistency_p"], r["consistency_p_dep"], r["rho_bar"]

if __name__ == "__main__":
    kind, beta, idio, seeds = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
    t = time.time(); rows = []
    for s in range(seeds):
        rows.append(one(kind, s, beta, idio))
    a = np.array([[x if x is not None else 1.0 for x in r] for r in rows])
    print(f"{kind} beta={beta} idio={idio} seeds={seeds} {time.time()-t:.0f}s "
          f"rho_bar med={np.median(a[:,2]):.3f}")
    for lvl in (0.05, 0.01, 0.0025):
        print(f"  p<={lvl}: raw {np.mean(a[:,0]<=lvl):.3f}  dep {np.mean(a[:,1]<=lvl):.3f}")
