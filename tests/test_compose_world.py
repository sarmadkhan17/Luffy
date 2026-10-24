"""Actual retained producer -> graph -> research/compiler, with causal mutations."""
from contextlib import closing
import json
import shutil
import sqlite3
import numpy as np
import pytest

from tests.test_wrld05_normal_context import retained, NOW, TF, SYMBOL, query  # noqa: F401
from trader.research.compose import data, graph
from trader.research.compose.world_snapshot import seal_world
from trader.research.closure import ClosureRefused, development
from trader.research import evaluate
from trader.strategy.compile import compile_spec
from trader.world.context import load_context


def candidate():
    return graph.make_candidate(dict(question='Does retained volume context select entries?', rationale='test only',
        expected='different observations change entries', failure_conditions='missing history',
        tf='4h', geo='fixed', structure='long_only',
        long={'cmp':'gt','a':{'f':'world_volume_anomaly'},'b':{'lit':1}}, short=None))


def test_world_path_reaches_bundle_and_compiled_entries_without_fallback(retained):
    path, df = retained
    ext = data.load_external(['world'], path, None, [SYMBOL], NOW, world_path=path)
    assert not ext['gaps']
    env = dict(tf='4h', risk={}, dev_end_ms=NOW, projection_counts={}, policy=dict(
        min_threshold_samples=2, min_finite_frac=.1, draws=2, seed=1))
    bundle = development.build_bundle({SYMBOL:df}, env, ext)
    assert bundle.world is ext['world']
    # Same existing compiler used by saved strategies, not a test-only evaluator.
    spec = graph.realize(candidate(), 'world-test').to_spec()
    comp = compile_spec(spec)
    lo, sh = comp.entries(bundle.sym_frames[SYMBOL], symbol=SYMBOL, world=bundle.world)
    absent, _ = comp.entries(bundle.sym_frames[SYMBOL], symbol=SYMBOL, world=None)
    assert np.asarray(lo).any() and not np.asarray(absent).any()
    assert not np.asarray(sh).any()
    missing = data.load_external(['world'], path, None, [SYMBOL], NOW)
    assert missing['gaps'][0]['requirement'] == 'world'


def test_future_world_changes_do_not_change_development_identity(retained, tmp_path):
    path, _ = retained
    early = NOW - TF
    before = data.load_external(['world'], path, None, [SYMBOL], early, world_path=path)
    other = tmp_path/'mutated.db'; shutil.copyfile(path, other)
    with closing(sqlite3.connect(other)) as db:
        db.execute('DELETE FROM scans WHERE as_of_ms>?', (early,))
        db.commit()
    after = data.load_external(['world'], other, None, [SYMBOL], early, world_path=other)
    assert before['data_id'] == after['data_id']
    assert query(before['world'], early) == query(after['world'], early)
    assert query(before['world'], NOW).reason == 'QUERY_AFTER_CONTEXT_CUT'


def test_sealed_world_reaches_normal_qualification_loader_and_is_immutable(retained, tmp_path):
    path, _ = retained
    env = dict(tf='4h', split=dict(start_ms=NOW-2*TF, end_ms=NOW-TF))
    dst = tmp_path/'sealed.db'
    receipt = seal_world(path, dst, env)
    context = evaluate.load_world({'world':str(dst)}, ['world'], NOW)
    assert receipt['cuts'] == 3 and query(context) == query(load_context(path, immutable=True))
    assert seal_world(path, dst, env) == receipt
    with closing(sqlite3.connect(path)) as db:
        db.execute('DELETE FROM scans WHERE as_of_ms=?', (NOW,))
        db.commit()
    with pytest.raises(ClosureRefused, match='sealed_world_changed'):
        seal_world(path, dst, env)
    assert query(evaluate.load_world({'world':str(dst)}, ['world'], NOW)) == query(context)


def test_missing_or_corrupt_source_refused_before_look(tmp_path):
    env = dict(tf='4h', split=dict(start_ms=NOW-TF, end_ms=NOW))
    with pytest.raises(ClosureRefused, match='world_snapshot_not_supplied'):
        seal_world(None, tmp_path/'sealed.db', env)
    src=tmp_path/'bad.db'
    with closing(sqlite3.connect(src)) as db:
        db.execute('CREATE TABLE scans(as_of_ms INTEGER,payload TEXT)')
        db.execute('INSERT INTO scans VALUES (?,?)',(NOW, '{bad'))
        db.commit()
    with pytest.raises(ClosureRefused, match='world_snapshot_invalid'):
        seal_world(src, tmp_path/'sealed.db', env)


def test_sealing_does_not_repair_a_mismatched_source_cut(retained, tmp_path):
    path, _ = retained
    env = dict(tf='4h', split=dict(start_ms=NOW-2*TF, end_ms=NOW-TF))
    with closing(sqlite3.connect(path)) as db:
        row = db.execute('SELECT payload FROM scans WHERE as_of_ms=?', (NOW,)).fetchone()
        payload = json.loads(row[0])
        payload['as_of_ms'] = NOW - 1
        db.execute('UPDATE scans SET payload=? WHERE as_of_ms=?', (json.dumps(payload), NOW))
        db.commit()
    dst = tmp_path/'sealed.db'
    with pytest.raises(ClosureRefused, match='world_snapshot_invalid'):
        seal_world(path, dst, env)
    assert not dst.exists()


def test_latest_inventory_does_not_reconstruct_older_models_but_normal_reader_still_validates_them(retained):
    from trader.observability.store import read_world_history
    from trader.research.compose import catalog
    path, _ = retained
    full, status = read_world_history(path, immutable=True)
    latest, last_status = read_world_history(path, immutable=True, latest_only=True)
    assert status == last_status == 'RETAINED'
    assert latest.cuts == (max(full.cuts),)
    assert latest.get_exact(latest.cuts[0]).to_json() == full.get_exact(max(full.cuts)).to_json()
    early, _ = read_world_history(path, immutable=True, latest_only=True, as_of_ms=NOW-TF)
    assert early.cuts == (NOW-TF,)
    from trader.research.closure.auto import copy_database
    snapshot = path.with_name('inventory-copy.db')
    copy_database(path, snapshot, min_free_bytes=0)
    measured = catalog.measure(snapshot, world_path=snapshot)
    expected = sum(o.kind == 'volume_anomaly' and o.quality.value == 'VALID' and type(o.value) in (int, float)
                   for state in latest.get_exact(latest.cuts[0]).states for o in state.observations)
    assert measured['world']['volume_anomaly']['rows'] == expected
    assert measured['world_measurement_scope'].startswith('LATEST_VERIFIED_CUT_ONLY')
