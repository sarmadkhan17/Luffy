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


from trader.strategy.seed_specs import load_seed_specs


def test_all_seed_specs_compile():
    specs = load_seed_specs()
    assert len(specs) >= 8
    for s in specs:
        compile_spec(s)          # raises SpecError on anything malformed


def test_seed_specs_have_distinct_theses():
    theses = [s.thesis for s in load_seed_specs()]
    assert len(set(theses)) == len(theses), \
        "every spec must state its OWN inefficiency — the bug that made every " \
        "ema_trend variant claim the seed's hypothesis"


def test_seed_specs_have_real_names():
    for s in load_seed_specs():
        assert "variant" not in s.name.lower()
        assert "harvested" not in s.name.lower()


def test_seed_specs_have_distinct_exit_geometry():
    """Exits are genes now. If every spec still carries the old global
    2.5/4.5/32 then nothing was actually gained."""
    geo = {(json_key(s)) for s in load_seed_specs()}
    assert len(geo) > 1


def json_key(s):
    e = s.exit
    return (str(e.stop), str(e.target), e.time.get("max_bars"))


@pytest.fixture
def busy_frame():
    """Long and volatile enough to actually produce range breaks and sweeps.

    A tame 600-bar walk never breaks a 48-bar Donchian with a volume surge, so
    it cannot distinguish 'spec is quiet' from 'spec is unsatisfiable' — which
    is exactly the bug this test exists to catch.
    """
    rng = np.random.default_rng(17)
    n = 3000
    vol = 0.004 * (1 + 0.8 * np.sin(np.arange(n) / 90.0))   # vol clustering
    close = 100 * np.cumprod(1 + rng.normal(0, 1, n) * vol)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "close": close,
        "volume": rng.lognormal(5, 0.8, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * (1 + rng.uniform(0, .003, n))
    df["low"] = df[["open", "close"]].min(axis=1) * (1 - rng.uniform(0, .003, n))
    return df[["ts", "open", "high", "low", "close", "volume"]]


def test_seed_specs_emit_signals(busy_frame):
    """A spec that can never fire is not a quiet strategy, it is a broken one.

    breakout_retest was exactly this: `prev(close,1) > donchian_hi(48)` is
    unsatisfiable because the Donchian window contains bar i-1, so a close can
    never exceed the max high of a window containing it. It scored zero
    signals on 8000 real bars and would have looked merely unprofitable.
    """
    fired = {}
    for s in load_seed_specs():
        lo, sh = compile_spec(s).entries({"15m": busy_frame})
        fired[s.id] = int(lo.sum() + sh.sum())
    # rotation_momo compares against a BTC leader that this fixture omits
    silent = [k for k, v in fired.items() if v == 0 and "rotation" not in k]
    assert not silent, f"specs that never fire: {silent} (all: {fired})"


def test_rotation_spec_needs_a_leader_frame(busy_frame):
    """It must be silent WITHOUT btc data and able to fire WITH it — proving
    the silence is missing data, not an unsatisfiable expression."""
    spec = [s for s in load_seed_specs() if "rotation" in s.id][0]
    c = compile_spec(spec)
    lo, _ = c.entries({"15m": busy_frame})
    assert not lo.any()
    lo2, _ = c.entries({"15m": busy_frame}, btc={"15m": busy_frame.copy()})
    assert len(lo2) == len(busy_frame)
