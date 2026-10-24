"""A7/A9 and §7 diagnostics on a development candidate: regime-conditional
behaviour with sample sufficiency and transitions, open-position
mark-to-market drawdown beside the closed-trade drawdown (the legacy field is
unchanged), and portfolio contribution against a declared incumbent book.

Everything is read from the SAME simulate() run the development verdict used:
`trace_out` carries each position's ACTUAL side, entry price, quantity, stake,
costs and the still-open end position. Marks are close-marked estimates of an
open position (entry costs charged); a missing price is an UNKNOWN mark, never
zero, and a bar with any unknown open mark has UNKNOWN equity. Closed-trade
fields keep their meaning and the marked curve is reconciled against them.
"""
from __future__ import annotations

from heapq import heappop, heappush

import numpy as np

from ...agents import indicators as ind
from ...strategy import dsl
from ...strategy.portfolio_evidence import portfolio_curve
from . import catalog

MIN_REGIME_TRADES = 30          # descriptive sufficiency only; it never grants a conditional claim
TRANSITION_LOOKBACK = 6


def role_report(candidate, ctxs, sims):
    """Predeclared factor roles measured on development trades. Tail comparisons describe historical
    conditions, not synthetic forecasts or qualified conditional claims. They never alter entries."""
    out = {}
    for key, role in (candidate.get('roles') or {}).items():
        values, returns, finite, total = [], [], 0, 0
        for sym, ctx in ctxs.items():
            raw = dsl.evaluate(dsl.parse(catalog.BY_KEY[key].formula), ctx)
            arr = raw.to_numpy(dtype=float, na_value=np.nan) if hasattr(raw, 'to_numpy') else np.full(len(ctx.index), float(raw))
            total += len(arr)
            finite += int(np.isfinite(arr).sum())
            for fill in sims[sym]['fills']:
                if np.isfinite(arr[fill.entry_i]):
                    values.append(float(arr[fill.entry_i]))
                    returns.append(float(fill.r_multiple))
        row = dict(role=role, formula=catalog.BY_KEY[key].formula, observations=total, finite=finite,
                   trades=len(values), evidence_scope='DEVELOPMENT_DESCRIPTIVE', qualified_claim=False)
        if len(values) >= 2:
            x, y = np.asarray(values), np.asarray(returns)
            lo, hi = np.quantile(x, [.1, .9])
            row.update(development_tail_cuts=[float(lo), float(hi)],
                       lower_condition=dict(trades=int((x <= lo).sum()), mean_net_r=float(y[x <= lo].mean())),
                       upper_condition=dict(trades=int((x >= hi).sum()), mean_net_r=float(y[x >= hi].mean())))
            row['interpretation'] = ('historical tail-condition stress comparison; no unobserved shock or future forecast'
                                     if role == 'stress' else 'observed factor association with actual cost-inclusive trade outcomes')
            row['association'] = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 and y.std() > 0 else None
        else:
            row['reason'] = 'insufficient matched trades; no factor-effect claim'
        out[key] = row
    return out


def _state_series(ctx) -> np.ndarray:
    """trend/range x volatility. Any NaN indicator -> 'unknown' (never silently 'mixed' or 'normalvol')."""
    def raw(k):
        v = dsl.evaluate(dsl.parse(catalog.BY_KEY[k].formula), ctx)
        return v
    n = len(ctx.index)
    er = ctx.get('efficiency_ratio', (30,)).to_numpy(float)
    adx = ctx.get('adx', (14,)).to_numpy(float)
    rank = ctx.get('atr_pct_rank', (200,)).to_numpy(float)
    out = np.full(n, 'unknown', dtype=object)
    known = np.isfinite(er) & np.isfinite(adx) & np.isfinite(rank)
    trend = np.where((er > 0.3) & (adx > 20), 'trend', np.where((er < 0.2) & (adx < 20), 'range', 'mixed'))
    vol = np.where(rank > 0.75, 'highvol', 'normalvol')
    out[known] = [f'{a}/{b}' for a, b in zip(trend[known], vol[known])]
    return out


def regime_report(fills_by_symbol: dict, ctxs: dict, *, gated: bool = False, min_trades: int = MIN_REGIME_TRADES) -> dict:
    """Descriptive per-regime net R at entry with persistence/transition split. These counts DESCRIBE the sample: they
    never grant a conditional claim. A regime-conditional rule needs its own revision evaluated separately."""
    rows = []
    for sym, fills in fills_by_symbol.items():
        states = _state_series(ctxs[sym])
        for f in fills:
            i = int(f.entry_i)
            prev = states[max(0, i - TRANSITION_LOOKBACK)]
            rows.append((states[i], states[i] != prev, float(f.r_multiple)))
    out = {}
    for st in sorted({r[0] for r in rows}):
        rs = np.array([r[2] for r in rows if r[0] == st])
        tr = np.array([r[2] for r in rows if r[0] == st and r[1]])
        out[st] = dict(trades=int(len(rs)), mean_r=round(float(rs.mean()), 4), win_rate=round(float((rs > 0).mean()), 3),
                       transition_entries=int(len(tr)), transition_mean_r=round(float(tr.mean()), 4) if len(tr) else None,
                       descriptive_sample_sufficient=bool(len(rs) >= min_trades), conditional_claim='NOT_ESTABLISHED')
    return dict(states=out, min_trades=min_trades, total_trades=len(rows),
                unseen_state_policy='ABSTAIN_ENFORCED_BY_GATE' if gated else 'NOT_ENFORCED: candidate carries no regime gate',
                note='descriptive; unknown = an indicator was NaN; a regime filter found here is a NEW development revision')


def clocks(frames: dict):
    """({symbol: UTC seconds per local bar}, t0): the evaluator's own common source clock (evaluate._clock, earliest
    first bar across the frames). Local bar indices differ across symbols with unequal starts or holes; UTC does not."""
    from ..evaluate import _clock
    c = {s: _clock(df) for s, df in frames.items()}
    return c, min(int(v[0]) for v in c.values())


def common_fills(fills_by_symbol: dict, frames: dict) -> list:
    """Fills re-stamped onto the shared UTC clock exactly as evaluate() does, so portfolio admission/settlement order
    is real time, not coincident local indices. Local-index fills stay available to callers for frame lookups."""
    from ...strategy.portfolio_evidence import Fill
    c, t0 = clocks(frames)
    return [Fill(entry_i=int(c[s][f.entry_i] - t0), exit_i=int(c[s][f.exit_i] - t0), r_multiple=f.r_multiple, symbol=s)
            for s, fs in fills_by_symbol.items() for f in fs]


def _positions(traces: list, frames: dict, clk: dict, t0: int) -> list:
    out = []
    for t in traces:
        c = clk[t['symbol']]
        close = frames[t['symbol']]['close'].to_numpy(float)
        i0 = max(t['entry_i'], t['fill_i'])
        end = t['exit_i'] if t['exit_i'] is not None else len(close)
        sgn = 1.0 if t['side'] == 'long' else -1.0
        # entry_px is the engine's ACTUAL slipped fill (open of the fill bar +/- ATR slippage): slippage is already in it,
        # so the entry fee and the engine's accrued funding convention are charged here.
        # Exit costs enter realized R at close; a held position has no exit fill yet.
        cost = t['amount'] * t['entry_px'] * t['fee_rate']
        r = np.full(end - i0, np.nan)
        px = close[i0:end]
        ok = np.isfinite(px)
        carry = np.asarray(t.get('mark_funding_rate', np.zeros(len(px))), dtype=float)
        if len(carry) != len(px):
            raise ValueError('marked funding does not match held bars')
        ok &= np.isfinite(carry)
        r[ok] = (sgn * (px[ok] - t['entry_px']) * t['amount'] - cost -
                 carry[ok] * px[ok] * t['amount']) / t['stake'] if t['stake'] else np.nan
        out.append(dict(t, i0=i0, end=end, path=r, r_final=None if t['open_at_end'] else t['pnl_usdt'] / t['stake'],
                        entry_t=int(c[t['entry_i']] - t0), exit_t=None if t['exit_i'] is None else int(c[t['exit_i']] - t0)))
    return sorted(out, key=lambda p: (p['entry_t'], p['symbol']))


def mtm_report(traces: list, frames: dict, fills: list, equity: float, risk_pct: float, max_open: int) -> dict:
    """Marked equity from the engine's own position trace on ONE UTC clock. `fills` must already be on the shared clock
    (common_fills). Admission/settlement follow portfolio_curve (same ordering key, exits settle before same-time entries).
    Open-at-end positions are included; unknown marks keep that bar's equity UNKNOWN."""
    closed = portfolio_curve(fills, equity, risk_pct, max_open)
    clk, t0 = clocks(frames)
    grid = np.unique(np.concatenate([v - t0 for v in clk.values()]))
    n = len(grid)
    at = {s: np.searchsorted(grid, v - t0) for s, v in clk.items()}          # local bar -> grid slot
    eq, pending, accepted = float(equity), [], []
    from math import fsum
    for p in _positions(traces, frames, clk, t0):
        # exits at one timestamp settle as ONE batch with the equity floor applied once (as portfolio_curve does)
        while pending and pending[0][0] <= p['entry_t']:
            when, batch = pending[0][0], []
            while pending and pending[0][0] == when:
                batch.append(heappop(pending)[2])
            eq = max(eq + fsum(batch), 1e-9)
        if len(pending) >= max_open:
            continue
        stake = eq * risk_pct / 100.0
        heappush(pending, (p['exit_t'] if p['exit_t'] is not None else 10 ** 12, len(accepted),
                           stake * (p['r_final'] if p['r_final'] is not None else 0.0)))
        accepted.append((p, stake))
    realized, unreal, opens = np.zeros(n), np.zeros(n), np.zeros(n, int)
    unknown = np.zeros(n, bool)
    for p, stake in accepted:
        a = at[p['symbol']]
        if p['exit_i'] is not None:
            realized[a[p['exit_i']]:] += stake * p['r_final']
        marked_slots = {}
        for k, r in zip(range(p['i0'], p['end']), p['path']):
            marked_slots[int(a[k])] = r
        # The position is HELD on every grid slot from its first mark until its exit slot (or the end of the grid for a
        # censored one). A slot where this symbol has no bar, or a missing price, is an UNKNOWN mark: never zero, never flat.
        first = int(a[p['i0']]) if p['i0'] < len(a) else n
        last = int(a[p['exit_i']]) if p['exit_i'] is not None else n
        for g in range(first, min(last, n)):
            opens[g] += 1
            r = marked_slots.get(g)
            if r is not None and np.isfinite(r):
                unreal[g] += stake * r
            else:
                unknown[g] = True
    marked = equity + realized + unreal
    marked[unknown] = np.nan
    ok = np.isfinite(marked)
    peak = np.maximum.accumulate(np.where(ok, marked, -np.inf))
    dd = float(((peak[ok] - marked[ok]) / peak[ok]).max() * 100.0) if ok.any() else None
    closed_curve = equity + realized
    cp = np.maximum.accumulate(closed_curve)
    closed_dd = float(((cp - closed_curve) / cp).max() * 100.0)
    open_end = [p for p, _ in accepted if p['open_at_end']]
    return dict(closed_max_dd_pct=round(closed['max_dd_pct'], 3), mtm_max_dd_pct=None if dd is None else round(dd, 3),
                reconciled=bool(abs(closed_dd - closed['max_dd_pct']) < 1e-6 and
                                len([p for p, _ in accepted if not p['open_at_end']]) == closed['taken']),
                unknown_mark_bars=int(unknown.sum()), open_at_end_positions=len(open_end),
                max_concurrent_open=int(opens.max()), mean_open=round(float(opens.mean()), 3), taken=closed['taken'],
                clock='UTC seconds from the earliest frame start (same as evaluate); local indices only index each frame',
                label='MARKED_FROM_ENGINE_TRACE: actual slipped fill price/side/qty; close-marked; entry fee charged; '
                      'funding accrues using the engine cost convention; exit costs enter realized R at close; '
                      'unknown marks stay unknown')


def contribution(book_fills: list, candidate_fills: list, equity: float, risk_pct: float, max_open: int) -> dict:
    """Portfolio contribution at the SAME capital/risk/concurrency: book alone vs book + candidate."""
    b = portfolio_curve(book_fills, equity, risk_pct, max_open) if book_fills else None
    c = portfolio_curve(book_fills + candidate_fills, equity, risk_pct, max_open)
    solo = portfolio_curve(candidate_fills, equity, risk_pct, max_open) if candidate_fills else None
    return dict(book=None if b is None else dict(total_pct=round(b['total_pct'], 3), max_dd_pct=round(b['max_dd_pct'], 3), taken=b['taken']),
                with_candidate=dict(total_pct=round(c['total_pct'], 3), max_dd_pct=round(c['max_dd_pct'], 3), taken=c['taken']),
                candidate_alone=None if solo is None else dict(total_pct=round(solo['total_pct'], 3), max_dd_pct=round(solo['max_dd_pct'], 3)),
                delta_total_pct=None if b is None else round(c['total_pct'] - b['total_pct'], 3),
                delta_max_dd_pct=None if b is None else round(c['max_dd_pct'] - b['max_dd_pct'], 3))
