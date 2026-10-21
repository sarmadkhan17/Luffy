"""Installation-local commands; retain the existing startup and recovery gates."""
import argparse
import fcntl
import importlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def refresh_protection(root=None):
    """Refresh expired venue evidence under the stopped Kernel's exclusive lock."""
    from trader.core.config import load_config
    from trader.runtime_identity import legacy_kernel_pids
    root = ROOT if root is None else Path(root)
    cfg = load_config(str(root / 'config.yaml'))
    path = root / 'data/luffy.db'
    # No state transition, recovery clear or runtime identity publication here.
    with (root / 'data/kernel.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if legacy_kernel_pids():
            raise ValueError('kernel_process_present')
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            state = dict(db.execute("SELECT key,value FROM state_kv WHERE key IN "
                                    "('control_state','macro_guard_operator_hold','market_type')"))
        finally:
            db.close()
        if state.get('control_state') != 'FROZEN' or state.get('macro_guard_operator_hold') != '1':
            raise ValueError('stopped_operator_containment_required')
        from trader.data.feed import make_exchange
        from trader.core.types import MarketType
        from trader.engine.protection_snapshot import make_monitor
        market = MarketType(state.get('market_type', cfg['mode']['market'])).value
        exchange = make_exchange(market)
        snapshot = make_monitor(exchange, path, supervisor_busy=lambda: False).run_once()
        return {'allow': bool(snapshot and snapshot.get('status') == 'VERIFIED'
                              and snapshot.get('control_state_observed') == 'FROZEN'
                              and snapshot.get('mutations') == 0),
                'snapshot': snapshot, 'trading_authority': False,
                'scope': 'existing read-only venue observation; startup gates still required'}


def verify(root=ROOT, *, sqlite=False):
    from trader.core.config import load_config
    from trader.runtime_identity import code_revision
    from trader.owner.ipc import load_key, resolve_ipc_dir
    from trader.observability.preflight import _filesystem, RECOVERY_ROOT
    issues = []
    cfg = load_config(str(root / 'config.yaml'))
    revision = code_revision(root)
    if revision.get('dirty_code') or not (root / '.git').is_dir():
        issues.append('self_contained_clean_git_required')
    external = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')]
        for name in dirs + files:
            path = Path(parent, name)
            if not path.is_symlink():
                continue
            target = path.resolve()
            # The copied venv uses the compatible host's system interpreter.
            if target.is_relative_to(root):
                if not target.exists(): issues.append('broken_link:' + str(path.relative_to(root)))
            elif path.parent == root / 'venv/bin' and name.startswith('python') and target.is_relative_to('/usr'):
                continue
            else:
                external.append(str(path.relative_to(root)))
    if external:
        issues.append('external_application_symlinks')
    modules = {}
    for module in ('trader.kernel', 'trader.dashboard.server', 'trader.learning.application',
                   'trader.research', 'scripts.monitor'):
        try:
            loaded = importlib.import_module(module)
            modules[module] = str(Path(loaded.__file__).relative_to(root))
        except Exception as exc:
            issues.append('import:' + module + ':' + type(exc).__name__)
    try:
        ipc = resolve_ipc_dir(cfg['owner_interface']['ipc_dir'])
        if ipc is None: raise ValueError('ipc_not_configured')
        ipc = (root / ipc).resolve()
        if not ipc.is_relative_to(root): raise ValueError('ipc_outside_installation')
        for channel in ('cli', 'dashboard'):
            load_key(ipc, channel)
    except Exception as exc:
        issues.append('owner_ipc:' + type(exc).__name__)
    try:
        recovery = Path(cfg.get('deployment', {}).get('recovery_root', RECOVERY_ROOT))
        _filesystem(root, root / recovery)
    except Exception as exc:
        issues.append('recovery:' + str(exc))
    stores = {}
    for name in ('luffy.db', 'candles.db', 'derivs.db'):
        try:
            path = root / 'data' / name
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5) as db:
                if sqlite:
                    result = [r[0] for r in db.execute('PRAGMA quick_check')]
                    if result != ['ok']: raise ValueError('sqlite_consistency_failed')
                else:
                    db.execute('SELECT count(*) FROM sqlite_master').fetchone()
            stores[name] = 'QUICK_CHECK_OK' if sqlite else 'READABLE'
        except Exception as exc:
            issues.append('database:' + name + ':' + type(exc).__name__)
    return dict(allow=not issues, revision=revision, issues=issues,
                external_links=external, imports=modules, stores=stores,
                scope='local closure and prerequisites; not a runtime portability certification')


def freeze_for_shutdown():
    """Use the normal authenticated owner route; no offline state fallback."""
    from trader.core.config import load_config, Env
    cfg = load_config()['dashboard']
    host = cfg['host']
    if host in ('0.0.0.0', '::'):
        host = '127.0.0.1'
    request_id = uuid.uuid4().hex
    query = ('mutation($r:String!,$t:Float!){res:owner_control(operation:"freeze",'
             'request_id:$r,issued_at_ms:$t){request_id status disposition '
             'control_state_after reasons}}')
    request = urllib.request.Request(
        'http://' + host + ':' + str(cfg['port']) + '/graphql',
        data=json.dumps({'query': query, 'variables': {
            'r': request_id, 't': time.time() * 1000}}).encode(),
        headers={'Content-Type': 'application/json', 'X-Luffy-Token': Env.get('DASH_TOKEN')})
    try:
        with urllib.request.urlopen(request, timeout=150) as response:
            result = json.loads(response.read())['data']['res']
        print(json.dumps({'shutdown_freeze': result}))
        return (result['status'] in ('ACCEPTED', 'ALREADY_SET')
                and result['control_state_after'] == 'FROZEN')
    except Exception as exc:
        print(json.dumps({'shutdown_freeze': 'UNCONFIRMED', 'request_id': request_id,
                          'error_class': type(exc).__name__, 'retry': False}))
        return False


def stop(timeout):
    from trader.runtime_identity import stop_kernel, proc_start_ticks, inspect
    (ROOT / 'data/watchdog.off').touch()
    identity = inspect(ROOT)
    if identity.get('state') == 'RUNNING' and identity.get('verified'):
        if not freeze_for_shutdown():
            return 5
    result = stop_kernel(ROOT, timeout=timeout)
    if result:
        return result
    # Stop only this installation's Dashboard, never another checkout's process.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit(): continue
        try:
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
            if b'trader.dashboard.server' not in argv or (proc / 'cwd').resolve() != ROOT:
                continue
            pid = int(proc.name); ticks = proc_start_ticks(pid)
            if ticks is None: continue
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 30
            while proc_start_ticks(pid) == ticks and time.monotonic() < deadline:
                time.sleep(.25)
            if proc_start_ticks(pid) == ticks:
                return 4
        except (FileNotFoundError, ProcessLookupError):
            continue
    # Independent observers remain available after shutdown, by design.
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'status', 'refresh-protection', 'start-kernel', 'start-dashboard', 'stop'])
    parser.add_argument('--sqlite', action='store_true', help='full SQLite quick_check (may be slow)')
    parser.add_argument('--timeout', type=float, default=1200)
    args = parser.parse_args()
    if args.command == 'refresh-protection':
        try:
            report = refresh_protection()
        except Exception as exc:
            report = {'allow': False, 'error_class': type(exc).__name__,
                      'trading_authority': False}
            if isinstance(exc, BlockingIOError):
                report['reason'] = 'kernel_lock_unavailable'
            elif isinstance(exc, ValueError) and str(exc) in (
                    'kernel_process_present', 'stopped_operator_containment_required'):
                report['reason'] = str(exc)
        print(json.dumps(report, indent=2)); return 0 if report['allow'] else 1
    if args.command == 'verify':
        report = verify(sqlite=args.sqlite)
        print(json.dumps(report, indent=2)); return 0 if report['allow'] else 1
    if args.command == 'status':
        from trader.runtime_identity import inspect
        print(json.dumps(inspect(ROOT), indent=2)); return 0
    if args.command == 'stop':
        return stop(args.timeout)
    from trader.runtime_identity import code_revision
    env = dict(os.environ, LUFFY_EXPECT_REVISION=code_revision(ROOT)['revision'])
    return subprocess.call([str(ROOT / 'restart.sh'), args.command.removeprefix('start-')], cwd=ROOT, env=env)


if __name__ == '__main__':
    sys.exit(main())
