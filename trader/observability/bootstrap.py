"""OBS-05 contained cold startup. Phase A admits observation, never entries.

Observers are independent transient user timers (the OBS-04 retention pattern).
One owned child is launched; failures retain timers and the durable entry fence.
No retry, ACTIVE transition, recovery-latch clear, SIGKILL or venue acquisition.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import threading
import time
from uuid import uuid4

from trader import runtime_identity as ri
from . import dashboard_readiness, preflight
from .safety import SafetyHealth, publish
from .shutdown import service_state

STARTUP_TIMEOUT_S = 240.0  # existing observed-cycle envelope, not extended
STOP_TIMEOUT_S = 300.0    # RUN-01 graceful stop envelope


def start_protection_monitor(kernel):
    """Wire the existing bounded read-only producer to this admitted boot."""
    from trader.engine.protection_snapshot import make_monitor
    monitor = make_monitor(kernel.exchange, kernel.journal.db_path,
                           supervisor_busy=kernel.supervisor._pass_lock.locked)
    kernel._bootstrap_protection_monitor = monitor
    thread = threading.Thread(target=monitor.loop, kwargs={'stopped': lambda: kernel._stop},
                              name='bootstrap-protection', daemon=True)
    thread.start()
    return monitor


def record_fence(root, token, status, revision, *, expected_health=None):
    health = SafetyHealth(Path(root) / 'data/safety_health.json')
    with health.locked():
        value = preflight._health(health.path)
        if expected_health is not None and value != expected_health:
            raise ValueError('health_changed_before_ready_publication')
        if value['recovery_required'] is not True:
            raise ValueError('recovery_latch_not_retained')
        if status != 'PENDING' and value.get('bootstrap', {}).get('token') != token:
            raise ValueError('bootstrap_attempt_changed')
        value['bootstrap'] = dict(token=token, status=status, intended_revision=revision)
        value['revision'] += 1
        publish(health.path, value)


def child_admission(root, token, revision):
    """Called before Kernel construction, after controller admission and fence."""
    result = preflight.prelaunch(root, revision)
    if not result['allow']:
        raise ValueError('child_prelaunch_refused')
    value = preflight._health(Path(root) / 'data/safety_health.json')
    if value.get('bootstrap') != dict(token=token, status='PENDING', intended_revision=revision):
        raise ValueError('bootstrap_fence_missing_or_changed')


def readiness(root, cfg, expected, baseline, *, clock=time.time):
    """Phase B consumes OBS-02 plus fresh post-launch protection/reconciliation.

    Successful contained readiness leaves FROZEN, hold and recovery unchanged.
    It supplies no trading authority and is not an owner recovery approval.
    """
    result = dashboard_readiness.check(root, cfg, expected_identity=expected, clock=clock)
    reasons = list(result['reasons'])
    try:
        health = preflight._health(Path(root) / 'data/safety_health.json')
        if health['recovery_required'] is not True:
            raise ValueError('recovery_latch_not_retained')
        with closing(sqlite3.connect((Path(root) / 'data/luffy.db').resolve().as_uri() + '?mode=ro',
                                     uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            protection = preflight._protection(db, clock())
        at = datetime.fromisoformat(protection['checked_at']).timestamp()
        if (at < expected['started_at']
                or protection['generation']['boot'] <= baseline['generation']['boot']):
            raise ValueError('protection_prior_boot')
        result['facts']['protection_reconciliation'] = protection
    except Exception as exc:
        reasons.append('fresh_protection_reconciliation_failed:' + type(exc).__name__)
    return dict(result, phase='CONTAINED_BOOT_READINESS', trading_authority=False,
                allow=not reasons, result='FAIL' if reasons else 'PASS', reasons=reasons)


class Host:
    def __init__(self, root, revision):
        self.root = Path(root).resolve()
        self.revision = revision
        self.token = uuid4().hex
        self.units = []
        self.process = None
        self.expected = None
        self.directory = self.root / 'data/bootstrap' / self.token
        self.python = str(self.root / 'venv/bin/python')

    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def prelaunch(self):
        return preflight.prelaunch(self.root, self.revision)

    def fence(self, status):
        record_fence(self.root, self.token, status, self.revision)

    def ready(self, phase_b):
        record_fence(self.root, self.token, 'READY', self.revision,
                     expected_health=phase_b['facts']['health'])

    def observers_start(self):
        # Record each unit BEFORE its creation, so partial setup is retained.
        self.directory.mkdir(parents=True)
        for mode, seconds in [('heartbeat-only', 10), ('safety-only', 30)]:
            unit = 'luffy-bootstrap-' + self.token + '-' + mode
            self.units.append(unit)
            publish(self.directory / 'observers.json', dict(units=self.units, cleanup='OWNER_ONLY'))
            subprocess.run(['systemd-run', '--user', '--unit=' + unit, '--on-active=1s',
                '--on-unit-inactive=' + str(seconds) + 's', '--timer-property=AccuracySec=1s',
                '--property=WorkingDirectory=' + str(self.root), '--property=Restart=no',
                '--property=SuccessExitStatus=1', '--property=TimeoutStartSec=20s',
                self.python, '-B', '-m', 'scripts.monitor', '--' + mode], check=True, timeout=30)

    def start(self):
        if self.process is not None:
            raise RuntimeError('one_launch_only')
        command = [self.python, '-B', '-m', 'trader.kernel', '--expect-revision', self.revision,
                   '--bootstrap-token', self.token]
        self.process = subprocess.Popen(command, cwd=self.root, start_new_session=True)
        self.expected = dict(pid=self.process.pid, start_ticks=ri.proc_start_ticks(self.process.pid),
                             started_at=time.time())
        publish(self.directory / 'child.json', self.expected)

    def sample(self, baseline):
        from trader.core.config import load_config
        # Bind to owned child PID/ticks, then require its lock record and revision.
        if service_state(self.process, self.expected) != 'LIVE':
            return dict(allow=False, reasons=['owned_child_not_live'])
        expected = dict(self.expected)
        record = (ri.inspect(self.root).get('record') or {})
        # Publication follows process creation: protection must start after the
        # identity record, never merely after this controller's Popen timestamp.
        expected['started_at'] = max(expected['started_at'], record.get('started_at', float('inf')))
        return readiness(self.root, load_config(str(self.root / 'config.yaml')), expected, baseline)

    def contain_stop(self):
        if self.process is None:
            return dict(stop='NOT_LAUNCHED', observers_retained=True)
        state = service_state(self.process, self.expected)
        if state == 'LIVE':
            try:
                self.process.send_signal(signal.SIGTERM)  # owned child identity verified
            except ProcessLookupError:
                pass  # signal delivery is never exit confirmation
        deadline = self.monotonic() + STOP_TIMEOUT_S
        while self.monotonic() < deadline and service_state(self.process, self.expected) != 'TERMINATED':
            self.sleep(1)
        state = service_state(self.process, self.expected)
        return dict(stop=state, child=self.expected, observers_retained=True)

    def save(self, result):
        self.directory.mkdir(parents=True, exist_ok=True)
        publish(self.directory / 'result.json', result)


def execute(host, *, timeout=STARTUP_TIMEOUT_S):
    """Single attempt; inert host fixtures exercise every lifecycle branch."""
    result = dict(allow=False, phase='PRE_LAUNCH', trading_authority=False)
    started = False
    try:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= STARTUP_TIMEOUT_S:
            raise ValueError('startup_deadline_invalid')
        phase_a = host.prelaunch()
        result['prelaunch'] = phase_a
        if phase_a.get('allow') is not True:
            return result
        host.fence('PENDING')  # durable entry/recovery fence before observers or child
        host.observers_start()
        deadline = host.monotonic() + timeout
        started = True  # a partial/exceptional Popen may still own a child
        host.start()
        while host.monotonic() < deadline:
            phase_b = host.sample(phase_a['facts']['protection'])
            result['readiness'] = phase_b
            if host.monotonic() < deadline and phase_b.get('allow') is True:
                host.ready(phase_b)
                result.update(allow=True, phase='CONTAINED_READY', observers_retained=True)
                return result
            host.sleep(1)
        result['reason'] = 'contained_startup_deadline_exceeded'
    except BaseException as exc:
        result['reason'] = 'contained_startup_failed:' + type(exc).__name__
    finally:
        if result['allow'] is not True and result.get('prelaunch', {}).get('allow') is True:
            try:
                host.fence('FAILED')
            except Exception:
                result['fence_failure'] = True  # existing PENDING fence still refuses
            if started:
                try:
                    result['containment'] = host.contain_stop()
                except Exception as exc:
                    result['containment'] = dict(stop='UNKNOWN', observers_retained=True,
                                               error=type(exc).__name__)
            result['observers_retained'] = True
        host.save(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--expect-revision', required=True)
    args = parser.parse_args(argv)
    host = Host(args.root, args.expect_revision)
    # Serialize controllers too; a failed attempt never loops back to start.
    path = args.root / 'data/bootstrap-controller.lock'
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps(dict(allow=False, reason='bootstrap_controller_already_running')))
            return 3
        result = execute(host)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['allow'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
