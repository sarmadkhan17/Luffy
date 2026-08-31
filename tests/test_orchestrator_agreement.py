"""Directional-agreement tally (orchestrator.agreement_fraction).

Regression: strategy signals used to be counted as `None` and scored via
`(v.conviction if v else net) * sign(net) > 0` — always True for a None. An
OPPOSING strategy signal therefore still registered as agreement, raising
`frac`, which LOWERS the execution threshold and RAISES reported confidence.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.types import Action, Side, StrategySignal, Vote
from trader.engine.orchestrator import agreement_fraction


def _sig(action):
    return StrategySignal(strategy_id="s", strategy_name="n", symbol="X/USDT",
                          action=action, confidence=0.6, rationale="r")


def _vote(conv):
    side = Side.LONG if conv > 0 else (Side.SHORT if conv < 0 else Side.FLAT)
    return Vote(agent="a", symbol="X/USDT", side=side, conviction=conv,
                confidence=0.7, rationale="r")


def test_opposing_signal_lowers_agreement():
    votes = [_vote(0.5), _vote(0.4)]
    net = 0.4
    aligned = agreement_fraction(votes, [_sig(Action.BUY)], net)
    opposed = agreement_fraction(votes, [_sig(Action.SELL)], net)
    assert aligned == 1.0
    assert opposed < aligned, (aligned, opposed)
    assert opposed == 2 / 3


def test_more_opposing_signals_keep_lowering_it():
    votes = [_vote(0.5)]
    net = 0.4
    one = agreement_fraction(votes, [_sig(Action.SELL)], net)
    two = agreement_fraction(votes, [_sig(Action.SELL), _sig(Action.SELL)], net)
    assert two < one, (one, two)


def test_short_side_symmetry():
    votes = [_vote(-0.5), _vote(-0.4)]
    net = -0.4
    aligned = agreement_fraction(votes, [_sig(Action.SELL)], net)
    opposed = agreement_fraction(votes, [_sig(Action.BUY)], net)
    assert aligned == 1.0
    assert opposed == 2 / 3


def test_low_conviction_votes_excluded_and_floor_applies():
    # |conviction| <= 0.05 is noise and is not counted
    assert agreement_fraction([_vote(0.01), _vote(-0.02)], [], 0.1) == 0.5
    # fewer than 2 opinions -> neutral 0.5
    assert agreement_fraction([_vote(0.9)], [], 0.5) == 0.5
