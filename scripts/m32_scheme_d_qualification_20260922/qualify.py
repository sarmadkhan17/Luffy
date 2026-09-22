#!/usr/bin/env python3
"""RNG-free host / filesystem qualification for the hardened M3.2 Scheme D shard layer (bundle v3).

No random generator is constructed (worlds are the layer's arithmetic-token fixtures; every probe payload is a hash
ramp), no validation world is generated, no owner authorization is read or created, and neither the bundle nor the
protocol is touched.  The tool is bound to the exact bundle-v3 and shard-layer SHA-256 below and fails closed (a FAIL
receipt, exit 2) if either differs.

Commands
  host          run every check for THIS host against ``--dir`` (the target directory / shared mount) and write one
                hash-bound (optionally HMAC-signed) receipt
  flock-peer    cross-host ``flock`` exclusivity protocol between two hosts sharing ``--dir`` (also run by ``host
                --flock-peer-label``); same-kernel peers are recorded as SAME_HOST_PEER, never as cross-host evidence
  combine       verify receipts and emit the combined report (compatibility classes, filesystem capability,
                qualified worker count, readiness of the execution hosts)
  calibrate     developer-only: record the reference-host FastMetric proxy speed used to scale throughput

Checks per host (see ``CHECK_ORDER``): bindings, environment gate + lock hash, CPU features, NumPy/OpenBLAS identity,
fixture canary (2-worker dynamic run + merge on the target mount == sequential runner bytes), numeric canary,
FastMetric bit identity vs the reference statistic, hard link + EEXIST, flock exclusivity, renameat2 no-replace (or a
verified fallback), file + directory fsync, atomic publish, and measured 1-worker FastMetric throughput with a worker
scaling ladder that yields the qualified worker count.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import hmac
import json
import os
import platform
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LAYER_DIR = ROOT / "scripts/m32_scheme_d_sharding_20260921"
BUNDLE = ROOT / "scripts/m32_scheme_d_validation_bundle_v3_20260921"
sys.path.insert(0, str(LAYER_DIR))
import shard_layer as SL  # noqa: E402  (stdlib-only at import; the runner and numpy load lazily)

EXPECTED_BUNDLE_SHA256 = "eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01"
EXPECTED_LAYER_SHA256 = "68195fb6f8548722d3055a00e5892d9d5fdc1caaab0735b3f706e05bc1ab0657"
SCHEMA_RECEIPT = "m3.2-scheme-d-host-qualification-receipt.v1"
SCHEMA_REPORT = "m3.2-scheme-d-qualification-report.v1"
SCHEMA_CALIBRATION = "m3.2-scheme-d-qualification-reference-calibration.v1"
TOOL_PATH = Path(__file__).resolve()
CALIBRATION_PATH = HERE / "reference_calibration.json"
PHASE90 = ROOT / "scripts/m32_scheme_d_phase90_fast_benchmark_20260921/benchmark_summary.json"
ENV = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}

VOLATILE_FS = {"tmpfs", "ramfs"}
UNSUPPORTED_FS = {"9p", "vboxsf", "vmhgfs", "fuse.vmhgfs-fuse", "fuse.sshfs", "fuse.s3fs", "fuse.gcsfuse", "fuse.rclone",
                  "cifs", "smb3", "smbfs", "davfs", "fuse.davfs", "afs"}
NETWORK_FS = {"nfs", "nfs4", "cephfs", "ceph", "lustre", "gpfs", "glusterfs", "fuse.glusterfs", "beegfs", "ocfs2", "gfs2"}
LOCAL_FS = {"ext4", "ext3", "xfs", "btrfs", "zfs", "f2fs", "jfs", "reiserfs"}
CHECK_ORDER = ("bindings", "environment_gate", "environment_lock_hash", "cpu_features", "numpy_openblas_identity",
               "filesystem_policy", "hardlink_eexist", "flock_exclusivity", "flock_cross_host", "renameat2_noreplace",
               "fsync_file_and_dir", "atomic_publish", "canary_fixture", "canary_numeric", "fastmetric_bit_identity",
               "fastmetric_throughput", "worker_scaling")
MIN_EFFICIENCY, MEMORY_HEADROOM, RSS_SAFETY = 0.60, 0.80, 1.5
CALIBRATION_DRAWS = 300
THROUGHPUT_PROCESSES = 7


class QualificationError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------ helpers
def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def host_identity() -> dict:
    machine = _read("/etc/machine-id") or _read("/var/lib/dbus/machine-id") or ""
    boot = _read("/proc/sys/kernel/random/boot_id") or ""
    return {"hostname": socket.gethostname(), "machine_id_sha256": sha256_bytes(machine.encode()) if machine else None,
            "boot_id_sha256": sha256_bytes(boot.encode()) if boot else None, "kernel": platform.release(),
            "machine": platform.machine(), "system": platform.system()}


def same_kernel(a: dict, b: dict) -> bool:
    return bool(a.get("boot_id_sha256")) and a.get("boot_id_sha256") == b.get("boot_id_sha256")


def _unescape(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def mount_of(path) -> dict:
    """The mount (fstype, source, options) holding ``path`` from /proc/self/mountinfo (longest mount-point prefix)."""
    real, best = os.path.realpath(path), None
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, _, right = line.partition(" - ")
        f, r = left.split(), right.split()
        mp = _unescape(f[4])
        if real == mp or real.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) >= len(best["mount_point"]):
                best = {"mount_point": mp, "fstype": r[0], "source": _unescape(r[1]), "options": f[5], "super_options": r[2]}
    if best is None:
        raise QualificationError("mount_not_found")
    return best


def _fsync_dir(path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def payload_bytes(tag: str, size: int) -> bytes:
    """Deterministic non-random payload: a SHA-256 hash ramp."""
    block, out, i = hashlib.sha256(tag.encode()).digest(), bytearray(), 0
    while len(out) < size:
        out += hashlib.sha256(block + i.to_bytes(8, "big")).digest()
        i += 1
    return bytes(out[:size])


def seal(body: dict, hmac_key: bytes | None) -> dict:
    digest = sha256_bytes(canon(body).encode())
    out = {**body, "receipt_sha256": digest}
    out["signature"] = ({"scheme": "hmac-sha256", "value": hmac.new(hmac_key, digest.encode(), hashlib.sha256).hexdigest()}
                        if hmac_key else {"scheme": "none", "note": "hash-bound only: no HMAC key was provisioned"})
    return out


def verify_seal(receipt: dict, hmac_key: bytes | None) -> list[str]:
    problems, body = [], {k: v for k, v in receipt.items() if k not in ("receipt_sha256", "signature")}
    if receipt.get("receipt_sha256") != sha256_bytes(canon(body).encode()):
        problems.append("receipt_hash_mismatch")
    sig = receipt.get("signature") or {}
    if hmac_key:
        want = hmac.new(hmac_key, str(receipt.get("receipt_sha256")).encode(), hashlib.sha256).hexdigest()
        if sig.get("scheme") != "hmac-sha256" or not hmac.compare_digest(str(sig.get("value")), want):
            problems.append("signature_invalid")
    elif sig.get("scheme") == "hmac-sha256":
        problems.append("signature_present_but_no_key_supplied")
    return problems


def run_role(role: str, *args, timeout: float = 120, env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(TOOL_PATH), "_role", role, *map(str, args)], env=env or ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(proc: subprocess.Popen, timeout: float = 120) -> dict:
    out, err = proc.communicate(timeout=timeout)
    if proc.returncode != 0:
        raise QualificationError(f"role_failed:{err.strip()[-200:]}")
    return json.loads(out.strip().splitlines()[-1])


def wait_for(predicate, timeout: float, what: str, poll: float = 0.01):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        value = predicate()
        if value:
            return value
        time.sleep(poll)
    raise QualificationError(f"timeout:{what}")


def wait_ready(paths: list[Path], timeout: float = 60) -> None:
    wait_for(lambda: all(p.exists() for p in paths), timeout, "children_ready")


# ------------------------------------------------------------------------------------------------ role processes
def _role_hold(a) -> None:
    fd = os.open(a.file, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("HELD", flush=True)
    time.sleep(3600)


def _role_probe(a) -> None:
    fd = os.open(a.file, os.O_CREAT | os.O_RDWR, 0o644)
    start, err = time.perf_counter(), None
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(canon({"blocked": False, "errno": err, "waited_seconds": time.perf_counter() - start}))
            return
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                print(canon({"blocked": None, "errno": exc.errno, "error": str(exc)}))
                return
            err = exc.errno
            if a.wait_acquire <= 0 or time.perf_counter() - start > a.wait_acquire:
                print(canon({"blocked": True, "errno": err, "waited_seconds": time.perf_counter() - start}))
                return
            time.sleep(0.05)


def _role_link(a) -> None:
    Path(a.dir, f"ready.{a.index}").write_bytes(b"")
    wait_for(lambda: Path(a.go).exists(), 60, "go")
    try:
        os.link(Path(a.dir) / f"src{a.index}", a.dst)
        print(canon({"index": a.index, "result": "won"}))
    except FileExistsError as exc:
        print(canon({"index": a.index, "result": "EEXIST", "errno": exc.errno}))
    except OSError as exc:
        print(canon({"index": a.index, "result": "error", "errno": exc.errno}))


def _role_publish(a) -> None:
    if a.freeze:                                                      # same host name, PID and clock reading everywhere
        os.getpid = lambda: 1
        SL.socket.gethostname = lambda: "qualhost"
        SL.time.time_ns = lambda: 123456789
    data = payload_bytes(f"payload{a.index}", a.size)
    Path(a.dir, f"ready.{a.index}").write_bytes(b"")
    wait_for(lambda: Path(a.go).exists(), 60, "go")
    try:
        SL.publish_exclusive(Path(a.target), data)
        print(canon({"index": a.index, "result": "won"}))
    except SL.ShardError as exc:
        print(canon({"index": a.index, "result": str(exc)}))


def _role_reader(a) -> None:
    Path(a.dir, f"ready.reader{a.index}").write_bytes(b"")
    good = set(a.hashes.split(","))
    reads = torn = absent = 0
    while not Path(a.stop).exists():
        try:
            data = Path(a.target).read_bytes()
        except FileNotFoundError:
            absent += 1
            continue
        reads += 1
        if len(data) != a.size or sha256_bytes(data) not in good:
            torn += 1
    print(canon({"reads": reads, "torn": torn, "absent": absent}))


def _role_dirreader(a) -> None:
    Path(a.dir, f"ready.reader{a.index}").write_bytes(b"")
    full = partial = 0
    while not Path(a.stop).exists():
        try:
            count = len(os.listdir(a.target))
        except (FileNotFoundError, NotADirectoryError):
            continue
        if count == a.expect:
            full += 1
        else:
            partial += 1
    print(canon({"full_listings": full, "partial_listings": partial}))


def _role_tput(a) -> None:
    fm = FastWorkload()
    Path(a.dir, f"ready.tput{a.index}").write_bytes(b"")
    wait_for(lambda: Path(a.go).exists(), 120, "go")
    runs = [fm.measure(a.draws) for _ in range(a.reps or 1)]
    import resource
    per_draw = median([r["seconds_per_draw_end_to_end"] for r in runs])
    print(canon({"draws": a.draws, "reps": len(runs), "seconds_per_draw_end_to_end": per_draw, "draws_per_second": 1.0 / per_draw,
                 "rep_draws_per_second": [r["draws_per_second"] for r in runs], "encode_seconds": fm.encode_seconds,
                 "tested_hypotheses": runs[0]["tested_hypotheses"], "cpu_seconds": median([r["cpu_seconds"] for r in runs]),
                 "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}))


ROLES = {"hold": _role_hold, "probe": _role_probe, "link": _role_link, "publish": _role_publish, "reader": _role_reader,
         "dirreader": _role_dirreader, "tput": _role_tput}


# ------------------------------------------------------------------------------------------------ FastMetric workload
class FastWorkload:
    """One production-size token world (real ``generate_world`` on arithmetic tokens, all 384 hypotheses tested) and the
    exact per-draw work of ``benchmark_world``'s timed loop.  RNG-free and non-inferential."""

    def __init__(self, draws_prepared: int = 0):
        sys.path.insert(0, str(LAYER_DIR))
        import shard_fixture as F
        self.R = SL.runner()
        self.R.open_statistic_gate(self.R.equivalence_gate_static())
        reg = F.StubRegistry(self.R.MASTER_SEED)
        self.world = self.R.generate_world(1, 0, 1, reg.generator(90, 0, 1, 1, 0), reg.generator(90, 0, 1, 1, 1))
        self.reasons = self.R.hypothesis_refusals(self.world)
        self.tested = [h for h, r in enumerate(self.reasons) if r is None]
        self.groups = self.R.strata_all(self.world.block_strata)
        self.perm = reg.generator(90, 0, 1, 1, 2)
        t0 = time.perf_counter()
        self.fm = self.R.FastMetric.from_world(self.world)
        self.encode_seconds = time.perf_counter() - t0

    def measure(self, draws: int) -> dict:
        R = self.R
        t0, c0 = time.perf_counter(), time.process_time()
        maps = [R.draw_block_map(self.perm, self.groups) for _ in range(draws)]
        map_seconds = time.perf_counter() - t0
        R.fast_vector(self.fm, None, self.tested)                     # actual-statistic vector (as the timed loop's setup)
        t1, c1 = time.perf_counter(), time.process_time()
        for m in maps:
            R.fast_vector(self.fm, m, self.tested)
        loop = time.perf_counter() - t1
        per_draw = loop / draws + map_seconds / draws
        return {"draws": draws, "tested_hypotheses": len(self.tested), "loop_seconds": loop, "map_seconds": map_seconds,
                "encode_seconds": self.encode_seconds, "seconds_per_draw_end_to_end": per_draw,
                "draws_per_second": 1.0 / per_draw, "cpu_seconds": time.process_time() - c0,
                "wall_seconds": time.perf_counter() - t0}


def median(values):
    v = sorted(values)
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


# ------------------------------------------------------------------------------------------------ checks
def ok(status="PASS", **detail) -> dict:
    return {"status": status, "detail": detail}


def check_bindings(ctx) -> dict:
    layer_sha, bundle_sha = sha256_file(SL.LAYER_PATH), sha256_file(BUNDLE / "BUNDLE_MANIFEST.json")
    manifest = json.loads((LAYER_DIR / "LAYER_MANIFEST.json").read_text())
    problems = []
    if layer_sha != EXPECTED_LAYER_SHA256:
        problems.append("shard_layer_sha_mismatch")
    if bundle_sha != EXPECTED_BUNDLE_SHA256:
        problems.append("bundle_sha_mismatch")
    if manifest.get("shard_layer_sha256") != layer_sha or manifest.get("bundle_sha256") != bundle_sha:
        problems.append("layer_manifest_disagrees")
    detail = {"bundle_sha256": bundle_sha, "shard_layer_sha256": layer_sha, "expected_bundle_sha256": EXPECTED_BUNDLE_SHA256,
              "expected_shard_layer_sha256": EXPECTED_LAYER_SHA256, "tool_sha256": sha256_file(TOOL_PATH)}
    if problems:
        return {"status": "FAIL", "detail": {**detail, "problems": problems}}
    R = SL.runner()
    try:
        R.verify_bundle()
        static = R.equivalence_gate_static()
    except Exception as exc:                                          # noqa: BLE001
        return {"status": "FAIL", "detail": {**detail, "problems": [f"{type(exc).__name__}:{exc}"]}}
    detail.update(runner_sha256=sha256_file(R.__file__), fast_metric_module_sha256=static["module_sha256"],
                  equivalence_comparisons=static["comparisons"])
    return ok(**detail)


def check_environment_gate(ctx) -> dict:
    R = SL.runner()
    try:
        env = R.assert_environment()
    except Exception as exc:                                          # noqa: BLE001
        return {"status": "FAIL", "detail": {"error": f"{type(exc).__name__}:{exc}", "OPENBLAS_NUM_THREADS":
                                             os.environ.get("OPENBLAS_NUM_THREADS")}}
    return ok(python=env["python"], numpy=env["numpy"], openblas_threads=env["openblas_threads"])


def installed_distributions_sha256() -> tuple[str, int]:
    import importlib.metadata as md
    freeze = sorted(f"{d.metadata['Name']}=={d.version}" for d in md.distributions())
    return sha256_bytes("\n".join(freeze).encode()), len(freeze)


def check_environment_lock(ctx) -> dict:
    lock_path = BUNDLE / "artifacts/environment_lock.json"
    manifest = json.loads((BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    rel = str(lock_path.relative_to(ROOT))
    lock_sha = sha256_file(lock_path)
    lock = json.loads(lock_path.read_text())
    if manifest["files_sha256"].get(rel) != lock_sha:
        return {"status": "FAIL", "detail": {"environment_lock_sha256": lock_sha, "problem": "lock_not_the_bundle_pinned_file"}}
    dist_sha, count = installed_distributions_sha256()
    warn = []
    if dist_sha != lock.get("installed_distributions_sha256"):
        warn.append("installed_distribution_set_differs_from_lock (informational in the lock)")
    if platform.machine() != lock["platform"]["machine"] or platform.system() != lock["platform"]["system"]:
        warn.append("platform_differs_from_lock (informational; canaries decide numeric compatibility)")
    return ok("WARN" if warn else "PASS", environment_lock_sha256=lock_sha, installed_distributions_sha256=dist_sha,
              installed_distribution_count=count, lock_installed_distributions_sha256=lock.get("installed_distributions_sha256"),
              warnings=warn)


def check_cpu(ctx) -> dict:
    p = ctx["provenance"]
    flags = set(p["cpu_flags"])
    simd = "avx512" if any(f.startswith("avx512") for f in flags) else "avx2" if "avx2" in flags else "other"
    if not p["cpu_model"] or not flags:
        return {"status": "WARN", "detail": {"cpu_model": p["cpu_model"], "problem": "cpuinfo_unreadable"}}
    return ok(cpu_model=p["cpu_model"], simd_class=simd, cpu_flag_count=len(flags), cpu_flags_sha256=SL.sha256_bytes(
        SL.canon(p["cpu_flags"]).encode()), numpy_cpu_features=p["numpy_cpu_features"], machine=platform.machine(),
              logical_cpus=os.cpu_count(), affinity_cpus=len(os.sched_getaffinity(0)))


def check_blas(ctx) -> dict:
    p, lock = ctx["provenance"], json.loads((BUNDLE / "artifacts/environment_lock.json").read_text())
    blas = p["blas"]
    problems = []
    build = blas.get("build") or {}
    if build.get("name") != lock["blas"]["name"] or build.get("version") != lock["blas"]["version"]:
        problems.append("blas_build_differs_from_lock")
    if not blas.get("runtime_corename"):
        problems.append("blas_runtime_core_unreadable")
    detail = {"numpy": p["environment"]["numpy"], "blas_build": build, "blas_runtime_library": blas.get("runtime_library"),
              "blas_runtime_library_sha256": blas.get("runtime_library_sha256"), "blas_runtime_corename": blas.get("runtime_corename"),
              "openblas_num_threads": p["environment"]["openblas_threads"], "openblas_coretype_env": p["environment"].get("openblas_coretype"),
              "hardware_identity": p["hardware_identity"], "problems": problems}
    return {"status": "FAIL" if problems else "PASS", "detail": detail}


def check_fs_policy(ctx) -> dict:
    m = ctx["mount"]
    fstype, opts = m["fstype"], set(m["options"].split(",")) | set(m["super_options"].split(","))
    detail = {"mount": m, "class": None}
    if fstype in VOLATILE_FS:
        return {"status": "FAIL", "detail": {**detail, "class": "volatile", "problem": "fs_type_volatile_not_durable"}}
    if fstype in UNSUPPORTED_FS or (fstype.startswith("fuse") and fstype not in ("fuse.glusterfs", "fuseblk")):
        return {"status": "FAIL", "detail": {**detail, "class": "unsupported", "problem": "fs_type_unsupported_semantics"}}
    warn = []
    if opts & {"nobarrier", "barrier=0", "data=writeback"}:
        warn.append("write_ordering_relaxed_mount_option")
    if fstype == "overlay":
        warn.append("overlay_fs_fsync_lands_on_upper_layer")
    cls = "network" if fstype in NETWORK_FS else "local" if fstype in LOCAL_FS else "unknown"
    if cls == "unknown":
        warn.append("fs_type_not_in_known_lists_probes_decide")
    return {"status": "WARN" if warn else "PASS", "detail": {**detail, "class": cls, "warnings": warn}}


def check_hardlink(ctx) -> dict:
    d = ctx["work"] / "link"
    d.mkdir()
    a, b, c = d / "a", d / "b", d / "c"
    a.write_bytes(b"A" * 4096)
    c.write_bytes(b"C")
    try:
        os.link(a, b)
    except OSError as exc:
        return {"status": "FAIL", "detail": {"problem": "hard_links_unsupported", "errno": exc.errno}}
    sa, sb = os.stat(a), os.stat(b)
    if not (sa.st_ino == sb.st_ino and sa.st_nlink == 2 and b.read_bytes() == a.read_bytes()):
        return {"status": "FAIL", "detail": {"problem": "link_not_same_inode"}}
    detail = {"nlink_after_link": sa.st_nlink}
    for target, label in ((b, "existing_link"), (c, "existing_other_file")):
        try:
            os.link(a, target)
            return {"status": "FAIL", "detail": {"problem": f"link_over_{label}_succeeded"}}
        except FileExistsError as exc:
            if exc.errno != errno.EEXIST:
                return {"status": "FAIL", "detail": {"problem": "wrong_errno", "errno": exc.errno}}
    if c.read_bytes() != b"C":
        return {"status": "FAIL", "detail": {"problem": "existing_file_modified_by_failed_link"}}
    b.unlink()
    if a.read_bytes() != b"A" * 4096 or os.stat(a).st_nlink != 1:
        return {"status": "FAIL", "detail": {"problem": "unlink_of_one_name_affected_the_other"}}
    n, dst, go = 8, d / "race_target", d / "go"
    for i in range(n):
        (d / f"src{i}").write_bytes(payload_bytes(f"src{i}", 2048))
    procs = [run_role("link", "--dir", d, "--index", i, "--dst", dst, "--go", go) for i in range(n)]
    wait_ready([d / f"ready.{i}" for i in range(n)])
    go.write_bytes(b"")
    res = [finish(p) for p in procs]
    won = [r for r in res if r["result"] == "won"]
    eexist = [r for r in res if r["result"] == "EEXIST" and r["errno"] == errno.EEXIST]
    if len(won) != 1 or len(eexist) != n - 1 or dst.read_bytes() != payload_bytes(f"src{won[0]['index']}", 2048) \
            or os.stat(dst).st_nlink != 2:
        return {"status": "FAIL", "detail": {"problem": "racing_links_not_exclusive", "results": res}}
    return ok(**detail, racers=n, winners=1, eexist=n - 1, errno_eexist=errno.EEXIST)


def check_flock_local(ctx) -> dict:
    d = ctx["work"] / "flock"
    d.mkdir()
    lock = d / "x.lock"
    holder = run_role("hold", "--file", lock)
    try:
        ready, _, _ = select.select([holder.stdout], [], [], 30)
        if not ready or holder.stdout.readline().strip() != "HELD":
            return {"status": "FAIL", "detail": {"problem": "holder_did_not_acquire"}}
        probe = finish(run_role("probe", "--file", lock, "--wait-acquire", 0))
        if probe["blocked"] is not True:
            return {"status": "FAIL", "detail": {"problem": "flock_not_exclusive_across_processes", "probe": probe}}
        holder.send_signal(signal.SIGKILL)
        holder.wait()
        after = finish(run_role("probe", "--file", lock, "--wait-acquire", 30))
        if after["blocked"] is not False:
            return {"status": "FAIL", "detail": {"problem": "lock_not_released_after_holder_killed", "probe": after}}
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()
    fd1, fd2 = os.open(lock, os.O_RDWR), os.open(lock, os.O_RDWR)
    fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
        same_process = "second_descriptor_not_blocked (POSIX-lock emulation; the layer only relies on cross-process exclusion)"
    except OSError:
        same_process = "second_descriptor_blocked"
    os.close(fd1), os.close(fd2)
    return ok(cross_process_blocked=True, released_after_sigkill_seconds=after["waited_seconds"], same_process=same_process)


def _raw_renameat2(src, dst) -> tuple[int, int]:
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.renameat2
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    rc = fn(-100, os.fsencode(src), -100, os.fsencode(dst), 1)
    return rc, (ctypes.get_errno() if rc else 0)


def check_renameat2(ctx) -> dict:
    d = ctx["work"] / "rename"
    d.mkdir()
    (d / "f1").write_bytes(b"1")
    (d / "f2").write_bytes(b"2")
    (d / "s").mkdir()
    (d / "s" / "x").write_bytes(b"x")
    (d / "e").mkdir()
    detail = {}
    try:
        rc1, e1 = _raw_renameat2(d / "f1", d / "f2")
    except (OSError, AttributeError) as exc:
        rc1, e1 = -1, errno.ENOSYS
        detail["libc"] = str(exc)
    unsupported = rc1 != 0 and e1 in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP)
    if not unsupported:
        rc2, e2 = _raw_renameat2(d / "s", d / "e")
        rc3, e3 = _raw_renameat2(d / "s", d / "fresh")
        if not (rc1 != 0 and e1 == errno.EEXIST and (d / "f1").exists() and (d / "f2").read_bytes() == b"2"
                and rc2 != 0 and e2 in (errno.EEXIST, errno.ENOTEMPTY) and list((d / "e").iterdir()) == []
                and rc3 == 0 and (d / "fresh" / "x").exists() and not (d / "s").exists()):
            return {"status": "FAIL", "detail": {"problem": "renameat2_noreplace_semantics", "results": [[rc1, e1], [rc2, e2], [rc3, e3]]}}
        primitive, status = "renameat2_noreplace", "PASS"
        detail.update(file_over_file_errno=e1, dir_over_empty_dir_errno=e2)
    else:
        primitive, status = "fallback_existence_check_under_merge_lock", "PASS_FALLBACK"
        detail.update(renameat2_errno=e1, requires="flock_exclusivity and (network mounts) flock_cross_host must PASS")
    (d / "s2").mkdir()
    (d / "s2" / "y").write_bytes(b"y")
    (d / "e2").mkdir()
    try:                                                              # the layer's own primitive must refuse an empty dir
        used = SL.rename_noreplace(d / "s2", d / "e2")
        return {"status": "FAIL", "detail": {"problem": "layer_rename_noreplace_replaced_empty_dir", "used": used}}
    except SL.ShardError:
        pass
    used = SL.rename_noreplace(d / "s2", d / "fresh2")
    if not (d / "fresh2" / "y").exists() or (d / "s2").exists():
        return {"status": "FAIL", "detail": {"problem": "layer_rename_noreplace_did_not_move"}}
    (d / "p").mkdir()                                                 # contrast: a plain rename silently replaces an empty dir
    (d / "q").mkdir()
    os.rename(d / "p", d / "q")
    return {"status": status, "detail": {"primitive": primitive, "layer_primitive_used": used, "plain_rename_replaces_empty_dir": True, **detail}}


def check_fsync(ctx) -> dict:
    d = ctx["work"] / "fsync"
    d.mkdir()
    lat_file, lat_dir = [], []
    data = payload_bytes("fsync", 1 << 20)
    try:
        for i in range(12):
            p = d / f"f{i}"
            with open(p, "wb") as fh:
                fh.write(data)
                fh.flush()
                t = time.perf_counter()
                os.fsync(fh.fileno())
                lat_file.append(time.perf_counter() - t)
            t = time.perf_counter()
            _fsync_dir(d)
            lat_dir.append(time.perf_counter() - t)
            if p.read_bytes() != data:
                return {"status": "FAIL", "detail": {"problem": "readback_mismatch_after_fsync"}}
        with open(d / "fd", "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fdatasync(fh.fileno())
        (d / "r1").write_bytes(b"r")
        os.rename(d / "r1", d / "r2")
        _fsync_dir(d)
    except OSError as exc:
        return {"status": "FAIL", "detail": {"problem": "fsync_or_dir_fsync_unsupported", "errno": exc.errno, "error": str(exc)}}
    return ok(file_fsync_ms_median=median(lat_file) * 1e3, dir_fsync_ms_median=median(lat_dir) * 1e3, fdatasync=True,
              note="semantic success only: durability across power loss cannot be proven by a software probe")


def check_atomic_publish(ctx) -> dict:
    quick = ctx["quick"]
    size, writers, readers = (256 << 10 if quick else 2 << 20), 6, 2
    rounds = []
    for frozen in (False, True):
        d = ctx["work"] / f"pub{int(frozen)}"
        d.mkdir()
        target, go, stop = d / "RUN_MANIFEST.json", d / "go", d / "stop"
        expected = {sha256_bytes(payload_bytes(f"payload{i}", size)) for i in range(writers)}
        rd = [run_role("reader", "--dir", d, "--index", i, "--target", target, "--hashes", ",".join(sorted(expected)),
                       "--size", size, "--stop", stop) for i in range(readers)]
        wr = [run_role("publish", "--dir", d, "--index", i, "--target", target, "--size", size, "--go", go,
                       "--freeze", int(frozen)) for i in range(writers)]
        wait_ready([d / f"ready.{i}" for i in range(writers)] + [d / f"ready.reader{i}" for i in range(readers)])
        go.write_bytes(b"")
        results = [finish(p) for p in wr]
        time.sleep(0.2)
        stop.write_bytes(b"")
        reads = [finish(p) for p in rd]
        won = [r for r in results if r["result"] == "won"]
        others = [r for r in results if r["result"] != "won"]
        leftovers = [p.name for p in d.iterdir() if ".tmp." in p.name]
        problems = []
        if len(won) != 1 or any(r["result"] != "refuse_overwrite:RUN_MANIFEST.json" for r in others):
            problems.append("publish_not_exclusive_or_not_reason_coded")
        elif target.read_bytes() != payload_bytes(f"payload{won[0]['index']}", size):
            problems.append("published_content_is_not_the_winners")
        if any(r["torn"] for r in reads):
            problems.append("reader_saw_torn_file")
        if leftovers:
            problems.append("temp_files_left_behind")
        rounds.append({"identical_host_pid_clock": frozen, "winners": len(won), "refusals": len(others),
                       "reader_reads": sum(r["reads"] for r in reads), "torn_reads": sum(r["torn"] for r in reads),
                       "problems": problems})
        if problems:
            return {"status": "FAIL", "detail": {"rounds": rounds}}
    files = 60 if quick else 200                                     # directory publish: appears whole or not at all
    d = ctx["work"] / "pubdir"
    d.mkdir()
    stop, staging, final = d / "stop", d / "staging", d / "published"
    staging.mkdir()
    for i in range(files):
        (staging / f"f{i:04d}").write_bytes(payload_bytes(f"f{i}", 512))
    dr = [run_role("dirreader", "--dir", d, "--index", i, "--target", final, "--expect", files, "--stop", stop) for i in range(readers)]
    wait_ready([d / f"ready.reader{i}" for i in range(readers)])
    SL.rename_noreplace(staging, final)
    time.sleep(0.3)
    stop.write_bytes(b"")
    listings = [finish(p) for p in dr]
    if any(r["partial_listings"] for r in listings) or len(os.listdir(final)) != files:
        return {"status": "FAIL", "detail": {"rounds": rounds, "problem": "directory_published_partially", "listings": listings}}
    (d / "again").mkdir()
    try:
        SL.rename_noreplace(d / "again", final)
        return {"status": "FAIL", "detail": {"problem": "directory_publish_overwrote"}}
    except SL.ShardError:
        pass
    return ok(rounds=rounds, dir_publish_files=files, dir_readers=listings)


def numeric_canary() -> dict:
    """Arithmetic-ramp numerics (no random source): BLAS, LAPACK, SIMD libm, reductions and sorts, hashed bit-for-bit."""
    import numpy as np
    i = np.arange(1, (1 << 16) + 1, dtype=np.float64)
    x = (i * 0.6180339887498949) % 1.0
    y = np.sin(i * 0.001) * 3.0
    A = ((np.arange(200 * 384, dtype=np.float64).reshape(200, 384) * 0.7548776662466927) % 1.0) - 0.5
    B = ((np.arange(384 * 64, dtype=np.float64).reshape(384, 64) * 0.5698402909980532) % 1.0) - 0.5
    Ai = ((np.arange(200 * 384).reshape(200, 384) * 7) % 5 - 2).astype(np.float32)
    Bi = ((np.arange(384 * 64).reshape(384, 64) * 3) % 7 - 3).astype(np.float32)
    G = A.T @ A + 384.0 * np.eye(384)
    ops = {"matmul_f64": A @ B, "matmul_f32_int": (Ai @ Bi).astype(np.int32), "gram_f64": A.T @ A,
           "cholesky": np.linalg.cholesky(G), "eigvalsh": np.linalg.eigvalsh(G[:96, :96]),
           "solve": np.linalg.solve(G[:96, :96], A[:96, :96]), "einsum": np.einsum("ij,jk->ik", A, B),
           "gemv": A @ B[:, 0], "exp": np.exp(y), "log": np.log(x + 1.0), "sin": np.sin(i), "cos": np.cos(i),
           "tanh": np.tanh(y), "power": np.power(x + 1.0, 1.7), "sqrt": np.sqrt(i), "arctan": np.arctan(y),
           "sum": np.array([x.sum(), (x * x).sum(), np.cumsum(x)[-1], x.mean(), x.var()]),
           "sum_f32": np.array([x.astype(np.float32).sum(), np.cumsum(x.astype(np.float32))[-1]]),
           "argsort": np.argsort(x, kind="stable"), "searchsorted": np.searchsorted(np.sort(x), x[:4096])}
    hashes = {k: sha256_bytes(np.ascontiguousarray(v).tobytes()) for k, v in ops.items()}
    return {"hashes": hashes, "canary_id": sha256_bytes(canon(hashes).encode())}


def check_numeric_canary(ctx) -> dict:
    a, b = numeric_canary(), numeric_canary()
    if a != b:
        return {"status": "FAIL", "detail": {"problem": "numeric_canary_not_repeatable_on_this_host"}}
    return ok(**a)


def _tree_hash(root: Path) -> str:
    return sha256_bytes(canon({str(p.relative_to(root)): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()}).encode())


def check_canary_fixture(ctx) -> dict:
    """Two dynamic workers + merge through the layer CLI on the target mount, compared with the sequential runner."""
    import shard_fixture as F
    R = SL.runner()
    out, cli = ctx["work"] / "canary", [sys.executable, str(LAYER_DIR / "shard_layer.py")]
    common = ["--fixture", "--out", str(out), "--shard-worlds", "3"]
    procs = [subprocess.Popen([*cli, "work", *common, "--worker", str(i), "--workers", "2", "--assign", "dynamic"], env=ENV,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(2)]
    errs = []
    for p in procs:
        _, err = p.communicate(timeout=600)
        if p.returncode:
            errs.append(err.strip()[-200:])
    if errs:
        return {"status": "FAIL", "detail": {"problem": "layer_worker_failed", "errors": errs}}
    for cmd in ("audit", "merge"):
        r = subprocess.run([*cli, cmd, *common], env=ENV, capture_output=True, text=True, timeout=600)
        if r.returncode:
            return {"status": "FAIL", "detail": {"problem": f"layer_{cmd}_failed", "error": r.stderr.strip()[-200:]}}
    seq = ctx["work"] / "canary_seq"
    plan = F.fixture_plan(R)
    old_plan, old_b = R.cell_plan, R.B
    try:
        with F.installed(R):
            R.cell_plan, R.B = (lambda: plan), F.FIXTURE_DRAWS
            R.run_validation(None, F.StubRegistry(R.MASTER_SEED), seq,
                             {"runtime_estimate_cpu_hours": 1.0, "fast_benchmark_receipt_sha256": "0" * 64})
    finally:
        R.cell_plan, R.B = old_plan, old_b
    merged = out / "merged"
    got = {p.name: p.read_bytes() for p in merged.iterdir() if p.name != "MERGE_MANIFEST.json"}
    want = {p.name: p.read_bytes() for p in seq.iterdir()}
    equal = got == want and len(got) == 6
    manifest = json.loads((merged / "MERGE_MANIFEST.json").read_text())
    files = {n: sha256_bytes(b) for n, b in sorted(got.items())}
    detail = {"files_sha256": files, "manifest_deterministic_sha256": manifest["deterministic_sha256"],
              "run_manifest_sha256": sha256_file(out / "RUN_MANIFEST.json"), "shards_tree_sha256": _tree_hash(out / "shards"),
              "worlds": manifest["worlds"], "shards": manifest["shards"], "equals_sequential_runner": equal}
    detail["canary_id"] = sha256_bytes(canon({k: v for k, v in detail.items() if k != "canary_id"}).encode())
    return ok("PASS" if equal else "FAIL", **detail)


def check_bit_identity(ctx) -> dict:
    fw = FastWorkload()
    R = fw.R
    try:
        out = R.benchmark_world(fw.world, fw.perm, reference_draws=0 if ctx["quick"] else 1, fast_draws=1)
    except Exception as exc:                                          # noqa: BLE001
        return {"status": "FAIL", "detail": {"problem": f"{type(exc).__name__}:{exc}"}}
    return ok(bitwise_identical=out["equivalence"]["bitwise_identical"], hypotheses_compared=out["equivalence"]["hypotheses_compared"],
              tested_hypotheses=out["workload"]["tested_hypotheses"], reference_vectors=out["workload"]["reference_vectors"],
              reference_seconds_per_vector=out["reference"]["seconds_per_vector"])


def reference_calibration() -> dict | None:
    return json.loads(CALIBRATION_PATH.read_text()) if CALIBRATION_PATH.is_file() else None


def project_throughput(per_draw: float, draws: int) -> dict:
    """Per-draw cost depends on the number of block maps held (cache pressure), so the ratio is only valid at the
    calibration draw count."""
    cal = reference_calibration()
    if not cal:
        return {"calibrated": False, "reason": "no_reference_calibration"}
    if cal.get("draws") != draws:
        return {"calibrated": False, "reason": f"draw_count_{draws}_differs_from_calibration_{cal.get('draws')}"}
    scale = per_draw / cal["proxy_seconds_per_draw_end_to_end"]
    return {"calibrated": True, "speed_ratio_vs_reference_host": 1.0 / scale,
            "calibrated_seconds_per_draw_end_to_end": cal["phase90_seconds_per_draw_end_to_end"] * scale,
            "calibrated_draws_per_second_per_core": cal["phase90_draws_per_second_per_core"] / scale,
            "calibrated_full_run_cpu_hours": cal["phase90_projection_cpu_hours"] * scale,
            "note": "projection: recorded phase-90 real-world rate scaled by this host's token-world speed ratio"}


def single_worker_processes(work: Path, processes: int, draws: int, reps: int) -> list[dict]:
    """``processes`` fresh single-worker processes, run one after another (each does ``reps`` timed repetitions).  Per-process
    speed varies on shared / virtualised hosts, so the qualification statistic is the median across processes."""
    out = []
    for i in range(processes):
        d = work / f"single{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "go").write_bytes(b"")
        out.append(finish(run_role("tput", "--dir", d, "--index", 0, "--draws", draws, "--reps", reps, "--go", d / "go"), timeout=900))
    return out


def check_throughput(ctx) -> dict:
    processes, draws, reps = (2, 60, 2) if ctx["quick"] else (THROUGHPUT_PROCESSES, CALIBRATION_DRAWS, 3)
    procs = single_worker_processes(ctx["work"], processes, draws, reps)
    rates = [p["draws_per_second"] for p in procs]
    per_draw = 1.0 / median(rates)
    R = SL.runner()
    encode = median([p["encode_seconds"] for p in procs])
    proxy_cpu_hours = R.V.FULL_WORLDS * (encode + (1 + R.B) * per_draw) / 3600
    ctx["single_draws_per_second"] = median(rates)
    return ok(processes=processes, draws=draws, reps_per_process=reps, tested_hypotheses=procs[0]["tested_hypotheses"],
              seconds_per_draw_end_to_end=per_draw, draws_per_second_1_worker=median(rates), process_draws_per_second=rates,
              process_spread_max_over_min=max(rates) / min(rates), encode_seconds=encode, proxy_full_run_cpu_hours=proxy_cpu_hours,
              **project_throughput(per_draw, draws), load_average_1m=os.getloadavg()[0], logical_cpus=os.cpu_count(),
              statistic="median across fresh single-worker processes")


def ladder(cpus: int) -> list[int]:
    w, out = 1, {1}
    while w < cpus:
        w *= 2
        out.add(min(w, cpus))
    return sorted(out)


def check_scaling(ctx) -> dict:
    cpus = len(os.sched_getaffinity(0))
    draws = 60 if ctx["quick"] else CALIBRATION_DRAWS
    rows = {}
    for w in ([1, 2] if ctx["quick"] else ladder(cpus)):
        if w > cpus:
            continue
        d = ctx["work"] / f"scale{w}"
        d.mkdir()
        go = d / "go"
        procs = [run_role("tput", "--dir", d, "--index", i, "--draws", draws, "--reps", 1, "--go", go) for i in range(w)]
        wait_ready([d / f"ready.tput{i}" for i in range(w)], timeout=300)
        t = time.perf_counter()
        go.write_bytes(b"")
        res = [finish(p, timeout=900) for p in procs]
        rows[w] = {"per_worker_draws_per_second": [r["draws_per_second"] for r in res], "wall_seconds": time.perf_counter() - t,
                   "aggregate_draws_per_second": sum(r["draws_per_second"] for r in res),
                   "peak_rss_mib_max": max(r["peak_rss_mib"] for r in res)}
    base = ctx.get("single_draws_per_second") or rows[1]["aggregate_draws_per_second"]
    for w, r in rows.items():
        r["efficiency"] = r["aggregate_draws_per_second"] / (w * base)
    mem_avail = next((int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemAvailable")), 0) / 1024
    rss = max(r["peak_rss_mib_max"] for r in rows.values())
    mem_bound = int(mem_avail * MEMORY_HEADROOM // (rss * RSS_SAFETY)) if rss else cpus
    eff_ok = [w for w, r in rows.items() if r["efficiency"] >= MIN_EFFICIENCY]
    qualified = max(1, min(max(eff_ok, default=1), cpus, mem_bound))
    return ok(ladder={str(w): r for w, r in rows.items()}, efficiency_baseline_draws_per_second=base, min_efficiency=MIN_EFFICIENCY, affinity_cpus=cpus,
              mem_available_mib=mem_avail, peak_rss_mib_per_worker=rss, memory_bound_workers=mem_bound,
              qualified_workers=qualified, load_average_1m_before=ctx.get("load_before"),
              rule="largest ladder size with parallel efficiency >= min_efficiency, capped by affinity CPUs and by "
                   "MemAvailable*0.8 / (peak RSS * 1.5)")


# ------------------------------------------------------------------------------------------------ cross-host flock
def _marker(d: Path, name: str, obj: dict) -> None:
    SL.publish_exclusive(d / name, (canon(obj) + "\n").encode())


def _wait_marker(d: Path, name: str, timeout: float) -> dict:
    return json.loads(wait_for(lambda: (d / name).read_bytes() if (d / name).exists() else None, timeout, name, 0.05))


def flock_peer(shared: Path, me: str, peer: str, timeout: float) -> dict:
    """Both hosts run this with swapped ``me``/``peer``.  Phase 1: the lexicographically first label holds the lock and
    the other probes it (must be blocked), then the holder's lock holder is SIGKILLed and the prober must acquire.
    Phase 2 swaps roles, so each host is tested as a prober.  Same-kernel peers are not cross-host evidence."""
    d = Path(shared) / f"qual_flock_{'_'.join(sorted([me, peer]))}"
    d.mkdir(exist_ok=True)
    ident, labels, result = host_identity(), sorted([me, peer]), {"me": me, "peer": peer, "phases": {}}
    try:
        for phase, (holder, prober) in enumerate([(labels[0], labels[1]), (labels[1], labels[0])], 1):
            lock = d / f"phase{phase}.lock"
            if me == holder:
                child = run_role("hold", "--file", lock)
                try:
                    ready, _, _ = select.select([child.stdout], [], [], timeout)
                    if not ready or child.stdout.readline().strip() != "HELD":
                        raise QualificationError("holder_did_not_acquire")
                    _marker(d, f"p{phase}.held.{me}", ident)
                    probe = _wait_marker(d, f"p{phase}.probe.{prober}", timeout)
                    child.send_signal(signal.SIGKILL)                 # the holder "crashes"
                    child.wait()
                    _marker(d, f"p{phase}.released.{me}", {})
                    acq = _wait_marker(d, f"p{phase}.acquired.{prober}", timeout)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
                result["phases"][str(phase)] = {"role": "holder", "prober_blocked": probe["blocked"],
                                                "prober_acquired_after_release_s": acq["seconds"]}
            else:
                held = _wait_marker(d, f"p{phase}.held.{holder}", timeout)
                first = finish(run_role("probe", "--file", lock, "--wait-acquire", 0))
                _marker(d, f"p{phase}.probe.{me}", {"blocked": first["blocked"], "identity": ident})
                _wait_marker(d, f"p{phase}.released.{holder}", timeout)
                t = time.perf_counter()
                after = finish(run_role("probe", "--file", lock, "--wait-acquire", timeout), timeout=timeout + 30)
                seconds = time.perf_counter() - t
                _marker(d, f"p{phase}.acquired.{me}", {"seconds": seconds})
                result["phases"][str(phase)] = {"role": "prober", "blocked_while_peer_held": first["blocked"],
                                                "acquired_after_peer_released": after["blocked"] is False,
                                                "acquired_after_release_s": seconds, "peer_identity": held}
                result["peer_identity"] = held
    except QualificationError as exc:
        return {"status": "NOT_EXERCISED", "detail": {**result, "reason": str(exc)}}
    mine = [p for p in result["phases"].values() if p["role"] == "prober"][0]
    if same_kernel(ident, result["peer_identity"]):
        return {"status": "SAME_HOST_PEER", "detail": {**result, "note": "peer shares this kernel: cross-process, not cross-host"}}
    good = mine["blocked_while_peer_held"] is True and mine["acquired_after_peer_released"] and mine["acquired_after_release_s"] <= 60
    return {"status": "PASS" if good else "FAIL", "detail": {**result, "prober": mine}}


# ------------------------------------------------------------------------------------------------ host command
def run_check(receipt_checks: dict, name: str, fn, ctx) -> str:
    started = time.perf_counter()
    try:
        r = fn(ctx)
    except BaseException as exc:                                      # noqa: BLE001 - fail closed on anything
        if isinstance(exc, KeyboardInterrupt):
            raise
        r = {"status": "FAIL", "detail": {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}}
    r["seconds"] = round(time.perf_counter() - started, 3)
    receipt_checks[name] = r
    return r["status"]


def host_verdict(checks: dict, mount: dict | None) -> tuple[str, dict]:
    bad = [n for n, c in checks.items() if c["status"] == "FAIL"]
    if bad:
        return "FAIL", {"failed_checks": bad}
    net = bool(mount) and mount["fstype"] in NETWORK_FS
    fb = checks.get("renameat2_noreplace", {}).get("status") == "PASS_FALLBACK"
    cross = checks.get("flock_cross_host", {}).get("status")
    scope = {"cross_host_flock": cross, "renameat2_fallback": fb}
    if (net or fb) and cross != "PASS":
        return "INCOMPLETE", {**scope, "reason": "network_mount_or_rename_fallback_requires_cross_host_flock_evidence"}
    missing = [n for n in CHECK_ORDER if n not in checks and n != "flock_cross_host"]
    if missing:
        return "INCOMPLETE", {**scope, "reason": "checks_not_run", "missing": missing}
    return "PASS", scope


def cmd_host(a) -> int:
    target = Path(a.dir)
    if not target.is_dir():
        print(f"fail closed: target directory missing: {target}", file=sys.stderr)
        return 2
    key = Path(a.hmac_key_file).read_bytes() if a.hmac_key_file else None
    label = a.label or SL.host_token()
    work = target / f"qual_{label}_{os.getpid()}_{time.time_ns()}"
    work.mkdir()
    started, checks = time.time(), {}
    ctx = {"work": work, "quick": a.quick, "load_before": os.getloadavg()[0]}
    mount = None
    try:
        mount = mount_of(work)
        ctx["mount"] = mount
    except Exception as exc:                                          # noqa: BLE001
        checks["filesystem_policy"] = {"status": "FAIL", "detail": {"error": str(exc)}, "seconds": 0}
    status = run_check(checks, "bindings", check_bindings, ctx)
    body = {"schema": SCHEMA_RECEIPT, "label": label, "quick": bool(a.quick), "tool_sha256": sha256_file(TOOL_PATH),
            "bindings": {"bundle_sha256": EXPECTED_BUNDLE_SHA256, "shard_layer_sha256": EXPECTED_LAYER_SHA256},
            "host": host_identity(), "target": {"dir": str(target), "work_dir": str(work), "mount": mount},
            "non_inferential": True, "rng_constructed": False, "validation_worlds": 0, "authorization_read": False}
    if status != "PASS":
        body.update(checks=checks, verdict="FAIL", verdict_detail={"failed_checks": ["bindings"], "skipped": "all other checks"},
                    started=started, finished=time.time())
    else:
        prov = SL.collect_provenance(sha256_file(BUNDLE / "artifacts/environment_lock.json"))
        ctx["provenance"] = prov
        body["provenance"] = prov
        run_check(checks, "environment_gate", check_environment_gate, ctx)
        run_check(checks, "environment_lock_hash", check_environment_lock, ctx)
        run_check(checks, "cpu_features", check_cpu, ctx)
        run_check(checks, "numpy_openblas_identity", check_blas, ctx)
        if "filesystem_policy" not in checks:
            run_check(checks, "filesystem_policy", check_fs_policy, ctx)
        fs_ok = checks["filesystem_policy"]["status"] != "FAIL"
        if fs_ok:
            for name, fn in (("hardlink_eexist", check_hardlink), ("flock_exclusivity", check_flock_local)):
                run_check(checks, name, fn, ctx)
            if a.flock_peer_label:
                r = flock_peer(target, label, a.flock_peer_label, a.peer_timeout)
                r["seconds"] = 0
                checks["flock_cross_host"] = r
            else:
                checks["flock_cross_host"] = {"status": "NOT_EXERCISED", "detail": {"reason": "no --flock-peer-label"}, "seconds": 0}
            for name, fn in (("renameat2_noreplace", check_renameat2), ("fsync_file_and_dir", check_fsync),
                             ("atomic_publish", check_atomic_publish), ("canary_fixture", check_canary_fixture)):
                run_check(checks, name, fn, ctx)
        else:
            checks["flock_cross_host"] = {"status": "NOT_EXERCISED", "detail": {"reason": "filesystem_policy_failed"}, "seconds": 0}
        run_check(checks, "canary_numeric", check_numeric_canary, ctx)
        run_check(checks, "fastmetric_bit_identity", check_bit_identity, ctx)
        run_check(checks, "fastmetric_throughput", check_throughput, ctx)
        run_check(checks, "worker_scaling", check_scaling, ctx)
        verdict, verdict_detail = host_verdict(checks, mount)
        body.update(checks=checks, verdict=verdict, verdict_detail=verdict_detail, started=started, finished=time.time())
        sc = checks.get("worker_scaling", {})
        body["qualified_workers"] = sc.get("detail", {}).get("qualified_workers") if verdict in ("PASS", "INCOMPLETE") else 0
        tp = checks.get("fastmetric_throughput", {}).get("detail", {})
        body["throughput"] = {k: tp.get(k) for k in ("draws_per_second_1_worker", "seconds_per_draw_end_to_end",
                                                     "speed_ratio_vs_reference_host", "calibrated_draws_per_second_per_core",
                                                     "calibrated_full_run_cpu_hours", "proxy_full_run_cpu_hours")}
        body["compatibility_key"] = {"canary_fixture": checks.get("canary_fixture", {}).get("detail", {}).get("canary_id"),
                                     "canary_numeric": checks.get("canary_numeric", {}).get("detail", {}).get("canary_id"),
                                     "bit_identity": checks.get("fastmetric_bit_identity", {}).get("status"),
                                     "hardware_identity": prov["hardware_identity"]}
    receipt = seal(body, key)
    out_dir = Path(a.receipts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"receipt_{label}.json"
    path.write_text(json.dumps(receipt, indent=1, sort_keys=True, default=str) + "\n")
    if body["verdict"] == "PASS" and not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"receipt": str(path), "verdict": body["verdict"], "receipt_sha256": receipt["receipt_sha256"],
                      "statuses": {n: c["status"] for n, c in checks.items()}, "qualified_workers": body.get("qualified_workers")},
                     indent=1))
    return 0 if body["verdict"] in ("PASS", "INCOMPLETE") else 2


# ------------------------------------------------------------------------------------------------ combine
def load_receipts(paths, key: bytes | None) -> tuple[list[dict], list[dict]]:
    good, bad = [], []
    for p in paths:
        try:
            r = json.loads(Path(p).read_text())
        except (OSError, ValueError):
            bad.append({"file": str(p), "problems": ["unreadable"]})
            continue
        problems = verify_seal(r, key)
        if r.get("schema") != SCHEMA_RECEIPT:
            problems.append("schema_mismatch")
        if r.get("bindings") != {"bundle_sha256": EXPECTED_BUNDLE_SHA256, "shard_layer_sha256": EXPECTED_LAYER_SHA256}:
            problems.append("bindings_mismatch")
        if r.get("tool_sha256") != sha256_file(TOOL_PATH):
            problems.append("tool_sha256_differs_from_this_tool")
        if r.get("quick"):
            problems.append("quick_receipt_not_a_qualification")
        if r.get("rng_constructed") is not False or r.get("validation_worlds") != 0 or r.get("authorization_read") is not False:
            problems.append("receipt_does_not_attest_rng_free_no_validation_no_authorization")
        (bad if problems else good).append({"file": str(p), "problems": problems} if problems else {"file": str(p), **r})
    return good, bad


def combine(receipts: list[dict], invalid: list[dict]) -> dict:
    labels = [r["label"] for r in receipts]
    dup = sorted({l for l in labels if labels.count(l) > 1})
    rows = {}
    for r in receipts:
        c = r.get("checks", {})
        rows[r["label"]] = {"verdict": r["verdict"], "hostname": r["host"]["hostname"], "kernel_boot_id": r["host"]["boot_id_sha256"],
                            "mount_fstype": (r["target"]["mount"] or {}).get("fstype"), "mount_source": (r["target"]["mount"] or {}).get("source"),
                            "check_statuses": {n: x["status"] for n, x in c.items()}, "qualified_workers": r.get("qualified_workers"),
                            "throughput": r.get("throughput"), "compatibility_key": r.get("compatibility_key"),
                            "receipt_sha256": r["receipt_sha256"], "flock_cross_host": c.get("flock_cross_host")}
    passing = [l for l, x in rows.items() if x["verdict"] == "PASS"]
    classes: dict = {}
    for l in passing:
        k = rows[l]["compatibility_key"]
        classes.setdefault(canon({x: k[x] for x in ("canary_fixture", "canary_numeric", "bit_identity")}), []).append(l)
    numeric_diff = {}
    if len(classes) > 1:
        ref = None
        for l in passing:
            h = next(r for r in receipts if r["label"] == l)["checks"]["canary_numeric"]["detail"]["hashes"]
            ref = ref or h
            numeric_diff[l] = sorted(k for k in h if h[k] != ref[k])
    chosen = sorted(max(classes.values(), key=len)) if classes else []
    hw = {rows[l]["compatibility_key"]["hardware_identity"] for l in chosen}
    if not chosen:
        compat = "NO_PASSING_HOST"
    elif len(classes) > 1:
        compat = "INCOMPATIBLE_CANARIES_BETWEEN_HOSTS"
    elif len(chosen) == 1:
        compat = "SINGLE_HOST_NO_CROSS_HOST_COMPARISON"
    else:
        compat = "IDENTICAL_HARDWARE_AND_CANARIES" if len(hw) == 1 else "DIFFERENT_HARDWARE_BYTE_EQUAL_CANARIES"
    multi = len(chosen) > 1
    flock_ok = True
    flock_notes = []
    if multi:
        for l in chosen:
            f = rows[l]["flock_cross_host"] or {}
            if f.get("status") != "PASS":
                flock_ok = False
                flock_notes.append(f"{l}: cross-host flock {f.get('status')}")
        sources = {(rows[l]["mount_fstype"], rows[l]["mount_source"]) for l in chosen}
        if len(sources) != 1:
            flock_ok = False
            flock_notes.append("hosts do not report one shared mount source (each host qualified its own storage)")
    fs_caps = {l: {"fstype": rows[l]["mount_fstype"], **{n: rows[l]["check_statuses"].get(n) for n in
                   ("filesystem_policy", "hardlink_eexist", "flock_exclusivity", "flock_cross_host", "renameat2_noreplace",
                    "fsync_file_and_dir", "atomic_publish")}} for l in rows}
    multi_ok = multi and flock_ok and len(classes) == 1
    workers_now = sum(rows[l]["qualified_workers"] or 0 for l in chosen) if (multi_ok or len(chosen) == 1) else \
        max((rows[l]["qualified_workers"] or 0 for l in chosen), default=0)
    speed = 0.0
    for l in chosen:
        t = rows[l]["throughput"] or {}
        hours = t.get("calibrated_full_run_cpu_hours") or t.get("proxy_full_run_cpu_hours")
        eff = next(r for r in receipts if r["label"] == l)["checks"]["worker_scaling"]["detail"]["ladder"]
        w = rows[l]["qualified_workers"] or 0
        e = eff.get(str(w), {}).get("efficiency", 1.0)
        if hours and (multi_ok or len(chosen) == 1):
            speed += w * e / hours
    wall_hours = 1.0 / speed if speed else None
    reasons = []
    if invalid:
        reasons.append("invalid_receipts_present")
    if dup:
        reasons.append(f"duplicate_labels:{dup}")
    if not chosen:
        reasons.append("no_host_passed")
    if len(classes) > 1:
        reasons.append("hosts_split_into_incompatible_canary_classes")
    if multi and not multi_ok:
        reasons += ["multi_host_not_qualified"] + flock_notes
    if len(chosen) == 1:
        reasons.append("only_one_host_evidenced: no cross-host flock, no cross-host canary comparison")
    if wall_hours is not None and wall_hours > 24 * 60:
        reasons.append(f"capacity: projected full run {wall_hours / 24:.0f} days on the qualified workers")
    hosts_ready = bool(chosen) and not invalid and not dup and len(classes) == 1 and (len(chosen) > 1 and multi_ok)
    return {"schema": SCHEMA_REPORT, "bindings": {"bundle_sha256": EXPECTED_BUNDLE_SHA256, "shard_layer_sha256": EXPECTED_LAYER_SHA256},
            "tool_sha256": sha256_file(TOOL_PATH), "hosts": rows, "invalid_receipts": invalid, "compatibility_classes": list(classes.values()),
            "numeric_canary_differing_ops": numeric_diff, "cpu_blas_compatibility": compat, "filesystem_capability": fs_caps,
            "selected_hosts": chosen, "multi_host_qualified": multi_ok, "qualified_worker_count": workers_now,
            "qualified_workers_per_host": {l: rows[l]["qualified_workers"] for l in rows},
            "projected_full_run_wall_hours_at_qualified_workers": wall_hours,
            "projected_full_run_wall_days_at_qualified_workers": (wall_hours / 24) if wall_hours else None,
            "execution_hosts_ready_for_authorization": hosts_ready,
            "single_host_qualified": len(chosen) == 1 and not invalid and not dup, "not_ready_reasons": reasons,
            "authorization_prerequisites_outside_this_tool": [
                "owner authorization with shard_layer_sha256 and shard_worlds bound", "measured optimized-benchmark receipt (owner-authorized, RNG) "
                "for runtime_estimate_cpu_hours", "target hosts running this tool and the peer flock protocol on the real shared mount"],
            "receipts_sha256": {l: rows[l]["receipt_sha256"] for l in rows}}


def cmd_combine(a) -> int:
    key = Path(a.hmac_key_file).read_bytes() if a.hmac_key_file else None
    paths = [Path(p) for p in a.receipts]
    good, bad = load_receipts(paths, key)
    report = combine(good, bad)
    report["report_sha256"] = sha256_bytes(canon(report).encode())
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: report[k] for k in ("cpu_blas_compatibility", "multi_host_qualified", "qualified_worker_count",
                                             "single_host_qualified", "execution_hosts_ready_for_authorization",
                                             "not_ready_reasons", "report_sha256")}, indent=1))
    return 0 if good and not bad else 2


# ------------------------------------------------------------------------------------------------ calibrate
def cmd_calibrate(a) -> int:
    work = Path(a.dir) / f"qual_calibrate_{os.getpid()}_{time.time_ns()}"
    work.mkdir()
    try:
        procs = single_worker_processes(work, a.processes, CALIBRATION_DRAWS, 3)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rates = [p["draws_per_second"] for p in procs]
    p90 = json.loads(PHASE90.read_text())
    cal = {"schema": SCHEMA_CALIBRATION, "reference_host": socket.gethostname(), "processes": a.processes, "draws": CALIBRATION_DRAWS,
           "process_draws_per_second": rates, "proxy_draws_per_second": median(rates),
           "proxy_seconds_per_draw_end_to_end": 1.0 / median(rates),
           "phase90_seconds_per_draw_end_to_end": p90["throughput"]["seconds_per_draw_end_to_end"],
           "phase90_draws_per_second_per_core": p90["throughput"]["fast_draws_per_second_per_core"],
           "phase90_projection_cpu_hours": p90["projection"]["cpu_hours"], "phase90_summary_sha256": sha256_file(PHASE90),
           "load_average_1m": os.getloadavg()[0],
           "note": "median across fresh single-worker processes of a token world; slower per draw than the real phase-90 "
                   "world, used only as a ratio (per-process speed varies about 1.5x on the reference VM)"}
    CALIBRATION_PATH.write_text(json.dumps(cal, indent=1, sort_keys=True) + "\n")
    print(json.dumps(cal, indent=1))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_role"]:
        ap = argparse.ArgumentParser()
        ap.add_argument("role", choices=sorted(ROLES))
        for opt, typ in (("--file", str), ("--wait-acquire", float), ("--dir", str), ("--index", int), ("--dst", str), ("--go", str),
                         ("--target", str), ("--size", int), ("--freeze", int), ("--hashes", str), ("--stop", str),
                         ("--expect", int), ("--draws", int), ("--reps", int)):
            ap.add_argument(opt, type=typ, default=None)
        args = ap.parse_args(argv[1:])
        ROLES[args.role](args)
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    h = sub.add_parser("host")
    h.add_argument("--dir", required=True, help="target directory on the mount the run will use (must exist)")
    h.add_argument("--label", help="host label (default: sanitised hostname)")
    h.add_argument("--receipts-dir", default=str(HERE / "receipts"))
    h.add_argument("--hmac-key-file", help="optional owner-provisioned key; without it the receipt is hash-bound only")
    h.add_argument("--flock-peer-label", help="run the cross-host flock protocol with this peer label on --dir")
    h.add_argument("--peer-timeout", type=float, default=300)
    h.add_argument("--quick", action="store_true", help="smaller workloads for tests; never counts as a qualification")
    h.add_argument("--keep", action="store_true", help="keep the work directory even on PASS")
    p = sub.add_parser("flock-peer")
    p.add_argument("--dir", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--peer-label", required=True)
    p.add_argument("--peer-timeout", type=float, default=300)
    c = sub.add_parser("combine")
    c.add_argument("receipts", nargs="+")
    c.add_argument("--out", default=str(HERE / "QUALIFICATION_REPORT.json"))
    c.add_argument("--hmac-key-file")
    k = sub.add_parser("calibrate")
    k.add_argument("--dir", required=True, help="scratch location for the calibration processes")
    k.add_argument("--processes", type=int, default=7)
    args = ap.parse_args(argv)
    try:
        if args.command == "host":
            return cmd_host(args)
        if args.command == "flock-peer":
            r = flock_peer(Path(args.dir), args.label, args.peer_label, args.peer_timeout)
            print(json.dumps(r, indent=1, sort_keys=True, default=str))
            return 0 if r["status"] in ("PASS", "SAME_HOST_PEER") else 2
        if args.command == "combine":
            return cmd_combine(args)
        return cmd_calibrate(args)
    except Exception as exc:                                          # noqa: BLE001 - fail closed with a reason
        print(f"fail closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
