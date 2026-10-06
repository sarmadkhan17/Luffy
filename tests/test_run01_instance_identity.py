"""RUN-01: one identifiable Kernel, verified stop, no SIGKILL, no second kernel.

Real child processes and real flock in a tmp root; no venue, no kernel boot.
"""
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from trader import runtime_identity as ri

HOLDER = textwrap.dedent("""
    import signal, sys, time
    from pathlib import Path
    from trader import runtime_identity as ri
    root = Path(sys.argv[1]); mode = sys.argv[2]
    inst = ri.KernelInstance(root); inst.acquire()
    inst.bind_heartbeat("hb-1")
    if mode == "graceful":
        signal.signal(signal.SIGTERM, lambda *a: (inst.release(), sys.exit(0)))
    else:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("ready", flush=True)
    time.sleep(120)
""")


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path


def spawn(root, mode="graceful"):
    p = subprocess.Popen([sys.executable, "-c", HOLDER, str(root), mode],
                         stdout=subprocess.PIPE, text=True, cwd=Path(ri.__file__).parents[1])
    assert p.stdout.readline().strip() == "ready"
    return p


@pytest.fixture
def procs():
    ps = []
    yield ps
    for p in ps:
        p.kill()
        p.wait()


def git_repo(path):
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (path / "trader").mkdir(); (path / "trader" / "a.py").write_text("x=1\n")
    (path / "config.yaml").write_text("a: 1\n"); (path / "org.yaml").write_text("b: 1\n")
    run("add", "-A"); run("commit", "-qm", "c")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()


# 1. intended revision checked before startup
def test_revision_binding(tmp_path):
    head = git_repo(tmp_path)
    assert ri.check_revision(head[:12], tmp_path)["revision"] == head
    with pytest.raises(ri.WrongRevision):
        ri.check_revision("deadbeef", tmp_path)
    (tmp_path / "trader" / "a.py").write_text("x=2\n")          # modified runtime code
    with pytest.raises(ri.WrongRevision):
        ri.check_revision(head, tmp_path)
    (tmp_path / "trader" / "a.py").write_text("x=1\n")
    (tmp_path / "docs.md").write_text("untracked")               # unrelated file is fine
    assert ri.check_revision(head, tmp_path)["dirty_code"] is False


def test_wrong_revision_takes_no_lock(root, tmp_path):
    git_repo(root)
    inst = ri.KernelInstance(root)
    with pytest.raises(ri.WrongRevision):
        inst.acquire("deadbeef")
    assert not ri._lock_held_elsewhere(inst.lock_path) and inst._fh is None
    assert not inst.rec_path.exists()


# 3. an existing valid kernel prevents duplicate launch
def test_duplicate_launch_refused(root, procs):
    p = spawn(root); procs.append(p)
    with pytest.raises(ri.AlreadyRunning) as e:
        ri.KernelInstance(root).acquire()
    assert e.value.reason == "kernel_lock_held" and e.value.detail["pid"] == p.pid


# 2/5. stale record is not mistaken for a kernel; heartbeat bound to instance
def test_stale_record_and_heartbeat_binding(root, procs):
    p = spawn(root); procs.append(p)
    hb = root / "data" / "heartbeat_luffy.json"
    hb.write_text(json.dumps({"instance_id": "hb-1"}))
    s = ri.inspect(root)
    assert s["state"] == "RUNNING" and s["verified"] and s["heartbeat_belongs_to_instance"] is True
    hb.write_text(json.dumps({"instance_id": "previous-process"}))   # old file, new kernel
    assert ri.inspect(root)["heartbeat_belongs_to_instance"] is False
    p.kill(); p.wait()                                                # crash: lock released by OS
    s = ri.inspect(root)
    assert s["state"] == "STALE_RECORD" and not s["verified"]
    assert ri.stop_kernel(root, kill=lambda *a: pytest.fail("signalled a stale record")) == 0
    ri.KernelInstance(root).acquire()                                 # restart possible


def test_legacy_unlocked_kernel_blocks_start(root, tmp_path_factory):
    fake = tmp_path_factory.mktemp("legacy")
    (fake / "trader").mkdir(); (fake / "trader" / "__init__.py").write_text("")
    (fake / "trader" / "kernel.py").write_text("import time; time.sleep(60)\n")
    p = subprocess.Popen([sys.executable, "-m", "trader.kernel"], cwd=fake)
    try:
        time.sleep(0.5)
        assert p.pid in ri.legacy_kernel_pids()
        with pytest.raises(ri.AlreadyRunning) as e:
            ri.KernelInstance(root).acquire()
        assert e.value.reason == "legacy_kernel_process"
        assert ri.inspect(root)["state"] == "LEGACY_UNLOCKED"
        assert ri.stop_kernel(root, kill=lambda *a: pytest.fail("signalled unverified")) == ri.EXIT_UNVERIFIED
        assert not ri._lock_held_elsewhere(ri._paths(root)[0])   # failed start left no lock
    finally:
        p.kill(); p.wait()


# 6. graceful stop targets only the verified process
def test_stop_graceful_verified(root, procs):
    p = spawn(root); procs.append(p)
    bystander = subprocess.Popen(["sleep", "60"]); procs.append(bystander)
    assert ri.stop_kernel(root, timeout=10) == 0
    assert p.wait(5) == 0 and bystander.poll() is None


def test_stop_refuses_pid_mismatch(root, procs):
    p = spawn(root); procs.append(p)
    bystander = subprocess.Popen(["sleep", "60"]); procs.append(bystander)
    rec_path = ri._paths(root)[1]
    rec = json.loads(rec_path.read_text())
    rec["pid"] = bystander.pid                                    # record names the wrong process
    rec_path.write_text(json.dumps(rec))
    assert ri.stop_kernel(root, timeout=1) == ri.EXIT_UNVERIFIED
    assert bystander.poll() is None and p.poll() is None
    rec["pid"], rec["start_ticks"] = p.pid, rec["start_ticks"] + 1  # pid reuse: ticks differ
    rec_path.write_text(json.dumps(rec))
    assert ri.stop_kernel(root, timeout=1) == ri.EXIT_UNVERIFIED
    assert p.poll() is None


# 7. timeout: no SIGKILL, kernel still held, still no second kernel
def test_stop_timeout_no_sigkill_no_second_kernel(root, procs):
    p = spawn(root, "stubborn"); procs.append(p)
    sent = []
    real = os.kill
    rc = ri.stop_kernel(root, timeout=1.5, poll=0.2, kill=lambda pid, sig: (sent.append(sig), real(pid, sig)))
    assert rc == ri.EXIT_STOP_TIMEOUT
    assert sent == [signal.SIGTERM] and p.poll() is None
    assert ri.inspect(root)["state"] == "RUNNING"
    with pytest.raises(ri.AlreadyRunning):
        ri.KernelInstance(root).acquire()


# restart.sh: stop failure/wrong revision never starts anything
@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "sb"; (d / "venv" / "bin").mkdir(parents=True)
    (d / "restart.sh").write_text((Path(ri.__file__).parents[1] / "restart.sh").read_text())
    (d / "restart.sh").chmod(0o755)
    py = d / "venv" / "bin" / "python"
    py.write_text(f'#!/usr/bin/env bash\necho "$@" >> {d}/calls\n'
                  'case "$*" in *observability.preflight*) exit ${PRE_RC:-0};; *"runtime_identity stop"*) exit ${STOP_RC:-0};; '
                  '*"runtime_identity verify"*) exit ${VERIFY_RC:-0};; esac\n')
    py.chmod(0o755)
    return d


def run_restart(d, target, **env):
    return subprocess.run(["bash", str(d / "restart.sh"), target], capture_output=True, text=True,
                          env={**os.environ, **{k: str(v) for k, v in env.items()}})


def calls(d):
    f = d / "calls"
    return f.read_text() if f.exists() else ""


def test_restart_stop_timeout_starts_nothing(sandbox):
    r = run_restart(sandbox, "kernel", STOP_RC=4)
    assert r.returncode == 4 and "started" not in r.stdout
    assert "-m trader.kernel" not in calls(sandbox)


def test_restart_wrong_revision_starts_nothing(sandbox):
    r = run_restart(sandbox, "kernel", LUFFY_EXPECT_REVISION="abc", VERIFY_RC=6)
    assert r.returncode == 6 and "-m trader.kernel" not in calls(sandbox)
    assert "stop" not in calls(sandbox)           # running kernel untouched on a bad revision


def test_restart_success_starts_bound_revision(sandbox):
    r = run_restart(sandbox, "kernel", LUFFY_EXPECT_REVISION="abc")
    assert r.returncode == 0 and "started trader.kernel" in r.stdout
    time.sleep(0.5)
    assert "-m trader.kernel --expect-revision abc" in calls(sandbox)


def test_restart_script_never_sigkills():
    src = (Path(ri.__file__).parents[1] / "restart.sh").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "kill -9" not in code and "-KILL" not in code and "SIGKILL" not in code
    assert "kill -TERM" in code


def test_dashboard_never_launches_kernel():
    src = (Path(ri.__file__).parent / "dashboard" / "server.py").read_text()
    assert "subprocess" not in src and "trader.kernel" not in src and "restart.sh" not in src


# 9. restart preserves control/recovery semantics (FROZEN hold, RECOVERY)
from tests.test_kernel_boot_recovery import (WORKERS, _entry_probe, _kernel, _last_event,  # noqa: E402,F401
                                             _protected, _status, _transitions, world)
from trader.core.types import ControlState  # noqa: E402
from trader.engine.state import ControlStateMachine  # noqa: E402


@pytest.mark.parametrize("held", ["FROZEN", "RECOVERY"])
def test_restart_cycle_preserves_frozen_and_recovery(world, monkeypatch, root, held):
    journal, venue = world
    _protected(journal, venue)
    if held == "FROZEN":
        ControlStateMachine(journal).set(ControlState.FROZEN, "operator", "owner hold")
    else:
        venue.unreadable = True
    first = ri.KernelInstance(root); first.acquire()
    k1, _, _ = _kernel(journal, venue, monkeypatch)
    first.bind_heartbeat("hb-1")
    k1.boot()
    assert journal.kv_get("control_state") == held
    first.release()                                   # graceful stop of instance 1
    venue.unreadable = False                          # fault gone: still must not self-activate
    mark = _last_event(journal)
    second = ri.KernelInstance(root)
    rec2 = second.acquire()                           # fresh instance after the stop
    k2, _, started = _kernel(journal, venue, monkeypatch)
    second.bind_heartbeat("hb-2")
    k2.boot()
    assert rec2["instance_id"] != first.record["instance_id"]
    assert rec2["pid"] == first.record["pid"] and second.record["heartbeat_instance_id"] == "hb-2"
    assert k2.state_machine.refresh() == ControlState[held]
    assert _transitions(journal, mark) == []
    assert _entry_probe(k2)[1:] == (f"state={held}: entries blocked", 0)
    assert WORKERS <= set(started)                    # exits/observation still managed
    assert [m for m in venue.mutations if m[0] != "set_leverage"] == []
    second.release()


def test_launcher_and_identity_never_write_control_state():
    for name in ("restart.sh", "trader/runtime_identity.py"):
        src = (Path(ri.__file__).parents[1] / name).read_text()
        assert "state_kv" not in src and "control_state" not in src and "ControlState" not in src


# 8. rendered Dashboard identity/compatibility payload
def test_rendered_dashboard_identity_payload(root, procs, tmp_path):
    from trader.dashboard import server
    head = git_repo(root)
    assert server.kernel_identity_view(root)["state"] == "NONE"
    p = spawn(root); procs.append(p)
    view = server.kernel_identity_view(root)
    assert view["state"] == "RUNNING" and view["verified"] is True
    assert view["record"]["pid"] == p.pid
    # holder was started on a non-git copy path? record carries the root's revision
    assert view["record"]["revision"] == head and view["compatible_revision"] is True
    rec_path = ri._paths(root)[1]
    rec = json.loads(rec_path.read_text()); rec["revision"] = "b" * 40
    rec_path.write_text(json.dumps(rec))
    assert server.kernel_identity_view(root)["compatible_revision"] is False
    rec["start_ticks"] += 1
    rec_path.write_text(json.dumps(rec))
    assert server.kernel_identity_view(root)["verified"] is False
    p.kill(); p.wait()
    assert server.kernel_identity_view(root)["state"] == "STALE_RECORD"


def test_summary_endpoint_renders_kernel_identity(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from trader.dashboard import server
    monkeypatch.setenv("DASH_TOKEN", "fixture-token")
    monkeypatch.setattr(server, "kernel_identity_view", lambda root: {"state": "RUNNING", "marker": 1})
    app = server.create_app({"attention": {"enabled": True}})
    r = TestClient(app).get("/api/summary", headers={"x-luffy-token": "fixture-token"})
    assert r.status_code == 200, r.text
    assert r.json()["kernel_identity"] == {"state": "RUNNING", "marker": 1}


def test_restart_order_preflight_then_revision_then_stop_then_start(sandbox):
    r = run_restart(sandbox, "kernel", PRE_RC=1, LUFFY_EXPECT_REVISION="abc")
    assert r.returncode == 1
    assert calls(sandbox).count("\n") == 1 and "observability.preflight" in calls(sandbox)
    (sandbox / "calls").unlink()
    r = run_restart(sandbox, "kernel", LUFFY_EXPECT_REVISION="abc")
    time.sleep(0.5)
    order = [l.split()[1] for l in calls(sandbox).splitlines()]
    assert order[:3] == ["trader.observability.preflight", "trader.runtime_identity", "trader.runtime_identity"]
    assert order[3] == "trader.kernel"
