"""The Scraper's whole job is putting ideas in the queue for the Strategist.

`brain/scraper.py` imports the queue module as `ideas` at module level and
then shadows it with a local list inside `harvest_once`, so the call that
records an idea hit a list instead of the module and the loop died before
queueing anything. The idea queue then starves the Strategist, which is the
only thing that writes new strategies.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain import ideas as idea_queue
from trader.brain.scraper import Scraper
from trader.core.journal import Journal


def _scraper(tmp_path, harvested):
    j = Journal(tmp_path / "s.db")
    cfg = {"scraper": {"enabled": True, "ideas_per_cycle": 4, "tv_pages": 0,
                       "tv_tags": [], "rss_feeds": []},
           "brain": {"daily_token_budget": 0, "base_url": "", "model_fast": "",
                     "model_deep": "", "max_tokens_per_call": 1,
                     "max_tokens_deep": 1},
           "strategies": {}, "risk": {}}
    s = Scraper(j, cfg, feed=None, notifier=None)
    s.tags = []
    s.scrape_tv_scripts = lambda tag: []
    s.scrape_feeds = lambda: harvested
    s.llm = None                      # no tokens spent; extraction is a no-op
    return j, s


def test_harvest_once_puts_the_scraped_idea_in_the_queue(tmp_path):
    idea = {"idea_id": "rss_test_1",
            "title": "Funding divergence setup after a liquidation cascade",
            "text": "When perpetual funding spikes beyond two standard "
                    "deviations while open interest falls, the crowded side "
                    "has been flushed. That divergence is a mean-reversion "
                    "entry: go long on the reclaim, stop-loss below the swept "
                    "low, take-profit at the prior range midpoint. The exit "
                    "is time-based if momentum does not resume.",
            "source": "example.com/feed", "url": "https://example.com/1"}
    j, s = _scraper(tmp_path, [idea])

    stats = s.harvest_once()

    assert stats["scraped"] == 1
    queued = {i["idea_id"] for i in idea_queue.pending(j)}
    assert "rss_test_1" in queued, f"idea never reached the queue: {queued}"


def test_a_finding_reaches_the_research_stream(tmp_path):
    finding = {"idea_id": "arxiv_1",
               "title": "Short-horizon reversal in cross-sectional returns",
               "text": "Coins in the top decile of 24-hour relative return "
                       "underperform the bottom decile by 41 bps over the "
                       "following 8 hours. Significant out-of-sample, decays "
                       "with a half-life near two days, persists after "
                       "controlling for volatility regime.",
               "source": "arxiv.org/q-fin", "url": "https://arxiv.org/abs/1"}
    j, s = _scraper(tmp_path, [finding])

    stats = s.harvest_once()

    assert stats["queued_research"] == 1
    assert {i["idea_id"] for i in idea_queue.pending(j, stream="research")} \
        == {"arxiv_1"}


def test_source_scores_rank_by_queued_not_dead_accepted_extracted(tmp_path):
    j = Journal(tmp_path / "s2.db")
    cfg = {"scraper": {"enabled": True, "ideas_per_cycle": 4, "tv_pages": 0,
                       "tv_tags": [], "rss_feeds": []},
           "brain": {"daily_token_budget": 0, "base_url": "", "model_fast": "",
                     "model_deep": "", "max_tokens_per_call": 1,
                     "max_tokens_deep": 1},
           "strategies": {}, "risk": {}}
    s = Scraper(j, cfg, feed=None, notifier=None)

    j.log_brain_event("harvest_cycle", "harvester", {
        "per_source": {
            "productive.com": {"scraped": 3, "queued": 3},
            "unproductive.com": {"scraped": 3, "queued": 0}}})
    j.log_brain_event("harvest_cycle", "harvester", {
        "per_source": {
            "productive.com": {"scraped": 2, "queued": 2},
            "unproductive.com": {"scraped": 2, "queued": 0}}})

    scores = s._source_scores()

    assert scores["productive.com"] > scores["unproductive.com"]
    assert scores["unproductive.com"] == 0.0
