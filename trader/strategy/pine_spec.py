"""StrategySpec → Pine Script v5.

The third compile target. `pine.py` forges from hand-written per-FAMILY
templates keyed on `genome.family`, so it cannot express a spec at all — and
its family-proxy heritage is exactly what let the old pipeline score every
candidate of a family identically. This walks the spec's own DSL AST instead,
so what TradingView tests is the same logic the internal backtester ran.

Honest degradation is the point. Funding, open interest, taker ratio and
long/short ratio have no Pine analogue on a price chart, so a spec using them
is marked `tv_testable = False` with the reason recorded, rather than silently
substituted with something else — the failure mode the gauntlet repair had to
undo.
"""
from __future__ import annotations

import ast

from . import dsl
from .spec import StrategySpec

BTC = "BINANCE:BTCUSDT"

_HEADER = '''//@version=5
// LUFFY spec · id={sid} · {name}
// thesis: {thesis}
strategy("LUFFY {name}", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=10, commission_type=strategy.commission.percent, commission_value={fee}, pyramiding=0, calc_on_every_tick=false)
'''

#: features with no Pine analogue — a spec using one cannot be TV-tested
_NO_PINE = {
    "funding": "perpetual funding rate is not available on a Pine chart",
    "funding_z": "perpetual funding rate is not available on a Pine chart",
    "funding_cum": "perpetual funding rate is not available on a Pine chart",
    "oi": "open interest history is not available on a Pine chart",
    "oi_ret": "open interest history is not available on a Pine chart",
    "oi_z": "open interest history is not available on a Pine chart",
    "taker_ratio": "taker buy/sell ratio is not available on a Pine chart",
    "taker_ratio_z": "taker buy/sell ratio is not available on a Pine chart",
    "ls_ratio": "long/short ratio is not available on a Pine chart",
    "ls_ratio_z": "long/short ratio is not available on a Pine chart",
    "basis": "spot-perp basis is not available on a Pine chart",
    "basis_z": "spot-perp basis is not available on a Pine chart",
    "taker_buy_frac": "derived from the candle store's taker_buy estimate, "
                      "which Pine cannot reproduce",
}

_SESSION_HOURS = {"asia": (0, 8), "eu": (7, 16), "us": (13, 22)}


class PineUnsupported(Exception):
    """This spec cannot be expressed in Pine."""


def _n(node) -> str:
    """Emit a numeric literal the way Pine wants it."""
    v = node.value
    return str(int(v)) if isinstance(v, (int, float)) and float(v).is_integer() \
        else repr(float(v))


# feature name -> f(emitted_args) -> pine expression
_EMIT = {
    "open": lambda a: "open", "high": lambda a: "high",
    "low": lambda a: "low", "close": lambda a: "close",
    "volume": lambda a: "volume",
    "ema": lambda a: f"ta.ema(close, {a[0]})",
    "sma": lambda a: f"ta.sma(close, {a[0]})",
    "rsi": lambda a: f"ta.rsi(close, {a[0]})",
    "atr": lambda a: f"ta.atr({a[0]})",
    "adx": lambda a: f"_adx{a[0]}",          # hoisted, see _hoist_adx
    "vwap": lambda a: f"_vwap{a[0]}",        # hoisted, see _hoist
    "realized_vol": lambda a: f"ta.stdev(math.log(close / close[1]), {a[0]})",
    "bb_upper": lambda a: (f"(ta.sma(close, {a[0]}) + {a[1]} * "
                           f"ta.stdev(close, {a[0]}))"),
    "bb_lower": lambda a: (f"(ta.sma(close, {a[0]}) - {a[1]} * "
                           f"ta.stdev(close, {a[0]}))"),
    "bb_pctb": lambda a: (
        f"((close - (ta.sma(close, {a[0]}) - {a[1]} * ta.stdev(close, {a[0]}))) / "
        f"math.max(2 * {a[1]} * ta.stdev(close, {a[0]}), 1e-12))"),
    "donchian_hi": lambda a: f"ta.highest(high, {a[0]})[1]",
    "donchian_lo": lambda a: f"ta.lowest(low, {a[0]})[1]",
    "ret": lambda a: f"(close / close[{a[0]}] - 1)",
    "abs": lambda a: f"math.abs({a[0]})",
    "max": lambda a: f"math.max({a[0]}, {a[1]})",
    "min": lambda a: f"math.min({a[0]}, {a[1]})",
    "prev": lambda a: f"({a[0]})[{a[1]}]",
    "sma_of": lambda a: f"ta.sma({a[0]}, {a[1]})",
    "zscore": lambda a: (f"(({a[0]}) - ta.sma({a[0]}, {a[1]})) / "
                         f"math.max(ta.stdev({a[0]}, {a[1]}), 1e-12)"),
    "pct_rank": lambda a: f"(ta.percentrank({a[0]}, {a[1]}) / 100)",
    "slope": lambda a: (f"((({a[0]}) - ({a[0]})[{a[1]}]) / "
                        f"math.max(math.abs({a[0]}), 1e-12) / {a[1]})"),
    "hour_utc": lambda a: 'hour(time, "UTC")',
    "dow": lambda a: 'dayofweek(time, "UTC")',
    "btc_ret": lambda a: f"(_btc / _btc[{a[0]}] - 1)",
    "btc_ema_dist": lambda a: (f"((_btc - ta.ema(_btc, {a[0]})) / "
                               f"math.max(math.abs(ta.ema(_btc, {a[0]})), 1e-12))"),
    "corr_btc": lambda a: f"ta.correlation(close, _btc, {a[0]})",
    "rel_strength_btc": lambda a: (f"((close / close[{a[0]}] - 1) - "
                                   f"(_btc / _btc[{a[0]}] - 1))"),
}

_BINOP = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
_CMP = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
        ast.Eq: "==", ast.NotEq: "!="}


#: features whose Pine form is hoisted to a named variable rather than
#: inlined — keeps lines short and stays under Pine's 40-security-call cap
_BTC_FEATURES = {"btc_ret", "btc_ema_dist", "corr_btc", "rel_strength_btc"}


def _emit(node, adx_periods: set) -> str:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return f'"{node.value}"'
        return _n(node)

    if isinstance(node, ast.Name):
        if node.id in _NO_PINE:
            raise PineUnsupported(_NO_PINE[node.id])
        if node.id in _BTC_FEATURES:
            adx_periods.add(("btc", None))
        return _EMIT[node.id](())

    if isinstance(node, ast.Call):
        name = node.func.id
        if name in _NO_PINE:
            raise PineUnsupported(_NO_PINE[name])
        if name == "htf":
            tf = node.args[0].value
            inner = _emit(node.args[1], adx_periods)
            return f'request.security(syminfo.tickerid, "{tf}", {inner})'
        if name == "is_session":
            lo, hi = _SESSION_HOURS[node.args[0].value]
            return f'(hour(time, "UTC") >= {lo} and hour(time, "UTC") < {hi})'
        args = [_emit(a, adx_periods) for a in node.args]
        if name == "adx":
            adx_periods.add(("adx", args[0]))
        elif name == "vwap":
            adx_periods.add(("vwap", args[0]))
        elif name in _BTC_FEATURES:
            adx_periods.add(("btc", None))
        if name not in _EMIT:
            raise PineUnsupported(f"no Pine emitter for feature '{name}'")
        return _EMIT[name](args)

    if isinstance(node, ast.BoolOp):
        joiner = " and " if isinstance(node.op, ast.And) else " or "
        return "(" + joiner.join(_emit(v, adx_periods)
                                 for v in node.values) + ")"

    if isinstance(node, ast.UnaryOp):
        inner = _emit(node.operand, adx_periods)
        if isinstance(node.op, ast.Not):
            return f"(not {inner})"
        return f"(-{inner})" if isinstance(node.op, ast.USub) else inner

    if isinstance(node, ast.BinOp):
        return (f"({_emit(node.left, adx_periods)} "
                f"{_BINOP[type(node.op)]} "
                f"{_emit(node.right, adx_periods)})")

    if isinstance(node, ast.Compare):
        parts, left = [], _emit(node.left, adx_periods)
        for op, comp in zip(node.ops, node.comparators):
            right = _emit(comp, adx_periods)
            parts.append(f"({left} {_CMP[type(op)]} {right})")
            left = right
        return "(" + " and ".join(parts) + ")"

    raise PineUnsupported(f"cannot emit {type(node).__name__}")


def _expr(text: str, adx_periods: set) -> str:
    return _emit(dsl.parse(text).body, adx_periods)


def _hoist(needs: set) -> str:
    """Emit the named intermediates.

    ta.dmi returns a tuple so ADX cannot be inlined at all; BTC and rolling
    VWAP are hoisted because inlining them produced 400+ character lines and
    repeated request.security calls, which Pine caps at 40 per script.
    """
    if not needs:
        return ""
    lines = []
    if ("btc", None) in needs:
        lines.append(f'_btc = request.security("{BTC}", timeframe.period, close)')
    adx = sorted(p for k, p in needs if k == "adx")
    if adx:
        lines.append("// ta.dmi returns a tuple; it cannot be inlined")
        for p in adx:
            lines.append(f"[_dip{p}, _dim{p}, _adx{p}] = ta.dmi({p}, {p})")
    for p in sorted(p for k, p in needs if k == "vwap"):
        lines.append(f"_vwap{p} = math.sum(hlc3 * volume, {p}) / "
                     f"math.max(math.sum(volume, {p}), 1e-12)")
    return "\n".join(lines) + "\n"


def _exits(spec: StrategySpec) -> str:
    """ExitSpec → Pine. The whole point of exits being genes is that the
    tester must risk the way this spec risks, not the way config does."""
    e = spec.exit
    stop, target, trail = e.stop, e.target, e.trail or {"kind": "none"}
    k = stop.get("kind", "atr")
    if k == "atr":
        sd = f"ta.atr(14) * {float(stop.get('mult', 2.0))}"
    elif k == "pct":
        sd = f"close * {float(stop.get('v', 0.01))}"
    else:
        n = int(stop.get("lookback", 20))
        sd = (f"math.max(close - ta.lowest(low, {n}), "
              f"ta.highest(high, {n}) - close)")
    tk = target.get("kind", "rr")
    if tk == "none":
        td = "na"
    elif tk == "atr":
        td = f"ta.atr(14) * {float(target.get('mult', 3.0))}"
    elif tk == "rr":
        td = f"_sd * {float(target.get('v', 2.0))}"
    else:
        td = f"close * {float(target.get('v', 0.02))}"
    out = [
        "// ── exits: this spec's OWN geometry, not global config ──",
        f"_sd = math.max({sd}, close * 0.004)",
        f"_td = {td}",
        "var float _entry = na",
        "var int   _bar   = na",
        "if strategy.position_size != 0 and na(_entry)",
        "    _entry := strategy.position_avg_price",
        "    _bar   := bar_index",
        "if strategy.position_size == 0",
        "    _entry := na",
        "    _bar   := na",
    ]
    if trail.get("kind") == "atr":
        out += [f"_trailPts = ta.atr(14) * {float(trail.get('mult', 2.0))}",
                f"_trailOff = _sd * {float(trail.get('arm_at_r', 1.0))}"]
        long_exit = ("strategy.exit(\"LX\", \"L\", stop=strategy.position_avg_price - _sd, "
                     "limit=na(_td) ? na : strategy.position_avg_price + _td, "
                     "trail_points=_trailPts, trail_offset=_trailOff)")
        short_exit = ("strategy.exit(\"SX\", \"S\", stop=strategy.position_avg_price + _sd, "
                      "limit=na(_td) ? na : strategy.position_avg_price - _td, "
                      "trail_points=_trailPts, trail_offset=_trailOff)")
    else:
        long_exit = ("strategy.exit(\"LX\", \"L\", stop=strategy.position_avg_price - _sd, "
                     "limit=na(_td) ? na : strategy.position_avg_price + _td)")
        short_exit = ("strategy.exit(\"SX\", \"S\", stop=strategy.position_avg_price + _sd, "
                      "limit=na(_td) ? na : strategy.position_avg_price - _td)")
    max_bars = int(e.time.get("max_bars", 32) or 32)
    out += [
        "if strategy.position_size > 0",
        f"    {long_exit}",
        "if strategy.position_size < 0",
        f"    {short_exit}",
        f"// time exit: {max_bars} bars",
        f"if strategy.position_size != 0 and not na(_bar) and "
        f"bar_index - _bar >= {max_bars}",
        '    strategy.close_all(comment="time")',
    ]
    return "\n".join(out) + "\n"


def to_pine(spec: StrategySpec, fee_pct: float = 0.05) -> tuple[str, bool, list]:
    """(pine_source, tv_testable, reasons).

    When tv_testable is False the source is empty and `reasons` says exactly
    which feature made it untestable — never a substituted proxy.
    """
    reasons, adx_periods = [], set()
    try:
        long_e = _expr(spec.entry_long, adx_periods) \
            if (spec.entry_long or "").strip() else None
        short_e = _expr(spec.entry_short, adx_periods) \
            if (spec.entry_short or "").strip() else None
        filters = [_expr(f, adx_periods) for f in (spec.filters or [])
                   if (f or "").strip()]
    except (PineUnsupported, KeyError) as ex:
        return "", False, [str(ex)]

    head = _HEADER.format(sid=spec.id, name=spec.name.replace('"', "'"),
                          thesis=(spec.thesis or "")[:150].replace("\n", " "),
                          fee=fee_pct)
    body = [_hoist(adx_periods)]
    filt = " and ".join(filters) if filters else "true"
    body.append(f"_filter = {filt}")
    body.append(f"_long  = {long_e} and _filter" if long_e else "_long  = false")
    body.append(f"_short = {short_e} and _filter" if short_e else "_short = false")
    body.append("")
    body.append('if _long and strategy.position_size == 0')
    body.append('    strategy.entry("L", strategy.long)')
    body.append('if _short and strategy.position_size == 0')
    body.append('    strategy.entry("S", strategy.short)')
    body.append("")
    code = head + "\n".join(body) + "\n" + _exits(spec)

    from ..brain.pine import lint
    errs = lint(code)
    if errs:
        return "", False, [f"lint: {e}" for e in errs]
    return code, True, reasons
