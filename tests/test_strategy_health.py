"""Is this strategy still doing what it was validated to do?

The governing premise is "is this working NOW", but the only live check was a
fixed threshold: PF below 0.85 over 10 trades and you are decayed. That
answers a different question. A mechanism validated at 37% win rate will show
a run of 3 losses in 5 trades about a quarter of the time, and a fixed rule
retires it. The same rule keeps a broken strategy alive as long as it has not
yet accumulated its 10 trades.

The honest question is whether live results are still consistent with the
validated envelope, or whether the gap is now larger than sampling noise can
explain. Until enough trades exist to tell, the answer is INSUFFICIENT — not
a pass and not a fail.
"""
import pytest

from trader.strategy.health import Health, assess_health


def test_no_trades_yet_is_insufficient_not_healthy():
    h = assess_health(expected_winrate=0.37, wins=0, losses=0)
    assert h.verdict == "INSUFFICIENT"
    assert h.enough is False


def test_a_handful_of_losses_is_not_yet_evidence_of_breakage():
    """3 losses from 3 at a 37% win rate happens ~25% of the time."""
    h = assess_health(expected_winrate=0.37, wins=0, losses=3)
    assert h.verdict == "INSUFFICIENT"


def test_live_matching_the_validated_rate_reads_as_consistent():
    h = assess_health(expected_winrate=0.37, wins=37, losses=63)
    assert h.verdict == "CONSISTENT"
    assert h.enough is True


def test_a_long_run_far_below_the_validated_rate_is_diverged():
    """2 wins in 60 at a validated 37% is not bad luck."""
    h = assess_health(expected_winrate=0.37, wins=2, losses=58)
    assert h.verdict == "DIVERGED"


def test_outperformance_is_reported_but_never_called_breakage():
    """Doing better than validated is not a fault; it is still worth seeing."""
    h = assess_health(expected_winrate=0.37, wins=55, losses=5)
    assert h.verdict == "CONSISTENT"
    assert h.observed_winrate > h.expected_winrate


def test_the_observed_rate_is_reported():
    h = assess_health(expected_winrate=0.40, wins=5, losses=15)
    assert h.observed_winrate == pytest.approx(0.25)


def test_probability_is_a_one_sided_underperformance_tail():
    """The number quoted must be the chance of doing THIS BADLY or worse."""
    h = assess_health(expected_winrate=0.50, wins=1, losses=9)
    assert 0.0 <= h.p_underperform <= 0.02


def test_an_impossible_expected_rate_does_not_crash():
    for wr in (0.0, 1.0):
        assert assess_health(expected_winrate=wr, wins=3, losses=3).verdict \
            in {"INSUFFICIENT", "CONSISTENT", "DIVERGED"}


def test_health_carries_what_a_human_needs_to_act():
    h = assess_health(expected_winrate=0.37, wins=2, losses=58)
    assert h.trades == 60 and "37" in h.summary and "DIVERGED" in h.summary
