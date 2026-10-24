"""D6 — where a closed-bar signal may legitimately fill.

Three facts stay separate: candle close, receipt availability, and the fill.
A signal on bar k is decided no earlier than max(close_k, availability of every
bar in its lookback): a decision cannot precede its inputs. It fills at the
open of the first retained bar starting at or after that instant, never at the
signal candle's close.

Validity: StrategySpec declares no entry validity, so the existing freshness
budget applies (interval + TTL after the close, `market_provenance.freshness_ms`
-- the same age past which the feed and kernel treat a closed bar as STALE),
measured at the fill. No new parameter. Missing or unknown inputs refuse.

Frames with no receipt columns, and explicitly retrospective datasets, decide
at close under their own declared "no historical availability claim" evidence
scope (never factory-validated); in a replay frame, unknown availability
refuses.
"""
from __future__ import annotations

import ast
from functools import lru_cache

import numpy as np
import pandas as pd

from ..core.types import TF_MS
from ..data import market_provenance as mp

POLICY_ID = "next-executable-open.v2"
#: engine-intrinsic minimum: ATR(14) sizing plus the previous bar
MIN_LOOKBACK = 15
#: exponentially smoothed features carry memory beyond their period. The multiplier is the EXISTING finite warm-up convention shared by
#: research and inference (parity under that convention), NOT a claim that an EMA's infinite memory is exactly bounded.
_SMOOTHED = {"ema": 3, "rsi": 3, "atr": 3, "adx": 3}
#: The hard bound on contiguous history one strategy may consume at inference. It replaces the old 210-bar clamp (the engine
#: warm-up), which understated the real dependency of graphs with rolling windows up to 500 and nested lag/rolling/fitted
#: inputs. A strategy needing more is refused/abstains EXPLICITLY (`history_requirements(...)['exceeds_bound']`), never
#: silently truncated; the bound is not a narrowing of the graph language.
INFERENCE_HISTORY_MAX = 6000


def _tf(df) -> str:
    tf = df.attrs.get("timeframe")
    if tf in TF_MS:
        return tf
    step = int(np.median(np.diff(mp.ms(df["ts"])))) if len(df) > 1 else 0
    for name, size in TF_MS.items():
        if size == step:
            return name
    raise ValueError("fill_timing_timeframe_unknown")


def _artifact_inputs(aid) -> list:
    """The DSL input expressions a frozen artifact consumes at inference (a schedule's inputs are shared by its segments)."""
    from . import fitted
    return list(fitted.get(aid)['inputs'])      # ArtifactError when unavailable: the dependency is then UNKNOWN, never zero


def _span(node, by_tf=None, tf=None) -> int:
    """Upper bound of base-frame bars an expression looks back over, INCLUDING the inputs of any fitted artifact it names.
    `htf(tf2, expr)` is evaluated on another frame: its own requirement (in tf2 bars) is recorded in `by_tf`, not added to
    the base span. `ref(...)` reads an external series whose chronology is its own contract (recorded as external)."""
    from .dsl import parse
    from .features import FEATURES, SERIES_ARG
    if isinstance(node, ast.Call):
        name = node.func.id
        if name == "htf":
            if by_tf is not None and isinstance(node.args[0], ast.Constant) and len(node.args) > 1:
                inner = _span(node.args[1], by_tf, node.args[0].value) + 1
                by_tf[node.args[0].value] = max(by_tf.get(node.args[0].value, 0), inner)
            return 0
        if name == "ref":
            if by_tf is not None:
                by_tf.setdefault("__external__", 0)
            return 0
        if name in ("fitted", "fitted_pass"):
            aid = node.args[0].value
            return max((_span(parse(e), by_tf, tf) for e in _artifact_inputs(aid)), default=0)
        period, inner = 0, 0
        for arg, aspec in zip(node.args, FEATURES[name].arg_specs):
            if aspec is SERIES_ARG:
                inner = max(inner, _span(arg, by_tf, tf))
            elif aspec[0] is int and isinstance(arg, ast.Constant):
                period = max(period, int(arg.value))
        return period * _SMOOTHED.get(name, 1) + inner
    return max((_span(child, by_tf, tf) for child in ast.iter_child_nodes(node)), default=0)


@lru_cache(maxsize=256)
def _lookback(expressions: tuple, swing: int) -> int:
    from .dsl import parse
    deepest = max((_span(parse(e)) for e in expressions), default=0)
    return max(MIN_LOOKBACK, deepest + 1, swing + 1)


def _expressions(spec) -> tuple:
    return tuple(e for e in (spec.entry_long, spec.entry_short, *spec.filters, spec.exit.signal_exit) if e)


def history_requirements(spec) -> dict:
    """Bounded dependency plan under the existing finite warm-up convention (including smoothed features):
    {'base': bars of the spec's own timeframe, 'htf': {tf: bars}, 'external': bool, 'exceeds_bound': bool}.
    Derived from the compiled expressions and the inputs of every fitted artifact they name (never the training window).
    An unavailable artifact raises: the requirement is unknown and the strategy must abstain."""
    from .dsl import parse
    stop = spec.exit.stop or {}
    swing = int(stop.get("lookback", 20)) if stop.get("kind") == "swing" else 0
    by_tf: dict = {}
    deepest = max((_span(parse(e), by_tf, spec.timeframe) for e in _expressions(spec)), default=0)
    base = max(MIN_LOOKBACK, deepest + 1, swing + 1)
    external = by_tf.pop("__external__", None) is not None
    htf = {k: int(v) + MIN_LOOKBACK for k, v in by_tf.items()}
    return dict(base=int(base), htf=htf, external=external,
                exceeds_bound=bool(base > INFERENCE_HISTORY_MAX or any(v > INFERENCE_HISTORY_MAX for v in htf.values())))


def lookback_bars(spec) -> int:
    """The same declared dependency bound in replay and inference; oversized expressions are refused, never truncated."""
    need = history_requirements(spec)
    if need['exceeds_bound']:
        raise ValueError('history_exceeds_bound')
    return need['base']


def fills(df):
    """(fill_i, reason) per signal bar: first executable bar index, else -1 and why.

    The history a signal needs is `df.attrs['signal_lookback_bars']` (bound by
    the compiled spec), else MIN_LOOKBACK. Reasons: availability_unknown (a
    lookback input has no recorded availability), signal_stale (the first
    executable open is past interval + TTL after the close), no_executable_bar,
    unexecutable_price (non-finite open) and, for receipt-bearing frames only,
    history_gap (a bar missing from the lookback)."""
    n = len(df)
    fill = np.full(n, -1, dtype=np.int64)
    why = np.full(n, "", dtype=object)
    if n == 0:
        return fill, why
    tf = _tf(df)
    step = TF_MS[tf]
    window = int(df.attrs.get("signal_lookback_bars") or MIN_LOOKBACK)
    ts = mp.ms(df["ts"])
    receipts = "available_at_ms" in df and df.attrs.get("read_mode") != "retrospective"
    close = (ts + step).astype(float)
    decision = close.copy()
    unknown = np.zeros(n, dtype=bool)
    gapped = np.zeros(n, dtype=bool)
    if receipts:
        available = pd.Series(pd.to_numeric(df["available_at_ms"], errors="coerce").to_numpy(float))
        unknown = available.isna().rolling(window, min_periods=1).max().to_numpy(bool)
        decision = np.maximum(close, available.rolling(window, min_periods=1).max().to_numpy())
        hole = np.r_[False, np.diff(ts) != step]
        gapped = pd.Series(hole).rolling(window, min_periods=1).max().to_numpy(bool)
    first = np.searchsorted(ts, np.where(unknown, 0.0, decision), side="left")
    clipped = np.minimum(first, n - 1)
    opens = df["open"].to_numpy(float)
    for reason, refused in (
            ("history_gap", gapped),
            ("unexecutable_price", ~np.isfinite(opens[clipped])),
            ("signal_stale", ts[clipped] - close > mp.freshness_ms(tf)),
            ("no_executable_bar", first >= n),
            ("availability_unknown", unknown)):
        why[refused] = reason          # later entries take precedence
    fill[why == ""] = first[why == ""]
    return fill, why


def summary(df) -> dict:
    """Counts of executable signal bars and refusals by reason, for evidence."""
    fill, why = fills(df)
    out = {"policy": POLICY_ID, "executable": int((fill >= 0).sum())}
    for reason in sorted(set(why[fill < 0])):
        out[reason] = int((why == reason).sum())
    return out


def tail_validity(frame, tf: str, bars: int):
    """Why the last `bars` rows of a frame are NOT a usable contiguous dependency window (or None): a missing source interval, a
    non-VALID/non-FINAL row, or non-finite price columns. Used by the live evaluator so inference applies the same contiguity
    discipline fill_timing applies to historical signals (contiguity is not availability and not freshness)."""
    if frame is None or len(frame) < bars:
        return f"insufficient_history:{0 if frame is None else len(frame)}<{bars}"
    tail = frame.iloc[-bars:]
    step = TF_MS[tf]
    ts = mp.ms(tail["ts"])
    if len(ts) > 1 and (np.diff(ts) != step).any():
        return "history_gap_in_consumed_tail"
    if "quality" in tail and not tail["quality"].eq("VALID").all():
        return "invalid_history_in_consumed_tail"
    if "bar_state" in tail and not tail["bar_state"].eq("FINAL").all():
        return "partial_history_in_consumed_tail"
    return None


def inference_limits(specs, *, floor: int = 400, margin: int = 16) -> dict:
    """{timeframe: bars to request} for the SELECTED symbol, from the specs' ACTUAL dependencies: each spec's own timeframe and
    every htf source timeframe it names (keys the default config may not list), never training history and never a universe.
    A requirement above INFERENCE_HISTORY_MAX is capped here ONLY as a fetch bound; the evaluator refuses that spec explicitly
    (`exceeds_bound`), so the cap is never a silent truncation of a consumed window. A spec whose requirement is unknown is skipped
    here (its evaluator abstains)."""
    needs: dict = {}
    for spec in specs:
        try:
            req = history_requirements(spec)
        except Exception:                                 # noqa: BLE001 - unknown dependency: evaluator abstains, nothing to size
            continue
        needs[spec.timeframe] = max(needs.get(spec.timeframe, 0), req['base'])
        for tf, bars in req['htf'].items():
            needs[tf] = max(needs.get(tf, 0), bars)
    return {tf: min(max(floor, bars + margin), INFERENCE_HISTORY_MAX + margin) for tf, bars in needs.items()}
