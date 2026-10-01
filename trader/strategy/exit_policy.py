"""Factory exit intent, independent of fills, orders, protection and costs.

Observation contract: consecutive COMPLETED bars on the declared timeframe.
Entry geometry uses signal-bar close and ATR(14) through that closed bar.
An open beyond a level precedes range checks. Otherwise OHLC has no intrabar
sequence: stop wins a both-hit range, with ambiguity recorded explicitly.
Price exits precede unconditional max_bars. No entry-bar exit or overlays.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace

CONTRACT = {
    'version': 'factory-exit.closed-bars.v1',
    'reference': 'signal_bar_close', 'atr': 'rolling_mean_true_range_14_through_signal_closed_bar',
    'stop_floor': 0.004, 'initial_r': 'reference_to_frozen_stop',
    'observations': 'consecutive_completed_spec_bars_after_signal_bar',
    'ordering': 'open_gap_then_stop_then_target_then_unconditional_max_bars',
    'ambiguity': 'both_range_hits_stop_first_explicit',
    'quantity': 'full_remaining', 'trail': 'none_only', 'signal_exit': 'refused',
}
EXIT_SEMANTICS_ID = CONTRACT['version'] + ':' + hashlib.sha256(
    json.dumps(CONTRACT, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def unsupported(ex):
    """Strict whitelist; extra keys are declared unsupported, never ignored."""
    for name, modes in (
        ('stop', {'atr': {'kind', 'mult'}, 'pct': {'kind', 'v'}}),
        ('target', {'rr': {'kind', 'v'}, 'atr': {'kind', 'mult'},
                    'pct': {'kind', 'v'}, 'none': {'kind'}}),
        ('trail', {'none': {'kind'}}),
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
    if ex.signal_exit:
        return 'signal_exit:runtime_parity_unavailable'
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


@dataclass(frozen=True)
class State:
    last_bar_ms: int
    bars_held: int = 0
    trail: str = 'none'
    reason: str | None = None
    reference_exit: float | None = None
    ambiguous: bool = False
    gap: bool = False


@dataclass(frozen=True)
class Observation:
    bar_ms: int
    open: float
    high: float
    low: float
    close: float
    completed: bool = True


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
                    d, reference, atr, side, ex.time['max_bars'], int(step_ms))
    if not all(math.isfinite(v) for v in (policy.stop,policy.target,policy.initial_r)) or policy.stop <= 0 or (policy.target and policy.target <= 0):
        raise ValueError('exit_geometry_nonpositive')
    return policy, State(int(entry_bar_ms))


def advance(policy, state, observation, remaining):
    if policy.semantics_id != EXIT_SEMANTICS_ID:
        raise ValueError('exit_semantics_mismatch')
    if not math.isfinite(remaining) or remaining < 0:
        raise ValueError('exit_quantity_invalid')
    # Terminal intent remains pending for an adapter's failed submission;
    # successful fills remove the open position. No second state transition.
    if state.reason:
        return Transition(state, True, state.reason, remaining, policy.stop, policy.target)
    if observation.bar_ms <= state.last_bar_ms:
        return Transition(state, False, None, 0, policy.stop, policy.target)
    if not observation.completed or observation.bar_ms != state.last_bar_ms + policy.step_ms:
        raise ValueError('exit_observation_not_consecutive_closed_bar')
    o, h, l, c = observation.open, observation.high, observation.low, observation.close
    if not all(math.isfinite(v) and v > 0 for v in (o,h,l,c)) or not l <= min(o,c) <= max(o,c) <= h:
        raise ValueError('exit_observation_invalid_ohlc')
    long = policy.side == 'long'
    stop_gap = o <= policy.stop if long else o >= policy.stop
    target_gap = bool(policy.target) and (o >= policy.target if long else o <= policy.target)
    stop = l <= policy.stop if long else h >= policy.stop
    target = bool(policy.target) and (h >= policy.target if long else l <= policy.target)
    reason, px, gap = None, None, stop_gap or target_gap
    if gap:
        reason, px = ('stop' if stop_gap else 'target'), o
    elif stop or target:
        reason, px = ('stop', policy.stop) if stop else ('target', policy.target)
    elif state.bars_held + 1 >= policy.max_bars:
        reason, px = 'time', c
    state = replace(state, last_bar_ms=observation.bar_ms, bars_held=state.bars_held+1,
                    reason=reason, reference_exit=px, ambiguous=bool(stop and target and not gap), gap=bool(gap))
    return Transition(state, bool(reason), reason, remaining if reason else 0, policy.stop, policy.target)


def encode(policy, state):
    return json.dumps({'policy': asdict(policy), 'state': asdict(state)}, sort_keys=True)


def decode(text):
    body = json.loads(text)
    policy, state = Policy(**body['policy']), State(**body['state'])
    if policy.semantics_id != EXIT_SEMANTICS_ID:
        raise ValueError('exit_semantics_mismatch')
    return policy, state
