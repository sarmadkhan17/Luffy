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
        if name == "htf":
            return _eval_htf(node, ctx)
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
