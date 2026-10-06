"""OBS-02 fixtures: real RUN-01 lock/proc identity, real heartbeat and observer.

No Kernel/Dashboard services, credentials, venue or network are used.
"""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from trader import runtime_identity as ri
from trader.engine.watchdog import Heartbeat
from trader.observability import dashboard_readiness as gate
from trader.observability.safety import HeartbeatPolicy, SafetyHealth, SafetyObserver

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/tracker/evidence/obs02-dashboard-readiness-sol/source'
CFG = {'timeframes': {'scan_interval_seconds': 60}, 'dashboard': {'host': 'fixture', 'port': 1}}
REV = 'a' * 40


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('network forbidden'))
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network forbidden'))


@pytest.fixture
def ready(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, 'code_revision', lambda root: {'revision': REV, 'dirty_code': False})
    monkeypatch.setattr(ri, 'legacy_kernel_pids', lambda *a, **k: [])
    instance = ri.KernelInstance(tmp_path); record = instance.acquire()
    now = record['started_at'] + 1
    beat = Heartbeat(path=tmp_path / 'data/heartbeat_luffy.json', clock=lambda: now)
    instance.bind_heartbeat(beat.instance_id)
    beat.beat(dict(last_successful_cycle_at=now, state='FROZEN', boot_complete=True, stopping=False))
    health = SafetyHealth(tmp_path / 'data/safety_health.json', clock=lambda: now, sink=lambda e: None)
    health.observe('fixture_recovery', 'FIXTURE_CONTAINMENT'); health.observe('fixture_recovery')
    assert SafetyObserver(health, clock=lambda: now).heartbeat(beat.path, 'luffy', HeartbeatPolicy.configured(CFG))['status'] == 'FRESH'
    with sqlite3.connect(tmp_path / 'data/luffy.db') as db:
        db.execute('CREATE TABLE state_kv(key TEXT PRIMARY KEY,value TEXT)')
        db.executemany('INSERT INTO state_kv VALUES (?,?)', [('control_state', 'FROZEN'), ('macro_guard_operator_hold', '1')])
    import yaml
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(CFG))
    fixture = SimpleNamespace(root=tmp_path, instance=instance, beat=beat, health=health, now=now)
    yield fixture
    instance.release()


def read(ready, name):
    return json.loads((ready.root / 'data' / name).read_text())


def write(ready, name, value):
    (ready.root / 'data' / name).write_text(json.dumps(value))


def check(ready, **kwargs):
    return gate.check(ready.root, CFG, clock=lambda: ready.now, **kwargs)


def mutate(ready, case):
    hb = read(ready, 'heartbeat_luffy.json'); health = read(ready, 'safety_health.json')
    identity = read(ready, 'kernel_instance.json')
    if case == 'prior_instance': hb['instance_id'] = 'prior'
    elif case == 'stale': ready.now += 241
    elif case == 'stale_work':
        ready.now += 241; hb['timestamp'] = ready.now
    elif case == 'start_tick_mismatch': identity['start_ticks'] += 1
    elif case == 'pid_only': identity.pop('heartbeat_instance_id')
    elif case == 'unlocked_pid_only': ready.instance.release()
    elif case == 'wrong_revision': identity['revision'] = 'b' * 40
    elif case == 'dirty_identity': identity['dirty_code'] = True
    elif case == 'boot_incomplete': hb['context']['boot_complete'] = False
    elif case == 'boot_unknown': hb['context'].pop('boot_complete')
    elif case == 'stopping': hb['context']['stopping'] = True
    elif case == 'blocking_health': health['conditions']['journal_write'] = {'status': 'ACTIVE', 'reason': 'COMMIT_FAILED'}
    elif case == 'unknown_health': health['conditions']['journal_write'] = {'status': 'UNKNOWN'}
    elif case == 'health_unreadable': (ready.root / 'data/safety_health.json').write_text('{'); return
    elif case == 'health_missing': (ready.root / 'data/safety_health.json').unlink(); return
    elif case == 'health_prior_instance': health['heartbeats']['luffy']['instance_id'] = 'prior'
    elif case == 'health_missing_receipt': health['heartbeats'] = {}
    elif case == 'health_time_unknown': health.pop('observed_at')
    elif case == 'health_time_future': health['observed_at'] += 1
    elif case == 'health_boot_unconfirmed': health['heartbeats']['luffy']['context']['boot_complete'] = False
    elif case == 'invalid_sequence': hb['sequence'] = 0
    elif case == 'boolean_sequence': hb['sequence'] = True
    elif case == 'sequence_regression': health['heartbeats']['luffy']['sequence'] += 1
    elif case == 'restamped_sequence':
        ready.now += 1; hb['timestamp'] = ready.now
    elif case == 'future_heartbeat': hb['timestamp'] += 1
    elif case == 'invalid_work_time': hb['context']['last_successful_cycle_at'] += 1
    elif case == 'heartbeat_before_identity': identity['started_at'] = ready.now + 1
    elif case == 'heartbeat_control_active': hb['context']['state'] = 'ACTIVE'
    elif case == 'current_control_active':
        with sqlite3.connect(ready.root / 'data/luffy.db') as db:
            db.execute("UPDATE state_kv SET value='ACTIVE' WHERE key='control_state'")
    elif case == 'hold_missing':
        with sqlite3.connect(ready.root / 'data/luffy.db') as db:
            db.execute("DELETE FROM state_kv WHERE key='macro_guard_operator_hold'")
    elif case == 'latch_cleared': health['recovery_required'] = False
    else: raise AssertionError(case)
    write(ready, 'heartbeat_luffy.json', hb); write(ready, 'safety_health.json', health)
    if case != 'unlocked_pid_only': write(ready, 'kernel_instance.json', identity)


BAD_CASES = ['prior_instance', 'stale', 'stale_work', 'start_tick_mismatch', 'pid_only', 'unlocked_pid_only',
    'wrong_revision', 'dirty_identity', 'boot_incomplete', 'boot_unknown', 'stopping', 'blocking_health',
    'unknown_health', 'health_unreadable', 'health_missing', 'health_prior_instance', 'health_missing_receipt',
    'health_time_unknown', 'health_time_future', 'health_boot_unconfirmed', 'invalid_sequence', 'boolean_sequence',
    'sequence_regression', 'restamped_sequence', 'future_heartbeat', 'invalid_work_time',
    'heartbeat_before_identity', 'heartbeat_control_active', 'current_control_active', 'hold_missing']


def test_genuine_current_fixture_passes_without_mutating_containment(ready):
    paths = list((ready.root / 'data').glob('*.json')) + [ready.root / 'data/luffy.db']
    before = {str(p): p.read_bytes() for p in paths}
    identity = ready.instance.record
    result = check(ready, expected_identity=identity)
    assert result['allow'] and result['result'] == 'PASS', result['reasons']
    assert result['facts']['controls']['control_state'] == 'FROZEN'
    assert result['facts']['health']['recovery_required'] is True
    assert before == {str(p): p.read_bytes() for p in paths}
    if os.environ.get('OBS02_FIXTURE_EVIDENCE'):
        Path(os.environ['OBS02_FIXTURE_EVIDENCE']).write_text(json.dumps(result, indent=2) + '\n')


@pytest.mark.parametrize('case', BAD_CASES)
def test_required_failure_prevents_direct_dashboard_attachment(ready, case, monkeypatch):
    mutate(ready, case)
    result = check(ready)
    assert result['allow'] is False and result['reasons']
    if os.environ.get('OBS02_NEGATIVE_EVIDENCE'):
        with Path(os.environ['OBS02_NEGATIVE_EVIDENCE']).open('a') as out:
            out.write(json.dumps(dict(case=case, allow=result['allow'], reasons=result['reasons'],
                                     dashboard_app_created=False)) + '\n')
    from trader.dashboard import server
    monkeypatch.setattr(server, 'ROOT', ready.root)
    monkeypatch.setattr(server, 'load_config', lambda: CFG)
    monkeypatch.setattr(gate, 'check', lambda *a, **k: result)
    monkeypatch.setattr(server, 'create_app', lambda *a: pytest.fail('Dashboard app attached on failure'))
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 1


def test_intended_process_and_reverification_are_required(ready, monkeypatch):
    assert not check(ready, expected_identity={'pid': os.getpid() + 1, 'start_ticks': 0})['allow']
    original = ri.inspect; calls = []
    def changing(root):
        state = original(root); calls.append(1)
        if len(calls) == 2: state['record']['instance_id'] = 'replacement'
        return state
    monkeypatch.setattr(ri, 'inspect', changing)
    assert 'kernel_changed_during_check' in check(ready)['reasons']


def test_independent_receipt_allows_newer_valid_sequence(ready):
    ready.now += 1
    ready.beat.clock = lambda: ready.now
    ready.beat.beat(dict(last_successful_cycle_at=ready.now, state='FROZEN', boot_complete=True, stopping=False))
    assert check(ready)['allow']


def test_known_clear_latch_is_preserved_on_healthy_readiness(ready):
    mutate(ready, 'latch_cleared')
    before = (ready.root / 'data/safety_health.json').read_bytes()
    result = check(ready)
    assert result['allow'] and result['facts']['health']['recovery_required'] is False
    assert (ready.root / 'data/safety_health.json').read_bytes() == before


def test_missing_configured_policy_fails_closed(ready):
    assert not gate.check(ready.root, {}, clock=lambda: ready.now)['allow']


def test_storage_refresh_cannot_hide_stale_independent_heartbeat(ready):
    ready.now += 241
    ready.beat.clock = lambda: ready.now
    ready.beat.beat(dict(last_successful_cycle_at=ready.now, state='FROZEN', boot_complete=True, stopping=False))
    health = read(ready, 'safety_health.json'); health['observed_at'] = ready.now
    write(ready, 'safety_health.json', health)
    assert 'independent_heartbeat_observation_stale' in check(ready)['reasons']


@pytest.mark.parametrize('fail', [False, True])
def test_actual_kernel_boot_marks_complete_only_after_success(monkeypatch, fail):
    from trader import kernel
    from trader.core.types import MarketType, ControlState
    from trader.observability import stage5_activation
    k = object.__new__(kernel.Kernel)
    k._boot_complete = True  # A failed retry cannot retain a prior completion flag.
    k.market_type = MarketType.FUTURES; k.state_machine = SimpleNamespace(state=ControlState.FROZEN)
    k.population = []; k.cfg = {'timeframes': {'scan_interval_seconds': 60}, 'references': {'enabled': False}}
    def markets():
        if fail: raise RuntimeError('fixture boot failure')
    k.feed = SimpleNamespace(initialize_markets=markets)
    k._filter_universe_to_venue = lambda: None
    k.supervisor = SimpleNamespace(pass_once=lambda **kw: SimpleNamespace(actions={'reconcile': {}}, reasons=[]))
    k.heartbeat = object(); k._start_owner_interface = lambda: None
    monkeypatch.setattr(stage5_activation, 'start', lambda *a: None)
    monkeypatch.setattr(kernel, 'start_stall_monitor', lambda *a, **k: None)
    monkeypatch.setattr(kernel.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(kernel.threading.Thread, 'start', lambda self: None)
    if fail:
        with pytest.raises(RuntimeError, match='fixture boot failure'): k.boot()
        assert k._boot_complete is False
    else:
        k.boot(); assert k._boot_complete is True


def test_actual_cycle_publishes_boot_and_stop_truth_after_statistics():
    tree = ast.parse((ROOT / 'trader/kernel.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Kernel')
    cycle = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'cycle')
    expression = next(n for n in cycle.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                      and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'beat')
    records = []
    for complete, stopping, control in ((False, False, 'FROZEN'), (True, False, 'FROZEN'),
                                       (True, True, 'FROZEN'), (True, False, 'ACTIVE')):
        obj = SimpleNamespace(_boot_complete=complete, _stop=stopping,
                              state_machine=SimpleNamespace(state=SimpleNamespace(value=control)),
                              heartbeat=SimpleNamespace(beat=lambda value: records.append(value)))
        namespace = dict(self=obj, balance=1, state=SimpleNamespace(value='FROZEN'),
                         time=SimpleNamespace(time=lambda: 100.), stats={'boot_complete': 'false-stat', 'stopping': 'false-stat', 'state': 'false-stat'})
        exec(compile(ast.Module(body=[expression], type_ignores=[]), '<actual-cycle-heartbeat>', 'exec'), namespace)
        assert records[-1]['boot_complete'] is complete and records[-1]['stopping'] is stopping
        assert records[-1]['state'] == control


@pytest.mark.parametrize('duplicate', [False, True])
def test_run01_kernel_main_binds_identity_before_run_and_refuses_duplicate(ready, monkeypatch, duplicate):
    from trader import kernel
    monkeypatch.setattr(sys, 'argv', ['fixture-kernel', '--bootstrap-token', 'fixture', '--expect-revision', REV])
    from trader.observability import bootstrap
    # OBS-05 separately proves admission; this isolates RUN-01 main wiring.
    monkeypatch.setattr(bootstrap, 'child_admission', lambda root, token, revision: None)
    monkeypatch.setattr(kernel, 'load_config', lambda *a: {})
    monkeypatch.setattr(kernel, 'setup_logging', lambda *a: None)
    real_class = ri.KernelInstance
    monkeypatch.setattr(ri, 'KernelInstance', lambda: real_class(ready.root))
    events = []
    def build(cfg):
        events.append('construct')
        def run():
            state = ri.inspect(ready.root)
            assert state['verified'] and state['record']['heartbeat_instance_id'] == 'fixture-run-hb'
            events.append('run')
        return SimpleNamespace(heartbeat=SimpleNamespace(instance_id='fixture-run-hb'), run=run)
    monkeypatch.setattr(kernel, 'Kernel', build)
    if duplicate:
        with pytest.raises(SystemExit) as exc: kernel.main()
        assert exc.value.code == ri.EXIT_ALREADY_RUNNING and events == []
    else:
        ready.instance.release(); kernel.main()
        assert events == ['construct', 'run'] and ri.inspect(ready.root)['state'] == 'NONE'


def test_healthy_direct_server_path_is_preserved(ready, monkeypatch):
    from trader.dashboard import server
    import uvicorn
    events = []; result = check(ready); assert result['allow']
    monkeypatch.setattr(server, 'ROOT', ready.root)
    monkeypatch.setattr(server, 'load_config', lambda: CFG)
    monkeypatch.setattr(gate, 'check', lambda *a, **k: events.append('gate') or result)
    monkeypatch.setattr(server, 'create_app', lambda *a: events.append('app') or 'fixture-app')
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **k: events.append('listen'))
    server.main(); assert events == ['gate', 'app', 'listen']


@pytest.mark.parametrize('allow', [False, True])
def test_prepared_launcher_gates_even_direct_start_calls(ready, monkeypatch, allow):
    monkeypatch.syspath_prepend(str(SOURCE))
    spec = importlib.util.spec_from_file_location('obs02_prepared', SOURCE / 'launch_once.py')
    launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
    monkeypatch.setattr(launcher, 'P', ready.root)
    host = launcher.Host(); host.directory = ready.root
    host.pids = {'kernel': ready.instance.record.copy()}
    if not allow: mutate(ready, 'blocking_health')
    # Config values are fixture-only; bind the check's clock to the fixture.
    original = gate.check
    monkeypatch.setattr(gate, 'check', lambda root, cfg, **k: original(root, cfg, clock=lambda: ready.now, **k))
    launched = []
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *a, **k:
                        launched.append(a) or SimpleNamespace(pid=os.getpid()))
    if allow:
        host.start('dashboard'); assert len(launched) == 1
    else:
        with pytest.raises(RuntimeError, match='not_ready'): host.start('dashboard')
        assert not launched


@pytest.mark.parametrize('preflight,readiness', [(False, True), (True, False), (True, True)])
def test_actual_restart_orders_both_gates_before_process_effects(ready, tmp_path, preflight, readiness):
    if not readiness: mutate(ready, 'blocking_health')
    binpath = tmp_path / 'bin'; binpath.mkdir()
    venv = tmp_path / 'venv/bin'; venv.mkdir(parents=True)
    script = tmp_path / 'restart.sh'
    script.write_text((ROOT / 'restart.sh').read_text().replace('/tmp/opencode', str(tmp_path)))
    events = tmp_path / 'events'
    shim = venv / 'python'
    shim.write_text('#!' + sys.executable + '\n' + '''import json,os,sys
with open(os.environ['OBS02_EVENTS'],'a') as out: out.write(sys.argv[2]+'\\n')
if sys.argv[2].endswith('dashboard_readiness'):
    sys.path.insert(0, os.environ['OBS02_REPO'])
    from trader import runtime_identity as ri
    from trader.observability import dashboard_readiness as gate
    ri.code_revision = lambda root: dict(revision='a'*40, dirty_code=False)
    original = gate.check
    gate.check = lambda root,cfg: original(root,cfg,clock=lambda: float(os.environ['OBS02_NOW']))
    raise SystemExit(gate.main(sys.argv[3:]))
allow = os.environ['OBS02_PRE'] == '1'
print(json.dumps(dict(allow=allow)))
raise SystemExit(0 if allow else 1)
'''); shim.chmod(0o755)
    for name in ('pgrep', 'readlink', 'sleep', 'mkdir', 'setsid'):
        path = binpath / name
        path.write_text('#!/bin/bash\necho ' + name + ' >> "$OBS02_EVENTS"\n'); path.chmod(0o755)
    result = subprocess.run(['bash', str(script), 'dashboard'], capture_output=True, text=True, timeout=10,
        env={**os.environ, 'PATH': str(binpath)+':'+os.environ['PATH'], 'OBS02_EVENTS': str(events),
             'OBS02_PRE': str(int(preflight)), 'OBS02_REPO': str(ROOT), 'OBS02_NOW': str(ready.now)})
    entries = events.read_text().splitlines()
    assert entries[0] == 'trader.observability.preflight'
    if not preflight: assert entries == ['trader.observability.preflight']
    elif not readiness: assert entries == ['trader.observability.preflight', 'trader.observability.dashboard_readiness']
    else:
        import time
        for _ in range(100):
            if 'setsid' in events.read_text(): break
            time.sleep(.01)
        assert events.read_text().splitlines()[:2] == ['trader.observability.preflight', 'trader.observability.dashboard_readiness']
        assert 'setsid' in events.read_text()
    assert (result.returncode == 0) == (preflight and readiness)


def test_retained_collector_unknown_health_was_not_a_launch_barrier(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE))
    import collect_prepared
    state = collect_prepared.State(71); state.boot = True; state.instance = 'current'; state.started_at = 100.
    beat = dict(instance_id='current', started_at=100., timestamp=101., sequence=0,
                context={'last_successful_cycle_at':101.})
    health = dict(schema='luffy-safety-health.v1', recovery_required=True,
                  conditions={'journal_write': {'status':'UNKNOWN'}})
    facts = dict(controls={'control_state':'FROZEN','macro_guard_operator_hold':'1'}, health=health,
                 beat=beat, watchdog_off=True)
    assert state.check(facts, None, 101., 101.)  # exact retained checker accepted invalid sequence/UNKNOWN.


def test_original_launcher_selected_dashboard_without_boot_evidence(tmp_path):
    path = ROOT / 'docs/tracker/evidence/obs04-shutdown-sol/source/original-unsafe-launcher.py'
    tree = ast.parse(path.read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    launch = next(n for n in main.body if isinstance(n, ast.Try))
    commands = next(n for n in launch.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'commands' for t in n.targets))
    assert [key.value for key in commands.value.keys] == ['kernel', 'dashboard']
    loop = next(n for n in launch.body if isinstance(n, ast.For) and isinstance(n.target, ast.Tuple)
                and [t.id for t in n.target.elts] == ['role', 'command'])
    assert isinstance(loop.iter, ast.Call) and isinstance(loop.iter.func, ast.Attribute) and loop.iter.func.attr == 'items'
    launched = []
    inert = SimpleNamespace(Popen=lambda command, **kwargs:
                            launched.append(command) or SimpleNamespace(pid=os.getpid()), STDOUT=None)
    namespace = dict(python='fixture-python', here=tmp_path, run=tmp_path, Path=Path,
                     processes={}, pids={}, production=tmp_path, subprocess=inert, os=os)
    exec(compile(ast.Module(body=[commands, loop], type_ignores=[]), '<original-launch-loop>', 'exec'), namespace)
    assert len(launched) == 2 and 'trader.dashboard.server' in launched[1]


def test_run01_groundwork_is_reused_verbatim():
    expected = subprocess.check_output(['git', 'show', '0ecaad8:trader/runtime_identity.py'], cwd=ROOT)
    assert Path(ri.__file__).read_bytes() == expected


def test_absent_identity_refuses_without_creating_runtime_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, 'inspect', lambda *a: pytest.fail('identity inspection before record exists'))
    assert not gate.check(tmp_path, CFG)['allow']
    assert not (tmp_path / 'data').exists()
