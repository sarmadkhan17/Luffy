"""Exit geometry is the yardstick every search result is measured against.

A fixed target amputates the tail a continuation mechanism lives on: every
sweep before 2026-09-02 used trail={"kind":"none"} and that single omission
hid the one mechanism that works. Both shapes are screened, and both must be
defined ONCE — a search that scores candidates under a geometry the screen
no longer uses is comparing numbers that were never comparable.
"""
from trader.strategy.geometries import GEOS
from trader.strategy.spec import StrategySpec, ExitSpec


def test_both_shapes_exist():
    assert set(GEOS) == {"fixed", "trail"}
    assert all(isinstance(g, ExitSpec) for g in GEOS.values())


def test_the_trail_is_the_shape_the_book_was_admitted_under():
    g = GEOS["trail"]
    assert g.stop == {"kind": "atr", "mult": 2.0}
    assert g.target == {"kind": "none"}
    assert g.trail == {"kind": "atr", "mult": 4.0, "arm_at_r": 1.0}
    assert g.time == {"max_bars": 500}


def test_the_fixed_shape_takes_a_three_r_target():
    g = GEOS["fixed"]
    assert g.stop == {"kind": "atr", "mult": 3.0}
    assert g.target == {"kind": "rr", "v": 3.0}
    assert g.trail == {"kind": "none"}


def test_every_geometry_is_a_valid_spec_exit():
    for name, geo in GEOS.items():
        spec = StrategySpec(
            id=f"probe_{name}", name=f"probe {name}",
            thesis="x" * 80, invalidation="y" * 40, provenance={},
            universe={"include": []}, timeframe="4h", direction="both",
            entry_long="close > ema(50)", entry_short="close < ema(50)",
            filters=[], exit=geo, regime_filter=[], markets=["futures"])
        assert StrategySpec.validate(spec) == []
