"""A trade needs a strategy that says so.

Measured on 204 live decisions (2026-08-24..09-02): at the 4h horizon the
blended analyst+strategy score returned -0.695% a call and won 34.6%
(t=-3.84), and BOTH sides lost there — BUY -0.335%, SELL -1.117%. Market
drift can make one side lose; it cannot make both lose at the same horizon.
That is negative skill, and until this gate the analyst blend could open a
position with no strategy behind it at all.

The gate must (a) refuse a directional call no strategy proposed, (b) refuse
one every strategy contradicts, (c) leave an agreeing call alone, and
(d) keep grading what it vetoed, so reopening it is an evidence decision.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.core.types import Action, StrategySignal


def _orc(require=True):
    from trader.core.journal import Journal
    from trader.engine.orchestrator import Orchestrator
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    return Orchestrator([], Journal(Path(path)),
                        cfg={"scouts": {"require_strategy_signal": require}})


def _sig(action, sid="s1", conf=0.7):
    return StrategySignal(strategy_id=sid, strategy_name=sid,
                          symbol="BTC/USDT", action=action,
                          confidence=conf, rationale="test")


def _apply(orc, action, sigs):
    """Exactly what decide() runs — the real function, not a copy of it."""
    from trader.engine.orchestrator import strategy_gate
    if not orc.require_strategy_signal:
        return action, "", None
    return strategy_gate(action, sigs)


def test_the_flag_defaults_on():
    assert _orc().require_strategy_signal is True


def test_the_flag_can_be_turned_off():
    orc = _orc(require=False)
    assert orc.require_strategy_signal is False
    act, veto, lean = _apply(orc, Action.BUY, [])
    assert act is Action.BUY and veto == "" and lean is None


def test_a_call_no_strategy_proposed_is_held():
    act, veto, lean = _apply(_orc(), Action.BUY, [])
    assert act is Action.HOLD
    assert "no strategy signal" in veto
    assert lean is Action.BUY          # kept, so the veto is graded


def test_a_call_every_strategy_contradicts_is_held():
    act, veto, lean = _apply(_orc(), Action.SELL, [_sig(Action.BUY)])
    assert act is Action.HOLD and "no strategy agrees" in veto
    assert lean is Action.SELL


@pytest.mark.parametrize("action", [Action.BUY, Action.SELL])
def test_an_agreeing_strategy_passes_both_sides(action):
    """Long and short must survive the gate identically — a gate that
    quietly favours one side turns a both-directions mechanism, which is
    the only one measured to work, into a market-direction bet."""
    act, veto, lean = _apply(_orc(), action, [_sig(action)])
    assert act is action and veto == "" and lean is None


def test_one_agreeing_strategy_is_enough_among_dissenters():
    act, veto, _ = _apply(_orc(), Action.BUY,
                          [_sig(Action.SELL, "a"), _sig(Action.BUY, "b")])
    assert act is Action.BUY and veto == ""


def test_hold_is_never_vetoed():
    act, veto, lean = _apply(_orc(), Action.HOLD, [])
    assert act is Action.HOLD and veto == "" and lean is None


# ── the score a strategy leads with ─────────────────────────────────────
def test_a_lone_signal_clears_every_threshold_the_system_produces():
    """The live failure: Donchian at 0.60 confidence scored 0.2454 against a
    0.2629 threshold, because a single signal reaches at most
    0.6 * 0.45 / 1.45 = 0.186 once seven analysts are averaged in. It had
    signalled for a week and taken zero trades."""
    from trader.engine.orchestrator import strategy_score
    s = strategy_score([_sig(Action.BUY, conf=0.6)], {})
    assert s == pytest.approx(0.6)
    assert s > 0.34            # the top of the adaptive threshold band


@pytest.mark.parametrize("action,sign", [(Action.BUY, 1), (Action.SELL, -1)])
def test_direction_is_signed_symmetrically(action, sign):
    from trader.engine.orchestrator import strategy_score
    assert strategy_score([_sig(action, conf=0.6)], {}) == \
        pytest.approx(0.6 * sign)


def test_disagreeing_strategies_cancel_rather_than_pick_a_winner():
    from trader.engine.orchestrator import strategy_score
    assert strategy_score([_sig(Action.BUY, "a", 0.6),
                           _sig(Action.SELL, "b", 0.6)], {}) == \
        pytest.approx(0.0)


def test_a_strategy_weighted_down_speaks_more_quietly():
    from trader.engine.orchestrator import strategy_score
    sigs = [_sig(Action.BUY, "a", 0.6), _sig(Action.SELL, "b", 0.6)]
    s = strategy_score(sigs, {"a": 3.0, "b": 1.0})
    assert s == pytest.approx((0.6 * 3 - 0.6) / 4)
    assert s > 0


def test_no_signal_is_no_opinion_not_a_zero_vote():
    from trader.engine.orchestrator import strategy_score
    assert strategy_score([], {}) == 0.0


def test_the_lead_can_be_turned_off():
    orc = _orc()
    assert orc.strategy_leads is True
    from trader.core.journal import Journal
    from trader.engine.orchestrator import Orchestrator
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    off = Orchestrator([], Journal(Path(path)),
                       cfg={"scouts": {"strategy_leads": False}})
    assert off.strategy_leads is False
