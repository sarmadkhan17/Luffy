"""The geometry a spec was validated with must be the geometry it trades.

ExitEngine took `genomes={id: st.params}`, and _load_spec_population sets
st.params = {}. So for every spec:

  * _max_hold() returned None and fell back to ExitConfig.max_hold_hours = 36,
    against Donchian Breakout Trail's 500 bars at 4h = 2000 hours;
  * the trail used config's 2.2 ATR against the spec's 4.0;
  * TP1 closed half the position at 1.5R although the spec has NO target;
  * and _max_hold converted bars with a hardcoded `* 15 / 60`, so even a spec
    that DID carry max_hold_bars was wrong by 16x on a 4h timeframe.

A backtest that validates one exit geometry while the engine runs another is
not evidence about anything. `grep ExitSpec trader/engine/` found nothing.
"""
import pytest

from trader.engine.exits import ExitEngine, SpecExit


def _engine(spec_exits=None):
    e = object.__new__(ExitEngine)
    e.genomes = {}
    e.spec_exits = spec_exits or {}
    from trader.engine.exits import ExitConfig
    e.c = ExitConfig()
    return e


def test_hold_is_converted_with_the_specs_own_timeframe():
    """500 bars at 4h is 2000 hours, not 500 * 15/60 = 125."""
    e = _engine({"s1": SpecExit(max_bars=500, timeframe="4h",
                                trail_atr_mult=4.0, has_target=False)})
    assert e._max_hold({"strategy_id": "s1"}) == pytest.approx(2000.0)


def test_a_fifteen_minute_spec_still_converts_correctly():
    e = _engine({"s1": SpecExit(max_bars=32, timeframe="15m",
                                trail_atr_mult=2.0, has_target=True)})
    assert e._max_hold({"strategy_id": "s1"}) == pytest.approx(8.0)


def test_an_unknown_strategy_falls_back_to_the_global_default():
    assert _engine()._max_hold({"strategy_id": "nope"}) is None


def test_the_specs_trail_multiple_is_used_not_the_global_one():
    e = _engine({"s1": SpecExit(max_bars=500, timeframe="4h",
                                trail_atr_mult=4.0, has_target=False)})
    assert e._trail_mult({"strategy_id": "s1"}) == 4.0
    assert e._trail_mult({"strategy_id": "other"}) == e.c.trail_atr_mult


def test_a_spec_with_no_target_takes_no_partial_profit():
    """Closing half at 1.5R on a strategy whose edge is the fat right tail
    removes precisely the trades that pay for the losers."""
    e = _engine({"s1": SpecExit(max_bars=500, timeframe="4h",
                                trail_atr_mult=4.0, has_target=False)})
    assert e._takes_partial({"strategy_id": "s1"}) is False


def test_a_spec_with_a_target_still_takes_its_partial():
    e = _engine({"s1": SpecExit(max_bars=96, timeframe="1h",
                                trail_atr_mult=2.0, has_target=True)})
    assert e._takes_partial({"strategy_id": "s1"}) is True


def test_legacy_genomes_keep_the_old_fifteen_minute_conversion():
    """Genome params are 15m bars by construction; do not break them."""
    e = _engine()
    e.genomes = {"g1": {"max_hold_bars": 32}}
    assert e._max_hold({"strategy_id": "g1"}) == pytest.approx(8.0)


def test_spec_exit_reads_its_timeframe_and_geometry_from_a_real_spec():
    from trader.strategy.spec import ExitSpec, StrategySpec
    spec = StrategySpec(
        id="dbt", name="Donchian Breakout Trail",
        thesis="A break of a multi-week range persists because risk is "
               "repriced slowly and participants scale in over days.",
        invalidation="Retire below profit factor 1.0 over 40 trades.",
        provenance={}, universe={"include": []}, timeframe="4h",
        direction="both", entry_long="close > donchian_hi(100)",
        entry_short="close < donchian_lo(100)", filters=[],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "none"},
                      trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
                      time={"max_bars": 500}),
        regime_filter=[], markets=["futures"])
    se = SpecExit.from_spec(spec)
    assert se.timeframe == "4h" and se.max_bars == 500
    assert se.trail_atr_mult == 4.0 and se.has_target is False
