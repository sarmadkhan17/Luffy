"""Cross-sectional features — this coin against the rest of the book.

Every other feature in the registry answers a question about one symbol.
Measured on this repo's cached data, the strongest single predictor of the
next eight hours was `rel_strength_btc`, at IC -0.15 to -0.17 in every
regime. That is a cross-asset effect narrowed to a single reference asset.
These generalise it to the whole universe.

Each is a MARKER registered only so `dsl.parse()` knows the name and arity;
the real evaluation happens in `dsl._eval_xs`, because the inner expression
must be evaluated once per universe member rather than once for this symbol.
`htf(tf, expr)` uses the same mechanism for the same reason.
"""
from __future__ import annotations

from .features import SERIES_ARG, register

register("xs_rank", arg_specs=(SERIES_ARG,), domain=(0.0, 1.0))(
    lambda ctx, expr: expr)
register("breadth", arg_specs=(SERIES_ARG,), domain=(0.0, 1.0))(
    lambda ctx, expr: expr)
register("dispersion", arg_specs=(SERIES_ARG,), domain=(0.0, 10.0))(
    lambda ctx, expr: expr)
