"""Guard the RNG-free host / filesystem qualification tool for the hardened M3.2 shard layer.

Every filesystem probe is shown to PASS on a real filesystem and to FAIL under fault injection, receipts are shown to be
hash-bound / signed / tamper-evident, and the combined report is shown to refuse to claim more than the evidence supports.
No test constructs a random generator, generates a validation world, or touches an owner authorization."""
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
QDIR = ROOT / "scripts/m32_scheme_d_qualification_20260922"
sys.path.insert(0, str(QDIR))
import qualify as Q  # noqa: E402

SL = Q.SL
ENV = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
TOOL = [sys.executable, str(QDIR / "qualify.py")]


def ctx_for(tmp_path, quick=True):
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    return {"work": work, "quick": quick, "load_before": 0.0}


# ------------------------------------------------------------------------------------------------ bindings
def test_bound_hashes_are_the_real_bundle_and_layer_hashes():
    assert Q.EXPECTED_BUNDLE_SHA256 == "eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01"
    assert len(Q.EXPECTED_LAYER_SHA256) == 64 and Q.EXPECTED_LAYER_SHA256.startswith("68195fb6f8548722d3055a0")
    assert Q.EXPECTED_LAYER_SHA256.endswith("05bc1ab0657")
    assert Q.sha256_file(SL.LAYER_PATH) == Q.EXPECTED_LAYER_SHA256
    assert Q.sha256_file(Q.BUNDLE / "BUNDLE_MANIFEST.json") == Q.EXPECTED_BUNDLE_SHA256
    assert Q.check_bindings({})["status"] == "PASS"


@pytest.mark.parametrize("attr", ("EXPECTED_LAYER_SHA256", "EXPECTED_BUNDLE_SHA256"))
def test_a_binding_mismatch_fails_closed_and_skips_every_other_check(tmp_path, monkeypatch, attr):
    monkeypatch.setattr(Q, attr, "0" * 64)
    rc = Q.main(["host", "--dir", str(tmp_path), "--label", "h", "--receipts-dir", str(tmp_path / "r"), "--quick"])
    receipt = json.loads((tmp_path / "r/receipt_h.json").read_text())
    assert rc == 2 and receipt["verdict"] == "FAIL" and list(receipt["checks"]) == ["bindings"]
    assert receipt["checks"]["bindings"]["detail"]["problems"]
    assert Q.verify_seal(receipt, None) == []                      # a FAIL receipt is still sealed and on record


def test_missing_target_directory_fails_closed(tmp_path):
    assert Q.main(["host", "--dir", str(tmp_path / "nope"), "--receipts-dir", str(tmp_path)]) == 2


# ------------------------------------------------------------------------------------------------ filesystem policy
@pytest.mark.parametrize("fstype,verdict,cls", [("ext4", "PASS", "local"), ("xfs", "PASS", "local"), ("nfs4", "PASS", "network"),
                                                 ("tmpfs", "FAIL", "volatile"), ("ramfs", "FAIL", "volatile"),
                                                 ("vmhgfs", "FAIL", "unsupported"), ("fuse.sshfs", "FAIL", "unsupported"),
                                                 ("9p", "FAIL", "unsupported"), ("cifs", "FAIL", "unsupported")])
def test_filesystem_policy_classification(fstype, verdict, cls):
    r = Q.check_fs_policy({"mount": {"fstype": fstype, "options": "rw", "super_options": "rw", "source": "x", "mount_point": "/"}})
    assert r["status"] == verdict and r["detail"]["class"] == cls


def test_relaxed_write_ordering_and_unknown_fs_only_warn():
    m = {"fstype": "ext4", "options": "rw,nobarrier", "super_options": "rw", "source": "x", "mount_point": "/"}
    assert Q.check_fs_policy({"mount": m})["status"] == "WARN"
    assert Q.check_fs_policy({"mount": {**m, "fstype": "weirdfs", "options": "rw"}})["status"] == "WARN"


def test_mount_lookup_reports_the_real_filesystem(tmp_path):
    m = Q.mount_of(tmp_path)
    assert m["fstype"] and m["mount_point"].startswith("/") and Path(m["mount_point"]).exists()


@pytest.mark.skipif(not Path("/dev/shm").is_dir(), reason="no /dev/shm")
def test_a_volatile_mount_is_rejected_by_the_full_host_command():
    shm = Q.mount_of("/dev/shm")
    if shm["fstype"] != "tmpfs":
        pytest.skip("/dev/shm is not tmpfs here")
    rc = Q.main(["host", "--dir", "/dev/shm", "--label", "shm", "--receipts-dir", str(Path("/dev/shm") / "qr"), "--quick"])
    receipt = json.loads((Path("/dev/shm") / "qr/receipt_shm.json").read_text())
    import shutil
    shutil.rmtree("/dev/shm/qr", ignore_errors=True)
    assert rc == 2 and receipt["verdict"] == "FAIL" and receipt["checks"]["filesystem_policy"]["status"] == "FAIL"
    assert "hardlink_eexist" not in receipt["checks"]              # no probe is trusted on an unsupported filesystem


# ------------------------------------------------------------------------------------------------ probes: pass and fail
def test_probes_pass_on_a_real_filesystem(tmp_path):
    for name, fn in (("hardlink", Q.check_hardlink), ("flock", Q.check_flock_local), ("rename", Q.check_renameat2),
                     ("fsync", Q.check_fsync), ("publish", Q.check_atomic_publish)):
        ctx = ctx_for(tmp_path / name)
        r = fn(ctx)
        assert r["status"] in ("PASS", "PASS_FALLBACK"), (name, r)
    assert r["detail"]["rounds"][1]["identical_host_pid_clock"] is True and r["detail"]["rounds"][1]["torn_reads"] == 0


def test_hardlink_probe_fails_when_link_overwrites(tmp_path, monkeypatch):
    real = os.link

    def overwriting(src, dst, **kw):
        with __import__("contextlib").suppress(FileNotFoundError):
            os.unlink(dst)
        return real(src, dst, **kw)
    monkeypatch.setattr(os, "link", overwriting)
    r = Q.check_hardlink(ctx_for(tmp_path))
    assert r["status"] == "FAIL" and "link_over" in r["detail"]["problem"]


def test_hardlink_probe_fails_when_links_are_unsupported(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "link", refuse)
    r = Q.check_hardlink(ctx_for(tmp_path))
    assert r["status"] == "FAIL" and r["detail"]["problem"] == "hard_links_unsupported"


def test_flock_probe_fails_when_a_second_process_is_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "finish", lambda proc, timeout=120: (proc.kill(), {"blocked": False, "waited_seconds": 0})[1])
    r = Q.check_flock_local(ctx_for(tmp_path))
    assert r["status"] == "FAIL" and r["detail"]["problem"] == "flock_not_exclusive_across_processes"


def test_flock_probe_fails_when_the_lock_survives_a_killed_holder(tmp_path, monkeypatch):
    real, calls = Q.finish, {"n": 0}

    def finish(proc, timeout=120):
        calls["n"] += 1
        return real(proc, timeout) if calls["n"] == 1 else (proc.kill(), {"blocked": True, "waited_seconds": 30})[1]
    monkeypatch.setattr(Q, "finish", finish)
    r = Q.check_flock_local(ctx_for(tmp_path))
    assert r["status"] == "FAIL" and r["detail"]["problem"] == "lock_not_released_after_holder_killed"


def test_renameat2_probe_fails_when_the_kernel_replaces_the_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "_raw_renameat2", lambda s, d: (0, 0))
    r = Q.check_renameat2(ctx_for(tmp_path))
    assert r["status"] == "FAIL" and r["detail"]["problem"] == "renameat2_noreplace_semantics"


def test_renameat2_unsupported_is_only_a_conditional_fallback_pass(tmp_path, monkeypatch):
    import errno
    monkeypatch.setattr(Q, "_raw_renameat2", lambda s, d: (-1, errno.EINVAL))
    r = Q.check_renameat2(ctx_for(tmp_path))
    assert r["status"] == "PASS_FALLBACK" and "flock" in r["detail"]["requires"]
    checks = {n: {"status": "PASS"} for n in Q.CHECK_ORDER}
    checks["renameat2_noreplace"] = r
    checks["flock_cross_host"] = {"status": "NOT_EXERCISED"}
    verdict, detail = Q.host_verdict(checks, {"fstype": "ext4"})
    assert verdict == "INCOMPLETE" and "cross_host_flock" in detail["reason"]      # never a plain PASS without lock evidence
    checks["flock_cross_host"] = {"status": "PASS"}
    assert Q.host_verdict(checks, {"fstype": "ext4"})[0] == "PASS"


def test_fsync_probe_fails_when_directory_fsync_is_unsupported(tmp_path, monkeypatch):
    import errno

    def bad(path):
        raise OSError(errno.EINVAL, "Invalid argument")
    monkeypatch.setattr(Q, "_fsync_dir", bad)
    r = Q.check_fsync(ctx_for(tmp_path))
    assert r["status"] == "FAIL" and r["detail"]["problem"] == "fsync_or_dir_fsync_unsupported"


def test_publish_reader_detects_a_torn_file(tmp_path):
    target, stop = tmp_path / "t", tmp_path / "stop"
    good = Q.payload_bytes("payload0", 4096)
    target.write_bytes(good[:100])                                  # a torn publish
    proc = Q.run_role("reader", "--dir", tmp_path, "--index", 0, "--target", target, "--hashes", Q.sha256_bytes(good),
                      "--size", 4096, "--stop", stop)
    Q.wait_ready([tmp_path / "ready.reader0"])
    time.sleep(0.3)
    stop.write_bytes(b"")
    r = Q.finish(proc)
    assert r["reads"] > 0 and r["torn"] == r["reads"]


def test_publish_probe_fails_if_the_published_name_is_torn_or_overwritten(tmp_path, monkeypatch):
    """A layer whose publish is non-exclusive must be caught: swap in a publisher that overwrites in place."""
    src = (QDIR / "qualify.py").read_text()
    assert "refuse_overwrite:RUN_MANIFEST.json" in src            # the probe demands the layer's reason code
    real = SL.publish_exclusive
    monkeypatch.setattr(Q, "run_role", lambda role, *a, **k: Q.subprocess.Popen(
        [sys.executable, "-c", "import sys,json;print(json.dumps({'index':0,'result':'won'}))"], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True) if role == "publish" else Q.subprocess.Popen(
        [sys.executable, "-c", "import json;print(json.dumps({'reads':1,'torn':0,'absent':0}))"], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True))
    monkeypatch.setattr(Q, "wait_ready", lambda *a, **k: None)
    r = Q.check_atomic_publish(ctx_for(tmp_path))
    assert real is SL.publish_exclusive
    assert r["status"] == "FAIL"                                    # six "winners" and no published file are rejected


# ------------------------------------------------------------------------------------------------ canaries
def test_numeric_canary_is_repeatable_and_covers_blas_lapack_and_simd_ops():
    a, b = Q.numeric_canary(), Q.numeric_canary()
    assert a == b and len(a["hashes"]) >= 18
    assert {"matmul_f64", "cholesky", "exp", "sin", "sum", "argsort"} <= set(a["hashes"])


def test_fixture_canary_matches_the_sequential_runner_and_is_repeatable(tmp_path):
    first = Q.check_canary_fixture(ctx_for(tmp_path / "a"))
    second = Q.check_canary_fixture(ctx_for(tmp_path / "b"))
    assert first["status"] == second["status"] == "PASS"
    assert first["detail"]["equals_sequential_runner"] is True
    assert first["detail"]["canary_id"] == second["detail"]["canary_id"]
    assert first["detail"]["worlds"] == 16 and len(first["detail"]["files_sha256"]) == 6


def test_throughput_calibration_is_only_used_at_the_calibration_draw_count():
    cal = Q.reference_calibration()
    assert cal["draws"] == Q.CALIBRATION_DRAWS == 300 and cal["phase90_draws_per_second_per_core"] > 0
    ok = Q.project_throughput(cal["proxy_seconds_per_draw_end_to_end"], 300)
    assert ok["calibrated"] and abs(ok["speed_ratio_vs_reference_host"] - 1.0) < 1e-9
    assert abs(ok["calibrated_full_run_cpu_hours"] - cal["phase90_projection_cpu_hours"]) < 1e-6
    assert not Q.project_throughput(0.001, 60)["calibrated"]


def test_throughput_uses_the_median_of_fresh_processes_and_reports_the_spread(tmp_path):
    procs = Q.single_worker_processes(tmp_path, 2, 20, 1)
    assert len(procs) == 2 and all(x["draws_per_second"] > 0 and x["tested_hypotheses"] == 384 for x in procs)
    r = Q.check_throughput({"work": tmp_path / "t", "quick": True})
    d = r["detail"]
    assert r["status"] == "PASS" and len(d["process_draws_per_second"]) == 2 and d["statistic"].startswith("median across fresh")
    assert d["process_spread_max_over_min"] >= 1.0 and d["draws_per_second_1_worker"] == Q.median(d["process_draws_per_second"])
    assert d["calibrated"] is False and "draw_count_60" in d["reason"]      # quick draw count never borrows the calibration
    cal = Q.reference_calibration()
    assert len(cal["process_draws_per_second"]) == cal["processes"] >= 5 and cal["proxy_draws_per_second"] == Q.median(cal["process_draws_per_second"])


def test_worker_ladder():
    assert Q.ladder(1) == [1] and Q.ladder(4) == [1, 2, 4] and Q.ladder(6) == [1, 2, 4, 6] and Q.ladder(64)[-1] == 64


# ------------------------------------------------------------------------------------------------ cross-host flock protocol
def run_peer(tmp_path, label, peer, boot=None, timeout=60):
    code = ("import sys, json; sys.path.insert(0, %r); import qualify as Q; from pathlib import Path\n"
            "%s\nprint(json.dumps(Q.flock_peer(Path(%r), %r, %r, %s), default=str))"
            % (str(QDIR), (f"_i=Q.host_identity; Q.host_identity=lambda: {{**_i(), 'boot_id_sha256': {boot!r}}}" if boot else ""),
               str(tmp_path), label, peer, timeout))
    return subprocess.Popen([sys.executable, "-c", code], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_flock_peer_protocol_between_two_kernels_passes_in_both_directions(tmp_path):
    a, b = run_peer(tmp_path, "hostA", "hostB", boot="bootA"), run_peer(tmp_path, "hostB", "hostA", boot="bootB")
    ra, rb = json.loads(a.communicate(timeout=120)[0]), json.loads(b.communicate(timeout=120)[0])
    assert ra["status"] == rb["status"] == "PASS"
    for r in (ra, rb):
        assert r["detail"]["prober"]["blocked_while_peer_held"] is True and r["detail"]["prober"]["acquired_after_peer_released"]
        assert set(r["detail"]["phases"]) == {"1", "2"}
    assert {p["role"] for p in ra["detail"]["phases"].values()} == {"holder", "prober"}


def test_flock_peer_on_the_same_kernel_is_never_counted_as_cross_host(tmp_path):
    a, b = subprocess.Popen(TOOL + ["flock-peer", "--dir", str(tmp_path), "--label", "x", "--peer-label", "y"], env=ENV,
                            stdout=subprocess.PIPE, text=True), \
        subprocess.Popen(TOOL + ["flock-peer", "--dir", str(tmp_path), "--label", "y", "--peer-label", "x"], env=ENV,
                         stdout=subprocess.PIPE, text=True)
    ra, rb = json.loads(a.communicate(timeout=120)[0]), json.loads(b.communicate(timeout=120)[0])
    assert ra["status"] == rb["status"] == "SAME_HOST_PEER" and a.returncode == b.returncode == 0


def test_flock_peer_without_a_peer_is_not_exercised_not_passed(tmp_path):
    r = json.loads(run_peer(tmp_path, "lonely", "ghost", boot="b", timeout=2).communicate(timeout=60)[0])
    assert r["status"] == "NOT_EXERCISED" and r["detail"]["reason"].startswith("timeout")


# ------------------------------------------------------------------------------------------------ receipts and combine
def fake_receipt(label, *, canary="C1", numeric="N1", hw="HW1", workers=3, cross="PASS", source="nfs:/x", fstype="nfs4",
                 verdict="PASS", ops=None, hours=6000.0, quick=False):
    hashes = ops or {"matmul_f64": "a", "exp": "b"}
    checks = {n: {"status": "PASS", "detail": {}} for n in Q.CHECK_ORDER}
    checks["flock_cross_host"] = {"status": cross, "detail": {}}
    checks["canary_numeric"]["detail"] = {"hashes": hashes, "canary_id": numeric}
    checks["worker_scaling"]["detail"] = {"ladder": {"1": {"efficiency": 1.0}, str(workers): {"efficiency": 0.9}},
                                          "qualified_workers": workers}
    body = {"schema": Q.SCHEMA_RECEIPT, "label": label, "quick": quick, "tool_sha256": Q.sha256_file(Q.TOOL_PATH),
            "bindings": {"bundle_sha256": Q.EXPECTED_BUNDLE_SHA256, "shard_layer_sha256": Q.EXPECTED_LAYER_SHA256},
            "host": {"hostname": label, "boot_id_sha256": f"boot-{label}"}, "target": {"mount": {"fstype": fstype, "source": source}},
            "non_inferential": True, "rng_constructed": False, "validation_worlds": 0, "authorization_read": False, "checks": checks,
            "verdict": verdict, "qualified_workers": workers,
            "throughput": {"calibrated_full_run_cpu_hours": hours},
            "compatibility_key": {"canary_fixture": canary, "canary_numeric": numeric, "bit_identity": "PASS", "hardware_identity": hw}}
    return Q.seal(body, None)


def write(tmp_path, *receipts):
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    paths = []
    for r in receipts:
        p = tmp_path / f"receipt_{r['label']}.json"
        p.write_text(json.dumps(r))
        paths.append(p)
    return paths


def combined(tmp_path, *receipts, key=None):
    good, bad = Q.load_receipts(write(tmp_path, *receipts), key)
    return Q.combine(good, bad)


def test_single_host_is_qualified_for_itself_but_never_claims_multi_host_readiness(tmp_path):
    rep = combined(tmp_path, fake_receipt("a", source="/dev/sda", fstype="ext4", cross="NOT_EXERCISED"))
    assert rep["single_host_qualified"] and not rep["multi_host_qualified"] and not rep["execution_hosts_ready_for_authorization"]
    assert rep["cpu_blas_compatibility"] == "SINGLE_HOST_NO_CROSS_HOST_COMPARISON" and rep["qualified_worker_count"] == 3
    assert any("only_one_host" in r for r in rep["not_ready_reasons"])
    assert abs(rep["projected_full_run_wall_days_at_qualified_workers"] - 6000 / (3 * 0.9) / 24) < 1e-9 and any("capacity" in r for r in rep["not_ready_reasons"])


def test_two_compatible_hosts_on_one_shared_mount_are_ready(tmp_path):
    rep = combined(tmp_path, fake_receipt("a", workers=4), fake_receipt("b", workers=8, hw="HW2"))
    assert rep["multi_host_qualified"] and rep["execution_hosts_ready_for_authorization"] and rep["qualified_worker_count"] == 12
    assert rep["cpu_blas_compatibility"] == "DIFFERENT_HARDWARE_BYTE_EQUAL_CANARIES"
    same = combined(tmp_path / "s", fake_receipt("a"), fake_receipt("b"))
    assert same["cpu_blas_compatibility"] == "IDENTICAL_HARDWARE_AND_CANARIES"


def test_hosts_with_different_numeric_canaries_are_not_mixed(tmp_path):
    rep = combined(tmp_path, fake_receipt("a", ops={"exp": "1", "sin": "2"}), fake_receipt("b", numeric="N2", ops={"exp": "1", "sin": "X"}),
                   fake_receipt("c", ops={"exp": "1", "sin": "2"}))
    assert rep["cpu_blas_compatibility"] == "INCOMPATIBLE_CANARIES_BETWEEN_HOSTS" and rep["selected_hosts"] == ["a", "c"]
    assert rep["numeric_canary_differing_ops"]["b"] == ["sin"] and not rep["execution_hosts_ready_for_authorization"]


def test_multi_host_needs_cross_host_flock_and_one_shared_mount(tmp_path):
    rep = combined(tmp_path, fake_receipt("a"), fake_receipt("b", cross="NOT_EXERCISED"))
    assert not rep["multi_host_qualified"] and not rep["execution_hosts_ready_for_authorization"]
    assert rep["qualified_worker_count"] == 3                        # only the largest single host is counted, not the sum
    rep = combined(tmp_path / "m", fake_receipt("a"), fake_receipt("b", source="other:/y"))
    assert not rep["multi_host_qualified"] and any("shared mount" in r for r in rep["not_ready_reasons"])
    rep = combined(tmp_path / "s", fake_receipt("a", cross="SAME_HOST_PEER"), fake_receipt("b", cross="SAME_HOST_PEER"))
    assert not rep["multi_host_qualified"]


def test_failed_and_incomplete_hosts_are_excluded(tmp_path):
    rep = combined(tmp_path, fake_receipt("a"), fake_receipt("b", verdict="FAIL"), fake_receipt("c", verdict="INCOMPLETE"))
    assert rep["selected_hosts"] == ["a"] and rep["single_host_qualified"] and not rep["multi_host_qualified"]


def test_tampered_receipts_are_rejected(tmp_path):
    r = fake_receipt("a")
    r["qualified_workers"] = 999
    good, bad = Q.load_receipts(write(tmp_path, r), None)
    assert not good and "receipt_hash_mismatch" in bad[0]["problems"]
    rep = Q.combine(good, bad)
    assert not rep["execution_hosts_ready_for_authorization"] and "invalid_receipts_present" in rep["not_ready_reasons"]


def test_hmac_signed_receipts_verify_only_with_the_key(tmp_path):
    key = b"owner-provisioned-secret"
    body = {k: v for k, v in fake_receipt("a").items() if k not in ("receipt_sha256", "signature")}
    signed = Q.seal(body, key)
    assert signed["signature"]["scheme"] == "hmac-sha256" and Q.verify_seal(signed, key) == []
    assert "signature_invalid" in Q.verify_seal(signed, b"wrong-key")
    assert "signature_present_but_no_key_supplied" in Q.verify_seal(signed, None)
    forged = {**signed, "signature": {"scheme": "hmac-sha256", "value": "0" * 64}}
    assert "signature_invalid" in Q.verify_seal(forged, key)
    assert Q.verify_seal(fake_receipt("a"), None) == [] and fake_receipt("a")["signature"]["scheme"] == "none"


@pytest.mark.parametrize("mutate,problem", [
    (lambda b: b.update(quick=True), "quick_receipt_not_a_qualification"),
    (lambda b: b.update(tool_sha256="0" * 64), "tool_sha256_differs_from_this_tool"),
    (lambda b: b["bindings"].update(shard_layer_sha256="0" * 64), "bindings_mismatch"),
    (lambda b: b.update(rng_constructed=True), "receipt_does_not_attest_rng_free_no_validation_no_authorization"),
    (lambda b: b.update(authorization_read=True), "receipt_does_not_attest_rng_free_no_validation_no_authorization"),
    (lambda b: b.update(schema="other"), "schema_mismatch")])
def test_receipts_with_wrong_bindings_or_attestations_are_invalid(tmp_path, mutate, problem):
    body = {k: v for k, v in fake_receipt("a").items() if k not in ("receipt_sha256", "signature")}
    mutate(body)
    good, bad = Q.load_receipts(write(tmp_path, Q.seal(body, None)), None)
    assert not good and problem in bad[0]["problems"]


def test_duplicate_labels_block_readiness(tmp_path):
    p1 = tmp_path / "one"
    p1.mkdir()
    a = write(p1, fake_receipt("a"))
    p2 = tmp_path / "two"
    p2.mkdir()
    b = write(p2, fake_receipt("a"))
    good, bad = Q.load_receipts(a + b, None)
    rep = Q.combine(good, bad)
    assert any(r.startswith("duplicate_labels") for r in rep["not_ready_reasons"]) and not rep["execution_hosts_ready_for_authorization"]


# ------------------------------------------------------------------------------------------------ end to end and RNG-free
def test_quick_host_run_end_to_end_seals_a_receipt_and_combine_refuses_it_as_a_qualification(tmp_path):
    rc = Q.main(["host", "--dir", str(tmp_path), "--label", "quick", "--receipts-dir", str(tmp_path / "r"), "--quick"])
    path = tmp_path / "r/receipt_quick.json"
    receipt = json.loads(path.read_text())
    assert rc == 0 and receipt["verdict"] == "PASS" and receipt["quick"] is True
    assert set(receipt["checks"]) == set(Q.CHECK_ORDER) and Q.verify_seal(receipt, None) == []
    assert receipt["rng_constructed"] is False and receipt["validation_worlds"] == 0 and receipt["authorization_read"] is False
    assert receipt["bindings"] == {"bundle_sha256": Q.EXPECTED_BUNDLE_SHA256, "shard_layer_sha256": Q.EXPECTED_LAYER_SHA256}
    assert receipt["checks"]["fastmetric_bit_identity"]["detail"]["bitwise_identical"] is True
    assert receipt["checks"]["worker_scaling"]["detail"]["qualified_workers"] >= 1
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("qual_")]      # work directory cleaned on PASS
    rc = Q.main(["combine", str(path), "--out", str(tmp_path / "rep.json")])
    rep = json.loads((tmp_path / "rep.json").read_text())
    assert rc == 2 and not rep["execution_hosts_ready_for_authorization"] and rep["invalid_receipts"][0]["problems"] == ["quick_receipt_not_a_qualification"]


def test_tool_constructs_no_random_generator_reads_no_authorization_and_touches_no_protocol_file(tmp_path, monkeypatch):
    src = (QDIR / "qualify.py").read_text()
    for banned in ("import random", "default_rng", "RandomState", "multiprocessing", "import secrets", "import uuid", "urandom",
                   "SeedRegistry", "R.authorize", "assert_equivalence_gate", "run_mode("):
        assert banned not in src, banned
    trips = []

    def trip(name):
        def f(*a, **k):
            trips.append(name)
            raise AssertionError(name)
        return f
    monkeypatch.setattr(random, "Random", trip("random.Random"))
    monkeypatch.setattr(np.random, "default_rng", trip("default_rng"))
    monkeypatch.setattr(np.random, "Generator", trip("Generator"))
    monkeypatch.setattr(np.random, "RandomState", trip("RandomState"))
    monkeypatch.setattr(os, "urandom", trip("urandom"))
    Q.numeric_canary()
    fw = Q.FastWorkload()
    fw.measure(5)
    assert Q.check_hardlink(ctx_for(tmp_path / "h"))["status"] == "PASS"
    assert not trips
    manifest = json.loads((Q.BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    for rel, digest in {**manifest["files_sha256"], **manifest["pinned_inputs_sha256"]}.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel
    assert Q.sha256_file(SL.LAYER_PATH) == Q.EXPECTED_LAYER_SHA256
