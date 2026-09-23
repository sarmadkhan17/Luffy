"""Guard the frozen M3.2 Scheme-D v4 pilot preparation (``scripts/m32_scheme_d_pilot_v4_20260923``).

Launcher: every binding (prespec, request, owner authorization, launcher, evaluator, bundle, v4 shard layer, measured
benchmark receipt, host qualification) verifies from files before the layer/runner is imported and before
``SeedRegistry`` exists; v3/tampered/mis-sized authorizations are refused.  Semantics: P1/C0 is ``perturbed_null``,
still injected, never power/FDR; P6/C4 stays power.  Evaluator: frozen STOP/GO/INCONCLUSIVE precedence, and refusal of
incomplete/tampered/mis-tagged runs built with the real v4 layer's work/merge from synthetic receipts.

No test constructs a random generator, generates a pilot world, or runs the pilot.
"""
from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "scripts/m32_scheme_d_pilot_v4_20260923"
V3_PILOT = ROOT / "scripts/m32_scheme_d_pilot_20260922"
V4_LAYER_DIR = ROOT / "scripts/m32_scheme_d_sharding_v4_20260923"
V3_LAYER = ROOT / "scripts/m32_scheme_d_sharding_20260921/shard_layer.py"
AUTH = PILOT / "OWNER_AUTHORIZATION.json"
TRUTH = ROOT / "scripts/m32_scheme_d_pre_rng_truth_20260921/package/tables"

sys.path.insert(0, str(V4_LAYER_DIR))
import rng_guard  # noqa: E402

_counter = itertools.count()


def _load(path: Path, prefix: str):
    spec = importlib.util.spec_from_file_location(f"{prefix}_{next(_counter)}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


RP = _load(PILOT / "run_pilot.py", "m32_v4_pilot_launcher_test")
EV = _load(PILOT / "evaluate_pilot.py", "m32_v4_pilot_evaluator_test")


def verify(auth=AUTH, **kw):
    kw.setdefault("shard_worlds", 5)
    kw.setdefault("workers", 2)
    return RP.verify_static(auth, **kw)


def auth_with(tmp_path: Path, **changes) -> Path:
    record = json.loads(AUTH.read_text())
    record.update(changes)
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(record))
    return path


# ================================================================================================ 1. bindings
def test_frozen_files_hash_chain_is_consistent():
    auth = json.loads(AUTH.read_text())
    req = json.loads((PILOT / "AUTHORIZATION_REQUEST.json").read_text())
    assert auth["pilot_prespec_sha256"] == sha(PILOT / "PILOT_PRESPEC.json") == RP.EXPECTED_PRESPEC_SHA256 \
        == EV.EXPECTED_PRESPEC_SHA256
    assert auth["authorization_request_sha256"] == sha(PILOT / "AUTHORIZATION_REQUEST.json")
    assert auth["pilot_launcher_sha256"] == sha(PILOT / "run_pilot.py") == req["pilot_launcher_sha256"]
    assert auth["pilot_evaluator_sha256"] == sha(PILOT / "evaluate_pilot.py") == req["pilot_evaluator_sha256"]
    assert auth["bundle_sha256"] == "a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023"
    assert auth["shard_layer_sha256"] == sha(V4_LAYER_DIR / "shard_layer.py") \
        == "5e322153c62b103d00e9e898b30df4efc2031ea66c7144b79d5037b4d03c2170"
    assert auth["fast_benchmark_receipt_sha256"] == "f70ace0a02ab6187647ecc33446238cf5ba9dbe97320194ea75f32876972f23e"
    assert auth["runtime_estimate_cpu_hours"] == 16406.782531828467 * 80 / 75000
    assert (auth["shard_worlds"], auth["workers"], auth["worlds"]) == (5, 2, 80)
    assert auth["cloud_spend"] is False and auth["full_validation_authorized"] is False
    assert auth["scope"] == "pilot_only_non_final" and auth["mode"] == "validation"


def test_real_authorization_verifies_statically():
    assert verify()["authorized_by"] == "owner"


def test_v3_authorization_is_refused():
    with pytest.raises(RP.PilotError, match="authorization_binding_mismatch"):
        verify(V3_PILOT / "OWNER_AUTHORIZATION.json")


def test_v3_authorization_is_refused_by_the_frozen_v4_runner_itself():
    R = RP.load_layer().runner()
    with pytest.raises(Exception, match="authorization_mismatch"):
        R.authorize(V3_PILOT / "OWNER_AUTHORIZATION.json", "validation")


def test_altered_prespec_is_refused(tmp_path):
    record = json.loads((PILOT / "PILOT_PRESPEC.json").read_text())
    record["outcome"]["thresholds"]["null_rejecting_rate_stop"] = 0.5
    altered = tmp_path / "PILOT_PRESPEC.json"
    altered.write_text(json.dumps(record, indent=2))
    with pytest.raises(RP.PilotError, match="pilot_prespec_altered"):
        verify(prespec_path=altered)
    with pytest.raises(EV.RefusedInput, match="pilot_prespec_altered"):
        EV.verify_authorization(AUTH, prespec_path=altered)


def test_altered_request_launcher_or_evaluator_is_refused(tmp_path):
    for kw, name in (("request_path", "AUTHORIZATION_REQUEST.json"), ("launcher_path", "run_pilot.py"),
                     ("evaluator_path", "evaluate_pilot.py")):
        copy = tmp_path / name
        copy.write_bytes((PILOT / name).read_bytes() + b"\n")
        with pytest.raises(RP.PilotError, match="authorization_binding_mismatch"):
            verify(**{kw: copy})


def test_wrong_benchmark_receipt_is_refused(tmp_path, monkeypatch):
    with pytest.raises(RP.PilotError, match="authorization_binding_mismatch:fast_benchmark_receipt_sha256"):
        verify(auth_with(tmp_path, fast_benchmark_receipt_sha256="0" * 64))
    with pytest.raises(RP.PilotError, match="authorization_binding_mismatch:runtime_estimate_cpu_hours"):
        verify(auth_with(tmp_path, runtime_estimate_cpu_hours=19.82))
    fake = tmp_path / "phase90_benchmark_receipt.json"
    fake.write_text("{}")
    monkeypatch.setattr(RP, "BENCHMARK_RECEIPT", fake)
    with pytest.raises(RP.PilotError, match="evidence_hash_mismatch"):
        verify()


def test_wrong_shard_layer_is_refused(tmp_path):
    with pytest.raises(RP.PilotError, match="authorization_binding_mismatch:shard_layer_sha256"):
        verify(auth_with(tmp_path, shard_layer_sha256=sha(V3_LAYER)))
    with pytest.raises(RP.PilotError, match="shard_layer_hash_mismatch"):
        verify(layer_path=V3_LAYER)


@pytest.mark.parametrize("key,value", [("shard_worlds", 10), ("workers", 4), ("worlds", 75000), ("worlds", 100),
                                       ("cloud_spend", True), ("full_validation_authorized", True),
                                       ("scope", "full_validation"), ("mode", "benchmark"),
                                       ("host_receipt_sha256", "0" * 64), ("qualified_workers", 8)])
def test_wrong_authorization_fields_are_refused(tmp_path, key, value):
    with pytest.raises(RP.PilotError, match=f"authorization_binding_mismatch:{key}"):
        verify(auth_with(tmp_path, **{key: value}))


def test_wrong_runtime_shard_worlds_or_workers_are_refused():
    with pytest.raises(RP.PilotError, match="pilot_execution_binding_mismatch"):
        verify(shard_worlds=10)
    with pytest.raises(RP.PilotError, match="pilot_execution_binding_mismatch"):
        verify(workers=3)
    for argv in (["init", "--workers", "3"], ["work", "--worker", "2"], ["init", "--shard-worlds", "10"]):
        assert RP.main(argv + ["--authorization", str(AUTH), "--out", "/nonexistent/never-created"]) == 2
    assert not Path("/nonexistent/never-created").exists()


def test_bad_authorization_fails_before_layer_import_or_seed_registry(tmp_path):
    fresh = _load(PILOT / "run_pilot.py", "m32_v4_pilot_launcher_fresh")
    out = tmp_path / "out"
    assert fresh.main(["init", "--authorization", str(V3_PILOT / "OWNER_AUTHORIZATION.json"), "--out", str(out)]) == 2
    assert fresh._LAYER == {} and not out.exists()


def test_valid_authorization_reaches_seed_registry_only_after_every_check(tmp_path):
    """Subprocess (clean import isolation, OPENBLAS_NUM_THREADS=1): the full ``context()`` chain -- static bindings,
    plan kinds, environment, isolation, runner ``authorize``, layer binding, equivalence gate -- passes with the real
    authorization and stops at a sentinel that replaces ``SeedRegistry``; no registry or generator is constructed."""
    code = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("rp", {str(PILOT / 'run_pilot.py')!r})
rp = importlib.util.module_from_spec(spec); spec.loader.exec_module(rp)
R = rp.load_layer().runner()
class Reached(BaseException): pass
def sentinel(auth):
    assert type(auth).__name__ == "Authorization" and auth.mode == "validation"
    raise Reached()
R.SeedRegistry = sentinel
try:
    rp.context({str(AUTH)!r}, 5, 2)
except Reached:
    print("REACHED_SEED_REGISTRY_SENTINEL")
"""
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "REACHED_SEED_REGISTRY_SENTINEL" in proc.stdout


# ================================================================================================ 2. cell semantics
def test_p1_is_perturbed_null_and_p6_is_power():
    R = RP.load_layer().runner()
    plan = {c["name"]: c for c in RP.pilot_plan(R)}
    assert list(plan) == ["N0/C0", "N3/C4", "P1/C0", "P6/C4"]
    assert all(c["worlds"] == 20 for c in plan.values())
    assert plan["P1/C0"]["kind"] == "perturbed_null" and plan["P1/C0"]["scenario"] == 101
    assert plan["P6/C4"]["kind"] == "power" and plan["P6/C4"]["scenario"] == 106
    assert plan["N0/C0"]["kind"] == plan["N3/C4"]["kind"] == "null"
    assert 101 not in R.acceptance.POWER_RULES and 101 not in R.acceptance.FDR_SCENARIOS
    assert 106 in R.acceptance.POWER_RULES and 106 in R.acceptance.FDR_SCENARIOS


def test_pilot_plan_refuses_p1_tagged_power():
    R = RP.load_layer().runner()

    class Fake:
        PERTURBED_NULL_SCENARIOS = R.PERTURBED_NULL_SCENARIOS
        acceptance = R.acceptance

        @staticmethod
        def cell_plan():
            return [{**c, "kind": "power"} if c["name"] == "P1/C0" else c for c in R.cell_plan()]
    with pytest.raises(RP.PilotError, match="pilot_cell_kind_mismatch:P1/C0"):
        RP.pilot_plan(Fake)


def test_p1_still_receives_injection_through_the_v4_layer_evaluator():
    S = RP.load_layer()
    R = S.runner()
    F = S.fixture_module()
    plan = {c["name"]: {**c, "worlds": 2} for c in RP.pilot_plan(R)}
    calls, original = [], R.apply_injection

    def spy(world, scenario, injection_rng):
        calls.append(scenario)
        return original(world, scenario, injection_rng)
    guard = rng_guard.RNGGuard()
    R.apply_injection = spy
    try:
        with guard, F.installed(R):
            evaluator = S.make_evaluator(F.StubRegistry(R.MASTER_SEED), F.FIXTURE_DRAWS)
            for name in ("N0/C0", "N3/C4", "P1/C0", "P6/C4"):
                for w in range(2):
                    receipt = evaluator(plan[name], w)
                    assert ("injection" in receipt["seed_tuples"]) == (plan[name]["scenario"] is not None)
    finally:
        R.apply_injection = original
    assert calls == [101, 101, 106, 106]
    assert "injection" in S.expected_tuples(plan["P1/C0"], 0)
    assert guard.counters()["constructor_attempts"] == 0


def test_p1_cannot_enter_power_or_fdr_evaluation():
    S = RP.load_layer()
    R = S.runner()
    F = S.fixture_module()
    plan = {c["name"]: c for c in RP.pilot_plan(R)}

    class NoGenerator:
        def generator(self, *a):
            raise AssertionError("bootstrap requested")
    assert S.bootstrap_provider(NoGenerator())(plan["P1/C0"]) is None       # no bootstrap/FDR stream for P1
    worlds = [{"refused": False, "refused_hypotheses": 0, "R": 1, "V": 1, "true_discoveries": 0,
               "attribution_errors": 0, "marginal_p": None}] * 3
    p1 = R.acceptance.evaluate_cell("perturbed_null", 101, worlds)
    assert set(p1["gates"]) == {"refusal_blocker_absent", "type_one"} and p1["fdp_count"] == 3
    assert R.acceptance.power_pass(101, [5, 5, 5]) is False
    EV.check_p1_not_power({"cells": {"P1/C0": p1, "P6/C4": {"gates": {"power": False, "fdr": False}}}})
    as_power = R.acceptance.evaluate_cell("power", 101, worlds)
    with pytest.raises(EV.RefusedInput, match="p1_evaluated_as_power"):
        EV.check_p1_not_power({"cells": {"P1/C0": {**as_power, "gates": {**as_power["gates"], "power": False}},
                                          "P6/C4": {"gates": {"power": False, "fdr": False}}}})
    with pytest.raises(EV.RefusedInput, match="p6_not_evaluated_as_power"):
        EV.check_p1_not_power({"cells": {"P1/C0": p1, "P6/C4": {"gates": {"type_one": True}}}})
    assert F is not None


def test_prespec_effect_targets_are_the_p6_injections_with_positive_truth_sign():
    from trader.cognition import m32_scheme_d_validation as V
    prespec = json.loads((PILOT / "PILOT_PRESPEC.json").read_text())
    targets = prespec["outcome"]["effect_targets"]
    assert list(targets) == ["P6/C4"]                                     # no P1 lift/effect target
    assert targets["P6/C4"] == list(V.INJECTIONS[106])
    records = json.loads((TRUTH / "P6_C4.json").read_text())["records"]
    assert {(records[h]["classification"], records[h]["sign"]) for h in targets["P6/C4"]} == {("analytic_non_null", 1)}
    assert prespec["cell_roles"]["P1/C0"]["kind"] == "perturbed_null"


# ================================================================================================ 3. evaluator precedence
def _refusal(exceeds=False):
    return {n: {"exceeds_limit": exceeds and n == "N0/C0"} for n in EV.CELLS}


def _t1(stop_cell=None):
    return {n: {"stop": n == stop_cell} for n in EV.TYPE_ONE_CELLS}


def _eff(median, pos=0, nonpos=0):
    return {"median_signed_target_lift": median, "positive_worlds": pos, "non_positive_worlds": nonpos}


@pytest.mark.parametrize("kw,expected", [
    (dict(integrity_failures=["run_integrity_stop"], refusal=_refusal(True), type_one=_t1("N0/C0"), effect=_eff(0.2, 20)),
     ("STOP", "integrity_failure")),
    (dict(integrity_failures=[], refusal=_refusal(True), type_one=_t1("N0/C0"), effect=_eff(0.2, 20)),
     ("STOP", "refusal_rate_exceeds_1pct")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1("P1/C0"), effect=_eff(0.2, 20)),
     ("STOP", "null_or_perturbed_null_rejecting_rate_ge_0.25:P1/C0")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=_eff(-0.05, 2, 18)),
     ("STOP", "p6_wrong_direction")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=_eff(-0.05, 9, 11)),
     ("INCONCLUSIVE", "no_stop_and_no_clean_p6_effect")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=_eff(0.005, 15, 5)),
     ("STOP", "p6_negligible_effect")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=_eff(-0.0099, 5, 15)),
     ("STOP", "p6_negligible_effect")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=_eff(None)),
     ("INCONCLUSIVE", "p6_no_effect_evaluable_worlds")),
    (dict(integrity_failures=["run_integrity_stop"], refusal=_refusal(), type_one=_t1(), effect=_eff(None)),
     ("STOP", "integrity_failure")),
    (dict(integrity_failures=[], refusal=_refusal(True), type_one=_t1(), effect=_eff(None)),
     ("STOP", "refusal_rate_exceeds_1pct")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1("N3/C4"), effect=_eff(None)),
     ("STOP", "null_or_perturbed_null_rejecting_rate_ge_0.25:N3/C4")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=_eff(0.01, 12, 8)),
     ("GO", "p6_encouraging_effect")),
    (dict(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=_eff(0.2, 11, 9)),
     ("INCONCLUSIVE", "no_stop_and_no_clean_p6_effect")),
])
def test_evaluator_applies_frozen_precedence(kw, expected):
    verdict, reason = EV.decide(**kw)
    assert verdict == expected[0] and reason.startswith(expected[1])


def test_type_one_diagnostic_threshold_and_wilson():
    ok = [{"R": 0}] * 16 + [{"R": 2}] * 4
    bad = [{"R": 0}] * 15 + [{"R": 1}] * 5
    assert EV.type_one_diagnostic(ok)["stop"] is False
    d = EV.type_one_diagnostic(bad)
    assert d["stop"] is True and d["rate"] == 0.25 and d["rejecting_worlds"] == 5
    R = RP.load_layer().runner()
    assert tuple(d["wilson_95"]) == R.acceptance.wilson(5, 20)
    assert EV.type_one_diagnostic([{"world_refused": ["x"]}] * 20)["stop"] is True


# ================================================================================================ 4. evaluator on real layer artifacts
TARGETS = json.loads((PILOT / "PILOT_PRESPEC.json").read_text())["outcome"]["effect_targets"]["P6/C4"]


def _receipt(S, cell, w, *, lift=0.0, reject=False, refused=False):
    body = {"schema": S.WORLD_SCHEMA, "cell": cell["name"], "phase": cell["phase"], "world": w,
            "seed_tuples": S.expected_tuples(cell, w)}
    if refused:
        body["world_refused"] = ["synthetic"]
    else:
        signed = [0.0] * 384
        if cell["name"] == "P6/C4":
            for h in TARGETS:
                signed[h] = lift
        rejected = [0] if reject else []
        body.update({"draws": 767_999, "statistic_path": "fast", "refusals": {}, "orbit_size": "1",
                     "signed_hex": [float(x).hex() for x in signed], "exceedances": [0] * 384,
                     "p": ["1/768000"] * 384, "rejected": rejected, "R": len(rejected), "V": len(rejected),
                     "true_discoveries": 0, "attribution_errors": 0})
    body["sha256"] = S.sha256_bytes(S.canon(body).encode())
    return body


def build_run(tmp_path: Path, *, p6_lifts=None, rejecting=None, refused=None, plan_edit=None, auth=AUTH) -> Path:
    """Real v4 layer init/work(2 static workers)/merge over synthetic receipts -- no RNG, no pilot world."""
    S = RP.load_layer()
    R = S.runner()
    plan = RP.pilot_plan(R)
    if plan_edit:
        plan = plan_edit(plan)
    p6_lifts = p6_lifts or [0.1] * 20
    rejecting = rejecting or {}
    refused = refused or {}

    def evaluator(cell, w):
        return _receipt(S, cell, w, lift=p6_lifts[w] if cell["name"] == "P6/C4" else 0.0,
                        reject=w < rejecting.get(cell["name"], 0), refused=w < refused.get(cell["name"], 0))
    common = {"bundle_sha256": R.bundle_sha256(), "authorization_sha256": sha(auth), "runner_sha256": sha(R.__file__),
              "environment_lock_sha256": "e" * 64, "environment": {"python": "x", "numpy": "x", "openblas_threads": "1"},
              "non_inferential": False, "pilot_prespec_sha256": sha(PILOT / "PILOT_PRESPEC.json"),
              "authorization_request_sha256": sha(PILOT / "AUTHORIZATION_REQUEST.json"),
              "pilot_launcher_sha256": sha(PILOT / "run_pilot.py"),
              "pilot_evaluator_sha256": sha(PILOT / "evaluate_pilot.py"), "pilot_scope": "pilot_only_non_final"}
    ctx = S.Context(mode="validation", plan=plan, shard_worlds=5, draws=767_999, bindings_common=common,
                    evaluator=evaluator, bootstrap=lambda cell: None)
    out = tmp_path / "run"
    S.init_run(out, ctx)
    for worker in (0, 1):
        S.work(out, ctx, worker, 2, "static")
    S.merge(out, ctx)
    return out


def test_evaluator_go_on_clean_synthetic_run(tmp_path):
    guard = rng_guard.RNGGuard()
    with guard:
        run = build_run(tmp_path, p6_lifts=[0.1] * 15 + [-0.1] * 5, rejecting={"P1/C0": 2})
        verdict = EV.evaluate(run, AUTH)
    assert guard.counters() == {"constructor_attempts": 0, "random_variates_requested": 0,
                                "validation_worlds_generated": 0}
    assert verdict["verdict"] == "GO" and verdict["reason"] == "p6_encouraging_effect"
    eff = verdict["p6_effect"]
    assert eff["positive_worlds"] == 15 and eff["median_signed_target_lift"] == 0.1
    assert eff["statistic"] == "composite_signed_target_lift" and eff["effect_evaluable_worlds"] == 20
    assert all(w["target_tested_count"] == 32 and w["target_refused_count"] == 0 for w in eff["worlds"])
    assert set(eff["family_median_of_world_means"]) == {"0-7", "136-143", "272-279", "344-351"}
    assert "not enough computation evidence" in eff["historical_v3_note"]
    p1 = verdict["p1_perturbed_null"]
    assert p1["rejecting_worlds"] == 2 and p1["rate"] == 0.1 and p1["every_rejection_is_false_discovery"]
    assert "cannot establish" in p1["statement"] and "diagnostic only" in p1["statement"]
    assert "P1/C0" not in json.dumps(verdict["p6_effect"])
    assert verdict["non_final_statements"] == EV.NON_FINAL and "does not authorize full validation" in EV.NON_FINAL
    assert verdict["integrity"]["worker_hardware_identities_distinct"] == 1


def test_evaluator_cli_writes_verdict_once(tmp_path):
    run = build_run(tmp_path)
    out = tmp_path / "PILOT_VERDICT.json"
    argv = ["--run-dir", str(run), "--authorization", str(AUTH), "--verdict-out", str(out)]
    assert EV.main(argv) == 0 and json.loads(out.read_text())["verdict"] == "GO"
    assert EV.main(argv) == 2                                            # never overwrites a verdict


@pytest.mark.parametrize("kw,expected", [
    (dict(rejecting={"P1/C0": 5}), ("STOP", "null_or_perturbed_null_rejecting_rate_ge_0.25:P1/C0")),
    (dict(rejecting={"N3/C4": 6}), ("STOP", "null_or_perturbed_null_rejecting_rate_ge_0.25:N3/C4")),
    (dict(refused={"N0/C0": 1}), ("STOP", "refusal_rate_exceeds_1pct:N0/C0")),
    (dict(p6_lifts=[-0.1] * 20), ("STOP", "p6_wrong_direction")),
    (dict(p6_lifts=[0.001] * 20), ("STOP", "p6_negligible_effect")),
    (dict(p6_lifts=[0.1] * 11 + [-0.001] * 9), ("INCONCLUSIVE", "no_stop_and_no_clean_p6_effect")),
])
def test_evaluator_end_to_end_stop_and_inconclusive(tmp_path, kw, expected):
    verdict = EV.evaluate(build_run(tmp_path, **kw), AUTH)
    assert (verdict["verdict"], verdict["reason"]) == expected


def _refused(run: Path, match: str, auth=AUTH):
    with pytest.raises(EV.RefusedInput, match=match):
        EV.evaluate(run, auth)


def test_evaluator_refuses_tampered_receipt(tmp_path):
    run = build_run(tmp_path)
    path = run / "merged" / "P6_C4.receipts.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"R":0', b'"R":1', 1))
    _refused(run, "merged_cell_file_mismatch")


def test_evaluator_refuses_missing_or_tampered_shard(tmp_path):
    run = build_run(tmp_path)
    (run / "shards" / "shard_000003.complete.json").unlink()
    _refused(run, "incomplete_shards")
    run2 = build_run(tmp_path / "b")
    comp = run2 / "shards" / "shard_000007.receipts.jsonl"
    comp.write_bytes(comp.read_bytes()[:-10])
    _refused(run2, "shard_completion_mismatch")


def test_evaluator_refuses_tampered_merge_manifest_or_result(tmp_path):
    run = build_run(tmp_path)
    manifest = run / "merged" / "MERGE_MANIFEST.json"
    record = json.loads(manifest.read_text())
    record["worlds"] = 60
    manifest.write_text(json.dumps(record, sort_keys=True, indent=1) + "\n")
    _refused(run, "merge_manifest_tampered")
    run2 = build_run(tmp_path / "b")
    (run2 / "merged" / "run_result.json").write_text("{}\n")
    _refused(run2, "run_result_mismatch")


def test_evaluator_refuses_missing_provenance(tmp_path):
    run = build_run(tmp_path)
    shutil.rmtree(run / "provenance")
    _refused(run, "missing_provenance")


def test_evaluator_refuses_unexpected_cell_set(tmp_path):
    def swap(plan):
        R = RP.load_layer().runner()
        n1 = next(c for c in R.cell_plan() if c["name"] == "N1/C0")
        return [plan[0], {**n1, "worlds": 20}, plan[2], plan[3]]
    _refused(build_run(tmp_path, plan_edit=swap), "unexpected_cell_set")


def test_evaluator_refuses_p1_tagged_power(tmp_path):
    def tag(plan):
        return [{**c, "kind": "power"} if c["name"] == "P1/C0" else c for c in plan]
    _refused(build_run(tmp_path, plan_edit=tag), "cell_kind_or_identity_mismatch:P1/C0")


def test_evaluator_refuses_wrong_or_v3_authorization(tmp_path):
    run = build_run(tmp_path)
    _refused(run, "authorization_binding_mismatch", auth=V3_PILOT / "OWNER_AUTHORIZATION.json")
    other = auth_with(tmp_path, authorized_on="tampered")                 # all fields valid, different file hash
    _refused(run, "run_binding_mismatch:authorization_sha256", auth=other)


def test_evaluator_refuses_run_bound_to_wrong_prespec_or_layer(tmp_path, monkeypatch):
    run = build_run(tmp_path)
    monkeypatch.setattr(EV, "LAYER_SHA256", "0" * 64)
    _refused(run, "authorization_binding_mismatch:shard_layer_sha256")


# ================================================================================================ 4b. P6 composite diagnostic
SIGNS = EV.effect_truth_signs(json.loads((PILOT / "PILOT_PRESPEC.json").read_text()))


def _p6(world=0, values=None, refused_targets=()):
    signed = [None] * 384
    for h in range(384):
        signed[h] = None if h in refused_targets else float((values or {}).get(h, 0.0)).hex()
    return {"world": world, "signed_hex": signed, "refusals": {str(h): "support" for h in refused_targets}}


def test_evaluator_target_set_is_exactly_injections_106_and_refuses_otherwise():
    from trader.cognition import m32_scheme_d_validation as V
    assert EV.INJECTIONS_106 == tuple(V.INJECTIONS[106])
    assert list(SIGNS) == list(V.INJECTIONS[106]) and set(SIGNS.values()) == {1}
    prespec = json.loads((PILOT / "PILOT_PRESPEC.json").read_text())
    for bad in (TARGETS[:-1], TARGETS + [352], TARGETS[:-1] + [352], sorted(TARGETS, reverse=True)):
        altered = json.loads(json.dumps(prespec))
        altered["outcome"]["effect_targets"]["P6/C4"] = bad
        with pytest.raises(EV.RefusedInput, match="effect_target_set_mismatch"):
            EV.effect_truth_signs(altered)
    altered = json.loads(json.dumps(prespec))
    altered["outcome"]["effect_targets"]["P1/C0"] = [0]
    with pytest.raises(EV.RefusedInput, match="effect_target_set_mismatch"):
        EV.effect_truth_signs(altered)


def test_evaluator_signs_come_from_hash_bound_truth_table_and_require_analytic_non_null(tmp_path, monkeypatch):
    prespec = json.loads((PILOT / "PILOT_PRESPEC.json").read_text())
    assert sha(TRUTH / "P6_C4.json") == EV.P6_TRUTH_TABLE_SHA256
    cert = json.loads((TRUTH.parent / "proof_certificate.json").read_text())
    assert cert["table_sha256"]["tables/P6_C4.json"] == EV.P6_TRUTH_TABLE_SHA256
    table = json.loads((TRUTH / "P6_C4.json").read_text())
    table["records"][137]["classification"] = "true_null"
    tampered = tmp_path / "P6_C4.json"
    tampered.write_text(json.dumps(table))
    with pytest.raises(EV.RefusedInput, match="p6_truth_table_hash_mismatch"):
        EV.effect_truth_signs(prespec, tampered)
    monkeypatch.setattr(EV, "P6_TRUTH_TABLE_SHA256", sha(tampered))
    with pytest.raises(EV.RefusedInput, match="p6_target_not_analytic_non_null:137"):
        EV.effect_truth_signs(prespec, tampered)
    table["records"][137]["classification"] = "analytic_non_null"
    table["records"][137]["sign"] = -1
    tampered.write_text(json.dumps(table))
    monkeypatch.setattr(EV, "P6_TRUTH_TABLE_SHA256", sha(tampered))
    signs = EV.effect_truth_signs(prespec, tampered)
    w = EV.world_effect(_p6(values={h: 0.5 for h in TARGETS}), signs)
    assert w["composite_signed_target_lift"] == (31 * 0.5 - 0.5) / 32          # contribution = effect * truth sign


def test_world_effect_exact_decode_excludes_refused_targets_and_counts():
    values = {h: 0.1 for h in TARGETS}
    values.update({0: 0.3, 136: -0.2, 351: 5.0})
    w = EV.world_effect(_p6(values=values, refused_targets=(1, 2, 272, 344)), SIGNS)
    tested = [h for h in TARGETS if h not in (1, 2, 272, 344)]
    assert (w["target_tested_count"], w["target_refused_count"]) == (28, 4)
    expected = sum(float.fromhex(float(values[h]).hex()) for h in tested) / 28
    assert w["composite_signed_target_lift"] == expected and w["effect_evaluable"]
    assert w["family_means"]["0-7"] == (0.3 + 0.1 * 5) / 6
    assert w["family_means"]["136-143"] == (-0.2 + 0.1 * 7) / 8
    assert w["family_means"]["272-279"] == 0.1 * 7 / 7
    assert w["family_means"]["344-351"] == (0.1 * 6 + 5.0) / 7
    exact = 0.1 + 2 ** -52
    assert EV.world_effect(_p6(values={h: exact for h in TARGETS}), SIGNS)["composite_signed_target_lift"] == exact


def test_world_effect_refuses_inconsistent_statistics():
    receipt = _p6(refused_targets=(5,))
    receipt["signed_hex"][5] = (0.2).hex()
    with pytest.raises(EV.RefusedInput, match="p6_refused_target_has_statistic"):
        EV.world_effect(receipt, SIGNS)
    receipt = _p6()
    receipt["signed_hex"][140] = None
    with pytest.raises(EV.RefusedInput, match="p6_tested_target_missing_statistic"):
        EV.world_effect(receipt, SIGNS)


def test_zero_testable_world_is_refused_for_effect_and_excluded_without_new_gate():
    all_refused = _p6(world=3, refused_targets=tuple(TARGETS))
    w = EV.world_effect(all_refused, SIGNS)
    assert w["effect_evaluable"] is False and w["effect_refusal"] == "zero_testable_injected_targets"
    assert w["effect_status"] == "UNEVALUABLE"
    assert w["composite_signed_target_lift"] is None and w["target_tested_count"] == 0 and w["target_refused_count"] == 32
    receipts = [_p6(world=i, values={h: 0.05 for h in TARGETS}) for i in range(12)] + [all_refused]
    d = EV.effect_diagnostic(receipts, SIGNS)
    assert d["effect_evaluable_worlds"] == 12 and d["effect_unevaluable_worlds"] == 1 and d["non_refused_worlds"] == 13
    assert [x["effect_status"] for x in d["worlds"]] == ["EVALUABLE"] * 12 + ["UNEVALUABLE"]
    assert d["median_signed_target_lift"] == 0.05 and d["positive_worlds"] == 12 and d["non_positive_worlds"] == 0
    assert "equal-weight, unstandardized, cross-family pilot diagnostic" in d["nature"]
    assert "NOT a formal Scheme-D effect-size statistic" in d["nature"]
    # an unevaluable world is never scored as zero: adding it cannot drag the median toward 0
    mixed = EV.effect_diagnostic([_p6(world=0, values={h: 0.05 for h in TARGETS}), all_refused], SIGNS)
    assert mixed["median_signed_target_lift"] == 0.05
    none = EV.effect_diagnostic([_p6(world=i, refused_targets=tuple(TARGETS)) for i in range(3)], SIGNS)
    assert none["median_signed_target_lift"] is None
    assert (none["effect_evaluable_worlds"], none["effect_unevaluable_worlds"]) == (0, 3)
    assert none["positive_worlds"] == 0 and none["non_positive_worlds"] == 0
    assert EV.decide(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=none) == \
        ("INCONCLUSIVE", "p6_no_effect_evaluable_worlds")
    assert EV.decide(integrity_failures=["run_integrity_stop"], refusal=_refusal(), type_one=_t1(), effect=none)[0] \
        == "STOP"
    assert EV.decide(integrity_failures=[], refusal=_refusal(True), type_one=_t1(), effect=none) == \
        ("STOP", "refusal_rate_exceeds_1pct:N0/C0")


def test_family_means_are_reported_but_never_decide():
    receipts = [_p6(world=i, values={**{h: 0.2 for h in TARGETS}, **{h: -1.0 for h in range(136, 144)}})
                for i in range(20)]
    d = EV.effect_diagnostic(receipts, SIGNS)
    assert d["family_median_of_world_means"] == {"0-7": 0.2, "136-143": -1.0, "272-279": 0.2, "344-351": 0.2}
    assert d["family_means_are_descriptive_only"] is True
    assert d["median_signed_target_lift"] == pytest.approx((24 * 0.2 - 8.0) / 32) and d["median_signed_target_lift"] < -0.01
    assert EV.decide(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=d) == ("STOP", "p6_wrong_direction")
    good = EV.effect_diagnostic([_p6(world=i, values={**{h: 0.3 for h in TARGETS}, **{h: -0.5 for h in range(0, 8)}})
                                 for i in range(20)], SIGNS)
    assert good["family_median_of_world_means"]["0-7"] == -0.5                  # a negative family cannot block GO
    assert EV.decide(integrity_failures=[], refusal=_refusal(), type_one=_t1(), effect=good) == \
        ("GO", "p6_encouraging_effect")


def test_prespec_documents_diagnostic_nature_and_no_historical_reproduction_claim():
    prespec = json.loads((PILOT / "PILOT_PRESPEC.json").read_text())
    text = json.dumps(prespec)
    d = prespec["outcome"]["signed_target_lift_definition"]
    assert d["name"] == "composite_signed_target_lift"
    assert "NOT a formal Scheme-D effect-size statistic" in d["nature"] and "equal-weight, unstandardized" in d["nature"]
    assert "not enough computation evidence to reconstruct its exact derivation" in prespec["outcome"]["comparability_note"]
    for claim in ("reproduces 0.133", "proven to be", "matches the v3", "identical to the v3"):
        assert claim not in text
    rules = prespec["outcome"]["rules"]
    assert "no world has a defined lift" not in rules["5_p6_negligible_stop"]
    assert "INCONCLUSIVE" in rules["7a_p6_no_effect_evaluable_world"]
    assert "UNEVALUABLE" in d["zero_testable_targets"] and "not assigned zero" in d["zero_testable_targets"]
    assert prespec["outcome"]["thresholds"] == {"refusal_rate_limit": 0.01, "null_rejecting_rate_stop": 0.25,
                                                "effect_min_abs_median": 0.01, "wrong_direction_median": -0.01,
                                                "min_directional_worlds": 12}


# ================================================================================================ 5. no RNG, no pilot run
def test_importing_launcher_and_evaluator_constructs_no_rng_and_runs_nothing():
    guard = rng_guard.RNGGuard()
    with guard:
        rp = _load(PILOT / "run_pilot.py", "m32_v4_pilot_import_check")
        ev = _load(PILOT / "evaluate_pilot.py", "m32_v4_pilot_import_check_ev")
        rp.verify_static(AUTH, shard_worlds=5, workers=2)
        ev.verify_authorization(AUTH)
    assert guard.counters() == {"constructor_attempts": 0, "random_variates_requested": 0,
                                "validation_worlds_generated": 0}
    assert rp._LAYER == {}                                               # import alone loads no layer/runner
    for token in ("np.random", "numpy.random", "default_rng", "PCG64", "SeedSequence", "import random"):
        assert token not in (PILOT / "evaluate_pilot.py").read_text()
        assert token not in (PILOT / "run_pilot.py").read_text()


def test_no_pilot_run_or_verdict_exists():
    assert sorted(p.name for p in PILOT.iterdir() if p.name != "__pycache__") == [
        "AUTHORIZATION_REQUEST.json", "OWNER_AUTHORIZATION.json", "PILOT_PRESPEC.json", "evaluate_pilot.py",
        "run_pilot.py"]
