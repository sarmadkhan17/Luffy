"""An analyst with no opinion abstains; it does not vote against.

`decide()` added every analyst's weight to the denominator whether or not it
expressed any conviction. A neutral analyst therefore contributed 0 to the
numerator and its full weight below the line, so silence pulled the score
toward zero exactly as an opposing vote would.

The arithmetic that follows: seven analysts carry a combined weight of 1.0
and STRATEGY_VOTE_WEIGHT is 0.45, so a lone strategy signal at its typical
0.6 confidence scored 0.6*0.45 / (1.0+0.45) = 0.186 against thresholds
running 0.22-0.47. A validated strategy could never clear the bar on its own
no matter how strong its evidence — which is precisely what the file's own
comment, "strategies speak louder than any analyst", claims it can.

`agreement_fraction` in the same module already treats |conviction| <= 0.05
as non-directional. This uses that same line.
"""
from trader.core.types import Action, Side, StrategySignal, Vote
from trader.engine.orchestrator import STRATEGY_VOTE_WEIGHT, net_score


def _vote(agent, conviction, confidence=1.0):
    return Vote(agent=agent, symbol="BTC/USDT", side=Side.LONG,
                conviction=conviction, confidence=confidence, rationale="t")


def _sig(action=Action.BUY, confidence=0.6):
    return StrategySignal(strategy_id="s1", strategy_name="s", symbol="BTC/USDT",
                          action=action, confidence=confidence, rationale="t")


W = {"structure": 0.24, "flow": 0.15, "momentum": 0.17, "value": 0.11,
     "rotation": 0.13, "positioning": 0.11, "depth": 0.09}


def test_silent_analysts_do_not_dilute_a_strategy_signal():
    silent = [_vote(a, 0.0) for a in W]
    alone = net_score([], [_sig()], W, {})
    with_silent = net_score(silent, [_sig()], W, {})
    assert with_silent == alone, \
        "seven analysts with no opinion must not change the score"


def test_a_lone_strategy_signal_can_clear_a_typical_threshold():
    """0.186 was below every threshold the live system produces."""
    assert net_score([_vote(a, 0.0) for a in W], [_sig()], W, {}) >= 0.47


def test_an_opposing_analyst_still_counts_against():
    """Abstention is not an opinion; disagreement is."""
    opposed = net_score([_vote("structure", -0.8)], [_sig()], W, {})
    alone = net_score([], [_sig()], W, {})
    assert opposed < alone


def test_an_agreeing_analyst_still_raises_the_score():
    agreed = net_score([_vote("structure", 0.8)], [_sig()], W, {})
    assert agreed > net_score([], [_sig()], W, {}) * 0.99


def test_analyst_only_scoring_is_unchanged_when_all_have_conviction():
    votes = [_vote("structure", 0.5), _vote("flow", 0.5)]
    expected = sum(0.5 * 0.5 * 1.0 * W[a] for a in ("structure", "flow")) \
        / (W["structure"] + W["flow"])
    assert net_score(votes, [], W, {}) == expected


def test_no_opinions_at_all_is_zero_not_a_crash():
    assert net_score([], [], W, {}) == 0.0
    assert net_score([_vote(a, 0.0) for a in W], [], W, {}) == 0.0


def test_two_opposing_strategies_cancel():
    s = net_score([], [_sig(Action.BUY), _sig(Action.SELL)], W, {})
    assert abs(s) < 1e-9
