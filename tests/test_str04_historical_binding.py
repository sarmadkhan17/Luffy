"""STR04-BINDING-01: synthetic offline journals; no runtime or external calls."""
import json
import sqlite3

import pytest

from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from tests.authority_factory_fixtures import cfg, _journal, T0, DAY
from tests.test_str04_governor_lifecycle import world, _snap, _row_state
from tests.test_universal_strategy_authority import NOW


PROTECTIVE = ('PAUSED', F.DEGRADED, F.RETIRED)


def corrupt_event(w, change, *, reidentify=False):
    """Privileged tamper fixture: recompute storage hash to exercise semantics."""
    row = w.j.query('SELECT * FROM strategy_governor_events ORDER BY seq')[0]
    e = json.loads(row['canonical_json'])
    change(e)
    if reidentify:
        e['transition_id'] = F._governor_transition_id(e)
    text = F.canonical(e)
    with w.j._tx() as c:
        c.execute('DROP TRIGGER IF EXISTS strategy_governor_events_no_update')
        c.execute('UPDATE strategy_governor_events SET canonical_json=?,canonical_sha256=? WHERE seq=?',
                  (text, F._sha(text), row['seq']))


def withdraw(w):
    from trader.research.ledger import Ledger
    h = w.j.query('SELECT hash FROM research_candidates')[0]['hash']
    Ledger(w.j).set_candidate(h, '4h', 'fixed', 'refused', reason='TEST-ONLY withdrawn')


@pytest.mark.parametrize('target', PROTECTIVE)
def test_first_approved_protective_event_binds_exact_decision(world, target):
    w = world
    before = _snap(w.j)
    req = F.approval_request(w.j, w.vid)
    decision = F._load(w.j.query('SELECT * FROM strategy_approval_decisions')[0])
    e = w.gov(target)['event']
    assert e['schema'] == 'strategy-governor-event.v2'
    assert e['version_id'] == w.vid
    assert e['binding'] == dict(strategy_id=w.sid,
        lineage_sha256=w.v['lineage_sha256'],
        install_id=req['install_id'], approval_request_id=req['request_id'])
    assert e['owner_decision_id'] == decision['decision_id']
    assert F.governor_events(w.j, w.vid) == [e]
    assert _row_state(w.j, w.sid) == F.PROJECTION[target]
    assert _snap(w.j) == before


@pytest.mark.parametrize('target', PROTECTIVE)
@pytest.mark.parametrize('missing', ('absent', 'null'))
def test_approved_protective_missing_decision_refuses(world, target, missing):
    w = world
    w.gov(target)
    def change(e):
        if missing == 'absent': e.pop('owner_decision_id')
        else: e['owner_decision_id'] = None
    corrupt_event(w, change)
    with pytest.raises(F.HandoffRefused, match='governor_owner_decision_missing'):
        F.governor_events(w.j, w.vid)
    block = F.live_entry_block(w.j, w.sid)
    if target == F.RETIRED:
        assert block == 'version_install_unbound'
    else:
        assert block.startswith('version_authority_unreadable:')


@pytest.mark.parametrize('field,code', [
    ('approval_request_id', 'governor_binding_missing'),
    ('install_id', 'governor_binding_missing'),
    ('lineage_sha256', 'governor_binding_missing'),
    ('strategy_id', 'governor_binding_missing')])
def test_missing_binding_field_refuses(world, field, code):
    world.gov('PAUSED')
    corrupt_event(world, lambda e: e['binding'].pop(field))
    with pytest.raises(F.HandoffRefused, match=code):
        F.governor_events(world.j, world.vid)


@pytest.mark.parametrize('field', ('approval_request_id', 'install_id', 'lineage_sha256', 'strategy_id'))
def test_wrong_binding_refuses_even_with_recomputed_identity(world, field):
    world.gov('PAUSED')
    corrupt_event(world, lambda e: e['binding'].__setitem__(field, 'wrong'), reidentify=True)
    with pytest.raises(F.HandoffRefused, match='governor_binding_differs'):
        F.governor_events(world.j, world.vid)


def test_wrong_decision_refuses_even_with_recomputed_identity(world):
    world.gov(F.RETIRED)
    corrupt_event(world, lambda e: e.__setitem__('owner_decision_id', 'wrong'), reidentify=True)
    with pytest.raises(F.HandoffRefused, match='governor_owner_decision_differs'):
        F.governor_events(world.j, world.vid)


@pytest.mark.parametrize('target', PROTECTIVE)
def test_stale_approval_does_not_block_first_containment(world, target):
    w = world
    withdraw(w)
    with w.j._tx() as c:
        c.execute("UPDATE trades SET realized_pnl=-50 WHERE id='t0'")
    before = _snap(w.j)
    # Empty/changed config, absent capacity/input/risk proof: historical only.
    result = F.govern_version(w.j, {}, w.vid, target, actor='strategy_governor',
        reason_code='TEST-ONLY emergency containment', at_ms=NOW)
    assert result['event']['owner_decision_id']
    assert F.governor_events(w.j, w.vid) == [result['event']]
    assert _snap(w.j) == before


@pytest.mark.parametrize('target', ('ACTIVE', 'REACTIVATED'))
def test_stale_activation_refuses(world, target):
    w = world
    if target == 'REACTIVATED':
        w.act()
        w.gov('PAUSED', at=NOW + 1)
    withdraw(w)
    before = F.governor_events(w.j, w.vid)
    with pytest.raises(F.HandoffRefused, match='governor_evidence_unavailable'):
        if target == 'ACTIVE': w.act()
        else: w.gov(target, at=NOW + 2)
    assert F.governor_events(w.j, w.vid) == before


@pytest.mark.parametrize('target', (F.DEGRADED, F.RETIRED))
def test_never_approved_has_explicit_null_decision(tmp_path, cfg, target):
    j, h = _journal(tmp_path)
    vid = F.create_version(j, cfg, {'kind':'research_candidate', 'hash':h}, at_ms=T0-DAY)['version_id']
    e = F.govern_version(j, {}, vid, target, actor='factory',
        reason_code='TEST-ONLY preapproval containment', at_ms=NOW)['event']
    assert e['owner_decision_id'] is None
    assert e['binding']['approval_request_id'] is None
    assert not j.query('SELECT * FROM strategy_approval_decisions')
    assert F.governor_events(j, vid) == [e]
    if target == F.DEGRADED:
        p = F.govern_version(j, {}, vid, 'PAUSED', actor='strategy_governor',
            reason_code='TEST-ONLY containment', at_ms=NOW+1)['event']
        assert p['owner_decision_id'] is None
        assert F.state_of(j, vid) == 'PAUSED'


def test_transition_identity_includes_owner_binding(world):
    e = world.gov('PAUSED', transition_key='k')['event']
    changed = {**e, 'owner_decision_id':'other'}
    assert F._governor_transition_id(changed) != e['transition_id']
    changed = {**e, 'binding':{**e['binding'], 'approval_request_id':'other'}}
    assert F._governor_transition_id(changed) != e['transition_id']
    corrupt_event(world, lambda e: e.__setitem__('owner_decision_id', 'other'), reidentify=True)
    with pytest.raises(F.HandoffRefused, match='governor_owner_decision_differs'):
        world.gov('PAUSED', transition_key='k')


def test_retry_restart_and_later_append_preserve_history(world):
    w = world
    first = w.gov('PAUSED', transition_key='k')['event']
    assert w.gov('PAUSED', transition_key='k') == dict(status='duplicate', version_id=w.vid, event=first)
    with pytest.raises(F.HandoffRefused, match='governor_replay_key_conflict'):
        w.gov(F.DEGRADED, at=NOW+1, transition_key='k')
    again = Journal(w.j.db_path)
    assert F.governor_events(again, w.vid) == [first]
    assert F.state_of(again, w.vid) == 'PAUSED'
    F.govern_version(again, {}, w.vid, F.DEGRADED, actor='strategy_governor',
        reason_code='TEST-ONLY later', at_ms=NOW+1)
    assert F.governor_events(w.j, w.vid)[0] == first
    assert w.gov('PAUSED', transition_key='k')['event'] == first


@pytest.mark.parametrize('trap', ('event', 'projection'))
def test_first_bound_transition_is_atomic_and_retry_inserts_once(world, trap):
    w = world
    before = (_snap(w.j), _row_state(w.j,w.sid))
    with w.j._tx() as c:
        c.execute('CREATE TRIGGER injected BEFORE '+
            ('INSERT ON strategy_governor_events' if trap == 'event' else 'UPDATE ON strategies')+
            " BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        w.gov('PAUSED', transition_key='k')
    assert F.governor_events(w.j, w.vid) == []
    assert (_snap(w.j), _row_state(w.j,w.sid)) == before
    with w.j._tx() as c: c.execute('DROP TRIGGER injected')
    first = w.gov('PAUSED', transition_key='k')
    assert first['status'] == 'inserted'
    assert w.gov('PAUSED', transition_key='k')['status'] == 'duplicate'
    assert F.governor_events(w.j,w.vid) == [first['event']]


def test_legacy_cannot_authorize_activation_but_independent_containment_remains(world):
    w = world
    w.gov('PAUSED')
    def legacy(e):
        e['schema'] = F.LEGACY_GOVERNOR_SCHEMA
        e.pop('binding')
        e.pop('owner_decision_id')
        e.pop('transition_id')
    corrupt_event(w, legacy)
    row = w.j.query('SELECT canonical_json FROM strategy_governor_events')[0]['canonical_json']
    assert F.governor_events(w.j, w.vid)[0]['schema'] == F.LEGACY_GOVERNOR_SCHEMA
    assert F.state_of(w.j,w.vid) == 'PAUSED'
    with pytest.raises(F.HandoffRefused, match='governor_legacy_history_requires_reconciliation'):
        w.gov('REACTIVATED', at=NOW+1)
    withdraw(w)
    event = w.gov(F.RETIRED, at=NOW+2)['event']
    assert event['schema'] == F.GOVERNOR_SCHEMA and event['owner_decision_id']
    assert event['allocation_ceiling'] is None and event['risk_sha256'] is None
    assert w.j.query('SELECT canonical_json FROM strategy_governor_events ORDER BY seq')[0]['canonical_json'] == row
    assert F.governor_events(w.j,w.vid)[1] == event


def test_unknown_contract_cannot_masquerade_as_legacy(world):
    world.gov('PAUSED')
    corrupt_event(world, lambda e: e.pop('schema'))
    with pytest.raises(F.HandoffRefused, match='governor_event_schema_invalid'):
        F.governor_events(world.j, world.vid)


@pytest.mark.parametrize('field', ('owner_decision_id', 'approval_request_id'))
def test_sibling_chain_cannot_be_copied(world, field):
    """Create a real second approval chain in a separate synthetic journal."""
    from tests.authority_factory_fixtures import _approved
    other, _ = _journal(world.j.db_path.parent / 'sibling')
    config = {**world.cfg, 'risk':{**world.cfg['risk'], 'max_open_positions':99}}
    v, _, _ = _approved(other, config)
    assert v['version_id'] != world.vid
    world.gov('PAUSED')
    req = F.approval_request(other, v['version_id'])
    decision = F._load(other.query('SELECT * FROM strategy_approval_decisions')[0])
    if field == 'owner_decision_id':
        copied = decision['decision_id']
        corrupt_event(world, lambda e: e.__setitem__(field,copied), reidentify=True)
        code = 'governor_owner_decision_differs'
    else:
        copied = req['request_id']
        corrupt_event(world, lambda e: e['binding'].__setitem__(field,copied), reidentify=True)
        code = 'governor_binding_differs'
    with pytest.raises(F.HandoffRefused, match=code):
        F.governor_events(world.j,world.vid)


def test_copied_probation_chain_refuses_historical_resolution(world):
    from tests.authority_factory_fixtures import _approved
    other, _ = _journal(world.j.db_path.parent / 'other-probation')
    config = {**world.cfg, 'risk':{**world.cfg['risk'], 'max_open_positions':99}}
    _, p, _ = _approved(other, config)
    copied = other.query('SELECT * FROM strategy_probation_receipts WHERE receipt_id=?', (p['probation_receipt_id'],))[0]
    req = F.approval_request(world.j,world.vid)
    # A copied, hash-consistent probation payload at the named historical
    # receipt must fail even without touching the owner's request/decision.
    with world.j._tx() as c:
        c.execute('DROP TRIGGER strategy_probation_receipts_no_update')
        c.execute('UPDATE strategy_probation_receipts SET canonical_json=?,canonical_sha256=? WHERE receipt_id=?',
                  (copied['canonical_json'],copied['canonical_sha256'],req['probation_receipt_id']))
    with pytest.raises(F.HandoffRefused, match='governor_historical_probation_differs'):
        world.gov(F.RETIRED)


def test_pending_request_without_decision_is_not_fabricated(tmp_path,cfg):
    from tests.authority_factory_fixtures import _to_approval
    j, _ = _journal(tmp_path)
    v, p = _to_approval(j,cfg)
    e = F.govern_version(j, {}, v['version_id'], F.RETIRED,
        actor='strategy_governor',reason_code='TEST-ONLY pending containment',at_ms=NOW)['event']
    assert e['binding']['approval_request_id'] == p['request_id']
    assert e['owner_decision_id'] is None
    assert not j.query('SELECT * FROM strategy_approval_decisions')
    assert F.governor_events(j,v['version_id']) == [e]


def test_predecision_history_remains_immutable_after_later_owner_decision(tmp_path,cfg):
    from tests.authority_factory_fixtures import _to_approval
    j, _ = _journal(tmp_path)
    v, p = _to_approval(j,cfg)
    e = F.govern_version(j, {}, v['version_id'], F.RETIRED,
        actor='strategy_governor',reason_code='TEST-ONLY pending containment',at_ms=NOW)['event']
    F.record_owner_decision(j,cfg,p['request_id'],'APPROVED',actor='operator',decided_at_ms=NOW+1)
    assert F.governor_events(j,v['version_id']) == [e]
    assert F.state_of(j,v['version_id']) == F.RETIRED


def test_backdated_approved_event_cannot_claim_no_decision(world):
    world.gov('PAUSED')
    d = F._load(world.j.query('SELECT * FROM strategy_approval_decisions')[0])
    def change(e):
        e['owner_decision_id'] = None
        e['at_ms'] = d['decided_at_ms']-1
    corrupt_event(world,change,reidentify=True)
    with pytest.raises(F.HandoffRefused,match='governor_owner_decision_missing'):
        F.governor_events(world.j,world.vid)


def test_shared_learning_transaction_retains_stale_historical_binding(world):
    from trader.learning.application import _BoundJournal
    w = world
    withdraw(w)
    target = F.lifecycle_target(w.j,w.vid)
    before = _snap(w.j)
    with pytest.raises(RuntimeError,match='after governed retirement'):
        with w.j._tx() as c:
            c.execute('BEGIN IMMEDIATE')
            F.govern_version(_BoundJournal(c,w.j),{},w.vid,F.RETIRED,
                actor='strategy_governor',reason_code='TEST-ONLY learning',at_ms=NOW,
                expected_target=target,_connection=c)
            raise RuntimeError('after governed retirement')
    assert F.governor_events(w.j,w.vid) == []
    assert _row_state(w.j,w.sid) == 'paper'
    with w.j._tx() as c:
        c.execute('BEGIN IMMEDIATE')
        e = F.govern_version(_BoundJournal(c,w.j),{},w.vid,F.RETIRED,
            actor='strategy_governor',reason_code='TEST-ONLY learning',at_ms=NOW,
            expected_target=target,_connection=c)['event']
    assert e['owner_decision_id']
    assert F.governor_events(w.j,w.vid) == [e]
    assert _row_state(w.j,w.sid) == 'retired'
    assert _snap(w.j) == before
