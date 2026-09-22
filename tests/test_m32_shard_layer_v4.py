"""Guard the RNG-free M3.2 v4 shard / resume / merge layer (bundle v4, pre-RNG correction): exact hash binding and
tamper refusal, v3 immutability, the P1/P3 perturbed_null dispatch fix (injection still applied, no power/FDR
gate), full-cell-plan byte-identity against the sequential bundle-v4 runner, import isolation from bundle v3, the
zero-RNG guard, and the deterministic v4 qualification receipt.

No test here constructs a random generator; worlds come from arithmetic tokens (non-inferential fixtures).  No
pilot is created or run, no authorization is minted, and a v3-shaped authorization is proven refused by the v4
production context (differing ``bundle_sha256``)."""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
V4_DIR = ROOT / "scripts/m32_scheme_d_sharding_v4_20260923"
QUAL_DIR = ROOT / "scripts/m32_scheme_d_qualification_v4_20260923"
V3_DIR = ROOT / "scripts/m32_scheme_d_sharding_20260921"
V3_BUNDLE_DIR = ROOT / "scripts/m32_scheme_d_validation_bundle_v3_20260921"
V4_BUNDLE_DIR = ROOT / "scripts/m32_scheme_d_validation_bundle_v4_20260922"
FREEZE_TAG = "m32-scheme-d-v4-freeze"
FREEZE_COMMIT = "079e8b5c590429aeb0ca5c7b0b261328291c6b2e"
ENV = {**__import__("os").environ, "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}

sys.path.insert(0, str(V4_DIR))
import rng_guard  # noqa: E402

_counter = itertools.count()


def fresh_v4_layer():
    """A brand-new module instance (its own globals, its own ``_RUNNER`` cache) -- for tests that mutate module
    state (e.g. ``BUNDLE``) without side-effecting the shared ``SL`` used elsewhere in this file."""
    spec = importlib.util.spec_from_file_location(f"m32_v4_shard_layer_test_{next(_counter)}", V4_DIR / "shard_layer.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SL = fresh_v4_layer()
CLI = [sys.executable, str(V4_DIR / "shard_layer.py")]
SYN_CELLS = (("N1/C0", 7), ("P1/C0", 5), ("P7/C1", 4))            # P1/C0 is perturbed_null under bundle v4


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
    F = SL.fixture_module()
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


# ================================================================================================ 1. exact hash binding / tamper
def test_v4_exact_hash_bindings_match_the_freeze_tag():
    assert SL.sha256_file(V4_BUNDLE_DIR / "BUNDLE_MANIFEST.json") == SL.EXPECTED_BUNDLE_MANIFEST_SHA256 \
        == "a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023"
    assert SL.sha256_file(V4_BUNDLE_DIR / "runner/scheme_d_runner.py") == SL.EXPECTED_RUNNER_SHA256 \
        == "7e4d693bdaffc7cf35370fe3425858ea195fc70eb0c6425c4461836008410548"
    assert SL.sha256_file(V4_BUNDLE_DIR / "runner/acceptance.py") == SL.EXPECTED_ACCEPTANCE_SHA256 \
        == "a575c07fcef86948c50467dc1f32a7bdba1ea32d78a3897b0e2eb1d4569d7ec1"
    manifest = json.loads((V4_BUNDLE_DIR / "BUNDLE_MANIFEST.json").read_text())
    assert manifest["frozen_truth_package"]["package_sha256"] == SL.EXPECTED_TRUTH_PACKAGE_SHA256 \
        == "a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb"
    assert subprocess.run(["git", "log", "-1", "--format=%H", FREEZE_TAG], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip() == FREEZE_COMMIT
    R = SL.runner()                                                    # positive path: real import succeeds
    assert R.BUNDLE == V4_BUNDLE_DIR


def test_v4_tampered_runner_file_is_refused_before_any_import(tmp_path):
    shutil = __import__("shutil")
    tampered_root = tmp_path / "tampered_repo"
    shutil.copytree(V4_BUNDLE_DIR, tampered_root / V4_BUNDLE_DIR.relative_to(ROOT))
    runner_copy = tampered_root / V4_BUNDLE_DIR.relative_to(ROOT) / "runner" / "scheme_d_runner.py"
    runner_copy.write_bytes(runner_copy.read_bytes() + b"\n# tampered\n")
    m = fresh_v4_layer()
    m.BUNDLE = tampered_root / V4_BUNDLE_DIR.relative_to(ROOT)
    with pytest.raises(m.ShardError, match=r"v4_hash_binding_mismatch:scheme_d_runner\.py"):
        m.runner()
    assert "module" not in m._RUNNER                                   # refused before caching anything


def test_v4_tampered_bundle_manifest_is_refused():
    shutil = __import__("shutil")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tampered_root = Path(td) / V4_BUNDLE_DIR.relative_to(ROOT)
        shutil.copytree(V4_BUNDLE_DIR, tampered_root)
        (tampered_root / "BUNDLE_MANIFEST.json").write_bytes(
            (tampered_root / "BUNDLE_MANIFEST.json").read_bytes() + b" ")
        m = fresh_v4_layer()
        m.BUNDLE = tampered_root
        with pytest.raises(m.ShardError, match=r"v4_hash_binding_mismatch:BUNDLE_MANIFEST\.json"):
            m.runner()


# ================================================================================================ 2. v3 immutability
def test_v3_tooling_byte_unchanged_against_freeze_commit():
    sys.path.insert(0, str(QUAL_DIR))
    import importlib
    spec = importlib.util.spec_from_file_location("m32_v4_qualify_check", QUAL_DIR / "qualify_v4.py")
    qual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qual)
    result = qual.check_v3_untouched()
    assert result["status"] == "PASS", result["detail"]
    assert result["detail"]["files_checked"] >= 10


def test_v3_layer_file_itself_is_byte_identical_to_the_freeze_commit():
    frozen = subprocess.run(["git", "show", f"{FREEZE_COMMIT}:scripts/m32_scheme_d_sharding_20260921/shard_layer.py"],
                            cwd=ROOT, capture_output=True, check=True, timeout=30).stdout
    assert hashlib.sha256(frozen).hexdigest() == SL.sha256_file(V3_DIR / "shard_layer.py")


# ================================================================================================ 3. P1/P3 dispatch fix
def test_p1_p3_receive_injection_via_apply_injection_spy_despite_perturbed_null_kind():
    """Spy on the frozen ``apply_injection`` (never faked) to prove the v4 evaluator calls it for every scenario
    cell -- power AND perturbed_null alike -- matching ``has_scenario`` dispatch, not ``kind == "power"``."""
    R = SL.runner()
    F = SL.fixture_module()
    calls = []
    original = R.apply_injection

    def spy(world, scenario, injection_rng):
        calls.append(scenario)
        return original(world, scenario, injection_rng)
    R.apply_injection = spy
    try:
        with F.installed(R):
            registry = F.StubRegistry(R.MASTER_SEED)
            evaluator = SL.make_evaluator(registry, F.FIXTURE_DRAWS)
            cp = {c["name"]: c for c in R.cell_plan()}
            assert cp["P1/C0"]["kind"] == "perturbed_null" and cp["P3/C2"]["kind"] == "perturbed_null"
            for name in ("N1/C0", "P1/C0", "P3/C2"):
                cell = {**cp[name], "worlds": 2}
                for w in range(2):
                    evaluator(cell, w)
    finally:
        R.apply_injection = original
    assert calls == [101, 101, 103, 103]                                # exactly once per scenario world, never for N1/C0


def test_perturbed_null_cell_has_no_power_or_fdr_gate(tmp_path):
    R = SL.runner()
    F = SL.fixture_module()
    with F.installed(R):
        ctx = SL.fixture_context(cells=(("N1/C0", 2), ("P1/C0", 3), ("P2/C0", 2)), shard_worlds=2)
        out = tmp_path / "o"
        SL.work(out, ctx)
        assert SL.audit(out, ctx)["ok"]
        SL.merge(out, ctx)
    result = json.loads((out / "merged" / "run_result.json").read_text())
    p1_gates = set(result["cells"]["P1/C0"]["gates"])
    p2_gates = set(result["cells"]["P2/C0"]["gates"])
    assert p1_gates == {"refusal_blocker_absent", "type_one"}
    assert "power" not in p1_gates and "fdr" not in p1_gates
    assert {"power", "fdr"} <= p2_gates                                 # P2 (real power scenario) keeps both


# ================================================================================================ 4. retained cells / gates
def test_v4_cell_plan_has_55_cells_with_the_correct_kind_breakdown():
    R = SL.runner()
    plan = R.cell_plan()
    assert len(plan) == 55
    kinds = {}
    for c in plan:
        kinds.setdefault(c["kind"], []).append(c["name"])
    assert len(kinds["null"]) == 20
    assert sorted(n.split("/")[0] for n in kinds["perturbed_null"]) == sorted(f"P1" for _ in range(5)) + sorted(
        f"P3" for _ in range(5))
    assert len(kinds["perturbed_null"]) == 10 and len(kinds["power"]) == 25
    assert {c["scenario"] for c in plan if c["kind"] == "perturbed_null"} == {101, 103}
    assert {c["scenario"] for c in plan if c["kind"] == "power"} == {102, 104, 105, 106, 107}


def test_v4_acceptance_gate_structure_for_null_and_power_cells_matches_v3():
    """v4's pre-RNG correction only changes P1/P3 evaluation; null and power gate *structure* is unchanged."""
    spec = importlib.util.spec_from_file_location("m32_v3_acceptance_compare", V3_BUNDLE_DIR / "runner/acceptance.py")
    v3_acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v3_acc)
    R = SL.runner()
    null_worlds = [{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
                    "attribution_errors": 0, "marginal_p": None} for _ in range(5)]
    power_worlds = [{"refused": False, "refused_hypotheses": 0, "R": 1, "V": 0, "true_discoveries": 1,
                     "attribution_errors": 0, "marginal_p": None} for _ in range(5)]
    v4_null = R.acceptance.evaluate_cell("null", None, null_worlds)
    v3_null = v3_acc.evaluate_cell("null", None, null_worlds)
    assert set(v4_null["gates"]) == set(v3_null["gates"])
    v4_power = R.acceptance.evaluate_cell("power", 102, power_worlds, lambda lo, hi, size: np.zeros(size, dtype=np.int64))
    v3_power = v3_acc.evaluate_cell("power", 102, power_worlds, lambda lo, hi, size: np.zeros(size, dtype=np.int64))
    assert set(v4_power["gates"]) == set(v3_power["gates"])


# ================================================================================================ 5. real refusal handling
def test_refusal_handling_with_real_v4_world_receipt_on_deterministic_refusal_fixture(tmp_path):
    R = SL.runner()
    F = SL.fixture_module()
    with F.installed(R, refuse_every=3):                                # every 3rd world forced link-crossing refused
        registry = F.StubRegistry(R.MASTER_SEED)
        evaluator = SL.make_evaluator(registry, F.FIXTURE_DRAWS)
        cp = {c["name"]: c for c in R.cell_plan()}
        cell = {**cp["P1/C0"], "worlds": 6}
        receipts = [evaluator(cell, w) for w in range(6)]
    refused = [r for r in receipts if "world_refused" in r]
    non_refused = [r for r in receipts if "world_refused" not in r]
    assert len(refused) == 2 and len(non_refused) == 4                  # worlds 2 and 5 (index % 3 == 2)
    for r in refused:
        line = SL.encode_receipt(r)
        assert SL.check_receipt_line(line, cell, r["world"], SL.Context(
            mode="fixture", plan=[cell], shard_worlds=6, draws=F.FIXTURE_DRAWS,
            bindings_common={"bundle_sha256": R.bundle_sha256(), "authorization_sha256": None,
                             "runner_sha256": SL.sha256_file(R.__file__),
                             "environment_lock_sha256": SL.sha256_file(SL.BUNDLE / "artifacts/environment_lock.json"),
                             "environment": {}, "non_inferential": True},
            evaluator=lambda c, w: None, bootstrap=lambda c: None))
    for r in non_refused:
        assert r["draws"] == F.FIXTURE_DRAWS and r["statistic_path"] == "fast"
    summaries = [R.world_summary(r) for r in receipts]
    assert sum(1 for s in summaries if s["refused"]) == 2


# ================================================================================================ 6. sequential vs shard/merge, all 55 cells
@pytest.fixture(scope="module")
def sequential_v4(tmp_path_factory):
    """The real bundle-v4 ``run_validation`` on every one of the 55 real cells (small deterministic world counts),
    arithmetic tokens, real world generation / injection / receipts."""
    R = SL.runner()
    F = SL.fixture_module()
    out = tmp_path_factory.mktemp("seqv4") / "o"
    boosted = {"N1/C0", "P1/C0", "P2/C0"}
    cells = [(c["name"], 4 if c["name"] in boosted else 1) for c in R.cell_plan()]
    plan = F.fixture_plan(R, cells)
    record = {"runtime_estimate_cpu_hours": 1.0, "fast_benchmark_receipt_sha256": "0" * 64}
    old_plan, old_b = R.cell_plan, R.B
    try:
        with F.installed(R):
            R.cell_plan, R.B = (lambda: plan), F.FIXTURE_DRAWS
            R.run_validation(None, F.StubRegistry(R.MASTER_SEED), out, record)
    finally:
        R.cell_plan, R.B = old_plan, old_b
    return tree(out), cells


def compare(merged: Path, sequential_tree: dict):
    got = tree(merged)
    assert got.pop("MERGE_MANIFEST.json")
    assert set(got) == set(sequential_tree)
    for name, data in sequential_tree.items():
        assert got[name] == data, name


def test_v4_sequential_run_validation_covers_all_55_cells(sequential_v4):
    seq_tree, cells = sequential_v4
    assert len(cells) == 55
    receipt_files = [k for k in seq_tree if k.endswith(".jsonl")]
    assert len(receipt_files) == 55
    result = json.loads(seq_tree["run_result.json"])
    assert result["overall"]["cells_evaluated"] == 55
    assert set(result["cells"]["P1/C0"]) >= {"fdp_count", "type_one_rate"} and "power" not in result["cells"]["P1/C0"]["gates"]
    assert "power" in result["cells"]["P2/C0"]["gates"]


def test_v4_sharded_merge_reproduces_sequential_runner_exactly_on_all_55_cells(tmp_path, sequential_v4):
    seq_tree, cells = sequential_v4
    R = SL.runner()
    F = SL.fixture_module()
    for shard_worlds, workers, assign in ((2, 1, "static"), (3, 4, "static")):
        out = tmp_path / f"sw{shard_worlds}"
        with F.installed(R):
            ctx = SL.fixture_context(cells=cells, shard_worlds=shard_worlds)
            for worker in reversed(range(workers)):
                SL.work(out, ctx, worker, workers, assign)
            assert SL.audit(out, ctx)["ok"]
            SL.merge(out, ctx)
        compare(out / "merged", seq_tree)


def test_v4_evaluator_uses_the_same_calls_and_seed_positions_as_run_cell():
    """The production registry cannot be exercised without RNG, so pin the wiring textually against ``run_cell``."""
    import inspect
    squash = lambda text: " ".join(text.split())                        # noqa: E731
    runner_src = squash(inspect.getsource(SL.runner().run_cell))
    layer_src = squash(inspect.getsource(SL.make_evaluator)) + " " + squash(inspect.getsource(SL.expected_tuples))
    same = ('has_scenario = cell["scenario"] is not None',
            'registry.generator(phase, c, w_dgp, w, 0), registry.generator(phase, c, w_dgp, w, 1))',
            'registry.generator(phase, c, w_dgp, w, 3))',
            'registry.generator(phase, c, w_dgp, w, 2), phase, cell["name"], tuples,',
            'draws=draws, truth=truth, injected=injected)')
    for fragment in same:
        assert fragment in runner_src and fragment in layer_src, fragment
    assert 'world = apply_injection(world, cell["scenario"],' in runner_src
    assert 'world = R.apply_injection(world, cell["scenario"],' in layer_src
    assert '1 if has_scenario else cell["dgp"], c, w,' in runner_src and '1 if has_scenario else cell["dgp"], c, w,' in layer_src


# ================================================================================================ 7. zero RNG guard
def test_v4_layer_and_fixture_construct_no_random_generator_via_monkeypatch(monkeypatch, tmp_path):
    import random
    trips = []

    def trip(*a, **k):
        trips.append(1)
        raise AssertionError("random generator construction attempted")
    for name in ("PCG64", "SeedSequence", "default_rng", "Generator", "RandomState"):
        monkeypatch.setattr(np.random, name, trip)
    monkeypatch.setattr(random, "Random", trip)
    R = SL.runner()
    F = SL.fixture_module()
    with F.installed(R):
        ctx = SL.fixture_context(cells=(("N1/C0", 2), ("P1/C0", 1)), shard_worlds=2)
        SL.work(tmp_path / "o", ctx)
        SL.merge(tmp_path / "o", ctx)
    assert not trips
    source = SL.LAYER_PATH.read_text() + (V4_DIR / "shard_fixture.py").read_text()
    for token in ("np.random", "numpy.random", "default_rng", "PCG64", "SeedSequence", "RandomState", "import random"):
        assert token not in source, token


def test_zero_guard_attempts_and_no_validation_worlds_across_full_fixture_battery(tmp_path):
    """The ``RNGGuard`` from ``scripts/m32_scheme_d_sharding_v4_20260923/rng_guard.py`` wraps a fresh import plus a
    full work/audit/merge cycle; every counter must stay at zero."""
    guard = rng_guard.RNGGuard()
    with guard:
        m = fresh_v4_layer()
        R = m.runner()
        F = m.fixture_module()
        with F.installed(R):
            ctx = m.fixture_context(cells=(("N1/C0", 2), ("P1/C0", 2), ("P3/C4", 1), ("P2/C0", 1)), shard_worlds=2)
            m.work(tmp_path / "o", ctx)
            report = m.audit(tmp_path / "o", ctx)
            assert report["ok"]
            m.merge(tmp_path / "o", ctx)
    assert guard.counters() == {"constructor_attempts": 0, "random_variates_requested": 0, "validation_worlds_generated": 0}


# ================================================================================================ 8. v4 qualification binding
def test_v4_qualification_cli_writes_a_zero_rng_pass_receipt_with_no_authorization(tmp_path):
    out = tmp_path / "receipt.json"
    proc = subprocess.run([sys.executable, str(QUAL_DIR / "qualify_v4.py"), "run", "--out", str(out)],
                          capture_output=True, text=True, env=ENV, timeout=120)
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(out.read_text())
    assert receipt["verdict"] == "PASS"
    assert receipt["rng_guard_counters"] == {"constructor_attempts": 0, "random_variates_requested": 0,
                                              "validation_worlds_generated": 0}
    assert receipt["authorization_minted"] is False
    assert receipt["production_gate"]["authorized"] is False
    assert receipt["bindings"]["scripts/m32_scheme_d_validation_bundle_v4_20260922/BUNDLE_MANIFEST.json"] == \
        "a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023"
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    assert receipt["receipt_sha256"] == hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for check in receipt["checks"].values():
        assert check["status"] == "PASS", check


# ================================================================================================ 9. import collision regression
_COLLISION_SCRIPT = r"""
import importlib.util, json, sys

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

ORDER = sys.argv[1]
v4_path = sys.argv[2]
v3_path = sys.argv[3]
if ORDER == "v4_first":
    SLv4 = load("m32_v4_shard_layer_order", v4_path)
    SLv3 = load("m32_v3_shard_layer_order", v3_path)
else:
    SLv3 = load("m32_v3_shard_layer_order", v3_path)
    SLv4 = load("m32_v4_shard_layer_order", v4_path)
Rv4, Rv3 = SLv4.runner(), SLv3.runner()
out = {
    "v4_bundle": str(Rv4.BUNDLE), "v3_bundle": str(Rv3.BUNDLE),
    "v4_fdr": list(Rv4.acceptance.FDR_SCENARIOS), "v3_fdr": list(Rv3.acceptance.FDR_SCENARIOS),
    "v4_p1_kind": [c["kind"] for c in Rv4.cell_plan() if c["name"] == "P1/C0"][0],
    "v3_p1_kind": [c["kind"] for c in Rv3.cell_plan() if c["name"] == "P1/C0"][0],
    "distinct_runner_modules": Rv4 is not Rv3, "distinct_acceptance_modules": Rv4.acceptance is not Rv3.acceptance,
    "generic_scheme_d_runner_is_v3": sys.modules.get("scheme_d_runner") is Rv3,
    "generic_acceptance_is_v3_acceptance": sys.modules.get("acceptance") is Rv3.acceptance,
}
print(json.dumps(out))
"""


@pytest.mark.parametrize("order", ("v4_first", "v3_first"))
def test_import_collision_regression_both_orders(order):
    """v3's ``scheme_d_runner.py`` does a bare ``import scheme_d_runner`` / ``import acceptance`` after its own
    ``sys.path.insert``, permanently occupying the generic ``sys.modules`` slots; v4's isolated ``runner()`` must
    still resolve to the correct bundle regardless of which layer imports first, and must leave the generic slots
    pointing at v3's modules afterwards (matching v3's own, unmodified, behaviour) in either order."""
    proc = subprocess.run([sys.executable, "-c", _COLLISION_SCRIPT, order,
                           str(V4_DIR / "shard_layer.py"), str(V3_DIR / "shard_layer.py")],
                          capture_output=True, text=True, env=ENV, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["v4_bundle"] == str(V4_BUNDLE_DIR) and out["v3_bundle"] == str(V3_BUNDLE_DIR)
    assert out["v4_fdr"] == [102, 104, 105, 106] and out["v3_fdr"] == [101, 102, 103, 104, 105, 106]
    assert out["v4_p1_kind"] == "perturbed_null" and out["v3_p1_kind"] == "power"
    assert out["distinct_runner_modules"] and out["distinct_acceptance_modules"]
    assert out["generic_scheme_d_runner_is_v3"] and out["generic_acceptance_is_v3_acceptance"]


# ================================================================================================ 10. shard resume / integrity
def test_v4_crash_mid_shard_resumes_from_the_last_durable_world_and_matches_uninterrupted(tmp_path):
    ref_out, _ = completed_run(tmp_path / "ref")
    out, ctx = tmp_path / "run", make_ctx()
    inner, count = ctx.evaluator, [0]

    def flaky(cell, w):
        if count[0] == 4:
            raise Crash()
        count[0] += 1
        return inner(cell, w)
    ctx.evaluator = flaky
    with pytest.raises(Crash):
        SL.work(out, ctx)
    partial = SL.shard_paths(out, 1)["partial"]
    assert partial.exists() and not SL.shard_paths(out, 1)["complete"].exists()
    ctx.evaluator = inner
    stats = SL.work(out, ctx)
    assert stats["shards"].get("already_complete") == 1
    assert tree(out / "shards") == tree(ref_out / "shards")
    assert SL.audit(out, ctx)["ok"]


def test_v4_torn_partial_tail_is_dropped_but_corrupt_complete_lines_fail_closed(tmp_path):
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
    with open(partial, "ab") as fh:
        fh.write(b'{"schema":"m3.2-scheme-d-wo')                        # torn write (no newline)
    ctx.evaluator = inner
    SL.work(out, ctx)
    assert tree(out / "shards") == tree(ref_out / "shards")
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


def test_v4_tampered_receipts_are_detected_and_block_the_merge(tmp_path):
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


# ================================================================================================ 11. never accept a v3 authorization
def test_v4_production_context_refuses_a_v3_shaped_authorization(tmp_path, monkeypatch):
    R = SL.runner()
    v3_bundle_sha256 = SL.sha256_file(V3_BUNDLE_DIR / "BUNDLE_MANIFEST.json")
    assert v3_bundle_sha256 != R.bundle_sha256()                        # the two bundles must differ for this to mean anything
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "validation",
                                "bundle_sha256": v3_bundle_sha256, "authorized_by": "owner",
                                "runtime_estimate_cpu_hours": 1.0, "fast_benchmark_receipt_sha256": "a" * 64,
                                "shard_layer_sha256": SL.sha256_file(SL.LAYER_PATH), "shard_worlds": 25}))
    monkeypatch.setattr(R, "assert_environment", lambda: {"python": "3.12.3", "numpy": "2.5.2", "openblas_threads": "1"})
    monkeypatch.setattr(R, "assert_isolation", lambda: None)
    with pytest.raises(R.IntegrityError, match="authorization_mismatch"):
        SL.production_context(auth, 25)


def test_v4_production_cli_fails_closed_without_owner_authorization(tmp_path):
    out = tmp_path / "out"
    base = [*CLI, "work", "--out", str(out), "--shard-worlds", "25"]
    proc = subprocess.run(base, capture_output=True, text=True, env=ENV, timeout=120)
    assert proc.returncode == 2 and "authorization_required_for_production" in proc.stderr
    bogus = tmp_path / "auth.json"
    bogus.write_text(json.dumps({"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "validation",
                                 "bundle_sha256": "0" * 64, "authorized_by": "owner"}))
    proc = subprocess.run([*base, "--authorization", str(bogus)], capture_output=True, text=True, env=ENV, timeout=120)
    assert proc.returncode == 2 and "authorization_mismatch" in proc.stderr
    assert not out.exists()
