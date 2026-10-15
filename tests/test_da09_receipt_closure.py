"""DA-09 offline closure: atomic receipts, competing writers and aborted scans.

Reuse the established admission/cycle fixtures; never boot a service or acquire
provider data. These checks add no policy or runtime compatibility path.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import inspect
import sqlite3
import textwrap
import threading
from types import FunctionType
from unittest.mock import Mock

import pytest

from trader import attention_admission as A
from trader.core import journal_evidence as E
from trader.core.journal import Journal
from trader.core.types import ControlState
from tests.test_hierarchical_admission import (
    CUT, cfg, policy, offline, _receipt_observation, full_cycle_kernel,
    test_actual_cycle_preserves_admission_with_market_lineage as lineage_proof,
    test_receipt_preparation_leaves_sqlite_writer_available as writer_proof,
)  # noqa: F401


def retained(journal):
    """Compare physical evidence and pointers, including unreferenced segments."""
    db = journal._conn()
    names = ['state_kv', 'brain_events'] + [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name LIKE 'journal_evidence_%' ORDER BY name")]
    return {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
            for name in names}


@pytest.mark.parametrize('schema', ['v1', 'v2'])
@pytest.mark.parametrize('failure', ['blob', 'event', 'rotation', 'latest'])
def test_receipt_write_failure_rolls_back_all_evidence(
        tmp_path, cfg, policy, monkeypatch, schema, failure):
    j = Journal(tmp_path/'rollback.db')
    obs = _receipt_observation(schema, tmp_path, cfg, monkeypatch)
    first = A.persist(j, obs, policy)
    before = retained(j)
    with monkeypatch.context() as fault:
        if failure == 'blob':
            store = E.store
            def failed(db, value):
                store(db, value)
                raise ValueError('DA09_INJECTED_AFTER_BLOBS')
            fault.setattr(E, 'store', failed)
        else:
            table = 'brain_events' if failure == 'event' else 'state_kv'
            key = A.STATE_KEY if failure == 'rotation' else A.RECEIPT_KEY
            when = "NEW.kind='attention_admission'" if failure == 'event' else "NEW.key=" + repr(key)
            with j._tx() as db:
                db.execute(f"CREATE TEMP TRIGGER da09_failure BEFORE INSERT ON {table} "
                           f"WHEN {when} BEGIN SELECT RAISE(ABORT,'DA09_INJECTED_WRITE'); END")
        with pytest.raises((ValueError, sqlite3.IntegrityError), match='DA09_INJECTED'):
            A.persist(j, obs, policy)
        if failure != 'blob':
            j._conn().execute('DROP TRIGGER da09_failure')
    assert retained(j) == before
    restarted = Journal(j.db_path)
    assert A.canonical(A.latest(restarted)) == A.canonical(first)
    second = A.persist(restarted, obs, policy)
    assert second == A.admit(obs, policy, first['next_state'])
    assert len(j.query("SELECT id FROM brain_events WHERE kind='attention_admission'")) == 2
    assert j._conn().execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('schema', ['v1', 'v2'])
@pytest.mark.parametrize('seeded', [False, True])
def test_real_competing_writers_commit_one_rotation(
        tmp_path, cfg, policy, monkeypatch, schema, seeded):
    j = Journal(tmp_path/'race.db')
    obs = _receipt_observation(schema, tmp_path, cfg, monkeypatch)
    first = A.persist(j, obs, policy) if seeded else None
    state = first['next_state'] if first else A.initial_state()
    expected = A.admit(obs, policy, state)
    verify = A.verify_receipt
    barrier = threading.Barrier(2, timeout=15)
    def checked(receipt):
        verified = verify(receipt)
        if receipt['receipt_id'] == expected['receipt_id']:
            barrier.wait()
        return verified
    def writer(_):
        own = Journal(j.db_path)
        try:
            return ('committed', A.persist(own, deepcopy(obs), policy))
        except ValueError as exc:
            return ('refused', str(exc))
        finally:
            own._conn().close()
    with monkeypatch.context() as fault:
        fault.setattr(A, 'verify_receipt', checked)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(writer, range(2)))
    assert sorted(r[0] for r in results) == ['committed', 'refused']
    assert next(r[1] for r in results if r[0] == 'refused') == 'admission_rotation_changed_during_prepare'
    assert A.canonical(A.latest(Journal(j.db_path))) == A.canonical(expected)
    assert len(j.query("SELECT id FROM brain_events WHERE kind='attention_admission'")) == 1 + int(seeded)


@pytest.mark.parametrize('schema', ['v1', 'v2'])
def test_busy_writer_refuses_without_advancing_then_restarts(
        tmp_path, cfg, policy, monkeypatch, schema):
    j = Journal(tmp_path/'busy.db')
    obs = _receipt_observation(schema, tmp_path, cfg, monkeypatch)
    first = A.persist(j, obs, policy)
    before = retained(j)
    j._conn().execute('PRAGMA busy_timeout=25')
    with sqlite3.connect(j.db_path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            A.persist(j, obs, policy)
    assert retained(j) == before
    assert j._safety_failed
    assert A.persist(Journal(j.db_path), obs, policy) == A.admit(obs, policy, first['next_state'])


@pytest.mark.parametrize('abort_at', [1, 2])
@pytest.mark.parametrize('auxiliary', [False, True])
def test_aborted_multisymbol_cycle_retains_receipt_and_protects_holdings(
        tmp_path, cfg, monkeypatch, abort_at, auxiliary):
    k = full_cycle_kernel(tmp_path, cfg, monkeypatch)
    original_snapshot = k._snapshot_for.side_effect
    def snapshot(symbol, **kwargs):
        snap = original_snapshot(symbol, **kwargs)
        snap.market_provenance_parts = {'as_of_ms': CUT}
        return snap
    k._snapshot_for.side_effect = snapshot
    k._market_aux = ({kind+':'+symbol: dict(available_at_ms=CUT, source='TEST_ONLY')
                      for symbol in k.exchange.markets
                      for kind in ('order_book', 'funding', 'open_interest')}
                     if auxiliary else {})
    k._try_enter = Mock(side_effect=AssertionError('FROZEN entry forbidden'))
    original_decide = k.orchestrator.decide.side_effect
    calls = []
    def decide(snap, *args, **kwargs):
        calls.append(snap.symbol)
        assert kwargs['entry_allowed'] is False
        if len(calls) == abort_at:
            raise ValueError('DA09_ABORTED_CYCLE')
        return original_decide(snap, *args, **kwargs)
    k.orchestrator.decide.side_effect = decide
    with pytest.raises(ValueError, match='DA09_ABORTED_CYCLE'):
        k.cycle()
    first = A.latest(Journal(k.journal.db_path))
    frozen = A.canonical(first)
    assert frozen == A.canonical(k._admission_receipt)
    assert len(calls) == abort_at and len(first['admitted_symbols']) == 2
    assert k._manage_one.call_count == 15
    k._manage_one.reset_mock()
    k.orchestrator.decide.side_effect = original_decide
    stats = k.cycle()
    second = A.latest(Journal(k.journal.db_path))
    assert A.canonical(second['prior_state']) == A.canonical(first['next_state'])
    history = k.journal.query("SELECT detail FROM brain_events WHERE kind='attention_admission' ORDER BY id")
    assert len(history) == 2 and A.canonical(A.verify_receipt(json.loads(history[0]['detail']))) == frozen
    assert stats['deep_processed'] == stats['decisions'] == stats['admission']['deep_admitted'] == 2
    assert stats['entries'] == 0 and k.state_machine.state == ControlState.FROZEN
    assert k._manage_one.call_count == 15
    k._try_enter.assert_not_called()


@pytest.mark.parametrize('aux_kind', ['missing', 'available', 'future'])
def test_original_shadow_defect_is_detected(tmp_path, cfg, monkeypatch, aux_kind):
    """Reintroduce only the exact 207e424 inverse hunk in a temporary method."""
    from trader import kernel
    source = textwrap.dedent(inspect.getsource(kernel.Kernel.cycle))
    assert source.count('aux_receipt') == 3
    namespace = dict(vars(kernel))
    exec(compile(source.replace('aux_receipt', 'receipt'), '<DA09-shadow-mutant>', 'exec'), namespace)
    monkeypatch.setattr(kernel.Kernel, 'cycle', namespace['cycle'])
    with pytest.raises(ValueError, match='admission_receipt_corrupt'):
        lineage_proof(tmp_path, cfg, monkeypatch, aux_kind)


@pytest.mark.parametrize('schema', ['v1', 'v2'])
def test_original_writer_lock_defect_is_detected(tmp_path, cfg, policy, monkeypatch, schema):
    """Move BEGIN back before preparation, matching the cef02b7 defect."""
    source = textwrap.dedent(inspect.getsource(A.persist))
    assert source.count("db.execute('BEGIN IMMEDIATE')") == 1
    source = source.replace("        db.execute('BEGIN IMMEDIATE')\n", '')
    source = source.replace('    with journal._tx() as db:\n',
                            "    with journal._tx() as db:\n        db.execute('BEGIN IMMEDIATE')\n")
    namespace = dict(vars(A))
    exec(compile(source, '<DA09-writer-lock-mutant>', 'exec'), namespace)
    monkeypatch.setattr(A, 'persist', FunctionType(namespace['persist'].__code__, vars(A)))
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        writer_proof(schema, policy, tmp_path, cfg, monkeypatch)
