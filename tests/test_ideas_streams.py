"""The queue serves two consumers now, so an item has to say which it is for."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.brain import ideas
from trader.core.journal import Journal

TEXT = ("Funding divergence is a mean-reversion entry: go long on the "
        "reclaim, stop-loss below the swept low, take-profit at the range "
        "midpoint. The exit is time-based if momentum does not resume.")


def _j(tmp_path):
    return Journal(tmp_path / "q.db")


def test_pending_filters_by_stream(tmp_path):
    j = _j(tmp_path)
    ideas.record(j, {"idea_id": "s1", "title": "A strategy", "text": TEXT,
                     "source": "tv"}, streams=("strategy",))
    ideas.record(j, {"idea_id": "r1", "title": "A finding", "text": TEXT,
                     "source": "arxiv"}, streams=("research",))

    assert {i["idea_id"] for i in ideas.pending(j, stream="strategy")} == {"s1"}
    assert {i["idea_id"] for i in ideas.pending(j, stream="research")} == {"r1"}
    assert {i["idea_id"] for i in ideas.pending(j)} == {"s1", "r1"}


def test_rows_written_before_streams_existed_read_as_strategy(tmp_path):
    j = _j(tmp_path)
    # exactly what the old code wrote: no stream key at all
    j.log_brain_event(ideas.KIND, "legacy1",
                      {"title": "Old", "text": TEXT, "source": "rss",
                       "url": "", "score": 9.0})

    got = ideas.pending(j, stream="strategy")

    assert [i["idea_id"] for i in got] == ["legacy1"]


def test_rows_written_with_the_old_singular_stream_key_still_read(tmp_path):
    j = _j(tmp_path)
    # exactly what the first cut of this feature wrote: one row, singular
    # "stream" key, before a row could carry more than one stream.
    j.log_brain_event(ideas.KIND, "legacy2",
                      {"title": "Mid-migration", "text": TEXT,
                       "source": "rss", "url": "", "score": 9.0,
                       "stream": "research"})

    got = ideas.pending(j, stream="research")

    assert [i["idea_id"] for i in got] == ["legacy2"]
    assert ideas.pending(j, stream="strategy") == []


def test_one_record_call_with_both_streams_reaches_both(tmp_path):
    j = _j(tmp_path)
    ideas.record(j, {"idea_id": "d1", "title": "Both", "text": TEXT,
                     "source": "tv"}, streams=("strategy", "research"))

    assert {i["idea_id"] for i in ideas.pending(j, stream="strategy")} == {"d1"}
    assert {i["idea_id"] for i in ideas.pending(j, stream="research")} == {"d1"}


def test_mark_consumed_removes_from_consumed_stream_only(tmp_path):
    """A dual-stream item should only disappear from the stream that consumed it."""
    j = _j(tmp_path)
    ideas.record(j, {"idea_id": "dual1", "title": "Dual", "text": TEXT,
                     "source": "tv"}, streams=("strategy", "research"))

    # Consumer 1: Strategist consumes the item for strategy stream
    ideas.mark_consumed(j, "dual1", outcome="admitted", stream="strategy")

    # After consumption for strategy, it should NOT appear in strategy queue
    strategy_pending = ideas.pending(j, stream="strategy")
    assert not any(i["idea_id"] == "dual1" for i in strategy_pending), \
        "Item should be gone from strategy queue after consumption"

    # But it SHOULD still appear in research queue
    research_pending = ideas.pending(j, stream="research")
    assert any(i["idea_id"] == "dual1" for i in research_pending), \
        "Item should still be available to research after strategy consumed it"


def test_mark_consumed_legacy_no_stream_suppresses_all_streams(tmp_path):
    """For backwards compatibility: a consumption row with no stream should suppress all streams."""
    j = _j(tmp_path)
    ideas.record(j, {"idea_id": "legacy_dual", "title": "Legacy", "text": TEXT,
                     "source": "tv"}, streams=("strategy", "research"))

    # Old code path: consumption recorded with no stream parameter
    ideas.mark_consumed(j, "legacy_dual", outcome="rejected")

    # Now it should be gone from BOTH streams
    assert ideas.pending(j, stream="strategy") == []
    assert ideas.pending(j, stream="research") == []


def test_record_accepts_bare_string_as_single_stream(tmp_path):
    """record() should accept a plain string (not a tuple) as a single stream."""
    j = _j(tmp_path)

    # Pass a bare string, not a tuple
    result = ideas.record(j, {"idea_id": "bare1", "title": "Bare", "text": TEXT,
                              "source": "tv"}, streams="research")

    assert result is True, "record() should accept a bare string"

    # Verify it was recorded for the right stream
    research_pending = ideas.pending(j, stream="research")
    assert any(i["idea_id"] == "bare1" for i in research_pending)

    # Verify it was NOT recorded for other streams
    strategy_pending = ideas.pending(j, stream="strategy")
    assert not any(i["idea_id"] == "bare1" for i in strategy_pending)
