"""Guard the RNG-free M3.2 validation bundle REVISION 4: the 2026-09-20 pre-RNG correction.

P1 (scenario 101) and P3 (scenario 103) are corrected from power scenarios to true-null
perturbed-calibration scenarios (protocol correction section 2-3): POWER_RULES/FDR_SCENARIOS
exclude them, and their cells get a Type-I false-positive gate instead of power/bootstrap-FDR/
marginal-calibration gates. P2's original single-target power criterion (Wilson lower >= 0.80)
is preserved under its own "P2" key.

Runtime behaviour is probed through subprocesses (mirroring verify_bundle.py's own
GATE_PROBE/CELL_PLAN_PROBE pattern) so this file never imports the bundle's plain
``scheme_d_runner``/``acceptance`` modules into the pytest process directly -- avoiding a
``sys.modules`` collision with tests/test_m32_validation_bundle_v3.py, which imports modules
of the same name from the frozen v3 bundle directory.

The full preflight suite takes well over a minute (it exercises FastMetric's live bit-identity
check).  It is run exactly ONCE per test session through the module-scoped ``fresh_preflight_report``
fixture into a temporary directory. Tests never rewrite the canonical bundle artifacts.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "scripts/m32_scheme_d_validation_bundle_v4_20260922"
RUNNER = BUNDLE / "runner"
V1_DIR = ROOT / "scripts/m32_scheme_d_validation_bundle_20260921"
V2_DIR = ROOT / "scripts/m32_scheme_d_validation_bundle_v2_20260921"
V3_DIR = ROOT / "scripts/m32_scheme_d_validation_bundle_v3_20260921"
V3_COMMIT = "90ca3b842af7184ca4e6dcf8bf495c81d89662a5"
V1_SHA = "0b42c6a38c3329eff6ec760176b682003ff993b0976d45588f72eebb4be78a3e"
V2_SHA = "397961d944f98f730eb723d537594d56428cf72db349449ad848678ad85f0c4e"
V3_SHA = "eab05d5d5acf1e4d435010b30005c9fda164da1b8c9fc0377502579c7d21dc01"
TRUTH_SHA = "a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb"
ENV = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
RNG_GUARD = '''
import random
import tempfile
import numpy as np
def _deny_rng(*args, **kwargs):
    raise AssertionError("RNG forbidden in v4 deterministic tests")
for _name in ("Generator", "PCG64", "PCG64DXSM", "SeedSequence", "default_rng", "RandomState", "MT19937", "Philox", "SFC64"):
    setattr(np.random, _name, _deny_rng)
random.Random = random.SystemRandom = _deny_rng
'''


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run(code: str, timeout: int = 60):
    proc = subprocess.run([sys.executable, "-c", RNG_GUARD + code], capture_output=True, text=True, env=ENV, timeout=timeout)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _run_polled(argv, out_path: Path, deadline: float = 600.0, poll: float = 3.0):
    """Run a subprocess without any single blocking call longer than ``poll`` seconds."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV, cwd=str(ROOT))
    waited = 0.0
    while proc.poll() is None:
        if waited >= deadline:
            proc.kill()
            proc.communicate()
            raise TimeoutError(f"{argv[1] if len(argv) > 1 else argv[0]} did not complete within {deadline}s")
        time.sleep(poll)
        waited += poll
    _, stderr = proc.communicate()
    assert proc.returncode == 0, stderr[-2000:]
    return json.loads(out_path.read_text())


# --------------------------------------------------------------------------- shared preflight fixture (one replay)
@pytest.fixture(scope="module")
def fresh_preflight_report():
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "preflight.json"
        return _run_polled([sys.executable, str(RUNNER / "preflight.py"), "--out", str(out_path)],
                           out_path, deadline=600.0, poll=3.0)


@pytest.fixture
def scoped_v4_copy(tmp_path, fresh_preflight_report):
    """Copy only the v4 bundle dir (mutable); symlink the immutable sibling deps its ROOT-relative
    path lookups need, instead of copying the entire scripts/ tree.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "trader").symlink_to(ROOT / "trader")
    (root / "docs").symlink_to(ROOT / "docs")
    (root / "tests").symlink_to(ROOT / "tests")
    shutil.copytree(BUNDLE, root / "scripts" / BUNDLE.name)
    for sib in ("m32_scheme_d_pre_rng_truth_20260921", "m32_fast_metric_20260921"):
        (root / "scripts" / sib).symlink_to(ROOT / "scripts" / sib)
    return root / "scripts" / BUNDLE.name


# --------------------------------------------------------------------------- frozen v1/v2/v3 unchanged (full, not just top hash)
def test_v1_v2_v3_all_manifest_files_and_truth_package_unchanged():
    for directory, expected in ((V1_DIR, V1_SHA), (V2_DIR, V2_SHA), (V3_DIR, V3_SHA)):
        manifest_path = directory / "BUNDLE_MANIFEST.json"
        assert _sha(manifest_path) == expected
        manifest = json.loads(manifest_path.read_text())
        for rel, digest in {**manifest["files_sha256"], **manifest["pinned_inputs_sha256"]}.items():
            assert _sha(ROOT / rel) == digest, rel
    truth_manifest = ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260921/package/manifest.sha256"
    assert _sha(truth_manifest) == TRUTH_SHA
    for line in truth_manifest.read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert _sha(truth_manifest.parent / name) == digest, name


def test_v3_bundle_dir_byte_identical_to_its_freeze_commit_including_unmanifested_files():
    """git diff on the whole v3 directory catches drift in files the v3 manifest never listed
    (e.g. artifacts/verification_result.json, written by v3's own verify_bundle.py run).
    """
    unmanifested = V3_DIR / "artifacts/verification_result.json"
    assert unmanifested.is_file()
    proc = subprocess.run(["git", "diff", "--quiet", V3_COMMIT, "--", str(V3_DIR.relative_to(ROOT))],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, f"v3 bundle directory differs from freeze commit {V3_COMMIT}: {proc.stdout}{proc.stderr}"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", str(unmanifested.relative_to(ROOT))],
                             cwd=ROOT, capture_output=True, text=True)
    assert tracked.returncode == 0, "v3 verification_result.json must be git-tracked"


def test_v4_manifest_supersedes_v3_exactly():
    manifest = json.loads((BUNDLE / "BUNDLE_MANIFEST.json").read_text())
    assert manifest["schema"] == "m3.2-scheme-d-validation-bundle-manifest.v4" and manifest["bundle_revision"] == 4
    sup = manifest["supersedes"]
    assert sup["bundle_sha256"] == V3_SHA == sup["v3_manifest_file_sha256"]
    assert sup["bundle_manifest"] == "scripts/m32_scheme_d_validation_bundle_v3_20260921/BUNDLE_MANIFEST.json"
    assert manifest["no_rng_no_seed_generation_no_worlds_no_search_no_gate2"] is False
    assert manifest["preparation_audit"]["owner_review_required"] is True
    for rel, digest in {**manifest["files_sha256"], **manifest["pinned_inputs_sha256"]}.items():
        assert _sha(ROOT / rel) == digest, rel


# --------------------------------------------------------------------------- POWER_RULES / FDR_SCENARIOS exclude P1/P3; P2 preserved
def test_power_rules_and_fdr_scenarios_exclude_101_103_and_preserve_p2():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
print(json.dumps({{"power_rules": sorted(A.POWER_RULES), "fdr_scenarios": sorted(A.FDR_SCENARIOS),
                   "perturbed_null": sorted(A.PERTURBED_NULL_SCENARIOS),
                   "power_pass_101": A.power_pass(101, [1] * 1000), "power_pass_103": A.power_pass(103, [1] * 1000),
                   "power_pass_102": A.power_pass(102, [1] * 1000), "rules_102": list(A.POWER_RULES[102])}}))
""")
    assert out["power_rules"] == [102, 104, 105, 106]
    assert out["fdr_scenarios"] == [102, 104, 105, 106]
    assert out["perturbed_null"] == [101, 103]
    assert out["power_pass_101"] is False and out["power_pass_103"] is False   # excluded: always False
    assert out["power_pass_102"] is True                                       # P2's ordinary power gate intact
    assert out["rules_102"] == [[1, 0.80]]                                     # unchanged original single-target rule
    config = json.loads((BUNDLE / "artifacts/config.json").read_text())["acceptance"]
    assert "P1_P3" not in config["power"]
    assert config["power"]["P2"] == {"wilson_lower_min": 0.80}


# --------------------------------------------------------------------------- P1/P3 role, cell_plan kind, unchanged truth
def test_p1_p3_role_calibration_and_cell_plan_kind_perturbed_null():
    out = _run(f"""
import sys, json
from collections import Counter
sys.path.insert(0, {str(RUNNER)!r})
import scheme_d_runner as R
plan = R.cell_plan()
kinds = dict(Counter(p["kind"] for p in plan))
p1 = next(p for p in plan if p["scenario"] == 101 and p["correlation"] == 0)
p3 = next(p for p in plan if p["scenario"] == 103 and p["correlation"] == 4)
p2 = next(p for p in plan if p["scenario"] == 102 and p["correlation"] == 0)
print(json.dumps({{"kinds": kinds, "cells": len(plan), "worlds": sum(p["worlds"] for p in plan),
                   "p1": p1, "p3": p3, "p2_kind": p2["kind"]}}))
""")
    assert out["kinds"] == {"null": 20, "power": 25, "perturbed_null": 10}
    assert out["cells"] == 55 and out["worlds"] == 75_000
    assert out["p1"] == {"kind": "perturbed_null", "phase": 20, "dgp": 1, "scenario": 101, "correlation": 0,
                         "worlds": 1000, "name": "P1/C0"}
    assert out["p3"] == {"kind": "perturbed_null", "phase": 20, "dgp": 1, "scenario": 103, "correlation": 4,
                         "worlds": 1000, "name": "P3/C4"}
    assert out["p2_kind"] == "power"

    effect_table = json.loads((BUNDLE / "artifacts/effect_assignment_table.json").read_text())
    roles = {s["scenario_code"]: s["role"] for s in effect_table["scenarios"]}
    assert roles[101] == roles[103] == "calibration"
    assert roles[102] == roles[104] == roles[105] == roles[106] == "acceptance"
    assert roles[107] == "sensitivity_only"


def test_p1_p3_truth_tables_all_true_null_frozen_and_unchanged():
    pkg = ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260921/package/tables"
    for scenario in (101, 103):
        for corr in range(5):
            recs = json.loads((pkg / f"P{scenario - 100}_C{corr}.json").read_text())["records"]
            assert len(recs) == 384
            assert all(r["classification"] == "true_null" and r.get("sign") is None for r in recs)
    for scenario in (102, 104, 105, 106):
        recs = json.loads((pkg / f"P{scenario - 100}_C0.json").read_text())["records"]
        assert any(r["classification"] == "analytic_non_null" for r in recs)


# --------------------------------------------------------------------------- run_cell dispatch spy (all 10 P1/P3 cells)
def test_run_cell_dispatch_spy_all_ten_p1_p3_cells():
    """Prove run_cell's ACTUAL dispatch for every P1/P3 x C0-C4 cell: N1 (dgp=1) generation, injection keyed by
    scenario (101/[0], 103/[320]), stream order [0, 1, 3, 2], phase 20, and seed coordinates
    (master, phase, correlation, scenario, world, stream) identical to what an ordinary power cell would use.
    Uses a pure registry stub and constructor guard; no real RNG or authorization object is constructed.
    """
    code = f"""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, {str(RUNNER)!r})
import numpy as np
import scheme_d_runner as R

calls = {{"generate_world": [], "apply_injection": [], "world_receipt": [], "generator": []}}

class RegistryStub:
    def generator(self, phase, correlation, dgp, world, stream):
        calls["generator"].append([phase, correlation, dgp, world, stream])
        return object()

def fake_generate_world(dgp, correlation, world_index, data_rng, member_rng):
    calls["generate_world"].append({{"dgp": dgp, "correlation": correlation, "world_index": world_index}})
    strata = tuple((1 + b % 4, int((b // 3) % 2), 0) for b in range(R.BLOCKS))
    mem = np.zeros((R.ROWS, 384), dtype=bool)
    return R.V.SyntheticWorld(correlation, dgp, world_index, mem, np.zeros(R.ROWS, dtype=np.int64),
                              np.zeros(R.ROWS), np.ones(R.ROWS, dtype=bool), strata, R.V.valid_metadata(strata))
R.generate_world = fake_generate_world

def fake_apply_injection(world, scenario, injection_rng):
    calls["apply_injection"].append({{"scenario": scenario, "world_index": world.world_index}})
    return world
R.apply_injection = fake_apply_injection

def fake_world_receipt(world, permutation_rng, phase, cell, tuples, *, draws, truth, injected, statistic="fast", block_maps=None):
    calls["world_receipt"].append({{"phase": phase, "cell": cell, "tuples": tuples, "injected": list(injected)}})
    return {{"schema": "x", "cell": cell, "phase": phase, "world": world.world_index,
            "world_refused": ["stub_short_circuit"]}}
R.world_receipt = fake_world_receipt

registry = RegistryStub()

with tempfile.TemporaryDirectory() as td:
    out_dir = Path(td)
    plan = [p for p in R.cell_plan() if p["kind"] == "perturbed_null"]
    assert len(plan) == 10
    for cell in plan:
        R.run_cell({{**cell, "worlds": 1}}, registry, out_dir, draws=2)

print(json.dumps(calls))
"""
    out = _run(code, timeout=60)
    gen_calls, inj_calls, receipt_calls, seed_calls = (out["generate_world"], out["apply_injection"],
                                                        out["world_receipt"], out["generator"])
    assert len(gen_calls) == 10 and all(c["dgp"] == 1 for c in gen_calls)      # base dgp = N1, unchanged
    p1_inj = [c for c in inj_calls if c["scenario"] == 101]
    p3_inj = [c for c in inj_calls if c["scenario"] == 103]
    assert len(p1_inj) == 5 and len(p3_inj) == 5                               # injection dispatched for every cell

    per_cell = {}
    for phase, corr, dgp, world, stream in seed_calls:
        per_cell.setdefault((corr, dgp), []).append((phase, world, stream))
    assert len(per_cell) == 10 and set(dgp for _, dgp in per_cell) == {101, 103}
    for (corr, dgp), entries in per_cell.items():
        assert [e[2] for e in entries] == [0, 1, 3, 2]                         # frozen stream order: data,membership,injection,permutation
        assert all(e[0] == 20 and e[1] == 0 for e in entries)                  # phase 20, world 0 (single stubbed world)

    by_name = {c["cell"]: c for c in receipt_calls}
    assert set(by_name) == {f"P1/C{c}" for c in range(5)} | {f"P3/C{c}" for c in range(5)}
    for corr in range(5):
        p1 = by_name[f"P1/C{corr}"]
        assert p1["injected"] == [0] and p1["phase"] == 20
        assert p1["tuples"]["data"] == [2026091902, 20, corr, 101, 0, 0]
        assert p1["tuples"]["membership"] == [2026091902, 20, corr, 101, 0, 1]
        assert p1["tuples"]["permutation"] == [2026091902, 20, corr, 101, 0, 2]
        assert p1["tuples"]["injection"] == [2026091902, 20, corr, 101, 0, 3]
        p3 = by_name[f"P3/C{corr}"]
        assert p3["injected"] == [320] and p3["phase"] == 20
        assert p3["tuples"]["data"] == [2026091902, 20, corr, 103, 0, 0]
        assert p3["tuples"]["injection"] == [2026091902, 20, corr, 103, 0, 3]


# --------------------------------------------------------------------------- perturbed_null acceptance gate
def test_perturbed_null_true_null_rejections_are_false_positives():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
calm = [{{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
         "attribution_errors": 0, "marginal_p": None}} for _ in range(1000)]
some = [{{**c, "R": 1, "V": 1}} if i < 30 else c for i, c in enumerate(calm)]
cell = A.evaluate_cell("perturbed_null", 101, some)
print(json.dumps({{"fdp_count": cell["fdp_count"], "mean_fdp": cell["mean_fdp"], "type_one_rate": cell["type_one_rate"],
                   "gates": sorted(cell["gates"])}}))
""")
    assert out["fdp_count"] == 30
    assert abs(out["mean_fdp"] - 0.03) < 1e-12
    assert abs(out["type_one_rate"] - 0.03) < 1e-12
    assert out["gates"] == ["refusal_blocker_absent", "type_one"]     # no power/fdr/marginal_calibration


def test_perturbed_null_zero_rejections_passes_and_excessive_fails():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
calm = [{{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
         "attribution_errors": 0, "marginal_p": None}} for _ in range(1000)]
zero = A.evaluate_cell("perturbed_null", 101, calm)
excessive = [{{**c, "R": 1, "V": 1}} if i < 150 else c for i, c in enumerate(calm)]   # 15% >> 5%/6% thresholds
excess_cell = A.evaluate_cell("perturbed_null", 103, excessive)
borderline_pass = [{{**c, "R": 1, "V": 1}} if i < 30 else c for i, c in enumerate(calm)]   # 3.0%: well inside both gates
border_cell = A.evaluate_cell("perturbed_null", 101, borderline_pass)
print(json.dumps({{"zero_passes": zero["passes"], "excess_fails": excess_cell["passes"],
                   "excess_mean_fdp": excess_cell["mean_fdp"], "border_passes": border_cell["passes"]}}))
""")
    assert out["zero_passes"] is True
    assert out["excess_fails"] is False and out["excess_mean_fdp"] == 0.15
    assert out["border_passes"] is True


def test_perturbed_null_refusal_exclusion_and_blocker():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
refused_world = {{"refused": True, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
                  "attribution_errors": 0, "marginal_p": None}}
calm = [{{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
         "attribution_errors": 0, "marginal_p": None}} for _ in range(1000)]
under_limit = calm[:991] + [refused_world] * 9     # 0.9%: below the 1% world-refusal limit
cell_ok = A.evaluate_cell("perturbed_null", 101, under_limit)
over_limit = calm[:980] + [refused_world] * 20     # 2%: above the 1% limit
cell_blocked = A.evaluate_cell("perturbed_null", 101, over_limit)
print(json.dumps({{"under_limit_blocks": cell_ok["refusal"]["blocks"], "under_limit_passes": cell_ok["passes"],
                   "over_limit_blocks": cell_blocked["refusal"]["blocks"], "over_limit_passes": cell_blocked["passes"],
                   "over_limit_non_refused": cell_blocked["non_refused"]}}))
""")
    assert out["under_limit_blocks"] is False and out["under_limit_passes"] is True
    assert out["over_limit_blocks"] is True and out["over_limit_passes"] is False
    assert out["over_limit_non_refused"] == 980


def test_perturbed_null_never_calls_bootstrap_callback():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
def poison(lo, hi, size):
    raise AssertionError("bootstrap_integers must never be invoked for a perturbed_null cell")
calm = [{{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
         "attribution_errors": 0, "marginal_p": None}} for _ in range(100)]
cell = A.evaluate_cell("perturbed_null", 101, calm, bootstrap_integers=poison)
print(json.dumps({{"ok": True, "gates": sorted(cell["gates"])}}))
""")
    assert out["ok"] is True
    assert "fdr" not in out["gates"] and "marginal_calibration" not in out["gates"]


# --------------------------------------------------------------------------- boundary conditions
def test_boundary_wilson_upper_fails_at_exact_point_threshold():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
print(json.dumps({{"point": 50 / 1000, "wilson_upper": A.wilson(50, 1000)[1], "type_one_pass": A.type_one_pass(50, 1000)}}))
""")
    assert out["point"] == 0.05                  # point estimate exactly at the 0.05 ceiling
    assert out["wilson_upper"] > 0.06             # Wilson-95 upper bound exceeds 0.06
    assert out["type_one_pass"] is False          # combined gate must fail


def test_empty_and_all_refused_cells_fail():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
empty = A.evaluate_cell("perturbed_null", 101, [])
refused = {{"refused": True, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
           "attribution_errors": 0, "marginal_p": None}}
all_refused = A.evaluate_cell("perturbed_null", 101, [refused] * 1000)
print(json.dumps({{"empty_passes": empty["passes"], "empty_non_refused": empty["non_refused"],
                   "all_refused_passes": all_refused["passes"], "all_refused_blocks": all_refused["refusal"]["blocks"]}}))
""")
    assert out["empty_passes"] is False and out["empty_non_refused"] == 0
    assert out["all_refused_passes"] is False and out["all_refused_blocks"] is True


def test_p7_refusal_still_blocks_in_overall():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
refused = {{"refused": True, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
           "attribution_errors": 0, "marginal_p": None}}
quiet = {{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
         "attribution_errors": 0, "marginal_p": None}}
p7_blocked = A.evaluate_cell("power", 107, [refused] * 20 + [quiet] * 80)   # 20% refused >> 1% limit
overall = A.overall({{"P7/C0": p7_blocked}})
print(json.dumps({{"p7_gates": sorted(p7_blocked["gates"]),
                   "p7_refusal_blocker_absent": p7_blocked["gates"]["refusal_blocker_absent"],
                   "overall_passes": overall["passes"], "failing_cells": overall["failing_cells"]}}))
""")
    assert out["p7_gates"] == ["refusal_blocker_absent"]       # descriptive only: no other gate
    assert out["p7_refusal_blocker_absent"] is False            # but the refusal blocker itself still applies
    assert out["overall_passes"] is False and out["failing_cells"] == ["P7/C0"]


# --------------------------------------------------------------------------- ordinary gates (N0-N3, P2/P4/P5/P6) intact
def test_null_cell_gates_unaffected():
    out = _run(f"""
import sys, json
from fractions import Fraction as F
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
grid = [F(2 * w + 1, 4000) for w in range(2000)]
quiet = [{{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
          "attribution_errors": 0, "marginal_p": grid[w]}} for w in range(2000)]
cell = A.evaluate_cell("null", None, quiet)
print(json.dumps({{"passes": cell["passes"], "gates": sorted(cell["gates"])}}))
""")
    assert out["passes"] is True
    assert out["gates"] == ["marginal_calibration", "refusal_blocker_absent", "type_one"]


def test_p2_p4_p5_p6_power_and_fdr_gates_intact():
    """Use the MAX threshold in each scenario's rule list (P4/P5/P6 have >1 threshold), with V=0 so FDP=0
    for every world -- otherwise a world satisfying only the smallest threshold spuriously fails the larger one.
    """
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
import numpy as np

def integers(lo, hi, size):
    return np.tile(np.arange(hi), (size[0], 1))

results = {{}}
for scenario in (102, 104, 105, 106):
    need_max = max(t[0] for t in A.POWER_RULES[scenario])
    worlds = [{{"refused": False, "refused_hypotheses": 0, "R": need_max, "V": 0, "true_discoveries": need_max,
               "attribution_errors": 0, "marginal_p": None}} for _ in range(1000)]
    cell = A.evaluate_cell("power", scenario, worlds, integers)
    results[str(scenario)] = {{"passes": cell["passes"], "gates": sorted(cell["gates"]), "mean_fdp": cell["mean_fdp"]}}
print(json.dumps(results))
""")
    for scenario, result in out.items():
        assert result["passes"] is True, (scenario, result)
        assert result["gates"] == ["fdr", "power", "refusal_blocker_absent"]
        assert result["mean_fdp"] == 0.0


def test_p7_remains_descriptive_only():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import acceptance as A
quiet = [{{"refused": False, "refused_hypotheses": 0, "R": 0, "V": 0, "true_discoveries": 0,
          "attribution_errors": 0, "marginal_p": None}} for _ in range(10)]
cell = A.evaluate_cell("power", 107, quiet)
print(json.dumps({{"passes": cell["passes"], "gates": sorted(cell["gates"])}}))
""")
    assert out["passes"] is True and out["gates"] == ["refusal_blocker_absent"]


# --------------------------------------------------------------------------- seed-tuple accounting
def test_seed_map_distinct_tuples_account_for_reduced_bootstrap_stream():
    config = json.loads((BUNDLE / "artifacts/config.json").read_text())["seed_map"]["distinct_tuples"]
    assert config["null"] == 4 * 5 * 2000 * 3 == 120_000
    assert config["power"] == 7 * 5 * 1000 * 4 == 140_000          # generation unaffected: all 7 scenarios, 4 streams
    assert config["checks_bootstrap"] == 4 * 5 == 20                # only FDR_SCENARIOS (102,104,105,106) get bootstrap
    assert config["total"] == 260_020


# --------------------------------------------------------------------------- preflight: single fresh replay, RNG-free
def test_preflight_fresh_is_rng_free_and_passes(fresh_preflight_report):
    report = fresh_preflight_report
    assert report["result"] == "PASS"
    assert report["rng_construction_attempts"] == 0 and report["rng_constructed"] is False
    assert report["worlds_generated_from_random_generator"] == 0 and report["seed_tuples_materialised"] == 0
    assert report["checks_total"] == report["checks_passed"] == len(report["results"])
    names = {r["check"] for r in report["results"]}
    assert {"power_rules_and_fdr_scenarios_exclude_p1_p3", "p1_p3_role_and_cell_kind_and_truth"} <= names


def test_recorded_preflight_report_is_the_fresh_report(fresh_preflight_report):
    """An independent replay must equal the canonical artifact without writing it."""
    recorded = json.loads((BUNDLE / "artifacts/preflight_report.json").read_text())
    assert recorded == fresh_preflight_report


# --------------------------------------------------------------------------- fail-closed: corruption and missing required checks
def test_verify_bundle_fails_closed_on_file_corruption(scoped_v4_copy):
    """Corruption guard: verify_bundle() must reject a tampered bundle file even when the manifest is untouched."""
    target = scoped_v4_copy / "runner/acceptance.py"
    target.write_bytes(target.read_bytes() + b"\n# corrupted\n")
    code = f"""
import sys
sys.path.insert(0, {str(scoped_v4_copy / "runner")!r})
import scheme_d_runner as R
try:
    R.verify_bundle()
    print("NOT REJECTED")
except R.IntegrityError as exc:
    print(str(exc))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=ENV, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = proc.stdout.strip().splitlines()[-1]
    assert out == "bundle_hash_mismatch:scripts/m32_scheme_d_validation_bundle_v4_20260922/runner/acceptance.py", out


def test_missing_v4_required_preflight_check_refuses_statistic_gate(scoped_v4_copy):
    """The 2026-09-20 correction's own preflight checks are REQUIRED for assert_equivalence_gate: dropping either
    one from the recorded report must fail-closed, not silently open the optimized-statistic / RNG-mode gate.
    """
    report_path = scoped_v4_copy / "artifacts/preflight_report.json"
    report = json.loads(report_path.read_text())
    kept = [r for r in report["results"] if r["check"] != "power_rules_and_fdr_scenarios_exclude_p1_p3"]
    report["results"] = kept
    report["checks_total"] = len(kept)
    report["checks_passed"] = sum(r["result"] == "PASS" for r in kept)
    report_path.write_text(json.dumps(report))
    manifest_path = scoped_v4_copy / "BUNDLE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files_sha256"][str((BUNDLE / "artifacts/preflight_report.json").relative_to(ROOT))] = _sha(report_path)
    manifest_path.write_text(json.dumps(manifest))
    code = f"""
import sys
sys.path.insert(0, {str(scoped_v4_copy / "runner")!r})
import scheme_d_runner as R
try:
    R.assert_equivalence_gate()
    print("NOT REJECTED")
except R.IntegrityError as exc:
    print(str(exc))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=ENV, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = proc.stdout.strip().splitlines()[-1]
    assert out == "recorded_preflight_not_passed", out


def test_required_preflight_checks_include_v4_correction_checks():
    out = _run(f"""
import sys, json
sys.path.insert(0, {str(RUNNER)!r})
import scheme_d_runner as R
print(json.dumps(sorted(R.REQUIRED_PREFLIGHT_CHECKS)))
""")
    assert {"power_rules_and_fdr_scenarios_exclude_p1_p3", "p1_p3_role_and_cell_kind_and_truth"} <= set(out)


def test_test_guard_rejects_rng_construction():
    with pytest.raises(AssertionError, match="RNG forbidden"):
        _run("np.random.default_rng(); print('{}')")


def test_authorization_and_rng_modes_fail_closed_without_owner_authorization():
    with tempfile.TemporaryDirectory() as td:
        bogus = Path(td) / "auth.json"
        bogus.write_text(json.dumps({"schema": "m3.2-scheme-d-owner-authorization.v1", "mode": "benchmark",
                                     "bundle_sha256": "0" * 64, "authorized_by": "owner"}))
        for mode in ("benchmark", "validation"):
            proc = subprocess.run([sys.executable, str(RUNNER / "scheme_d_runner.py"), mode,
                                   "--authorization", str(bogus), "--out", str(Path(td) / "out")],
                                  capture_output=True, text=True, env=ENV, timeout=60)
            assert proc.returncode != 0 and "authorization_mismatch" in proc.stderr
        assert not (Path(td) / "out").exists()
