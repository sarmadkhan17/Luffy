"""A combination is a SET of AND-ed parts under a canonical hash.

`and` is commutative, so the same set reached from two different parents is
the same rule and must be evaluated once — otherwise the ledger double-counts
looks, and a search that counts its own looks wrongly cannot price them.

The hash carries the percentile RANK, never the threshold value, so a rule keeps
its identity if the percentiles are ever re-measured; the rendered expressions
stored alongside preserve exactly what was traded.
"""
import pytest

from trader.research.combo import (Combination, MAX_PARTS, compatible,
                                   subsets, window_key)
from trader.research.vocab import Part
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec

A = Part("ret24>p90", "gauge", "ret24", "ret(24) > 0.05", "0 - ret(24) > 0.06")
B = Part("st:above_ema50", "state", "st:above_ema50",
         "close > ema(50)", "close < ema(50)")
C = Part("ev:donch100", "event", "ev:donch100",
         "close > donchian_hi(100)", "close < donchian_lo(100)")
D = Part("ev:donch20", "event", "ev:donch20",
         "close > donchian_hi(20)", "close < donchian_lo(20)")
E = Part("ret24<p10", "gauge", "ret24", "ret(24) < -0.05",
         "0 - ret(24) < -0.06")
F = Part("fundz360>p90", "gauge", "fundz360", "funding_z(360) > 1.4",
         "0 - funding_z(360) > 1.5")


def _c(parts, tf="4h", geo="trail"):
    return Combination(parts=tuple(parts), tf=tf, geo=geo)


def test_part_order_does_not_change_the_hash():
    assert _c([A, B, C]).hash == _c([C, A, B]).hash


def test_a_different_percentile_is_a_different_combination():
    assert _c([A]).hash != _c([E]).hash


def test_timeframe_and_geometry_are_part_of_the_identity():
    assert _c([A], tf="4h").hash != _c([A], tf="1h").hash
    assert _c([A], geo="trail").hash != _c([A], geo="fixed").hash


def test_the_hash_ignores_how_it_was_reached():
    one = Combination((A, B), "4h", "trail", trigger="ret24>p90",
                      round="grow", parent="abc")
    two = Combination((B, A), "4h", "trail", trigger="st:above_ema50",
                      round="grow", parent="def")
    assert one.hash == two.hash


def test_both_legs_are_the_conjunction_of_their_parts():
    c = _c([A, B])
    assert c.long == "ret(24) > 0.05 and close > ema(50)"
    assert c.short == "0 - ret(24) > 0.06 and close < ema(50)"


def test_two_parts_of_one_gauge_are_incompatible():
    assert not compatible((A, E))


def test_two_events_are_incompatible():
    assert not compatible((C, D))
    assert compatible((C, A, B))


def test_a_combination_may_not_exceed_six_parts():
    many = [A, B, C,
            Part("rsi14>p90", "gauge", "rsi14", "rsi(14) > 70",
                 "100 - rsi(14) > 71"),
            Part("er30>p75", "gauge", "er30", "efficiency_ratio(30) > 0.3",
                 "efficiency_ratio(30) > 0.3"),
            Part("adx14>p75", "gauge", "adx14", "adx(14) > 25",
                 "adx(14) > 25"),
            F]
    assert len(many) == MAX_PARTS + 1
    assert not compatible(tuple(many))
    assert compatible(tuple(many[:MAX_PARTS]))


def test_a_combination_compiles_into_a_valid_spec():
    spec = _c([A, B, C]).to_spec()
    assert StrategySpec.validate(spec) == []
    compiled = compile_spec(spec)
    assert compiled.spec.timeframe == "4h"
    assert compiled.spec.direction == "both"


def test_the_spec_carries_the_chosen_geometry():
    spec = _c([C], geo="trail").to_spec()
    assert spec.exit.trail == {"kind": "atr", "mult": 4.0, "arm_at_r": 1.0}
    spec = _c([C], geo="fixed").to_spec()
    assert spec.exit.target == {"kind": "rr", "v": 3.0}


def test_requirements_are_the_union_of_the_parts():
    assert _c([C]).requires == ("ohlcv",)
    assert "funding" in _c([C, F]).requires


def test_the_window_is_what_is_needed_beyond_candles():
    assert window_key(("ohlcv",)) == "ohlcv"
    assert window_key(("ohlcv", "funding")) == "funding"
    assert window_key(("ref:spx", "ohlcv", "funding")) == "funding|ref:spx"


def test_subsets_are_every_one_part_ablation():
    c = _c([A, B, C])
    subs = subsets(c)
    assert len(subs) == 3
    assert {s.hash for s in subs} == {_c([B, C]).hash, _c([A, C]).hash,
                                      _c([A, B]).hash}
    assert all(s.k == 2 for s in subs)


def test_a_single_has_no_subsets():
    assert subsets(_c([A])) == []


def test_object_equality_agrees_with_the_canonical_hash():
    """A later `if c not in seen: seen.add(c)` must deduplicate by identity.

    The default frozen-dataclass equality compares every field in the order
    given, so it would treat two orderings of one rule as two rules — the
    double-counting the hash exists to prevent.
    """
    one, two = _c([A, B]), _c([B, A])
    assert one == two
    assert len({one, two}) == 1
    assert _c([A, B], geo="fixed") != _c([A, B], geo="trail")
    # provenance is how a rule was reached, not what it is
    assert Combination((A, B), "4h", "trail", round="grow",
                       parent="cafe") == one


def test_a_combination_survives_a_round_trip_through_a_dict():
    c = Combination((A, B), "1h", "fixed", trigger="ret24>p90",
                    round="grow", parent="cafe")
    back = Combination.from_dict(c.as_dict())
    assert back == c and back.hash == c.hash
