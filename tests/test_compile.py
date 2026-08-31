import numpy as np
import pandas as pd
import pytest

from trader.core.types import Action, Snapshot
from trader.strategy.compile import compile_spec
from trader.strategy.dsl import SpecError
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(**kw) -> StrategySpec:
    base = dict(
        id="t1", name="Trend Pullback Probe",
        thesis="Short-horizon trends persist because discretionary entries lag "
               "the impulse; buying a shallow pullback inside an aligned stack "
               "captures the continuation at reduced adverse excursion.",
        invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="15m", direction="long",
        entry_long="close > ema(20) and ema(20) > ema(50)",
        entry_short="", filters=["adx(14) > 20"],
        exit=ExitSpec(), regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


@pytest.fixture
def frame():
    rng = np.random.default_rng(5)
    n = 600
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n)))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.002, "low": close * 0.998,
        "close": close, "volume": rng.uniform(50, 500, n)})


def test_entries_are_boolean_arrays_of_frame_length(frame):
    lo, sh = compile_spec(_spec()).entries({"15m": frame})
    assert lo.dtype == bool and len(lo) == len(frame)
    assert not sh.any(), "a long-only spec must never emit shorts"


def test_filters_are_anded_with_entry(frame):
    with_f = compile_spec(_spec()).entries({"15m": frame})[0]
    without = compile_spec(_spec(filters=[])).entries({"15m": frame})[0]
    assert with_f.sum() <= without.sum()
    assert np.array_equal(with_f, with_f & without)


def test_data_requires_is_derived(frame):
    assert compile_spec(_spec()).data_requires == ("ohlcv",)


def test_invalid_expression_fails_at_compile_time():
    with pytest.raises(SpecError):
        compile_spec(_spec(entry_long="close > nope(3)"))


def test_invalid_spec_fails_at_compile_time():
    with pytest.raises(SpecError):
        compile_spec(_spec(thesis="too short"))


def test_conflicting_directions_resolve_to_no_trade(frame):
    c = compile_spec(_spec(direction="both", entry_long="close > ema(2)",
                           entry_short="close > ema(2)", filters=[]))
    lo, sh = c.entries({"15m": frame})
    assert not (lo & sh).any()
    assert not lo.any() and not sh.any()


def test_to_evaluator_matches_the_last_bar_of_entries(frame):
    c = compile_spec(_spec())
    lo, _ = c.entries({"15m": frame})
    snap = Snapshot(symbol="BTC/USDT", ts="",
                    price=float(frame["close"].iloc[-1]),
                    dfs={"15m": frame}, market_type="futures")
    sig = c.to_evaluator()(c.spec, snap)
    assert (sig is not None and sig.action == Action.BUY) == bool(lo[-1])


def test_to_evaluator_returns_strategy_signal_shape(frame):
    c = compile_spec(_spec(entry_long="close > ema(2)", filters=[]))
    snap = Snapshot(symbol="ETH/USDT", ts="", price=1.0,
                    dfs={"15m": frame}, market_type="futures")
    sig = c.to_evaluator()(c.spec, snap)
    if sig is not None:
        assert sig.strategy_id == "t1" and 0.0 < sig.confidence <= 1.0
        assert sig.symbol == "ETH/USDT" and sig.rationale


def test_to_evaluator_missing_timeframe_returns_none(frame):
    c = compile_spec(_spec())
    snap = Snapshot(symbol="X", ts="", price=1.0, dfs={"1h": frame},
                    market_type="futures")
    assert c.to_evaluator()(c.spec, snap) is None


def test_evaluator_is_dispatchable_by_library_evaluate(frame):
    """The cutover contract: registering under a family name must make
    library.evaluate() find it with zero orchestrator change."""
    from trader.strategy import library
    from trader.strategy.genome import Genome
    c = compile_spec(_spec(entry_long="close > ema(2)", filters=[]))
    library.register_evaluator("spec:t1", c.to_evaluator())
    g = Genome(strategy_id="t1", family="spec:t1", hypothesis="x" * 70,
               invalidation="y" * 30, regime_filter=frozenset(),
               markets=frozenset({"futures"}), params={})
    snap = Snapshot(symbol="BTC/USDT", ts="", price=1.0,
                    dfs={"15m": frame}, market_type="futures")
    library.evaluate(g, snap)      # must not raise


def test_markdown_contains_thesis_and_readable_logic():
    md = compile_spec(_spec()).to_markdown()
    assert "Trend Pullback Probe" in md
    assert "close > ema(20)" in md
    assert "adx(14) > 20" in md
    assert "Invalidation" in md
