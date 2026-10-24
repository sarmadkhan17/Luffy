"""Fixture-only falsifiers for corrected research efficiency contracts."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock
import pytest
from trader.research.closure import development as D, manifest, study, exposure, ClosureRefused
from trader.research.closure.store import Store
from trader.research.compose import data, world_producer as W


def test_re08_lineage_reuses_exact_frame_identity(monkeypatch):
    import pandas as pd
    df = pd.DataFrame({'ts': [pd.Timestamp('2020-01-01', tz='UTC')], 'value': [1.0]})
    identity = data._frame_id(df)
    before = data._lineage(df)
    monkeypatch.setattr(data, '_frame_id', lambda _: pytest.fail('duplicate hash'))
    assert data._lineage(df, identity) == before


@pytest.fixture
def cached_development(tmp_path, monkeypatch):
    s = Store(tmp_path / 'research.db')
    env = dict(envelope_id='e', evaluator_id='eval', cost_id='cost', split_id='split',
               development_status='ELIGIBLE', development_mode='RETROSPECTIVE', policy={'draws': 4}, risk={})
    monkeypatch.setattr(D, 'require_current', lambda e: None)
    monkeypatch.setattr(D.families, 'verify_candidate', lambda c: c)
    monkeypatch.setattr(D.graph, 'is_graph', lambda c: True)
    monkeypatch.setattr(D.manifest, 'development_frames', lambda *a: ({'S': object()}, 'data'))
    external = dict(data_id='external', gaps=[])
    monkeypatch.setattr(D, '_external', lambda *a: dict(external))
    calls = []
    def evaluate(c, frames, data_id, env, draws, ext):
        calls.append(draws)
        return dict(outcome='DEVELOPMENT_POSITIVE', combo={'parts': []})
    monkeypatch.setattr(D, '_develop', evaluate)
    yield s, env, external, calls
    s.close()


def test_re01_child_readonly_cache_options_and_dependency_invalidation(cached_development):
    s, env, external, calls = cached_development
    c = {'candidate_id': 'c'}
    first = D.develop(c, 'fixture', env, store=s, draws=2)
    hit = D.develop(c, 'fixture', env, cache_path=s.path, draws=2)
    assert hit['cached'] and hit['result_id'] == first['result_id'] and calls == [2]
    D.develop(c, 'fixture', env, cache_path=s.path, draws=3)
    external['data_id'] = 'corrected-availability'
    D.develop(c, 'fixture', env, cache_path=s.path, draws=2)
    assert calls == [2, 3, 2]


def test_re01_missing_or_transient_failure_never_permanent(cached_development, monkeypatch):
    s, env, external, calls = cached_development
    c = {'candidate_id': 'c'}
    def evaluate(*args):
        calls.append(1)
        return {'outcome': 'CODE_FAILURE' if len(calls) == 1 else 'DEVELOPMENT_POSITIVE', 'combo': {'parts': []}}
    monkeypatch.setattr(D, '_develop', evaluate)
    a = D.develop(c, 'fixture', env, store=s)
    b = D.develop(c, 'fixture', env, store=s)
    assert a['outcome'] == 'CODE_FAILURE' and b['outcome'] == 'DEVELOPMENT_POSITIVE'
    external['gaps'] = [{'requirement': 'funding', 'reason': 'missing'}]
    # Same served-frame IDs cannot establish negative-state cache equivalence.
    assert not D.develop(c, 'fixture', env, cache_path=s.path)['cached']
    assert len(calls) == 3


def test_re01_restores_embedded_fitted_content(cached_development, monkeypatch):
    from trader.strategy import fitted
    s, env, external, calls = cached_development
    c = {'candidate_id': 'c'}
    monkeypatch.setattr(D, '_develop', lambda *a: {'outcome': 'DEVELOPMENT_POSITIVE',
        'combo': {'parts': [{'artifacts': [{'artifact_id': 'fixture-artifact'}]}]}})
    D.develop(c, 'fixture', env, store=s)
    restored = []
    monkeypatch.setattr(fitted, 'register_artifact', lambda a: restored.append(a))
    D.develop(c, 'fixture', env, cache_path=s.path)
    assert restored == [{'artifact_id': 'fixture-artifact'}]


def test_re02_same_job_manifest_no_second_inventory(tmp_path, monkeypatch):
    from tests.closure_fixtures import build
    from tests.test_closure_study import spec_for
    b = build(tmp_path, name='prepared', seal='SYNTHETIC')
    spec = spec_for(b)
    syms = spec['discovery_symbols'] + spec['heldout_symbols']
    m = manifest.build_manifest(spec['snapshot_path'], '4h', syms, exposure.events(b.store))
    monkeypatch.setattr(manifest, 'build_manifest', lambda *a, **k: pytest.fail('duplicate inventory'))
    sid = study.create_study(b.store, spec, now_ms=1, prepared_manifest=m)
    assert b.store.study(sid)
    b.store.close()


def test_re02_sidecar_and_content_handoff_refused(tmp_path):
    p = tmp_path / 'fixture.db'
    p.write_bytes(b'fixture')
    identity = manifest.snapshot_identity(p)
    Path(str(p) + '-wal').write_bytes(b'changed logical database')
    with pytest.raises(ClosureRefused, match='snapshot_(sidecar_present|not_quiescent)'):
        manifest.require_unchanged(p, identity)
    Path(str(p) + '-wal').unlink()
    p.write_bytes(b'changed')
    with pytest.raises(ClosureRefused, match='snapshot_changed_since_manifest'):
        manifest.require_unchanged(p, identity)


def test_re03_narrow_store_selectors_preserve_order_and_failures(tmp_path):
    s = Store(tmp_path / 's.db')
    for i in range(5):
        s.put_run(str(i), dict(result_id='r' + str(i), candidate_id='c', envelope_id='wanted' if i < 2 else 'other', stage='DEVELOPMENT', outcome='CODE_FAILURE'))
    assert [r['result_id'] for r in s.runs_for(envelope_id='wanted')] == ['r0', 'r1']
    assert s.runs_for(result_id='r1')[0]['outcome'] == 'CODE_FAILURE'
    s.close()


def test_re06_auto_outer_lock_does_not_reenter_step(tmp_path, monkeypatch):
    from trader.research.closure import auto
    s = Store(tmp_path / 's.db')
    calls = []
    def body(*args, **kwargs):
        with study.single_flight(s) as free:
            assert not free
        calls.append(1)
        return {'status': 'FIXTURE'}
    monkeypatch.setattr(auto, '_tick', body)
    assert auto.tick({}, s, {}, now_ms=1, budget_s=1)['status'] == 'FIXTURE'
    assert calls == [1]
    s.close()


def test_re07_stream_counts_order_and_failure_atomicity(tmp_path):
    path = tmp_path / 'world.db'
    seen = []
    def rows():
        for i in range(3):
            seen.append(i)
            yield i, json.dumps({'as_of_ms': i})
    result = W.write_store(path, rows())
    assert result['cuts'] == 3 and seen == [0, 1, 2]
    with sqlite3.connect(path) as db:
        assert [r[0] for r in db.execute('SELECT as_of_ms FROM scans ORDER BY rowid')] == seen
    before = path.read_bytes()
    def broken():
        yield 4, '{}'
        raise RuntimeError('fixture interruption')
    with pytest.raises(RuntimeError):
        W.write_store(path, broken())
    assert path.read_bytes() == before and not path.with_suffix('.pending').exists()


def test_re04_raw_collector_preserves_local_clocks_and_trace(monkeypatch):
    import dataclasses
    from tests.test_compose_trace import _gapped_frame
    from tests.closure_fixtures import RISK
    from trader.research import evaluate as E
    from trader.research.closure import families
    a = _gapped_frame(430, 7)
    b = _gapped_frame(430, 8).iloc[37:].reset_index(drop=True)
    env = dict(tf='4h', risk=dict(RISK, real_funding=False), dev_end_ms=int(a.ts.iloc[-1].timestamp() * 1000),
        projection_counts={'a': {}, 'b': {}}, policy=dict(draws=2, seed=1, min_threshold_samples=2, min_finite_frac=.1))
    bundle = D.build_bundle({'A': a, 'B': b}, env)
    c = families.realize(families.make_candidate('trend_breakout.v1'), {}, 'collector-fixture')
    raw = {}
    real = E.simulate
    calls = []
    def count(*args, **kwargs):
        calls.append(kwargs['symbol'])
        return real(*args, **kwargs)
    monkeypatch.setattr(E, 'simulate', count)
    observed = E.evaluate(c, bundle, draws=2, min_symbol_trades=100000, raw_runs=raw, traces=True)
    assert calls == ['A', 'B']
    plain = E.evaluate(c, bundle, draws=2, min_symbol_trades=100000)
    reference = D.run_symbols(c, bundle, traces=True)
    assert observed == plain
    for sym in raw:
        assert raw[sym]['df'] is bundle.frames[sym]
        assert raw[sym]['fills'] == reference[sym]['fills']
        assert raw[sym]['trace'] == reference[sym]['trace']
        assert dataclasses.asdict(raw[sym]['result']) == dataclasses.asdict(reference[sym]['result'])
    def broken(*args, **kwargs):
        if kwargs['symbol'] == 'B':
            raise ValueError('fixture per-symbol failure')
        return real(*args, **kwargs)
    monkeypatch.setattr(E, 'simulate', broken)
    with pytest.raises(ValueError, match='fixture per-symbol failure'):
        E.evaluate(c, bundle, draws=2, min_symbol_trades=100000, raw_runs={})


def test_managed_scheduled_entry_does_not_boot_kernel(monkeypatch):
    from trader.research.closure import __main__ as cli
    from trader import runtime_identity
    seen = []
    monkeypatch.setattr(runtime_identity, 'check_revision', lambda revision, root: seen.append(revision))
    monkeypatch.setattr(study, 'scheduled', lambda cfg, **kw: {'status': 'FIXTURE', 'stop': kw['should_stop']()})
    cfg = {'research': {'enabled': True, 'closure': {'slot_budget_s': 12}}}
    result = cli.scheduled_once('fixture-revision', cfg=cfg, should_stop=lambda: True)
    assert seen == ['fixture-revision'] and result == {'status': 'FIXTURE', 'stop': True}


def test_unreaped_child_fence_blocks_other_coordinator_processes(tmp_path):
    s = Store(tmp_path / 's.db')
    Path(str(s.path) + '.child-unreaped').write_text('CHILD_NOT_REAPED\n')
    with study.single_flight(s) as free:
        assert not free
    other = Store(s.path)
    with study.single_flight(other) as free:
        assert not free
    other.close()
    s.close()


def test_re05_fence_clears_only_authoritative_original_cessation(tmp_path, monkeypatch):
    from trader.core import child
    from trader.core.child import ChildResult
    s = Store(tmp_path / 's.db')
    result = ChildResult(ok=False, terminated=False, pid=1234, start_ticks=99)
    study.fence_child(s, result, stage='fixture', attempt_ms=7)
    monkeypatch.setattr(child, 'process_state', lambda pid: ('S', 99))
    with study.single_flight(s) as free:
        assert not free
    monkeypatch.setattr(child, 'process_state', lambda pid: (_ for _ in ()).throw(PermissionError()))
    with study.single_flight(s) as free:
        assert not free
    monkeypatch.setattr(child, 'process_state', lambda pid: ('S', 100))  # PID reuse proves original ceased
    with study.single_flight(s) as free:
        assert free
    assert not Path(str(s.path) + '.child-unreaped').exists()
    assert s.schedule_events(1)[0]['status'] == 'CHILD_CESSATION_VERIFIED'
    s.close()


def test_re05_completed_development_recovers_without_new_evaluation(tmp_path, monkeypatch):
    from trader.core import child
    from trader.core.child import ChildResult, _pack
    from trader.research.closure import digest
    s = Store(tmp_path / 's.db')
    # Fixture isolated identity; real reconciliation verifies frozen envelope/candidate/hash.
    monkeypatch.setattr(study, '_load', lambda *a: ({}, {'envelope_id': 'env'}))
    artifact = tmp_path / 'luffy-child-fixture'
    artifact.mkdir()
    result = dict(stage='DEVELOPMENT', envelope_id='env', candidate_id='candidate', cache_key='key',
                  outcome='DEVELOPMENT_POSITIVE', combo={'parts': []})
    result['result_id'] = digest(result)
    (artifact / 'result.json').write_text(json.dumps(['ok', _pack(dict(result, cached=False), [1000])]))
    study.fence_child(s, ChildResult(ok=False, terminated=False, pid=1234, start_ticks=99, artifact_dir=str(artifact)),
                      study_id='study', unit_id='develop:candidate')
    monkeypatch.setattr(child, 'process_state', lambda pid: None)
    with study.single_flight(s) as free:
        assert free
    assert s.get_run('key')['result_id'] == result['result_id']
    assert not artifact.exists()
    s.close()
