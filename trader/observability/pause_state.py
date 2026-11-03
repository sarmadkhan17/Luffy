"""Noncreating control-switch status and no-follow, owner-validated file locks."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess

FLAGS = ('watchdog.off', 'observers.off', 'safety_monitor.off',
         'declared_population.enabled', 'investigation.enabled', 'accounting.enabled')
LOCKS = ('kernel.lock', 'watchdog.lock', 'safety_health.lock', 'bootstrap-controller.lock',
         'observation-cycle.lock', 'attention_learning.lock', 'declared_population.lock',
         'investigation.lock', 'accounting.lock')


class UnsafeControlPath(ValueError):
    pass


def _validate(st, directory=False):
    if stat.S_ISLNK(st.st_mode):
        return 'symlink'
    if not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
        return 'not_directory' if directory else 'not_regular'
    if st.st_uid != os.geteuid():
        return 'foreign_owner'
    # The installation uses a shared owner group (umask 0002). Preserve that
    # contract; require this UID and reject world-writable controls/directories.
    if st.st_mode & 0o002:
        return 'world_writable'
    if not directory and st.st_nlink != 1:
        return 'hardlink'
    return None


@contextmanager
def _directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        reason = _validate(os.fstat(fd), directory=True)
        if reason:
            raise UnsafeControlPath(reason)
        yield fd
    finally:
        os.close(fd)


def status(root):
    """Read metadata only. Invalid switches never count as an opt-out."""
    root = Path(root).absolute()
    switches, locks, errors = {}, {}, []
    directory_valid = False
    try:
        with _directory(root) as rootfd:
            # dir_fd + NOFOLLOW pins the checked directory across child checks.
            try:
                fd = os.open('data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=rootfd)
            except FileNotFoundError:
                # Fresh roots have no switches; status must not create data/.
                directory_valid = True
                for name in FLAGS + LOCKS:
                    (switches if name in FLAGS else locks)[name] = dict(
                        state='ABSENT', present=False, valid=True, reason=None)
                fd = None
            try:
                reason = _validate(os.fstat(fd), directory=True) if fd is not None else None
                if reason:
                    raise UnsafeControlPath(reason)
                directory_valid = True
                for name in FLAGS + LOCKS if fd is not None else ():
                    try:
                        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        reason = _validate(st)
                        record = dict(state='INVALID' if reason else 'PRESENT', present=True,
                                      valid=reason is None, owner_uid=st.st_uid,
                                      mode=oct(stat.S_IMODE(st.st_mode)), reason=reason)
                        if reason:
                            errors.append(name + ':' + reason)
                    except FileNotFoundError:
                        record = dict(state='ABSENT', present=False, valid=True, reason=None)
                    (switches if name in FLAGS else locks)[name] = record
            finally:
                if fd is not None:
                    os.close(fd)
    except (OSError, UnsafeControlPath) as exc:
        errors.append('control_directory_unavailable:' + type(exc).__name__)
    for name in FLAGS + LOCKS:
        (switches if name in FLAGS else locks).setdefault(name,
            dict(state='UNKNOWN', present=None, valid=False, reason='directory_unavailable'))
    enabled = lambda name: switches[name]['state'] == 'PRESENT'
    return dict(schema='luffy-pause-state.v1', root=str(root), valid=not errors,
                directory_valid=directory_valid, switches=switches, locks=locks, errors=errors,
                watchdog_paused=enabled('watchdog.off'), observers_paused=enabled('observers.off'),
                safety_monitor_paused=enabled('safety_monitor.off'),
                kernel_supervision_allowed=not errors and not enabled('watchdog.off'))


@contextmanager
def file_lock(path, *, blocking=False):
    """Never follow, truncate, or hand the lock descriptor to a child."""
    path = Path(path)
    with _directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        try:
            reason = _validate(os.fstat(fd))
            if reason:
                raise UnsafeControlPath(reason)
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            held = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                raise UnsafeControlPath('lock_replaced')
            yield fd
        finally:
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--status', action='store_true')
    mode.add_argument('--validate', action='store_true')
    mode.add_argument('--check', choices=FLAGS)
    mode.add_argument('--lock-run', choices=LOCKS)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    state = status(args.root)
    if args.status:
        print(json.dumps(state, sort_keys=True))
        return 0 if state['valid'] else 2
    if args.check:
        flag = state['switches'][args.check]
        return (0 if flag['present'] else 1) if flag['valid'] else 2
    if not state['valid']:
        print(json.dumps(state, sort_keys=True))
        return 2
    if args.validate:
        return 0
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('--lock-run requires a command')
    try:
        with file_lock(args.root / 'data' / args.lock_run):
            return subprocess.run(command, close_fds=True).returncode
    except BlockingIOError:
        return 3
    except (OSError, UnsafeControlPath):
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
