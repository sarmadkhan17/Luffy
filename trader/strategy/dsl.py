"""The strategy expression language.

A restricted Python expression, e.g.

    close > ema(20) and adx(14) > 25 and funding_z(96) < -1.5

parsed with `ast.parse(mode="eval")` against a node whitelist and resolved
against `features.FEATURES`.

This REPLACES `proposer._sandbox_evaluator()`, which `exec()`s LLM-written
Python behind a builtins whitelist — a whitelist that does not stop
`().__class__.__bases__[0].__subclasses__()` traversal. Here there is no code
execution path at all: no node type can reach an attribute, a subscript, a
comprehension, or a name that is not a registered feature.

The same text is what the Librarian prints on a vault card, so the strategy a
human reads and the strategy the backtester runs cannot drift apart.
"""
from __future__ import annotations

import ast
import copy
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .features import FEATURES, SERIES_ARG


class SpecError(ValueError):
    """A malformed or unsafe strategy expression."""


_ALLOWED = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.Call, ast.Name, ast.Load, ast.Constant,
)


# ── parsing / validation ─────────────────────────────────────────────────
def parse(expr: str) -> ast.Expression:
    """Parse and validate. Raises SpecError on anything unsafe or unknown."""
    text = (expr or "").strip()
    if not text:
        raise SpecError("empty expression")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise SpecError(f"SyntaxError: {e.msg}") from e

    # ast.walk visits a Call's `func` as a bare Name; those are callees, not
    # zero-arity feature references, and must not be arity-checked as such.
    callees = {id(n.func) for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise SpecError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise SpecError("only direct calls to registered features")
            name = node.func.id
            if name not in FEATURES:
                raise SpecError(f"unknown feature '{name}'")
            if node.keywords:
                raise SpecError(f"{name}(): keyword arguments are not allowed")
            spec = FEATURES[name].arg_specs
            if len(node.args) != len(spec):
                raise SpecError(f"{name}() takes {len(spec)} arg(s), "
                                f"got {len(node.args)}")
            _check_args(name, node, spec)
            if name == "ref":
                _check_ref(node)
        elif isinstance(node, ast.Name):
            if id(node) in callees:
                continue
            if node.id not in FEATURES:
                raise SpecError(f"unknown name '{node.id}'")
            if FEATURES[node.id].arg_specs:
                raise SpecError(f"'{node.id}' needs "
                                f"{len(FEATURES[node.id].arg_specs)} arg(s)")
    return tree


def _check_args(name: str, node: ast.Call, spec: tuple) -> None:
    for i, (arg, aspec) in enumerate(zip(node.args, spec)):
        if aspec is SERIES_ARG:
            continue
        typ = aspec[0]
        if typ is str:
            if not (isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)):
                raise SpecError(f"{name}() arg {i} must be a string literal")
        elif isinstance(arg, ast.Constant) and not isinstance(
                arg.value, (int, float)):
            raise SpecError(f"{name}() arg {i} must be numeric")


#: what cannot mean anything on a reference series: other frames, the
#: book, BTC context, the traded symbol's derivatives
_NOT_IN_REF = {"ref", "htf", "xs_rank", "breadth", "dispersion",
               "btc_ret", "btc_ema_dist", "corr_btc", "rel_strength_btc"}


def _check_ref(node: ast.Call) -> None:
    from ..data.references import REFS
    key = node.args[0].value
    if key not in REFS:
        raise SpecError(f"ref(): unknown reference '{key}' "
                        f"(known: {', '.join(sorted(REFS))})")
    for sub in ast.walk(node.args[1]):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            name = sub.func.id
        elif isinstance(sub, ast.Name):
            name = sub.id
        else:
            continue
        if name not in FEATURES:
            continue
        if name in _NOT_IN_REF or tuple(FEATURES[name].requires) != ("ohlcv",):
            raise SpecError(f"ref(): '{name}' cannot be evaluated on a "
                            f"reference series")


def features_used(tree: ast.Expression) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out.add(node.func.id)
        elif isinstance(node, ast.Name):
            out.add(node.id)
    return out


def data_requires(*trees: ast.Expression) -> tuple[str, ...]:
    """Union of the data sources every referenced feature declares.

    DERIVED, never declared: a Strategist cannot claim a spec is OHLCV-only
    while reading funding, so the gauntlet can always tell whether the data
    needed to test it honestly actually exists.
    """
    req: set[str] = set()
    for tree in trees:
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "ref"):
                req.add(f"ref:{node.args[0].value}")
        for name in features_used(tree):
            req.update(FEATURES[name].requires)
    return tuple(sorted(req or {"ohlcv"}))


# ── evaluation ───────────────────────────────────────────────────────────
_BOOLOP = {ast.And: lambda a, b: a & b, ast.Or: lambda a, b: a | b}
_BINOP = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
          ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def evaluate(tree: ast.Expression, ctx):
    return _eval(tree.body, ctx)


def evaluate_bool(tree: ast.Expression, ctx) -> np.ndarray:
    """Boolean array over the base index. NaN — an indicator still warming
    up, or missing leader/derivative data — is FALSE, never a firing
    signal."""
    v = _eval(tree.body, ctx)
    if isinstance(v, pd.Series):
        if v.dtype == bool:
            return v.to_numpy(dtype=bool)
        return v.fillna(False).astype(bool).to_numpy(dtype=bool)
    return np.full(len(ctx.index), bool(v))


def _eval(node, ctx):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return ctx.get(node.id, ())

    if isinstance(node, ast.Call):
        name = node.func.id
        if name == "ref":
            return _eval_ref(node, ctx)
        if name == "htf":
            return _eval_htf(node, ctx)
        if name in ("xs_rank", "breadth", "dispersion"):
            return _eval_xs(name, node, ctx)
        # only literal args participate in the memo key; a Series argument is
        # keyed by its sub-expression source so two different expressions
        # never collide in the cache
        key = tuple(a.value if isinstance(a, ast.Constant) else ast.dump(a)
                    for a in node.args)
        cache_key = (name, ctx.tf, key)
        if cache_key not in ctx._cache:
            args = tuple(_eval(a, ctx) for a in node.args)
            ctx._cache[cache_key] = FEATURES[name].fn(ctx, *args)
        return ctx._cache[cache_key]

    if isinstance(node, ast.BoolOp):
        vals = [_as_bool(_eval(v, ctx), ctx) for v in node.values]
        op = _BOOLOP[type(node.op)]
        out = vals[0]
        for v in vals[1:]:
            out = op(out, v)
        return out

    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return ~_as_bool(v, ctx)
        if isinstance(node.op, ast.USub):
            return -v
        return +v

    if isinstance(node, ast.BinOp):
        return _BINOP[type(node.op)](_eval(node.left, ctx),
                                     _eval(node.right, ctx))

    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        out = None
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, ctx)
            r = _CMP[type(op)](left, right)
            out = r if out is None else (_as_bool(out, ctx) & _as_bool(r, ctx))
            left = right
        return out

    raise SpecError(f"cannot evaluate {type(node).__name__}")


def _as_bool(v, ctx) -> pd.Series:
    """Coerce to a boolean Series on the base index. NaN -> False."""
    if isinstance(v, pd.Series):
        if v.dtype == bool:
            return v
        return v.fillna(False).astype(bool)
    return pd.Series(bool(v), index=ctx.index)


def _eval_htf(node: ast.Call, ctx):
    """`htf(tf, expr)` — evaluate `expr` on the `tf` frame, then map each
    higher-timeframe value onto the base bars that came AFTER it closed.

    Same discipline as backtest.ctx_at(): searchsorted side='right' minus one,
    so a base bar can only see HTF bars that are already complete.
    """
    tf = node.args[0].value
    if tf not in ctx.frames or ctx.frames[tf] is None \
            or not len(ctx.frames[tf]):
        return pd.Series(np.nan, index=ctx.index)
    sub = ctx.scoped(tf)
    vals = _eval(node.args[1], sub)
    if not isinstance(vals, pd.Series):
        return pd.Series(vals, index=ctx.index)
    base_ts = pd.to_datetime(ctx.df["ts"], utc=True).values
    htf_ts = pd.to_datetime(ctx.frames[tf]["ts"], utc=True).values
    pos = np.searchsorted(htf_ts, base_ts, side="right") - 1
    arr = vals.to_numpy(dtype=float)
    out = np.where(pos >= 0, arr[np.clip(pos, 0, None)], np.nan)
    return pd.Series(out, index=ctx.index)


def _eval_ref(node: ast.Call, ctx):
    """`ref(key, expr)` — evaluate `expr` on reference market `key`, then
    give each base bar the value that was KNOWN when that bar closed.

    A reference bar is known at its stamp plus its source's close_after
    (trader.data.references.REFS); a base bar closes at its stamp plus its
    own bar length. searchsorted side='right' minus one — the htf rule — on
    those two clocks, so a bar never reads a value from after its own close.
    Past the source's max_stale the feed is treated as dead: NaN, never the
    last value carried forever.
    """
    from ..core.types import TF_MS
    from ..data.references import REFS, to_ms
    from .features import FeatureCtx
    key = node.args[0].value
    ref = REFS[key]
    frame = (ctx.market or {}).get(key)
    if frame is None or not len(frame):
        return pd.Series(np.nan, index=ctx.index)
    sub = FeatureCtx(frames={ref.tf: frame}, tf=ref.tf, market=None,
                     symbol=key, _cache={})
    vals = _eval(node.args[1], sub)
    if not isinstance(vals, pd.Series):
        return pd.Series(vals, index=ctx.index)
    known = to_ms(frame["ts"]) + ref.close_after_ms
    base_close = to_ms(ctx.df["ts"]) + TF_MS.get(ctx.tf, 0)
    pos = np.searchsorted(known, base_close, side="right") - 1
    safe = np.clip(pos, 0, None)
    arr = vals.to_numpy(dtype=float)
    fresh = (pos >= 0) & (base_close - known[safe] <= ref.max_stale_ms)
    return pd.Series(np.where(fresh, arr[safe], np.nan), index=ctx.index)


def _bar_interval(ts: np.ndarray) -> np.timedelta64:
    """Median spacing between consecutive base bars, for the staleness cap
    below. Median rather than mean/first-diff so one gap (a missing candle)
    cannot distort the interval the cap is measured against."""
    if len(ts) < 2:
        return np.timedelta64(0, "ns")
    diffs = np.diff(ts).astype("timedelta64[ns]").astype("int64")
    return np.timedelta64(int(np.median(diffs)), "ns")


def _eval_xs(name: str, node: ast.Call, ctx):
    """Evaluate the inner expression for every universe member, align each
    onto this symbol's bars point-in-time, then reduce across members.

    Same discipline as _eval_htf: searchsorted side='right' minus one, so a
    bar can only see peer bars that had already closed.

    NOTE: unlike every other feature call, this bypasses `ctx._cache` — the
    inner expression is evaluated once per universe member on every call to
    `_eval_xs`, not memoised on `(name, tf, args)`. The same cross-sectional
    term appearing in both an entry expression and a filter is therefore
    recomputed rather than reused.
    """
    universe = ctx.universe or {}
    if not universe:
        return pd.Series(np.nan, index=ctx.index)

    base_ts = pd.to_datetime(ctx.df["ts"], utc=True).values
    # F: a peer whose frame stops updating must not contribute a value
    # frozen forward onto every later base bar forever — cap how old a
    # peer's source bar may be before it stops counting.
    stale_after = 3 * _bar_interval(base_ts)
    cols = {}
    for sym in universe:
        if name == "xs_rank" and sym == ctx.symbol:
            # E: never let the base symbol rank against itself. Skipping it
            # here — rather than including it and correcting the count
            # afterward — is correct whether or not `universe` happens to
            # carry the base's own key. breadth/dispersion are answering a
            # different question ("what is the whole book doing"), where
            # the base's own value legitimately belongs in the reduction.
            continue
        sub = ctx.for_symbol(sym)
        if sub is None:
            continue
        vals = _eval(node.args[0], sub)
        if not isinstance(vals, pd.Series):
            continue
        peer_ts = pd.to_datetime(sub.df["ts"], utc=True).values
        pos = np.searchsorted(peer_ts, base_ts, side="right") - 1
        arr = vals.to_numpy(dtype=float)
        clipped = np.clip(pos, 0, None)
        fresh = (pos >= 0) & (base_ts - peer_ts[clipped] <= stale_after)
        cols[sym] = np.where(fresh, arr[clipped], np.nan)

    if not cols:
        return pd.Series(np.nan, index=ctx.index)

    panel = pd.DataFrame(cols, index=ctx.index)
    mine = _eval(node.args[0], ctx)
    if not isinstance(mine, pd.Series):
        mine = pd.Series(mine, index=ctx.index)

    if name == "breadth":
        return panel.astype(float).mean(axis=1)
    if name == "dispersion":
        return panel.std(axis=1)
    # xs_rank: this symbol's position within the cross-section, in [0, 1].
    # The base's own key was already excluded from `panel` above, so
    # `valid` is the true peer count — no "-1" correction, and no
    # assumption that the base is even a member of its own universe.
    below = panel.lt(mine, axis=0).sum(axis=1)
    valid = panel.notna().sum(axis=1)
    denom = valid.where(valid > 0)
    # mine.notna(): when the base symbol's own value is undefined, every
    # `panel.lt(mine)` comparison is False, so `below` comes out 0 and an
    # unmasked rank would read as 0.0 — "weakest coin in the book" — which
    # is fabricated, not measured. NaN is the honest answer.
    return (below / denom).clip(0.0, 1.0).where(mine.notna())


# ── literal extraction: every number is a tunable gene ───────────────────
@dataclass
class Literal:
    """A tunable number inside an expression.

    Every numeric literal is automatically a parameter, with a range inferred
    from the feature registry. This is why there is no FAMILY_GENE_SPECS: the
    Strategist writes structure, an optimizer perturbs these, and the
    rendered text stays the single source of truth for both — so the Python
    evaluator and the Pine template can never score different strategies the
    way ema_trend's phantom fast_len/slow_len defaults did.
    """
    index: int          # position in document order
    value: float
    lo: float
    hi: float
    is_int: bool
    source: str         # "arg_spec" | "domain" | "fallback"


def _numeric_constants(tree: ast.Expression) -> list[ast.Constant]:
    """Numeric literals in DOCUMENT order.

    ast.walk() is breadth-first, so `adx(14) > 25` yields 25 before 14 —
    which would make apply_literals() silently assign tuned values to the
    wrong parameters. Source position is the stable ordering, and it matches
    between a tree and its deepcopy.
    """
    consts = [n for n in ast.walk(tree)
              if isinstance(n, ast.Constant)
              and isinstance(n.value, (int, float))
              and not isinstance(n.value, bool)]
    return sorted(consts, key=lambda n: (getattr(n, "lineno", 0),
                                         getattr(n, "col_offset", 0)))


def _unwrap_const(node):
    """A threshold may be negated: `< -1.5` is UnaryOp(USub, Constant)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node
    if isinstance(node, ast.UnaryOp) \
            and isinstance(node.op, (ast.USub, ast.UAdd)) \
            and isinstance(node.operand, ast.Constant):
        return node.operand
    return None


def _domain_of(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return FEATURES[node.func.id].domain
    if isinstance(node, ast.Name) and node.id in FEATURES:
        return FEATURES[node.id].domain
    return None


def _fallback_range(v: float) -> tuple:
    """No declared domain (a price level, an ATR multiple). Tune relative to
    the author's chosen value rather than refusing to tune it at all."""
    if v == 0:
        return (-1.0, 1.0, False, "fallback")
    lo, hi = sorted((v * 0.5, v * 2.0))
    return (lo, hi, float(v).is_integer() and abs(v) < 1000, "fallback")


def extract_literals(tree: ast.Expression) -> list[Literal]:
    ranges: dict[int, tuple] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            spec = FEATURES[node.func.id].arg_specs
            for arg, aspec in zip(node.args, spec):
                if aspec is SERIES_ARG or not isinstance(arg, ast.Constant):
                    continue
                if not isinstance(arg.value, (int, float)):
                    continue
                typ, lo, hi = aspec
                if lo is not None:
                    ranges[id(arg)] = (float(lo), float(hi), typ is int,
                                       "arg_spec")
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for i, operand in enumerate(operands):
                const = _unwrap_const(operand)
                if const is None or id(const) in ranges:
                    continue
                other = operands[i - 1] if i else operands[1]
                dom = _domain_of(other)
                if dom:
                    ranges[id(const)] = (float(dom[0]), float(dom[1]), False,
                                         "domain")

    out: list[Literal] = []
    for i, node in enumerate(_numeric_constants(tree)):
        lo, hi, is_int, src = ranges.get(
            id(node), _fallback_range(float(node.value)))
        out.append(Literal(i, node.value, lo, hi, is_int, src))
    return out


def apply_literals(tree: ast.Expression, values) -> ast.Expression:
    """Return a NEW tree with each numeric literal replaced, in document
    order. Integer-typed literals are rounded so a period stays a period."""
    values = list(values)
    originals = extract_literals(tree)
    if len(values) != len(originals):
        raise SpecError(f"expected {len(originals)} literal values, "
                        f"got {len(values)}")
    out = copy.deepcopy(tree)
    for node, v, lit in zip(_numeric_constants(out), values, originals):
        if lit.is_int:
            node.value = int(round(float(v)))
        else:
            r = round(float(v), 6)
            # render an integral float as an int so the Librarian's card reads
            # `adx(14) > 30`, not `adx(14) > 30.0`
            node.value = int(r) if r.is_integer() else r
    return ast.fix_missing_locations(out)


def render(tree: ast.Expression) -> str:
    """Back to source. This text is what the Librarian prints, so it must
    always re-parse."""
    return ast.unparse(tree.body)
