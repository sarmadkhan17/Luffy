"""While the LLM provider is blocked (`brain.enabled: false`) nothing can turn a queued
STRATEGY idea into a strategy, so intake for that stream stops instead of accruing rows.
The RESEARCH stream has its own provider switch and keeps being queued."""
import pytest
import requests

from trader.brain import ideas as idea_queue
from trader.brain.crawler import DeepCrawler
from trader.brain.scraper import Scraper
from trader.core.journal import Journal

DUAL = {"idea_id": "dual_1",
        "title": "Funding divergence setup, backtested",
        "text": "A mean-reversion entry on the reclaim, stop-loss below the swept low, take-profit at the range "
                "midpoint. Backtest shows the setup is significant out-of-sample: it captures a decile momentum "
                "effect worth 41 bps over the following 8 hours, and decays with a half-life near two days, "
                "holding in the trending regime.",
        "source": "arxiv.org/q-fin", "url": "https://arxiv.org/abs/2"}


def _cfg(brain_enabled=None, research_provider=False):
    brain = {"daily_token_budget": 0, "base_url": "", "model_fast": "", "model_deep": "",
             "max_tokens_per_call": 1, "max_tokens_deep": 1}
    if brain_enabled is not None:
        brain["enabled"] = brain_enabled
    cfg = {"scraper": {"enabled": True, "ideas_per_cycle": 4, "tv_pages": 0, "tv_tags": [], "rss_feeds": []},
           "crawler": {"enabled": True}, "brain": brain, "strategies": {}, "risk": {}}
    if research_provider:
        cfg["research"] = {"closure": {"proposal_provider_enabled": True}}
    return cfg


def _scraper(tmp_path, cfg, items):
    j = Journal(tmp_path / "s.db")
    s = Scraper(j, cfg, feed=None, notifier=None)
    s.tags = []
    s.scrape_tv_scripts = lambda tag: []
    calls = []
    s.scrape_feeds = lambda: calls.append(1) or items
    return j, s, calls


@pytest.mark.parametrize("brain,provider,expected", [
    (None, False, {"strategy", "research"}),          # absent key means enabled, as in brain/llm.py
    (True, False, {"strategy", "research"}),
    (False, False, set()),
    (False, True, {"research"}),
])
def test_live_streams_follow_the_provider_switches(brain, provider, expected):
    assert idea_queue.live_streams(_cfg(brain, provider)) == expected
    assert idea_queue.live_streams({}) == {"strategy", "research"}


def test_no_consumer_means_no_fetch_and_no_queue(tmp_path):
    j, s, calls = _scraper(tmp_path, _cfg(False), [DUAL])
    stats = s.harvest_once()
    assert stats["skipped_reason"] == "no_live_idea_consumer"
    assert not calls, "feeds must not be fetched when nothing can use the result"
    assert idea_queue.pending(j) == [] and idea_queue.pending(j, stream="research") == []


def test_research_stream_still_queued_but_strategy_stream_is_not(tmp_path):
    j, s, _ = _scraper(tmp_path, _cfg(False, research_provider=True), [DUAL])
    stats = s.harvest_once()
    assert stats["queued_research"] == 1 and stats["queued_strategy"] == 0
    assert {i["idea_id"] for i in idea_queue.pending(j, stream="research")} == {"dual_1"}
    assert idea_queue.pending(j, stream="strategy") == []


def test_enabled_provider_queues_both_streams_as_before(tmp_path):
    j, s, _ = _scraper(tmp_path, _cfg(True), [DUAL])
    stats = s.harvest_once()
    assert stats["queued_strategy"] == 1 and stats["queued_research"] == 1


def test_crawler_skips_the_cycle_without_touching_the_network(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used while the strategy stream has no consumer")
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests.Session, "get", boom, raising=False)
    j = Journal(tmp_path / "c.db")
    stats = DeepCrawler(j, _cfg(False), feed=None, notifier=None).crawl_once()
    assert stats["skipped_reason"] == "strategy_stream_has_no_live_consumer"
    assert stats["pages"] == 0 and "queued" not in stats
    assert idea_queue.pending(j) == []
