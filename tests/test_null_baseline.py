"""A strategy must beat its own signals fired at a random time.

The gauntlet compared profit factor against an absolute 1.15 and had no null
hypothesis, so it could not tell a real edge from the exit geometry's
arithmetic. Measured on real production candles: an ALWAYS-LONG rule scores
median PF 0.76 with a 38% win rate, against the 35.7% a random entry earns
mechanically from TP 4.5 ATR / SL 2.5 ATR. Every spec in the book scored
0.46-0.81 — i.e. at or BELOW the no-edge baseline. Several were worse than
entering at random, and the gate never noticed.

The null: circularly rotate the entry array. Signal count and clustering are
preserved exactly; only the alignment to market state is destroyed.
"""
import numpy as np
import pandas as pd
import pytest

from trader.strategy.null_baseline import (assess, edge_percentile,
                                           null_pfs, rotate_entries)


@pytest.fixture
def entries():
    a = np.zeros(1000, dtype=bool)
    a[[10, 11, 12, 400, 401, 700, 950]] = True
    return a


def test_rotation_preserves_signal_count(entries):
    for off in (1, 37, 500, 999):
        assert rotate_entries(entries, off).sum() == entries.sum()


def test_rotation_preserves_clustering(entries):
    """Three consecutive signals must stay three consecutive signals, or the
    null would be easier to beat than the strategy purely on trade spacing."""
    def runs(a):
        return sorted(len(list(g)) for k, g in
                      __import__("itertools").groupby(a) if k)
    assert runs(rotate_entries(entries, 123)) == runs(entries)


def test_rotation_actually_moves_the_signals(entries):
    assert not np.array_equal(rotate_entries(entries, 250), entries)


def test_zero_offset_is_identity(entries):
    assert np.array_equal(rotate_entries(entries, 0), entries)


def test_edge_percentile_ranks_against_the_null():
    null = [0.5, 0.6, 0.7, 0.8, 0.9]
    assert edge_percentile(1.0, null) == 1.0      # beats every draw
    assert edge_percentile(0.4, null) == 0.0      # beats none
    assert edge_percentile(0.75, null) == pytest.approx(0.6)


def test_edge_percentile_of_empty_null_is_undefined():
    assert edge_percentile(1.2, []) is None


def test_null_pfs_returns_one_pf_per_draw():
    n = 900
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.003, "low": close * 0.997,
        "close": close, "volume": 100.0})
    lo = np.zeros(n, dtype=bool)
    lo[300::40] = True
    from trader.strategy.spec import ExitSpec
    out = null_pfs(lo, np.zeros(n, dtype=bool), df, ExitSpec(),
                   {"risk_per_trade_pct": 1.0}, draws=12, seed=7)
    assert len(out) == 12
    assert all(p >= 0 for p in out)


def test_null_is_deterministic_for_a_seed():
    n = 900
    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.003, "low": close * 0.997,
        "close": close, "volume": 100.0})
    lo = np.zeros(n, dtype=bool); lo[200::30] = True
    from trader.strategy.spec import ExitSpec
    kw = dict(draws=8, seed=42)
    a = null_pfs(lo, np.zeros(n, dtype=bool), df, ExitSpec(),
                 {"risk_per_trade_pct": 1.0}, **kw)
    b = null_pfs(lo, np.zeros(n, dtype=bool), df, ExitSpec(),
                 {"risk_per_trade_pct": 1.0}, **kw)
    assert a == b


# ── the null must be measured on the SAME slice as the actual ────────────
# assess() sliced nothing: it built the null from the full train+test frame
# while the caller passed a TEST-half profit factor. Over a test half that
# rose 20-54%, that mismatch alone inflated every percentile. The tell was an
# always-long rule scoring 100% — a rule whose rotation is itself, so its
# only honest answer is 0%.

def _trending_frame(n=1200, drift=0.0008):
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.004, n)))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.003, "low": close * 0.997,
        "close": close, "volume": 100.0})


def test_a_rule_that_is_always_on_cannot_beat_its_own_rotation():
    """Rotating an all-true array returns the same array, so the null is the
    strategy. It must not read as an edge."""
    from trader.strategy.spec import ExitSpec
    df = _trending_frame()
    n = len(df)
    always = np.ones(n, dtype=bool)
    pfs = null_pfs(always, np.zeros(n, dtype=bool), df, ExitSpec(),
                   {"risk_per_trade_pct": 1.0}, draws=25, seed=3)
    assert pfs, "null must produce draws"
    assert len(set(round(p, 6) for p in pfs)) == 1, \
        "every rotation of an all-on rule is the same rule"
    actual = pfs[0]
    assert edge_percentile(actual, pfs) == 0.0


def test_assess_measures_the_null_on_the_requested_slice():
    """The null for a test-half PF must come from the test half."""
    from trader.strategy.compile import compile_spec
    from trader.strategy.spec import ExitSpec, StrategySpec
    df = _trending_frame()
    spec = StrategySpec(
        id="slice_probe", name="Slice Alignment Probe",
        thesis="A slice-alignment probe: the null and the actual profit "
               "factor must be computed over the very same bars.",
        invalidation="Retire below profit factor 1.0 over 30 trades.",
        provenance={}, universe={"include": []}, timeframe="15m",
        direction="long", entry_long="close > 0", entry_short="",
        filters=[], exit=ExitSpec(), regime_filter=[], markets=["futures"])
    c = compile_spec(spec)
    full = assess(c, {"15m": df}, {"risk_per_trade_pct": 1.0}, 1.0, draws=25)
    tail = assess(c, {"15m": df}, {"risk_per_trade_pct": 1.0}, 1.0, draws=25,
                  split=0.7, part="test")
    assert full["bars"] == len(df)
    assert tail["bars"] < full["bars"], "test slice must be shorter"
