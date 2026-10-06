"""Single identifiable Kernel instance (RUN-01).

The Kernel holds an exclusive non-blocking ``flock`` on ``data/kernel.lock``
for its whole life. The kernel dying releases the lock, so there is no stale
lock to clean up and no PID-reuse guesswork; the lock is the only authority on
"a kernel is running". ``data/kernel_instance.json`` is the identity the lock
holder publishes (pid + /proc start ticks, code revision, heartbeat instance).

Launcher contract (``restart.sh`` uses the CLI below):
  * ``verify``: refuse to start when the checkout is not the intended revision.
  * Kernel start: refuses (exit 3) when the lock is held or a legacy un-locked
    kernel process exists. A failed boot therefore can never become a second one.
  * ``stop``: SIGTERM only the process whose pid AND start ticks match the
    record of the lock holder, then wait for the lock to be released. On timeout
    it reports and exits non-zero: no SIGKILL, no replacement start.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from .core.config import ROOT

SCHEMA = "luffy-kernel-instance.v1"
EXIT_ALREADY_RUNNING = 3
EXIT_STOP_TIMEOUT = 4
EXIT_UNVERIFIED = 5
EXIT_WRONG_REVISION = 6


class AlreadyRunning(RuntimeError):
    def __init__(self, reason: str, detail=None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


class WrongRevision(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def code_revision(root: Path = ROOT) -> dict:
    """HEAD plus whether tracked runtime code/config differs from it. Untracked
    files and unrelated docs/graph output do not change what the kernel runs."""
    head = _git(root, "rev-parse", "HEAD")
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no", "--",
                 "trader", "config.yaml", "org.yaml")
    return {"revision": head, "dirty_code": None if dirty is None else bool(dirty)}


def check_revision(expected: str, root: Path = ROOT) -> dict:
    """Raise unless the checkout is exactly ``expected`` with clean runtime code."""
    cur = code_revision(root)
    if not expected or not cur["revision"] or not cur["revision"].startswith(expected):
        raise WrongRevision(f"revision {cur['revision']} != expected {expected}")
    if cur["dirty_code"] is not False:
        raise WrongRevision(f"runtime code not clean at {cur['revision']} "
                            f"(dirty_code={cur['dirty_code']})")
    return cur


def proc_start_ticks(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[19])
    except Exception:
        return None


def _paths(root: Path):
    return root / "data" / "kernel.lock", root / "data" / "kernel_instance.json"


def _read_record(path: Path) -> dict | None:
    try:
        v = json.loads(path.read_text())
        ok = (isinstance(v, dict) and v.get("schema") == SCHEMA
              and type(v.get("pid")) is int and type(v.get("start_ticks")) is int
              and isinstance(v.get("instance_id"), str))
        return v if ok else None
    except Exception:
        return None


def _lock_held_elsewhere(lock_path: Path) -> bool:
    """True iff another open file description holds the kernel lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False


def legacy_kernel_pids(exclude=()) -> list[int]:
    """Kernel processes started before the lock existed (module run, not
    --status/--panic). They hold no lock, so they must be found by cmdline."""
    out = []
    me = {os.getpid(), *exclude}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit() or int(p.name) in me:
            continue
        try:
            argv = (p / "cmdline").read_bytes().split(b"\0")
        except Exception:
            continue
        if (len(argv) >= 3 and Path(argv[0].decode()).name.startswith("python")
                and argv[1] == b"-m" and argv[2] == b"trader.kernel"
                and not {b"--status", b"--panic"} & set(argv[3:])):
            out.append(int(p.name))
    return out


def inspect(root: Path = ROOT, heartbeat_path: Path | None = None) -> dict:
    """Read-only identity of the kernel, verified against /proc and the lock."""
    lock_path, rec_path = _paths(root)
    rec = _read_record(rec_path)
    held = _lock_held_elsewhere(lock_path)
    out = {"state": "RUNNING" if held else ("STALE_RECORD" if rec else "NONE"),
           "record": rec, "verified": False, "heartbeat_belongs_to_instance": None,
           "legacy_pids": [] if held else legacy_kernel_pids()}
    if held and rec is not None:
        out["verified"] = proc_start_ticks(rec["pid"]) == rec["start_ticks"]
        try:
            hb = json.loads((heartbeat_path or root / "data" / "heartbeat_luffy.json").read_text())
            bound = rec.get("heartbeat_instance_id")
            out["heartbeat_belongs_to_instance"] = bound is not None and hb.get("instance_id") == bound
        except Exception:
            out["heartbeat_belongs_to_instance"] = False
    if out["legacy_pids"]:
        out["state"] = "LEGACY_UNLOCKED"
    return out


class KernelInstance:
    """Holder of the kernel lock. Keep the object alive for the process life."""

    def __init__(self, root: Path = ROOT):
        self.root = root
        self.lock_path, self.rec_path = _paths(root)
        self._fh = None
        self.record: dict | None = None

    def acquire(self, expect_revision: str | None = None) -> dict:
        rev = check_revision(expect_revision, self.root) if expect_revision \
            else code_revision(self.root)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise AlreadyRunning("kernel_lock_held", _read_record(self.rec_path))
        legacy = legacy_kernel_pids()
        if legacy:
            fh.close()
            raise AlreadyRunning("legacy_kernel_process", legacy)
        self._fh = fh
        pid = os.getpid()
        self.record = {"schema": SCHEMA, "instance_id": uuid4().hex, "pid": pid,
                       "start_ticks": proc_start_ticks(pid), "started_at": time.time(),
                       "heartbeat_instance_id": None, **rev}
        self._publish()
        return self.record

    def bind_heartbeat(self, heartbeat_instance_id: str) -> None:
        self.record["heartbeat_instance_id"] = heartbeat_instance_id
        self._publish()

    def _publish(self) -> None:
        tmp = self.rec_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.record, sort_keys=True))
        os.replace(tmp, self.rec_path)

    def release(self) -> None:
        if self._fh is not None:
            cur = _read_record(self.rec_path)
            if cur and cur["instance_id"] == self.record["instance_id"]:
                try:
                    self.rec_path.unlink()
                except FileNotFoundError:
                    pass
            self._fh.close()
            self._fh = None


def stop_kernel(root: Path = ROOT, timeout: float = 300.0, poll: float = 1.0,
                kill=os.kill, sleep=time.sleep) -> int:
    """Graceful stop of the verified lock holder only. Never SIGKILL."""
    state = inspect(root)
    if state["state"] in ("NONE", "STALE_RECORD"):
        return 0
    if state["state"] == "LEGACY_UNLOCKED" or not state["verified"]:
        print(json.dumps({"stop": "refused_unverified_process", **state}), file=sys.stderr)
        return EXIT_UNVERIFIED
    pid, ticks = state["record"]["pid"], state["record"]["start_ticks"]
    if proc_start_ticks(pid) != ticks:                      # re-verify right before signalling
        return EXIT_UNVERIFIED
    kill(pid, signal.SIGTERM)
    lock_path, _ = _paths(root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _lock_held_elsewhere(lock_path):
            return 0
        sleep(poll)
    print(json.dumps({"stop": "timeout_still_running", "pid": pid,
                      "waited_s": timeout}), file=sys.stderr)
    return EXIT_STOP_TIMEOUT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runtime_identity")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    v = sub.add_parser("verify")
    v.add_argument("--expect-revision", required=True)
    s = sub.add_parser("stop")
    s.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(inspect(), indent=2, default=str))
        return 0
    if a.cmd == "verify":
        try:
            print(json.dumps(check_revision(a.expect_revision)))
            return 0
        except WrongRevision as e:
            print(f"wrong_revision: {e}", file=sys.stderr)
            return EXIT_WRONG_REVISION
    return stop_kernel(timeout=a.timeout)


if __name__ == "__main__":
    sys.exit(main())
