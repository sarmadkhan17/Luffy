"""Factory exit intent, independent of fills, orders, protection and costs.

Observation contract: consecutive COMPLETED bars on the declared timeframe.
Entry geometry uses signal-bar close and ATR(14) through that closed bar.
An open beyond a level precedes range checks. Otherwise OHLC has no intrabar
sequence: stop wins a both-hit range, with ambiguity recorded explicitly.
Price exits precede a declared signal decision, which precedes max_bars. A surviving completed bar can arm
and tighten its declared ATR trail for the NEXT bar only. No entry-bar exit.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace

CONTRACT = {
    'version': 'factory-exit.closed-bars.v2',
    'reference': 'signal_bar_close', 'atr': 'rolling_mean_true_range_14_through_signal_closed_bar',
    'stop_floor': 0.004, 'initial_r': 'reference_to_frozen_stop',
    'observations': 'consecutive_completed_spec_bars_after_signal_bar',
    'ordering': 'open_gap_then_stop_then_target_then_declared_signal_then_max_bars_then_next_bar_trail',
    'ambiguity': 'both_range_hits_stop_first_explicit',
    'quantity': 'full_remaining',
    'trail': 'declared_atr14_completed_bar_favorable_extreme_next_bar_only',
    'arming': 'favorable_excursion_from_reference_gte_declared_arm_at_r_times_initial_r',
    'ratchet': 'initial_stop_then_monotonic_tightening_fixed_target_retained',
    'missing_trail_atr': 'refuse_surviving_transition_price_and_time_exits_remain_effective',
    'signal_exit': 'completed_bar_decision_after_price_before_time_no_automatic_close_fill',
    'signal_fill': 'reference_exit_unset_pending_D6_first_legitimately_executable_price',
}
EXIT_SEMANTICS_ID = CONTRACT['version'] + ':' + hashlib.sha256(
    json.dumps(CONTRACT, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def unsupported(ex):
    """Strict whitelist; extra keys are declared unsupported, never ignored."""
    for name, modes in (
        ('stop', {'atr': {'kind', 'mult'}, 'pct': {'kind', 'v'}}),
        ('target', {'rr': {'kind', 'v'}, 'atr': {'kind', 'mult'},
                    'pct': {'kind', 'v'}, 'none': {'kind'}}),
        ('trail', {'none': {'kind'}, 'atr': {'kind', 'mult', 'arm_at_r'}}),
    ):
        value = getattr(ex, name, None)
        if not isinstance(value, dict) or value.get('kind') not in modes:
            return f'{name}={value.get("kind") if isinstance(value, dict) else "missing"}'
        if set(value) - modes[value['kind']]:
            return f'{name}:undeclared_fields'
        parameter = 'mult' if value['kind'] == 'atr' else 'v'
        if value['kind'] != 'none':
            v = value.get(parameter)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                return f'{name}:invalid_parameter'
        if name == 'trail' and value['kind'] == 'atr':
            arm = value.get('arm_at_r')
            if isinstance(arm, bool) or not isinstance(arm, (int, float)) or not math.isfinite(arm) or arm < 0:
                return 'trail:invalid_arm_at_r'
    if not isinstance(ex.signal_exit, str):
        return 'signal_exit:invalid_expression'
    t = ex.time
    if not isinstance(t, dict) or set(t) != {'max_bars'} or type(t['max_bars']) is not int or not 1 <= t['max_bars'] <= 500:
        return 'time:max_bars_unsupported'
    return None


def bind_research(spec):
    """Transient adapter routing; never changes the serialized StrategySpec.

    Factory candidate specs and their compiled/reloaded descendants use this
    route. Unsupported discovery shapes retain legacy research diagnostics,
    but can never produce a current semantic admission receipt.
    """
    import copy
    spec.exit = copy.deepcopy(spec.exit)
    spec.exit.__dict__.pop("_exit_semantics_id", None)
    if not unsupported(spec.exit):
        spec.exit._exit_semantics_id = EXIT_SEMANTICS_ID
        spec.exit._exit_timeframe = spec.timeframe
    return spec


@dataclass(frozen=True)
class Policy:
    stop: float
    target: float
    initial_r: float
    reference: float
    entry_atr: float
    side: str
    max_bars: int
    step_ms: int
    semantics_id: str = EXIT_SEMANTICS_ID
    trail_mult: float = 0.0
    arm_at_r: float = 0.0
    signal_exit: bool = False


@dataclass(frozen=True)
class State:
    last_bar_ms: int
    bars_held: int = 0
    trail: str = 'none'
    reason: str | None = None
    reference_exit: float | None = None
    ambiguous: bool = False
    gap: bool = False
    active_stop: float | None = None
    best: float | None = None


@dataclass(frozen=True)
class Observation:
    bar_ms: int
    open: float
    high: float
    low: float
    close: float
    completed: bool = True
    atr: float | None = None
    signal_exit: bool | None = None


@dataclass(frozen=True)
class Transition:
    state: State
    due: bool
    reason: str | None
    quantity: float
    stop: float
    target: float
    authority: str = 'STRATEGY'


def initialize(ex, reference, atr, side, entry_bar_ms, step_ms):
    bad = unsupported(ex)
    if bad:
        raise ValueError('unsupported_versioned_exit:' + bad)
    if side not in ('long', 'short') or not all(math.isfinite(v) and v > 0 for v in (reference, atr, step_ms)):
        raise ValueError('exit_entry_evidence_unavailable')
    sign = 1 if side == 'long' else -1
    d = atr * ex.stop['mult'] if ex.stop['kind'] == 'atr' else reference * ex.stop['v']
    d = max(d, reference * CONTRACT['stop_floor'])
    t = ex.target
    distance = (d * t['v'] if t['kind'] == 'rr' else atr * t['mult']
                if t['kind'] == 'atr' else reference * t['v'] if t['kind'] == 'pct' else 0)
    policy = Policy(reference - sign*d, reference + sign*distance if distance else 0,
                    d, reference, atr, side, ex.time['max_bars'], int(step_ms),
                    trail_mult=ex.trail['mult'] if ex.trail['kind'] == 'atr' else 0.0,
                    arm_at_r=ex.trail['arm_at_r'] if ex.trail['kind'] == 'atr' else 0.0,
                    signal_exit=bool(ex.signal_exit.strip()))
    if not all(math.isfinite(v) for v in (policy.stop,policy.target,policy.initial_r)) or policy.stop <= 0 or (policy.target and policy.target <= 0):
        raise ValueError('exit_geometry_nonpositive')
    return policy, State(int(entry_bar_ms), trail='dormant' if policy.trail_mult else 'none',
                         active_stop=policy.stop, best=reference)


def advance(policy, state, observation, remaining):
    if policy.semantics_id != EXIT_SEMANTICS_ID:
        raise ValueError('exit_semantics_mismatch')
    if not math.isfinite(remaining) or remaining < 0:
        raise ValueError('exit_quantity_invalid')
    active_stop = state.active_stop if state.active_stop is not None else policy.stop
    # Terminal intent remains pending for an adapter's failed submission;
    # successful fills remove the open position. No second state transition.
    if state.reason:
        return Transition(state, True, state.reason, remaining, active_stop, policy.target)
    if observation.bar_ms <= state.last_bar_ms:
        return Transition(state, False, None, 0, active_stop, policy.target)
    if not observation.completed or observation.bar_ms != state.last_bar_ms + policy.step_ms:
        raise ValueError('exit_observation_not_consecutive_closed_bar')
    o, h, l, c = observation.open, observation.high, observation.low, observation.close
    if not all(math.isfinite(v) and v > 0 for v in (o,h,l,c)) or not l <= min(o,c) <= max(o,c) <= h:
        raise ValueError('exit_observation_invalid_ohlc')
    long = policy.side == 'long'
    stop_gap = o <= active_stop if long else o >= active_stop
    target_gap = bool(policy.target) and (o >= policy.target if long else o <= policy.target)
    stop = l <= active_stop if long else h >= active_stop
    target = bool(policy.target) and (h >= policy.target if long else l <= policy.target)
    reason, px, gap = None, None, stop_gap or target_gap
    if gap:
        reason, px = ('stop' if stop_gap else 'target'), o
    elif stop or target:
        reason, px = ('stop', active_stop) if stop else ('target', policy.target)
    else:
        if policy.signal_exit:
            if type(observation.signal_exit) is not bool:
                raise ValueError('exit_signal_evidence_unavailable')
            if observation.signal_exit:
                reason = 'signal'  # decision only; D6 must supply a legitimate fill
        if not reason and state.bars_held + 1 >= policy.max_bars:
            reason, px = 'time', c
    best, trail = state.best, state.trail
    if policy.trail_mult and not reason:
        atr = observation.atr
        if isinstance(atr, bool) or not isinstance(atr, (int, float)) or not math.isfinite(atr) or atr <= 0:
            raise ValueError('exit_trail_atr_unavailable')
        previous_best = best if best is not None else policy.reference
        best = max(previous_best, h) if long else min(previous_best, l)
        sign = 1 if long else -1
        if trail == 'armed' or (best-policy.reference)*sign >= policy.arm_at_r*policy.initial_r:
            trail = 'armed'
            candidate = best - sign*policy.trail_mult*atr
            active_stop = max(active_stop, candidate) if long else min(active_stop, candidate)
    state = replace(state, last_bar_ms=observation.bar_ms, bars_held=state.bars_held+1,
                    active_stop=active_stop, best=best, trail=trail,
                    reason=reason, reference_exit=px, ambiguous=bool(stop and target and not gap), gap=bool(gap))
    return Transition(state, bool(reason), reason, remaining if reason else 0, active_stop, policy.target)


def execute_open(policy, state, bar_ms, price, remaining):
    """Execute a proven signal open after checking protection at that open.

    Adapters prove availability. This shared price transition never inspects
    an unfinished candle's range or advances its completed-bar counters. A
    time fallback at this very boundary may yield to the executable signal.
    """
    if (bar_ms != state.last_bar_ms + policy.step_ms
            or state.reason not in (None, 'time')):
        raise ValueError('signal_fill_missing_causal_history')
    if not math.isfinite(price) or price <= 0 or not math.isfinite(remaining) or remaining <= 0:
        raise ValueError('signal_fill_invalid_price_or_quantity')
    stop = state.active_stop if state.active_stop is not None else policy.stop
    long = policy.side == 'long'
    hit_stop = price <= stop if long else price >= stop
    hit_target = bool(policy.target) and (price >= policy.target if long else price <= policy.target)
    reason = 'stop' if hit_stop else 'target' if hit_target else 'signal'
    final = replace(state, reason=reason, reference_exit=price, gap=bool(hit_stop or hit_target))
    return Transition(final, True, reason, remaining, stop, policy.target)


def encode(policy, state):
    return json.dumps({'policy': asdict(policy), 'state': asdict(state)}, sort_keys=True)


def decode(text):
    body = json.loads(text)
    policy, state = Policy(**body['policy']), State(**body['state'])
    if policy.semantics_id != EXIT_SEMANTICS_ID:
        raise ValueError('exit_semantics_mismatch')
    return policy, state


def trail_atr_series(frame, step_ms):
    """ATR(14) with its actual 15-bar input window intact and trustworthy.

    The extra prior close supplies the first true range. This adapter helper
    does not decide observation availability or fill timing.
    """
    import numpy as np
    import pandas as pd
    from ..agents.indicators import atr_series
    stamps = frame['ts'].map(lambda v: int(v.timestamp()*1000) if hasattr(v, 'timestamp') else int(v))
    values = frame[['open', 'high', 'low', 'close']].to_numpy(float)
    valid = pd.Series(np.isfinite(values).all(axis=1) & (values > 0).all(axis=1), index=frame.index)
    valid &= (frame['low'] <= frame[['open','close']].min(axis=1)) & (frame['high'] >= frame[['open','close']].max(axis=1))
    quality = 'observation_quality' if frame.attrs.get('read_mode') == 'retrospective' else 'quality'
    for key, expected in ((quality,'VALID'), ('bar_state','FINAL')):
        if key in frame:
            valid &= frame[key].eq(expected)
    consecutive = stamps.diff().eq(step_ms)
    intact = valid.rolling(15).sum().eq(15) & consecutive.rolling(14).sum().eq(14)
    return atr_series(frame, 14).where(intact)
