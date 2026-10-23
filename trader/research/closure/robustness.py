"""P4a — robustness diagnostics on the existing trade accounting.

Same frozen signals, same eligible sample, same capital for every scenario:

* execution delay moves only the FILL bar (engine fill-timing array); signal
  ATR, stop geometry and exit intent are unchanged
* cost stress scales fee and slippage only
* paired comparison runs candidate and baseline on the identical frames
* shifted-duplicate probe: a copy of the entries moved a few bars is the SAME
  trade idea; it must not be credited as diversification
* capacity without liquidity/depth evidence is UNKNOWN, never a number

Diagnostics are descriptive (they never admit anything) but every scenario is
counted in `comparisons_made` so the search history stays honest.
"""
from __future__ import annotations

import numpy as np

from ...strategy.compile import compile_spec
from ...strategy.portfolio_evidence import Fill, portfolio_curve
from .. import evaluate as ev, referee
from . import digest
from .development import run_symbols

DELAYS = (0, 1, 2)
COST_MULTS = (1.0, 2.0, 3.0)
SHIFTS = (1, 2, 4, 8)


def _book(runs, bundle):
    """One account over all symbols, clocks aligned exactly as evaluate()."""
    clocks = {s: ev._clock(r['df']) for s, r in runs.items()}
    t0 = min(int(c[0]) for c in clocks.values())
    fills = [Fill(entry_i=int(clocks[s][f.entry_i] - t0), exit_i=int(clocks[s][f.exit_i] - t0),
                  r_multiple=f.r_multiple, symbol=s) for s, r in runs.items() for f in r['fills']]
    curve = portfolio_curve(fills, bundle.equity, bundle.risk_pct, bundle.max_open) if fills else None
    return dict(total_pct=round(curve['total_pct'], 3) if curve else 0.0,
                max_dd_pct=round(curve['max_dd_pct'], 3) if curve else 0.0, taken=curve['taken'] if curve else 0,
                trades=int(sum(r['result'].trades for r in runs.values())),
                median_pf=round(float(np.median([r['result'].profit_factor for r in runs.values()
                                                 if r['result'].trades])), 3) if any(r['result'].trades for r in runs.values()) else 0.0)


def entry_sets(c, bundle):
    compiled = compile_spec(c.to_spec())
    out = {}
    for sym in bundle.frames:
        lo, sh = compiled.entries(bundle.sym_frames[sym], btc=bundle.btc, derivs=bundle.derivs.get(sym),
                                  universe=bundle.universe, market=bundle.market, symbol=sym, world=bundle.world)
        out[sym] = set(np.flatnonzero(np.asarray(lo, bool) | np.asarray(sh, bool)).tolist())
    return out


def _overlap(a: dict, b: dict, tol: int) -> float:
    """Share of `a`'s entries within +-tol bars of an entry in `b`."""
    hit = total = 0
    for sym, ea in a.items():
        eb = b.get(sym, set())
        total += len(ea)
        hit += sum(1 for i in ea if any((i + d) in eb for d in range(-tol, tol + 1)))
    return hit / total if total else 0.0


def report(c, baseline, bundle, *, delays=DELAYS, cost_mults=COST_MULTS, shifts=SHIFTS, liquidity=None) -> dict:
    sample = dict(symbols=sorted(bundle.frames), bars={s: len(d) for s, d in bundle.frames.items()},
                  first={s: str(d['ts'].iloc[0]) for s, d in bundle.frames.items()},
                  equity=bundle.equity, risk_pct=bundle.risk_pct, max_open=bundle.max_open)
    sample_id = digest(sample)
    grid, n = [], 0
    for cm in cost_mults:
        for d in delays:
            grid.append(dict(cost_mult=cm, delay_bars=d, **_book(run_symbols(c, bundle, delay_bars=d, cost_mult=cm), bundle)))
            n += 1
    gone = next((g for g in grid if g['total_pct'] <= 0), None)
    paired = None
    if baseline is not None:
        a = run_symbols(c, bundle)
        b = run_symbols(baseline, bundle)
        per = {s: dict(candidate_pnl=round(a[s]['result'].pnl_usdt, 4), baseline_pnl=round(b[s]['result'].pnl_usdt, 4),
                       delta=round(a[s]['result'].pnl_usdt - b[s]['result'].pnl_usdt, 4)) for s in a}
        ca, cb = _book(a, bundle), _book(b, bundle)
        paired = dict(eligible_sample_id=sample_id, candidate=ca, baseline=cb,
                      total_pct_delta=round(ca['total_pct'] - cb['total_pct'], 3),
                      symbols_candidate_better=sum(1 for v in per.values() if v['delta'] > 0),
                      symbols=len(per), per_symbol=per, same_capital=True)
        n += 2
    own = entry_sets(c, bundle)
    probes = []
    for s in shifts:
        moved = {sym: {i + s for i in e} for sym, e in own.items()}
        probes.append(dict(shift_bars=s, overlap=round(_overlap(moved, own, 1), 4)))
    dup = [p for p in probes if p['overlap'] > referee.MAX_OVERLAP]
    return dict(schema='closure-robustness.v1', candidate_hash=c.hash, eligible_sample_id=sample_id,
                scenarios=grid, edge_disappears_at=dict(gone) if gone else None,
                paired_vs_baseline=paired,
                shifted_duplicate=dict(probes=probes, flagged_shifts=[p['shift_bars'] for p in dup],
                                       diversification_credit=not dup,
                                       note='a shifted copy of the same entries is one idea, not a new strategy'),
                capacity=dict(status='UNKNOWN', reason='no liquidity or order-book depth evidence supplied'
                              if liquidity is None else 'liquidity input is unverified and not modelled'),
                comparisons_made=n + len(probes), authority='DESCRIPTIVE_ONLY')


def distinct_from(a, b, bundle, tol: int = 1) -> dict:
    """Is `b` a different trade idea from `a` on the same eligible sample?"""
    ov = _overlap(entry_sets(b, bundle), entry_sets(a, bundle), tol)
    return dict(overlap=round(ov, 4), distinct=ov <= referee.MAX_OVERLAP, diversification_credit=ov <= referee.MAX_OVERLAP)
