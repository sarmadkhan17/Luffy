"""Generated scratch input release never removes incomplete-study evidence."""
from copy import deepcopy
import fcntl
import hashlib
import json

import pytest

from trader.research.closure import ClosureRefused, calibration as C


def protocol():
    p = dict(deepcopy(C.PROTOCOL), **C.SMOKE)
    p.update(replicates=1, cases=[p['cases'][0]], retain_generated_snapshots=False)
    return p


def stub_replica(calls, *, completed=True):
    def replica(p, case, index, seed, work, run_id):
        calls.append(seed)
        directory = work / f'{case["name"]}-{index}'
        directory.mkdir()
        # Use a real generated database. Its numerical result is explicitly a
        # scheduling fixture; no economic or qualification claim is made.
        C.synthetic.write_snapshot(directory / 'snapshot.db', n_symbols=2, n_bars=20, seed=seed)
        (directory / 'closure.db').write_bytes(b'retained study evidence fixture')
        return dict(run_id=run_id, case=case['name'], index=index, seed=seed,
                    outcome='NO_FINALIST', trials=0, failed_units=0,
                    process_status='COMPLETED' if completed else 'PAUSED',
                    stop_reason='NO_FINALIST' if completed else 'DEADLINE')
    return replica


def test_release_follows_completed_result_and_resume_uses_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(C, '_replicate', stub_replica(calls))
    p = protocol()
    report = C.run(p, tmp_path)
    directory = tmp_path / 'null_independent-0'
    saved = json.loads((tmp_path / 'rep-null_independent-0.json').read_text())
    receipt = json.loads((directory / 'snapshot-release.json').read_text())
    assert saved['process_status'] == 'COMPLETED'
    assert receipt['run_id'] == saved['run_id'] and receipt['seed'] == saved['seed']
    assert len(receipt['snapshot_sha256']) == 64 and receipt['snapshot_bytes'] > 0
    assert not (directory / 'snapshot.db').exists()
    assert (directory / 'closure.db').read_bytes() == b'retained study evidence fixture'
    assert report['calibration_status'] == 'INCONCLUSIVE' and not report['authority_granted']
    assert report['generated_snapshot_retention'] == 'RELEASED_AFTER_COMPLETED_RESULT'
    assert C.run(p, tmp_path) == report and len(calls) == 1


def test_default_retains_input_and_failed_execution_cannot_release_it(tmp_path, monkeypatch):
    monkeypatch.setattr(C, '_replicate', stub_replica([]))
    p = protocol(); p.pop('retain_generated_snapshots')
    report = C.run(p, tmp_path / 'retained')
    assert report['generated_snapshot_retention'] == 'RETAINED'
    assert (tmp_path / 'retained/null_independent-0/snapshot.db').exists()
    monkeypatch.setattr(C, '_replicate', stub_replica([], completed=False))
    failed = tmp_path / 'failed'
    with pytest.raises(ClosureRefused, match='calibration_replicate_incomplete'):
        C.run(protocol(), failed)
    assert (failed / 'null_independent-0/snapshot.db').exists()
    assert not (failed / 'rep-null_independent-0.json').exists()
    assert not (failed / 'null_independent-0/snapshot-release.json').exists()


def test_failed_result_commit_never_releases_generated_input(tmp_path, monkeypatch):
    monkeypatch.setattr(C, '_replicate', stub_replica([]))
    monkeypatch.setattr(C, '_save_record', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        C.run(protocol(), tmp_path)
    assert (tmp_path / 'null_independent-0/snapshot.db').exists()
    assert not (tmp_path / 'null_independent-0/snapshot-release.json').exists()


def test_active_run_refuses_second_producer_before_any_experiment(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('second producer started')
    monkeypatch.setattr(C, '_run', forbidden)
    with (tmp_path / '.calibration.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ClosureRefused, match='calibration_already_running'):
            C.run(protocol(), tmp_path)


def test_release_receipt_is_written_before_input_disappears(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(C, '_replicate', stub_replica(calls))
    save = C._save_record
    def observe(path, value):
        if path.name == 'snapshot-release.json':
            snapshot = path.parent / 'snapshot.db'
            assert snapshot.exists()
            assert (tmp_path / 'rep-null_independent-0.json').exists()
            assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == value['snapshot_sha256']
        save(path, value)
    monkeypatch.setattr(C, '_save_record', observe)
    C.run(protocol(), tmp_path)


def test_retention_change_cannot_retroactively_delete_frozen_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(C, '_replicate', stub_replica([]))
    retained = protocol(); retained.pop('retain_generated_snapshots')
    C.run(retained, tmp_path)
    with pytest.raises(ValueError, match='calibration_run_identity_changed'):
        C.run(protocol(), tmp_path)
    assert (tmp_path / 'null_independent-0/snapshot.db').exists()
    assert not (tmp_path / 'null_independent-0/snapshot-release.json').exists()


@pytest.mark.parametrize('value', ['false', 0, None])
def test_invalid_retention_policy_does_not_launch_or_delete(tmp_path, monkeypatch, value):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid policy started a replicate')
    monkeypatch.setattr(C, '_replicate', forbidden)
    with pytest.raises(ValueError, match='retention_must_be_boolean'):
        C.run(dict(protocol(), retain_generated_snapshots=value), tmp_path)
