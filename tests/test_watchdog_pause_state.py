"""Control metadata, synchronous shell stubs, and read-only /tmp safety checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from scripts import monitor as M
from trader.observability import pause_state as P, bootstrap as B
from trader.observability.safety import SafetyHealth, SafetyObserver
from tests.test_launch_preflight import local, NOW

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('network forbidden'))
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network forbidden'))


@pytest.fixture
def root(tmp_path):
    (tmp_path/'data').mkdir()
    return tmp_path


def tree(root):
    return sorted((str(p.relative_to(root)),p.lstat().st_size,p.lstat().st_mtime_ns)
                  for p in root.rglob('*'))


def test_unified_status_is_noncreating_and_reports_all_switches(root):
    (root/'data/watchdog.off').touch()
    (root/'data/declared_population.enabled').touch()
    before = tree(root)
    result = P.status(root)
    assert result['valid'] and result['watchdog_paused'] and not result['kernel_supervision_allowed']
    assert not result['safety_monitor_paused']
    assert set(result['switches']) == set(P.FLAGS)
    assert result['switches']['declared_population.enabled']['state'] == 'PRESENT'
    assert result['switches']['accounting.enabled']['state'] == 'ABSENT'
    assert tree(root) == before


def test_status_on_fresh_root_does_not_create_data(tmp_path):
    assert P.status(tmp_path)['valid']
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('name', P.FLAGS + P.LOCKS)
def test_symlink_and_dangling_symlink_switches_and_locks_are_invalid(root, name):
    target = root/'target'
    target.write_text('unchanged')
    path = root/'data'/name
    path.symlink_to(target)
    for _ in range(2):
        state = P.status(root)
        record = state['switches' if name in P.FLAGS else 'locks'][name]
        assert record['state'] == 'INVALID' and record['reason'] == 'symlink'
        assert not state['kernel_supervision_allowed']
        assert not state['safety_monitor_paused']
        if target.exists():
            assert target.read_text() == 'unchanged'
            target.unlink()


@pytest.mark.parametrize('kind', ['directory','fifo','writable','hardlink','foreign_owner'])
def test_unsafe_switch_types_permissions_and_ownership_are_rejected(root, monkeypatch, kind):
    flag = root/'data/watchdog.off'
    if kind == 'directory': flag.mkdir()
    elif kind == 'fifo': os.mkfifo(flag)
    elif kind == 'hardlink':
        target = root/'target'; target.touch(); os.link(target,flag)
    else:
        flag.touch()
        if kind == 'writable': flag.chmod(0o666)
        if kind == 'foreign_owner':
            original = os.stat
            def foreign(path, *args, **kwargs):
                st = original(path,*args,**kwargs)
                if str(path) == 'watchdog.off':
                    values = list(st); values[4] = os.geteuid()+1
                    return os.stat_result(values)
                return st
            monkeypatch.setattr(os,'stat',foreign)
    result = P.status(root)
    assert not result['valid'] and not result['watchdog_paused']


def test_secure_locks_reject_symlinks_and_exclude_second_holder(root):
    path = root/'data/bootstrap-controller.lock'
    target = root/'target'; target.write_text('protected')
    path.symlink_to(target)
    with pytest.raises((OSError,P.UnsafeControlPath)):
        with P.file_lock(path): pytest.fail('symlink accepted')
    assert target.read_text() == 'protected'
    path.unlink()
    with P.file_lock(path) as fd:
        assert not os.get_inheritable(fd)
        with pytest.raises(BlockingIOError):
            with P.file_lock(path): pytest.fail('second holder accepted')


def test_bootstrap_lock_and_safety_lock_reject_symlink_targets(root, monkeypatch, capsys):
    target = root/'target'; target.write_text('protected')
    (root/'data/bootstrap-controller.lock').symlink_to(target)
    monkeypatch.setattr(B,'execute',lambda *a,**k:pytest.fail('controller executed'))
    assert B.main(['--root',str(root),'--expect-revision','a'*40]) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'bootstrap_controller_lock_unsafe'
    (root/'data/safety_health.lock').symlink_to(target)
    with pytest.raises((OSError,P.UnsafeControlPath)):
        SafetyHealth(root/'data/safety_health.json').observe('test','failed')
    assert target.read_text() == 'protected'


def test_data_directory_symlink_is_refused_without_writes(root):
    (root/'data').rmdir()
    other = root/'other'; other.mkdir()
    (root/'data').symlink_to(other,target_is_directory=True)
    assert not P.status(root)['directory_valid']
    assert M.safety_check(root,{})['health']['recovery_required']
    assert not list(other.iterdir())


def test_monitor_status_does_not_load_config_or_open_sqlite(root, monkeypatch, capsys):
    from trader.core import config
    (root/'data/observers.off').touch()
    before = tree(root)
    monkeypatch.setattr(config,'ROOT',root)
    monkeypatch.setattr(M,'load_config',lambda:pytest.fail('status loaded configuration'))
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:pytest.fail('status opened SQLite'))
    monkeypatch.setattr(sys,'argv',['monitor','--status'])
    assert M.main() == 0
    assert json.loads(capsys.readouterr().out)['observers_paused']
    assert tree(root) == before


def test_lock_runner_keeps_lock_but_does_not_pass_descriptor_to_child(root):
    path = root/'data/attention_learning.lock'
    code = '''import fcntl,os,pathlib,sys
p=pathlib.Path(sys.argv[1])
assert not any(os.path.realpath('/proc/self/fd/'+name)==str(p) for name in os.listdir('/proc/self/fd'))
with p.open('a') as f:
 try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:pass
 else:raise AssertionError('parent did not hold lock')
'''
    assert P.main(['--root',str(root),'--lock-run','attention_learning.lock','--',
                   sys.executable,'-B','-c',code,str(path)]) == 0
    with P.file_lock(path):
        pass  # released on child completion


def disk_ok(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(os,'statvfs',lambda *a:SimpleNamespace(f_bavail=2*1024**3,f_frsize=1,f_flag=0))


def test_paused_monitor_is_readonly_preserves_latches_and_does_not_spam_missing_heartbeat(local, monkeypatch):
    root = local[0]
    disk_ok(monkeypatch)
    events = []
    health = SafetyHealth(root/'data/safety_health.json',clock=lambda:NOW,sink=events.append)
    health.observe('heartbeat:luffy','MISSING')
    health.observe('journal_write','SQLITE_IOERR')
    original_condition = health.read()['conditions']['heartbeat:luffy']
    events.clear()
    path = root/'data/luffy.db'
    exact = hashlib.sha256(path.read_bytes()).hexdigest()
    original = sqlite3.connect
    statements = []
    class ReadOnly(sqlite3.Connection):
        def execute(self, sql, *a, **k):
            statements.append(sql)
            assert not sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','DROP'))
            return super().execute(sql,*a,**k)
    def connect(database,*a,**k):
        assert 'mode=ro' in str(database)
        return original(database,*a,**k,factory=ReadOnly)
    monkeypatch.setattr(sqlite3,'connect',connect)
    monkeypatch.setattr(SafetyObserver,'heartbeat',lambda *a,**k:pytest.fail('intentional stop heartbeat alarm'))
    for _ in range(2):
        result = M.safety_check(root,{},clock=lambda:NOW,sink=events.append,identity_probe=lambda _:dict(state='NONE'))
        assert result['components'][0]['status'] == 'INTENTIONALLY_STOPPED'
        store = result['components'][-1]
        assert store['status'] == 'AVAILABLE' and store['committed'] is False
        assert store['venue_protection']['status'] == 'VERIFIED'
        assert result['health']['recovery_required']
        assert result['health']['conditions']['journal_write']['status'] == 'ACTIVE'
    assert health.read()['conditions']['heartbeat:luffy'] == original_condition
    assert events == [] and statements
    assert hashlib.sha256(path.read_bytes()).hexdigest() == exact


@pytest.mark.parametrize('state',['RUNNING','UNLOCKED_PROCESS','UNKNOWN'])
def test_pause_does_not_hide_heartbeat_failures_for_running_or_unconfirmed_kernel(local, monkeypatch, state):
    calls = []
    monkeypatch.setattr(SafetyObserver,'heartbeat',lambda *a,**k:calls.append('heartbeat') or dict(status='STALE'))
    result = M.safety_check(local[0],{},heartbeat_only=True,identity_probe=lambda _:dict(state=state))
    assert calls == ['heartbeat'] and result['components'][0]['status'] == 'STALE'
    if state != 'RUNNING':
        assert result['health']['conditions']['paused_kernel_identity']['status'] == 'ACTIVE'


def test_paused_disk_and_venue_uncertainty_still_raise_alerts(local, monkeypatch):
    from types import SimpleNamespace
    root = local[0]
    monkeypatch.setattr(os,'statvfs',lambda *a:SimpleNamespace(f_bavail=0,f_frsize=1,f_flag=0))
    events = []
    first = M.safety_check(root,{},clock=lambda:NOW+1000,sink=events.append,identity_probe=lambda _:dict(state='NONE'))
    second = M.safety_check(root,{},clock=lambda:NOW+1001,sink=events.append,identity_probe=lambda _:dict(state='NONE'))
    assert first['health']['conditions']['paused_disk']['status'] == 'ACTIVE'
    assert first['health']['conditions']['paused_venue_protection']['status'] == 'ACTIVE'
    assert second['health']['recovery_required'] and len(events) == 2
    assert 'heartbeat:luffy' not in second['health']['conditions']


def test_only_valid_explicit_monitor_off_suppresses_all_safety_checks(local, monkeypatch):
    root = local[0]
    flag = root/'data/safety_monitor.off'; flag.touch()
    before = tree(root)
    monkeypatch.setattr(sqlite3,'connect',lambda *a,**k:pytest.fail('disabled SQL'))
    monkeypatch.setattr(SafetyObserver,'heartbeat',lambda *a,**k:pytest.fail('disabled heartbeat'))
    result = M.safety_check(root,{})
    assert result['components'][0]['status'] == 'DISABLED' and tree(root) == before
    flag.unlink(); flag.symlink_to(root/'data/watchdog.off')
    result = M.safety_check(root,{},heartbeat_only=True,identity_probe=lambda _:dict(state='NONE'))
    assert result['components'][0]['status'] == 'INTENTIONALLY_STOPPED'
    assert result['health']['conditions']['control_paths']['status'] == 'ACTIVE'


@pytest.fixture
def shell(root):
    (root/'scripts').mkdir(); (root/'venv/bin').mkdir(parents=True)
    shutil.copyfile(REPO/'scripts/watchdog.sh',root/'scripts/watchdog.sh')
    # Synchronous launcher/monitor/observer stubs; never run a real service.
    shim = root/'venv/bin/python'
    shim.write_text('#!/usr/bin/python3\n'+f'''
import json,os,sys
sys.path.insert(0,{str(REPO)!r})
args=sys.argv[1:]
if args[:1]==['-B']:args=args[1:]
if args[:2]==['-m','trader.observability.pause_state']:
 from trader.observability.pause_state import main
 raise SystemExit(main(args[2:]))
with open('calls','a') as f:f.write(' '.join(args)+'\\n')
raise SystemExit(10 if 'trader.runtime_identity' in args else int(os.environ.get('MONITOR_EXIT','0')))
''')
    shim.chmod(0o755)
    restart = root/'restart.sh'
    restart.write_text('#!/bin/sh\nprintf "restart %s\\n" "$1" >> calls\n')
    restart.chmod(0o755)
    return root


def run_shell(root, *args, monitor_exit=0):
    return subprocess.run(['bash',str(root/'scripts/watchdog.sh'),*args],cwd=root,
        env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1','TMPDIR':'/tmp','MONITOR_EXIT':str(monitor_exit)},
        capture_output=True,text=True,timeout=20)


def test_paused_watchdog_runs_monitor_without_identity_probe_or_restart(shell):
    (shell/'data/watchdog.off').touch()
    result = run_shell(shell)
    assert result.returncode == 0, result.stderr
    assert (shell/'calls').read_text().splitlines() == ['-m scripts.monitor --safety-only']


def test_explicit_monitor_off_and_watchdog_off_run_no_actions(shell):
    for name in ('watchdog.off','safety_monitor.off'):(shell/'data'/name).touch()
    assert run_shell(shell).returncode == 0
    assert not (shell/'calls').exists()


def test_paused_watchdog_propagates_monitor_failure_without_restart(shell):
    (shell/'data/watchdog.off').touch()
    assert run_shell(shell,monitor_exit=1).returncode == 1
    assert (shell/'calls').read_text().splitlines() == ['-m scripts.monitor --safety-only']


def test_unpaused_watchdog_monitors_before_launch_and_observers_have_independent_pause(shell):
    (shell/'data/observers.off').touch()
    result = run_shell(shell)
    assert result.returncode == 0, result.stderr
    calls = (shell/'calls').read_text().splitlines()
    assert calls[0] == '-m scripts.monitor --safety-only'
    assert any('trader.runtime_identity status-only' in call for call in calls[1:])
    assert 'restart kernel' in calls
    assert not any('trader.observability.learning' in call for call in calls)


def test_watchdog_status_is_inert_and_reports_invalid_flags(shell):
    (shell/'data/accounting.enabled').touch()
    before = tree(shell)
    result = run_shell(shell,'--status')
    assert result.returncode == 0
    assert json.loads(result.stdout)['switches']['accounting.enabled']['state'] == 'PRESENT'
    assert tree(shell) == before and not (shell/'logs').exists()
    (shell/'data/safety_monitor.off').symlink_to(shell/'missing')
    result = run_shell(shell,'--status')
    assert result.returncode == 2
    assert not json.loads(result.stdout)['safety_monitor_paused']


def test_unsafe_flag_or_lock_never_authorizes_restart_or_suppresses_monitor(shell):
    (shell/'data/safety_monitor.off').symlink_to(shell/'missing')
    (shell/'data/attention_learning.lock').symlink_to(shell/'missing-lock')
    result = run_shell(shell)
    assert result.returncode == 2
    assert (shell/'calls').read_text().splitlines() == ['-m scripts.monitor --safety-only']
    assert not (shell/'missing-lock').exists()
