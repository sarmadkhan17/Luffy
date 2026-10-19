"""Postmortem reuses exact Factory paper evidence without granting authority."""
import inspect
import json
import sqlite3

import pytest

from trader.brain import postmortem as PM
from trader.strategy import factory_handoff as F
from tests.test_sol1_paper_cost import Run, cfg, no_public_network  # noqa: F401
from tests.authority_factory_fixtures import _trades, T0, DAY


class _Vault:
    def __init__(self):
        self.notes = []

    def incident_note(self, title, body):
        self.notes.append((title, body))


def test_native_paper_outcomes_use_exact_identity_and_net_costs(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = r.trade('win')
    r.complete()
    before = r.j._conn().total_changes
    writes = {getattr(sqlite3, name) for name in (
        'SQLITE_INSERT', 'SQLITE_UPDATE', 'SQLITE_DELETE', 'SQLITE_CREATE_TABLE',
        'SQLITE_CREATE_INDEX', 'SQLITE_CREATE_TRIGGER', 'SQLITE_DROP_TABLE',
        'SQLITE_DROP_INDEX', 'SQLITE_DROP_TRIGGER', 'SQLITE_ALTER_TABLE',
        'SQLITE_ATTACH', 'SQLITE_DETACH', 'SQLITE_TRANSACTION', 'SQLITE_SAVEPOINT')}
    denied = []
    def read_only(action, *args):
        if action in writes:
            denied.append(action)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    r.j._conn().set_authorizer(read_only)
    try:
        health = PM.book_health(r.j, cfg)[0]
    finally:
        r.j._conn().set_authorizer(None)
    assert not denied and r.j._conn().total_changes == before
    assert health['version_id'] == r.vid
    assert health['install_id'] == F.resolve_current_install(r.j, r.v['strategy_id'])['install_id']
    stats = health['health']['stats']
    costs = r.receipt(tid)['known_costs']
    assert stats['trades'] == 1
    assert stats['pnl'] == pytest.approx(r.row(tid)['realized_pnl'] - costs)
    assert health['health']['verdict'] == F.P_INSUFFICIENT
    assert F.state_of(r.j, r.vid) == F.SHADOW
    assert F.approval_request(r.j, r.vid) is None


def test_missing_costs_cannot_be_reported_as_economically_healthy(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    r.trade('win')
    h = PM.book_health(r.j, cfg)[0]['health']
    assert h['verdict'] == F.P_COST_INCOMPLETE
    assert h['stats'] is None and h['missing_cost_evidence']


def test_unattributed_legacy_outcomes_are_not_assigned_to_current_version(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    r.trade('win')
    r.complete()
    _trades(r.j, r.v['strategy_id'], [10000.], T0 + 6 * DAY, prefix='legacy', identity=None)
    h = PM.book_health(r.j, cfg)[0]['health']
    assert h['verdict'] == F.P_INCOMPLETE and h['stats'] is None
    assert {'id': 'legacy0', 'reason': 'unattributed'} in h['excluded_trades']


def test_preinstall_outcomes_never_inflate_current_health(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    r.trade('loss')
    r.complete()
    _trades(r.j, r.v['strategy_id'], [10000.], T0 - DAY, prefix='old', identity=None)
    h = PM.book_health(r.j, cfg)[0]['health']
    assert h['stats']['trades'] == 1 and h['stats']['pnl'] < 0


def test_unversioned_spec_has_no_fabricated_health_or_identity(tmp_path, cfg):
    from tests.test_sol1_strategy_lifecycle import _journal, _spec
    j = _journal(tmp_path)
    j.upsert_spec(_spec(), state='paper')
    h = PM.book_health(j, cfg)[0]
    assert h['version_id'] is h['install_id'] is None
    assert h['health']['verdict'] == 'UNAVAILABLE'
    assert h['health']['stats'] is None


def test_read_reports_satisfied_without_advancing_probation(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    for _ in range(10):
        r.trade('win')
        r.complete()
    assert PM.book_health(r.j, cfg)[0]['health']['verdict'] == F.P_SATISFIED
    assert F.state_of(r.j, r.vid) == F.SHADOW
    assert not r.j.query('SELECT * FROM strategy_approval_requests')


def test_change_only_notes_follow_exact_install_and_verdict(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    vault = _Vault()
    first = PM.run_postmortem(r.j, vault, cfg=cfg)
    assert first['changed'] and len(vault.notes) == 1
    second = PM.run_postmortem(r.j, vault, cfg=cfg)
    assert not second['changed'] and len(vault.notes) == 1
    # Same status under an old or missing identity is not the same health report.
    r.j.log_brain_event('postmortem', 'book', {'verdicts': first['verdicts'], 'identities': {}})
    assert PM.run_postmortem(r.j, vault, cfg=cfg)['changed']
    assert len(vault.notes) == 2
    assert first['authority'] == 'DIAGNOSTIC_ONLY'


def test_judge_summary_includes_attributed_health_and_recorded_rent(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    r.j.kv_set('rent_state', json.dumps({'week_start': '2026-09-07', 'net': 11.83, 'bar': 50.}))
    text = PM.summary_text(r.j, cfg)
    assert r.v['spec']['name'] in text
    assert '11.83' in text and '2026-09-07' in text
    assert 'exact-version paper health' in text


def test_postmortem_keeps_no_model_or_legacy_health_calculation():
    source = inspect.getsource(PM)
    assert 'BrainLLM' not in source and '.chat' not in source
    assert 'assess_health' not in source and 'expected_winrate' not in source
    from trader.brain.doctrine import load_doctrine
    assert {'beliefs', 'version'} <= load_doctrine().keys()
