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
import re
import signal
import sqlite3
import threading
import time
from uuid import uuid4

from trader import runtime_identity as ri
from . import dashboard_readiness, managed, preflight
from .safety import SafetyHealth, publish
from .shutdown import service_state

STARTUP_TIMEOUT_S = 240.0  # initialized, protected FROZEN startup; not cycle completion
STOP_TIMEOUT_S = 300.0    # RUN-01 graceful stop envelope

# Only known local validation codes may cross the diagnostic boundary.
_READINESS_FAILURE_CODES = frozenset({
    'duplicate_json_key', 'nonfinite_json', 'safety_health_malformed',
    'safety_condition_malformed', 'recovery_latch_not_retained',
    'local_execution_ledger_not_clear', 'protection_missing', 'protection_unverified',
    'protection_clock_unknown', 'protection_stale_or_future',
    'protection_orders_or_coverage_uncertain', 'protection_coverage_malformed',
    'protection_position_uncertain', 'protection_current_book_changed',
    'protection_prior_boot',
})


def start_protection_monitor(kernel):
    """Wire the existing bounded read-only producer to this admitted boot."""
    from trader.engine.protection_snapshot import make_monitor
    monitor = make_monitor(kernel.exchange, kernel.journal.db_path,
                           supervisor_busy=kernel.supervisor._pass_lock.locked,
                           interval_s=30.0 if getattr(kernel, "_use_split_cycles", False) else 60.0)
    kernel._bootstrap_protection_monitor = monitor
    thread = threading.Thread(target=monitor.loop, kwargs={'stopped': lambda: kernel._stop,
        'start_delay_s': 10.0 if getattr(kernel, '_use_split_cycles', False) else 0.0},
                              name='bootstrap-protection', daemon=True)
    thread.start()
    return monitor


def record_fence(root, token, status, revision, *, expected_health=None):
    health = SafetyHealth(Path(root) / 'data/safety_health.json')
    with health.locked():
        value = preflight._health(health.path)
        if expected_health is not None and value != expected_health:
            # An observer may publish between readiness and this existing lock.
            # Revalidate its latest state, never approve from a stale snapshot.
            from trader.core.config import load_config
            try:
                ri.check_revision(revision, Path(root))
                latest = dashboard_readiness.check(root, load_config(str(Path(root) / 'config.yaml')),
                                                   require_cycle=status == 'READY')
            except Exception:
                raise ValueError('health_changed_before_ready_publication') from None
            if (not latest['allow'] or not dashboard_readiness._health_progression(
                    expected_health, latest['facts']['health'])):
                raise ValueError('health_changed_before_ready_publication')
            value = latest['facts']['health']
        if value['recovery_required'] is not True:
            raise ValueError('recovery_latch_not_retained')
        if status != 'PENDING' and value.get('bootstrap', {}).get('token') != token:
            raise ValueError('bootstrap_attempt_changed')
        value['bootstrap'] = dict(token=token, status=status, intended_revision=revision)
        value['revision'] += 1
        publish(health.path, value)


class ChildPrelaunchRefused(ValueError):
    def __init__(self, result):
        super().__init__('child_prelaunch_refused')
        # Only the preflight's existing sanitized codes cross into stderr.
        self.reason_codes = result['reasons']


def child_admission(root, token, revision):
    """Called before Kernel construction, after controller admission and fence."""
    result = preflight.prelaunch(root, revision)
    if not result['allow']:
        raise ChildPrelaunchRefused(result)
    value = preflight._health(Path(root) / 'data/safety_health.json')
    if value.get('bootstrap') != dict(token=token, status='PENDING', intended_revision=revision):
        raise ValueError('bootstrap_fence_missing_or_changed')


def readiness(root, cfg, expected, baseline, *, clock=time.time, require_cycle=True):
    """Use existing readiness facts and fresh post-launch protection.

    Startup checks process liveness; the default also requires a genuine cycle.
    Both leave FROZEN, hold and recovery unchanged.
    It supplies no trading authority and is not an owner recovery approval.
    """
    result = dashboard_readiness.check(root, cfg, expected_identity=expected, clock=clock,
                                       require_cycle=require_cycle)
    reasons = list(result['reasons'])
    protection_diagnostics = {}
    try:
        health = preflight._health(Path(root) / 'data/safety_health.json')
        if health['recovery_required'] is not True:
            raise ValueError('recovery_latch_not_retained')
        with closing(sqlite3.connect((Path(root) / 'data/luffy.db').resolve().as_uri() + '?mode=ro',
                                     uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            if not require_cycle:
                preflight._journal_zero(db)
            protection = preflight._protection(db, clock(), diagnostics=protection_diagnostics)
        at = datetime.fromisoformat(protection['checked_at']).timestamp()
        if (at < expected['started_at']
                or protection['generation']['boot'] <= baseline['generation']['boot']):
            raise ValueError('protection_prior_boot')
        result['facts']['protection_reconciliation'] = protection
    except Exception as exc:
        reasons.append('fresh_protection_reconciliation_failed:' + type(exc).__name__)
        code = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], str) else None
        result['facts']['protection_failure'] = {
            'error_class': type(exc).__name__,
            'reason_code': code if code in _READINESS_FAILURE_CODES else 'unclassified',
            'receipt': protection_diagnostics,
        }
    return dict(result, phase='CONTAINED_BOOT_READINESS' if require_cycle else 'STARTED_FROZEN_CHECK', trading_authority=False,
                allow=not reasons, result='FAIL' if reasons else 'PASS', reasons=reasons)


OBSERVERS = (('heartbeat-only', 10), ('safety-only', 30))
_TOKEN = re.compile(r'^[0-9a-f]{32}$')


class ObserverCoverageIncomplete(RuntimeError):
    pass


class Host:
    def __init__(self, root, revision, manager=None, proc_root=Path('/proc')):
        self.root = Path(root).resolve()
        self.revision = revision
        self.token = uuid4().hex
        self.units = []
        self.process = None          # verified managed main process, never a launcher PID
        self.unit = None
        self.expected = None
        self.manager = manager or managed.UserManager()
        self.proc_root = Path(proc_root)
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

    def started_frozen(self, phase_b):
        record_fence(self.root, self.token, 'STARTED_FROZEN', self.revision,
                     expected_health=phase_b['facts']['health'])

    def _manifest(self, status, **extra):
        publish(self.directory / 'observers.json', dict(
            units=self.units, cleanup='OWNER_ONLY', root=str(self.root),
            revision=self.revision, token=self.token, status=status, **extra))

    def observers_start(self):
        """Create this generation's pair, verify it covers both modes, and only
        then retire exact superseded pairs. A failure at any step leaves every
        earlier pair untouched, so detection coverage never has a gap."""
        self.directory.mkdir(parents=True)
        for mode, seconds in OBSERVERS:
            # Record each unit BEFORE its creation, so partial setup is retained.
            self.units.append('luffy-bootstrap-' + self.token + '-' + mode)
            self._manifest('PENDING')
            self.manager.run_timer(self.units[-1], [self.python, '-B', '-m', 'scripts.monitor', '--' + mode],
                                   cwd=self.root, period_s=seconds, env={'TMPDIR': str(self.root / 'tmp')})
        coverage = {}
        for (mode, seconds), unit in zip(OBSERVERS, self.units):
            # Exact timer + service identity (root, command), not merely "active".
            timer, service, problem = self._observer_facts(unit, mode)
            if (problem or timer.get('LoadState') != 'loaded' or timer.get('ActiveState') != 'active'
                    or service.get('LoadState') != 'loaded'):
                self._manifest('PENDING', coverage=coverage)
                raise ObserverCoverageIncomplete(mode)
            coverage[mode] = dict(unit=unit, period_s=seconds, timer='active',
                                  root=str(self.root), command=self._observer_argv(mode))
        self._manifest('COVERED', coverage=coverage)
        self._manifest('COVERED', coverage=coverage, superseded=self.retire_superseded(coverage))

    def retire_superseded(self, coverage):
        """Stop only manifest-listed, correctly named, root-verified older pairs,
        and only for modes this generation's verified timers now cover."""
        results = []
        base = self.root / 'data/bootstrap'
        for old in sorted(base.iterdir()) if base.is_dir() else []:
            if old.name == self.token or not _TOKEN.match(old.name) or not old.is_dir():
                continue
            try:
                manifest = json.loads((old / 'observers.json').read_text())
                units = manifest['units']
                if manifest['cleanup'] != 'OWNER_ONLY' or not isinstance(units, list):
                    raise ValueError
            except (OSError, ValueError, KeyError, TypeError):
                results.append(dict(generation=old.name, result='manifest_unusable'))
                continue
            if manifest.get('root', str(self.root)) != str(self.root):
                results.append(dict(generation=old.name, result='foreign_root'))
                continue
            for unit in units:
                mode = next((m for m, _s in OBSERVERS if unit == 'luffy-bootstrap-%s-%s' % (old.name, m)), None)
                if mode is None:
                    results.append(dict(unit=str(unit), result='not_owned_by_manifest'))
                elif mode not in coverage:
                    results.append(dict(unit=unit, result='replacement_not_covering'))
                else:
                    results.append(dict(unit=unit, result=self._retire(unit, mode)))
        return results

    def _observer_argv(self, mode):
        return [self.python, '-B', '-m', 'scripts.monitor', '--' + mode]

    def _observer_facts(self, unit, mode):
        """(timer props, service props, problem|None): the one verification used
        for both a replacement's coverage and a superseded unit's ownership."""
        service = self.manager.show(unit + '.service', 'Id', 'LoadState', 'WorkingDirectory', 'ExecStart')
        timer = self.manager.show(unit + '.timer', 'Id', 'LoadState', 'ActiveState')
        argv = re.search(r'argv\[\]=([^;]*);', service.get('ExecStart', ''))
        problem = None
        if (timer.get('Id') != unit + '.timer' or service.get('Id') != unit + '.service'
                or service.get('WorkingDirectory') != str(self.root)
                or argv is None or argv.group(1).split() != self._observer_argv(mode)):
            problem = 'identity_unverified'
        return timer, service, problem

    def _retire(self, unit, mode):
        try:
            timer, service, problem = self._observer_facts(unit, mode)
            if timer.get('LoadState') == 'not-found' and service.get('LoadState') == 'not-found':
                return 'already_absent'
            if problem:
                return 'identity_unverified_retained'
            self.manager.stop(unit + '.timer')    # no further invocations first
            if self.manager.show(unit + '.service', 'ActiveState').get('ActiveState') in ('active', 'activating'):
                self.manager.stop(unit + '.service')
            states = [self.manager.show(unit + suffix, 'ActiveState').get('ActiveState')
                      for suffix in ('.timer', '.service')]
            return ('retired' if not {'active', 'activating', 'deactivating'} & set(states)
                    else 'stop_unconfirmed_retained')
        except managed.ManagedUnavailable as exc:
            return 'retained:' + str(exc)

    def start(self):
        if self.process is not None or self.unit is not None:
            raise RuntimeError('one_launch_only')
        command = [self.python, '-B', '-m', 'trader.kernel', '--expect-revision', self.revision,
                   '--bootstrap-token', self.token]
        self.unit = 'luffy-kernel-' + self.token
        # Raises (leaving self.unit set) when the manager is unavailable or the
        # unit's actual main process cannot be verified; never an unmanaged Popen.
        self.process = managed.launch(
            self.manager, self.unit, command, cwd=self.root, proc_root=self.proc_root,
            env={'TMPDIR': str(self.root / 'tmp')},
            properties=('StandardOutput=append:' + str(self.root / 'logs/kernel-unit.log'),
                        'StandardError=inherit'),
            monotonic=self.monotonic, sleep=self.sleep)
        self.expected = dict(pid=self.process.pid, start_ticks=self.process.start_ticks,
                             started_at=time.time(), unit=self.unit, cgroup=self.process.cgroup,
                             revision=self.revision, token=self.token)
        publish(self.directory / 'child.json', self.expected)

    def sample(self, baseline):
        from trader.core.config import load_config
        # Bind to the unit's actual main PID/ticks, then require its lock record.
        if service_state(self.process, self.expected) != 'LIVE':
            return dict(allow=False, fatal=True, reasons=['owned_child_not_live'])
        mixed = self.process.verify()
        if mixed:
            return dict(allow=False, fatal=True, reasons=['managed_identity_mixed'] + mixed)
        expected = dict(self.expected)
        identity = ri.inspect(self.root)
        record = identity.get('record') or {}
        if (identity.get('state') == 'RUNNING' and record.get('pid') != expected['pid']
                and ri.proc_start_ticks(record.get('pid', -1)) == record.get('start_ticks')):
            # A different live, verified Kernel holds the lock: not our unit.
            return dict(allow=False, fatal=True, reasons=['kernel_record_names_other_process'])
        # Publication follows process creation: protection must start after the
        # identity record, never merely after this controller's launch time.
        expected['started_at'] = max(expected['started_at'], record.get('started_at', float('inf')))
        cfg = load_config(str(self.root / 'config.yaml'))
        result = readiness(self.root, cfg, expected, baseline, require_cycle=False)
        result['cycle_ready'] = False
        if result['allow']:
            result['cycle_readiness'] = readiness(self.root, cfg, expected, baseline)
            result['cycle_ready'] = result['cycle_readiness']['allow']
        return result

    def contain_stop(self):
        if self.process is None and self.unit is None:
            return dict(stop='NOT_LAUNCHED', observers_retained=True)
        if self.process is None:
            # The job was accepted but its main process was never verified.
            # Adopt it only if it now matches exactly; absence needs proof.
            command = [self.python, '-B', '-m', 'trader.kernel', '--expect-revision',
                       self.revision, '--bootstrap-token', self.token]
            try:
                self.process = managed.adopt(self.manager, self.unit, proc_root=self.proc_root,
                                             expect_argv=command, timeout_s=5.0,
                                             monotonic=self.monotonic, sleep=self.sleep)
            except (managed.ManagedUnavailable, managed.ManagedIdentityMixed):
                absent = managed.unit_absent(self.manager, self.unit, self.token, self.proc_root)
                return dict(stop='TERMINATED' if absent else 'UNKNOWN', unit=self.unit,
                            observers_retained=True)
            self.expected = dict(pid=self.process.pid, start_ticks=self.process.start_ticks,
                                 started_at=time.time(), unit=self.unit)
        state = service_state(self.process, self.expected)
        if state == 'LIVE':
            try:
                self.process.send_signal(signal.SIGTERM)  # managed identity verified
            except (ProcessLookupError, managed.ManagedUnavailable, managed.ManagedIdentityMixed):
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
    startup_verified = False
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
        while startup_verified or host.monotonic() < deadline:
            phase_b = host.sample(phase_a['facts']['protection'])
            result['readiness'] = phase_b
            if phase_b.get('fatal') or (startup_verified and phase_b.get('allow') is not True):
                result.update(allow=False, reason='contained_startup_health_failed')
                break
            if not startup_verified and host.monotonic() < deadline and phase_b.get('allow') is True:
                host.started_frozen(phase_b)
                startup_verified = True
                result.update(allow=True, phase='STARTED_FROZEN', cycle_ready=False,
                              startup_verification=phase_b, observers_retained=True)
                host.save(result)
                continue  # sample the newly persisted STARTED_FROZEN fence
            if startup_verified and phase_b.get('cycle_ready') is True:
                host.ready(phase_b['cycle_readiness'])
                result.update(allow=True, phase='CYCLE_READY', cycle_ready=True, observers_retained=True)
                return result
            host.sleep(1)
        result.update(allow=False)
        result.setdefault('reason', 'contained_startup_deadline_exceeded')
    except BaseException as exc:
        result.update(allow=False, reason='contained_startup_failed:' + type(exc).__name__)
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
