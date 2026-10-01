"""Truth gates; synthetic rows here test attribution, not execution acceptance."""
import json

import pytest

from trader.strategy import factory_handoff as F
from tests.paper_cost_evidence_fixture import complete_test_cost_evidence
from tests.test_cross_stage_authority import _version
from tests.test_strategy_factory_handoff import (DAY, T0, PASSING, _identity, _trades, cfg)  # noqa: F401


def evidence(tmp_path, cfg, *, identity=None):
    j, v = _version(tmp_path, cfg)
    kwargs = {} if identity is None else {'identity': identity(j, v)}
    _trades(j, v['strategy_id'], PASSING, T0, spec_hash=v['spec_hash'], **kwargs)
    return j, v


def assess(j, cfg, v):
    result = F.evaluate_probation(j, cfg, v['version_id'], at_ms=T0 + 30 * DAY)
    receipt = F._load(j.query('SELECT * FROM strategy_probation_receipts WHERE receipt_id=?',
                             (result['probation_receipt_id'],))[0])
    return result, receipt


def exact_identity(j, v):
    body = json.loads(_identity(v['strategy_id'], v['spec_hash']))
    body.update(version_id=v['version_id'], install_id=F.verify_install(j, v)['install_id'], exec_mode='paper')
    return body


@pytest.mark.parametrize('field', ['strategy_id', 'version_id', 'spec_sha256', 'install_id', 'exec_mode'])
@pytest.mark.parametrize('mutation', ['missing', 'blank', 'wrong'])
def test_missing_or_wrong_exact_identity_never_counts(tmp_path, cfg, field, mutation):
    def identity(j, v):
        body = exact_identity(j, v)
        if mutation == 'missing':
            body.pop(field)
        else:
            body[field] = '' if mutation == 'blank' else 'wrong'
        return json.dumps(body)
    j, v = evidence(tmp_path, cfg, identity=identity)
    result, receipt = assess(j, cfg, v)
    assert result['status'] == F.P_INCOMPLETE and result['request_id'] is None
    assert receipt['trades'] == [] and len(receipt['excluded_trades']) == 15
    assert F.approval_request(j, v['version_id']) is None


@pytest.mark.parametrize('identity', [None, '', 'not json', 'null',
                                     json.dumps({'status': 'INFERRED'}), 'legacy-hash-only'])
def test_legacy_rows_never_count(tmp_path, cfg, identity):
    j, v = _version(tmp_path, cfg)
    if identity == 'legacy-hash-only':
        identity = _identity(v['strategy_id'], v['spec_hash'])
    _trades(j, v['strategy_id'], PASSING, T0, spec_hash=v['spec_hash'], identity=identity)
    result, receipt = assess(j, cfg, v)
    assert result['status'] == F.P_INCOMPLETE and result['request_id'] is None
    assert receipt['trades'] == []


@pytest.mark.parametrize('mode', ['live', None, '', 'shadow'])
def test_nonpaper_rows_never_count(tmp_path, cfg, mode):
    j, v = evidence(tmp_path, cfg)
    with j._tx() as c:
        c.execute('UPDATE trades SET exec_mode=?', (mode,))
    result, receipt = assess(j, cfg, v)
    assert receipt['trades'] == [] and result['request_id'] is None
    assert result['status'] == (F.P_INSUFFICIENT if mode == 'live' else F.P_INCOMPLETE)


def test_open_or_outside_window_never_counts(tmp_path, cfg):
    j, v = evidence(tmp_path, cfg)
    with j._tx() as c:
        c.execute("UPDATE trades SET status='open', closed_at=NULL WHERE id='t0'")
        c.execute("UPDATE trades SET closed_at='2100-01-01T00:00:00+00:00' WHERE id='t1'")
        c.execute("UPDATE trades SET opened_at='2000-01-01T00:00:00+00:00' WHERE id='t2'")
    result, receipt = assess(j, cfg, v)
    assert result['status'] == F.P_INSUFFICIENT
    assert len(receipt['trades']) == 12 and result['request_id'] is None


@pytest.mark.parametrize('dimension', ['commission', 'slippage', 'funding'])
@pytest.mark.parametrize('state', ['UNAVAILABLE', 'UNKNOWN', 'NOT_APPLICABLE'])
def test_unknown_cost_or_unproven_nonapplicability_is_incomplete(tmp_path, cfg, monkeypatch, dimension, state):
    j, v = evidence(tmp_path, cfg)
    def provider(journal, trade, version):
        costs = complete_test_cost_evidence(journal, trade, version)
        # Even a numeric zero cannot turn missing evidence into known costs.
        costs[dimension].update(status=state, amount=0.0)
        return costs
    monkeypatch.setattr(F, '_paper_cost_evidence', provider)
    result, receipt = assess(j, cfg, v)
    assert result['status'] == F.P_COST_INCOMPLETE and result['request_id'] is None
    assert len(receipt['trades']) == 15 and receipt['stats'] is None
    assert receipt['gross_stats']['profit_factor'] >= 1.15
    assert receipt['gross_stats']['basis'] == 'GROSS_ONLY'
    assert receipt['economic_validation'] == 'NOT_ECONOMICALLY_VALIDATED'
    assert all(r['dimensions'] == [dimension] for r in receipt['missing_cost_evidence'])
    assert F.approval_request(j, v['version_id']) is None
    assert not F.eligible_for_first_live(j, v['version_id'], cfg=cfg).eligible


def test_nonapplicability_requires_authoritative_rule(tmp_path, cfg, monkeypatch):
    j, v = evidence(tmp_path, cfg)
    def provider(journal, trade, version):
        costs = complete_test_cost_evidence(journal, trade, version)
        costs['funding'].update(status='NOT_APPLICABLE', amount=None, rule='TEST-ONLY:nonfunding-instrument')
        return costs
    monkeypatch.setattr(F, '_paper_cost_evidence', provider)
    # Explicit test-only authority, never a production funding assumption.
    monkeypatch.setattr(F, '_cost_nonapplicability_proven',
                        lambda trade, version, dimension, item:
                        dimension == 'funding' and item.get('rule') == 'TEST-ONLY:nonfunding-instrument')
    result, receipt = assess(j, cfg, v)
    assert result['status'] == F.P_SATISFIED and result['request_id']
    assert receipt['stats']['pnl'] == pytest.approx(sum(PASSING) - 15 * 0.03)


def test_complete_costs_use_net_thresholds_and_allow_approval(tmp_path, cfg):
    j, v = evidence(tmp_path, cfg)
    result, receipt = assess(j, cfg, v)
    assert result['status'] == F.P_SATISFIED and result['request_id']
    assert receipt['policy']['min_trades'] == 15
    assert receipt['policy']['min_winrate'] == 0.4
    assert receipt['policy']['min_profit_factor'] == 1.15
    assert receipt['stats']['pnl'] == pytest.approx(sum(PASSING) - 15 * 0.06)
    F.record_owner_decision(j, cfg, result['request_id'], 'APPROVED', actor='operator', decided_at_ms=T0 + 31 * DAY)
    assert not F.eligible_for_first_live(j, v['version_id'], cfg=cfg).eligible


def test_costs_can_make_gross_passing_performance_fail(tmp_path, cfg, monkeypatch):
    j, v = evidence(tmp_path, cfg)
    def provider(journal, trade, version):
        costs = complete_test_cost_evidence(journal, trade, version)
        costs['commission']['amount'] = 11.0
        return costs
    monkeypatch.setattr(F, '_paper_cost_evidence', provider)
    result, receipt = assess(j, cfg, v)
    assert receipt['gross_stats']['profit_factor'] >= 1.15
    assert receipt['stats']['winrate'] == 0 and result['status'] == F.P_NOT_SATISFIED
    assert result['request_id'] is None


def test_identity_insufficiency_precedes_missing_costs(tmp_path, cfg, monkeypatch):
    j, v = evidence(tmp_path, cfg, identity=lambda j, v: _identity(v['strategy_id'], v['spec_hash']))
    monkeypatch.setattr(F, '_paper_cost_evidence', lambda *args: {})
    result, _ = assess(j, cfg, v)
    assert result['status'] == F.P_INCOMPLETE and result['request_id'] is None


def test_satisfied_receipt_cannot_survive_lost_cost_evidence(tmp_path, cfg, monkeypatch):
    j, v = evidence(tmp_path, cfg)
    result, _ = assess(j, cfg, v)
    monkeypatch.setattr(F, '_paper_cost_evidence', lambda *args: {})
    with pytest.raises(F.HandoffRefused, match='probation_evidence_changed'):
        F.record_owner_decision(j, cfg, result['request_id'], 'APPROVED', actor='operator', decided_at_ms=T0 + 31 * DAY)
    with pytest.raises(F.HandoffRefused, match='probation_evidence_changed'):
        F.evaluate_probation(j, cfg, v['version_id'], at_ms=T0 + 31 * DAY)
    assert j.query('SELECT * FROM strategy_approval_decisions') == []


@pytest.mark.parametrize('field', ['evidence_id', 'trade_id', 'version_id', 'install_id', 'currency'])
def test_cost_evidence_must_bind_trade_install_and_currency(tmp_path, cfg, monkeypatch, field):
    j, v = evidence(tmp_path, cfg)
    def provider(journal, trade, version):
        costs = complete_test_cost_evidence(journal, trade, version)
        costs['commission'].pop(field)
        return costs
    monkeypatch.setattr(F, '_paper_cost_evidence', provider)
    result, _ = assess(j, cfg, v)
    assert result['status'] == F.P_COST_INCOMPLETE and result['request_id'] is None


def test_signed_funding_evidence_is_not_clamped_to_zero(tmp_path, cfg, monkeypatch):
    j, v = evidence(tmp_path, cfg)
    def provider(journal, trade, version):
        costs = complete_test_cost_evidence(journal, trade, version)
        costs['funding']['amount'] = -0.1
        return costs
    monkeypatch.setattr(F, '_paper_cost_evidence', provider)
    result, receipt = assess(j, cfg, v)
    assert result['status'] == F.P_SATISFIED
    assert receipt['stats']['pnl'] == pytest.approx(sum(PASSING) + 15 * 0.07)
