"""Two consumers, two screens. A finding is not a setup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_research_prose_scores_on_the_research_screen():
    assert research_score(FINDING) > 0


def test_the_strategy_screen_would_have_discarded_that_finding():
    # this is the bug the second scorer exists to fix
    assert idea_score(FINDING) < 1


def test_a_setup_routes_to_the_strategy_stream():
    assert "strategy" in streams_for(SETUP)


def test_a_finding_routes_to_the_research_stream():
    assert "research" in streams_for(FINDING)
