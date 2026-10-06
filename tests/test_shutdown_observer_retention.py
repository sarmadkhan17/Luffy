"""OBS-04: corrected prepared controller, fixture children, real user timers."""
import importlib.util
import ast
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from trader.observability.shutdown import service_state

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/tracker/evidence/obs04-shutdown-sol/source'


@pytest.fixture
def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE))
    spec = importlib.util.spec_from_file_location('obs04_launcher', SOURCE / 'launch_once.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('case', ['live', 'ticks_mismatch', 'pid_mismatch', 'missing_proc',
                                 'unreadable_proc', 'zombie_unreaped', 'terminated'])
def test_death_requires_owned_child_confirmation(monkeypatch, case):
    identity = {'pid': 71, 'start_ticks': '123'}
    process = SimpleNamespace(pid=71, poll=lambda: 0 if case == 'terminated' else None)
    fields = ['S'] + ['0'] * 18 + ['123']
    if case == 'ticks_mismatch': fields[19] = '456'
    if case == 'pid_mismatch': identity['pid'] = 72
    if case == 'zombie_unreaped': fields[0] = 'Z'
    def read(path):
        if case == 'missing_proc': raise FileNotFoundError()
        if case == 'unreadable_proc': raise PermissionError()
        return '71 (fixture name) ' + ' '.join(fields)
    monkeypatch.setattr(Path, 'read_text', read)
    expected = 'TERMINATED' if case == 'terminated' else 'LIVE' if case == 'live' else 'UNKNOWN'
    assert service_state(process, identity) == expected


def test_identity_uncertainty_neither_signals_nor_cleans(launcher, monkeypatch):
    host = launcher.Host()
    host.pids = {'kernel': {'pid': os.getpid(), 'start_ticks': 'wrong'}}
    host.processes = {'kernel': SimpleNamespace(pid=os.getpid(), poll=lambda: None,
        send_signal=lambda sig: pytest.fail('identity mismatch signalled'))}
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *a, **k: pytest.fail('cleanup executed'))
    assert host.alive('kernel') and host.shutdown_state('kernel') == 'UNKNOWN'
    host.signal_services()
    with pytest.raises(RuntimeError, match='shutdown_unconfirmed'):
        host.timers_stop()


def test_retained_predecessor_mismatch_wrongly_permits_cleanup(launcher, monkeypatch):
    spec = importlib.util.spec_from_file_location('obs04_before', SOURCE / 'before-launch_once.py')
    before = importlib.util.module_from_spec(spec); spec.loader.exec_module(before)
    host = before.Host(); host.units = ['fixture-heartbeat']
    host.pids = {'kernel': {'pid': os.getpid(), 'start_ticks': 'wrong'}}
    host.processes = {'kernel': SimpleNamespace(pid=os.getpid(), poll=lambda: None)}
    commands = []
    monkeypatch.setattr(before.subprocess, 'run', lambda command, **kwargs:
                        commands.append(command) or SimpleNamespace(returncode=0, stdout='not-found\n'))
    assert host.processes['kernel'].poll() is None and not host.alive('kernel')
    host.timers_stop()
    assert commands[0] == ['systemctl', '--user', 'stop', 'fixture-heartbeat.timer']


def test_retention_exception_leaves_observers_owned_and_running(launcher, tmp_path, monkeypatch):
    host = launcher.Host(); host.directory = tmp_path
    host.preflight = lambda: {'allow': True}
    host.storage = lambda *a: None
    host.timers_start = lambda: host.units.append('fixture-heartbeat')
    def start(role):
        host.pids[role] = {'pid': os.getpid(), 'start_ticks': 'uncertain'}
        host.processes[role] = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    host.start = start
    monkeypatch.setattr(launcher.collector, 'observe', lambda *a: dict(exact_stop_reason='graceful_shutdown_timeout'))
    def interrupted(*a): raise RuntimeError('fixture collector interrupted')
    monkeypatch.setattr(launcher.collector, 'retain_until_stopped', interrupted)
    monkeypatch.setattr(host, 'timers_stop', lambda: pytest.fail('uncertain cleanup'))
    with pytest.raises(RuntimeError, match='collector interrupted'):
        launcher.execute(host)
    record = json.loads((tmp_path / 'launch-result.json').read_text())
    assert host.units and record['observers_retained'] and record['shutdown_states']['kernel'] == 'UNKNOWN'


def test_preflight_refusal_has_no_operational_effect(launcher, tmp_path, monkeypatch):
    host = launcher.Host(); host.directory = tmp_path
    host.preflight = lambda: {'allow': False, 'reasons': ['fixture_refusal']}
    for name in ('storage', 'timers_start', 'start', 'signal_services', 'final_account'):
        monkeypatch.setattr(host, name, lambda *a: pytest.fail('effect before preflight'))
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *a, **k: pytest.fail('process effect'))
    assert launcher.execute(host)['exact_stop_reason'] == 'preflight_refused'


def test_launched_directory_prevents_second_attempt(launcher, tmp_path, monkeypatch):
    (tmp_path / 'pids.json').write_text('{}')
    monkeypatch.setattr(launcher, 'Host', lambda: SimpleNamespace(directory=tmp_path))
    monkeypatch.setattr(launcher, 'execute', lambda *a: pytest.fail('duplicate launch'))
    monkeypatch.setattr(sys, 'argv', ['fixture', '--execute-authorized'])
    with pytest.raises(SystemExit, match='one attempt already launched'):
        launcher.main()


def test_exact_original_finally_removes_observers_despite_timeout(tmp_path):
    """Execute the retained unsafe finally block with inert process/systemctl seams."""
    tree = ast.parse((SOURCE / 'original-unsafe-launcher.py').read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
    cleanup = next(node for node in main.body if isinstance(node, ast.Try)).finalbody
    commands = []
    process = SimpleNamespace(pid=71, poll=lambda: None, send_signal=lambda sig: None)
    def timeout(**kwargs): raise subprocess.TimeoutExpired('fixture', 420)
    process.wait = timeout
    fake_subprocess = SimpleNamespace(TimeoutExpired=subprocess.TimeoutExpired,
        run=lambda command, **kwargs: commands.append(command) or SimpleNamespace(returncode=0))
    namespace = dict(processes={'kernel': process}, units=['fixture-heartbeat', 'fixture-storage'],
        subprocess=fake_subprocess, signal=signal, time=SimpleNamespace(monotonic=lambda: 0),
        python='fixture-python', here=tmp_path, run=tmp_path, json=json)
    exec(compile(ast.Module(body=cleanup, type_ignores=[]), '<retained-original-finally>', 'exec'), namespace)
    assert process.poll() is None
    assert len([c for c in commands if c[0] == 'systemctl']) == 2
    assert json.loads((tmp_path / 'launch-result.json').read_text())['exits']['kernel'] is None


def test_confirmed_clean_child_exit_precedes_cleanup(launcher, tmp_path, monkeypatch):
    host = launcher.Host(); host.directory = tmp_path
    host.preflight = lambda: {'allow': True}
    events = []
    host.storage = lambda name: events.append(name)
    host.timers_start = lambda: host.units.append('fixture-heartbeat')
    host.final_account = lambda: 0
    def start(role):
        child = subprocess.Popen([sys.executable, '-B', '-c', 'pass'])
        host.processes[role] = child
        host.pids[role] = {'pid': child.pid, 'start_ticks': 'fixture'}
        child.wait(timeout=5)
        events.append('verified_exit')
    host.start = start
    monkeypatch.setattr(launcher.collector, 'observe', lambda *a: dict(exact_stop_reason='clean'))
    def command(args, **kwargs):
        assert host.shutdown_state('kernel') == 'TERMINATED'
        events.append('cleanup')
        return SimpleNamespace(returncode=0, stdout='not-found\n')
    monkeypatch.setattr(launcher.subprocess, 'run', command)
    result = launcher.execute(host)
    assert result['observers_stopped'] and not result.get('observers_retained')
    assert events.index('verified_exit') < events.index('storage-final-stopped') < events.index('cleanup')


def test_real_timers_retain_truthful_observations_until_verified_exit(launcher, tmp_path):
    # Never launch Kernel/Dashboard. The child deliberately delays SIGTERM exit
    # until a fixture release file appears. Timer workers call actual safety probes.
    child_file = tmp_path / 'child.py'
    child_file.write_text('''import signal, time
from pathlib import Path
import sys
root = Path(sys.argv[1])
signal.signal(signal.SIGTERM, lambda *a: (root/'term').touch())
(root/'ready').touch()
while not (root/'release').exists(): time.sleep(.02)
''')
    observer_file = tmp_path / 'observer.py'
    observer_file.write_text(f'''import json, os, sys, time
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
root = Path(sys.argv[1]); mode = sys.argv[2]
def audit(event, args):
    if event.startswith('socket.'): raise RuntimeError('network forbidden')
    if event == 'open':
        path, access, flags = args
        writing = (isinstance(access, str) and any(c in access for c in 'wax+')) or (isinstance(flags, int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if not isinstance(path, int):
            resolved = Path(os.fsdecode(path)).resolve()
            if resolved.name == '.env' or (writing and root not in (resolved, *resolved.parents)):
                raise RuntimeError('nonfixture access forbidden')
sys.addaudithook(audit)
from trader.observability.safety import SafetyHealth, SafetyObserver, HeartbeatPolicy
observer = SafetyObserver(SafetyHealth(root/(mode+'-health.json'), sink=lambda event: None))
if mode == 'heartbeat':
    result = observer.heartbeat(root/'missing-heartbeat.json', 'luffy', HeartbeatPolicy.configured({{'timeframes': {{'scan_interval_seconds': 60}}}}))
else:
    result = observer.store(root/'fixture.db', 'journal', journal=True)
ticks = Path('/proc', str(os.getpid()), 'stat').read_text().rsplit(')', 1)[1].split()[19]
with (root/(mode+'.jsonl')).open('a') as stream:
    stream.write(json.dumps(dict(at=time.time(), pid=os.getpid(), start_ticks=ticks, result=result))+'\\n')
''')
    import sqlite3
    with sqlite3.connect(tmp_path / 'fixture.db') as db:
        db.execute('CREATE TABLE state_kv(key TEXT PRIMARY KEY,value TEXT)')
    host = launcher.Host(); host.directory = tmp_path
    events = []; marks = {}
    host.preflight = lambda: {'allow': True}
    host.storage = lambda name: events.append(name)
    host.resources = lambda: None
    host.final_account = lambda: 0
    def run(command):
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
    def rows(mode):
        path = tmp_path / (mode + '.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    def wait_for(predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            assert time.monotonic() < deadline, 'fixture deadline exceeded'
            time.sleep(.05)
    def timers_start():
        for mode in ('heartbeat', 'storage'):
            unit = 'luffy-obs04-fixture-' + uuid4().hex
            host.units.append(unit)
            run(['systemd-run', '--user', '--unit=' + unit, '--on-active=1s',
                 '--on-unit-inactive=1s', '--timer-property=AccuracySec=10ms',
                 '--property=Restart=no', '--property=TimeoutStartSec=10s',
                 sys.executable, '-B', str(observer_file), str(tmp_path), mode])
        wait_for(lambda: all(rows(mode) for mode in ('heartbeat', 'storage')))
    host.timers_start = timers_start
    def start(role):
        assert role == 'kernel' and not host.pids, 'duplicate or Dashboard launch'
        child = subprocess.Popen([sys.executable, '-B', str(child_file), str(tmp_path)])
        host.processes[role] = child
        ticks = Path('/proc', str(child.pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
        host.pids[role] = dict(pid=child.pid, start_ticks=ticks)
        (tmp_path / 'pids.json').write_text(json.dumps(host.pids))
        wait_for(lambda: (tmp_path / 'ready').exists())
    host.start = start
    def observe(directory, backend):
        backend.signal_services()
        wait_for(lambda: (tmp_path / 'term').exists())
        assert backend.alive('kernel')  # SIGTERM delivery does not confirm death.
        with pytest.raises(subprocess.TimeoutExpired):
            backend.processes['kernel'].wait(timeout=.2)  # Original retained drain is 420s.
        marks['timeout_at'] = time.time()
        marks['bound_child_identity'] = dict(backend.pids['kernel'])
        marks['counts'] = {mode: len(rows(mode)) for mode in ('heartbeat', 'storage')}
        # Nearby adverse variant: lose identity while the child remains alive.
        backend.pids['kernel']['start_ticks'] = 'mismatch'
        assert backend.shutdown_state('kernel') == 'UNKNOWN'
        with pytest.raises(RuntimeError): backend.timers_stop()
        return dict(exact_stop_reason='graceful_shutdown_timeout', still_running=['kernel'])
    original_sleep = host.sleep
    def retention_sleep(seconds):
        assert host.alive('kernel')
        assert json.loads((tmp_path / 'launch-result.json').read_text())['observers_retained']
        for unit in host.units:
            assert run(['systemctl', '--user', 'is-active', unit + '.timer']).stdout.strip() == 'active'
        if all(len(rows(mode)) >= marks['counts'][mode] + 2 for mode in marks['counts']):
            marks['release_at'] = time.time()
            (tmp_path / 'release').touch()
            host.processes['kernel'].wait(timeout=5)
            assert host.shutdown_state('kernel') == 'TERMINATED'
        else:
            assert time.time() - marks['timeout_at'] < 15
        original_sleep(.1)
    host.sleep = retention_sleep
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(launcher.collector, 'observe', observe)
            result = launcher.execute(host)  # Actual execute + actual retention loop + actual cleanup.
        assert result['observers_retained'] and result['observers_stopped']
        assert result['shutdown_states']['kernel'] == 'UNKNOWN'
        assert result['final_shutdown_states']['kernel'] == 'TERMINATED'
        assert result['final_still_running'] == []
        for unit in host.units:
            stopped = subprocess.run(['systemctl', '--user', 'is-active', unit + '.timer'],
                                     capture_output=True, text=True, timeout=10)
            assert stopped.returncode != 0 and stopped.stdout.strip() in ('inactive', 'unknown')
        assert events.index('storage-timeout-continuing') < events.index('storage-after-delayed-termination')
        time.sleep(.3)
        counts = {mode: len(rows(mode)) for mode in marks['counts']}
        time.sleep(1.5)
        assert counts == {mode: len(rows(mode)) for mode in marks['counts']}
        for mode in marks['counts']:
            during = [r for r in rows(mode) if marks['timeout_at'] < r['at'] < marks['release_at']]
            assert len(during) >= 2 and all(r['pid'] > 0 and r['start_ticks'] for r in during)
            if mode == 'heartbeat': assert all(r['result']['status'] == 'MISSING' for r in during)
            else: assert all(r['result']['committed'] for r in during)
        assert json.loads((tmp_path / 'pids.json').read_text())['kernel'] == marks['bound_child_identity']
        evidence = dict(result=result, marks=marks, child_identity=marks['bound_child_identity'],
                        units=host.units, observations={mode: rows(mode) for mode in marks['counts']},
                        cleanup_counts=counts, events=events)
        evidence_path = os.environ.get('OBS04_FIXTURE_EVIDENCE')
        if evidence_path: Path(evidence_path).write_text(json.dumps(evidence, indent=2) + '\n')
    finally:
        (tmp_path / 'release').touch()
        for child in host.processes.values(): child.wait(timeout=5)
        for unit in host.units:
            subprocess.run(['systemctl', '--user', 'stop', unit + '.timer', unit + '.service'],
                           capture_output=True, timeout=10)
