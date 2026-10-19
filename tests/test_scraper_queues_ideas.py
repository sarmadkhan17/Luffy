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
        "source_intake": "fetched_selected_queued.v1", "per_source": {
            "productive.com": {"fetched": 3, "selected": 3, "queued": 3},
            "unproductive.com": {"fetched": 3, "selected": 3, "queued": 0}}})
    j.log_brain_event("harvest_cycle", "harvester", {
        "source_intake": "fetched_selected_queued.v1", "per_source": {
            "productive.com": {"fetched": 2, "selected": 2, "queued": 2},
            "unproductive.com": {"fetched": 2, "selected": 2, "queued": 0}}})

    scores = s._source_scores()

    assert scores["productive.com"] > scores["unproductive.com"]
    assert scores["unproductive.com"] == 0.0


def test_an_item_scoring_on_both_screens_reaches_both_streams(tmp_path):
    dual = {"idea_id": "dual_1",
            "title": "Funding divergence setup, backtested",
            "text": "A mean-reversion entry on the reclaim, stop-loss below "
                    "the swept low, take-profit at the range midpoint. "
                    "Backtest shows the setup is significant out-of-sample: "
                    "it captures a decile momentum effect worth 41 bps over "
                    "the following 8 hours, and decays with a half-life "
                    "near two days, holding in the trending regime.",
            "source": "arxiv.org/q-fin", "url": "https://arxiv.org/abs/2"}
    j, s = _scraper(tmp_path, [dual])

    stats = s.harvest_once()

    assert stats["queued_strategy"] == 1
    assert stats["queued_research"] == 1
    assert {i["idea_id"] for i in idea_queue.pending(j, stream="strategy")} \
        == {"dual_1"}
    assert {i["idea_id"] for i in idea_queue.pending(j, stream="research")} \
        == {"dual_1"}


# Shared relevance routing checks, merged from test_scraper_research_stream.py
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


def test_source_yield_records_fetched_selected_and_accepted(tmp_path):
    import json
    items = [{'idea_id': f'i{i}', 'source': 'https://example.org/feed',
              'title': 'Momentum entry', 'text': 'When momentum rises, enter long with a stop loss and exit on reversal. ' * 2}
             for i in range(10)]
    j, s = _scraper(tmp_path, items)
    # A preexisting usable item is fetched again but neither selected nor requeued.
    idea_queue.record(j, items[0])
    stats = s.harvest_once()
    event = json.loads(j.query("SELECT detail FROM brain_events WHERE kind='harvest_cycle'")[0]['detail'])
    counts = event['per_source']['https://example.org/feed']
    assert counts == {'fetched': 10, 'selected': 4, 'queued': 4}
    assert stats['scraped'] == 10 and stats['new'] == 9
    assert s._source_scores()['example.org'] == 4 / 15


def test_legacy_selected_only_measurement_cannot_drive_source_rank(tmp_path):
    j, s = _scraper(tmp_path, [])
    j.log_brain_event('harvest_cycle', 'harvester', {
        'per_source': {'example.org': {'scraped': 1, 'queued': 1}}})
    assert s._source_scores() == {}


def test_pure_research_passage_reaches_queue_even_with_zero_strategy_score(tmp_path):
    finding = {'idea_id': 'pure', 'source': 'arxiv.org',
               'title': 'Empirical market relationships',
               'text': 'We document that a predictor correlates significantly with future returns over weekly horizons. Regression evidence predicts subsequent price changes across markets.'}
    assert idea_queue.score(finding['text'], finding['title']) == 0
    j, s = _scraper(tmp_path, [finding])
    assert s.harvest_once()['queued_research'] == 1
    assert [i['idea_id'] for i in idea_queue.pending(j, stream='research')] == ['pure']
    assert idea_queue.pending(j, stream='strategy') == []
    idea_queue.mark_consumed(j, 'pure', 'observed', stream='research')
    assert idea_queue.pending(j, stream='research') == []


def test_selected_intake_does_not_count_rejected_queue_writes(tmp_path, monkeypatch):
    import json
    items = [{'idea_id': f'i{i}', 'source': 'example.org', 'title': 'Momentum entry',
              'text': 'When momentum rises enter long with a stop loss and exit on reversal. ' * 2}
             for i in range(10)]
    j, s = _scraper(tmp_path, items)
    record = idea_queue.record
    monkeypatch.setattr(idea_queue, 'record', lambda j, item, **kw:
                        False if item['idea_id'] in {'i0', 'i1'} else record(j, item, **kw))
    s.harvest_once()
    event = json.loads(j.query("SELECT detail FROM brain_events WHERE kind='harvest_cycle'")[0]['detail'])
    assert event['per_source']['example.org'] == {'fetched': 10, 'selected': 4, 'queued': 2}
    assert s._source_scores()['example.org'] == 2 / 15
