"""Autonomous scheduled research: snapshot -> derived study -> bounded steps -> persisted outcomes.
Synthetic fixtures only; nothing here is evidence of an edge. Qualification/handoff are never run by the scheduler."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tests import compose_fixtures as C
from trader.core.child import run_child
from trader.core.journal import Journal
from trader.owner import queries as Q
from trader.research.closure import auto, manifest, study as S
from trader.research.closure.store import Store
from trader.research.compose import search as csearch


def hog_memory(n):
    return len(bytearray(n))


def spin(_):
    while True:
        pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cfg_for(tmp, snap, **auto_over):
    a = dict(enabled=True, snapshot_dir=str(tmp / 'snaps'), market_db=str(snap), fit_bars=900, interval_hours=12,
             max_candidates=2, search=dict(max_attempts=3, max_fits=1, factors=['rsi14', 'relvol50', 'ret24'],
                                           structures=['long_only'], interaction_share=0.0), unit_timeout_s=900)
    a.update(auto_over)
    return dict(research=dict(closure=dict(enabled=True, store_path=str(tmp / 'auto.closure'), auto=a)),
                risk=dict(C.RISK))


# ── snapshot: filesystem copy of a live WAL database ───────────────────────
def test_snapshot_copies_files_settles_the_copy_and_leaves_source_untouched(tmp_path):
    src = tmp_path / 'live' / 'candles.db'
    src.parent.mkdir()
    con = sqlite3.connect(src)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE t(x INTEGER)')
    con.executemany('INSERT INTO t VALUES (?)', [(i,) for i in range(50)])
    con.commit()                                             # committed rows still live in the -wal (connection stays open)
    assert Path(str(src) + '-wal').exists() and Path(str(src) + '-wal').stat().st_size > 0
    before = (sha(src), sha(str(src) + '-wal'))
    out = auto.copy_database(src, tmp_path / 'snaps' / 'candles-1.db')
    assert (sha(src), sha(str(src) + '-wal')) == before                                    # source bytes untouched
    dst = Path(out['path'])
    assert not Path(str(dst) + '-wal').exists() and not Path(str(dst) + '-shm').exists()   # quiescent single file
    with sqlite3.connect(dst) as db:
        assert db.execute('SELECT COUNT(*) FROM t').fetchone()[0] == 50                    # WAL content reached the copy
    manifest.refuse_live(dst)                                                              # accepted as a quiescent snapshot
    con.close()


def test_missing_source_is_refused_and_no_partial_file_remains(tmp_path):
    with pytest.raises(Exception, match='auto_source_missing'):
        auto.copy_database(tmp_path / 'nope.db', tmp_path / 'snaps' / 'candles-1.db')
    assert not list((tmp_path / 'snaps').glob('*')) if (tmp_path / 'snaps').exists() else True


# ── resource enforcement in children ───────────────────────────────────────
def test_memory_ceiling_is_enforced_in_the_child():
    res = run_child(hog_memory, 600 * 1024 * 1024, timeout_s=60, memory_mb=256)
    assert not res.ok and 'MemoryError' in res.error and not res.timed_out
    assert run_child(hog_memory, 1024, timeout_s=60, memory_mb=256).ok


def test_cpu_ceiling_ends_a_runaway_child_well_before_the_deadline():
    res = run_child(spin, 0, timeout_s=60, cpu_seconds=2)
    assert not res.ok and res.elapsed_s < 30 and not res.timed_out


def test_dataset_read_budget_is_enforced_at_admission(tmp_path):
    st = Store(tmp_path / 'b.closure')
    budget = csearch.register_budget(st, 's', dict(dataset_reads=2, factors=['rsi14', 'relvol50', 'ret24'],
                                                   structures=['long_only'], max_fits=1, interaction_share=0.0))
    from trader.research.compose import catalog
    status = {r['key']: r['status'] for d in catalog.inventory(None)['domains'].values() for r in d['factors']}
    out = csearch.plan(st, 's', budget, status, now_ms=1)
    assert len(out['admitted']) == 2 and out['stopped'] == 'BUDGET_EXHAUSTED:dataset_reads'
    assert out['spent']['dataset_reads'] == 2
    with pytest.raises(Exception):
        csearch.register_budget(st, 's2', dict(memory_mb=0))                              # memory ceiling must be positive
    # a budget registered before these keys existed still resumes with defaults, and a widened one conflicts
    st.put_once('budgets', 'old', {k: v for k, v in csearch.DEFAULT_BUDGET.items() if k != 'memory_mb'})
    assert csearch.register_budget(st, 'old', {})['memory_mb'] == csearch.DEFAULT_BUDGET['memory_mb']
    with pytest.raises(Exception, match='record_conflict'):
        csearch.register_budget(st, 'old', dict(max_attempts=999))


# ── scheduling outcomes ────────────────────────────────────────────────────
def test_invalid_config_and_failed_snapshot_are_recorded_not_raised(tmp_path):
    cfg = cfg_for(tmp_path, tmp_path / 'absent.db')
    out = S.scheduled(cfg, cycle_seconds=1.0, slot_remaining_s=60)                           # real child, fails fast
    assert out['status'] == 'PREP_FAILED'
    bad = cfg_for(tmp_path, tmp_path / 'absent.db')
    del bad['research']['closure']['auto']['market_db']
    assert S.scheduled(bad, slot_remaining_s=60)['status'] == 'CONFIG_INVALID'
    st = Store(tmp_path / 'auto.closure')
    assert [e['status'] for e in st.schedule_events()][:2] == ['CONFIG_INVALID', 'PREP_FAILED']
    assert not st.studies()                                                                  # nothing invented
    st.close()


def test_scheduled_without_auto_keeps_manual_contract(tmp_path):
    cfg = dict(research=dict(closure=dict(enabled=True, store_path=str(tmp_path / 'm.closure'))))
    assert S.scheduled(cfg, slot_remaining_s=60)['status'] == 'NOT_INITIALISED'


def test_insufficient_coverage_is_data_blocked_without_padding(tmp_path, monkeypatch):
    snap = tmp_path / 'live' / 'market-src.db'
    snap.parent.mkdir()
    C.write_context_snapshot(snap, n_symbols=10)
    cfg = cfg_for(tmp_path, snap)                                         # real configured policy needs 16 discovery symbols
    st = Store(tmp_path / 'auto.closure')
    out = auto.tick(cfg, st, auto.settings(cfg), now_ms=2_000_000_000_000, budget_s=600, child=False)
    assert out['status'] == 'DATA_BLOCKED' and 'insufficient_covered_symbols' in out['detail']['reason']
    assert not st.studies() and st.schedule_events()[0]['status'] == 'DATA_BLOCKED'
    st.close()


def test_autonomous_study_is_created_resumed_bounded_and_projected(tmp_path, monkeypatch):
    snap = tmp_path / 'live' / 'market-src.db'
    snap.parent.mkdir()
    C.write_context_snapshot(snap, n_symbols=10)
    src_hash = sha(snap)
    monkeypatch.setattr(manifest, 'configured_policy', lambda cfg: manifest.test_policy())   # synthetic floors only
    cfg = cfg_for(tmp_path, snap)
    st = Store(tmp_path / 'auto.closure')
    a = auto.settings(cfg)
    out = auto.tick(cfg, st, a, now_ms=2_000_000_000_000, budget_s=900, child=False)
    assert out['status'] == 'STEPPED', out
    [row] = st.studies()
    study = json.loads(row['payload'])
    assert study['origin'] == 'auto' and study['snapshot_path'] != str(snap)                  # read from the COPY
    assert Path(study['snapshot_path']).name.startswith('candles-') and sha(snap) == src_hash
    assert study['development_mode'] == 'RETROSPECTIVE' and len(study['discovery_symbols']) == 6
    kinds = {u['kind']: u['status'] for u in st.units(row['study_id'])}
    assert kinds['search'] == 'DONE' and any(u['kind'] == 'develop' and u['status'] == 'DONE' for u in st.units(row['study_id']))
    # qualification is NEVER run by the scheduler: no protected look, qualify (if planned) stays pending
    assert kinds.get('qualify', 'PENDING') == 'PENDING' and kinds.get('handoff', 'PENDING') == 'PENDING'
    assert not st.all_once('looks') and not st.all_once('qualifications')
    # timings and outcomes persisted per unit
    for u in st.units(row['study_id']):
        if u['status'] == 'DONE':
            r = json.loads(u['result'])
            assert r['started_ms'] and r['ended_ms'] >= r['started_ms'] and r['elapsed_s'] >= 0
    # a second tick resumes/idles rather than creating a duplicate study before the interval
    again = auto.tick(cfg, st, a, now_ms=2_000_000_001_000, budget_s=900, child=False)
    assert len(st.studies()) == 1 and again['status'] in ('STEPPED', 'IDLE')
    # due again after the interval -> a NEW snapshot and study, same policy, nothing carried over as evidence
    later = auto.tick(cfg, st, a, now_ms=2_000_000_000_000 + 13 * 3_600_000, budget_s=900, child=False)
    assert later['status'] in ('STEPPED', 'IDLE') and len(st.schedule_events(100)) >= 3
    ev = auto.evidence(st.path)
    assert ev['studies'] and ev['studies'][-1]['origin'] == 'auto' and ev['schedule_events']
    assert 'never run by the scheduler' in ev['note']
    st.close()
    # owner projection (read-only) through the existing research query
    j = Journal(tmp_path / 'j.db')
    before = j._conn().total_changes
    res = Q.query(j, 'research', cfg=cfg, root=tmp_path)
    assert any(r['kind'] == 'closure_research' for r in res['records']) and j._conn().total_changes == before
