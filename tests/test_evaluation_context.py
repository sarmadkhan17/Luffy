"""A spec whose features need context must not silently score zero.

`xs_rank`, `breadth` and `dispersion` need `FeatureCtx.universe`; the funding
and basis features need `derivs`; `rel_strength_btc` and friends need `btc`.
A caller that omits any of them gets NaN, which the DSL turns into "no
signal" — so the spec takes zero trades and reads as a mechanism that does
not work, rather than as one that was never evaluated.

That is not hypothetical. `scripts/screen_mechanisms.py` omitted all three
for its entire life, so every cross-sectional, carry and BTC-relative
mechanism it ever printed took zero trades, and `Analyst._null_percentiles`
omitted `universe`, putting the same hole in the admission path. Measured at
4h on ten symbols: a cross-sectional momentum rule took 0 trades without
context and 874 with it.

These tests pin the CONTRACT — a context-hungry spec produces trades through
the production evaluation paths — rather than the numbers, which move with
the data.
"""
import numpy as np
import pandas as pd
import pytest

from trader.strategy import spec_evidence
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec

TF = "4h"
GEO = ExitSpec(stop={"kind": "atr", "mult": 2.0}, target={"kind": "none"},
               trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
               time={"max_bars": 200})


def _frame(seed, n=1200):
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0002, 0.02, n))
    return pd.DataFrame({
        "ts": pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC"),
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1000.0),
        "taker_buy": np.full(n, 500.0)})


def _spec(lo, sh, requires=("ohlcv",)):
    return StrategySpec(
        id="ctx_probe", name="ctx probe",
        thesis=("Probe spec asserting that the evaluation context reaches the "
                "feature layer; it makes no claim about a market edge."),
        invalidation="Probe only; never admitted to the book under any result.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe=TF, direction="both", entry_long=lo, entry_short=sh,
        filters=[], exit=GEO, regime_filter=[], markets=["futures"],
        data_requires=list(requires))


@pytest.fixture
def book():
    frames = {f"S{i}/USDT": _frame(i) for i in range(6)}
    frames["BTC/USDT"] = _frame(99)
    return frames


def _entries(spec, frames, sym, **ctx):
    compiled = compile_spec(spec)
    lo, sh = compiled.entries(spec_evidence.frames_for(frames[sym], TF),
                              symbol=sym, **ctx)
    return int(np.sum(lo)) + int(np.sum(sh))


def test_cross_sectional_needs_the_universe(book):
    spec = _spec("xs_rank(ret(30)) > 0.8", "xs_rank(ret(30)) < 0.2")
    universe = {s: {TF: d} for s, d in book.items()}
    assert _entries(spec, book, "S0/USDT") == 0, \
        "without a universe this must be silent, not a fabricated signal"
    assert _entries(spec, book, "S0/USDT", universe=universe) > 0


def test_btc_relative_needs_the_leader(book):
    spec = _spec("rel_strength_btc(30) > 0.05", "rel_strength_btc(30) < -0.05")
    btc = {TF: book["BTC/USDT"]}
    assert _entries(spec, book, "S0/USDT") == 0
    assert _entries(spec, book, "S0/USDT", btc=btc) > 0


def test_the_admission_path_supplies_the_universe():
    """The regression that matters: `Analyst._null_percentiles` evaluated
    specs with no `universe`, so a cross-sectional spec would have been
    admitted or refused on zero trades."""
    import inspect

    from trader.brain.analyst import Analyst
    src = inspect.getsource(Analyst._null_percentiles)
    assert "universe=universe" in src, \
        "the admission path must pass the universe to the evaluator"
    assert "funding=" in src, \
        "the null must be charged the same carry as the actual result"


def test_the_screen_supplies_the_context():
    """`screen_mechanisms.py` is the discovery tool. It produced every
    'nothing survives' verdict this project has recorded while passing no
    context at all."""
    from pathlib import Path
    src = Path("scripts/screen_mechanisms.py").read_text()
    call = src[src.index("r=vector_walk_forward"):][:200]
    assert "**ctx" in call, "the screen must pass btc/derivs/universe"
    assert "universe=uni_held if is_held else uni_disc" in src, \
        "discovery and held-out symbols must be ranked within their own half"
