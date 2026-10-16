"""The DA-05 restore connection change must not resurrect entry authority."""
import json

import pytest

from trader.core.journal import Journal
from trader.engine.state import ControlStateMachine
from tests.test_stage1_recovery_monitoring import backup  # noqa: F401


@pytest.mark.parametrize('state,expected', [('ACTIVE', 'RECOVERY'),
    ('FROZEN', 'FROZEN'), ('HALTED', 'HALTED'), ('RECOVERY', 'RECOVERY')])
def test_restore_preserves_stop_and_requires_owner(backup, tmp_path, state, expected):
    _, journal, sets = backup
    journal.kv_set('control_state', state)
    generation = sets.capture()
    target = tmp_path / 'restored'
    receipt = sets.restore(generation, target)
    restored = Journal(target / 'data/luffy.db')
    assert receipt['restored_control_state'] == expected
    assert restored.kv_get('control_state') == expected
    assert not ControlStateMachine(restored).can_enter()
    status = json.loads(restored.kv_get('supervisor_status'))
    assert status['safe_to_activate'] is False and status['needs_owner'] is True
    assert restored.kv_get('replay_sentinel') == 'exact-bytes-state'
