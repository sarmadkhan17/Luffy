"""Guard the RNG-free M3.2 shard / resume / merge layer (bundle v3): partition, exclusivity, crash-restart, tamper,
detection of missing/duplicate/overlapping work, deterministic merge, and byte-identity with the sequential runner.

No test here constructs a random generator; worlds come from arithmetic tokens (non-inferential fixtures)."""
import contextlib
import hashlib
import itertools
import json
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
LAYER_DIR = ROOT / "scripts/m32_scheme_d_sharding_20260921"
sys.path.insert(0, str(LAYER_DIR))
import shard_fixture as F  # noqa: E402
import shard_layer as SL  # noqa: E402

CLI = [sys.executable, str(LAYER_DIR / "shard_layer.py")]
ENV = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
SYN_CELLS = (("N1/C0", 7), ("P1/C0", 5), ("P7/C1", 4))


class Crash(BaseException):
    pass


def synthetic_receipt(cell, w, draws):
    """Cheap layer-level receipt (valid for the layer's checks and for ``world_summary``); no runner statistics."""
    body = {"schema": SL.WORLD_SCHEMA, "cell": cell["name"], "phase": cell["phase"], "world": w,
            "seed_tuples": SL.expected_tuples(cell, w)}
    if w % 4 == 3:
        body["world_refused"] = ["link_crossing"]
    else:
        body.update({"orbit_size": "1", "draws": draws, "statistic_path": "fast", "refusals": {}, "signed_hex": [],
                     "exceedances": [], "p": [f"{1 + (w * 7 + h) % 5}/{draws + 1}" for h in range(384)], "rejected": [],
                     "R": w % 3, "V": int(w % 3 > 0 and w % 2 == 1), "true_discoveries": 0, "attribution_errors": 0})
    return SL.self_hashed(body)


def make_ctx(cells=SYN_CELLS, shard_worlds=3, overrides=None, evaluator=None):
    R = SL.runner()
    common = {"bundle_sha256": R.bundle_sha256(), "authorization_sha256": None, "runner_sha256": SL.sha256_file(R.__file__),
              "environment_lock_sha256": SL.sha256_file(SL.BUNDLE / "artifacts/environment_lock.json"),
              "environment": {"python": "x", "numpy": "y", "openblas_threads": "1"}, "non_inferential": True,
              **(overrides or {})}
    ctx = SL.Context(mode="fixture", plan=F.fixture_plan(R, cells), shard_worlds=shard_worlds, draws=3,
                     bindings_common=common, evaluator=lambda c, w: None,
                     bootstrap=SL.bootstrap_provider(F.StubRegistry(R.MASTER_SEED)))
    ctx.evaluator = evaluator or (lambda cell, w: synthetic_receipt(cell, w, ctx.draws))
    return ctx


def tree(path: Path) -> dict:
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(Path(path).rglob("*")) if p.is_file()}


def completed_run(tmp_path, **kw):
    ctx = make_ctx(**kw)
    out = tmp_path / "run"
    SL.work(out, ctx)
    return out, ctx


# ------------------------------------------------------------------------------------------------ partition
@pytest.mark.parametrize("shard_worlds", (1, 7, 25, 50, 64, 3000))
def test_production_plan_partition_covers_every_world_exactly_once(shard_worlds):
    plan = SL.normalise_plan(SL.runner().cell_plan())
    assert sum(c["worlds"] for c in plan) == 75_000
    shards = SL.make_shards(plan, shard_worlds)
    assert [s["id"] for s in shards] == list(range(len(shards)))
    cursor = 0
    for s in shards:
        assert s["global_lo"] == cursor and s["lo"] < s["hi"] <= plan[s["cell_index"]]["worlds"]
        assert s["hi"] - s["lo"] <= shard_worlds and s["cell"] == plan[s["cell_index"]]["name"]
        assert s["global_hi"] - s["global_lo"] == s["hi"] - s["lo"]
        cursor = s["global_hi"]
    assert cursor == 75_000
    for ci in (0, 19, 20, 54):                                         # inverse map on cell edges
        for w in (0, plan[ci]["worlds"] // 2, plan[ci]["worlds"] - 1):
            sid = SL.shard_of(plan, shard_worlds, ci, w)
            assert shards[sid]["cell_index"] == ci and shards[sid]["lo"] <= w < shards[sid]["hi"]


def test_partition_is_pure_function_and_worker_assignment_partitions_shards():
    plan = SL.normalise_plan(SL.runner().cell_plan())
    assert SL.make_shards(plan, 25) == SL.make_shards(plan, 25) and len(SL.make_shards(plan, 25)) == 3000
    ctx = make_ctx(cells=(("N1/C0", 7), ("P1/C0", 5), ("P7/C1", 4)), shard_worlds=2)
    ids = [s["id"] for s in ctx.shards]
    for workers in (1, 2, 3, 16):
        static = [i["id"] for w in range(workers) for i in SL.assigned_shards(ctx, w, workers, "static")]
        assert sorted(static) == ids
        for w in range(workers):
            assert sorted(s["id"] for s in SL.assigned_shards(ctx, w, workers, "dynamic")) == ids
    for bad in ((0, 0), (2, 2), (-1, 3)):
        with pytest.raises(SL.ShardError):
            SL.assigned_shards(ctx, bad[0], bad[1], "static")
    for bad in (0, -3, True, 2.5):
        with pytest.raises(SL.ShardError):
            SL.make_shards(SL.normalise_plan(ctx.plan), bad)
    with pytest.raises(SL.ShardError):
        SL.normalise_plan([{"name": "a", "worlds": 3}, {"name": "a", "worlds": 3}])


# ------------------------------------------------------------------------------------------------ exclusivity / bindings
def test_completed_shards_are_never_overwritten_and_reruns_are_noops(tmp_path):
    out, ctx = completed_run(tmp_path)
    before = tree(out / "shards")
    calls = []
    ctx.evaluator = lambda c, w: calls.append((c["name"], w))
    stats = SL.work(out, ctx)
    assert set(stats["shards"]) == {"already_complete"} and calls == []
    assert tree(out / "shards") == before
    shard = ctx.shards[0]
    with pytest.raises(SL.ShardError, match="refuse_overwrite"):
        SL.publish_exclusive(SL.shard_paths(out, 0)["complete"], b"{}")
    with pytest.raises(SL.ShardError, match="refuse_overwrite"):
        SL.publish_exclusive(SL.shard_paths(out, shard["id"])["receipts"], b"")
    assert tree(out / "shards") == before


def test_every_shard_is_bound_to_bundle_authorization_runner_layer_and_environment(tmp_path):
    out, ctx = completed_run(tmp_path)
    completion = SL.read_completion(SL.shard_paths(out, 0)["complete"])
    b = completion["bindings"]
    assert b["bundle_sha256"] == SL.runner().bundle_sha256() == "eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01"
    assert b["runner_sha256"] == SL.sha256_file(SL.runner().__file__) and b["shard_layer_sha256"] == SL.sha256_file(SL.LAYER_PATH)
    assert b["environment_lock_sha256"] == SL.sha256_file(SL.BUNDLE / "artifacts/environment_lock.json")
    assert "authorization_sha256" in b and b["plan_sha256"] and b["shard_plan_sha256"] and b["draws"] == 3
    assert completion["run_manifest_sha256"] == SL.sha256_file(SL.run_manifest_path(out))
    assert completion["receipts_sha256"] == SL.sha256_file(SL.shard_paths(out, 0)["receipts"])
    for field in ("bundle_sha256", "authorization_sha256", "runner_sha256", "environment_lock_sha256", "environment",
                  "draws", "non_inferential"):
        other = make_ctx(overrides={field: "tampered" if field != "draws" else 9})
        if field == "draws":
            other.bindings["draws"] = 9
        with pytest.raises(SL.ShardError, match="run_manifest_mismatch"):
            SL.init_run(out, other)                                    # a worker with different bindings is refused
        assert SL.audit(out, other)["invalid"].get("run_manifest") == "run_manifest_mismatch"


def test_shards_from_a_different_binding_are_rejected_even_with_a_matching_manifest(tmp_path):
    out, ctx = completed_run(tmp_path)
    other = make_ctx(overrides={"authorization_sha256": "f" * 64})
    manifest = SL.run_manifest_path(out)
    manifest.unlink()                                                  # attacker swaps in a manifest for the other binding
    SL.publish_exclusive(manifest, SL._pretty(SL.expected_run_manifest(other)))
    report = SL.audit(out, other)
    assert not report["ok"] and all(v.startswith("completion_binding_mismatch:authorization_sha256")
                                    or v == "completion_run_manifest_mismatch" for v in report["invalid"].values())
    with pytest.raises(SL.ShardError, match="merge_refused_audit_failed"):
        SL.merge(out, other)


# ------------------------------------------------------------------------------------------------ crash / resume
def test_crash_mid_shard_resumes_from_the_last_durable_world_and_matches_uninterrupted(tmp_path):
    ref_out, _ = completed_run(tmp_path / "ref")
    out, ctx = tmp_path / "run", make_ctx()
    inner, count = ctx.evaluator, [0]

    def flaky(cell, w):
        if count[0] == 4:                                              # dies inside the second shard, after 1 world
            raise Crash()
        count[0] += 1
        return inner(cell, w)
    ctx.evaluator = flaky
    with pytest.raises(Crash):
        SL.work(out, ctx)
    partial = SL.shard_paths(out, 1)["partial"]
    assert partial.exists() and not SL.shard_paths(out, 1)["complete"].exists()
    assert partial.read_bytes().count(b"\n") == 1 and SL.shard_paths(out, 0)["complete"].exists()
    resumed = []
    ctx.evaluator = lambda cell, w: (resumed.append((cell["name"], w)), inner(cell, w))[1]
    stats = SL.work(out, ctx)
    assert (resumed[0] == ("N1/C0", 4) and ("N1/C0", 3) not in resumed and ("N1/C0", 0) not in resumed)
    assert stats["shards"].get("already_complete") == 1
    assert tree(out / "shards") == tree(ref_out / "shards")
    assert SL.audit(out, ctx)["ok"]


def test_torn_partial_tail_is_dropped_but_corrupt_complete_lines_fail_closed(tmp_path):
    ref_out, _ = completed_run(tmp_path / "ref")
    out, ctx = tmp_path / "run", make_ctx()
    inner, count = ctx.evaluator, [0]

    def flaky(cell, w):
        if count[0] == 2:
            raise Crash()
        count[0] += 1
        return inner(cell, w)
    ctx.evaluator = flaky
    with pytest.raises(Crash):
        SL.work(out, ctx)
    partial = SL.shard_paths(out, 0)["partial"]
    good = partial.read_bytes()
    with open(partial, "ab") as fh:
        fh.write(b'{"schema":"m3.2-scheme-d-wo')                       # torn write (no newline)
    ctx.evaluator = inner
    SL.work(out, ctx)
    assert tree(out / "shards") == tree(ref_out / "shards")
    event = [json.loads(x) for x in (out / "events/worker_0000.jsonl").read_text().splitlines()]
    assert any(e.get("torn_tail_bytes_dropped", 0) == len(b'{"schema":"m3.2-scheme-d-wo') for e in event)
    # corruption (complete line with wrong content) is tamper, not a crash: fail closed
    out2, ctx2 = tmp_path / "run2", make_ctx()
    ctx2.evaluator = flaky
    count[0] = 0
    with pytest.raises(Crash):
        SL.work(out2, ctx2)
    p2 = SL.shard_paths(out2, 0)["partial"]
    p2.write_bytes(p2.read_bytes().replace(b'"R":', b'"R":9', 1))
    ctx2.evaluator = inner
    with pytest.raises(SL.ShardError, match="receipt_internal_hash_mismatch"):
        SL.work(out2, ctx2)


def test_crash_between_finalise_steps_recovers_without_rewriting(tmp_path):
    ref_out, ctx = completed_run(tmp_path / "ref")
    for label in ("receipts_without_completion", "completion_with_stray_partial"):
        out = tmp_path / label
        shutil.copytree(ref_out, out)
        paths = SL.shard_paths(out, 2)
        if label == "receipts_without_completion":
            paths["complete"].unlink()
            shutil.copy(paths["receipts"], paths["partial"])            # crash after the link, before completion/unlink
        else:
            shutil.copy(paths["receipts"], paths["partial"])            # crash after completion, before unlink
        ctx.evaluator = lambda c, w: pytest.fail("finalised work must not be re-evaluated")
        SL.work(out, ctx)
        assert tree(out / "shards") == tree(ref_out / "shards")
        assert (out / "RUN_MANIFEST.json").read_bytes() == (ref_out / "RUN_MANIFEST.json").read_bytes()
    ctx.evaluator = lambda c, w: pytest.fail("no")
    out = tmp_path / "diverged"
    shutil.copytree(ref_out, out)
    paths = SL.shard_paths(out, 2)
    paths["partial"].write_bytes(paths["receipts"].read_bytes() + b"x\n")
    with pytest.raises(SL.ShardError, match="partial_differs_from_final"):
        SL.work(out, ctx)


def test_duplicate_worker_on_the_same_shard_is_refused_by_lock(tmp_path):
    ctx, out = make_ctx(), tmp_path / "run"
    SL.init_run(out, ctx)
    with SL.shard_lock(SL.shard_paths(out, 0)["lock"]) as held:
        assert held
        ctx.evaluator = lambda c, w: pytest.fail("locked shard must not run")
        assert SL.run_shard(out, ctx, ctx.shards[0])["status"] == "locked"
        with SL.shard_lock(SL.shard_paths(out, 0)["lock"]) as second:
            assert second is False


# ------------------------------------------------------------------------------------------------ audit / tamper
def _rewrite_completion(out, sid, mutate):
    path = SL.shard_paths(out, sid)["complete"]
    body = {k: v for k, v in json.loads(path.read_text()).items() if k != "sha256"}
    mutate(body)
    path.write_bytes(SL._pretty(SL.self_hashed(body)))                 # attacker recomputes the completion self-hash


def test_tampered_receipts_are_detected_and_block_the_merge(tmp_path):
    out, ctx = completed_run(tmp_path)
    receipts = SL.shard_paths(out, 1)["receipts"]
    original = receipts.read_bytes()
    receipts.write_bytes(original.replace(b'"R":', b'"R":1', 1))
    report = SL.audit(out, ctx)
    assert not report["ok"] and report["invalid"]["1"].startswith("receipts_checksum_mismatch")
    with pytest.raises(SL.ShardError, match="merge_refused_audit_failed"):
        SL.merge(out, ctx)
    assert not (out / "merged").exists()
    receipts.write_bytes(original)
    assert SL.audit(out, ctx)["ok"]


def test_consistent_but_wrong_identity_swapped_receipts_are_rejected(tmp_path):
    out, ctx = completed_run(tmp_path)
    src, dst = SL.shard_paths(out, 0), SL.shard_paths(out, 1)          # shard 0's worlds presented as shard 1's
    data = src["receipts"].read_bytes()
    dst["receipts"].write_bytes(data)
    _rewrite_completion(out, 1, lambda b: b.update(receipts_sha256=SL.sha256_bytes(data), receipts_bytes=len(data)))
    report = SL.audit(out, ctx)
    assert report["invalid"]["1"].startswith("receipt_identity_mismatch")


def test_missing_shard_is_reported_with_its_gap(tmp_path):
    out, ctx = completed_run(tmp_path)
    for kind in ("complete", "receipts"):
        SL.shard_paths(out, 2)[kind].unlink()
    report = SL.audit(out, ctx)
    assert report["missing"] == [2] and not report["ok"]
    assert report["gaps"] == [{"from": ctx.shards[2]["global_lo"], "to": ctx.shards[2]["global_hi"]}]
    with pytest.raises(SL.ShardError, match="merge_refused_audit_failed"):
        SL.merge(out, ctx)


def test_duplicated_shard_files_report_overlap_and_duplicate_worlds(tmp_path):
    out, ctx = completed_run(tmp_path)
    for kind in ("complete", "receipts"):                               # shard 1's files copied under shard 2's names
        shutil.copy(SL.shard_paths(out, 1)[kind], SL.shard_paths(out, 2)[kind])
    report = SL.audit(out, ctx)
    assert not report["ok"] and report["invalid"]["2"].startswith("completion_shard_mismatch")
    assert report["overlaps"] and report["gaps"]
    assert set(report["duplicate_worlds"]) == set(range(ctx.shards[1]["global_lo"], ctx.shards[1]["global_hi"]))


def test_completion_claiming_an_overlapping_range_is_detected_even_with_a_recomputed_hash(tmp_path):
    out, ctx = completed_run(tmp_path)
    _rewrite_completion(out, 0, lambda b: b["shard"].update(global_hi=b["shard"]["global_hi"] + 1))
    report = SL.audit(out, ctx)
    assert not report["ok"] and "0" in report["invalid"] and report["overlaps"]
    assert ctx.shards[1]["global_lo"] in report["duplicate_worlds"]


def test_unexpected_extra_shard_and_stray_temp_files_are_reported(tmp_path):
    out, ctx = completed_run(tmp_path)
    (out / "shards" / "shard_000099.complete.json").write_text("{}")
    (out / "shards" / "shard_000001.complete.json.tmp.123").write_text("")
    report = SL.audit(out, ctx)
    assert sorted(report["unexpected_files"]) == ["shard_000001.complete.json.tmp.123", "shard_000099.complete.json"]
    assert not report["ok"]
    SL.work(out, ctx)                                                   # a restarted worker cleans its own temp files
    assert (out / "shards" / "shard_000001.complete.json.tmp.123").exists() is False


def test_partial_shard_blocks_merge_and_run_manifest_tamper_is_detected(tmp_path):
    out, ctx = completed_run(tmp_path)
    for p in ("complete", "receipts"):
        SL.shard_paths(out, 3)[p].unlink()
    SL.shard_paths(out, 3)["partial"].write_bytes(b"")
    report = SL.audit(out, ctx)
    assert report["partial"] == [3] and not report["ok"]
    with pytest.raises(SL.ShardError, match="merge_refused_audit_failed"):
        SL.merge(out, ctx)
    SL.work(out, ctx)
    assert SL.audit(out, ctx)["ok"]
    manifest = SL.run_manifest_path(out)
    manifest.write_bytes(manifest.read_bytes().replace(b'"shard_worlds": 3', b'"shard_worlds": 4'))
    assert SL.audit(out, ctx)["invalid"]["run_manifest"] == "run_manifest_mismatch"


# ------------------------------------------------------------------------------------------------ merge determinism
def test_merge_is_deterministic_across_workers_order_and_refuses_overwrite(tmp_path):
    outs = {}
    for label, (workers, assign, reverse) in {"one": (1, "static", False), "three": (3, "static", True),
                                              "dyn": (2, "dynamic", True)}.items():
        ctx, out = make_ctx(), tmp_path / label
        for worker in (reversed(range(workers)) if reverse else range(workers)):
            SL.work(out, ctx, worker, workers, assign)
        SL.merge(out, ctx)
        outs[label] = tree(out / "merged")
    assert outs["one"] == outs["three"] == outs["dyn"]
    assert set(outs["one"]) == {"N1_C0.receipts.jsonl", "P1_C0.receipts.jsonl", "P7_C1.receipts.jsonl", "run_result.json",
                                "MERGE_MANIFEST.json"}
    lines = outs["one"]["N1_C0.receipts.jsonl"].splitlines()
    assert [json.loads(x)["world"] for x in lines] == list(range(7))    # world order within the cell
    with pytest.raises(SL.ShardError, match="refuse_overwrite:merged"):
        SL.merge(tmp_path / "one", make_ctx())
    manifest = json.loads(outs["one"]["MERGE_MANIFEST.json"])
    assert manifest["non_inferential"] is True and manifest["worlds"] == 16 and len(manifest["shard_completion_sha256"]) == 7


def test_shards_from_independent_output_dirs_merge_identically_for_multi_host_runs(tmp_path):
    ref_out, ref_ctx = completed_run(tmp_path / "ref")
    SL.merge(ref_out, ref_ctx)
    ctx = make_ctx()
    host_a, host_b = tmp_path / "host_a", tmp_path / "host_b"
    SL.work(host_a, ctx, 0, 2, "static")
    SL.work(host_b, ctx, 1, 2, "static")
    assert (host_a / "RUN_MANIFEST.json").read_bytes() == (host_b / "RUN_MANIFEST.json").read_bytes() \
        == (ref_out / "RUN_MANIFEST.json").read_bytes()
    assert not SL.audit(host_a, ctx)["ok"]                              # half the shards are on the other host
    for src in (host_b / "shards").iterdir():
        if src.name.endswith((".complete.json", ".receipts.jsonl")):
            shutil.copy(src, host_a / "shards" / src.name)              # copy finished, self-verifying shard files
    assert SL.audit(host_a, ctx)["ok"]
    SL.merge(host_a, ctx)
    assert tree(host_a / "merged") == tree(ref_out / "merged")


def test_foreign_or_stale_merge_staging_is_never_deleted_and_published_merge_is_never_touched(tmp_path):
    out, ctx = completed_run(tmp_path)
    for name in ("merged.partial", "merged.staging.otherhost.7.1.0"):     # a legacy dir and another merger's staging
        (out / name).mkdir()
        (out / name / "junk").write_text("not ours")
    manifest = SL.merge(out, ctx)
    assert (out / "merged/run_result.json").exists()
    assert (out / "merged.partial/junk").read_text() == "not ours"
    assert (out / "merged.staging.otherhost.7.1.0/junk").read_text() == "not ours"
    assert manifest["informational"]["stale_staging_left_untouched"] == ["merged.partial", "merged.staging.otherhost.7.1.0"]
    assert not [p for p in out.iterdir() if p.name.startswith("merged.staging.") and p.name != "merged.staging.otherhost.7.1.0"]


# ------------------------------------------------------------------------------------------------ RNG-free / fail closed
def test_layer_and_fixture_construct_no_random_generator(tmp_path, monkeypatch):
    import random
    trips = []

    def trip(*a, **k):
        trips.append(1)
        raise AssertionError("random generator construction attempted")
    for name in ("PCG64", "SeedSequence", "default_rng", "Generator", "RandomState"):
        monkeypatch.setattr(np.random, name, trip)
    monkeypatch.setattr(random, "Random", trip)
    R = SL.runner()
    with F.installed(R):
        ctx = SL.fixture_context(cells=(("N1/C0", 2), ("P1/C0", 1)), shard_worlds=2)
        SL.work(tmp_path / "o", ctx)
        SL.merge(tmp_path / "o", ctx)
    assert not trips
    source = SL.LAYER_PATH.read_text() + (LAYER_DIR / "shard_fixture.py").read_text()
    for token in ("np.random", "numpy.random", "default_rng", "PCG64", "SeedSequence", "RandomState", "import random"):
        assert token not in source.replace("Generator construction", ""), token


def test_production_cli_fails_closed_without_owner_authorization(tmp_path):
    out = tmp_path / "out"
    base = [*CLI, "work", "--out", str(out), "--shard-worlds", "25"]
    proc = subprocess.run(base, capture_output=True, text=True, env=ENV, timeout=120)
    assert proc.returncode == 2 and "authorization_required_for_production" in proc.stderr
    bogus = tmp_path / "auth.json"
    bogus.write_text(json.dumps({"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "validation",
                                 "bundle_sha256": "0" * 64, "authorized_by": "owner"}))
    proc = subprocess.run([*base, "--authorization", str(bogus)], capture_output=True, text=True, env=ENV, timeout=120)
    assert proc.returncode == 2 and "authorization_mismatch" in proc.stderr
    proc = subprocess.run([*base, "--authorization", str(bogus), "--draws", "3"], capture_output=True, text=True, env=ENV, timeout=120)
    assert proc.returncode == 2 and "fixture_only_option_in_production" in proc.stderr
    assert not out.exists()


def test_production_context_requires_measured_benchmark_and_layer_binding(tmp_path, monkeypatch):
    R = SL.runner()
    auth = tmp_path / "auth.json"
    record = {"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "validation", "authorized_by": "owner",
              "bundle_sha256": R.bundle_sha256()}
    auth.write_text(json.dumps(record))
    monkeypatch.setattr(R, "assert_environment", lambda: {"python": "3.12.3", "numpy": "2.5.2", "openblas_threads": "1"})
    monkeypatch.setattr(R, "assert_isolation", lambda: None)
    monkeypatch.setattr(R, "authorize", lambda path, mode: object())
    monkeypatch.setattr(R, "SeedRegistry", lambda a: pytest.fail("no generator may be built before the bindings verify"))
    with pytest.raises(SL.ShardError, match="validation_requires_measured_fast_benchmark_in_authorization"):
        SL.production_context(auth, 25)
    record.update(runtime_estimate_cpu_hours=19000.0, fast_benchmark_receipt_sha256="a" * 64)
    auth.write_text(json.dumps(record))
    with pytest.raises(SL.ShardError, match="authorization_missing_shard_layer_binding"):
        SL.production_context(auth, 25)
    record.update(shard_layer_sha256=SL.sha256_file(SL.LAYER_PATH), shard_worlds=50)
    auth.write_text(json.dumps(record))
    with pytest.raises(SL.ShardError, match="authorization_missing_shard_layer_binding"):
        SL.production_context(auth, 25)                                 # authorized shard size must match


# ------------------------------------------------------------------------------------------------ sequential equivalence
@pytest.fixture(scope="module")
def sequential(tmp_path_factory):
    """The real ``run_validation`` on the fixture plan (arithmetic tokens, real world generation and receipts)."""
    R = SL.runner()
    out = tmp_path_factory.mktemp("seq") / "o"
    plan = F.fixture_plan(R)
    record = {"runtime_estimate_cpu_hours": 1.0, "fast_benchmark_receipt_sha256": "0" * 64}
    old_plan, old_b = R.cell_plan, R.B
    try:
        with F.installed(R):
            R.cell_plan, R.B = (lambda: plan), F.FIXTURE_DRAWS
            R.run_validation(None, F.StubRegistry(R.MASTER_SEED), out, record)
    finally:
        R.cell_plan, R.B = old_plan, old_b
    return tree(out)


def compare(merged: Path, sequential_tree: dict):
    got = tree(merged)
    assert got.pop("MERGE_MANIFEST.json")
    assert set(got) == set(sequential_tree) and len(got) == 6
    for name, data in sequential_tree.items():
        assert got[name] == data, name                                   # byte-identical receipts and run_result.json


def test_sharded_merge_reproduces_sequential_runner_exactly(tmp_path, sequential):
    R = SL.runner()
    assert set(sequential) == {"N1_C0.receipts.jsonl", "N3_C4.receipts.jsonl", "P1_C0.receipts.jsonl",
                               "P4_C3.receipts.jsonl", "P7_C1.receipts.jsonl", "run_result.json"}
    receipts = [json.loads(line) for name, data in sequential.items() if name.endswith(".jsonl") for line in data.splitlines()]
    assert len(receipts) == 16 and any("world_refused" in r for r in receipts) and any(r.get("R", 0) > 0 for r in receipts)
    result = json.loads(sequential["run_result.json"])
    assert result["overall"]["cells_evaluated"] == 5 and result["cells"]["P1/C0"]["fdp_bootstrap_upper"] is not None
    for shard_worlds, workers, assign in ((3, 1, "static"), (1, 3, "static")):
        out = tmp_path / f"sw{shard_worlds}"
        with F.installed(R):
            ctx = SL.fixture_context(shard_worlds=shard_worlds)
            for worker in reversed(range(workers)):
                SL.work(out, ctx, worker, workers, assign)
            assert SL.audit(out, ctx)["ok"]
            SL.merge(out, ctx)
        compare(out / "merged", sequential)


def _cli(args, **kw):
    return subprocess.run([*CLI, *args], capture_output=True, text=True, env=ENV, timeout=600, **kw)


def test_cli_crash_and_restart_then_merge_matches_sequential(tmp_path, sequential):
    out = tmp_path / "run"
    common = ["--fixture", "--out", str(out), "--shard-worlds", "3"]
    crashed = _cli(["work", *common, "--crash-after-worlds", "5"])
    assert crashed.returncode == 137 and list((out / "shards").glob("*.partial"))
    assert _cli(["audit", *common]).returncode == 1                     # incomplete: audit fails, merge refuses
    assert _cli(["merge", *common]).returncode == 2
    resumed = _cli(["work", *common])
    assert resumed.returncode == 0 and json.loads(resumed.stdout)["worlds_evaluated"] == 16 - 5
    assert _cli(["audit", *common]).returncode == 0 and _cli(["merge", *common]).returncode == 0
    compare(out / "merged", sequential)
    again = _cli(["work", *common])
    assert json.loads(again.stdout)["worlds_evaluated"] == 0            # nothing re-run after completion


def test_cli_sigkill_mid_shard_then_restart_matches_sequential(tmp_path, sequential):
    out = tmp_path / "run"
    common = ["--fixture", "--out", str(out), "--shard-worlds", "4"]
    proc = subprocess.Popen([*CLI, "work", *common], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.time() + 120
    while time.time() < deadline:
        parts = list((out / "shards").glob("*.partial")) if (out / "shards").exists() else []
        if parts and parts[0].read_bytes().count(b"\n") >= 1:
            break
        time.sleep(0.05)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    assert proc.returncode == -signal.SIGKILL
    assert _cli(["merge", *common]).returncode == 2
    assert _cli(["work", *common]).returncode == 0
    assert _cli(["merge", *common]).returncode == 0
    compare(out / "merged", sequential)


def test_two_concurrent_dynamic_workers_never_duplicate_a_shard(tmp_path, sequential):
    out = tmp_path / "run"
    common = ["--fixture", "--out", str(out), "--shard-worlds", "1", "--assign", "dynamic", "--workers", "2"]
    procs = [subprocess.Popen([*CLI, "work", *common, "--worker", str(i)], env=ENV, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for i in range(2)]
    stats = [json.loads(p.communicate(timeout=600)[0]) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum(s["worlds_evaluated"] for s in stats) == 16              # each world computed once across both workers
    assert _cli(["audit", "--fixture", "--out", str(out), "--shard-worlds", "1"]).returncode == 0
    assert _cli(["merge", "--fixture", "--out", str(out), "--shard-worlds", "1"]).returncode == 0
    compare(out / "merged", sequential)


def test_production_evaluator_uses_the_same_calls_and_seed_positions_as_run_cell():
    """The production registry cannot be exercised without RNG, so pin the wiring textually against ``run_cell``."""
    import inspect
    squash = lambda text: " ".join(text.split())                        # noqa: E731
    runner_src, layer_src = squash(inspect.getsource(SL.runner().run_cell)), squash(inspect.getsource(SL.make_evaluator))
    same = (                                                            # fragments identical on both sides
        'registry.generator(phase, c, w_dgp, w, 0), registry.generator(phase, c, w_dgp, w, 1))',
        'cell["dgp"] if cell["kind"] == "null" else cell["scenario"]',
        '1 if cell["kind"] == "power" else cell["dgp"], c, w,',
        'injected = ', 'if cell["scenario"] else ()',
        'registry.generator(phase, c, w_dgp, w, 3))', 'registry.generator(phase, c, w_dgp, w, 2), phase, cell["name"], tuples,',
        'draws=draws, truth=truth, injected=injected)')
    for fragment in same:
        assert fragment in runner_src and fragment in layer_src, fragment
    assert 'world = apply_injection(world, cell["scenario"],' in runner_src
    assert 'world = R.apply_injection(world, cell["scenario"],' in layer_src
    assert 'receipt = world_receipt(world,' in runner_src and 'return R.world_receipt(world,' in layer_src
    assert 'V.INJECTIONS[cell["scenario"]]' in runner_src and 'R.V.INJECTIONS[cell["scenario"]]' in layer_src
    assert 'tuples = {n: [MASTER_SEED, phase, c, w_dgp, w, STREAM[n]]' in runner_src
    assert 'R.MASTER_SEED, cell["phase"], cell["correlation"], dgp, world, R.STREAM[n]' in squash(
        inspect.getsource(SL.expected_tuples))


def test_bundle_v3_and_statistic_files_are_untouched():
    manifest = json.loads((SL.BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    assert SL.sha256_file(SL.BUNDLE / "BUNDLE_MANIFEST.json") == \
        "eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01"
    for rel, digest in {**manifest["files_sha256"], **manifest["pinned_inputs_sha256"]}.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel


# ================================================================================================ D1 / D2 / D3 hardening
def legacy_publish_pid_only(path: Path, data: bytes) -> None:
    """The pre-hardening algorithm (temp name = PID only, plain open): kept here to prove the D1 schedules are real."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise SL.ShardError(f"refuse_overwrite:{path.name}") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def freeze_identity(monkeypatch, host="containerhost", pid=1, ns=123456789):
    """Two workers on different hosts / containers that happen to share host name, PID and clock reading."""
    monkeypatch.setattr(SL.socket, "gethostname", lambda: host)
    monkeypatch.setattr(os, "getpid", lambda: pid)
    monkeypatch.setattr(SL.time, "time_ns", lambda: ns)
    monkeypatch.setattr(SL, "_TMP_COUNTER", itertools.count())


def hook_link(monkeypatch, action):
    """Run ``action`` once, just before the first ``os.link`` (host A is paused between fsync and link)."""
    real, state = os.link, {"done": False}

    def linked(src, dst, **kw):
        if not state["done"]:
            state["done"] = True
            action(Path(src))
        return real(src, dst, **kw)
    monkeypatch.setattr(os, "link", linked)


# ---- D1: temp-file collisions
def test_d1_legacy_pid_only_temp_name_crashes_the_publisher_when_pids_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getpid", lambda: 1)
    target, payload = tmp_path / "RUN_MANIFEST.json", b"A" * 5000
    hook_link(monkeypatch, lambda tmp: legacy_publish_pid_only(target, payload))       # host B publishes the same file
    with pytest.raises(FileNotFoundError):                              # host A lost its temp file: uncaught crash
        legacy_publish_pid_only(target, payload)


def test_d1_legacy_pid_only_temp_name_publishes_a_truncated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getpid", lambda: 1)
    target, payload = tmp_path / "RUN_MANIFEST.json", b"A" * 5000

    def host_b_truncates_hosts_a_fsynced_tmp(tmp):
        open(tmp.with_name(f"{target.name}.tmp.1"), "wb").close()      # same PID -> same name -> open("wb") truncates
    hook_link(monkeypatch, host_b_truncates_hosts_a_fsynced_tmp)
    legacy_publish_pid_only(target, payload)
    assert target.stat().st_size == 0                                   # the published name showed a torn file


def test_d1_temp_names_carry_host_pid_time_and_never_reuse_a_name(tmp_path, monkeypatch):
    freeze_identity(monkeypatch)
    name = SL.unique_tmp_name("x.json")
    assert name.startswith("x.json.tmp.containerhost.1.123456789.")
    monkeypatch.setattr(SL, "_TMP_COUNTER", itertools.count())         # another process restarts its counter at 0
    target = tmp_path / "x.json"
    first, fd1 = SL.create_exclusive_tmp(target)
    monkeypatch.setattr(SL, "_TMP_COUNTER", itertools.count())         # ... and derives the very same name
    second, fd2 = SL.create_exclusive_tmp(target)
    os.close(fd1), os.close(fd2)
    assert first != second and first.exists() and second.exists()      # O_EXCL clash was retried, nothing reopened
    monkeypatch.setattr(SL, "_TMP_COUNTER", itertools.count())
    monkeypatch.setattr(SL, "unique_tmp_name", lambda n: f"{n}.tmp.same")   # even a permanently colliding namer
    (tmp_path / "x.json.tmp.same").write_bytes(b"someone else's data")
    with pytest.raises(SL.ShardError, match="tmp_name_exhausted"):     # fails closed, never opens the existing file
        SL.create_exclusive_tmp(target)
    assert (tmp_path / "x.json.tmp.same").read_bytes() == b"someone else's data"


def test_d1_colliding_identity_interleaves_cannot_crash_or_tear_a_publish(tmp_path, monkeypatch):
    freeze_identity(monkeypatch)
    target, payload = tmp_path / "RUN_MANIFEST.json", b"A" * 5000

    def host_b_publishes_first(tmp):
        monkeypatch.setattr(SL, "_TMP_COUNTER", itertools.count())    # host B: identical host/pid/ns/counter
        SL.publish_exclusive(target, payload)
    hook_link(monkeypatch, host_b_publishes_first)
    with pytest.raises(SL.ShardError, match="refuse_overwrite"):        # clean reason code instead of FileNotFoundError
        SL.publish_exclusive(target, payload)
    assert target.read_bytes() == payload
    assert not [p for p in tmp_path.iterdir() if ".tmp." in p.name]


def test_d1_second_writer_cannot_truncate_the_temp_file_being_published(tmp_path, monkeypatch):
    freeze_identity(monkeypatch)
    target, payload, seen = tmp_path / "RUN_MANIFEST.json", b"A" * 5000, {}

    def host_b_tries_the_same_tmp(tmp):
        monkeypatch.setattr(SL, "_TMP_COUNTER", itertools.count())
        other, fd = SL.create_exclusive_tmp(target)                     # would have been A's name; must be a new one
        os.close(fd)
        seen["a"], seen["b"], seen["a_size"] = tmp, other, tmp.stat().st_size
    hook_link(monkeypatch, host_b_tries_the_same_tmp)
    SL.publish_exclusive(target, payload)
    assert seen["a"] != seen["b"] and seen["a_size"] == len(payload)   # A's fsynced temp file was left alone
    assert target.read_bytes() == payload                               # never published torn
    seen["b"].unlink()


def _init_worker(args):
    out, index = args
    try:
        SL.init_run(Path(out), _INIT_CTX["ctx"])
        return "ok"
    except BaseException as exc:                                        # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"[:80]


_INIT_CTX: dict = {}


def _frozen_child(args):
    os.getpid = lambda: 1                                               # every "container" is PID 1 ...
    SL.socket.gethostname = lambda: "containerhost"                     # ... on the same host name ...
    SL.time.time_ns = lambda: 123456789                                 # ... at the same clock reading
    return _init_worker(args)


def test_d1_concurrent_run_manifest_init_with_identical_pids_never_fails_or_exposes_a_torn_file(tmp_path):
    _INIT_CTX["ctx"] = make_ctx()
    results = []
    for rep in range(6):
        out = tmp_path / f"run{rep}"
        with multiprocessing.get_context("fork").Pool(8) as pool:
            results += pool.map(_frozen_child, [(str(out), i) for i in range(8)])
        assert json.loads((out / "RUN_MANIFEST.json").read_text())["sha256"] == SL.expected_run_manifest(_INIT_CTX["ctx"])["sha256"]
    assert set(results) == {"ok"}


def test_d1_unreadable_run_manifest_is_a_reason_coded_refusal(tmp_path):
    ctx = make_ctx()
    (tmp_path / "RUN_MANIFEST.json").write_bytes(b"")
    with pytest.raises(SL.ShardError, match="run_manifest_unreadable"):
        SL.init_run(tmp_path, ctx)


# ---- D2: concurrent merge
def reference_merge(tmp_path, name="ref"):
    out, ctx = completed_run(tmp_path / name)
    SL.merge(out, ctx)
    return out, tree(out / "merged")


def test_d2_a_second_merger_during_staging_exits_cleanly_and_touches_nothing(tmp_path, monkeypatch):
    _, ref = reference_merge(tmp_path)
    out, ctx = completed_run(tmp_path / "run")
    writes, seen = {"n": 0}, {}
    real_write = SL._StageFile.write

    def write(self, data):
        writes["n"] += 1
        if writes["n"] == 3 and "b" not in seen:                        # merger A is mid-way through its staging dir
            staging = [p for p in out.iterdir() if p.name.startswith("merged.staging.")]
            seen["before"] = {p.name: {f.name: f.read_bytes() for f in p.iterdir()} for p in staging}
            try:
                SL.merge(out, make_ctx())                               # merger B, same output directory
                seen["b"] = "MERGED"
            except SL.ShardError as exc:
                seen["b"] = str(exc)
            seen["after"] = {p.name: {f.name: f.read_bytes() for f in p.iterdir()}
                             for p in out.iterdir() if p.name.startswith("merged.staging.")}
        return real_write(self, data)
    monkeypatch.setattr(SL._StageFile, "write", write)
    SL.merge(out, ctx)
    assert seen["b"] == "merge_locked_by_another_process"
    assert len(seen["before"]) == 1 and seen["after"] == seen["before"]   # B neither deleted nor changed A's staging
    assert {k: v for k, v in tree(out / "merged").items() if k != "MERGE_MANIFEST.json"} == \
        {k: v for k, v in ref.items() if k != "MERGE_MANIFEST.json"}
    assert not [p for p in out.iterdir() if p.name.startswith("merged.staging.")]


def _merge_child(args):
    out = args
    try:
        SL.merge(Path(out), make_ctx())
        return "merged"
    except SL.ShardError as exc:
        return f"ShardError: {str(exc)[:40]}"
    except BaseException as exc:                                        # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:60]}"


def test_d2_many_concurrent_mergers_exactly_one_publishes_and_the_rest_fail_with_reason_codes(tmp_path):
    _, ref = reference_merge(tmp_path)
    out, _ctx = completed_run(tmp_path / "run")
    with multiprocessing.get_context("fork").Pool(6) as pool:
        results = pool.map(_merge_child, [str(out)] * 6)
    assert results.count("merged") == 1
    assert all(r in ("merged", "ShardError: merge_locked_by_another_process", "ShardError: refuse_overwrite:merged")
               for r in results), results
    got = tree(out / "merged")
    assert {k: v for k, v in got.items() if k != "MERGE_MANIFEST.json"} == {k: v for k, v in ref.items() if k != "MERGE_MANIFEST.json"}
    assert not [p for p in out.iterdir() if p.name.startswith("merged.staging.")]


def test_d2_legacy_shared_staging_path_published_an_incomplete_archive(tmp_path):
    """The pre-hardening race: merger B's ``rmtree(merged.partial)`` + ``mkdir`` while merger A is writing into the same
    fixed path made A publish a directory that lacked its earlier cell files (and still return success)."""
    staging = tmp_path / "merged.partial"
    staging.mkdir()
    (staging / "N1_C0.receipts.jsonl").write_text("a")                  # A's first cell file
    shutil.rmtree(staging)
    staging.mkdir()                                                     # merger B
    (staging / "P1_C0.receipts.jsonl").write_text("b")                  # A's next file lands in B's directory
    os.rename(staging, tmp_path / "merged")
    assert sorted(p.name for p in (tmp_path / "merged").iterdir()) == ["P1_C0.receipts.jsonl"]   # first cell lost


def test_d2_staging_is_private_and_rename_never_replaces(tmp_path):
    dst = tmp_path / "merged"
    dst.mkdir()                                                         # a plain os.rename would silently replace an empty dir
    src = tmp_path / "staging"
    src.mkdir()
    (src / "f").write_text("x")
    with pytest.raises(SL.ShardError, match="refuse_overwrite:merged"):
        SL.rename_noreplace(src, dst)
    assert (src / "f").exists() and list(dst.iterdir()) == []
    dst.rmdir()
    assert SL.rename_noreplace(src, dst) in ("renameat2_noreplace", "rename_under_lock") and (dst / "f").exists()
    a = SL.host_token(), os.getpid(), SL.time.time_ns()
    assert a[0] and "." not in a[0]


def test_d2_failed_merge_removes_only_its_own_staging(tmp_path, monkeypatch):
    out, ctx = completed_run(tmp_path)
    (out / "merged.staging.otherhost.9.9.9").mkdir()
    real, calls = SL.verify_shard, {"n": 0}

    def failing(o, c, shard):
        calls["n"] += 1
        if calls["n"] == len(ctx.shards) + 3:                           # after the audit's own pass, mid-merge
            raise Crash()
        return real(o, c, shard)
    monkeypatch.setattr(SL, "verify_shard", failing)
    with pytest.raises(Crash):
        SL.merge(out, ctx)
    monkeypatch.setattr(SL, "verify_shard", real)
    assert not (out / "merged").exists()
    assert sorted(p.name for p in out.iterdir() if p.name.startswith("merged.")) == ["merged.staging.otherhost.9.9.9"]
    _, ref = reference_merge(tmp_path)
    SL.merge(out, ctx)                                                  # retry works and is identical
    assert {k: v for k, v in tree(out / "merged").items() if k != "MERGE_MANIFEST.json"} == \
        {k: v for k, v in ref.items() if k != "MERGE_MANIFEST.json"}


# ---- D3: durability and publication order
def test_d3_publication_order_is_fsync_files_fsync_dirs_rename_fsync_verify_manifest(tmp_path, monkeypatch):
    out, ctx = completed_run(tmp_path)
    ops, real_fsync, real_link, real_rename = [], os.fsync, os.link, SL.rename_noreplace

    def path_of(fd):
        return os.readlink(f"/proc/self/fd/{fd}")

    def fsync(fd):
        ops.append(("fsync", path_of(fd)))
        return real_fsync(fd)

    def link(src, dst, **kw):
        ops.append(("link", str(dst)))
        return real_link(src, dst, **kw)

    def rename(src, dst):
        ops.append(("rename", str(dst), sorted(p.name for p in Path(src).iterdir())))
        return real_rename(src, dst)
    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(SL, "rename_noreplace", rename)
    SL.merge(out, ctx)
    monkeypatch.undo()
    kinds = [o[0] for o in ops]
    r = kinds.index("rename")
    staged = ["N1_C0.receipts.jsonl", "P1_C0.receipts.jsonl", "P7_C1.receipts.jsonl", "run_result.json"]
    assert ops[r][2] == staged                                          # no MERGE_MANIFEST in the directory being published
    before = [o[1] for o in ops[:r] if o[0] == "fsync"]
    for name in staged:                                                 # every staged receipt/result fsynced before publish
        assert any(p.endswith("/" + name) and "merged.staging." in p for p in before), name
    assert any(p.split("/")[-1].startswith("merged.staging.") for p in before)   # staging directory itself
    after = ops[r + 1:]
    fs_after = [o[1] for o in after if o[0] == "fsync"]
    assert str(out / "merged") in fs_after and str(out) in fs_after     # published dir and its parent
    manifest_link = next(i for i, o in enumerate(after) if o[0] == "link" and o[1].endswith("MERGE_MANIFEST.json"))
    synced_before_marker = [o[1] for o in after[:manifest_link] if o[0] == "fsync"]
    assert str(out / "merged") in synced_before_marker and str(out) in synced_before_marker   # both durable before the marker
    assert (out / "merged/MERGE_MANIFEST.json").exists()


def test_d3_published_files_are_verified_before_the_manifest_is_written(tmp_path, monkeypatch):
    out, ctx = completed_run(tmp_path)
    real = SL.rename_noreplace

    def rename_then_lose_data(src, dst):
        result = real(src, dst)
        victim = Path(dst) / "N1_C0.receipts.jsonl"
        victim.write_bytes(victim.read_bytes()[:-5])                    # the filesystem lost the tail of a file
        return result
    monkeypatch.setattr(SL, "rename_noreplace", rename_then_lose_data)
    with pytest.raises(SL.ShardError, match="merged_content_mismatch:N1_C0.receipts.jsonl"):
        SL.merge(out, ctx)
    assert not (out / "merged/MERGE_MANIFEST.json").exists()            # the commit marker was never written


def test_d3_crash_between_publish_and_manifest_is_finalised_not_overwritten(tmp_path, monkeypatch):
    _, ref = reference_merge(tmp_path)
    out, ctx = completed_run(tmp_path / "run")
    real = SL.publish_exclusive

    def crashing(path, data):
        if Path(path).name == "MERGE_MANIFEST.json":
            raise Crash()
        return real(path, data)
    monkeypatch.setattr(SL, "publish_exclusive", crashing)
    with pytest.raises(Crash):
        SL.merge(out, ctx)
    monkeypatch.setattr(SL, "publish_exclusive", real)
    unmarked = tree(out / "merged")
    assert "MERGE_MANIFEST.json" not in unmarked and set(unmarked) == {k for k in ref if k != "MERGE_MANIFEST.json"}
    manifest = SL.merge(out, make_ctx())                                # fresh process: re-verified, then marked
    assert manifest["informational"]["publish"] == "finalised_existing_unmarked_merged_dir"
    got = tree(out / "merged")
    assert {k: v for k, v in got.items() if k != "MERGE_MANIFEST.json"} == unmarked   # published files never rewritten
    assert json.loads(got["MERGE_MANIFEST.json"])["deterministic_sha256"] == \
        json.loads(ref["MERGE_MANIFEST.json"])["deterministic_sha256"]
    with pytest.raises(SL.ShardError, match="refuse_overwrite:merged"):
        SL.merge(out, make_ctx())


def test_d3_unmarked_merged_dir_that_differs_from_the_shards_is_refused_and_untouched(tmp_path, monkeypatch):
    out, ctx = completed_run(tmp_path)
    real = SL.publish_exclusive
    monkeypatch.setattr(SL, "publish_exclusive", lambda p, d: (_ for _ in ()).throw(Crash()) if Path(p).name == "MERGE_MANIFEST.json" else real(p, d))
    with pytest.raises(Crash):
        SL.merge(out, ctx)
    monkeypatch.setattr(SL, "publish_exclusive", real)
    victim = out / "merged/P1_C0.receipts.jsonl"
    good = victim.read_bytes()
    victim.write_bytes(good[:-30])                                      # torn / truncated file after a power loss
    before = tree(out / "merged")
    with pytest.raises(SL.ShardError, match="merged_content_mismatch:P1_C0.receipts.jsonl"):
        SL.merge(out, make_ctx())
    assert tree(out / "merged") == before                               # nothing overwritten, no manifest written
    victim.write_bytes(good)
    (out / "merged/extra.txt").write_text("x")
    with pytest.raises(SL.ShardError, match="merged_content_mismatch:file_set"):
        SL.merge(out, make_ctx())
    assert not (out / "merged/MERGE_MANIFEST.json").exists()


def test_d3_empty_or_foreign_merged_directory_is_never_replaced(tmp_path):
    out, ctx = completed_run(tmp_path)
    (out / "merged").mkdir()
    with pytest.raises(SL.ShardError, match="merged_content_mismatch"):
        SL.merge(out, ctx)
    assert list((out / "merged").iterdir()) == []


# ---- informational provenance
def test_provenance_records_host_cpu_blas_and_environment_lock_without_touching_deterministic_bytes(tmp_path, monkeypatch):
    ctx = make_ctx()
    out = tmp_path / "run"
    SL.work(out, ctx)
    files = sorted((out / "provenance").glob("prov_*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["sha256"] == SL.sha256_bytes(SL.canon({k: v for k, v in rec.items() if k != "sha256"}).encode())
    assert rec["informational"] is True and rec["hostname"] == SL.socket.gethostname()
    assert rec["environment_lock_sha256"] == ctx.bindings["environment_lock_sha256"] == \
        SL.sha256_file(SL.BUNDLE / "artifacts/environment_lock.json")
    assert rec["cpu_model"] and rec["cpu_flags"] and rec["environment"]["numpy"] and "openblas_threads" in rec["environment"]
    assert rec["blas"]["build"]["name"] and rec["blas"]["runtime_corename"] and len(rec["blas"]["runtime_library_sha256"]) == 64
    assert len(rec["hardware_identity"]) == 64
    started = [json.loads(x) for x in (out / "events/worker_0000.jsonl").read_text().splitlines()][0]
    assert started["provenance_sha256"] == rec["sha256"] and started["host"] == SL.host_token()
    for shard in ctx.shards:                                            # provenance never enters a shard completion
        assert "provenance" not in (out / "shards" / f"shard_{shard['id']:06d}.complete.json").read_text()
    manifest = SL.merge(out, ctx)
    info = manifest["informational"]
    assert [w["sha256"] for w in info["worker_provenance"]] == [rec["sha256"]] and info["worker_hardware_identities_distinct"] == 1
    assert info["merger"]["hardware_identity"] == rec["hardware_identity"]
    # a different host name changes the informational block and the full hash but never the deterministic hash
    other = tmp_path / "other"
    monkeypatch.setattr(SL.socket, "gethostname", lambda: "some-other-host")
    ctx2 = make_ctx()
    SL.work(other, ctx2)
    m2 = SL.merge(other, ctx2)
    assert m2["deterministic_sha256"] == manifest["deterministic_sha256"] and m2["sha256"] != manifest["sha256"]
    assert {k: v for k, v in tree(other / "merged").items() if k != "MERGE_MANIFEST.json"} == \
        {k: v for k, v in tree(out / "merged").items() if k != "MERGE_MANIFEST.json"}


def test_provenance_failure_never_blocks_a_worker_or_changes_output(tmp_path, monkeypatch):
    _, ref = reference_merge(tmp_path)
    monkeypatch.setattr(SL, "collect_provenance", lambda sha: (_ for _ in ()).throw(RuntimeError("no /proc")))
    ctx = make_ctx()
    out = tmp_path / "run"
    SL.work(out, ctx)
    started = json.loads((out / "events/worker_0000.jsonl").read_text().splitlines()[0])
    assert "RuntimeError" in started["provenance_error"]
    monkeypatch.undo()
    SL.merge(out, ctx)
    assert {k: v for k, v in tree(out / "merged").items() if k != "MERGE_MANIFEST.json"} == \
        {k: v for k, v in ref.items() if k != "MERGE_MANIFEST.json"}
