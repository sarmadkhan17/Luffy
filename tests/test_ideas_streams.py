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
                     "source": "tv"}, stream="strategy")
    ideas.record(j, {"idea_id": "r1", "title": "A finding", "text": TEXT,
                     "source": "arxiv"}, stream="research")

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
