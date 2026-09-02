"""A spec must beat a rotation of its own entries across independent symbols.

The PF gate cannot tell a mechanism from a market direction — the same
arithmetic scores ALWAYS-LONG at 1.28 on drift. The rotation null can, but a
single symbol's percentile is noisy and a median throws away the sample. What
carries the evidence is the SHAPE across symbols: under no edge the
percentiles are uniform, so the binomial tail is a test rather than a
threshold someone picked.

Calibrated on real specs, not invented:
  Donchian Breakout Trail over 15 symbols     p = 3.5e-04
  momo_persist on its discovery universe      p = 1.1e-03
  the same rule on 9 symbols it never saw     p = 2.7e-01
"""
import random

import pytest

from trader.strategy.null_baseline import MIN_SYMBOLS, consistency_p

DONCHIAN = [0.60, 0.88, 0.82, 0.90, 0.90, 0.92, 0.60, 0.97, 0.88,
            0.73, 0.92, 0.48, 0.95, 0.78, 0.90]
MOMO_DISCOVERY = [0.87, 0.91, 0.95, 0.72, 0.88, 0.62, 0.90, 0.55]
MOMO_HELD_OUT = [0.63, 0.60, 0.80, 0.87, 0.78, 0.15, 0.97, 0.40, 0.65]


def test_the_one_validated_strategy_clears_the_gate():
    assert consistency_p(DONCHIAN) < 0.01


def test_each_half_of_it_clears_on_its_own():
    """Eight symbols and seven, independently, in the same direction."""
    assert consistency_p(DONCHIAN[:8]) < 0.05
    assert consistency_p(DONCHIAN[8:]) < 0.05


def test_a_rule_that_only_worked_where_it_was_found_is_refused():
    assert consistency_p(MOMO_HELD_OUT) > 0.05


def test_sitting_exactly_on_the_no_edge_line_is_not_an_edge():
    """Percentiles land on a grid, so ties at a cut are ordinary. With `>=`
    at the cut, ten symbols all at the no-edge median scored p=0.003."""
    assert consistency_p([0.5] * 10) > 0.05


def test_percentiles_scattered_around_the_middle_do_not_pass():
    assert consistency_p([0.52, 0.44, 0.61, 0.38, 0.55, 0.47, 0.66]) > 0.05


def test_a_single_lucky_symbol_does_not_carry_the_rest():
    assert consistency_p([0.99, 0.4, 0.5, 0.3, 0.55, 0.45]) > 0.05


def test_too_few_symbols_is_silence_not_a_verdict():
    assert consistency_p([0.99, 0.99, 0.99]) is None
    assert consistency_p([]) is None
    assert consistency_p([0.9] * MIN_SYMBOLS) is not None


def test_missing_percentiles_are_dropped_not_counted():
    assert consistency_p([0.95, None, 0.95, None, 0.95, 0.95]) == \
        consistency_p([0.95, 0.95, 0.95, 0.95])


def test_the_false_positive_rate_is_under_the_nominal_level():
    """The claim in the docstring, measured rather than asserted."""
    rng = random.Random(11)
    for n in (8, 15):
        fp = sum(consistency_p([rng.random() for _ in range(n)]) < 0.01
                 for _ in range(4000)) / 4000
        assert fp < 0.01, f"{n} symbols: {fp:.3%}"


def test_every_symbol_at_the_ceiling_is_as_improbable_as_it_gets():
    assert consistency_p([1.0] * 10) == pytest.approx(3 * 0.10 ** 10, rel=1e-6)
