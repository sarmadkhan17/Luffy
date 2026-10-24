"""Vectorized backtester.

The old engine called a Python evaluator once per bar: ~1.9 ms/bar/symbol, so
a 5-symbol x 8000-bar gauntlet cost ~76 s and had to run inside a 300 s brain
tick — which is why `gauntlet_max_full_runs` is 2. Here the entry signal is a
boolean array computed once, and the simulation touches only the sparse bars
where a trade actually opens.

The fill and accounting model is deliberately identical to backtest.py:137-208
— same pessimism (stop before target when both are touched intrabar), same
fee/slippage/funding arithmetic — so any difference the equivalence harness
finds is attributable to the new exit geometry, not to an accounting change.

Both engines start the drawdown peak at opening equity. The legacy engine
previously started at zero, hiding initial losses; its correction changes
max_dd_pct only. The equivalence criterion compares trades/wins/pnl.
"""
from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd

from ..agents.indicators import atr_series
from . import fill_timing
from .backtest import BacktestResult
from .portfolio_evidence import Fill
from .spec import ExitSpec

WARMUP = 210


def warm_window(a: int, b: int) -> tuple[slice, int]:
    """The slice to hand `simulate` for window [a, b) of a longer history,
    and the `score_from` that keeps its first bar the first scorable one.

    WARMUP is a bar count, so a window cut out of a history used to lose its
    first 210 bars to warm-up even when the bars before it were right there:
    a 4h 30-day decay window is 180 bars and could never see a trade.
    """
    lo = max(0, a - WARMUP)
    return slice(lo, b), a - lo


def _stop_distance(exit_spec: ExitSpec, ref_px: float, atr: float,
                   df: pd.DataFrame, i: int, side: str) -> float:
    kind = exit_spec.stop.get("kind", "atr")
    if kind == "atr":
        d = atr * float(exit_spec.stop.get("mult", 2.0))
    elif kind == "pct":
        d = ref_px * float(exit_spec.stop.get("v", 0.01))
    else:                                   # swing
        n = int(exit_spec.stop.get("lookback", 20))
        w = df.iloc[max(0, i - n):i + 1]
        d = (ref_px - float(w["low"].min())) if side == "long" \
            else (float(w["high"].max()) - ref_px)
    # same floor as the old engine, computed from the same reference price
    return max(d, ref_px * 0.004)


def _target_distance(exit_spec: ExitSpec, ref_px: float, atr: float,
                     stop_dist: float) -> float | None:
    t = exit_spec.target
    kind = t.get("kind", "rr")
    if kind == "none":
        return None
    if kind == "atr":
        return atr * float(t.get("mult", 3.0))
    if kind == "rr":
        return stop_dist * float(t.get("v", 2.0))
    return ref_px * float(t.get("v", 0.02))          # pct


def _trade(i, side, df, closes, highs, lows, atr, exit_spec, slip_frac, fee,
           max_bars, trail_mult, arm_at_r, exit_sig, fund_arr, funding_8h,
           bar_minutes, exit_evidence_out=None, intended_quantity=1.0, transitions_out=None,
           fill=None, exit_sig_available=None, detail_out=None):
    """One trade signalled on bar `i`: (exit_i, stop distance, P&L per unit
    of size). `fill` is `fill_timing.fills`: the entry fills at the OPEN of
    bar fill[i], and a declared signal exit seen on bar j at the open of bar
    fill[j]; with no array both default to the next bar. Signal ATR and swing
    context stop at bar `i`. A max_bars exit is a clock event, not a data
    decision, so it stays at the final held bar's close.
    Canonical signal intent remains separate from its execution evidence;
    active protection and next-bar trails continue while that intent waits.
    Every cost scales with size, so P&L = unit P&L x amount and the R
    multiple — unit P&L / stop distance — does not depend on the balance.
    `simulate` and the research rotation table both call this, so there is
    one definition of a trade."""
    sign = 1.0 if side == "long" else -1.0
    f = i + 1 if fill is None else int(fill[i])
    e = f - 1                       # the notional entry bar: the trade starts as it ends
    px = float(df["open"].iloc[f])
    slip = atr[i] * slip_frac
    entry = px + sign * slip
    if detail_out is not None:      # observation only: the ACTUAL slipped fill, never read back by the engine
        detail_out.update(entry=float(entry), entry_open=float(px), fill_bar=int(f))
    n = len(closes)
    # stop distance is measured from the CLOSE (as the old engine does),
    # then applied from the slipped entry
    sl_dist = _stop_distance(exit_spec, px, atr[i], df, i, side)
    tp_dist = _target_distance(exit_spec, px, atr[i], sl_dist)
    canonical = getattr(exit_spec, "_exit_semantics_id", None) is not None
    if canonical:
        from . import exit_policy as E
        from ..core.types import TF_MS
        if exit_spec._exit_semantics_id != E.EXIT_SEMANTICS_ID:
            raise ValueError("exit_semantics_mismatch")
        step = TF_MS[exit_spec._exit_timeframe]
        bar_minutes = step / 60_000
        clock = df["ts"].iloc[e]
        entry_ms = int(clock.timestamp()*1000) if hasattr(clock, "timestamp") else int(clock)
        policy, state = E.initialize(exit_spec, px, atr[i], side, entry_ms, step)
    sl = entry - sign * sl_dist
    tp = entry + sign * tp_dist if tp_dist is not None else None

    end = min(e + max_bars, n - 1)
    exit_i, exit_px = end, closes[end]
    best = entry
    due = -1                        # bar whose open fills a signalled exit
    at_open = 0
    pending_signal = None
    if canonical:
        # Scalar column reads: `df.iloc[j]` builds (and deep-copies attrs for)
        # a whole row per bar, which dominated every research backtest.
        ts_col, open_col = df['ts'].array, df['open'].to_numpy(float)
        quality_name = 'observation_quality' if df.attrs.get('read_mode') == 'retrospective' else 'quality'
        quality_col = df[quality_name].to_numpy() if quality_name in df.columns else None
        state_col = df['bar_state'].to_numpy() if 'bar_state' in df.columns else None
    for j in range(f, n if canonical else end + 1):
        if j == due:
            if canonical:
                clock = df['ts'].iloc[j]
                ms = int(clock.timestamp()*1000) if hasattr(clock, 'timestamp') else int(clock)
                executed = E.execute_open(policy, state, ms, float(df['open'].iloc[j]), intended_quantity)
                state = pending_signal if executed.reason == 'signal' else executed.state
            exit_i, exit_px, at_open = j, float(df["open"].iloc[j]), 1
            break
        if canonical:
            clock = ts_col[j]
            ms = int(clock.timestamp()*1000) if hasattr(clock, 'timestamp') else int(clock)
            signal = (bool(exit_sig[j]) if exit_sig is not None and exit_sig_available is not None
                      and exit_sig_available[j] else None)
            g = j + 1 if fill is None else int(fill[j])
            # A known but unexecutable signal cannot become a simulated fill.
            # Pending execution still leaves the active protection and trail in force.
            observation = E.Observation(ms, float(open_col[j]), highs[j], lows[j], closes[j],
                completed=bool((quality_col is None or quality_col[j] == 'VALID')
                               and (state_col is None or state_col[j] == 'FINAL')),
                atr=float(atr[j]), signal_exit=False if due >= 0 or (signal and not j < g < n) else signal)
            active_policy = replace(policy, signal_exit=False) if due >= 0 else policy
            transition = E.advance(active_policy, state, observation, intended_quantity)
            if transition.reason == 'signal':
                pending_signal, due = transition.state, g
                if transitions_out is not None:
                    transitions_out.append(transition)
                # The signal is an intent, not a terminal protective state. Advance
                # the surviving bar so its trail is effective while awaiting fill.
                transition = E.advance(replace(policy, signal_exit=False),
                                       state, replace(observation, signal_exit=False), intended_quantity)
            if transitions_out is not None:
                transitions_out.append(transition)
            state = transition.state
            if transition.reason == 'time' and due == j + 1:
                # A proven open at this exact boundary precedes the fallback.
                # A later/unavailable signal fill never suspends max_bars.
                state = replace(state, reason=None, reference_exit=None)
                continue
            if transition.due:
                exit_i, exit_px = j, state.reference_exit
                break
            continue
        if trail_mult:
            best = max(best, highs[j]) if side == "long" \
                else min(best, lows[j])
            if abs(best - entry) >= arm_at_r * sl_dist:
                trailed = best - sign * trail_mult * atr[j]
                sl = max(sl, trailed) if side == "long" \
                    else min(sl, trailed)
        hit_sl = lows[j] <= sl if side == "long" else highs[j] >= sl
        hit_tp = tp is not None and (
            highs[j] >= tp if side == "long" else lows[j] <= tp)
        if hit_sl:                                    # pessimistic: stop first
            exit_i, exit_px = j, sl
            break
        if hit_tp:
            exit_i, exit_px = j, tp
            break
        if exit_sig is not None and exit_sig[j] and due < 0:
            g = j + 1 if fill is None else int(fill[j])
            if g > j:                                 # unexecutable signals cannot exit
                due = g

    if canonical:
        if exit_evidence_out is not None:
            exit_evidence_out.append({'entry_i': e, 'exit_i': exit_i if state.reason else None,
                'exit_semantics_id': E.EXIT_SEMANTICS_ID, 'state': json.loads(E.encode(policy,state)),
                'censored': not bool(state.reason), 'authority': 'STRATEGY',
                'fill_i': exit_i if state.reason else None,
                'fill_price': float(exit_px) if state.reason else None, 'at_open': bool(at_open)})
        if not state.reason:
            return -2, sl_dist, float('nan')
    exit_px = exit_px - sign * slip                   # exits pay the spread too
    gross = (exit_px - entry) * sign
    fees = fee * (entry + exit_px)
    held_to = exit_i - at_open      # an open fill ends the previous bar
    hours = (held_to - e) * bar_minutes / 60.0
    if fund_arr is None:
        carry = abs(funding_8h) * (hours / 8.0)
        basis = 'flat_estimate'
    else:
        held = fund_arr[e:held_to]
        known = held[np.isfinite(held)]
        per_bar = bar_minutes / 60.0 / 8.0
        # the real signed rate where the venue told us, the flat
        # conservative charge for every bar it did not
        carry = float(np.sum(known) * sign * per_bar) \
            + abs(funding_8h) * (len(held) - len(known)) * per_bar
        basis = 'actual_signed' if len(known) == len(held) else 'mixed_actual_and_flat'
    if detail_out is not None:
        detail_out.update(exit_px=float(exit_px), exit_bar=int(exit_i), held_to=int(held_to), fees_per_unit=float(fees),
                          carry_per_unit=float(carry * exit_px), funding_basis=basis)
    return exit_i, sl_dist, gross - fees - carry * exit_px


ENTRY_REFUSAL = 'exit_geometry_nonpositive'


def _try_trade(*args, **kw):
    """`_trade`, or None when the canonical exit policy refuses THIS entry for
    unexecutable price geometry (a stop or target at or below zero), as the live
    entry path does: nothing fills, no cost or exposure, later signals still
    evaluate. Every other error, including an invalid reference price or ATR,
    propagates. Both `simulate` and `trade_table` enter through here so the
    same entries are eligible in each."""
    try:
        return _trade(*args, **kw)
    except ValueError as e:
        if str(e) != ENTRY_REFUSAL:
            raise
        return None


def trade_table(df: pd.DataFrame, exit_spec: ExitSpec, risk_cfg: dict,
                funding: np.ndarray | None = None,
                first_bar: int = 0, exit_sig=None, exit_sig_available=None) -> dict:
    """Every bar's trade, both sides, computed once: {side: (exit_i, r,
    px_over_stop)} arrays with exit_i = -1 where no trade can open.

    A rotation of the entries re-uses these outcomes instead of re-running
    the engine, which is what makes thousands of null draws affordable.
    `simulate` over the same entries is reproduced exactly by
    `walk_table` — tests/test_research_portfolio_null.py holds that."""
    n = len(df)
    fee = float(risk_cfg.get("taker_fee_pct", 0.05)) / 100.0
    slip_frac = float(risk_cfg.get("slippage_atr_frac", 0.06))
    funding_8h = float(risk_cfg.get("funding_rate_8h", 0.0001))
    bar_minutes = float(risk_cfg.get("bar_minutes", 15))
    fund_arr = None
    if funding is not None:
        fund_arr = np.asarray(funding, dtype=float)
        if len(fund_arr) != n:
            fund_arr = None
    closes = df["close"].to_numpy(float)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    atr = atr_series(df, 14).to_numpy(float)
    if (getattr(exit_spec, "_exit_semantics_id", None) is not None
            and exit_spec.trail.get("kind") == "atr"):
        from .exit_policy import trail_atr_series
        from ..core.types import TF_MS
        atr = trail_atr_series(df, TF_MS[exit_spec._exit_timeframe]).to_numpy(float)
    max_bars = int(exit_spec.time.get("max_bars", 32) or 32)
    trail = exit_spec.trail or {"kind": "none"}
    trail_mult = float(trail.get("mult", 0.0)) \
        if trail.get("kind") == "atr" else 0.0
    arm_at_r = float(trail.get("arm_at_r", 1.0))
    fill, _ = fill_timing.fills(df)
    out = {}
    for side in ("long", "short"):
        ex = np.full(n, -1, dtype=np.int64)
        r = np.zeros(n)
        pos = np.zeros(n)
        for i in range(max(WARMUP, int(first_bar)), n - 1):
            if fill[i] < 0 or not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            trade = _try_trade(
                i, side, df, closes, highs, lows, atr, exit_spec, slip_frac,
                fee, max_bars, trail_mult, arm_at_r, exit_sig, fund_arr,
                funding_8h, bar_minutes, fill=fill, exit_sig_available=exit_sig_available)
            if trade is None:               # refused entry: no trade can open here
                continue
            e, sl_dist, unit = trade
            ex[i], r[i], pos[i] = e, unit / sl_dist, closes[i] / sl_dist
        out[side] = (ex, r, pos)
    return out


def walk_table(table: dict, long: np.ndarray, short: np.ndarray,
               risk_cfg: dict, equity: float = 2000.0) -> list:
    """`simulate`'s entry walk over a `trade_table`: [(entry_i, exit_i, r)].
    One position at a time, long wins a bar both sides fire on, and the
    dust guard replays against the leg's own running balance."""
    risk_frac = float(risk_cfg["risk_per_trade_pct"]) / 100.0
    lex, lr, lpos = table["long"]
    sex, sr, spos = table["short"]
    n = len(lex)
    eq, cursor, out = float(equity), WARMUP, []
    for i in np.flatnonzero(long | short):
        i = int(i)
        if i < cursor or i >= n - 1:
            continue
        ex, r, pos = (lex, lr, lpos) if long[i] else (sex, sr, spos)
        if ex[i] == -1:
            continue
        staked = eq * risk_frac
        if staked * pos[i] < 10:                      # dust guard
            continue
        if ex[i] == -2:  # censored position occupies the rest of this slice
            break
        out.append((i, int(ex[i]), float(r[i])))
        eq += r[i] * staked
        cursor = int(ex[i]) + 1
    return out


def simulate(long: np.ndarray, short: np.ndarray, df: pd.DataFrame,
             exit_spec: ExitSpec, risk_cfg: dict, equity: float = 2000.0,
             genome_id: str = "", symbol: str = "BT",
             exit_sig: np.ndarray | None = None,
             funding: np.ndarray | None = None,
             fills_out: list | None = None,
             score_from: int = 0, exit_evidence_out=None, exit_sig_available=None,
             fill_delay_bars: int = 0, trace_out: list | None = None) -> BacktestResult:
    """`funding`, when given, is the SIGNED 8-hourly rate per bar.

    Without it the engine charges abs(flat rate) to both sides, which bills a
    short for carry it would actually receive. That is conservative, so it is
    the default and stays the default wherever the real series is unknown —
    a NaN bar falls back to it rather than to a fabricated zero. With the
    real series a long pays a positive rate and a short is paid it, which is
    what the venue does.

    `score_from` is where the scored window begins inside `df`. A caller that
    judges a window of a longer history passes the window plus up to WARMUP
    bars of the history before it, so indicators and ATR warm on real bars
    and only the window's own bars can open a trade. WARMUP still floors it:
    with no prior history the first WARMUP bars stay unscored, never faked.
    """
    score_from = max(WARMUP, int(score_from))
    res = BacktestResult(genome_id=genome_id, symbol=symbol, bars=len(df))
    fee = float(risk_cfg.get("taker_fee_pct", 0.05)) / 100.0
    slip_frac = float(risk_cfg.get("slippage_atr_frac", 0.06))
    risk_frac = float(risk_cfg["risk_per_trade_pct"]) / 100.0
    funding_8h = float(risk_cfg.get("funding_rate_8h", 0.0001))
    bar_minutes = float(risk_cfg.get("bar_minutes", 15))
    fund_arr = None
    if funding is not None:
        fund_arr = np.asarray(funding, dtype=float)
        if len(fund_arr) != len(df):
            fund_arr = None                 # misaligned is unknown, not zero

    n = len(df)
    if n <= score_from + 1:
        return res
    closes = df["close"].to_numpy(float)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    atr = atr_series(df, 14).to_numpy(float)
    if (getattr(exit_spec, "_exit_semantics_id", None) is not None
            and exit_spec.trail.get("kind") == "atr"):
        from .exit_policy import trail_atr_series
        from ..core.types import TF_MS
        atr = trail_atr_series(df, TF_MS[exit_spec._exit_timeframe]).to_numpy(float)

    max_bars = int(exit_spec.time.get("max_bars", 32) or 32)
    trail = exit_spec.trail or {"kind": "none"}
    trail_mult = float(trail.get("mult", 0.0)) \
        if trail.get("kind") == "atr" else 0.0
    arm_at_r = float(trail.get("arm_at_r", 1.0))

    res.exit_semantics_id = getattr(exit_spec, "_exit_semantics_id", None)
    trace = exit_evidence_out if exit_evidence_out is not None else ([] if res.exit_semantics_id else None)
    equity_curve = [equity]
    peak = equity
    candidates = np.flatnonzero(long | short)
    cursor = score_from
    fill, _ = fill_timing.fills(df)
    if type(fill_delay_bars) is not int or fill_delay_bars < 0:
        raise ValueError('fill_delay_bars must be a non-negative integer')
    if fill_delay_bars:
        # Execution-latency stress: the SIGNAL (ATR, stop geometry, exit intent)
        # is unchanged; only the bar each entry/exit fills on moves later.
        fill = np.where(fill >= 0, fill + int(fill_delay_bars), fill)
        fill = np.where(fill >= n, -1, fill)

    def mark_funding(i, f, end, side):
        # Same accrual convention as _trade: one bar's signed eight-hour rate
        # allocated by bar duration, with the existing conservative missing-rate estimate.
        sign = 1.0 if side == "long" else -1.0
        rates = np.full(max(end - (f - 1), 0), abs(funding_8h))
        if fund_arr is not None:
            raw = fund_arr[f - 1:end]
            rates = np.where(np.isfinite(raw), raw * sign, abs(funding_8h))
        step_minutes = bar_minutes
        if getattr(exit_spec, "_exit_semantics_id", None) is not None:
            from ..core.types import TF_MS
            step_minutes = TF_MS[exit_spec._exit_timeframe] / 60_000
        prefix = np.cumsum(rates) * step_minutes / 480.0
        return tuple(float(v) for v in prefix[:max(end - f, 0)])

    for raw_i in candidates:
        i = int(raw_i)
        if i < cursor or i >= n - 1 or fill[i] < 0:
            continue
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        side = "long" if long[i] else "short"
        px = closes[i]
        detail = {} if trace_out is not None else None
        trade = _try_trade(
            i, side, df, closes, highs, lows, atr, exit_spec, slip_frac,
            fee, max_bars, trail_mult, arm_at_r, exit_sig, fund_arr,
            funding_8h, bar_minutes, trace, fill=fill, exit_sig_available=exit_sig_available, detail_out=detail)
        if trade is None:                   # refused before any fill, cost or exposure
            res.errors.append(f'entry_refused:{ENTRY_REFUSAL}:{side}:{i}')
            continue
        exit_i, sl_dist, unit_pnl = trade
        amount = (equity_curve[-1] * risk_frac) / sl_dist
        if amount * px < 10:                          # dust guard
            continue
        if exit_i == -2:
            res.censored_position_count += 1
            if trace_out is not None:           # opt-in position trace: the still-open position, as the engine held it
                trace_out.append(dict(symbol=symbol, entry_i=i, fill_i=int(fill[i]), exit_i=None, side=side,
                                      entry_px=detail['entry'], signal_close_px=float(px), entry_open_px=detail['entry_open'],
                                      sl_dist=float(sl_dist), amount=float(amount),
                                      stake=float(equity_curve[-1] * risk_frac), fee_rate=fee, slip_frac=slip_frac,
                                      atr=float(atr[i]), open_at_end=True, pnl_usdt=None,
                                      mark_funding_rate=mark_funding(i, int(fill[i]), n, side)))
            break  # open through dataset end; subsequent entries cannot open
        if trace is not None and trace:
            evidence = trace[-1]
            state = evidence.get('state', {}).get('state', {})
            reason = state.get('reason')
            if reason:
                res.exit_reasons[reason] = res.exit_reasons.get(reason, 0) + 1
            if state.get('ambiguous'):
                res.ambiguous_exit_count += 1
        pnl = unit_pnl * amount

        if fills_out is not None:
            # R is measured against the equity actually staked at THIS entry,
            # so a portfolio walk can re-risk a different fraction of a
            # different balance and still reproduce the arithmetic
            staked = equity_curve[-1] * risk_frac
            fills_out.append(Fill(entry_i=i, exit_i=exit_i, symbol=symbol,
                                  r_multiple=pnl / staked if staked else 0.0))
        if trace_out is not None:
            trace_out.append(dict(symbol=symbol, entry_i=i, fill_i=int(fill[i]), exit_i=int(exit_i), side=side,
                                  entry_px=detail['entry'], signal_close_px=float(px), entry_open_px=detail['entry_open'],
                                  exit_px=detail['exit_px'], fees_per_unit=detail['fees_per_unit'],
                                  carry_per_unit=detail['carry_per_unit'], funding_basis=detail['funding_basis'],
                                  sl_dist=float(sl_dist), amount=float(amount),
                                  stake=float(equity_curve[-1] * risk_frac), fee_rate=fee, slip_frac=slip_frac,
                                  atr=float(atr[i]), open_at_end=False, pnl_usdt=float(pnl),
                                  mark_funding_rate=mark_funding(i, int(fill[i]), int(exit_i), side)))
        res.trades += 1
        res.pnl_usdt += pnl
        if pnl > 0:
            res.wins += 1
            res.gross_win += pnl
        else:
            res.losses += 1
            res.gross_loss += abs(pnl)
        equity_curve.append(equity_curve[-1] + pnl)
        peak = max(peak, equity_curve[-1])
        if peak > 0:
            res.max_dd_pct = max(res.max_dd_pct,
                                 (peak - equity_curve[-1]) / peak * 100)
        cursor = exit_i + 1                           # one position at a time

    return res


def vector_backtest(compiled, frames: dict, risk_cfg: dict, btc=None,
                    derivs=None, equity: float = 2000.0,
                    symbol: str = "BT", universe=None, market=None) -> BacktestResult:
    _require_replay(frames)
    _require_replay(btc)
    for member in (universe or {}).values():
        _require_replay(member)
    df = frames[compiled.spec.timeframe]
    if df.attrs.get('read_mode') == 'retrospective':
        from ..data.feed import norm_symbol
        declared = df.attrs.get('symbol')
        if symbol == 'BT':
            if not declared:
                raise ValueError('retrospective backtest requires explicit market symbol')
            symbol = declared
        elif declared and norm_symbol(symbol) != declared:
            raise ValueError('retrospective symbol binding mismatch')
    lo, sh = compiled.entries(frames, btc=btc, derivs=derivs, symbol=symbol,
                              universe=universe, market=market)
    ex, ex_available = compiled.exit_signal_detail(frames, btc=btc, derivs=derivs, symbol=symbol,
                              universe=universe, market=market)
    df = frames[compiled.spec.timeframe]
    fund = funding_for(symbol, df, risk_cfg) if df.attrs.get('read_mode') == 'retrospective' else None
    result = simulate(lo, sh, df, compiled.spec.exit, risk_cfg, equity=equity,
                      genome_id=compiled.spec.id, symbol=symbol, exit_sig=ex, funding=fund, exit_sig_available=ex_available)
    if df.attrs.get('read_mode') == 'retrospective':
        from . import quant_contract
        from ..data.market_provenance import digest
        inputs = {'frames':frames, 'btc':btc or {}, 'derivs':derivs or {},
                  'universe':universe or {}, 'market':market or {}}
        if fund is not None:
            inputs['funding_cost'] = df[['ts']].assign(value=fund)
        contract = quant_contract.contract(compiled.spec, inputs, risk_cfg)
        result.evidence = {'quant_contract':contract,
            'evaluation_identity':digest({'run':contract['run']['identity'], 'equity':equity,
                                          'compiler_identity':compiled.identity}),
            'evidence_scope':'retrospective_observation', 'factory_validated':False}
    return result


#: (symbol, first bar, bar count) -> aligned signed funding, or None
_FUNDING_CACHE: dict = {}


def _require_replay(frames):
    for df in (frames or {}).values():
        if df is not None and 'revision_id' in df and df.attrs.get('read_mode') not in ('replay', 'retrospective'):
            raise ValueError('historical engine requires retained bar-cut replay')
        if df is not None and 'quality' in df and not df['observation_quality' if df.attrs.get('read_mode') == 'retrospective' else 'quality'].eq('VALID').all():
            raise ValueError('historical engine requires valid market evidence')


def funding_for(symbol: str, df, risk_cfg: dict):
    """The real signed funding series for this frame, or None to charge flat.

    Off when `real_funding` is false, and off for the synthetic symbols the
    equivalence harness uses. Cached per frame because a gauntlet scores the
    same symbol many times.
    """
    if getattr(df, 'attrs', {}).get('read_mode') == 'retrospective' and risk_cfg.get('real_funding', True) and (not symbol or symbol == 'BT'):
        raise ValueError('retrospective measured funding requires explicit market symbol')
    if not risk_cfg.get("real_funding", True) or not symbol or symbol == "BT":
        return None
    bound = getattr(df, 'attrs', {}).get('research_funding_binding')
    if bound is not None:
        from ..data.market_provenance import digest, ms
        rates = np.asarray(bound['rates'], dtype=float)
        if (bound.get('symbol') != symbol or len(rates) != len(df) or not np.isfinite(rates).all()
                or not bound.get('source_id') or bound.get('timestamp_id') != digest(ms(df['ts']).tolist())):
            raise ValueError('research funding binding does not match the scored frame')
        return rates.copy()
    if df is None or not len(df) or "ts" not in getattr(df, "columns", ()):
        return None
    if df.attrs.get('research_read_paths') is not None:
        from . import spec_evidence
        return spec_evidence.funding_series(symbol, df)
    if df.attrs.get('read_mode') == 'retrospective':
        # A candle-only cache key cannot authenticate dependency corrections.
        # Read the explicitly bound retained settlements each time instead.
        from . import spec_evidence
        return spec_evidence.funding_series(symbol, df)
    from ..data.market_provenance import digest
    key = (symbol, str(df["ts"].iloc[0]), len(df),
           df.attrs.get('as_of_ms'), df.attrs.get('dataset_id'), df.attrs.get('input_identity'),
           digest(df.attrs.get('retrospective')),digest(df['revision_id'].tolist()) if 'revision_id' in df else None)
    if key not in _FUNDING_CACHE:
        try:
            from . import spec_evidence
            _FUNDING_CACHE[key] = spec_evidence.funding_series(symbol, df)
        except Exception:
            if df.attrs.get('read_mode') == 'retrospective':
                raise
            _FUNDING_CACHE[key] = None
    return _FUNDING_CACHE[key]


def vector_walk_forward(compiled, frames: dict, risk_cfg: dict,
                        split: float = 0.7, btc=None, derivs=None,
                        symbol: str = "BT", universe=None, market=None) -> dict:
    """Same contract as backtest.walk_forward so evidence.py can call either.

    Signals are computed ONCE over the full frame and then sliced, so an
    indicator near the split boundary is warmed up exactly as it would be
    live — the old engine re-ran the evaluator on a truncated frame, which
    cost the test half its first 210 bars. Handing `simulate` the bare half
    still cost them, until `warm_window` (2026-09-14).
    """
    _require_replay(frames)
    _require_replay(btc)
    for member in (universe or {}).values():
        _require_replay(member)
    tf = compiled.spec.timeframe
    df = frames[tf]
    if df.attrs.get('read_mode') == 'retrospective':
        from ..data.feed import norm_symbol
        declared = df.attrs.get('symbol')
        if symbol == 'BT':
            if not declared:
                raise ValueError('retrospective walk-forward requires explicit market symbol')
            symbol = declared
        elif declared and norm_symbol(symbol) != declared:
            raise ValueError('retrospective symbol binding mismatch')
    lo, sh = compiled.entries(frames, btc=btc, derivs=derivs,
                              universe=universe, market=market, symbol=symbol)
    ex, ex_available = compiled.exit_signal_detail(frames, btc=btc, derivs=derivs,
                              universe=universe, market=market, symbol=symbol)
    cut = int(len(df) * split)
    fund = funding_for(symbol, df, risk_cfg)

    def run(a, b):
        sl, score_from = warm_window(a, b)
        return simulate(lo[sl], sh[sl], df.iloc[sl].reset_index(drop=True),
                        compiled.spec.exit, risk_cfg,
                        genome_id=compiled.spec.id, symbol=symbol,
                        exit_sig=None if ex is None else ex[sl],
                        exit_sig_available=None if ex_available is None else ex_available[sl],
                        funding=None if fund is None else fund[sl],
                        score_from=score_from)

    train, test = run(0, cut), run(cut, len(df))
    ok_train, f_train = train.passes()
    ok_test, f_test = test.passes(min_trades=max(3, int(train.trades * 0.2)),
                                  min_pf=1.0)
    result = {"train": train, "test": test, "robust": ok_train and ok_test,
              "train_fails": f_train, "test_fails": f_test}
    if df.attrs.get('read_mode') == 'retrospective':
        from . import quant_contract
        from ..data.market_provenance import digest
        inputs = {'frames':frames, 'btc':btc or {}, 'derivs':derivs or {},
                  'universe':universe or {}, 'market':market or {}}
        if fund is not None:
            inputs['funding_cost'] = df[['ts']].assign(value=fund)
        result['quant_contract'] = quant_contract.contract(compiled.spec, inputs, risk_cfg)
        result['evaluation_identity'] = digest({'run':result['quant_contract']['run']['identity'], 'split':split,
                                                'compiler_identity':compiled.identity})
        result.update(evidence_scope='retrospective_observation', factory_validated=False)
    return result
