"""M3.2 scheme D coverage_class_confounding independent-seed replication. SYNTHETIC ONLY.

Thin wrapper around the FROZEN harness scripts/m32_conditioning_comparison_20260919/sim.py
(base commit 12ffa3e9b8e28a816a53ad00c7b63d7e773793e8). It copies no formulas and replaces no
functions: it imports the unchanged sim.py, applies the single authorized runtime override
sim.SEED = 2026091901 (the frozen prespec's master_seed is NOT changed), and calls the unchanged
sim.run_world(sc, w, "D", 0.0, 1999, forced=False, tag=7) for w in 0..9999, then the unchanged
sim.summarize.

Order: verify replication_prespec.json fields, this wrapper's SHA256, every frozen file against
the prespec hashes AND the base-commit blob bytes, and the environment -- all BEFORE any RNG call
and before the output directory exists. Then exclusive-create the output directory (the attempt
marker; one attempt, no rerun), write the run-start receipt, compute, and write results.

Run:  OPENBLAS_NUM_THREADS=1 ./venv/bin/python -B scripts/m32_scheme_d_replication_20260919/run_replication.py
"""
import sys

sys.dont_write_bytecode = True

import copy  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import platform  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PRESPEC_PATH = os.path.join(HERE, "replication_prespec.json")
WRAPPER_PATH = os.path.abspath(__file__)

# Values this wrapper is written for; the prespec must state exactly these.
EXPECT = {
    "schema": "m32-scheme-d-replication-prespec.v1",
    "base_commit": "12ffa3e9b8e28a816a53ad00c7b63d7e773793e8",
    "root_seed": 2026091901,
    "frozen_master_seed": 20260919,
    "scenario_name": "coverage_class_confounding",
    "scenario_code": 8,
    "scheme": "D",
    "scheme_index": 3,
    "effect": 0.0,
    "B": 1999,
    "forced": False,
    "tag": 7,
    "world_start": 0,
    "world_stop_exclusive": 10000,
    "n_worlds": 10000,
    "alpha": 0.05,
    "output_dir": "docs/superpowers/artifacts/m32-scheme-d-replication/2026-09-19-seed-2026091901",
    "frozen_dir": "scripts/m32_conditioning_comparison_20260919",
    "frozen_report": "docs/superpowers/reports/2026-09-19-m32-conditioning-comparison.md",
    "openblas_num_threads": "1",
    "progress_every": 1000,
}
FROZEN_HARNESS_FILES = [
    "checks.py", "code_manifest.sha256", "make_prespec.py", "prespec.json",
    "prespec_v1_discarded.sha256", "prespec_v2_discarded.sha256",
    "reference_outputs_manifest.json", "sim.py",
]


def die(msg):
    sys.stderr.write("REFUSED: " + msg + "\n")
    sys.exit(2)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_bytes(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return fh.read()


def git(*args):
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, check=False)
    if r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def jdefault(o):
    # numpy scalars -> exact Python scalars; anything else is an error, never silently coerced.
    if hasattr(o, "item") and getattr(o, "shape", None) == ():
        return o.item()
    raise TypeError(f"not JSON serializable: {type(o)!r}")


# ------------------------------------------------------------------ pre-RNG verification
def verify_prespec():
    if not os.path.isfile(PRESPEC_PATH):
        die("replication_prespec.json missing")
    with open(PRESPEC_PATH, "rb") as fh:
        raw = fh.read()
    ps = json.loads(raw)
    run = ps.get("run", {})
    checks = {
        "schema": ps.get("schema"),
        "base_commit": ps.get("frozen_base", {}).get("commit"),
        "root_seed": ps.get("seed", {}).get("root_seed"),
        "frozen_master_seed": ps.get("seed", {}).get("frozen_config_master_seed_unchanged"),
        "scenario_name": run.get("scenario_name"),
        "scenario_code": run.get("scenario_code"),
        "scheme": run.get("scheme"),
        "scheme_index": run.get("scheme_index"),
        "effect": run.get("effect"),
        "B": run.get("B"),
        "forced": run.get("forced"),
        "tag": run.get("tag"),
        "world_start": run.get("world_indices", {}).get("start"),
        "world_stop_exclusive": run.get("world_indices", {}).get("stop_exclusive"),
        "n_worlds": run.get("world_indices", {}).get("count"),
        "alpha": ps.get("decision_rule", {}).get("alpha"),
        "output_dir": ps.get("output", {}).get("directory"),
        "frozen_dir": ps.get("frozen_base", {}).get("harness_dir"),
        "frozen_report": ps.get("frozen_base", {}).get("report", {}).get("path"),
        "openblas_num_threads": ps.get("environment", {}).get("OPENBLAS_NUM_THREADS"),
        "progress_every": ps.get("output", {}).get("progress_every_worlds"),
    }
    for k, v in checks.items():
        if v != EXPECT[k] or type(v) is not type(EXPECT[k]):
            die(f"prespec field {k}={v!r} != expected {EXPECT[k]!r}")
    if ps.get("decision_rule", {}).get("size_control_rejected_if") != "wilson95_lower > 0.05":
        die("prespec decision rule is not 'wilson95_lower > 0.05'")
    for flag in ("no_interim_inference", "no_tuning", "one_attempt_no_rerun",
                 "guards_preserved", "no_forced_diagnostics"):
        if ps.get("constraints", {}).get(flag) is not True:
            die(f"prespec constraint {flag} is not true")
    if ps.get("seed", {}).get("chosen_before_outcomes") is not True:
        die("prespec does not record seed chosen before outcomes")
    return ps, raw


def verify_wrapper(ps):
    with open(WRAPPER_PATH, "rb") as fh:
        got = sha256_bytes(fh.read())
    want = ps.get("wrapper", {}).get("sha256")
    if ps.get("wrapper", {}).get("path") != "scripts/m32_scheme_d_replication_20260919/run_replication.py":
        die("prespec wrapper path mismatch")
    if got != want:
        die(f"wrapper sha256 {got} != prespec {want}")
    return got


def frozen_expectations(ps):
    fb = ps["frozen_base"]
    exp = {fb["report"]["path"]: fb["report"]["sha256"]}
    files = fb.get("harness_files", {})
    if sorted(files) != sorted(FROZEN_HARNESS_FILES):
        die("prespec harness file list mismatch")
    for name, h in files.items():
        exp[f"{EXPECT['frozen_dir']}/{name}"] = h
    return exp


def verify_frozen(ps, exp, against_git=True):
    """Every frozen file: working-tree bytes hash == prespec hash, and == base-commit blob bytes."""
    out = {}
    if against_git:
        full = git("rev-parse", "--verify", EXPECT["base_commit"] + "^{commit}").decode().strip()
        if full != EXPECT["base_commit"]:
            die(f"base commit resolves to {full}")
        in_commit = set(git("show", "--name-only", "--format=", EXPECT["base_commit"]).decode().split())
        if in_commit != set(exp):
            die(f"frozen file set != files in base commit: {sorted(in_commit ^ set(exp))}")
    for rel, want in sorted(exp.items()):
        b = read_bytes(rel)
        h = sha256_bytes(b)
        if h != want:
            die(f"frozen file {rel} sha256 {h} != prespec {want}")
        if against_git:
            blob = git("cat-file", "blob", f"{EXPECT['base_commit']}:{rel}")
            if blob != b:
                die(f"frozen file {rel} bytes differ from base commit blob")
        out[rel] = h
    # internal consistency with the frozen code manifest
    cm = read_bytes(f"{EXPECT['frozen_dir']}/code_manifest.sha256").decode().split("\n")
    for line in filter(None, cm):
        h, name = line.split()
        if exp[f"{EXPECT['frozen_dir']}/{name}"] != h:
            die(f"code_manifest.sha256 disagrees for {name}")
    return out


def verify_reference_outputs(ps):
    ref = json.loads(read_bytes(f"{EXPECT['frozen_dir']}/reference_outputs_manifest.json"))
    out = {}
    for a in ref["artifacts"]:
        h = sha256_bytes(read_bytes(a["path"]))
        if h != a["sha256"]:
            die(f"reference output {a['path']} sha256 {h} != manifest {a['sha256']}")
        out[a["path"]] = h
    want = ps["original_comparison"]
    orig = json.loads(read_bytes(want["results_path"]))["null"][EXPECT["scenario_name"]][EXPECT["scheme"]]
    if (orig.get("worlds"), orig.get("tested"), orig.get("reject")) != (want["worlds"], want["tested"], want["reject"]):
        die(f"original results {orig.get('worlds')}/{orig.get('tested')}/{orig.get('reject')} "
            f"!= prespec {want['worlds']}/{want['tested']}/{want['reject']}")
    return out, orig


def verify_environment(ps):
    if "numpy" in sys.modules:
        die("numpy imported before thread setting was verified")
    if os.environ.get("OPENBLAS_NUM_THREADS") != EXPECT["openblas_num_threads"]:
        die("OPENBLAS_NUM_THREADS must be set to '1' in the environment")
    pyv = platform.python_version()
    if pyv != ps["environment"]["python_version"]:
        die(f"python {pyv} != prespec {ps['environment']['python_version']}")
    return pyv


def verify_no_pycache():
    if os.path.exists(os.path.join(ROOT, EXPECT["frozen_dir"], "__pycache__")):
        die("__pycache__ exists in frozen harness dir; refusing (bytecode must not be used)")


# ------------------------------------------------------------------ frozen harness import
def import_frozen_sim():
    verify_no_pycache()
    path = os.path.join(ROOT, EXPECT["frozen_dir"], "sim.py")
    spec = importlib.util.spec_from_file_location("m32_frozen_sim", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # module-level code only reads the frozen prespec.json; no RNG
    verify_no_pycache()
    return mod


def write_manifest(outdir):
    names = sorted(n for n in os.listdir(outdir) if n != "SHA256SUMS")
    lines = []
    for n in names:
        with open(os.path.join(outdir, n), "rb") as fh:
            lines.append(f"{sha256_bytes(fh.read())}  {n}\n")
    with open(os.path.join(outdir, "SHA256SUMS"), "x", encoding="utf-8") as fh:
        fh.writelines(lines)


def main():
    t_wall0 = time.time()
    ps, ps_raw = verify_prespec()
    prespec_sha = sha256_bytes(ps_raw)
    wrapper_sha = verify_wrapper(ps)
    pyv = verify_environment(ps)
    exp = frozen_expectations(ps)
    frozen_hashes = verify_frozen(ps, exp)
    ref_hashes, orig_summary = verify_reference_outputs(ps)

    import numpy as np  # after OPENBLAS_NUM_THREADS verification
    if np.__version__ != ps["environment"]["numpy_version"]:
        die(f"numpy {np.__version__} != prespec {ps['environment']['numpy_version']}")

    sim = import_frozen_sim()
    frozen_ps_bytes = read_bytes(f"{EXPECT['frozen_dir']}/prespec.json")
    if sim.PS != json.loads(frozen_ps_bytes):
        die("sim.PS differs from frozen prespec.json bytes")
    ps_snapshot = copy.deepcopy(sim.PS)
    ps_canon_sha = sha256_bytes(canon(sim.PS))
    if sim.SEED != EXPECT["frozen_master_seed"] or sim.PS["master_seed"] != EXPECT["frozen_master_seed"]:
        die(f"frozen SEED {sim.SEED} != {EXPECT['frozen_master_seed']}")
    if sim.SCHEMES.index(EXPECT["scheme"]) != EXPECT["scheme_index"] or sim.ALPHA != EXPECT["alpha"]:
        die("frozen scheme index / alpha mismatch")
    if [float(v) for v in sim.CLASS_EDGES] != [0.2, 0.4, 0.6]:
        die("frozen missing-fraction class edges changed")
    matches = [s for s in sim.PS["scenarios"] if s["name"] == EXPECT["scenario_name"]]
    if len(matches) != 1 or matches[0]["code"] != EXPECT["scenario_code"]:
        die("scenario not found uniquely with expected code")
    sc = matches[0]
    sc_sha = sha256_bytes(canon(sc))
    if sc_sha != ps["run"]["scenario_canonical_sha256"]:
        die(f"scenario canonical sha256 {sc_sha} != prespec")
    fn_names = ["run_world", "gen_world", "test_world", "strata_ids", "metadata_guards", "mask_class",
                "block_means", "availability", "perm_orders", "center", "summarize", "wilson"]
    fn_ids = {n: getattr(sim, n) for n in fn_names}
    guard_reasons_pre = sim.metadata_guards(sc, EXPECT["scheme"])  # deterministic, no RNG

    # ---- the single authorized runtime override (frozen config untouched)
    sim.SEED = EXPECT["root_seed"]
    if sim.PS["master_seed"] != EXPECT["frozen_master_seed"]:
        die("frozen PS master_seed altered")

    # ---- exclusive attempt: output dir must not exist; creating it spends the one attempt
    outdir = os.path.join(ROOT, EXPECT["output_dir"])
    os.makedirs(os.path.dirname(outdir), exist_ok=True)
    try:
        os.mkdir(outdir)
    except FileExistsError:
        die(f"{EXPECT['output_dir']} exists: one attempt only, no rerun")

    def xopen(name):
        return open(os.path.join(outdir, name), "x", encoding="utf-8")

    start_utc = utc_now()
    with xopen("attempt_marker.json") as fh:
        json.dump({"attempt": 1, "started_utc": start_utc, "prespec_sha256": prespec_sha,
                   "wrapper_sha256": wrapper_sha, "note": "exists => the single authorized attempt was "
                   "started; an interrupted run must not be rerun into this or any other directory"}, fh, indent=1)
        fh.write("\n")
    env = {
        "python_version": pyv, "python_executable": sys.executable, "numpy_version": np.__version__,
        "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
        "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"),
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS"),
        "sys_dont_write_bytecode": sys.dont_write_bytecode,
        "PYTHONDONTWRITEBYTECODE": os.environ.get("PYTHONDONTWRITEBYTECODE"),
    }
    rng_formula = {
        "world_data": f"np.random.default_rng(np.random.SeedSequence([{EXPECT['root_seed']}, "
                      f"{EXPECT['scenario_code']}, world_index]))  (frozen sim.gen_world, SEED overridden)",
        "permutations": f"np.random.default_rng(np.random.SeedSequence([{EXPECT['root_seed']}, "
                        f"{EXPECT['scenario_code']}, world_index, {EXPECT['scheme_index']}, {EXPECT['tag']}]))"
                        "  (frozen sim.run_world, SEED overridden)",
        "call": "sim.run_world(sc, w, 'D', 0.0, 1999, forced=False, tag=7) for w in range(0, 10000)",
        "original_run_seed_for_contrast": EXPECT["frozen_master_seed"],
    }
    receipt = {
        "schema": "m32-scheme-d-replication-run-start.v1", "started_utc": start_utc,
        "prespec_path": os.path.relpath(PRESPEC_PATH, ROOT), "prespec_sha256": prespec_sha,
        "wrapper_path": os.path.relpath(WRAPPER_PATH, ROOT), "wrapper_sha256": wrapper_sha,
        "base_commit": EXPECT["base_commit"], "frozen_file_sha256_verified_vs_prespec_and_commit": frozen_hashes,
        "reference_outputs_sha256_verified": ref_hashes,
        "frozen_PS_canonical_sha256": ps_canon_sha, "scenario_canonical_sha256": sc_sha,
        "runtime_override": {"sim.SEED": {"frozen": EXPECT["frozen_master_seed"], "replication": EXPECT["root_seed"]},
                             "frozen_prespec_master_seed_unchanged": sim.PS["master_seed"]},
        "metadata_guard_reasons_scheme_D_pre_run": guard_reasons_pre,
        "environment": env, "rng_formula": rng_formula,
    }
    with xopen("run_start_receipt.json") as fh:
        json.dump(receipt, fh, indent=1)
        fh.write("\n")

    try:
        results = []
        t0 = time.time()
        with xopen("worlds.jsonl") as wf, xopen("progress.log") as pf:
            for w in range(EXPECT["world_start"], EXPECT["world_stop_exclusive"]):
                r = sim.run_world(sc, w, EXPECT["scheme"], EXPECT["effect"], EXPECT["B"],
                                  forced=EXPECT["forced"], tag=EXPECT["tag"])
                results.append(r)
                wf.write(json.dumps({"world_index": w, "result": r}, default=jdefault, allow_nan=False) + "\n")
                wf.flush()
                done = w - EXPECT["world_start"] + 1
                if done % EXPECT["progress_every"] == 0:  # world counts only; no outcome peeking
                    os.fsync(wf.fileno())
                    line = f"{utc_now()} worlds_done={done} elapsed_s={time.time() - t0:.1f}"
                    pf.write(line + "\n")
                    pf.flush()
                    print(line, flush=True)
            os.fsync(wf.fileno())
        runtime_s = time.time() - t0
        if len(results) != EXPECT["n_worlds"]:
            raise RuntimeError("world count mismatch")

        summary = sim.summarize(results)  # unchanged frozen aggregate

        # ---- post-run integrity (recorded honestly; failure marks the run invalid, never hidden)
        integrity = {}
        try:
            verify_frozen(ps, exp, against_git=True)
            integrity["frozen_files_unchanged"] = True
        except SystemExit:
            integrity["frozen_files_unchanged"] = False
        with open(WRAPPER_PATH, "rb") as fh:
            integrity["wrapper_unchanged"] = sha256_bytes(fh.read()) == wrapper_sha
        with open(PRESPEC_PATH, "rb") as fh:
            integrity["prespec_unchanged"] = sha256_bytes(fh.read()) == prespec_sha
        integrity["sim_PS_equal_deepcopy_snapshot"] = sim.PS == ps_snapshot
        integrity["sim_PS_canonical_sha256_unchanged"] = sha256_bytes(canon(sim.PS)) == ps_canon_sha
        integrity["sim_PS_equal_frozen_bytes"] = sim.PS == json.loads(frozen_ps_bytes)
        integrity["sim_SEED_is_replication_seed"] = sim.SEED == EXPECT["root_seed"]
        integrity["frozen_functions_not_replaced"] = all(getattr(sim, n) is f for n, f in fn_ids.items())
        integrity["no_pycache_in_frozen_dir"] = not os.path.exists(os.path.join(ROOT, EXPECT["frozen_dir"], "__pycache__"))
        try:
            verify_reference_outputs(ps)
            integrity["reference_outputs_unchanged"] = True
        except SystemExit:
            integrity["reference_outputs_unchanged"] = False
        integrity_ok = all(integrity.values())

        # ---- fixed descriptive diagnostics (no new statistical tests)
        def mmm(vals):
            vals = [v for v in vals if v is not None]
            if not vals:
                return None
            return [float(np.min(vals)), float(np.median(vals)), float(np.max(vals))]

        tested = [r for r in results if r["status"] == "tested"]
        with_av = [r for r in results if r.get("avail")]
        diagnostics = {
            "totals": {"worlds": len(results), "tested": len(tested),
                       "refused": len(results) - len(tested),
                       "refused_world_indices": [EXPECT["world_start"] + i for i, r in enumerate(results)
                                                 if r["status"] == "refused"]},
            "support_min_median_max_all_worlds_with_avail": {
                k: mmm([r["avail"][k] for r in with_av])
                for k in ("n_strata", "movable_strata", "movable_blocks", "log10_G", "floor")},
            "support_min_median_max_tested": {
                "distinct_drawn_stats": mmm([r["distinct"] for r in tested]),
                "draws": mmm([r["draws"] for r in tested]),
                "floor": mmm([r["avail"]["floor"] for r in tested]),
            },
        }

        if not tested:
            decision = {"outcome": "UNTESTABLE", "size_control_rejected_wilson_lower_gt_05": None}
        else:
            decision = {"outcome": "SIZE_CONTROL_REJECTED" if summary["size_control_rejected_wilson_lower_gt_05"]
                        else "SIZE_CONTROL_NOT_REJECTED",
                        "size_control_rejected_wilson_lower_gt_05": summary["size_control_rejected_wilson_lower_gt_05"],
                        "wilson95": summary["wilson95"], "reject": summary["reject"], "tested": summary["tested"]}
        if not integrity_ok:
            decision["outcome"] = "INVALID_RUN_INTEGRITY_FAILURE"

        with open(os.path.join(outdir, "worlds.jsonl"), "rb") as fh:
            worlds_sha = sha256_bytes(fh.read())
        res = {
            "schema": "m32-scheme-d-replication-results.v1",
            "status": "complete",
            "started_utc": start_utc, "finished_utc": utc_now(),
            "runtime_s": runtime_s, "wall_s_including_verification": time.time() - t_wall0,
            "scope": ps["scope"],
            "replication": {
                "scenario": EXPECT["scenario_name"], "scenario_code": EXPECT["scenario_code"],
                "scheme": EXPECT["scheme"], "effect": EXPECT["effect"], "B": EXPECT["B"],
                "forced": EXPECT["forced"], "tag": EXPECT["tag"], "root_seed": EXPECT["root_seed"],
                "worlds": [EXPECT["world_start"], EXPECT["world_stop_exclusive"]],
                "decision_rule": ps["decision_rule"], "decision": decision,
                "summary_frozen_sim_summarize": summary,
                "diagnostics_descriptive_only": diagnostics,
                "worlds_jsonl_sha256": worlds_sha,
            },
            "original_comparison_separate_not_pooled": {
                "note": "Original 2026-09-19 run (seed 20260919, worlds 0..1999). Separate evidence; "
                        "NOT pooled with, and NOT used to adjust, this replication.",
                "reported": ps["original_comparison"],
                "summary_from_verified_reference_results": orig_summary,
            },
            "integrity_post_run": integrity, "integrity_ok": integrity_ok,
            "hashes": {"prespec_sha256": prespec_sha, "wrapper_sha256": wrapper_sha,
                       "base_commit": EXPECT["base_commit"], "frozen_files": frozen_hashes,
                       "reference_outputs": ref_hashes, "frozen_PS_canonical_sha256": ps_canon_sha,
                       "scenario_canonical_sha256": sc_sha},
            "environment": env, "rng_formula": rng_formula,
        }
        with xopen("results.json") as fh:
            json.dump(res, fh, indent=1, default=jdefault, allow_nan=False)
            fh.write("\n")
    except BaseException as e:
        with xopen("failure_receipt.json") as fh:
            json.dump({"failed_utc": utc_now(), "error": repr(e), "traceback": traceback.format_exc(),
                       "note": "attempt spent; do not rerun"}, fh, indent=1)
            fh.write("\n")
        write_manifest(outdir)
        raise
    write_manifest(outdir)
    print(f"complete: {EXPECT['output_dir']}/results.json", flush=True)


if __name__ == "__main__":
    main()
