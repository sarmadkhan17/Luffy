"""SP-06: noncreating status-only kernel identity. Fake /proc and lock table in tmp_path."""
import builtins
import contextlib
import fcntl
import io
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from trader import runtime_identity as ri

REV = "a" * 40
TOKEN = "t" * 8
PID = 4242
TICKS = 9001


def tree(root):
    """Names + sizes + mtimes of everything under root (or None if absent)."""
    if not Path(root).exists():
        return None
    return sorted((str(p.relative_to(root)), p.lstat().st_size, p.lstat().st_mtime_ns)
                  for p in [Path(root), *Path(root).rglob("*")])


class Fixture:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.root = tmp_path / "root"
        self.proc = tmp_path / "proc"
        self.proc.mkdir()
        self.locks = tmp_path / "locks"
        self.locks.write_text("")

    def make_root(self, *, lock=True, record=True, heartbeat=True, pid=PID, ticks=TICKS):
        (self.root / "data").mkdir(parents=True)
        if lock:
            (self.root / "data/kernel.lock").write_text("")
        if record:
            (self.root / "data/kernel_instance.json").write_text(json.dumps({
                "schema": ri.SCHEMA, "instance_id": "i1", "pid": pid, "start_ticks": ticks,
                "started_at": 1.0, "heartbeat_instance_id": "hb1"}))
        if heartbeat:
            (self.root / "data/heartbeat_luffy.json").write_text(json.dumps({
                "instance_id": "hb1", "timestamp": __import__("time").time() - 7}))

    def hold_lock(self, pid=PID):
        st = os.stat(self.root / "data/kernel.lock")
        self.locks.write_text(
            f"1: POSIX  ADVISORY  WRITE 77 00:01:5 0 EOF\n"
            f"2: FLOCK  ADVISORY  WRITE {pid} {os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino} 0 EOF\n")

    def process(self, pid, argv, *, cwd=None, ticks=TICKS, state="S"):
        d = self.proc / str(pid)
        d.mkdir(exist_ok=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "stat").write_text(f"{pid} (python) {state} " + " ".join(["0"] * 18) + f" {ticks}")
        link = d / "cwd"
        if link.is_symlink():
            link.unlink()
        link.symlink_to(cwd or self.root.resolve())

    def kernel(self, pid=PID, **kw):
        self.process(pid, ["/x/venv/bin/python", "-B", "-m", "trader.kernel",
                           "--expect-revision", REV, "--bootstrap-token", TOKEN], **kw)

    def status(self):
        with armed():
            return ri.status_only(self.root, proc_root=self.proc, locks_file=self.locks)


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)


@contextlib.contextmanager
def armed():
    """Anything creating/appending/writing, importing startup code, loading env or networking fails."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        def deny_open(real):
            def opener(file, mode="r", *a, **k):
                if any(c in str(mode) for c in "awx+"):
                    pytest.fail(f"write-capable open of {file}")
                return real(file, mode, *a, **k)
            return opener
        monkeypatch.setattr(builtins, "open", deny_open(builtins.open))
        monkeypatch.setattr(io, "open", deny_open(io.open))
        real_os_open = os.open

        def os_open(path, flags, *a, **k):
            if flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_TRUNC):
                pytest.fail(f"write-capable os.open of {path}")
            return real_os_open(path, flags, *a, **k)
        monkeypatch.setattr(os, "open", os_open)
        for name in ("mkdir", "makedirs", "rename", "replace", "unlink"):
            monkeypatch.setattr(os, name, lambda *a, _n=name, **k: pytest.fail(f"os.{_n}"))
        monkeypatch.setattr(Path, "mkdir", lambda *a, **k: pytest.fail("Path.mkdir"))
        monkeypatch.setattr(fcntl, "flock", lambda *a: pytest.fail("flock taken/probed"))
        monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network"))
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("subprocess"))
        import dotenv
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail("env load"))
        from trader.core import journal
        monkeypatch.setattr(journal.Journal, "__init__", lambda *a, **k: pytest.fail("Journal opened"))
        yield


def test_missing_root_stays_byte_for_byte_absent_and_reads_none(fx):
    out = fx.status()
    assert out["state"] == "NONE" and out["verified"] is False
    assert not fx.root.exists()


def test_existing_files_are_not_modified_by_any_state(fx):
    fx.make_root()
    fx.kernel()
    fx.hold_lock()
    before = tree(fx.root)
    assert fx.status()["state"] == "RUNNING"
    assert tree(fx.root) == before


def test_argument_bearing_dash_b_kernel_is_recognised_and_verified(fx):
    fx.make_root()
    fx.kernel()
    fx.hold_lock()
    out = fx.status()
    assert out["state"] == "RUNNING" and out["verified"] is True
    assert out["processes"][0]["revision"] == REV and out["processes"][0]["token"] == TOKEN
    assert out["processes"][0]["flags"] == ["-B"]
    assert out["heartbeat_belongs_to_instance"] is True and 5 <= out["heartbeat_age_s"] <= 60


@pytest.mark.parametrize("argv", [
    ["/x/python", "-B", "-m", "trader.kernel", "--status"],
    ["/x/python", "-m", "trader.kernel", "--panic"],
    ["/x/python", "-B", "-m", "trader.kernel", "--expect-revision", REV, "--status"],
    ["/x/python", "-B", "-m", "trader.research"],
    ["/x/python", "-c", "import trader.kernel"],
    ["/bin/sh", "-c", "python -m trader.kernel"],
    ["/x/python", "scripts/monitor.py", "trader.kernel"],
])
def test_status_panic_and_unrelated_processes_are_not_a_kernel(fx, argv):
    fx.make_root(lock=True, record=False)
    fx.process(PID, argv)
    out = fx.status()
    assert out["processes"] == [] and out["state"] == "NONE"


def test_other_checkout_kernel_is_not_ours(fx, tmp_path):
    fx.make_root(lock=False, record=False)
    other = tmp_path / "other"
    other.mkdir()
    fx.kernel(cwd=other.resolve())
    assert fx.status()["state"] == "NONE"


def test_unlocked_matching_process_is_booting_or_legacy_not_none(fx):
    fx.make_root(record=False)
    fx.kernel()
    assert fx.status()["state"] == "UNLOCKED_PROCESS"


def test_pid_reuse_start_ticks_mismatch_refuses_inference(fx):
    fx.make_root()
    fx.kernel(ticks=TICKS + 1)                    # same pid, different process generation
    fx.hold_lock()
    out = fx.status()
    assert out["state"] == "UNKNOWN" and out["verified"] is False
    assert "record_does_not_match_lock_holder" in out["reasons"]


def test_lock_holder_that_is_not_the_recorded_pid_is_unknown(fx):
    fx.make_root()
    fx.kernel()
    fx.hold_lock(pid=PID + 1)
    assert fx.status()["state"] == "UNKNOWN"


def test_lock_held_by_a_non_kernel_process_is_unknown(fx):
    fx.make_root()
    fx.process(PID, ["/x/python", "-B", "-m", "trader.kernel", "--status"], ticks=TICKS)
    fx.hold_lock()
    out = fx.status()
    assert out["state"] == "UNKNOWN" and "holder_not_a_kernel_run" in out["reasons"]


def test_held_lock_without_record_is_unknown_not_none(fx):
    fx.make_root(record=False)
    fx.kernel()
    fx.hold_lock()
    assert fx.status()["state"] == "UNKNOWN"


def test_live_record_pid_without_a_lock_is_ambiguous(fx):
    fx.make_root()
    fx.process(PID, ["/x/python", "-m", "other"], ticks=TICKS)
    assert fx.status()["state"] == "UNKNOWN"


def test_dead_record_without_lock_is_stale(fx):
    fx.make_root()
    assert fx.status()["state"] == "STALE_RECORD"


def test_unreadable_or_malformed_record_is_unknown(fx):
    fx.make_root()
    (fx.root / "data/kernel_instance.json").write_text("{not json")
    assert fx.status()["state"] == "UNKNOWN"
    (fx.root / "data/kernel_instance.json").write_text(json.dumps({"schema": "bad"}))
    assert fx.status()["state"] == "UNKNOWN"


def test_unavailable_lock_table_is_unknown_never_none(fx):
    fx.make_root()
    with armed():
        out = ri.status_only(fx.root, proc_root=fx.proc, locks_file=fx.tmp / "missing-locks")
    assert out["state"] == "UNKNOWN" and "lock_table_unavailable" in out["reasons"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_process_entry_is_ambiguous(fx):
    fx.make_root(record=False)
    fx.kernel()
    (fx.proc / str(PID) / "cmdline").chmod(0)
    out = fx.status()
    assert out["state"] == "UNKNOWN" and "process_table_ambiguous" in out["reasons"]
    (fx.proc / str(PID) / "cmdline").chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_dead_heartbeat_binding_is_reported_false(fx):
    fx.make_root(heartbeat=False)
    fx.kernel()
    fx.hold_lock()
    out = fx.status()
    assert out["state"] == "RUNNING" and out["heartbeat_belongs_to_instance"] is False


def test_no_filesystem_creation_when_root_exists_without_data_dir(fx):
    fx.root.mkdir()
    before = tree(fx.root)
    assert fx.status()["state"] == "NONE"
    assert tree(fx.root) == before


@pytest.mark.parametrize("state,code", [("RUNNING", 0), ("NONE", 10), ("STALE_RECORD", 11),
                                        ("UNKNOWN", 12), ("UNLOCKED_PROCESS", 12)])
def test_cli_exit_codes_distinguish_verified_absence_from_unknown(monkeypatch, capsys, state, code):
    monkeypatch.setattr(ri, "status_only", lambda root: {"state": state})
    assert ri.main(["status-only", "--root", "/nonexistent-fixture"]) == code
    assert json.loads(capsys.readouterr().out)["state"] == state


def test_existing_mutating_commands_are_untouched():
    names = {a for a in ("inspect", "stop_kernel", "KernelInstance", "legacy_kernel_pids") if hasattr(ri, a)}
    assert names == {"inspect", "stop_kernel", "KernelInstance", "legacy_kernel_pids"}


# ── watchdog wiring (static: the script is never executed) ───────────────────
WATCHDOG = Path(__file__).resolve().parents[1] / "scripts/watchdog.sh"


def test_watchdog_uses_status_only_not_the_bare_command_regex():
    text = WATCHDOG.read_text()
    assert "trader.runtime_identity status-only" in text
    assert "pgrep -f -x '.*/python[0-9.]* -m trader\\.kernel'" not in text


def test_watchdog_off_retains_safety_before_exiting_without_launcher_probe():
    lines = WATCHDOG.read_text().splitlines()
    off = next(i for i, l in enumerate(lines) if 'if flag_present watchdog.off' in l)
    first_probe = next(i for i, l in enumerate(lines) if "status-only" in l and not l.lstrip().startswith("#"))
    safety = next(i for i, l in enumerate(lines) if l.strip().startswith('act ') and '--safety-only' in l)
    assert safety < off < first_probe


def test_watchdog_acts_only_on_verified_absence_or_stale_heartbeat():
    text = WATCHDOG.read_text()
    body = text.split('case "$krc" in', 1)[1].split("esac", 1)[0]
    cases = {l.strip() for l in body.splitlines() if l.strip().endswith(")") and not l.strip().startswith("say")}
    assert {"10|11)", "0)", "*)"} <= cases
    star = body.split("*)", 1)[1]
    assert "restart.sh" not in star and "act " not in star     # unknown never starts or restarts
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "touch" not in code and "rm " not in code           # never arms/disarms watchdog.off


def test_launch_scripts_parse():
    root = WATCHDOG.parents[1]
    for script in (WATCHDOG, root / "restart.sh"):
        done = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=20)
        assert done.returncode == 0, done.stderr


def test_heartbeat_from_another_instance_is_not_reported_as_fresh_health(fx):
    fx.make_root()
    (fx.root / "data/heartbeat_luffy.json").write_text(json.dumps({
        "instance_id": "someone-else", "timestamp": __import__("time").time()}))
    fx.kernel()
    fx.hold_lock()
    out = fx.status()
    assert out["state"] == "RUNNING"
    assert out["heartbeat_belongs_to_instance"] is False and out["heartbeat_age_s"] is None


def test_watchdog_age_extraction_ignores_unbound_heartbeats():
    text = WATCHDOG.read_text()
    line = next(l for l in text.splitlines() if l.lstrip().startswith("age=$("))
    assert 'heartbeat_belongs_to_instance' in line and "is True" in line
    snippet = line.split("python3 -c '", 1)[1].rsplit("'", 1)[0]
    for doc, expect in (({"heartbeat_age_s": 5, "heartbeat_belongs_to_instance": True}, "5"),
                        ({"heartbeat_age_s": 5, "heartbeat_belongs_to_instance": False}, ""),
                        ({"heartbeat_age_s": 5, "heartbeat_belongs_to_instance": None}, ""),
                        ({"heartbeat_age_s": 5}, ""),
                        ({"heartbeat_age_s": None, "heartbeat_belongs_to_instance": True}, "")):
        done = subprocess.run([sys.executable, "-I", "-c", snippet], input=json.dumps(doc),
                              capture_output=True, text=True, timeout=20)
        assert done.returncode == 0 and done.stdout.strip() == expect, (doc, done)
