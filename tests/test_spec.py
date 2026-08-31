import pytest

from trader.strategy.spec import ExitSpec, StrategySpec


def _valid() -> StrategySpec:
    return StrategySpec(
        id="seed_test",
        name="Funding Exhaustion Fade",
        thesis="Perpetual funding paid by crowded longs marks positioning "
               "exhaustion; fading price after an extreme funding z-score "
               "captures the unwind as leveraged longs are forced out.",
        invalidation="Retire if OOS profit factor falls below 1.0 over 30 trades.",
        provenance={"source_kind": "authored", "author": "quant"},
        universe={"include": ["BTC/USDT"], "exclude": [], "min_volume_usdt": 0},
        timeframe="15m",
        direction="short",
        entry_long="",
        entry_short="funding_z(96) > 2.0",
        filters=["adx(14) < 25"],
        exit=ExitSpec(stop={"kind": "atr", "mult": 1.8},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"},
                      time={"max_bars": 24},
                      signal_exit=""),
        regime_filter=["RANGING"],
        markets=["futures"],
    )


def test_roundtrip_is_lossless():
    s = _valid()
    assert StrategySpec.from_dict(s.to_dict()) == s


def test_json_roundtrip_is_lossless():
    s = _valid()
    assert StrategySpec.from_json(s.to_json()) == s


def test_valid_spec_has_no_errors():
    assert StrategySpec.validate(_valid()) == []


def test_short_thesis_rejected():
    s = _valid()
    s.thesis = "it goes up"
    assert any("thesis" in e for e in StrategySpec.validate(s))


def test_missing_invalidation_rejected():
    s = _valid()
    s.invalidation = ""
    assert any("invalidation" in e for e in StrategySpec.validate(s))


def test_direction_must_have_matching_entry():
    s = _valid()
    s.direction = "long"          # but entry_long is ""
    assert any("entry_long" in e for e in StrategySpec.validate(s))


def test_unknown_timeframe_rejected():
    s = _valid()
    s.timeframe = "3m"
    assert any("timeframe" in e for e in StrategySpec.validate(s))


def test_unknown_exit_stop_kind_rejected():
    s = _valid()
    s.exit.stop = {"kind": "vibes"}
    assert any("stop" in e for e in StrategySpec.validate(s))


def test_auto_generated_name_rejected():
    """The whole point of a spec is a name a human or the Strategist chose."""
    s = _valid()
    s.name = "ema_trend variant (22.0)"
    assert any("name" in e for e in StrategySpec.validate(s))


def test_harvested_suffix_name_rejected():
    s = _valid()
    s.name = "ema_trend harvested"
    assert any("name" in e for e in StrategySpec.validate(s))


def test_bad_regime_rejected():
    s = _valid()
    s.regime_filter = ["SIDEWAYS"]
    assert any("regime" in e for e in StrategySpec.validate(s))


def test_empty_markets_rejected():
    s = _valid()
    s.markets = []
    assert any("markets" in e for e in StrategySpec.validate(s))


def test_max_bars_out_of_range_rejected():
    s = _valid()
    s.exit.time = {"max_bars": 0}
    assert any("max_bars" in e for e in StrategySpec.validate(s))
