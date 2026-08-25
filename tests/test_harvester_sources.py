"""Harvester source-widening tests: prefilter screen, batch extraction,
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
    from trader.brain.harvester import idea_score
    assert idea_score(_idea()) >= 2
    paper = _idea("Mean reversion at 15m horizons in crypto",
                  "Binance pairs show significant directional reversal; "
                  "backtest with out-of-sample protocol")
    assert idea_score(paper) >= 2


def test_screen_drops_hype_and_news():
    from trader.brain.harvester import idea_score
    hype = _idea("PEPE to hit $1 — 1000x potential, buy now!",
                 "next moonshot gem, airdrop incoming")
    news = _idea("India plans first tokenized bonds using wholesale CBDC",
                 "regulatory announcement from the finance ministry")
    assert idea_score(hype) < 1
    assert idea_score(news) < 1


# ── batched extraction ────────────────────────────────────────────────────
class FakeLLM:
    available = True

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat_json(self, prompt, deep=False):
        self.calls += 1
        return self.reply


def _harvester_with(llm):
    import tempfile, pathlib, yaml
    from trader.brain.harvester import Harvester
    from trader.core.journal import Journal
    cfg = yaml.safe_load(open("config.yaml"))
    j = Journal(pathlib.Path(tempfile.mkdtemp()) / "h.db")
    h = Harvester(j, cfg, feed=None)
    h.llm = llm
    return h


def test_batch_maps_by_id():
    from trader.brain.harvester import Harvester
    ideas = [_idea(title=f"strategy {i}", iid=f"tv_{i}") for i in range(3)]
    reply = {"results": [
        {"id": "tv_0", "family": "ema_trend", "params": {},
         "hypothesis": "h"},
        {"id": "tv_1", "skip": True, "reason": "opinion"},
        {"id": "tv_2", "family": "not_a_family", "params": {}},
    ]}
    h = _harvester_with(FakeLLM(reply))
    reply["results"][0]["params"] = {"adx_min": 20, "pullback_atr": 0.8,
                                     "trend_tf": "15m"}
    out = h.extract_batch(ideas, {})
    assert set(out) == {"tv_0", "tv_1", "tv_2"}
    assert out["tv_1"] is None                    # skipped
    assert out["tv_2"] is None                    # unknown family rejected
    g = out["tv_0"]
    assert g is not None and g.family == "ema_trend"


def test_batch_single_call_for_many_ideas():
    ideas = [_idea(title=f"setup {i}", iid=f"tv_{i}") for i in range(8)]
    llm = FakeLLM({"results": []})
    h = _harvester_with(llm)
    h.extract_batch(ideas, {})
    assert llm.calls == 1                         # one call, not eight


def test_genome_rejects_out_of_schema_params():
    from trader.brain.harvester import Harvester
    g = Harvester._genome_from(
        type("S", (), {})(),  # unused self path via instance below
        {}) if False else None
    h = _harvester_with(FakeLLM(None))
    g = h._genome_from(_idea(iid="x1"),
                       {"family": "ema_trend",
                        "params": {"nonsense_gene": 5}})
    # unknown gene ignored, defaults fill in — genome survives
    assert g is not None and g.family == "ema_trend"
    assert "nonsense_gene" not in g.params
    assert set(g.params) == {"adx_min", "pullback_atr", "trend_tf"}


def test_genome_repairs_range_strings():
    from trader.brain.harvester import repair_params
    p = repair_params("ma_cross", {"fast_len": "5..50",
                                   "slow_len": "30..200"})
    assert p == {"fast_len": 50, "slow_len": 200}   # clamped to bounds
    p2 = repair_params("rsi_extreme", {})
    assert p2["rsi_len"] == 14                      # defaults fill gaps


# ── Atom / entity parsing ────────────────────────────────────────────────
def test_scrape_feed_handles_atom(monkeypatch):
    import trader.brain.harvester as hv

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
    h = _harvester_with(FakeLLM(None))
    h.journal = j
    s = h._source_scores()
    tv_key = "tradingview.com/ideas/btcusdt"
    med_key = "medium.com/feed/tag/algorithmic-trading"
    assert s[tv_key] > s[med_key]
    assert s[med_key] < 0.05                      # dead source near zero
