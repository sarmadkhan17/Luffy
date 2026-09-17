"""Synthetic population/coverage integration; no trading or network."""
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from trader.cognition import dataset as D
from trader.observability import population as P, learning as L, investigation as C
from tests.test_attention_learning import publish

NOW = 1789000000000 // L.TF * L.TF + 60000


def config(tmp_path, start=NOW):
    from tests.test_pit_dataset import declaration as make_declaration
    declaration = make_declaration(['S%d/USDT' % i for i in range(6)],
                                   NOW+86400000, start=start, mode='forward')
    freeze = D.freeze(declaration, NOW-1000, code_manifest={'fixture': 'synthetic'})
    a, b = tmp_path/'declaration.json', tmp_path/'freeze.json'
    a.write_text(json.dumps(declaration)); b.write_text(json.dumps(freeze))
    return {'declaration': str(a), 'receipt': str(b), 'export_directory': str(tmp_path/'exports')}


def events(cfg, kind=None):
    rows = [json.loads(p.read_text()) for p in Path(cfg['export_directory']).glob('*/*.json')]
    return [r for r in rows if kind is None or r['kind'] == kind]


def test_selected_ignored_registration_resolution_and_capture(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW)
    first = L.step(src, dst, NOW, cfg)
    regs = events(cfg, 'registration')
    assert len(regs) == 6
    assert {r['source']['prediction']['selected'] for r in regs} == {True, False}
    assert all(r['source']['status'] == 'pending' for r in regs)
    assert len(events(cfg, 'population')[0]['source']['rows']) == 6
    end = first['recent'][0]['deadline_ms']
    publish(src, end+1, 'resolve', 120)
    L.step(src, dst, end+1, cfg)
    assert len(events(cfg, 'terminal')) == 6
    capture = P.capture(cfg, end+2)
    assert not capture['coverage']['typed_outcome_refusals']
    assert len(capture['dataset']['rows']) >= 6
    assert capture['coverage']['local_restoration_ms'] == end+2
    assert not capture['coverage']['search_ready']
    L.step(src, dst, end+2, cfg)
    assert len(events(cfg, 'terminal')) == 6


def test_missing_and_expired_survive_eviction(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW)
    first = L.step(src, dst, NOW, cfg)
    end = first['recent'][0]['deadline_ms']
    late = end+L.PROTOCOL['grace_ms']+1
    L.step(src, dst, late, cfg)
    assert len(events(cfg, 'terminal')) == 6
    assert {e['source']['status'] for e in events(cfg, 'terminal')} == {'unavailable'}
    L.step(src, dst, late+L.PROTOCOL['retention_ms']+1, cfg)
    with sqlite3.connect(dst) as db:
        assert db.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 0
    assert len(events(cfg, 'registration')) == len(events(cfg, 'terminal')) == 6


def test_late_activation_is_gap_not_backfill(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW)
    L.step(src, dst, NOW)
    L.step(src, dst, NOW+1000, cfg)
    assert not events(cfg, 'registration')
    assert len([e for e in events(cfg, 'gap') if e['source']['reason'] == 'missed_registration']) == 6
    assert events(cfg, 'activation')[0]['source']['missed_registration_interval'] == [NOW, NOW+1000]


def test_investigation_all_results_and_registration_memory(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'investigation.db'
    publish(src, NOW)
    C.step(src, dst, NOW, cfg)
    regs = events(cfg, 'registration')
    assert regs and all(e['source']['updates'] == [] for e in regs)
    assert all(e['source']['memory']['status'] == 'frozen_at_registration' for e in regs)
    end = C.dossiers(dst)[0]['investigation']['measurement']['deadline_ms']
    publish(src, end+1, 'complete')
    C.step(src, dst, end+1, cfg)
    terminals = events(cfg, 'terminal')
    assert len(terminals) == len(regs)
    assert all(e['source']['updates'][-1]['evidence']['status'] == 'measured' for e in terminals)
    C.step(src, dst, end+2, cfg)
    assert len(events(cfg, 'terminal')) == len(terminals)
    assert len(events(cfg, 'population')[0]['source']['rows']) == 6
    assert not P.capture(cfg, end+3)['coverage']['typed_outcome_refusals']


def test_investigation_expiry_and_pruning(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'investigation.db'
    publish(src, NOW); C.step(src, dst, NOW, cfg)
    expiry = max(d['investigation']['measurement']['expires_ms'] for d in C.dossiers(dst))
    C.step(src, dst, expiry+1, cfg)
    assert all(e['source']['updates'][-1]['evidence']['status'] == 'not_testable' for e in events(cfg, 'terminal'))
    C.step(src, dst, expiry+C.RETENTION_MS+2, cfg)
    assert not C.dossiers(dst)
    assert events(cfg, 'terminal') and events(cfg, 'registration')


def test_outbox_rollback_capacity_immutable_and_retry(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    db = sqlite3.connect(tmp_path/'outbox.db')
    with db:
        pop = P.Producer(db, cfg, 'forecast', NOW)
        pop.emit('one', 'terminal', {'a': 1})
    monkeypatch.setattr(P, 'MAX_EXPORT_BYTES', 1)
    with pytest.raises(ValueError, match='export_capacity'):
        P.flush(db, cfg, 'forecast')
    assert db.execute('SELECT COUNT(*) FROM population_events WHERE payload IS NOT NULL').fetchone()[0] == 2
    monkeypatch.setattr(P, 'MAX_EXPORT_BYTES', 256*1024**2)
    P.flush(db, cfg, 'forecast'); P.flush(db, cfg, 'forecast')
    assert len(events(cfg)) == 2
    pop.emit('one', 'terminal', {'a': 1})
    with pytest.raises(ValueError, match='immutable_conflict'):
        pop.emit('one', 'terminal', {'a': 2})
    with pytest.raises(RuntimeError):
        with db:
            pop.emit('rolled_back', 'registration', {'a': 3})
            raise RuntimeError()
    P.flush(db, cfg, 'forecast')
    assert len(events(cfg)) == 2


def test_failed_export_prevents_source_pruning(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW)
    monkeypatch.setattr(P, 'MAX_EXPORT_BYTES', 1)
    with pytest.raises(ValueError, match='export_capacity'):
        L.step(src, dst, NOW, cfg)
    with pytest.raises(ValueError, match='export_capacity'):
        L.step(src, dst, NOW+L.PROTOCOL['retention_ms']*2, cfg)
    with sqlite3.connect(dst) as db:
        assert db.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 6


def test_capacity_rolls_back_registration(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW)
    monkeypatch.setattr(P, 'MAX_PENDING', 2)
    with pytest.raises(ValueError, match='export_required'):
        L.step(src, dst, NOW, cfg)
    with sqlite3.connect(dst) as db:
        assert db.execute('SELECT COUNT(*) FROM episodes').fetchone()[0] == 0


def test_boundary_and_clock_regression(tmp_path):
    cfg = config(tmp_path)
    with sqlite3.connect(tmp_path/'p.db') as db:
        pop = P.Producer(db, cfg, 'forecast', NOW)
        assert pop.eligible('S0/USDT', NOW)
        assert not pop.eligible('S0/USDT', NOW+86400000)
        assert not pop.eligible('OTHER', NOW)
        with pytest.raises(ValueError, match='clock_regression'):
            P.Producer(db, cfg, 'forecast', NOW-1)


def test_capture_replays_without_source_and_refuses_tamper(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'investigation.db'
    publish(src, NOW); C.step(src, dst, NOW, cfg)
    end = C.dossiers(dst)[0]['investigation']['measurement']['expires_ms']
    C.step(src, dst, end+1, cfg)
    artifact = P.capture(cfg, end+2)
    src.unlink(); dst.unlink()
    assert P.replay_capture(artifact) == artifact['coverage']
    bad = copy.deepcopy(artifact)
    bad['events'][0]['local_imported_ms'] -= 1
    bad['sha256'] = D.digest({k:v for k,v in bad.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='integrity'):
        P.replay_capture(bad)


def test_late_terminal_cannot_enter_prospective_typed_dataset(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); first = L.step(src, dst, NOW)
    end = first['recent'][0]['deadline_ms']
    publish(src, end+1, 'resolved', 120)
    L.step(src, dst, end+1, cfg)
    artifact = P.capture(cfg, end+2)
    assert len(events(cfg, 'terminal')) == 6
    assert not artifact['dataset']['rows']
    assert artifact['coverage']['explicit_gaps']


def test_local_index_preserves_knowledge_clock_and_rejects_conflict(tmp_path):
    cfg = config(tmp_path)
    src, dst = tmp_path/'attention.db', tmp_path/'learning.db'
    publish(src, NOW); first = L.step(src, dst, NOW, cfg)
    end = first['recent'][0]['deadline_ms']
    publish(src, end+1, 'resolved', 120); L.step(src, dst, end+1, cfg)
    cfg['local_ledgers'] = {'forecast': str(dst)}
    artifact = P.capture(cfg, NOW+86400001)
    assert artifact['coverage']['local_restoration_ms'] is None
    assert artifact['dataset']['rows']
    with sqlite3.connect(dst) as db:
        db.execute('UPDATE population_events SET local_imported_ms=local_imported_ms-1')
    with pytest.raises(ValueError, match='local_receipt_mismatch'):
        P.capture(cfg, NOW+86400002)
