from scripts import portable
from trader import runtime_identity
import json
import os
from pathlib import Path
import subprocess
import sys


def test_failed_verified_stop_does_not_touch_dashboard(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr(portable, 'ROOT', tmp_path)
    monkeypatch.setattr(runtime_identity, 'inspect', lambda *a: {'state': 'NONE'})
    monkeypatch.setattr(runtime_identity, 'stop_kernel', lambda *a, **kw: 17)
    monkeypatch.setattr(portable.os, 'kill', lambda *a: (_ for _ in ()).throw(AssertionError('signal after refused stop')))
    assert portable.stop(1) == 17
    assert (tmp_path / 'data/watchdog.off').exists()


def test_unconfirmed_freeze_prevents_shutdown(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    monkeypatch.setattr(portable, 'ROOT', tmp_path)
    monkeypatch.setattr(runtime_identity, 'inspect', lambda *a: {'state': 'RUNNING', 'verified': True})
    monkeypatch.setattr(portable, 'freeze_for_shutdown', lambda: False)
    monkeypatch.setattr(runtime_identity, 'stop_kernel', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('unconfirmed containment')))
    assert portable.stop(1) == 5


def test_start_uses_supported_launcher_with_exact_revision(monkeypatch):
    monkeypatch.setattr(portable.sys, 'argv', ['luffy', 'start-kernel'])
    monkeypatch.setattr(runtime_identity, 'code_revision', lambda *a: {'revision': 'a' * 40})
    calls = []
    monkeypatch.setattr(portable.subprocess, 'call', lambda args, **kw: calls.append((args, kw)) or 0)
    assert portable.main() == 0
    args, kwargs = calls[0]
    assert args == [str(portable.ROOT / 'restart.sh'), 'kernel']
    assert kwargs['env']['LUFFY_EXPECT_REVISION'] == 'a' * 40


def test_copied_launcher_owns_temporary_directory_from_any_cwd(tmp_path):
    root = tmp_path / 'copied LUFFY'; root.mkdir()
    launcher = root / 'luffy'
    launcher.write_bytes((Path(__file__).resolve().parents[1] / 'luffy').read_bytes())
    launcher.chmod(0o755)
    binary = root / 'venv/bin/python'; binary.parent.mkdir(parents=True)
    binary.write_text('#!' + sys.executable + '\nimport os,json\n'
                      'print(json.dumps({"cwd":os.getcwd(),"tmp":os.environ["TMPDIR"]}))\n')
    binary.chmod(0o755)
    run = subprocess.run([str(launcher), 'status'], cwd=tmp_path,
                         env={**os.environ, 'TMPDIR': '/old-machine/absent'},
                         capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == {'cwd': str(root), 'tmp': str(root / 'tmp')}
    assert (root / 'tmp').is_dir()


def _contained_store(tmp_path, state='FROZEN', hold='1'):
    import sqlite3
    (tmp_path / 'data').mkdir()
    (tmp_path / 'config.yaml').write_text('mode:\n  market: futures\n')
    with sqlite3.connect(tmp_path / 'data/luffy.db') as db:
        db.execute('CREATE TABLE state_kv(key TEXT PRIMARY KEY,value TEXT)')
        db.executemany('INSERT INTO state_kv VALUES (?,?)', [
            ('control_state', state), ('macro_guard_operator_hold', hold),
            ('execution_recovery', 'retain-this-recovery-state'), ('market_type', 'spot')])


def test_refresh_refuses_uncontained_state_before_venue_access(tmp_path, monkeypatch):
    import pytest
    from trader.data import feed
    _contained_store(tmp_path, state='ACTIVE', hold='0')
    monkeypatch.setattr(runtime_identity, 'legacy_kernel_pids', lambda: [])
    monkeypatch.setattr(feed, 'make_exchange', lambda *a: pytest.fail('venue accessed without containment'))
    with pytest.raises(ValueError, match='stopped_operator_containment_required'):
        portable.refresh_protection(tmp_path)


def test_refresh_refuses_existing_kernel_lock(tmp_path, monkeypatch):
    import fcntl
    import pytest
    _contained_store(tmp_path)
    with (tmp_path / 'data/kernel.lock').open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            portable.refresh_protection(tmp_path)


def test_refresh_uses_bound_market_preserves_control_and_recovery(tmp_path, monkeypatch):
    import fcntl
    import sqlite3
    import pytest
    from trader.data import feed
    from trader.engine import protection_snapshot
    _contained_store(tmp_path)
    monkeypatch.setattr(runtime_identity, 'legacy_kernel_pids', lambda: [])
    seen = []
    monkeypatch.setattr(feed, 'make_exchange', lambda market: seen.append(market) or object())
    status = ['VERIFIED']
    class Monitor:
        def run_once(self):
            with (tmp_path / 'data/kernel.lock').open('a') as contender:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return {'status': status[0], 'control_state_observed': 'FROZEN', 'mutations': 0}
    monkeypatch.setattr(protection_snapshot, 'make_monitor', lambda *a, **kw: Monitor())
    assert portable.refresh_protection(tmp_path)['allow'] is True
    status[0] = 'PARTIAL'
    assert portable.refresh_protection(tmp_path)['allow'] is False
    assert seen == ['spot', 'spot']
    with sqlite3.connect(tmp_path / 'data/luffy.db') as db:
        state = dict(db.execute('SELECT key,value FROM state_kv'))
    assert state['control_state'] == 'FROZEN'
    assert state['macro_guard_operator_hold'] == '1'
    assert state['execution_recovery'] == 'retain-this-recovery-state'


def test_refresh_refuses_other_kernel_before_venue_access(tmp_path, monkeypatch):
    import pytest
    from trader.data import feed
    _contained_store(tmp_path)
    monkeypatch.setattr(runtime_identity, 'legacy_kernel_pids', lambda: [12345])
    monkeypatch.setattr(feed, 'make_exchange', lambda *a: pytest.fail('venue accessed with another Kernel'))
    with pytest.raises(ValueError, match='kernel_process_present'):
        portable.refresh_protection(tmp_path)


def test_refresh_requires_operator_hold_even_when_frozen(tmp_path, monkeypatch):
    import pytest
    from trader.data import feed
    _contained_store(tmp_path, hold='0')
    monkeypatch.setattr(runtime_identity, 'legacy_kernel_pids', lambda: [])
    monkeypatch.setattr(feed, 'make_exchange', lambda *a: pytest.fail('venue accessed without operator hold'))
    with pytest.raises(ValueError, match='stopped_operator_containment_required'):
        portable.refresh_protection(tmp_path)
