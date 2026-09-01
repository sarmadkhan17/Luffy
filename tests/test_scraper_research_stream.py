"""Two consumers, two screens. A finding is not a setup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain import ideas
from trader.brain.scraper import idea_score, research_score, streams_for

SETUP = {"title": "Funding divergence setup",
         "text": "Mean-reversion entry on the reclaim, stop-loss below the "
                 "swept low, take-profit at the range midpoint."}

FINDING = {"title": "Short-horizon reversal in cross-sectional crypto returns",
           "text": "We document that coins in the top decile of 24-hour "
                   "relative return underperform the bottom decile by 41 bps "
                   "over the following 8 hours. The effect is significant "
                   "out-of-sample and decays with a half-life of roughly two "
                   "days, and it persists after controlling for volatility."}


def test_named_constants_exist_and_are_distinct():
    """Verify that STRATEGY and RESEARCH constants exist, are distinct,
    and both are members of STREAMS."""
    assert hasattr(ideas, 'STRATEGY')
    assert hasattr(ideas, 'RESEARCH')
    assert ideas.STRATEGY != ideas.RESEARCH
    assert ideas.STRATEGY in ideas.STREAMS
    assert ideas.RESEARCH in ideas.STREAMS


def test_default_stream_is_strategy():
    """Verify that DEFAULT_STREAM points to the STRATEGY constant."""
    assert ideas.DEFAULT_STREAM == ideas.STRATEGY


def test_research_prose_scores_on_the_research_screen():
    assert research_score(FINDING) > 0


def test_the_strategy_screen_would_have_discarded_that_finding():
    # this is the bug the second scorer exists to fix
    assert idea_score(FINDING) < 1


def test_a_setup_routes_to_the_strategy_stream():
    """A trade setup routes to the strategy stream via the named constant."""
    assert ideas.STRATEGY in streams_for(SETUP)


def test_a_finding_routes_to_the_research_stream():
    """A research finding routes to the research stream via the named constant."""
    assert ideas.RESEARCH in streams_for(FINDING)
