"""SP-05 managed lifetimes and SP-04 observer handover.

Every manager interaction is a fake; /proc is a fixture directory. Nothing here
runs systemd, a service, a signal or a subprocess.
"""
import json
import signal
import subprocess
from pathlib import Path

import pytest

from trader.observability import bootstrap as B, managed as M

REV = "b" * 40
KPID, KTICKS = 5151, 777


@pytest.fixture(autouse=True)
def nothing_unmanaged(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("subprocess/Popen reached: unmanaged or real service action")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def fake_service_state_for(proc_root):
    """shutdown.service_state's exact logic, reading the fixture /proc instead of the real one."""
    def state(process, identity):
        try:
            if process.pid != identity['pid']:
                return 'UNKNOWN'
            if process.poll() is not None:
                return 'TERMINATED'
            fields = (proc_root / str(process.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[19] == str(identity['start_ticks']) and fields[0] not in ('Z', 'X'):
                return 'LIVE'
        except (OSError, ValueError, KeyError, IndexError):
            pass
        return 'UNKNOWN'
    return state


class FakeProc:
    def __init__(self, tmp):
        self.root = tmp / "proc"
        self.root.mkdir()

    def add(self, pid, argv, *, ticks=KTICKS, cgroup=None, state="S"):
        d = self.root / str(pid)
        d.mkdir(exist_ok=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "stat").write_text(f"{pid} (python) {state} " + " ".join(["0"] * 18) + f" {ticks}")
        (d / "cgroup").write_text(f"0::{cgroup}\n" if cgroup else "0::/user.slice/other.service\n")

    def remove(self, pid):
        for f in (self.root / str(pid)).glob("*"):
            f.unlink()
        (self.root / str(pid)).rmdir()


class FakeManager:
    """Stands in for UserManager. `units` maps full unit name -> property dict."""

    def __init__(self, proc, *, accept=True, activate=True, kill_ends=True, pid=KPID, ticks=KTICKS,
                 cgroup_ok=True):
        self.proc, self.accept, self.activate, self.kill_ends = proc, accept, activate, kill_ends
        self.pid, self.ticks, self.cgroup_ok = pid, ticks, cgroup_ok
        self.units, self.calls, self.argv, self.properties = {}, [], {}, {}
        self.stop_fails = set()

    # -- UserManager surface -------------------------------------------------
    def run_service(self, unit, argv, *, cwd, env=None, properties=()):
        self.calls.append(("run_service", unit))
        if not self.accept:
            raise M.ManagedUnavailable("manager_refused_job")
        self.argv[unit] = argv
        self.properties[unit] = tuple(properties)
        group = f"/user.slice/app.slice/{unit}.service"
        self.units[unit + ".service"] = dict(
            Id=unit + ".service", LoadState="loaded", ActiveState="active" if self.activate else "activating",
            SubState="running", Result="success", ExecMainStatus="0", ExecMainPID=str(self.pid),
            MainPID=str(self.pid) if self.activate else "0", ControlGroup=group,
            InvocationID="inv-" + unit + "-1", ExecMainStartTimestampMonotonic="1000",
            WorkingDirectory=str(cwd),
            ExecStart="{ path=%s ; argv[]=%s ; ignore_errors=no }" % (argv[0], " ".join(argv)))
        if self.instant is not None:               # finished before anyone could see it alive
            self.units[unit + ".service"].update(
                MainPID="0", ActiveState="active" if self.instant_remain else ("inactive" if self.instant == 0 else "failed"),
                SubState="exited", ExecMainStatus=str(self.instant),
                Result="success" if self.instant == 0 else "exit-code")
        elif self.activate:
            self.proc.add(self.pid, argv, ticks=self.ticks, cgroup=group if self.cgroup_ok else None)

    def run_timer(self, unit, argv, *, cwd, period_s, env=None):
        self.calls.append(("run_timer", unit))
        if self.fail_timer_after is not None and len([c for c in self.calls if c[0] == "run_timer"]) > self.fail_timer_after:
            raise M.ManagedUnavailable("manager_refused_timer")
        if self.bad_replacement == "cwd":
            cwd = "/other/checkout"
        if self.bad_replacement == "argv":
            argv = [*argv[:-1], "--something-else"]
        self.add_observer(unit, argv, str(cwd))

    bad_replacement = None
    fail_timer_after = None
    timer_inactive = False

    def add_observer(self, unit, argv, cwd, active=True):
        self.units[unit + ".timer"] = dict(Id=unit + ".timer", LoadState="loaded",
                                           ActiveState="active" if active and not self.timer_inactive else "inactive")
        self.units[unit + ".service"] = dict(Id=unit + ".service", LoadState="loaded", ActiveState="inactive",
                                             WorkingDirectory=cwd,
                                             ExecStart="{ path=%s ; argv[]=%s ; ignore_errors=no }" % (argv[0], " ".join(argv)))

    def show(self, unit, *props):
        name = unit if "." in unit else unit + ".service"
        self.calls.append(("show", name))
        if self.show_fails:
            raise M.ManagedUnavailable("manager_show_failed")
        if name not in self.units:
            return dict(Id=name, LoadState="not-found", ActiveState="inactive", MainPID="0")
        return dict(self.units[name])

    show_fails = False

    def kill_main(self, unit, signame="SIGTERM"):
        self.calls.append(("kill_main", unit))
        if self.kill_ends:
            self.end(unit)

    instant = None
    instant_remain = True

    def replace_invocation(self, unit):
        u = self.units[unit + ".service"]
        u.update(InvocationID=u["InvocationID"] + "-replaced", ExecMainStartTimestampMonotonic="2000")

    def finish(self, unit, code=0, *, remain=True):
        """The workload exits by itself with `code` (RemainAfterExit keeps the record)."""
        u = self.units[unit + ".service"]
        u.update(MainPID="0", ExecMainStatus=str(code), Result="success" if code == 0 else "exit-code")
        u.update(ActiveState="active", SubState="exited") if remain else u.update(
            ActiveState="inactive" if code == 0 else "failed")
        if (self.proc.root / str(self.pid)).exists():
            self.proc.remove(self.pid)

    def end(self, unit):
        u = self.units[unit + ".service"]
        u.update(ActiveState="inactive", MainPID="0")
        if (self.proc.root / str(self.pid)).exists():
            self.proc.remove(self.pid)

    def stop(self, name):
        self.calls.append(("stop", name))
        if name in self.stop_fails:
            raise M.ManagedUnavailable("manager_stop_failed")
        if name in self.units:
            self.units[name]["ActiveState"] = "inactive"


@pytest.fixture
def env(tmp_path, monkeypatch):
    proc = FakeProc(tmp_path)
    (tmp_path / "root/data").mkdir(parents=True)
    monkeypatch.setattr(B, "service_state", fake_service_state_for(proc.root))
    return tmp_path, proc


def host(tmp, manager, proc):
    h = B.Host(tmp / "root", REV, manager=manager, proc_root=proc.root)
    h.now = 0.0
    h.monotonic = lambda: h.now
    h.sleep = lambda n: setattr(h, "now", h.now + n)
    return h


# ── SP-05: identity comes from the unit's actual main process ───────────────
def test_start_binds_unit_mainpid_not_any_launcher_pid(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    assert h.process.pid == KPID and h.expected["pid"] == KPID and h.expected["start_ticks"] == KTICKS
    assert h.expected["unit"].startswith("luffy-kernel-") and h.expected["cgroup"].endswith(".service")
    assert h.expected["token"] == h.token and h.expected["revision"] == REV
    assert json.loads((h.directory / "child.json").read_text())["pid"] == KPID
    assert h.process.verify() == []
    assert [c for c in m.calls if c[0] == "run_service"] == [("run_service", h.unit)]
    assert m.argv[h.unit][1] == "-B" and "--bootstrap-token" in m.argv[h.unit]


def test_job_acceptance_alone_is_not_readiness(env):
    tmp, proc = env
    m = FakeManager(proc, activate=False)             # accepted, never reaches a main process
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable, match="identity_not_established"):
        h.start()
    assert h.process is None and h.unit is not None
    with pytest.raises(RuntimeError, match="one_launch_only"):
        h.start()                                      # a failed start can never launch a duplicate


def test_failed_start_with_absent_unit_is_provably_terminated(env):
    tmp, proc = env
    m = FakeManager(proc, activate=False)
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable):
        h.start()
    m.units[h.unit + ".service"].update(ActiveState="inactive")
    assert h.contain_stop() == dict(stop="TERMINATED", unit=h.unit, observers_retained=True)


def test_failed_start_with_unknown_manager_state_is_unknown_not_terminated(env):
    tmp, proc = env
    m = FakeManager(proc, activate=False)
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable):
        h.start()
    m.show_fails = True
    assert h.contain_stop()["stop"] == "UNKNOWN"


def test_failed_start_with_a_token_process_alive_is_not_called_terminated(env):
    tmp, proc = env
    m = FakeManager(proc, activate=False)
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable):
        h.start()
    m.units[h.unit + ".service"].update(ActiveState="inactive")
    proc.add(9999, ["x/python", "-B", "-m", "trader.kernel", "--bootstrap-token", h.token])
    assert h.contain_stop()["stop"] == "UNKNOWN"


def test_late_activating_unit_is_adopted_and_stopped_by_containment(env):
    tmp, proc = env
    m = FakeManager(proc, activate=False)
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable):
        h.start()
    group = f"/user.slice/app.slice/{h.unit}.service"
    m.units[h.unit + ".service"].update(ActiveState="active", MainPID=str(KPID))
    proc.add(KPID, m.argv[h.unit], ticks=KTICKS, cgroup=group)
    result = h.contain_stop()
    assert result["stop"] == "TERMINATED" and ("kill_main", h.unit) in m.calls


def test_manager_unavailable_fails_closed_without_unmanaged_fallback(env):
    tmp, proc = env
    m = FakeManager(proc, accept=False)
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable):
        h.start()
    assert h.process is None                           # autouse guard proves Popen never ran


def test_launcher_exit_zero_with_workload_alive_is_still_live(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()                                          # run_service returned (wrapper gone)
    assert B.service_state(h.process, h.expected) == "LIVE"
    assert h.process.poll() is None


def test_wrong_cgroup_is_mixed_identity_and_refuses_binding(env):
    tmp, proc = env
    m = FakeManager(proc, cgroup_ok=False)
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedIdentityMixed, match="cgroup_mismatch"):
        h.start()


def test_wrong_argv_for_unit_pid_is_mixed(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    proc.add(KPID, ["/x/python", "-m", "something.else"], ticks=KTICKS, cgroup=h.expected["cgroup"])
    assert "argv_mismatch" in h.process.verify()
    result = h.sample({"generation": {"boot": 0}})
    assert result["fatal"] is True and "managed_identity_mixed" in result["reasons"]


def test_stale_mainpid_and_pid_reuse_are_not_live_and_not_signalled(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    proc.add(KPID, m.argv[h.unit], ticks=KTICKS + 5, cgroup=h.expected["cgroup"])   # PID reused
    assert B.service_state(h.process, h.expected) != "LIVE"
    assert h.sample({"generation": {"boot": 0}})["reasons"] == ["owned_child_not_live"]
    with pytest.raises(M.ManagedIdentityMixed):
        h.process.send_signal(signal.SIGTERM)
    assert not any(c[0] == "kill_main" for c in m.calls)
    # unit still names this PID while the ticks differ: contradictory, never "terminated"
    assert h.process.poll() is None


def test_mainpid_moved_by_the_manager_is_mixed(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    m.units[h.unit + ".service"]["MainPID"] = str(KPID + 1)
    assert "main_pid_mismatch" in h.process.verify()
    assert h.process.poll() is None                    # pid still alive: not terminated


def test_graceful_stop_observes_actual_cessation(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    result = h.contain_stop()
    assert result["stop"] == "TERMINATED" and result["observers_retained"] is True
    assert [c for c in m.calls if c[0] == "kill_main"] == [("kill_main", h.unit)]
    assert not any(c[0] == "stop" for c in m.calls)    # no systemctl stop (which would SIGKILL)


def test_stop_timeout_reports_live_and_cannot_start_a_replacement(env):
    tmp, proc = env
    m = FakeManager(proc, kill_ends=False)             # workload ignores SIGTERM
    h = host(tmp, m, proc)
    h.start()
    result = h.contain_stop()
    assert result["stop"] == "LIVE" and result["observers_retained"] is True
    assert h.now >= B.STOP_TIMEOUT_S
    with pytest.raises(RuntimeError, match="one_launch_only"):
        h.start()


def test_unit_inactive_but_pid_present_is_not_termination(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    m.units[h.unit + ".service"].update(ActiveState="inactive", MainPID="0")
    assert h.process.poll() is None                    # /proc still shows the process


def test_manager_loss_during_stop_is_unknown_not_terminated(env):
    tmp, proc = env
    m = FakeManager(proc, kill_ends=False)
    h = host(tmp, m, proc)
    h.start()
    m.show_fails = True
    result = h.contain_stop()
    assert result["stop"] in ("LIVE", "UNKNOWN") and result["stop"] != "TERMINATED"


def test_execute_failed_start_fences_failed_and_retains_observers(env, monkeypatch):
    tmp, proc = env
    m = FakeManager(proc, accept=False)
    h = host(tmp, m, proc)
    h.prelaunch = lambda: dict(allow=True, facts={"protection": {"generation": {"boot": 1}}})
    h.observers_start = lambda: None
    fences = []
    h.fence = fences.append
    h.save = lambda r: None
    result = B.execute(h, timeout=2)
    assert result["allow"] is False and fences == ["PENDING", "FAILED"]
    assert result["containment"]["stop"] == "TERMINATED"   # nothing was ever accepted: provable
    assert result["observers_retained"] is True


# ── UserManager command shapes ─────────────────────────────────────────────
class Runner:
    def __init__(self, rc=0, out="", exc=None):
        self.calls, self.rc, self.out, self.exc = [], rc, out, exc

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(argv, self.rc, self.out, "")


def test_user_manager_builds_argv_lists_with_no_shell_and_no_sigkill(tmp_path):
    r = Runner()
    mgr = M.UserManager(runner=r)
    mgr.run_service("luffy-kernel-x", ["/p/python", "-B", "-m", "m"], cwd=tmp_path, env={"TMPDIR": "/t"})
    argv, kw = r.calls[0]
    assert argv[:3] == ["systemd-run", "--user", "--unit=luffy-kernel-x"] and "--collect" in argv
    assert "--property=SendSIGKILL=no" in argv and "--property=Restart=no" in argv
    assert argv[argv.index("--") + 1:] == ["/p/python", "-B", "-m", "m"]
    assert "shell" not in kw
    mgr.kill_main("luffy-kernel-x")
    assert r.calls[1][0] == ["systemctl", "--user", "kill", "--signal=SIGTERM", "--kill-whom=main",
                             "luffy-kernel-x.service"]
    with pytest.raises(ValueError):
        mgr.kill_main("u", "SIGKILL")


@pytest.mark.parametrize("failure", [Runner(rc=1), Runner(exc=FileNotFoundError()),
                                     Runner(exc=subprocess.TimeoutExpired("x", 1))])
def test_user_manager_unavailable_or_refusing_raises_managed_unavailable(failure, tmp_path):
    mgr = M.UserManager(runner=failure)
    for call in (lambda: mgr.run_service("u", ["a"], cwd=tmp_path), lambda: mgr.show("u", "MainPID"),
                 lambda: mgr.kill_main("u"), lambda: mgr.stop("u.timer")):
        with pytest.raises(M.ManagedUnavailable):
            call()


def test_show_parses_properties():
    r = Runner(out="ActiveState=active\nMainPID=12\nExecStart={ a=b ; c=d }\n")
    assert M.UserManager(runner=r).show("u", "ActiveState", "MainPID") == {
        "ActiveState": "active", "MainPID": "12", "ExecStart": "{ a=b ; c=d }"}


# ── dashboard / research lifetimes ────────────────────────────────────────
def test_dashboard_role_launches_a_verified_managed_unit(env):
    tmp, proc = env
    m = FakeManager(proc, pid=6001)
    result = M.launch_role("dashboard", tmp / "root", manager=m, proc_root=proc.root)
    assert result["allow"] is True and result["pid"] == 6001 and result["unit"].startswith("luffy-dashboard-")
    assert m.argv[result["unit"]][1:] == ["-m", "trader.dashboard.server"]


def test_unknown_role_and_research_requires_a_full_revision(env):
    tmp, proc = env
    with pytest.raises(ValueError):
        M.role_command("analyst", "py")
    for bad in (None, "", "abc", "Z" * 40):
        with pytest.raises(ValueError, match="revision_required"):
            M.launch_role("research", tmp / "root", revision=bad, manager=FakeManager(proc), proc_root=proc.root)


def test_cli_fails_closed_when_manager_unavailable(env, monkeypatch, capsys):
    tmp, proc = env
    real = M.UserManager
    monkeypatch.setattr(M, "UserManager", lambda: real(runner=Runner(exc=FileNotFoundError())))
    assert M.main(["launch", "--role", "dashboard", "--root", str(tmp / "root")]) == 1
    assert json.loads(capsys.readouterr().out)["allow"] is False


def test_restart_script_has_no_unmanaged_dashboard_fallback():
    text = (Path(__file__).resolve().parents[1] / "restart.sh").read_text()
    dash = text.split('if [ "$TARGET" = dashboard ]; then\n  # Dashboard lifetime', 1)[1].split("fi\n", 2)
    block = dash[0] + dash[1]
    assert "trader.observability.managed launch --role dashboard" in block
    code = "\n".join(l for l in block.splitlines() if not l.lstrip().startswith("#"))
    assert "setsid" not in code and "nohup" not in code and "exit 5" in code


# ── SP-04: verified observer generation handover ──────────────────────────
OLD = "a" * 32


def old_generation(tmp, m, name=OLD, *, units=None, cwd=None, modes=("heartbeat-only", "safety-only")):
    root = tmp / "root"
    d = root / "data/bootstrap" / name
    d.mkdir(parents=True)
    listed = units if units is not None else [f"luffy-bootstrap-{name}-{mode}" for mode in modes]
    manifest = dict(units=listed, cleanup="OWNER_ONLY")
    (d / "observers.json").write_text(json.dumps(manifest))
    for u in listed:
        mode = u.rsplit("-", 2)[-2] + "-" + u.rsplit("-", 1)[-1]
        m.add_observer(u, [str(root.resolve() / "venv/bin/python"), "-B", "-m", "scripts.monitor", "--" + mode],
                       cwd or str(root.resolve()))
    return listed


def active(m):
    return sorted(k for k, v in m.units.items() if k.endswith(".timer") and v["ActiveState"] == "active")


def test_replacement_is_verified_before_exact_superseded_pair_is_retired(env):
    tmp, proc = env
    m = FakeManager(proc)
    old = old_generation(tmp, m)
    h = host(tmp, m, proc)
    h.observers_start()
    first_stop = next(i for i, c in enumerate(m.calls) if c[0] == "stop")
    timers_made = [i for i, c in enumerate(m.calls) if c[0] == "run_timer"]
    verified = [i for i, c in enumerate(m.calls) if c == ("show", h.units[-1] + ".timer")]
    assert max(timers_made) < min(verified) < first_stop       # coverage proven before any stop
    assert active(m) == sorted(u + ".timer" for u in h.units)    # settled to one verified pair
    manifest = json.loads((h.directory / "observers.json").read_text())
    assert manifest["status"] == "COVERED" and manifest["root"] == str(h.root) and manifest["revision"] == REV
    assert {r["unit"]: r["result"] for r in manifest["superseded"]} == {u: "retired" for u in old}
    assert manifest["coverage"]["safety-only"]["period_s"] == 30


def test_repeat_startups_settle_without_stopping_the_current_pair(env):
    tmp, proc = env
    m = FakeManager(proc)
    old_generation(tmp, m)
    h1 = host(tmp, m, proc)
    h1.observers_start()
    h2 = host(tmp, m, proc)
    h2.observers_start()
    assert active(m) == sorted(u + ".timer" for u in h2.units)


def test_partial_new_setup_retains_every_existing_observer(env):
    tmp, proc = env
    m = FakeManager(proc)
    old = old_generation(tmp, m)
    m.fail_timer_after = 1                                     # second timer refused
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable):
        h.observers_start()
    assert not any(c[0] == "stop" for c in m.calls)
    assert all(u + ".timer" in active(m) for u in old)
    manifest = json.loads((h.directory / "observers.json").read_text())
    assert manifest["status"] == "PENDING" and len(manifest["units"]) == 2   # partial unit recorded


def test_new_timer_not_active_means_no_replacement_coverage_and_no_retirement(env):
    tmp, proc = env
    m = FakeManager(proc)
    old = old_generation(tmp, m)
    m.timer_inactive = True
    h = host(tmp, m, proc)
    with pytest.raises(B.ObserverCoverageIncomplete):
        h.observers_start()
    assert not any(c[0] == "stop" for c in m.calls)
    assert json.loads((h.directory / "observers.json").read_text())["status"] == "PENDING"


def test_wrong_unit_ownership_is_never_stopped(env):
    tmp, proc = env
    m = FakeManager(proc)
    foreign = "luffy-bootstrap-" + "c" * 32 + "-safety-only"          # another generation's unit
    old_generation(tmp, m, units=[foreign, "luffy-watchdog-timer", f"luffy-bootstrap-{OLD}-safety-only"])
    h = host(tmp, m, proc)
    h.observers_start()
    manifest = json.loads((h.directory / "observers.json").read_text())
    results = {r["unit"]: r["result"] for r in manifest["superseded"]}
    assert results[foreign] == "not_owned_by_manifest" and results["luffy-watchdog-timer"] == "not_owned_by_manifest"
    assert results[f"luffy-bootstrap-{OLD}-safety-only"] == "retired"
    stopped = {c[1] for c in m.calls if c[0] == "stop"}
    assert foreign + ".timer" not in stopped and "luffy-watchdog-timer.timer" not in stopped
    assert foreign + ".timer" in active(m)


def test_unlisted_running_pairs_are_untouched_no_blanket_retirement(env):
    tmp, proc = env
    m = FakeManager(proc)
    stray = [f"luffy-bootstrap-{'d' * 32}-{mode}" for mode in ("heartbeat-only", "safety-only")]
    for u in stray:
        m.add_observer(u, ["/elsewhere/python"], "/elsewhere")      # running, but in no manifest
    h = host(tmp, m, proc)
    h.observers_start()
    assert all(u + ".timer" in active(m) for u in stray)
    assert json.loads((h.directory / "observers.json").read_text())["superseded"] == []


def test_foreign_root_workingdir_or_argv_mismatch_is_retained(env):
    tmp, proc = env
    m = FakeManager(proc)
    old = old_generation(tmp, m, cwd="/another/checkout")             # unit runs from another root
    h = host(tmp, m, proc)
    h.observers_start()
    results = {r["unit"]: r["result"] for r in json.loads((h.directory / "observers.json").read_text())["superseded"]}
    assert set(results.values()) == {"identity_unverified_retained"}
    assert all(u + ".timer" in active(m) for u in old)


def test_manifest_naming_another_root_is_skipped_and_garbage_manifests_ignored(env):
    tmp, proc = env
    m = FakeManager(proc)
    old_generation(tmp, m)
    manifest_path = tmp / "root/data/bootstrap" / OLD / "observers.json"
    manifest_path.write_text(json.dumps(dict(units=[f"luffy-bootstrap-{OLD}-safety-only"], cleanup="OWNER_ONLY",
                                             root="/not/this/root")))
    bad = tmp / "root/data/bootstrap" / ("e" * 32)
    bad.mkdir()
    (bad / "observers.json").write_text("{nope")
    (tmp / "root/data/bootstrap" / "not-a-token").mkdir()
    h = host(tmp, m, proc)
    h.observers_start()
    results = {r.get("generation") or r.get("unit"): r["result"]
               for r in json.loads((h.directory / "observers.json").read_text())["superseded"]}
    assert results[OLD] == "foreign_root" and results["e" * 32] == "manifest_unusable"
    assert not any(c[0] == "stop" for c in m.calls)


def test_failed_or_unconfirmed_stop_is_retained_and_reported(env):
    tmp, proc = env
    m = FakeManager(proc)
    old_generation(tmp, m)
    m.stop_fails = {f"luffy-bootstrap-{OLD}-safety-only.timer"}
    h = host(tmp, m, proc)
    h.observers_start()                                               # a retire failure never aborts startup
    results = {r["unit"]: r["result"] for r in json.loads((h.directory / "observers.json").read_text())["superseded"]}
    assert results[f"luffy-bootstrap-{OLD}-safety-only"].startswith("retained:")
    assert results[f"luffy-bootstrap-{OLD}-heartbeat-only"] == "retired"
    assert f"luffy-bootstrap-{OLD}-safety-only.timer" in active(m)


def test_already_absent_superseded_units_are_reported_not_stopped(env):
    tmp, proc = env
    m = FakeManager(proc)
    old_generation(tmp, m)
    for k in [k for k in m.units if OLD in k]:
        del m.units[k]
    h = host(tmp, m, proc)
    h.observers_start()
    results = {r["result"] for r in json.loads((h.directory / "observers.json").read_text())["superseded"]}
    assert results == {"already_absent"} and not any(c[0] == "stop" for c in m.calls)


# ── poll: cessation needs terminal unit + original pid gone + empty cgroup ─────
def test_active_unit_with_replaced_mainpid_is_never_cessation(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    m.units[h.unit + ".service"]["MainPID"] = str(KPID + 9)      # manager now names another process
    assert h.process.poll() is None                               # original workload still alive
    with pytest.raises(M.ManagedIdentityMixed):
        h.process.send_signal(signal.SIGTERM)                     # and no signal is sent on that basis
    assert not any(c[0] == "kill_main" for c in m.calls)
    proc.remove(KPID)                                             # original gone, unit STILL active
    assert h.process.poll() is None
    assert h.contain_stop()["stop"] != "TERMINATED"


def test_terminal_unit_with_surviving_cgroup_member_is_unknown(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    m.end(h.unit)
    proc.add(7777, ["/x/python", "worker"], cgroup=h.expected["cgroup"])   # orphan in the unit's cgroup
    assert h.process.poll() is None
    proc.remove(7777)
    assert h.process.poll() == 0


def test_unreadable_cgroup_evidence_is_unknown_not_terminated(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.start()
    m.end(h.unit)
    (proc.root / "8888").mkdir()
    (proc.root / "8888" / "cgroup").mkdir()                       # reading it raises (not a file)
    assert h.process.poll() is None


# ── Research: launch receipt + bounded wait ─────────────────────────────────
RSHA = "c" * 40


def research(tmp, proc, **kw):
    m = FakeManager(proc, **kw)
    receipt = M.launch_role("research", tmp / "root", revision=RSHA, manager=m, proc_root=proc.root,
                            uuid_hex="d" * 32)
    return m, receipt


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, n):
        self.sleeps.append(n)
        self.now += max(n, 0.01)


def wait(m, tmp, proc, receipt, *, timeout=10, stop=lambda: False, clock=None, rev=RSHA, **kw):
    clock = clock or Clock()
    return M.wait_role(receipt, tmp / "root", rev, timeout, stop, manager=m, proc_root=proc.root,
                       monotonic=clock.monotonic, sleep=clock.sleep, **kw)


def test_research_launch_receipt_is_serializable_exact_identity_and_cli(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    assert json.loads(json.dumps(r)) == r
    assert r["unit"] == "luffy-research-" + "d" * 32 and r["pid"] == KPID and r["start_ticks"] == KTICKS
    assert r["root"] == str((tmp / "root").resolve()) and r["revision"] == RSHA and r["allow"] is True
    assert r["cgroup"].endswith(r["unit"] + ".service")
    assert r["argv"][1:] == ["-B", "-m", "trader.research.closure", "scheduled", "--expected-revision", RSHA]
    assert "RemainAfterExit=yes" in m.properties[r["unit"]]
    assert r["invocation_id"] == "inv-" + r["unit"] + "-1" and r["exec_start_monotonic"] == "1000"
    assert r["completed"] is False


def test_exit_zero_unit_success_needs_manager_result_and_proof_of_cessation(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.finish(r["unit"], 0)
    out = wait(m, tmp, proc, r)
    assert out["state"] == "SUCCEEDED" and out["result"] == "success" and out["exit_status"] == "0"
    assert not any(c[0] in ("kill_main", "stop") for c in m.calls)


def test_failed_job_is_failed_not_success(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.finish(r["unit"], 2)                                         # e.g. ClosureRefused exit 2
    out = wait(m, tmp, proc, r)
    assert out["state"] == "FAILED" and out["exit_status"] == "2" and out["result"] == "exit-code"


def test_failed_unit_state_without_remain_is_failed(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.finish(r["unit"], 1, remain=False)
    assert wait(m, tmp, proc, r)["state"] == "FAILED"


def test_accepted_job_and_running_process_is_running_then_times_out_without_cancelling(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    clock = Clock()
    out = wait(m, tmp, proc, r, timeout=5, clock=clock)
    assert out["state"] == "RUNNING" and out["reason"] == "timeout" and out["cancelled"] is False
    assert clock.now >= 5 and clock.sleeps                          # bounded polling, fake clock
    assert not any(c[0] in ("kill_main", "stop", "run_service") for c in m.calls[2:])
    assert [c for c in m.calls if c[0] == "run_service"] == [("run_service", r["unit"])]   # no restart


def test_stop_request_returns_running_explicitly_and_leaves_the_unit(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    out = wait(m, tmp, proc, r, stop=lambda: True)
    assert out["state"] == "RUNNING" and out["reason"] == "stop_requested" and out["cancelled"] is False
    assert not any(c[0] in ("kill_main", "stop") for c in m.calls)
    assert (proc.root / str(KPID)).exists() and m.units[r["unit"] + ".service"]["ActiveState"] == "active"


def test_mainpid_zero_alone_is_not_completion(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.units[r["unit"] + ".service"]["MainPID"] = "0"               # workload still in /proc
    out = wait(m, tmp, proc, r, timeout=1)
    assert out["state"] == "UNKNOWN" and out["reason"] == "identity_mixed"


def test_process_gone_but_no_recorded_result_is_unknown(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.finish(r["unit"], 0)
    m.units[r["unit"] + ".service"]["Result"] = ""
    out = wait(m, tmp, proc, r, timeout=1)
    assert out["state"] == "UNKNOWN" and out["reason"] == "terminal_proof_missing"


def test_unit_collected_with_no_result_is_unknown_not_success(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    proc.remove(KPID)
    del m.units[r["unit"] + ".service"]
    out = wait(m, tmp, proc, r, timeout=1)
    assert out["state"] == "UNKNOWN" and out["reason"] == "unit_absent_without_result"


@pytest.mark.parametrize("mutate", ["exec_pid", "cgroup", "pid_reused"])
def test_mixed_identity_is_unknown_and_never_success(env, mutate):
    tmp, proc = env
    m, r = research(tmp, proc)
    u = m.units[r["unit"] + ".service"]
    if mutate == "exec_pid":
        u["ExecMainPID"] = str(KPID + 1)
        m.finish(r["unit"], 0)
        u["ExecMainPID"] = str(KPID + 1)
    elif mutate == "cgroup":
        u["ControlGroup"] = "/user.slice/other.service"
        m.finish(r["unit"], 0)
    else:
        proc.add(KPID, ["/x/python"], ticks=KTICKS + 3, cgroup=r["cgroup"])
        u["MainPID"] = "0"
    out = wait(m, tmp, proc, r, timeout=1)
    assert out["state"] == "UNKNOWN" and out["reason"] == "identity_mixed" and out["mixed"] is True


def test_surviving_cgroup_workload_blocks_success(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.finish(r["unit"], 0)
    proc.add(7001, ["/x/python", "child"], cgroup=r["cgroup"])
    out = wait(m, tmp, proc, r, timeout=1)
    assert out["state"] == "UNKNOWN" and out["reason"] == "cgroup_workload_survives"


def test_unreadable_manager_is_unknown_after_the_deadline_not_failure_or_success(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.show_fails = True
    clock = Clock()
    out = wait(m, tmp, proc, r, timeout=3, clock=clock)
    assert out["state"] == "UNKNOWN" and out["reason"] == "manager_show_failed" and clock.now >= 3
    m.show_fails = False                                            # recovers: same unit observed again
    m.finish(r["unit"], 0)
    assert wait(m, tmp, proc, r)["state"] == "SUCCEEDED"


@pytest.mark.parametrize("field,value", [("revision", "e" * 40), ("root", "/elsewhere"), ("unit", "other-unit"),
                                         ("pid", "x")])
def test_receipt_that_does_not_match_root_revision_or_shape_is_refused(env, field, value):
    tmp, proc = env
    m, r = research(tmp, proc)
    out = wait(m, tmp, proc, dict(r, **{field: value}))
    assert out["state"] == "UNKNOWN" and out["reason"] == "receipt_mismatch"
    assert wait(m, tmp, proc, r, rev="f" * 40)["reason"] == "receipt_mismatch"


def test_release_only_drops_an_exited_unit_and_never_signals(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    assert M.release_role(r, manager=m, proc_root=proc.root) is False       # still running
    assert not any(c[0] in ("stop", "kill_main") for c in m.calls)
    m.finish(r["unit"], 0)
    assert M.release_role(r, manager=m, proc_root=proc.root) is True
    assert ("stop", r["unit"]) in m.calls and not any(c[0] == "kill_main" for c in m.calls)


def test_research_launch_failures_are_fail_closed_with_no_detached_fallback(env):
    tmp, proc = env
    with pytest.raises(M.ManagedUnavailable):
        M.launch_role("research", tmp / "root", revision=RSHA, manager=FakeManager(proc, accept=False),
                      proc_root=proc.root)
    clock = Clock()
    with pytest.raises(M.ManagedUnavailable, match="identity_not_established"):
        M.launch_role("research", tmp / "root", revision=RSHA, manager=FakeManager(proc, activate=False),
                      proc_root=proc.root, sleep=clock.sleep, monotonic=clock.monotonic)


# ── SP04: replacement coverage must match exact root/command, not just be active ──
@pytest.mark.parametrize("bad", ["cwd", "argv"])
def test_replacement_with_wrong_root_or_command_never_retires_the_old_pair(env, bad):
    tmp, proc = env
    m = FakeManager(proc)
    old = old_generation(tmp, m)
    m.bad_replacement = bad                                         # timers ACTIVE but wrong identity
    h = host(tmp, m, proc)
    with pytest.raises(B.ObserverCoverageIncomplete):
        h.observers_start()
    assert not any(c[0] == "stop" for c in m.calls)
    assert all(u + ".timer" in active(m) for u in old)
    manifest = json.loads((h.directory / "observers.json").read_text())
    assert manifest["status"] == "PENDING" and "superseded" not in manifest


def test_covered_manifest_records_exact_root_and_command(env):
    tmp, proc = env
    m = FakeManager(proc)
    h = host(tmp, m, proc)
    h.observers_start()
    cov = json.loads((h.directory / "observers.json").read_text())["coverage"]
    assert cov["safety-only"]["command"] == [h.python, "-B", "-m", "scripts.monitor", "--safety-only"]
    assert cov["safety-only"]["root"] == str(h.root)


# ── fast completion: the job finished before a live MainPID could be seen ───────
def instant(tmp, proc, code, **kw):
    m = FakeManager(proc, **kw)
    m.instant = code
    r = M.launch_role("research", tmp / "root", revision=RSHA, manager=m, proc_root=proc.root,
                      uuid_hex="d" * 32, sleep=lambda n: pytest.fail("polled for a live pid"),
                      monotonic=lambda: 0.0)
    return m, r


def test_instant_success_is_a_completion_receipt_without_invented_proc_identity(env):
    tmp, proc = env
    m, r = instant(tmp, proc, 0)
    assert r["completed"] is True and r["start_ticks"] is None and r["pid"] == KPID
    assert r["invocation_id"] and r["exec_start_monotonic"] == "1000" and r["revision"] == RSHA
    assert json.loads(json.dumps(r)) == r
    assert not (proc.root / str(KPID)).exists()                       # nothing was ever in /proc
    out = wait(m, tmp, proc, r)
    assert out["state"] == "SUCCEEDED" and out["exit_status"] == "0"
    assert not any(c[0] in ("kill_main", "stop") for c in m.calls)


def test_instant_failure_is_failed_and_fast_inactive_units_are_recognised(env):
    tmp, proc = env
    m, r = instant(tmp, proc, 2)
    assert r["completed"] is True
    assert wait(m, tmp, proc, r)["state"] == "FAILED"
    tmp2 = tmp / "again"
    (tmp2 / "root/data").mkdir(parents=True)
    proc2 = FakeProc(tmp2)
    m2 = FakeManager(proc2)
    m2.instant, m2.instant_remain = 0, False                          # collected-as-inactive variant
    r2 = M.launch_role("research", tmp2 / "root", revision=RSHA, manager=m2, proc_root=proc2.root,
                       uuid_hex="e" * 32, monotonic=lambda: 0.0, sleep=lambda n: None)
    assert r2["completed"] is True
    assert M.wait_role(r2, tmp2 / "root", RSHA, 5, manager=m2, proc_root=proc2.root)["state"] == "SUCCEEDED"


def test_accepted_but_unrecorded_or_absent_job_is_not_a_completion(env):
    tmp, proc = env
    m = FakeManager(proc, activate=False)                              # accepted, nothing recorded
    clock = Clock()
    with pytest.raises(M.ManagedUnavailable, match="identity_not_established"):
        M.launch_role("research", tmp / "root", revision=RSHA, manager=m, proc_root=proc.root,
                      monotonic=clock.monotonic, sleep=clock.sleep)
    m2 = FakeManager(proc)
    m2.instant = 0
    real = m2.run_service

    def vanish(unit, *a, **k):
        real(unit, *a, **k)
        del m2.units[unit + ".service"]                                # collected: no evidence at all
    m2.run_service = vanish
    clock = Clock()
    with pytest.raises(M.ManagedUnavailable, match="identity_not_established"):
        M.launch_role("research", tmp / "root", revision=RSHA, manager=m2, proc_root=proc.root,
                      monotonic=clock.monotonic, sleep=clock.sleep)


@pytest.mark.parametrize("missing", ["Result", "InvocationID", "ExecMainStartTimestampMonotonic"])
def test_completion_without_recorded_result_or_invocation_is_not_established(env, missing):
    tmp, proc = env
    m = FakeManager(proc)
    m.instant = 0
    real = m.run_service

    def strip(unit, *a, **k):
        real(unit, *a, **k)
        m.units[unit + ".service"][missing] = "" if missing != "ExecMainStartTimestampMonotonic" else "0"
    m.run_service = strip
    clock = Clock()
    with pytest.raises(M.ManagedUnavailable, match="identity_not_established"):
        M.launch_role("research", tmp / "root", revision=RSHA, manager=m, proc_root=proc.root,
                      monotonic=clock.monotonic, sleep=clock.sleep)


def test_completed_unit_with_other_command_or_root_or_cgroup_survivor_refuses(env):
    tmp, proc = env
    for tweak, reason in ((lambda u, g: u.update(ExecStart="{ path=/x ; argv[]=/x other ; }"), "command_mismatch"),
                          (lambda u, g: u.update(WorkingDirectory="/elsewhere"), "command_mismatch"),
                          (lambda u, g: proc.add(7003, ["/x/python"], cgroup=g), "cgroup_workload_survives")):
        m = FakeManager(proc)
        m.instant = 0
        real = m.run_service

        def run(unit, *a, _real=real, _m=m, _t=tweak, **k):
            _real(unit, *a, **k)
            _t(_m.units[unit + ".service"], _m.units[unit + ".service"]["ControlGroup"])
        m.run_service = run
        with pytest.raises(M.ManagedIdentityMixed, match=reason):
            M.launch_role("research", tmp / "root", revision=RSHA, manager=m, proc_root=proc.root,
                          monotonic=lambda: 0.0, sleep=lambda n: None)
        if (proc.root / "7003").exists():
            proc.remove(7003)


def test_replaced_invocation_refuses_a_fast_completion_receipt(env):
    tmp, proc = env
    m, r = instant(tmp, proc, 0)
    m.replace_invocation(r["unit"])                                    # the unit was re-run / replaced
    out = wait(m, tmp, proc, r)
    assert out["state"] == "UNKNOWN" and out["reason"] == "identity_mixed" and out["mixed"] is True


def test_replaced_invocation_refuses_a_live_receipt_and_fast_unit_that_restarted_alive(env):
    tmp, proc = env
    m, r = research(tmp, proc)
    m.finish(r["unit"], 0)
    m.replace_invocation(r["unit"])
    assert wait(m, tmp, proc, r)["reason"] == "identity_mixed"
    m2, r2 = instant(tmp, proc, 0)
    m2.units[r2["unit"] + ".service"].update(MainPID="9999", ExecMainPID="9999")   # something runs again
    assert wait(m2, tmp, proc, r2)["state"] == "UNKNOWN"


def test_fast_receipt_mismatch_shapes_are_refused(env):
    tmp, proc = env
    m, r = instant(tmp, proc, 0)
    for bad in (dict(invocation_id=""), dict(invocation_id=None), dict(exec_start_monotonic="0"),
                dict(start_ticks="x"), dict(cgroup=None), dict(revision="e" * 40)):
        assert wait(m, tmp, proc, dict(r, **bad))["reason"] == "receipt_mismatch"
    assert wait(m, tmp, proc, {k: v for k, v in r.items() if k != "invocation_id"})["reason"] == "receipt_mismatch"


def test_release_uses_the_invocation_binding_for_fast_receipts(env):
    tmp, proc = env
    m, r = instant(tmp, proc, 0)
    m.replace_invocation(r["unit"])
    assert M.release_role(r, manager=m, proc_root=proc.root) is False
    assert not any(c[0] == "stop" for c in m.calls)


def test_kernel_startup_still_requires_a_live_verified_main_process(env):
    tmp, proc = env
    m = FakeManager(proc)
    m.instant = 0                                                      # unit already "done": not a kernel start
    h = host(tmp, m, proc)
    with pytest.raises(M.ManagedUnavailable, match="identity_not_established"):
        h.start()
    assert h.process is None
