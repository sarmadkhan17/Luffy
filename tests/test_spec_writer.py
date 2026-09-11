import json

import pytest

from trader.brain import spec_writer as sw
from trader.strategy.compile import compile_spec
from trader.strategy.spec import StrategySpec

GOOD = {
    "name": "Funding Squeeze Reclaim",
    "thesis": "Deeply negative funding means shorts are paying longs to hold, "
              "so positioning is crowded short; any upward move forces "
              "covering, and covering is price-insensitive buying.",
    "invalidation": "Retire if pooled profit factor over the last 30 days "
                    "falls below 0.85.",
    "timeframe": "1h", "direction": "long",
    "entry_long": "close > ema(50) and rsi(14) > 50",
    "entry_short": "", "filters": ["adx(14) > 18"],
    "regime_filter": ["TRENDING_UP"],
    "exit": {"stop": {"kind": "atr", "mult": 2.0},
             "target": {"kind": "rr", "v": 2.0},
             "trail": {"kind": "none"}, "time": {"max_bars": 48}},
}


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.available = True

    def chat_json(self, prompt, deep=False, purpose="misc"):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else None


def test_vocabulary_lists_features_with_signatures():
    v = sw.vocabulary()
    assert "ema(int[3..300])" in v
    assert "rsi(int[3..50])  [range 0.0..100.0]" in v


def test_vocabulary_marks_features_needing_extra_data():
    assert "[needs funding]" in sw.vocabulary()


def test_prompt_teaches_the_unsatisfiable_donchian_trap():
    """The trap that made a ported spec score 0 signals on 8000 real bars."""
    p = sw._prompt(None, None, [])
    assert "UNSATISFIABLE" in p and "donchian_hi(48)" in p


def test_prompt_teaches_cross_vs_level():
    assert "prev(rsi(14), 1) < 30" in sw._prompt(None, None, [])


def test_writes_a_valid_spec():
    spec, trace = sw.SpecWriter(FakeLLM([GOOD])).write()
    assert isinstance(spec, StrategySpec)
    assert StrategySpec.validate(spec) == []
    compile_spec(spec)
    assert spec.provenance["source_kind"] == "strategist"


def test_derives_data_requires_from_the_expression():
    body = dict(GOOD, entry_long="funding_z(1920) < -1.5 and close > ema(50)")
    spec, _ = sw.SpecWriter(FakeLLM([body])).write()
    assert set(spec.data_requires) == {"funding", "ohlcv"}


def test_repairs_its_own_invalid_expression():
    """Compile errors go back to the model rather than being guessed at."""
    bad = dict(GOOD, entry_long="close > nope(3)")
    llm = FakeLLM([bad, GOOD])
    spec, trace = sw.SpecWriter(llm).write()
    assert spec is not None
    assert len(llm.prompts) == 2
    assert "REJECTED" in llm.prompts[1]
    assert "unknown feature" in llm.prompts[1]


def test_rejects_an_auto_generated_name():
    bad = dict(GOOD, name="ema_trend variant (22.0)")
    spec, trace = sw.SpecWriter(FakeLLM([bad, bad, bad])).write()
    assert spec is None
    assert any("name" in str(a.get("errors", "")) for a in trace["attempts"])


def test_rejects_a_thin_thesis():
    bad = dict(GOOD, thesis="it goes up")
    spec, _ = sw.SpecWriter(FakeLLM([bad, bad, bad])).write()
    assert spec is None


def test_gives_up_after_max_repairs():
    bad = dict(GOOD, entry_long="close > nope(3)")
    llm = FakeLLM([bad, bad, bad, GOOD])
    spec, trace = sw.SpecWriter(llm).write()
    assert spec is None
    assert len(llm.prompts) == sw.MAX_REPAIRS + 1


def test_survives_a_non_json_reply():
    llm = FakeLLM(["not json at all", GOOD])
    spec, _ = sw.SpecWriter(llm).write()
    assert spec is not None


def test_no_llm_is_handled():
    class Off:
        available = False
    spec, trace = sw.SpecWriter(Off()).write()
    assert spec is None and "no LLM" in trace["reason"]


def test_idea_text_reaches_the_prompt():
    llm = FakeLLM([GOOD])
    sw.SpecWriter(llm).write(idea={"title": "Basis carry unwind",
                                   "text": "perp premium collapses",
                                   "idea_id": "x1"})
    assert "Basis carry unwind" in llm.prompts[0]
    assert "do not force it into a shape".lower() in llm.prompts[0].lower()


def test_book_is_listed_so_duplicates_are_discouraged():
    llm = FakeLLM([GOOD])
    sw.SpecWriter(llm).write(avoid=["Aggressor Thrust Breakout"])
    assert "Aggressor Thrust Breakout" in llm.prompts[0]
