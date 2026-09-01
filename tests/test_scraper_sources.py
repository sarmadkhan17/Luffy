"""Scraper source-widening tests: prefilter screen, param repair,
Atom parsing, source-yield learning."""
import json

import pytest


def _idea(title="EMA cross strategy", text="use ema 20/50 crossover entry",
          source="tradingview.com/ideas/btcusdt", iid=None):
    import hashlib
    iid = iid or ("tv_" + hashlib.md5(title.encode()).hexdigest()[:6])
    return {"source": source, "idea_id": iid, "title": title,
            "text": text, "age_h": 1.0}


# ── cheap screen ──────────────────────────────────────────────────────────
def test_screen_keeps_systematic():
    from trader.brain.scraper import idea_score
    assert idea_score(_idea()) >= 2
    paper = _idea("Mean reversion at 15m horizons in crypto",
                  "Binance pairs show significant directional reversal; "
                  "backtest with out-of-sample protocol")
    assert idea_score(paper) >= 2


def test_screen_drops_hype_and_news():
    from trader.brain.scraper import idea_score
    hype = _idea("PEPE to hit $1 — 1000x potential, buy now!",
                 "next moonshot gem, airdrop incoming")
    news = _idea("India plans first tokenized bonds using wholesale CBDC",
                 "regulatory announcement from the finance ministry")
    assert idea_score(hype) < 1
    assert idea_score(news) < 1


# ── helpers ────────────────────────────────────────────────────────────
class FakeLLM:
    available = True

    def __init__(self, reply, replies=None):
        self.reply = reply
        self.replies = list(replies or [])   # consumed per call, then repeat
        self.calls = 0

    def chat_json(self, prompt, deep=False):
        self.calls += 1
        if len(self.replies) >= self.calls:
            return self.replies[self.calls - 1]
        return self.reply


def _scraper_with(llm):
    import tempfile, pathlib, yaml
    from trader.brain.scraper import Scraper
    from trader.core.journal import Journal
    cfg = yaml.safe_load(open("config.yaml"))
    j = Journal(pathlib.Path(tempfile.mkdtemp()) / "h.db")
    h = Scraper(j, cfg, feed=None)
    h.llm = llm
    return h


def test_genome_repairs_range_strings():
    from trader.brain.scraper import repair_params
    p = repair_params("ma_cross", {"fast_len": "5..50",
                                   "slow_len": "30..200"})
    assert p == {"fast_len": 50, "slow_len": 200}   # clamped to bounds
    p2 = repair_params("rsi_extreme", {})
    assert p2["rsi_len"] == 14                      # defaults fill gaps


# ── Atom / entity parsing ────────────────────────────────────────────────
def test_scrape_feed_handles_atom(monkeypatch):
    import trader.brain.scraper as hv

    class R:
        status_code = 200
        text = ('<feed><entry><title>RSI divergence &amp; volume'
                '</title><updated>2026-08-25T10:00:00Z</updated>'
                '<content type="html">&lt;p&gt;A backtested setup using '
                'rsi divergence signals with clear entry rules&lt;/p&gt;'
                '</content></entry></feed>')

    monkeypatch.setattr(hv.requests, "get",
                        lambda *a, **k: R())
    items = hv.scrape_feed("https://example.com/.rss")
    assert len(items) == 1
    assert "RSI divergence & volume" in items[0]["title"]
    assert "backtested setup" in items[0]["text"]
    assert items[0]["age_h"] is not None


# ── source yield ──────────────────────────────────────────────────────────
def test_source_scores_rank_productive_sources():
    import tempfile, pathlib
    from trader.core.journal import Journal
    j = Journal(pathlib.Path(tempfile.mkdtemp()) / "sy.db")
    good = {"tradingview.com/ideas/btcusdt": {"scraped": 10, "extracted": 4,
                                              "accepted": 2}}
    bad = {"medium.com/feed/tag/algorithmic-trading": {"scraped": 40,
                                                       "extracted": 0,
                                                       "accepted": 0}}
    j.log_brain_event("harvest_cycle", "harvester", {"per_source": good})
    j.log_brain_event("harvest_cycle", "harvester", {"per_source": bad})
    h = _scraper_with(FakeLLM(None))
    h.journal = j
    s = h._source_scores()
    tv_key = "tradingview.com/ideas/btcusdt"
    med_key = "medium.com/feed/tag/algorithmic-trading"
    assert s[tv_key] > s[med_key]
    assert s[med_key] < 0.05                      # dead source near zero


def test_scrape_tv_scripts_requests_the_scripts_section(monkeypatch):
    """/ideas/ is people predicting; /scripts/ is the Pine library."""
    from trader.brain import scraper as S

    seen = []

    class _R:
        status_code = 200
        text = ""

    def _get(url, **kw):
        seen.append(url)
        return _R()

    monkeypatch.setattr(S.requests, "get", _get)
    s = _scraper_with(None)         # existing helper in this file, takes an llm
    s.tv_pages = 1
    s.scrape_tv_scripts("meanreversion")

    assert seen, "no request was made"
    assert "/scripts/" in seen[0], seen[0]
    assert "/ideas/" not in seen[0], seen[0]


# ── single creation path ─────────────────────────────────────────────────
def test_the_scraper_no_longer_creates_strategies():
    """One creation path. The Scraper queues; the Strategist writes."""
    from trader.brain import scraper as S

    for gone in ("extract_batch", "_genome_from", "_dual_gauntlet", "_deploy"):
        assert not hasattr(S.Scraper, gone), (
            f"Scraper.{gone} still exists — that is the second, "
            f"population-writing creation path")
