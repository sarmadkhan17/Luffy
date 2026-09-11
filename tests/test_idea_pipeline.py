"""Researcher -> Strategist: the idea queue that connects the two halves.

Before this, `kernel._mechanism_once` called `SpecWriter.write()` with no
idea, so DeepSeek invented from nothing every 3h while the harvester's 400+
scraped ideas rotted in `brain_events` carrying nothing but a 120-char title.
These tests pin the contract that makes the handoff real: ideas persist with
the TEXT that holds the mechanism, the Strategist consumes the best one, and
consumption is recorded so the same idea is not retried forever.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain import ideas
from trader.core.journal import Journal


@pytest.fixture
def journal(tmp_path) -> Journal:
    return Journal(tmp_path / "ideas.db")


def _idea(iid: str, **kw) -> dict:
    base = {"idea_id": iid, "title": f"title {iid}",
            "text": "A mechanism: funding is deeply negative so shorts pay "
                    "longs; covering is price-insensitive buying.",
            "source": "arxiv.org", "link": f"https://x/{iid}"}
    base.update(kw)
    return base


# ── record: the text must survive ────────────────────────────────────────
def test_record_persists_the_text_not_just_the_title(journal):
    ideas.record(journal, _idea("a1"))
    got = ideas.next_idea(journal)
    assert got["idea_id"] == "a1"
    assert "price-insensitive buying" in got["text"]


def test_record_persists_the_url_so_provenance_survives(journal):
    ideas.record(journal, _idea("a1"))
    assert ideas.next_idea(journal)["url"] == "https://x/a1"


def test_record_writes_the_kind_the_harvester_dedupes_on(journal):
    """`Harvester._already_processed` counts kind='harvest_idea' rows."""
    ideas.record(journal, _idea("a1"))
    n = journal.query("SELECT COUNT(*) n FROM brain_events "
                      "WHERE kind='harvest_idea' AND subject='a1'")[0]["n"]
    assert n == 1


def test_record_truncates_giant_passages(journal):
    long = "Enter long when RSI crosses back above 30, stop at 2 ATR. " * 900
    ideas.record(journal, _idea("a1", text=long))
    assert len(ideas.next_idea(journal)["text"]) <= ideas.MAX_TEXT


def test_record_is_idempotent_on_idea_id(journal):
    ideas.record(journal, _idea("a1"))
    ideas.record(journal, _idea("a1"))
    assert len(ideas.pending(journal)) == 1


# ── next_idea: pick, and never pick a consumed one ───────────────────────
def test_next_idea_is_none_on_an_empty_queue(journal):
    assert ideas.next_idea(journal) is None


def test_consumed_ideas_are_never_handed_out_again(journal):
    ideas.record(journal, _idea("a1"))
    assert ideas.next_idea(journal)["idea_id"] == "a1"
    ideas.mark_consumed(journal, "a1", outcome="rejected")
    assert ideas.next_idea(journal) is None


def test_consumption_is_recorded_against_the_idea_id(journal):
    """The old sketch deduped on spec_admitted/spec_rejected, whose subject
    is a SPEC id — it never matched, so every idea would be retried forever."""
    ideas.record(journal, _idea("a1"))
    ideas.mark_consumed(journal, "a1", outcome="admitted",
                        spec_id="spec_funding_reclaim")
    rows = journal.query("SELECT subject, detail FROM brain_events "
                         "WHERE kind=?", (ideas.CONSUMED,))
    assert rows[0]["subject"] == "a1"
    assert "spec_funding_reclaim" in rows[0]["detail"]


def test_ideas_with_no_usable_text_are_not_offered(journal):
    """A bare TradingView chart caption holds no mechanism to express."""
    ideas.record(journal, _idea("caption", text="", title="BTC to the moon"))
    assert ideas.next_idea(journal) is None


# ── ranking: the Strategist should get the best material first ───────────
def test_denser_strategy_language_outranks_chart_art(journal):
    ideas.record(journal, _idea(
        "art", source="tradingview.com/ideas/btcusdt",
        text="Watching this level closely, could bounce here, not advice."))
    ideas.record(journal, _idea(
        "quant", source="arxiv.org",
        text="Cross-sectional momentum with a volatility filter: rank the "
             "universe by 30-day return, go long the top decile, stop out "
             "at 2 ATR, rebalance weekly."))
    assert ideas.next_idea(journal)["idea_id"] == "quant"


def test_stale_ideas_age_out(journal):
    ideas.record(journal, _idea("old"))
    journal.query("UPDATE brain_events SET ts='2020-01-01T00:00:00+00:00' "
                  "WHERE subject='old'")
    assert ideas.next_idea(journal, max_age_days=30) is None


def test_pending_excludes_consumed(journal):
    ideas.record(journal, _idea("a1"))
    ideas.record(journal, _idea("a2"))
    ideas.mark_consumed(journal, "a1", outcome="admitted")
    assert [i["idea_id"] for i in ideas.pending(journal)] == ["a2"]


# ── the payload the SpecWriter actually consumes ─────────────────────────
def test_next_idea_matches_the_spec_writer_contract(journal):
    """`SpecWriter._prompt` reads source/title/text; `write()` stamps
    idea_id and url into provenance."""
    ideas.record(journal, _idea("a1"))
    got = ideas.next_idea(journal)
    assert set(got) >= {"idea_id", "title", "text", "source", "url"}


def test_spec_writer_prompt_carries_a_queued_idea(journal):
    from trader.brain import spec_writer as sw
    ideas.record(journal, _idea("a1"))
    p = sw._prompt(ideas.next_idea(journal), None, [])
    assert "price-insensitive buying" in p
    assert "Research the Researcher surfaced" in p


# ── the kernel wiring: the whole point of item 0 ─────────────────────────
class _FakeWriter:
    def __init__(self):
        self.seen: list = []
        self.avoid: list = []

    def write(self, idea=None, doctrine=None, avoid=None, data=""):
        self.seen.append(idea)
        self.avoid.append(avoid)
        self.data = data
        return None, {"reason": "test"}


def _kernel_stub(journal, monkeypatch, writer):
    """A Kernel carrying only what `_mechanism_once` touches."""
    import trader.brain.analyst as analyst_mod
    import trader.brain.llm as llm_mod
    import trader.brain.spec_writer as sw_mod
    from trader.kernel import Kernel

    class _Analyst:
        def __init__(self, *a, **kw):
            pass

        def review_deployed(self, book):
            return []

    monkeypatch.setattr(analyst_mod, "Analyst", _Analyst)
    monkeypatch.setattr(llm_mod, "BrainLLM", lambda cfg: object())
    monkeypatch.setattr(sw_mod, "SpecWriter", lambda llm: writer)

    k = Kernel.__new__(Kernel)
    k.journal, k.cfg = journal, {}
    k.feed = k.notifier = None
    k.population = []
    k._load_population = lambda: []
    return k


def test_mechanism_passes_a_queued_idea_to_the_writer(journal, monkeypatch):
    """The regression item 0 names: write() was called with no idea=."""
    ideas.record(journal, _idea("a1"))
    w = _FakeWriter()
    _kernel_stub(journal, monkeypatch, w)._mechanism_once(1, 8)
    assert w.seen and w.seen[0] is not None
    assert w.seen[0]["idea_id"] == "a1"
    assert "price-insensitive buying" in w.seen[0]["text"]


def test_mechanism_marks_the_idea_consumed_even_when_writing_fails(
        journal, monkeypatch):
    """Otherwise the same dud idea is retried every 3h forever."""
    ideas.record(journal, _idea("a1"))
    _kernel_stub(journal, monkeypatch, _FakeWriter())._mechanism_once(1, 8)
    assert ideas.next_idea(journal) is None


def test_mechanism_still_runs_with_an_empty_queue(journal, monkeypatch):
    """No ideas must not stop generation — the Strategist falls back to
    inventing, which is the old behaviour, not an error."""
    w = _FakeWriter()
    rep = _kernel_stub(journal, monkeypatch, w)._mechanism_once(1, 8)
    assert w.seen == [None]
    assert rep["book"] == 0


# ── upgrading the 416 stub rows the old harvester left behind ────────────
def _stub(journal, iid: str, title: str = "BTC breakout retest setup"):
    """What `harvest_idea` rows looked like before the queue existed:
    a title and a source, no text at all."""
    journal.log_brain_event(ideas.KIND, iid,
                            {"title": title, "source": "tradingview.com"})


def test_a_text_less_stub_is_not_offered_to_the_strategist(journal):
    _stub(journal, "old1")
    assert ideas.next_idea(journal) is None


def test_record_upgrades_a_text_less_stub_in_place(journal):
    """416 ideas were recorded title-only. They must not be stranded as
    'already processed' forever — text arriving later fills them in."""
    _stub(journal, "old1")
    assert ideas.record(journal, _idea("old1")) is True
    got = ideas.next_idea(journal)
    assert got is not None and got["idea_id"] == "old1"
    assert "price-insensitive buying" in got["text"]


def test_an_upgraded_idea_is_offered_once_not_twice(journal):
    _stub(journal, "old1")
    ideas.record(journal, _idea("old1"))
    assert [i["idea_id"] for i in ideas.pending(journal)] == ["old1"]


def test_record_does_not_downgrade_a_good_idea(journal):
    ideas.record(journal, _idea("a1"))
    assert ideas.record(journal, _idea("a1", text="")) is False
    assert "price-insensitive" in ideas.next_idea(journal)["text"]


def test_consumption_still_wins_over_an_upgrade(journal):
    ideas.record(journal, _idea("a1"))
    ideas.mark_consumed(journal, "a1", outcome="rejected")
    ideas.record(journal, _idea("a1", text="A new mechanism: momentum "
                                           "breakout with an ATR stop and "
                                           "a 2R target, filtered by ADX."))
    assert ideas.next_idea(journal) is None


# ── RSS descriptions arrive as raw HTML ──────────────────────────────────
def test_html_markup_is_stripped_before_the_strategist_sees_it(journal):
    """Real dev.to items arrive as '<p><strong>The Alpha in the Spread…'.
    Tags are tokens spent on nothing and they blur the mechanism."""
    ideas.record(journal, _idea("h1", text=(
        "<p><strong>Funding Rate Arbitrage</strong></p> <p>Perpetual futures "
        "pay a funding rate; when it is deeply positive, longs pay shorts, so "
        "a delta-neutral short earns the carry with an ATR stop.</p>")))
    text = ideas.next_idea(journal)["text"]
    assert "<p>" not in text and "<strong>" not in text
    assert "Funding Rate Arbitrage" in text
    assert "delta-neutral short earns the carry" in text


def test_html_entities_are_decoded(journal):
    ideas.record(journal, _idea("h2", text=(
        "Enter long when RSI &lt; 30 and price &gt; the 200 EMA; stop at "
        "2 ATR, target 2R, filtered by ADX above 20 &amp; rising.")))
    t = ideas.next_idea(journal)["text"]
    assert "RSI < 30" in t and "&amp;" not in t


def test_a_passage_that_is_only_markup_is_not_offered(journal):
    ideas.record(journal, _idea("h3", text="<div><span></span></div>" * 40))
    assert ideas.next_idea(journal) is None


def test_next_idea_ranks_over_the_whole_pool_not_the_newest_few(journal):
    """next_idea() asks for one idea, but it must still choose that one out
    of the whole recent pool. Prefetching a window scaled to the *requested*
    count made it look only at the newest handful — in the live journal those
    were unrelated tech-news items, so it returned nothing while 37 usable
    ideas sat behind them."""
    ideas.record(journal, _idea("good", source="arxiv.org", text=(
        "Cross-sectional momentum with a volatility filter: rank by 30-day "
        "return, long the top decile, stop at 2 ATR, rebalance weekly.")))
    for n in range(30):                       # newer, and useless
        ideas.record(journal, _idea(
            f"noise{n}", title="Testing a modular PC with an RTX 5060 Ti",
            text="A hardware review of a small form-factor desktop machine, "
                 "covering build quality, thermals and gaming benchmarks."))
    got = ideas.next_idea(journal)
    assert got is not None and got["idea_id"] == "good"


# ── the Strategist writes against real constraints, not just an idea ─────
def test_prompt_carries_the_data_brief_verbatim():
    """The brief tells the Strategist which series are deep enough to score.
    Smuggling it inside `doctrine` truncated it away at 600 chars, so the
    model never saw it — it needs its own section."""
    from trader.brain import spec_writer as sw
    brief = ("usable: funding, basis\n  too short to test yet: oi(31d/75d)")
    p = sw._prompt(None, {"belief": "x" * 2000}, [], data=brief)
    assert "too short to test yet: oi(31d/75d)" in p


def test_prompt_without_a_brief_is_unchanged():
    from trader.brain import spec_writer as sw
    assert "Numeric data" not in sw._prompt(None, None, [])


def test_writer_passes_the_brief_through_to_the_prompt():
    from trader.brain import spec_writer as sw

    class _LLM:
        available = True

        def __init__(self):
            self.prompts = []

        def chat_json(self, prompt, deep=False, purpose="misc"):
            self.prompts.append(prompt)
            return None

    llm = _LLM()
    sw.SpecWriter(llm).write(data="usable: funding, basis")
    assert "usable: funding, basis" in llm.prompts[0]


# ── stream filtering: research vs strategy ideas ─────────────────────────────
def test_next_idea_without_stream_gets_all_streams(journal):
    """When no stream is specified, next_idea returns from any stream.
    This is how it currently works; kernel._mechanism_once must pass stream='strategy'."""
    ideas.record(journal, _idea("research1", text=(
        "A deep review of funding rate dynamics and mean-reversion signals "
        "in perpetual futures markets. Examines historical divergence patterns "
        "and momentum implications for position entry and stop-loss placement."
    )), streams=["research"])
    ideas.record(journal, _idea("strategy1", text=(
        "Entry signal: momentum divergence at resistance with stop-loss at "
        "2 ATR and take-profit at mean-reversion target. Backtest confirms "
        "edge in trending regimes; exit on divergence closure."
    )), streams=["strategy"])

    # Without stream parameter, both should be in the pool
    got = ideas.next_idea(journal)
    assert got is not None, "Should find an idea"
    # The strategy idea ranks higher (more terms), so should be returned first
    assert got["idea_id"] == "strategy1"

    # Consume strategy1, now only research1 remains
    ideas.mark_consumed(journal, "strategy1", outcome="admitted")
    got_second = ideas.next_idea(journal)
    assert got_second is not None
    assert got_second["idea_id"] == "research1"


def test_next_idea_stream_parameter_filters_to_strategy(journal):
    """When stream='strategy' is passed, only strategy ideas are returned."""
    ideas.record(journal, _idea("research1", text=(
        "A deep review of funding rate dynamics and mean-reversion signals "
        "in perpetual futures markets. Examines historical divergence patterns "
        "and momentum implications for position entry and stop-loss placement."
    )), streams=["research"])
    ideas.record(journal, _idea("strategy1", text=(
        "Entry signal: momentum divergence at resistance with stop-loss at "
        "2 ATR and take-profit at mean-reversion target. Backtest confirms "
        "edge in trending regimes; exit on divergence closure."
    )), streams=["strategy"])

    # Strategist asks for strategy stream only — should get strategy1, not research1
    got = ideas.next_idea(journal, stream="strategy")
    assert got is not None, "Should find a strategy idea"
    assert got["idea_id"] == "strategy1"

    # Research idea should never be handed to strategy stream, even though it exists
    # Mark strategy1 consumed and call again with strategy stream
    ideas.mark_consumed(journal, "strategy1", outcome="admitted")
    got_second = ideas.next_idea(journal, stream="strategy")
    assert got_second is None, \
        "Should not return research1 when asking for strategy stream"


def test_next_idea_research_stream_never_returns_strategy_ideas(journal):
    """A hypothetical Researcher should not consume strategy-only ideas."""
    ideas.record(journal, _idea("strategy1", text=(
        "Entry signal: momentum divergence at resistance with stop-loss at "
        "2 ATR and take-profit at mean-reversion target."
    )), streams=["strategy"])
    ideas.record(journal, _idea("research1", text=(
        "A deep review of funding rate dynamics and mean-reversion signals "
        "in perpetual futures markets."
    )), streams=["research"])

    # Research stream query should only get research ideas
    got = ideas.next_idea(journal, stream="research")
    assert got is not None, "Should find a research idea"
    assert got["idea_id"] == "research1"
