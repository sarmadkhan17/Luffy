"""Where this coin sits against the rest of the book."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy import dsl
from trader.strategy.features import FeatureCtx


def _df(v):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=len(v), freq="15min",
                            tz="UTC"),
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.ones(len(v))})


def _ctx(mine, others):
    """Base ctx whose OWN key ("ME") is also present in `universe`, the way
    rolling.py's callers always build it."""
    uni = {"ME": {"15m": _df(mine)}}
    for name, v in others.items():
        uni[name] = {"15m": _df(v)}
    return FeatureCtx(frames={"15m": _df(mine)}, tf="15m", universe=uni,
                      symbol="ME")


def _ctx_no_self(mine, others):
    """Base ctx whose universe holds ONLY other members — a legitimate
    configuration where the base is not, itself, a universe member."""
    uni = {name: {"15m": _df(v)} for name, v in others.items()}
    return FeatureCtx(frames={"15m": _df(mine)}, tf="15m", universe=uni)


def test_xs_rank_is_one_for_the_strongest_member():
    ctx = _ctx(np.array([1.0, 4.0]), {"A": np.array([1.0, 2.0]),
                                      "B": np.array([1.0, 3.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.iloc[-1] == 1.0


def test_xs_rank_is_zero_for_the_weakest_member():
    ctx = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 2.0]),
                                      "B": np.array([1.0, 3.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.iloc[-1] == 0.0


def test_xs_rank_is_nan_without_a_universe():
    """No peers means no cross-sectional answer — not a fabricated 0.5."""
    ctx = FeatureCtx(frames={"15m": _df(np.ones(4))}, tf="15m")
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.isna().all()


def test_xs_rank_is_nan_when_the_base_value_is_undefined_even_if_peers_are_warm():
    """rolling.recent_verdict passes FULL peer frames while the base frame
    is sliced to the recent window, so a peer's indicator can be fully
    warmed up at a timestamp where the base's OWN indicator is still NaN
    (not enough history in the slice). `mine` NaN must make every
    comparison undefined, never 'weakest in the book' (0.0): every
    `panel.lt(NaN)` is False, so an unmasked rank silently reads as 0.0."""
    n_full = 100
    ts_full = pd.date_range("2026-01-01", periods=n_full, freq="15min",
                            tz="UTC")
    full_close = np.linspace(100.0, 200.0, n_full)
    full_df = pd.DataFrame({
        "ts": ts_full, "open": full_close, "high": full_close,
        "low": full_close, "close": full_close,
        "volume": np.ones(n_full)})
    # base: sliced to only the last 20 bars -- sma(50) can never be defined
    # over such a short slice, even though the full history exists.
    base_df = full_df.iloc[-20:].reset_index(drop=True)
    uni = {"ME": {"15m": full_df}, "PEER": {"15m": full_df}}
    ctx = FeatureCtx(frames={"15m": base_df}, tf="15m", universe=uni)
    out = dsl.evaluate(dsl.parse("xs_rank(sma(50))"), ctx)
    assert out.isna().all()


def test_breadth_is_the_fraction_of_the_book_satisfying_the_condition():
    ctx = _ctx(np.array([5.0, 5.0]), {"A": np.array([5.0, 5.0]),
                                      "B": np.array([1.0, 1.0])})
    out = dsl.evaluate(dsl.parse("breadth(close > 3)"), ctx)
    assert out.iloc[-1] == pytest.approx(2 / 3)


def test_xs_rank_of_a_derivative_feature_does_not_leak_the_base_symbols_own_series():
    """Derivative frames (funding, OI, basis) are per-symbol raw observation
    frames held on the base FeatureCtx. A peer evaluated with those same raw
    frames would be scored on the BASE symbol's funding/OI for every member
    of the book, so xs_rank(funding_pct(...)) would compare a symbol against
    itself. Honest behaviour: a peer with no derivs of its own is NaN, so
    the cross-section has no valid members and the rank is NaN."""
    n = 60
    ts = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    close = np.linspace(100.0, 110.0, n)
    df = pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close,
                       "close": close, "volume": np.ones(n)})
    funding_df = pd.DataFrame({"ts": ts,
                               "value": np.linspace(-0.01, 0.01, n)})
    uni = {"ME": {"15m": df}, "PEER": {"15m": df}}
    ctx = FeatureCtx(frames={"15m": df}, tf="15m",
                     derivs={"funding": funding_df}, universe=uni)
    out = dsl.evaluate(dsl.parse("xs_rank(funding_pct(24))"), ctx)
    assert out.isna().all()


def test_for_symbol_child_context_does_not_inherit_base_derivs():
    ts = pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")
    close = np.ones(10)
    df = pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close,
                       "close": close, "volume": np.ones(10)})
    derivs = {"funding": pd.DataFrame({"ts": ts, "value": np.zeros(10)})}
    ctx = FeatureCtx(frames={"15m": df}, tf="15m", derivs=derivs,
                     universe={"OTHER": {"15m": df}})
    sub = ctx.for_symbol("OTHER")
    assert sub is not None
    assert sub.derivs is None


def test_xs_rank_denominator_is_not_off_by_one_when_base_is_not_a_universe_member():
    """`denom = valid - 1` assumes the panel always contains a redundant
    self-entry for the base symbol. When the universe genuinely does not
    include the base's own key — a legitimate configuration, comparing
    against "the rest of the book" without redundantly re-adding yourself —
    that subtraction removes one real peer from the denominator and the
    `.clip(0, 1)` hides the resulting bias."""
    ctx = _ctx_no_self(np.array([5.0]),
                       {"A": np.array([3.0]), "B": np.array([10.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    # A(3) < mine(5) < B(10): 1 of the 2 REAL peers is below -> 1/2 = 0.5.
    # The old code computed denom = valid(2) - 1 = 1 -> 1/1 = 1.0.
    assert out.iloc[-1] == pytest.approx(0.5)


def test_xs_rank_excludes_the_base_symbols_own_key_from_the_panel():
    """When the base symbol's own key IS present in `universe` (as
    rolling.py's callers always include it), it must not be double-counted
    as a peer — it should neither count toward `below` nor toward the
    denominator, matching what the base itself already knows about itself."""
    ctx = _ctx(np.array([5.0]), {"A": np.array([3.0]), "B": np.array([10.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.iloc[-1] == pytest.approx(0.5)


def test_xs_rank_does_not_carry_a_stale_peer_forward_forever():
    """searchsorted(..., side='right') - 1 maps a base bar onto the LAST
    peer bar at or before it — with no bound on how far back that peer bar
    may be. A peer whose frame ends early must stop counting once it goes
    stale, not freeze its last value onto every later base bar."""
    n = 10
    ts = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    mine = np.full(n, 50.0)
    base_df = pd.DataFrame({"ts": ts, "open": mine, "high": mine,
                            "low": mine, "close": mine,
                            "volume": np.ones(n)})
    # A: alive for the whole window, rises above `mine` by the end.
    a_close = np.linspace(10.0, 90.0, n)
    a_df = pd.DataFrame({"ts": ts, "open": a_close, "high": a_close,
                         "low": a_close, "close": a_close,
                         "volume": np.ones(n)})
    # B: stops updating after bar 2 (index 0-2), frozen at a LOW value that
    # would (if carried forward forever) make `mine` look relatively strong
    # at the end of the window.
    b_close = np.array([1.0, 1.0, 1.0])
    b_df = pd.DataFrame({"ts": ts[:3], "open": b_close, "high": b_close,
                         "low": b_close, "close": b_close,
                         "volume": np.ones(3)})
    uni = {"A": {"15m": a_df}, "B": {"15m": b_df}}
    ctx = FeatureCtx(frames={"15m": base_df}, tf="15m", universe=uni)
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    # At the last bar B is 7 bars (>3 * the 15m interval) stale and must be
    # masked out, leaving only A(90) as a peer: mine(50) < A(90), so 0 of 1
    # peers is below -> 0.0. Unmasked, B's frozen 1.0 would count as
    # "below" and give 1/2 = 0.5 instead.
    assert out.iloc[-1] == pytest.approx(0.0)


def test_dispersion_rises_when_members_diverge():
    tight = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 1.0])})
    wide = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 50.0])})
    t = dsl.evaluate(dsl.parse("dispersion(close)"), tight).iloc[-1]
    w = dsl.evaluate(dsl.parse("dispersion(close)"), wide).iloc[-1]
    assert w > t
