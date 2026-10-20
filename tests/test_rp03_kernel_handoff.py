"""Real synthetic Bank -> authenticated transfer -> Kernel/Factory boundary.

No provider, protected real data, new numerical look, install or activation.
"""
from copy import deepcopy
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trader.core.journal import Journal
from trader.kernel import Kernel
from trader.research import factory_transfer as T, predictive_bridge as B
from trader.research.ledger import Ledger
from trader.strategy import factory_handoff as F
from tests.test_predictive_research_bridge import CFG, saved
from tests.test_rp03_research_factory_transfer import (
    built, population, target, _supported, _dump, _other,
)  # noqa: F401


def kernel(target, bank, later, monkeypatch):
    k = Kernel.__new__(Kernel)
    k.journal = target
    k.cfg = deepcopy(CFG)
    k.cfg['research'].update(handoff=True, predictive_bank_path=str(bank), referee=False)
    k.notifier = Mock()
    k._install_spec = Mock(side_effect=AssertionError('automatic install forbidden'))
    k._install_version = Mock(side_effect=AssertionError('automatic probation forbidden'))
    k._load_population = Mock(side_effect=AssertionError('automatic population change forbidden'))
    analyst = SimpleNamespace(admit=Mock(side_effect=AssertionError('new statistical evaluation forbidden')))
    monkeypatch.setattr('trader.kernel.time.time', lambda: later / 1000)
    return k, analyst


def _working_ledger(bank, final):
    """The experiment's working ledger, recreated from the Bank archive.

    Completed work is archived in the Bank and its working file retired; the
    file exists only mid-run or in the crash window before the unlink. Faults
    of that working file are exercised by materializing it exactly."""
    from trader.persistence.storage_cleanup import completed_reader
    path = T.experiment_ledger_path(bank, final['experiment_id'])
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(completed_reader(bank, final['experiment_id'])) as src, \
                closing(sqlite3.connect(path)) as dst:
            src.backup(dst)
    return path


def _dump_experiment(bank, final):
    from trader.persistence.storage_cleanup import completed_reader
    with closing(completed_reader(bank, final['experiment_id'])) as db:
        return {t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                for (t,) in db.execute("SELECT name FROM sqlite_master "
                                       "WHERE type='table' AND name NOT LIKE "
                                       "'sqlite_%'").fetchall()}


def count(j, table):
    if not j.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)):
        return 0
    return j.query('SELECT COUNT(*) n FROM '+table)[0]['n']


def no_authority(k, analyst):
    k._install_spec.assert_not_called()
    k._install_version.assert_not_called()
    k._load_population.assert_not_called()
    k.notifier.send.assert_not_called()
    analyst.admit.assert_not_called()
    for table in ('strategies', 'trades', 'strategy_version_installs', 'strategy_probation_receipts',
                  'strategy_approval_requests', 'strategy_approval_decisions', 'strategy_governor_events'):
        assert count(k.journal, table) == 0


def events(j, result_id):
    return [json.loads(r['detail']) for r in j.query(
        'SELECT journal_detail_v1(detail) detail FROM brain_events WHERE subject=? ORDER BY id', (result_id,))]


@pytest.mark.parametrize('control', ['FROZEN', 'RECOVERY'])
def test_kernel_authenticates_transfers_and_stops_at_validated(population, target, monkeypatch, control):
    bank, _prod, final, later = _supported(population)
    packet = T.authenticate(bank, final['result_id'])
    target.kv_set('control_state', control)
    target.kv_set('execution_recovery', '{"phase":"unresolved","symbol":"HELD/USDT"}')
    kv = target.query('SELECT * FROM state_kv ORDER BY key')
    source_before = _dump(bank)
    experiment_before = _dump_experiment(bank, final)
    k, analyst = kernel(target, bank, later, monkeypatch)
    book = []
    result = k._research_handoff(analyst, book)
    assert result['status'] == 'VALIDATED' and result['outcome'] == 'inserted'
    assert result['result_id'] == final['result_id']
    v = F.load_version(target, result['version_id'])
    assert F.verify_validation(target, v)
    assert v['lineage']['research']['result_id'] == final['result_id']
    assert F.state_of(target, v['version_id']) == F.VALIDATED
    assert F.live_entry_block(target, v['strategy_id']) is not None
    assert target.query('SELECT * FROM state_kv ORDER BY key') == kv
    assert [dict(r) for r in target.query('SELECT * FROM research_tests ORDER BY seq')] == packet['tests']
    assert [dict(r) for r in target.query('SELECT * FROM research_budget')] == [packet['budget']]
    assert Ledger(target).candidate(packet['hash'])['state'] == 'referee_passed'
    assert Ledger(target).next_alpha(*Ledger(target)._registered_or_default())[0] == packet['look_seq'] + 1
    assert _dump(bank) == source_before and _dump_experiment(bank, final) == experiment_before
    assert book == []
    no_authority(k, analyst)
    # Full restart: no new candidate, budget, lifecycle or diagnostic rows.
    before = _dump(target.db_path)
    k2, a2 = kernel(Journal(target.db_path), bank, later + 100, monkeypatch)
    again = k2._research_handoff(a2, [])
    assert again['version_id'] == result['version_id'] and again['outcome'] == 'duplicate'
    assert _dump(target.db_path) == before
    no_authority(k2, a2)


def test_retired_working_ledger_is_read_from_the_bank_archive(population, target, monkeypatch):
    bank, _prod, final, later = _supported(population)
    assert not T.experiment_ledger_path(bank, final['experiment_id']).exists()
    k, analyst = kernel(target, bank, later, monkeypatch)
    assert k._research_handoff(analyst, [])['status'] == 'VALIDATED'
    no_authority(k, analyst)


def test_temporary_missing_lineage_recovers_without_candidate_poisoning(population, target, monkeypatch):
    bank, _prod, final, later = _supported(population)
    packet = T.authenticate(bank, final['result_id'])
    absent = bank
    kept = absent.with_suffix('.kept')
    absent.rename(kept)
    k, analyst = kernel(target, bank, later, monkeypatch)
    result = k._research_handoff(analyst, [])
    assert result['status'] == 'DEFERRED'
    assert count(target, 'strategy_versions') == 0 and Ledger(target).candidate(packet['hash']) is None
    before = _dump(target.db_path)
    assert k._research_handoff(analyst, [])['status'] == 'DEFERRED'
    assert _dump(target.db_path) == before
    kept.rename(absent)
    result = k._research_handoff(analyst, [])
    assert result['status'] == 'VALIDATED'
    assert Ledger(target).candidate(packet['hash'])['state'] == 'referee_passed'
    no_authority(k, analyst)


@pytest.mark.parametrize('reason', sorted(T.RECOVERABLE_LINEAGE_REFUSALS))
def test_exact_old_kernel_unavailable_lineage_refusal_is_recoverable(population, target, monkeypatch, reason):
    bank, _prod, final, later = _supported(population)
    packet = T.authenticate(bank, final['result_id'])
    T.transfer(target, packet)
    Ledger(target).set_candidate(packet['hash'], packet['tf'], packet['geo'], 'refused', reason=reason)
    looks = target.query('SELECT * FROM research_tests ORDER BY seq')
    k, analyst = kernel(target, bank, later, monkeypatch)
    result = k._research_handoff(analyst, [])
    assert result['status'] == 'VALIDATED'
    assert dict(Ledger(target).candidate(packet['hash'])) == packet['candidate']
    assert target.query('SELECT * FROM research_tests ORDER BY seq') == looks
    no_authority(k, analyst)


@pytest.mark.parametrize('reason', ['gate2_failed', 'forged_source', 'validation_reconstruction_drift'])
def test_invalid_refusal_is_not_relabelled_as_missing_lineage(population, target, monkeypatch, reason):
    bank, _prod, final, later = _supported(population)
    packet = T.authenticate(bank, final['result_id'])
    T.transfer(target, packet)
    Ledger(target).set_candidate(packet['hash'], packet['tf'], packet['geo'], 'refused', reason=reason)
    candidate = dict(Ledger(target).candidate(packet['hash']))
    looks = target.query('SELECT * FROM research_tests ORDER BY seq')
    k, analyst = kernel(target, bank, later, monkeypatch)
    result = k._research_handoff(analyst, [])
    assert result['status'] == 'REFUSED'
    assert any(r.get('reason_code') == 'candidate_conflict' for r in events(target, final['result_id']))
    assert dict(Ledger(target).candidate(packet['hash'])) == candidate
    assert target.query('SELECT * FROM research_tests ORDER BY seq') == looks
    no_authority(k, analyst)


@pytest.mark.parametrize('disposition', ['UNSUPPORTED', 'DATA_INSUFFICIENT'])
def test_unsupported_sources_do_not_transfer(built, tmp_path, target, monkeypatch, disposition):
    bank, results = _other(built, tmp_path, disposition)
    k, analyst = kernel(target, bank, built[disposition]['later'], monkeypatch)
    assert k._research_handoff(analyst, [])['status'] == 'REFUSED'
    assert target.query('SELECT * FROM research_tests') == []
    assert count(target, 'strategy_versions') == 0
    no_authority(k, analyst)


def test_forged_bank_cannot_transfer_or_qualify(population, target, monkeypatch):
    bank, _prod, final, later = _supported(population)
    with closing(sqlite3.connect(bank)) as db:
        body = json.loads(db.execute('SELECT payload FROM bridge_bank_results WHERE id=?', (final['result_id'],)).fetchone()[0])
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute(f'DROP TRIGGER {name}')
        body['quantitative_evidence']['spent_looks'][0]['alpha_t'] = .9
        db.execute('UPDATE bridge_bank_results SET payload=? WHERE id=?', (json.dumps(body), final['result_id']))
        db.commit()
    k, analyst = kernel(target, bank, later, monkeypatch)
    assert k._research_handoff(analyst, [])['status'] == 'REFUSED'
    assert target.query('SELECT * FROM research_tests') == []
    no_authority(k, analyst)


def test_genuine_new_registration_makes_old_result_stale_without_spending(population, target, monkeypatch):
    from trader.cognition import predictive as P
    from trader.research import predictive_experiment as E
    bank, _prod, final, later = _supported(population)
    old_tests = saved(bank, 'bridge_tests')
    ex = deepcopy(next(e for e in saved(bank, 'bridge_experiments') if e['experiment_id'] == final['experiment_id']))
    policy = json.loads(ex['registered_policy_json']);policy['seed'] = 973
    ex['registered_policy_json'] = P.canonical(policy)
    config = json.loads(ex['evaluation_config_json']);config['research'] = policy
    ex['evaluation_config_json'] = P.canonical(config)
    ex['minimum_evidence'] = E.evidence_policy(config)
    ex['experiment_id'] = P.digest({k:v for k,v in ex.items() if k != 'experiment_id'})
    E.validate(ex)
    with closing(B.store(bank)) as db:
        B.append(db, 'bridge_experiments', ex['experiment_id'], ex)
        db.commit()
    k, analyst = kernel(target, bank, later, monkeypatch)
    assert k._research_handoff(analyst, [])['status'] == 'REFUSED'
    assert any(r.get('reason_code') == 'research_result_stale' for r in events(target, final['result_id']))
    assert saved(bank, 'bridge_tests') == old_tests
    assert target.query('SELECT * FROM research_tests') == []
    no_authority(k, analyst)


def test_conflicting_destination_refuses_before_factory(population, target, monkeypatch):
    bank, _prod, final, later = _supported(population)
    packet = T.authenticate(bank, final['result_id'])
    with target._tx() as db:
        db.execute('INSERT INTO research_budget VALUES (1,.5,.25,\'other\')')
    budget = target.query('SELECT * FROM research_budget')
    k, analyst = kernel(target, bank, later, monkeypatch)
    assert k._research_handoff(analyst, [])['status'] == 'REFUSED'
    assert target.query('SELECT * FROM research_budget') == budget
    assert target.query('SELECT * FROM research_tests') == []
    assert count(target, 'strategy_versions') == 0
    no_authority(k, analyst)


def test_no_retroactive_fill_below_spent_later_look(population, target):
    bank, _prod, final, _later = _supported(population)
    packet = T.authenticate(bank, final['result_id'])
    row = dict(packet['tests'][0]);row.update(seq=2, hash='other')
    columns = list(row)
    with target._tx() as db:
        db.execute('INSERT INTO research_tests('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', tuple(row[k] for k in columns))
    before = _dump(target.db_path)
    with pytest.raises(T.TransferRefused, match='budget_sequence_diverged'):
        T.transfer(target, packet)
    assert _dump(target.db_path) == before


def test_discovery_reason_passed_label_does_not_bypass_gate2(tmp_path):
    from tests.test_strategy_factory_handoff import _journal
    j, h = _journal(tmp_path, state='reason_passed')
    k = Kernel.__new__(Kernel);k.journal = j;k.cfg = {'research': {'handoff': True}}
    analyst = SimpleNamespace(admit=Mock(side_effect=AssertionError('unregistered reasoning look')))
    assert k._research_handoff(analyst, [])['status'] == 'DEFERRED'
    assert Ledger(j).candidate(h)['state'] == 'reason_passed'
    assert count(j, 'strategy_versions') == 0
    analyst.admit.assert_not_called()


def test_handoff_disabled_has_no_effect(population, target, monkeypatch):
    bank, _prod, _final, later = _supported(population)
    k, analyst = kernel(target, bank, later, monkeypatch)
    k.cfg['research']['handoff'] = False
    before = _dump(target.db_path)
    assert k._research_handoff(analyst, []) is None and _dump(target.db_path) == before
    no_authority(k, analyst)


@pytest.mark.parametrize('locked', ['bank', 'experiment'])
def test_locked_lineage_is_retryable_and_recovers(population, target, monkeypatch, locked):
    bank, _prod, final, later = _supported(population)
    source = bank if locked == 'bank' else _working_ledger(bank, final)
    k, analyst = kernel(target, bank, later, monkeypatch)
    with closing(sqlite3.connect(source)) as db:
        # WAL intentionally permits readers during EXCLUSIVE; use a real read lock.
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('BEGIN EXCLUSIVE')
        outcome = k._research_handoff(analyst, [])
        assert outcome['status'] == 'DEFERRED'
        assert count(target, 'strategy_versions') == 0
        db.rollback()
    assert k._research_handoff(analyst, [])['status'] == 'VALIDATED'
    no_authority(k, analyst)


@pytest.mark.parametrize('max_book', [0, 8])
def test_mechanism_reports_validation_without_book_addition_or_reload(population, target, monkeypatch, max_book):
    from tests.test_idea_pipeline import _kernel_stub, _FakeWriter
    bank, _prod, final, later = _supported(population)
    k, analyst = kernel(target, bank, later, monkeypatch)
    caller = _kernel_stub(target, monkeypatch, _FakeWriter())
    caller.cfg = k.cfg
    caller._load_population = k._load_population
    caller._install_spec = k._install_spec
    caller._strategist_knowledge = lambda: {}
    report = caller._mechanism_once(per_cycle=0, max_book=max_book)
    assert report['research_handoff']['status'] == 'VALIDATED'
    assert report['research_handoff']['result_id'] == final['result_id']
    assert report['added'] == [] and report['book'] == 0 and caller.population == []
    no_authority(k, analyst)


@pytest.mark.parametrize('invalid,code', [('binding', 'experiment_ledger_binding'),
    ('corrupt', 'experiment_ledger_unreadable'), ('symlink', 'experiment_ledger_alias')])
def test_invalid_lineage_is_distinct_from_temporary_unavailability(population, target, monkeypatch, invalid, code):
    bank, _prod, final, later = _supported(population)
    path = _working_ledger(bank, final)
    if invalid == 'binding':
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE predictive_experiment_binding SET id='foreign'")
            db.commit()
    elif invalid == 'corrupt':
        path.write_bytes(b'not a database')
    else:
        kept = path.with_suffix('.kept')
        path.rename(kept)
        path.symlink_to(kept)
    k, analyst = kernel(target, bank, later, monkeypatch)
    assert k._research_handoff(analyst, [])['status'] == 'REFUSED'
    assert any(e.get('reason_code') == code for e in events(target, final['result_id']))
    assert target.query('SELECT * FROM research_tests') == []
    assert count(target, 'strategy_versions') == 0
    no_authority(k, analyst)


@pytest.mark.parametrize('value,code', [('destination', 'source_destination_alias'),
    (42, 'research_bank_path_invalid')])
def test_invalid_routing_cannot_authenticate_destination_as_source(target, value, code):
    k = Kernel.__new__(Kernel)
    k.journal = target
    k.cfg = {'research': {'handoff': True, 'predictive_bank_path':
        str(target.db_path) if value == 'destination' else value}}
    result = k._research_handoff(None, [])
    assert result['status'] == 'REFUSED' and result['reason_code'] == code
    assert count(target, 'strategy_versions') == 0
    assert target.query('SELECT * FROM research_tests') == []
