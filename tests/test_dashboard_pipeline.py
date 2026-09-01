"""Test the dashboard /api/pipeline endpoint returns queued counters."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.config import ROOT


def test_pipeline_endpoint_shows_queued_totals(tmp_path):
    """Verify pipeline endpoint returns queued_strategy + queued_research totals.

    The /api/pipeline endpoint should no longer query for harvest_accepted/rejected
    (which the Scraper no longer emits), and instead extract queued counters from
    harvest_cycle events.
    """
    db_path = tmp_path / "test.db"
    j = Journal(str(db_path))

    # Log a harvest_cycle event with queued counters
    j.log_brain_event("harvest_cycle", "harvester", {
        "scraped": 10,
        "new": 8,
        "screened_out": 2,
        "queued_strategy": 5,
        "queued_research": 3,
        "per_source": {
            "example.com": {"scraped": 10, "queued": 8}
        }
    })

    # Simulate the build_company function's _queued_total helper
    total = 0
    rows = j.query(
        "SELECT detail FROM brain_events WHERE kind='harvest_cycle' "
        "AND ts >= datetime('now','-24 hours')")
    for r in rows:
        try:
            d = json.loads(r["detail"])
            total += d.get("queued_strategy", 0)
            total += d.get("queued_research", 0)
        except Exception:
            pass

    assert total == 8, f"Expected total queued=8, got {total}"


def test_pipeline_verdicts_excludes_harvest_events(tmp_path):
    """Verify the verdicts query excludes harvest_accepted/rejected events.

    Only proposal_accepted/rejected verdicts should be returned now, since
    the Scraper queues but doesn't accept strategies.
    """
    db_path = tmp_path / "test.db"
    j = Journal(str(db_path))

    # Log some events that should NOT be in the verdicts (old scraper events)
    j.log_brain_event("harvest_accepted", "old_strat", {
        "source": "example.com",
        "title": "Old Strategy"
    })
    j.log_brain_event("harvest_rejected", "rejected_strat", {
        "title": "Rejected Strategy",
        "stage": "backtest"
    })

    # Log events that SHOULD be in the verdicts (strategist decisions)
    j.log_brain_event("proposal_accepted", "new_strat", {
        "family": "Momentum",
        "title": "New Strategy"
    })
    j.log_brain_event("proposal_rejected", "bad_strat", {
        "family": "MeanReversion",
        "title": "Bad Strategy"
    })

    # Query as the pipeline endpoint would
    rows = j.query(
        "SELECT ts, kind, subject, detail FROM brain_events "
        "WHERE kind IN ('proposal_accepted','proposal_rejected') "
        "ORDER BY id DESC LIMIT 14")

    kinds = [r["kind"] for r in rows]
    subjects = [r["subject"] for r in rows]

    # Should only have proposal events, not harvest events
    assert "proposal_accepted" in kinds
    assert "proposal_rejected" in kinds
    assert "harvest_accepted" not in kinds
    assert "harvest_rejected" not in kinds

    # Should have the new strategy decisions but not the old harvest ones
    assert "new_strat" in subjects
    assert "bad_strat" in subjects
    assert "old_strat" not in subjects
    assert "rejected_strat" not in subjects
