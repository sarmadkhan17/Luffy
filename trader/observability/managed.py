"""Managed-process adapter (SP-05) and user-manager transport (SP-04).

A transient user-manager unit owns a workload's lifetime, so the launcher's
exit (``systemd-run`` returns once a job is *accepted*) is never identity,
readiness or termination. Identity is the unit's actual MainPID plus its /proc
start ticks, control group and exact argv; cessation is the unit being no
longer active AND that PID/ticks being gone. Unavailable manager, mixed or
unreadable evidence is refused or UNKNOWN. There is no unmanaged fallback, no
SIGKILL (graceful SIGTERM to the unit's main process only) and no shell.

Every manager interaction goes through ``UserManager``; tests inject a fake
``runner``. Nothing here touches Journal, the venue or the network.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

READY_TIMEOUT_S = 30.0
MANAGER_TIMEOUT_S = 30.0
SERVICE_PROPERTIES = ('Restart=no', 'SendSIGKILL=no')   # RUN-01: graceful stop only

# Standalone lifetimes. Research is the bounded `closure scheduled` CLI; its
# receipt/wait API is below (launch_role / wait_role / release_role).
ROLES = {
    'dashboard': ['-m', 'trader.dashboard.server'],
    'research': ['-B', '-m', 'trader.research.closure', 'scheduled'],
}


class ManagedUnavailable(RuntimeError):
    """The manager cannot establish or confirm the requested fact."""


class ManagedIdentityMixed(RuntimeError):
    """The unit's actual process contradicts the identity that was expected."""


def _unit_name(unit: str) -> str:
    return unit if '.' in unit else unit + '.service'


class UserManager:
    """Thin ``systemd-run --user`` / ``systemctl --user`` transport."""

    def __init__(self, runner=None, timeout_s: float = MANAGER_TIMEOUT_S):
        self._runner = runner
        self.timeout_s = timeout_s

    def _run(self, argv: list[str]) -> tuple[int, str]:
        run = self._runner or subprocess.run     # resolved per call: patchable
        try:
            done = run(argv, capture_output=True, text=True, timeout=self.timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagedUnavailable('manager_' + type(exc).__name__) from None
        return done.returncode, done.stdout or ''

    def run_service(self, unit: str, argv: list[str], *, cwd, env=None, properties=()) -> None:
        cmd = ['systemd-run', '--user', '--unit=' + unit, '--collect',
               '--property=WorkingDirectory=' + str(cwd)]
        cmd += ['--property=' + p for p in (*SERVICE_PROPERTIES, *properties)]
        cmd += ['--setenv=%s=%s' % kv for kv in (env or {}).items()]
        rc, _ = self._run([*cmd, '--', *argv])
        if rc != 0:
            raise ManagedUnavailable('manager_refused_job')   # accepted-job only; never readiness

    def run_timer(self, unit: str, argv: list[str], *, cwd, period_s: int, env=None) -> None:
        cmd = ['systemd-run', '--user', '--unit=' + unit, '--on-active=1s',
               '--on-unit-inactive=%ds' % period_s, '--timer-property=AccuracySec=1s',
               '--property=WorkingDirectory=' + str(cwd), '--property=Restart=no',
               *['--setenv=%s=%s' % kv for kv in (env or {}).items()],
               '--property=SuccessExitStatus=1', '--property=TimeoutStartSec=20s', *argv]
        rc, _ = self._run(cmd)
        if rc != 0:
            raise ManagedUnavailable('manager_refused_timer')

    def show(self, unit: str, *props: str) -> dict[str, str]:
        rc, out = self._run(['systemctl', '--user', 'show', _unit_name(unit),
                             *['--property=' + p for p in props]])
        if rc != 0:
            raise ManagedUnavailable('manager_show_failed')
        values = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
        return values

    def kill_main(self, unit: str, signame: str = 'SIGTERM') -> None:
        if signame != 'SIGTERM':
            raise ValueError('only_sigterm')
        rc, _ = self._run(['systemctl', '--user', 'kill', '--signal=SIGTERM',
                           '--kill-whom=main', _unit_name(unit)])
        if rc != 0:
            raise ManagedUnavailable('manager_kill_failed')

    def stop(self, unit: str) -> None:
        rc, _ = self._run(['systemctl', '--user', 'stop', _unit_name(unit)])
        if rc != 0:
            raise ManagedUnavailable('manager_stop_failed')


def _stat_fields(proc_root: Path, pid: int):
    """/proc stat fields after the comm, None if gone, ManagedUnavailable if unreadable."""
    try:
        return (proc_root / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, IndexError):
        raise ManagedUnavailable('proc_unreadable') from None


def _ticks(proc_root: Path, pid: int):
    fields = _stat_fields(proc_root, pid)
    return None if fields is None or len(fields) < 20 else int(fields[19])


def _argv(proc_root: Path, pid: int) -> list[str]:
    try:
        raw = (proc_root / str(pid) / 'cmdline').read_bytes()
    except OSError:
        raise ManagedUnavailable('proc_unreadable') from None
    return [a.decode('utf-8', 'replace') for a in raw.split(b'\0') if a]


def check_identity(manager, unit: str, pid: int, start_ticks: int, proc_root: Path,
                   expect_argv: list[str] | None = None) -> list[str]:
    """Reasons the unit's CURRENT main process is not (pid, start_ticks); [] = verified."""
    reasons = []
    props = manager.show(unit, 'ActiveState', 'MainPID', 'ControlGroup')
    if props.get('ActiveState') != 'active':
        reasons.append('unit_not_active')
    if props.get('MainPID') != str(pid):
        reasons.append('main_pid_mismatch')
    if _ticks(proc_root, pid) != start_ticks:
        reasons.append('start_ticks_mismatch')
    group = props.get('ControlGroup', '')
    try:
        in_group = any(line.rsplit(':', 1)[-1] == group
                       for line in (proc_root / str(pid) / 'cgroup').read_text().splitlines())
    except OSError:
        in_group = False
    if not group.endswith('/' + _unit_name(unit)) or not in_group:
        reasons.append('cgroup_mismatch')
    if expect_argv is not None and _argv(proc_root, pid) != expect_argv:
        reasons.append('argv_mismatch')
    return reasons


def cgroup_members(group: str, proc_root: Path) -> list[int]:
    """Pids whose cgroup line is exactly ``group``. Unreadable entries raise."""
    found = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            lines = (entry / 'cgroup').read_text().splitlines()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            raise ManagedUnavailable('proc_unreadable') from None
        if any(line.rsplit(':', 1)[-1] == group for line in lines):
            found.append(int(entry.name))
    return found


class ManagedProcess:
    """A unit's verified main process, shaped like the owned-child handle the
    bootstrap controller already uses (``pid`` / ``poll`` / ``send_signal``)."""

    def __init__(self, manager, unit: str, pid: int, start_ticks: int, cgroup: str,
                 proc_root: Path = Path('/proc'), expect_argv: list[str] | None = None):
        self.manager, self.unit, self.pid = manager, unit, pid
        self.start_ticks, self.cgroup = start_ticks, cgroup
        self.proc_root, self.expect_argv = proc_root, expect_argv

    def verify(self) -> list[str]:
        try:
            return check_identity(self.manager, self.unit, self.pid, self.start_ticks,
                                  self.proc_root, self.expect_argv)
        except ManagedUnavailable as exc:
            return ['identity_unavailable:' + str(exc)]

    def poll(self):
        """0 only on proof of cessation: the unit is terminal (inactive/failed),
        the ORIGINAL pid/ticks is gone and no process remains in its cgroup.
        An active unit, a replaced MainPID, or unreadable evidence is None
        (unknown), never termination."""
        try:
            props = self.manager.show(self.unit, 'ActiveState')
            fields = _stat_fields(self.proc_root, self.pid)
            members = cgroup_members(self.cgroup, self.proc_root)
        except ManagedUnavailable:
            return None
        pid_gone = (fields is None or len(fields) < 20 or fields[0] in ('Z', 'X')
                    or fields[19] != str(self.start_ticks))
        unit_terminal = props.get('ActiveState') in ('inactive', 'failed')
        return 0 if pid_gone and unit_terminal and not members else None

    def send_signal(self, sig) -> None:
        if sig != signal.SIGTERM:
            raise ValueError('only_sigterm')
        reasons = self.verify()
        if reasons:
            raise ManagedIdentityMixed(','.join(reasons))
        self.manager.kill_main(self.unit)


def adopt(manager, unit: str, *, proc_root: Path = Path('/proc'),
          expect_argv: list[str] | None = None, timeout_s: float = READY_TIMEOUT_S,
          monotonic=time.monotonic, sleep=time.sleep) -> ManagedProcess:
    """Bind to the unit's actual main process once the manager reports it active."""
    deadline = monotonic() + timeout_s
    while True:
        props = manager.show(unit, 'ActiveState', 'MainPID', 'ControlGroup')
        pid = int(props['MainPID']) if props.get('MainPID', '').isdigit() else 0
        if props.get('ActiveState') == 'active' and pid > 0:
            ticks = _ticks(proc_root, pid)
            if ticks is not None:
                reasons = check_identity(manager, unit, pid, ticks, proc_root, expect_argv)
                if reasons:
                    raise ManagedIdentityMixed(','.join(reasons))
                return ManagedProcess(manager, unit, pid, ticks, props['ControlGroup'],
                                      proc_root, expect_argv)
        if monotonic() >= deadline:
            raise ManagedUnavailable('managed_identity_not_established')
        sleep(0.5)


def launch(manager, unit: str, argv: list[str], *, cwd, env=None, properties=(),
           proc_root: Path = Path('/proc'), **wait) -> ManagedProcess:
    """Submit one transient service, then establish its actual identity."""
    manager.run_service(unit, argv, cwd=cwd, env=env, properties=properties)
    return adopt(manager, unit, proc_root=proc_root, expect_argv=list(argv), **wait)


def token_pids(token: str, proc_root: Path = Path('/proc')) -> list[int]:
    """Pids whose argv carries ``--bootstrap-token <token>``. Unreadable entries raise."""
    found = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = _argv(proc_root, int(entry.name))
        except ManagedUnavailable:
            if (proc_root / entry.name).exists():
                raise
            continue
        if '--bootstrap-token' in argv and token in argv:
            found.append(int(entry.name))
    return found


def unit_absent(manager, unit: str, token: str, proc_root: Path = Path('/proc')) -> bool:
    """True only when the manager no longer has the unit active and no process
    carries its token. Any doubt is False (caller reports UNKNOWN)."""
    try:
        props = manager.show(unit, 'ActiveState', 'MainPID')
        return (props.get('ActiveState') in ('inactive', 'failed')
                and props.get('MainPID') in (None, '0') and not token_pids(token, proc_root))
    except ManagedUnavailable:
        return False


# ── explicit role lifetimes ───────────────────────────────────────────────────
# The research CLI is revision-checked, has its own slot budget and single-flight
# lock and honours SIGTERM. Its unit keeps its terminal state (RemainAfterExit)
# so a waiter can read Result/ExecMainStatus after the process exits.
_COMPLETION_ROLES = {'research'}
_FULL_SHA = __import__('re').compile(r'^[0-9a-f]{40}$')


def role_command(role: str, python: str, revision: str | None = None) -> list[str]:
    spec = ROLES.get(role, ...)
    if spec is ...:
        raise ValueError('unknown_role')
    if role == 'research':
        if not revision or not _FULL_SHA.match(revision):
            raise ValueError('revision_required')
        return [python, *spec, '--expected-revision', revision]
    return [python, *spec]


def launch_role(role: str, root, *, revision: str | None = None, manager=None,
                uuid_hex: str | None = None, **wait) -> dict:
    """Launch one managed unit and return a serializable identity receipt."""
    from uuid import uuid4
    root = Path(root).resolve()
    manager = manager or UserManager()
    unit = 'luffy-%s-%s' % (role, uuid_hex or uuid4().hex)
    argv = role_command(role, str(root / 'venv/bin/python'), revision)
    properties = ['StandardOutput=append:%s/logs/%s-unit.log' % (root, role), 'StandardError=inherit']
    if role in _COMPLETION_ROLES:
        properties.append('RemainAfterExit=yes')
        manager.run_service(unit, argv, cwd=root, env={'TMPDIR': str(root / 'tmp')},
                            properties=tuple(properties))
        return dict(allow=True, role=role, unit=unit, root=str(root), revision=revision, argv=argv,
                    **adopt_completion(manager, unit, argv, root, **wait))
    handle = launch(manager, unit, argv, cwd=root, env={'TMPDIR': str(root / 'tmp')},
                    properties=tuple(properties), **wait)
    return dict(allow=True, role=role, unit=unit, pid=handle.pid, start_ticks=handle.start_ticks,
                cgroup=handle.cgroup, root=str(root), revision=revision, argv=argv)


_INVOCATION_PROPS = ('LoadState', 'ActiveState', 'SubState', 'Result', 'ExecMainStatus', 'ExecMainPID',
                     'MainPID', 'ControlGroup', 'InvocationID', 'ExecMainStartTimestampMonotonic',
                     'WorkingDirectory', 'ExecStart')


def _exec_argv(props) -> list[str] | None:
    match = re.search(r'argv\[\]=([^;]*);', props.get('ExecStart', ''))
    return match.group(1).split() if match else None


def adopt_completion(manager, unit: str, argv: list[str], root: Path, *, proc_root: Path = Path('/proc'),
                     timeout_s: float = READY_TIMEOUT_S, monotonic=time.monotonic, sleep=time.sleep) -> dict:
    """Identity for a bounded run-to-completion unit, bound to ONE invocation.

    Either the live main process (pid/ticks/cgroup/argv verified, as for any
    managed unit) or, when the job already finished before it could be seen
    alive, the manager's own recorded evidence for this invocation: the exact
    InvocationID, the main pid and its monotonic start stamp, ExecStart and
    WorkingDirectory, a recorded Result/ExecMainStatus, MainPID 0 and no
    process left in the cgroup. /proc start ticks are NOT invented for it
    (``start_ticks`` is None, ``completed`` True). An absent or merely accepted
    unit is neither."""
    deadline = monotonic() + timeout_s
    while True:
        p = manager.show(unit, *_INVOCATION_PROPS)
        invocation, stamp = p.get('InvocationID', ''), p.get('ExecMainStartTimestampMonotonic', '')
        pid = int(p['MainPID']) if p.get('MainPID', '').isdigit() else 0
        bound = bool(invocation) and stamp not in ('', '0')
        if p.get('LoadState') == 'loaded' and bound:
            if p.get('ActiveState') == 'active' and pid > 0 and _ticks(proc_root, pid) is not None:
                ticks = _ticks(proc_root, pid)
                reasons = check_identity(manager, unit, pid, ticks, proc_root, argv)
                if reasons:
                    raise ManagedIdentityMixed(','.join(reasons))
                return dict(pid=pid, start_ticks=ticks, cgroup=p['ControlGroup'], completed=False,
                            invocation_id=invocation, exec_start_monotonic=stamp)
            exec_pid = p.get('ExecMainPID', '')
            finished = (pid == 0 and exec_pid.isdigit() and int(exec_pid) > 0 and p.get('Result')
                        and p.get('ExecMainStatus') is not None
                        and (p.get('ActiveState') in _TERMINAL
                             or (p.get('ActiveState') == 'active' and p.get('SubState') == 'exited')))
            if finished:
                if _exec_argv(p) != list(argv) or p.get('WorkingDirectory') != str(root):
                    raise ManagedIdentityMixed('completed_unit_command_mismatch')
                group = p.get('ControlGroup', '')
                if group and cgroup_members(group, proc_root):
                    raise ManagedIdentityMixed('cgroup_workload_survives')
                return dict(pid=int(exec_pid), start_ticks=None, cgroup=group, completed=True,
                            invocation_id=invocation, exec_start_monotonic=stamp)
        if monotonic() >= deadline:
            raise ManagedUnavailable('managed_identity_not_established')
        sleep(0.5)


_TERMINAL = ('inactive', 'failed')


def wait_role(result: dict, root, revision: str | None, timeout_s: float, should_stop=lambda: False, *,
              manager=None, proc_root: Path = Path('/proc'), poll_s: float = 2.0,
              monotonic=time.monotonic, sleep=time.sleep) -> dict:
    """Boundedly observe a launched role until verified completion.

    Returns ``dict(state=..., reason=...)`` with state one of
      SUCCEEDED  unit terminal/exited, Result=success, ExecMainStatus=0, the
                 original pid/ticks gone and no process left in its cgroup;
      FAILED     same proof of cessation with a failing Result/ExecMainStatus;
      RUNNING    the same verified process is still alive (timeout / stop request);
      UNKNOWN    identity mixed, evidence unreadable, or terminal proof missing.
    It only reads: it never signals, stops, restarts or cancels the unit, and a
    stop request or timeout leaves the independently owned unit running.
    Accepted job, MainPID 0 or an absent unit alone is never completion."""
    manager = manager or UserManager()
    unit, pid, ticks, group = (result.get(k) for k in ('unit', 'pid', 'start_ticks', 'cgroup'))
    completed = result.get('completed') is True
    bind = dict(invocation=result.get('invocation_id'), stamp=result.get('exec_start_monotonic'),
                completed=completed)
    if (result.get('allow') is not True or not isinstance(unit, str) or not unit.startswith('luffy-')
            or result.get('root') != str(Path(root).resolve()) or result.get('revision') != revision
            or not isinstance(pid, int) or not (isinstance(ticks, int) or (completed and ticks is None))
            or not isinstance(group, str) or not (group or completed)
            or not isinstance(bind['invocation'], str) or not bind['invocation']
            or not isinstance(bind['stamp'], str) or bind['stamp'] in ('', '0')):
        return dict(state='UNKNOWN', reason='receipt_mismatch', unit=unit)
    deadline = monotonic() + timeout_s
    last = dict(state='UNKNOWN', reason='not_observed', unit=unit)
    while True:
        last = _observe(manager, unit, pid, ticks, group, proc_root, bind) | dict(unit=unit)
        if last['state'] in ('SUCCEEDED', 'FAILED') or last.get('mixed'):
            return last
        if should_stop():
            return dict(last, reason='stop_requested', cancelled=False)
        if monotonic() >= deadline:
            return dict(last, reason='timeout' if last['state'] == 'RUNNING' else last['reason'], cancelled=False)
        sleep(min(poll_s, max(0.0, deadline - monotonic())))


def _observe(manager, unit, pid, ticks, group, proc_root, bind) -> dict:
    try:
        p = manager.show(unit, 'LoadState', 'ActiveState', 'SubState', 'Result', 'ExecMainStatus',
                         'ExecMainPID', 'MainPID', 'ControlGroup', 'InvocationID',
                         'ExecMainStartTimestampMonotonic')
        fields = _stat_fields(proc_root, pid)
        members = cgroup_members(group, proc_root) if group else []
    except ManagedUnavailable as exc:
        return dict(state='UNKNOWN', reason=str(exc))
    mixed = dict(state='UNKNOWN', reason='identity_mixed', mixed=True)
    if p.get('LoadState') == 'not-found':
        return dict(state='UNKNOWN', reason='unit_absent_without_result')
    if (p.get('InvocationID') != bind['invocation'] or p.get('ExecMainStartTimestampMonotonic') != bind['stamp']
            or p.get('ExecMainPID') not in (str(pid), '0') or p.get('ControlGroup') not in (group, '')):
        return mixed                           # another invocation / process owns this unit now
    if ticks is None:
        # fast-completion receipt: no /proc identity exists; the manager's record of
        # THIS invocation (above) plus MainPID 0 is the whole claim
        if p.get('MainPID') != '0' or p.get('ExecMainPID') != str(pid):
            return mixed
    else:
        alive = fields is not None and len(fields) >= 20 and fields[0] not in ('Z', 'X')
        same = alive and fields[19] == str(ticks)
        if alive and not same:
            return mixed                       # pid reused
        if same:
            if p.get('ActiveState') in _TERMINAL or p.get('MainPID') != str(pid):
                return mixed
            return dict(state='RUNNING', reason='running')
    # the process is gone: need the manager's recorded outcome and an empty cgroup
    if members:
        return dict(state='UNKNOWN', reason='cgroup_workload_survives')
    exited = p.get('ActiveState') in _TERMINAL or (p.get('ActiveState') == 'active' and p.get('SubState') == 'exited')
    if not exited or p.get('MainPID') != '0' or not p.get('Result') or p.get('ExecMainStatus') is None:
        return dict(state='UNKNOWN', reason='terminal_proof_missing')
    ok = p['Result'] == 'success' and p['ExecMainStatus'] == '0' and p.get('ActiveState') != 'failed'
    return dict(state='SUCCEEDED' if ok else 'FAILED', reason='completed' if ok else 'unit_failed',
                result=p['Result'], exit_status=p['ExecMainStatus'])


def release_role(result: dict, manager=None, proc_root: Path = Path('/proc')) -> bool:
    """Drop an EXITED completion-role unit's retained record. Refuses anything
    whose original process is still alive; never signals."""
    manager = manager or UserManager()
    try:
        state = _observe(manager, result['unit'], result['pid'], result['start_ticks'], result['cgroup'],
                         proc_root, dict(invocation=result['invocation_id'],
                                         stamp=result['exec_start_monotonic']))['state']
        if state not in ('SUCCEEDED', 'FAILED'):
            return False
        manager.stop(result['unit'])
        return True
    except (ManagedUnavailable, KeyError):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='managed')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('launch')
    p.add_argument('--role', required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--revision')
    args = ap.parse_args(argv)
    try:
        result = launch_role(args.role, args.root, revision=args.revision)
    except (ManagedUnavailable, ManagedIdentityMixed, ValueError) as exc:
        print(json.dumps(dict(allow=False, role=args.role, reason=str(exc))))
        return 1                       # fail closed: no unmanaged fallback
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
