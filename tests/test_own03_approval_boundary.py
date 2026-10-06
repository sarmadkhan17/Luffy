"""OWN-03 offline regressions: routine work cannot create owner approval loops."""
import json

import pytest

from trader.core.types import Action, Decision
from trader.owner import approvals as A, queries as Q
from trader.research.external_research import research_pass
from trader.strategy import factory_handoff as F
from tests.test_external_research_router import registered, FakeChild
from tests.test_stage8_owner_os import proposal, service, request
from tests.test_strategy_factory_handoff import cfg, _journal, _to_approval, T0, DAY


def test_routine_hold_decision_and_research_never_create_blanket_approval(tmp_path):
    j = registered(tmp_path)
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol) VALUES(?,?,?)",
                  ('fixture-cycle', '2026-10-06T00:00:00+00:00', 'BTC/USDT'))
    # Real bounded research producer, with retrieval replaced by an offline child.
    bank = research_pass(j, child=FakeChild([]))['results'][0]['bank']
    for action in (Action.HOLD, Action.BUY):
        d = Decision('own03-' + action.value, 'fixture-cycle', 'BTC/USDT',
                     action, .8, .2, .9, [], [])
        j.log_decision(d)
    for _ in range(3):
        before = j._conn().total_changes
        assert Q.query(j, 'research', bank['bank_object_id'])['status'] == 'AVAILABLE'
        for action in (Action.HOLD, Action.BUY):
            assert Q.query(j, 'decision', 'own03-' + action.value)['status'] == 'AVAILABLE'
        assert A.items(j, {})['items'] == []
        assert j._conn().total_changes == before
    assert not A.exists(j, 'owner_approval_items')
    assert not A.exists(j, 'strategy_approval_requests')


@pytest.mark.parametrize('action', ['hold', 'research', 'decision', 'normal_operation'])
def test_routine_action_cannot_be_registered_as_approval_class(tmp_path, action):
    from trader.core.journal import Journal
    j = Journal(tmp_path / 'j.db')
    with pytest.raises(ValueError):
        proposal(j, action=action)
    assert not A.exists(j, 'owner_approval_items')


def test_expired_request_and_rejected_decision_grant_no_authority(tmp_path):
    from trader.core.journal import Journal
    j = Journal(tmp_path / 'j.db')
    p = proposal(j)
    item = A.items(j, {}, now_ms=p['created_at_ms'])['items'][0]
    with pytest.raises(ValueError, match='approval_stale_or_hash_changed'):
        A.decide(j, {}, request(item).args, actor='owner', request_id='expired',
                 now_ms=p['valid_until_ms'])
    assert not j.query('SELECT * FROM owner_approval_receipts')
    assert service(j).execute(request(item, decision='REJECTED')).status == 'ACCEPTED'
    assert service(j).execute(request(item, rid='own03-reapprove-rejected')).status == 'REFUSED'
    assert json.loads(j.query('SELECT payload FROM owner_approval_receipts')[0]['payload'])['decision'] == 'REJECTED'
    assert j.kv_get('control_state') is None


def test_routine_question_cannot_satisfy_missing_first_live_approval(tmp_path, cfg):
    j, _ = _journal(tmp_path)
    v, _ = _to_approval(j, cfg)
    at = T0 + 31 * DAY
    before = j._conn().total_changes
    for _ in range(3):
        Q.query(j, 'decision', 'ordinary-no-trade-question')
        Q.query(j, 'research', 'ordinary-research-question')
        result = F.eligible_for_first_live(j, v['version_id'], cfg=cfg,
                                           available_inputs=[], now_ms=at)
        assert not result.eligible and 'owner_approval_missing' in result.reasons
        assert A.items(j, cfg, now_ms=at)['items'][0]['action_type'] == 'first_live'
    assert j._conn().total_changes == before
    assert not j.query('SELECT * FROM strategy_approval_decisions')
