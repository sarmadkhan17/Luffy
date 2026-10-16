"""Retained transition guarantees on the current versioned Governor path.

The former tests called the retired automatic genome promoter. Reasons,
atomicity and exact restart history remain mandatory on the sole writer.
"""

import pytest

from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from tests.test_str04_governor_lifecycle import world, cfg, NOW  # noqa: F401


def test_retirement_reason_matches_exact_version_event_and_restart(world):
    world.act()
    reason = 'TEST-ONLY exact version retirement with retained provenance'
    result = world.gov('RETIRED', reason=reason, at=NOW + 1)
    row = world.j.query('SELECT * FROM strategies WHERE id=?', (world.sid,))[0]
    assert row['state'] == 'retired' and row['retire_reason'] == reason
    assert result['event']['reason_code'] == reason
    assert result['event']['version_id'] == world.vid
    history = F.governor_events(world.j, world.vid)
    assert F.governor_events(Journal(world.j.db_path), world.vid) == history
    assert F.state_of(Journal(world.j.db_path), world.vid) == F.RETIRED


def test_duplicate_transition_preserves_reason_clock_and_event(world):
    world.act()
    first = world.gov('PAUSED', reason='TEST-ONLY pause', at=NOW + 1)
    before = world.j.query('SELECT * FROM strategies WHERE id=?', (world.sid,))
    history = F.governor_events(world.j, world.vid)
    again = world.gov('PAUSED', reason='TEST-ONLY pause', at=NOW + 1)
    assert again['status'] == 'duplicate' and again['event'] == first['event']
    assert world.j.query('SELECT * FROM strategies WHERE id=?', (world.sid,)) == before
    assert F.governor_events(world.j, world.vid) == history


def test_refused_reason_never_changes_row_or_history(world):
    world.act()
    before = world.j.query('SELECT * FROM strategies WHERE id=?', (world.sid,))
    history = F.governor_events(world.j, world.vid)
    with pytest.raises(F.HandoffRefused):
        world.gov('RETIRED', reason='', at=NOW + 1)
    assert world.j.query('SELECT * FROM strategies WHERE id=?', (world.sid,)) == before
    assert F.governor_events(world.j, world.vid) == history
