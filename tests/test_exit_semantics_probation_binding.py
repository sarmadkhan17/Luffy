"""Probation and approval must re-verify recorded canonical paper exits."""
import json

import pytest

from trader.engine import paper_exit_evidence as X
from trader.strategy import exit_policy as E, factory_handoff as F
from tests.test_strategy_factory_handoff import T0, DAY, PASSING, _trades
from tests.test_versioned_paper_execution import setup
from tests.paper_cost_evidence_fixture import register_test_cost_evidence


def prepared(tmp_path):
    j, cfg, v = setup(tmp_path)
    _trades(j, v['strategy_id'], PASSING, T0, spec_hash=v['spec_hash'])
    return j, cfg, v


def replace_identity(j, change):
    # TEST-ONLY: simulate a trade originally recorded with this identity.
    # Production's immutable identity trigger is neither disabled nor changed.
    row = j.query("SELECT * FROM trades WHERE id='t0'")[0]
    identity = json.loads(row['entry_identity_json'])
    change(identity)
    row['entry_identity_json'] = json.dumps(identity, sort_keys=True)
    with j._tx() as c:
        c.execute("DELETE FROM trades WHERE id='t0'")
        c.execute(f"INSERT INTO trades ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))


def alter_evidence(j, change, rehash=True):
    row = j.query(f"SELECT * FROM {X.TABLE} WHERE trade_id='t0'")[0]
    body = json.loads(row['canonical_json'])
    change(body)
    with j._tx() as c:
        if rehash:
            X.record(c, body)
        else:
            c.execute(f"UPDATE {X.TABLE} SET canonical_json=? WHERE trade_id='t0'", (X.canonical(body),))


def assess(j, cfg, v):
    return F.evaluate_probation(j, cfg, v['version_id'], at_ms=T0+30*DAY)


@pytest.mark.parametrize('semantics', [None, '', 'unknown', 'factory-exit.old'])
def test_missing_blank_unknown_or_different_semantics_incomplete(tmp_path, semantics):
    j, cfg, v = prepared(tmp_path)
    def change(identity):
        if semantics is None:
            identity.pop('exit_semantics_id')
        else:
            identity['exit_semantics_id'] = semantics
    replace_identity(j, change)
    result = assess(j, cfg, v)
    assert result['status'] == F.P_EXIT_INCOMPLETE and result['request_id'] is None
    receipt = F._load(j.query('SELECT * FROM strategy_probation_receipts')[0])
    assert len(receipt['trades']) == 14
    assert receipt['excluded_trades'] == [{'id':'t0','reason':'exit_semantics_evidence_unproven'}]


def test_absent_canonical_evidence_incomplete_without_backfill(tmp_path):
    j, cfg, v = prepared(tmp_path)
    with j._tx() as c:
        c.execute(f"DELETE FROM {X.TABLE} WHERE trade_id='t0'")
    assert assess(j, cfg, v)['status'] == F.P_EXIT_INCOMPLETE
    assert not j.query(f"SELECT * FROM {X.TABLE} WHERE trade_id='t0'")


@pytest.mark.parametrize('conflict', ['digest','trade','version','install','semantics',
                                    'geometry','initial','final','observation','duplicate','quantity'])
def test_conflicting_canonical_evidence_incomplete_even_when_rehashed(tmp_path, conflict):
    j, cfg, v = prepared(tmp_path)
    def change(body):
        if conflict in ('trade','version','install','semantics'):
            key = {'trade':'trade_id','version':'version_id','install':'install_id','semantics':'exit_semantics_id'}[conflict]
            body[key] = 'different'
        elif conflict in ('geometry','initial','final'):
            key = 'final' if conflict == 'final' else 'initial'
            state = json.loads(body[key])
            if conflict == 'geometry':
                state['policy']['target'] += 1
            else:
                state['state']['bars_held'] += 1
            body[key] = json.dumps(state, sort_keys=True)
        elif conflict == 'duplicate':
            body['observations'] += body['observations']
        elif conflict == 'quantity':
            body['quantity'] += 1
        else:
            body['observations'][0]['completed'] = False
    # A changed trade_id must stay stored under the original row key.
    if conflict == 'trade':
        row = j.query(f"SELECT * FROM {X.TABLE} WHERE trade_id='t0'")[0]
        body = json.loads(row['canonical_json']); change(body); text = X.canonical(body)
        with j._tx() as c:
            c.execute(f"UPDATE {X.TABLE} SET canonical_json=?,canonical_sha256=? WHERE trade_id='t0'", (text,X.digest(text)))
    else:
        alter_evidence(j, change, rehash=conflict != 'digest')
    assert assess(j, cfg, v)['status'] == F.P_EXIT_INCOMPLETE


@pytest.mark.parametrize('field,value', [('close_reason','time'),('stop_loss',98),
                                       ('take_profit',108),('exit_price',111)])
def test_closure_conflicts_with_canonical_replay(tmp_path, field, value):
    j, cfg, v = prepared(tmp_path)
    with j._tx() as c:
        c.execute(f"UPDATE trades SET {field}=? WHERE id='t0'", (value,))
    assert assess(j, cfg, v)['status'] == F.P_EXIT_INCOMPLETE


@pytest.mark.parametrize('complete_costs', [False, True])
def test_matching_semantics_preserve_cost_gate_and_thresholds(tmp_path, monkeypatch, complete_costs):
    j, cfg, v = prepared(tmp_path)
    if complete_costs:
        register_test_cost_evidence(monkeypatch)
    result = assess(j, cfg, v)
    assert result['status'] == (F.P_SATISFIED if complete_costs else F.P_COST_INCOMPLETE)
    receipt = F._load(j.query('SELECT * FROM strategy_probation_receipts')[0])
    assert len(receipt['trades']) == 15
    assert all(t['exit_semantics_id'] == E.EXIT_SEMANTICS_ID and t['canonical_exit_evidence_sha256'] for t in receipt['trades'])
    assert bool(result['request_id']) == complete_costs


@pytest.mark.parametrize('after_approval', [False, True])
def test_approval_and_first_live_replay_counted_evidence(tmp_path, monkeypatch, after_approval):
    j, cfg, v = prepared(tmp_path)
    register_test_cost_evidence(monkeypatch)
    result = assess(j, cfg, v)
    if after_approval:
        F.record_owner_decision(j,cfg,result['request_id'],'APPROVED',actor='operator',decided_at_ms=T0+31*DAY)
    alter_evidence(j, lambda body: body.update(exit_semantics_id='old'))
    with pytest.raises(F.HandoffRefused, match='probation_evidence_changed'):
        F.record_owner_decision(j,cfg,result['request_id'],'APPROVED',actor='operator',decided_at_ms=T0+31*DAY)
    eligibility = F.eligible_for_first_live(j,v['version_id'],cfg=cfg,available_inputs={'ohlcv'})
    assert not eligibility.eligible and 'probation_evidence_changed' in eligibility.reasons
    assert F.live_entry_block(j,v['strategy_id']) is not None


def test_semantics_change_does_not_relabel_historical_trades_or_receipt(tmp_path, monkeypatch):
    j, cfg, v = prepared(tmp_path)
    register_test_cost_evidence(monkeypatch)
    result = assess(j, cfg, v)
    before = j.query(f'SELECT * FROM {X.TABLE} ORDER BY trade_id')
    old_receipt = j.query('SELECT * FROM strategy_probation_receipts')[0]
    semantics_b = E.EXIT_SEMANTICS_ID + '.B'
    monkeypatch.setattr(E,'EXIT_SEMANTICS_ID',semantics_b)
    with pytest.raises(F.HandoffRefused,match='exit_semantics_mismatch'):
        F._verify_probation(j,cfg,v,result['probation_receipt_id'])
    with pytest.raises(F.HandoffRefused,match='exit_semantics_mismatch'):
        F.record_owner_decision(j,cfg,result['request_id'],'APPROVED',actor='operator',decided_at_ms=T0+31*DAY)
    install = json.loads(j.query('SELECT canonical_json FROM strategy_version_installs')[0]['canonical_json'])
    install_b = {**install, 'exit_semantics_id':semantics_b}
    version_b = {**v, 'evidence_ids':{**v['evidence_ids'],'exit_semantics_id':semantics_b}}
    with pytest.raises(ValueError,match='trade_exit_semantics_unbound'):
        X.verify(j,j.query("SELECT * FROM trades WHERE id='t0'")[0],version_b,install_b)
    assert j.query(f'SELECT * FROM {X.TABLE} ORDER BY trade_id') == before
    assert j.query('SELECT * FROM strategy_probation_receipts')[0] == old_receipt
