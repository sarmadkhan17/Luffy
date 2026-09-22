#!/usr/bin/env python3
"""RNG-free deterministic sharding / resume / merge layer for the M3.2 Scheme D validation (bundle v4).

This is a v4-bound sibling of ``scripts/m32_scheme_d_sharding_20260921/shard_layer.py`` (bundle v3), which stays
untouched.  Storage layout, exclusivity, crash-recovery, audit and merge mechanics are copied unchanged from the
hardened v3 layer: only the bundle binding and the evaluator's dispatch rule change.  This file constructs no
random generator.  Production world evaluation goes through the bundle-v4 runner's own ``SeedRegistry`` (which
needs a verified owner authorization bound to *this exact bundle*, ``shard_layer_sha256`` and ``shard_worlds``);
the layer itself only partitions, stores, verifies and merges.

Bundle v4 (pre-RNG correction, protocol correction section 2-3): ``cell_plan()`` tags the P1 (scenario 101) and P3
(scenario 103) cells ``kind="perturbed_null"`` instead of ``"power"``.  World generation and injection dispatch in
the frozen ``run_cell`` are keyed by ``cell["scenario"] is not None`` (``has_scenario``), NOT by
``cell["kind"] == "power"``: every scenario cell -- power and perturbed_null alike -- is generated with base dgp 1
and receives its injection.  Only the acceptance-side evaluation differs for perturbed_null cells.  The v3 shard
layer's evaluator dispatches on ``cell["kind"] == "power"``, which was equivalent to ``has_scenario`` in bundle v3
(every scenario cell there is tagged ``"power"``) but is WRONG for bundle v4: it would silently skip injection for
P1/P3.  ``make_evaluator`` and ``expected_tuples`` below dispatch on ``has_scenario`` instead, matching bundle v4's
``run_cell`` textually (see ``tests/test_m32_shard_layer_v4.py::test_v4_evaluator_uses_the_same_calls_and_seed_positions_as_run_cell``).

Import isolation: bundle v3 and bundle v4 each ship a same-named ``runner/scheme_d_runner.py`` and
``runner/acceptance.py``.  ``scheme_d_runner.py`` (frozen, not ours to edit) imports its acceptance module with a
bare ``import acceptance`` after inserting its own directory at the front of ``sys.path``; that import resolves via
``sys.modules`` first, so if v3's layer already cached ``sys.modules["acceptance"]`` / ``["scheme_d_runner"]``, a
naive ``import acceptance`` / ``import scheme_d_runner`` from here would silently receive v3's modules (or vice
versa if v4 runs first).  ``runner()`` below loads both v4 modules under the generic names only for the instant it
takes to execute them (so ``scheme_d_runner.py``'s own ``import acceptance`` resolves to the v4 acceptance module),
then relocates them to private ``sys.modules`` keys and restores whatever was in the generic slots beforehand
(nothing, if v3 has not run yet; v3's modules, if it has).  v3's own cached module reference (held in v3's
``shard_layer.runner._RUNNER['module']``) is a direct object reference and is unaffected by this either way.  Not
thread-safe (matches the rest of this layer: concurrency is via separate OS processes, never threads).

Storage, exclusivity, resume, audit and merge: identical to the v3 layer.  See that file's module docstring for the
full description of ``RUN_MANIFEST.json``, ``shards/*``, ``events/*``, ``provenance/*`` and ``merged/*``.

Commands: init | work | status | audit | merge.  Production needs ``--authorization`` (a validation authorization
that also carries ``shard_layer_sha256`` and ``shard_worlds``, and whose ``bundle_sha256`` matches *this* v4
bundle -- a v3 pilot/validation authorization is refused by construction, since its ``bundle_sha256`` differs);
``--fixture`` uses RNG-free arithmetic stand-ins and is non-inferential by construction.

No authorization is minted here and no production run is executed by this work; production entry remains gated on
a separate, future, explicitly v4-bound owner authorization (see ``LAYER_MANIFEST.json``'s ``production_gate``).
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import importlib.util
import itertools
import json
import os
import re
import resource
import shutil
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUNDLE = ROOT / "scripts/m32_scheme_d_validation_bundle_v4_20260922"
LAYER_PATH = Path(__file__).resolve()

SCHEMA_BINDINGS = "m3.2-scheme-d-shard-bindings.v4"
SCHEMA_RUN = "m3.2-scheme-d-shard-run-manifest.v4"
SCHEMA_SHARD = "m3.2-scheme-d-shard-completion.v4"
SCHEMA_MERGE = "m3.2-scheme-d-shard-merge-manifest.v4"
SCHEMA_PROVENANCE = "m3.2-scheme-d-shard-provenance.v4"
WORLD_SCHEMA = "m3.2-scheme-d-world-receipt.v1"           # world receipt shape is unchanged by the pre-RNG correction
FILE_RE = re.compile(r"^shard_(\d{6})\.(receipts\.jsonl\.partial|receipts\.jsonl|complete\.json|lock)$")

# Explicit exact hash binding, verified before either frozen v4 module is ever imported (see ``runner()``).
EXPECTED_BUNDLE_MANIFEST_SHA256 = "a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023"
EXPECTED_RUNNER_SHA256 = "7e4d693bdaffc7cf35370fe3425858ea195fc70eb0c6425c4461836008410548"
EXPECTED_ACCEPTANCE_SHA256 = "a575c07fcef86948c50467dc1f32a7bdba1ea32d78a3897b0e2eb1d4569d7ec1"
EXPECTED_TRUTH_PACKAGE_SHA256 = "a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb"
FREEZE_TAG = "m32-scheme-d-v4-freeze"
FREEZE_COMMIT = "079e8b5c590429aeb0ca5c7b0b261328291c6b2e"


class ShardError(RuntimeError):
    """Fail-closed integrity error; the message starts with a stable reason code."""


# ------------------------------------------------------------------------------------------------ small helpers
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def self_hashed(body: dict) -> dict:
    return {**body, "sha256": sha256_bytes(canon(body).encode())}


def _pretty(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1, default=str) + "\n").encode()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


_TMP_COUNTER = itertools.count()


def host_token() -> str:
    """Filesystem-safe host identity (no dots, so temp-name globs stay unambiguous)."""
    return re.sub(r"[^A-Za-z0-9-]", "-", socket.gethostname())[:64] or "host"


def unique_tmp_name(target_name: str) -> str:
    """``<target>.tmp.<host>.<pid>.<time_ns>.<counter>``: unique across hosts and restarts without any random source."""
    return f"{target_name}.tmp.{host_token()}.{os.getpid()}.{time.time_ns()}.{next(_TMP_COUNTER)}"


def create_exclusive_tmp(path: Path) -> tuple[Path, int]:
    """Create a fresh temp file next to ``path`` with O_EXCL; a name clash (same host+pid+time_ns on another mount
    of the same directory) is retried with the next counter instead of ever opening someone else's file."""
    for _ in range(1000):
        tmp = path.with_name(unique_tmp_name(path.name))
        try:
            return tmp, os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
    raise ShardError(f"tmp_name_exhausted:{path.name}")


def publish_exclusive(path: Path, data: bytes) -> None:
    """Durably write ``data`` to a private exclusive-create temp file, then hard-link it into place: the published name
    only ever refers to a fully written, fsynced inode (never torn), and an existing file is never overwritten."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp, fd = create_exclusive_tmp(path)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise ShardError(f"refuse_overwrite:{path.name}") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
    _fsync_dir(path.parent)


def rename_noreplace(src: Path, dst: Path) -> str:
    """Atomic rename that refuses to replace an existing destination (``renameat2(RENAME_NOREPLACE)``).  Where the
    kernel or filesystem lacks it, fall back to an existence check + rename, which is only safe under the merge lock."""
    import ctypes
    fn = None
    with contextlib.suppress(OSError, AttributeError):
        fn = ctypes.CDLL(None, use_errno=True).renameat2
    if fn is not None:
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        if fn(-100, os.fsencode(src), -100, os.fsencode(dst), 1) == 0:      # AT_FDCWD, RENAME_NOREPLACE
            return "renameat2_noreplace"
        err = ctypes.get_errno()
        if err in (errno.EEXIST, errno.ENOTEMPTY):
            raise ShardError(f"refuse_overwrite:{Path(dst).name}")
        if err not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
            raise OSError(err, os.strerror(err), str(dst))
    if os.path.lexists(dst):
        raise ShardError(f"refuse_overwrite:{Path(dst).name}")
    os.rename(src, dst)
    return "rename_under_lock"


_RUNNER: dict = {}
_V4_RUNNER_KEY = "m32_v4_scheme_d_runner"
_V4_ACCEPTANCE_KEY = "m32_v4_scheme_d_acceptance"
_V4_FIXTURE_KEY = "m32_v4_shard_fixture"


def _load_isolated(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def runner():
    """The pinned bundle-v4 runner module, loaded under a private ``sys.modules`` key -- never the generic
    ``scheme_d_runner`` / ``acceptance`` names the v3 layer uses -- so both bundle layers can be imported in the
    same process without either shadowing the other.  Hash-verifies the bundle manifest, the frozen runner and the
    frozen acceptance module against the exact freeze-tag (``m32-scheme-d-v4-freeze``) hashes before importing
    either.  Importing constructs no generator (frozen runner docstring; see also
    ``tests/test_m32_shard_layer_v4.py::test_v4_layer_and_fixture_construct_no_random_generator``)."""
    if "module" not in _RUNNER:
        manifest_path = BUNDLE / "BUNDLE_MANIFEST.json"
        runner_path = BUNDLE / "runner" / "scheme_d_runner.py"
        acceptance_path = BUNDLE / "runner" / "acceptance.py"
        for path, expected in ((manifest_path, EXPECTED_BUNDLE_MANIFEST_SHA256),
                                (runner_path, EXPECTED_RUNNER_SHA256),
                                (acceptance_path, EXPECTED_ACCEPTANCE_SHA256)):
            if not path.is_file() or sha256_file(path) != expected:
                raise ShardError(f"v4_hash_binding_mismatch:{path.name}")
        saved = {k: sys.modules.get(k) for k in ("acceptance", "scheme_d_runner")}
        try:
            _load_isolated("acceptance", acceptance_path)                  # pre-seed so scheme_d_runner's bare
            _load_isolated("scheme_d_runner", runner_path)                 # `import acceptance` resolves to this one
        finally:
            sys.modules[_V4_ACCEPTANCE_KEY] = sys.modules.pop("acceptance")
            sys.modules[_V4_RUNNER_KEY] = sys.modules.pop("scheme_d_runner")
            for key, mod in saved.items():
                if mod is not None:
                    sys.modules[key] = mod
                else:
                    sys.modules.pop(key, None)
        _RUNNER["module"] = sys.modules[_V4_RUNNER_KEY]
    return _RUNNER["module"]


def fixture_module():
    """The v4 fixture helper (RNG-free arithmetic stand-ins), loaded under a private key -- never the generic
    ``shard_fixture`` name the v3 layer uses for its own same-named file."""
    if "fixture" not in _RUNNER:
        _RUNNER["fixture"] = _load_isolated(_V4_FIXTURE_KEY, HERE / "shard_fixture.py")
    return _RUNNER["fixture"]


# ------------------------------------------------------------------------------------------------ plan and shards
def normalise_plan(plan) -> list[dict]:
    names, out = set(), []
    for cell in plan:
        worlds = cell.get("worlds")
        if not isinstance(worlds, int) or isinstance(worlds, bool) or worlds <= 0:
            raise ShardError(f"plan_invalid_world_count:{cell.get('name')}")
        if cell["name"] in names:
            raise ShardError(f"plan_duplicate_cell:{cell['name']}")
        names.add(cell["name"])
        out.append(dict(cell))
    if not out:
        raise ShardError("plan_empty")
    return out


def make_shards(plan: list[dict], shard_worlds: int) -> list[dict]:
    """Deterministic partition: contiguous world ranges inside one cell; each world is in exactly one shard."""
    if not isinstance(shard_worlds, int) or isinstance(shard_worlds, bool) or shard_worlds <= 0:
        raise ShardError("shard_worlds_invalid")
    shards, offset = [], 0
    for cell_index, cell in enumerate(plan):
        for lo in range(0, cell["worlds"], shard_worlds):
            hi = min(lo + shard_worlds, cell["worlds"])
            shards.append({"id": len(shards), "cell_index": cell_index, "cell": cell["name"], "lo": lo, "hi": hi,
                           "global_lo": offset + lo, "global_hi": offset + hi})
        offset += cell["worlds"]
    return shards


def shard_of(plan: list[dict], shard_worlds: int, cell_index: int, world: int) -> int:
    """Inverse map (used by tests and status): the id of the shard holding (cell, world)."""
    if not 0 <= world < plan[cell_index]["worlds"]:
        raise ShardError("world_out_of_range")
    before = sum(-(-c["worlds"] // shard_worlds) for c in plan[:cell_index])
    return before + world // shard_worlds


def shard_stem(shard_id: int) -> str:
    return f"shard_{shard_id:06d}"


def shard_paths(out: Path, shard_id: int) -> dict[str, Path]:
    d, stem = Path(out) / "shards", shard_stem(shard_id)
    return {"partial": d / f"{stem}.receipts.jsonl.partial", "receipts": d / f"{stem}.receipts.jsonl",
            "complete": d / f"{stem}.complete.json", "lock": d / f"{stem}.lock"}


# ------------------------------------------------------------------------------------------------ context / bindings
class Context:
    """Everything a worker or the merger needs; ``evaluator(cell, world)`` returns one world receipt dict."""

    def __init__(self, *, mode, plan, shard_worlds, draws, bindings_common, evaluator, bootstrap):
        self.mode = mode
        self.plan = normalise_plan(plan)
        self.shard_worlds, self.draws = shard_worlds, draws
        self.shards = make_shards(self.plan, shard_worlds)
        self.total_worlds = sum(c["worlds"] for c in self.plan)
        self.evaluator, self.bootstrap = evaluator, bootstrap
        self.bindings = {"schema": SCHEMA_BINDINGS, "mode": mode, **bindings_common,
                         "shard_layer_sha256": sha256_file(LAYER_PATH), "plan_sha256": sha256_bytes(canon(self.plan).encode()),
                         "shard_plan_sha256": sha256_bytes(canon(self.shards).encode()), "shard_worlds": shard_worlds,
                         "draws": draws, "worlds": self.total_worlds, "shards": len(self.shards),
                         "master_seed": runner().MASTER_SEED, "statistic_path": "fast"}
        self.evaluations = 0


def expected_tuples(cell: dict, world: int) -> dict:
    """Seed tuples exactly as bundle-v4's ``run_cell`` builds them: a pure function of the cell and the world
    index, dispatched by ``cell["scenario"] is not None`` (not ``cell["kind"]``)."""
    R = runner()
    has_scenario = cell["scenario"] is not None
    w_dgp = cell["scenario"] if has_scenario else cell["dgp"]
    streams = ("data", "membership", "permutation") + (("injection",) if has_scenario else ())
    return {n: [R.MASTER_SEED, cell["phase"], cell["correlation"], w_dgp, world, R.STREAM[n]] for n in streams}


def make_evaluator(registry, draws: int):
    """Per-world evaluation, identical in structure and argument order to bundle-v4's ``run_cell`` loop body.

    Dispatch (base dgp = N1 and injection) is keyed by ``has_scenario = cell["scenario"] is not None``, matching
    ``run_cell`` exactly.  This differs from the v3 shard layer's ``cell["kind"] == "power"`` dispatch, which is
    equivalent to ``has_scenario`` under bundle v3 (every scenario cell there is ``"power"``) but would silently
    skip injection for bundle v4's ``perturbed_null`` (P1/P3) cells if copied verbatim -- see the module docstring.

    ``registry`` is the runner's ``SeedRegistry`` in production (the only RNG construction) and an arithmetic
    stand-in for fixtures.  Runner functions are looked up at call time so fixtures can install hooks."""
    truth_cache: dict = {}

    def evaluate(cell: dict, w: int) -> dict:
        R = runner()
        phase, c = cell["phase"], cell["correlation"]
        has_scenario = cell["scenario"] is not None
        w_dgp = cell["scenario"] if has_scenario else cell["dgp"]
        if cell["name"] not in truth_cache:
            truth_cache[cell["name"]] = R.load_truth(cell["scenario"], cell["dgp"] if not has_scenario else None, c)
        truth = truth_cache[cell["name"]]
        injected = R.V.INJECTIONS[cell["scenario"]] if cell["scenario"] else ()
        tuples = expected_tuples(cell, w)
        world = R.generate_world(1 if has_scenario else cell["dgp"], c, w,
                                 registry.generator(phase, c, w_dgp, w, 0), registry.generator(phase, c, w_dgp, w, 1))
        if has_scenario:
            world = R.apply_injection(world, cell["scenario"], registry.generator(phase, c, w_dgp, w, 3))
        return R.world_receipt(world, registry.generator(phase, c, w_dgp, w, 2), phase, cell["name"], tuples,
                               draws=draws, truth=truth, injected=injected)
    return evaluate


def bootstrap_provider(registry):
    """Mirror of bundle-v4's ``run_validation``: a bootstrap stream (phase 30, stream 4) only for FDR power cells.
    ``acceptance.FDR_SCENARIOS`` already excludes 101/103 (perturbed_null) under bundle v4, so this is unchanged
    from the v3 layer's own logic and matches the frozen ``run_validation`` textually."""
    R = runner()

    def provider(cell: dict):
        if cell["kind"] == "power" and cell["scenario"] in R.acceptance.FDR_SCENARIOS:
            return registry.generator(30, cell["correlation"], cell["scenario"], 0, 4).integers
        return None
    return provider


def _live_environment() -> dict:
    import numpy as np
    return {"python": ".".join(map(str, sys.version_info[:3])), "numpy": np.__version__,
            "openblas_threads": os.environ.get("OPENBLAS_NUM_THREADS")}


def fixture_context(cells=None, shard_worlds: int = 3, draws: int | None = None) -> Context:
    """RNG-free fixture context (arithmetic tokens, real runner code, FastMetric gate opened by the caller)."""
    F = fixture_module()
    R = runner()
    plan = F.fixture_plan(R, cells or F.FIXTURE_CELLS)
    draws = F.FIXTURE_DRAWS if draws is None else draws
    registry = F.StubRegistry(R.MASTER_SEED)
    common = {"bundle_sha256": R.bundle_sha256(), "authorization_sha256": None, "runner_sha256": sha256_file(R.__file__),
              "environment_lock_sha256": sha256_file(BUNDLE / "artifacts/environment_lock.json"),
              "environment": _live_environment(), "fixture_sha256": F.fixture_sha256(), "non_inferential": True}
    return Context(mode="fixture", plan=plan, shard_worlds=shard_worlds, draws=draws, bindings_common=common,
                   evaluator=make_evaluator(registry, draws), bootstrap=bootstrap_provider(F.StubRegistry(R.MASTER_SEED)))


def production_context(authorization_path: Path, shard_worlds: int) -> Context:
    """Validation context.  Fails closed: environment, isolation, bundle, owner authorization (mode ``validation``,
    measured benchmark, this exact shard layer and shard size) and the FastMetric gate must all verify first.  The
    authorization's ``bundle_sha256`` is checked against *this* v4 bundle inside ``authorize()``: a v3 pilot or
    validation authorization (bound to the v3 bundle's different ``bundle_sha256``) is refused by construction."""
    R = runner()
    env = R.assert_environment()
    R.assert_isolation()
    auth = R.authorize(Path(authorization_path), "validation")
    record = json.loads(Path(authorization_path).read_text())
    if (not isinstance(record.get("runtime_estimate_cpu_hours"), (int, float))
            or not isinstance(record.get("fast_benchmark_receipt_sha256"), str)
            or len(record["fast_benchmark_receipt_sha256"]) != 64):
        raise ShardError("validation_requires_measured_fast_benchmark_in_authorization")
    if record.get("shard_layer_sha256") != sha256_file(LAYER_PATH) or record.get("shard_worlds") != shard_worlds:
        raise ShardError("authorization_missing_shard_layer_binding")
    R.assert_equivalence_gate()
    registry = R.SeedRegistry(auth)
    common = {"bundle_sha256": R.bundle_sha256(), "authorization_sha256": sha256_file(authorization_path),
              "runner_sha256": sha256_file(R.__file__),
              "environment_lock_sha256": sha256_file(BUNDLE / "artifacts/environment_lock.json"),
              "environment": {k: env[k] for k in ("python", "numpy", "openblas_threads")}, "non_inferential": False}
    return Context(mode="validation", plan=R.cell_plan(), shard_worlds=shard_worlds, draws=R.B, bindings_common=common,
                   evaluator=make_evaluator(registry, R.B), bootstrap=bootstrap_provider(registry))


# ------------------------------------------------------------------------------------------------ run manifest
def run_manifest_path(out: Path) -> Path:
    return Path(out) / "RUN_MANIFEST.json"


def expected_run_manifest(ctx: Context) -> dict:
    return self_hashed({"schema": SCHEMA_RUN, "bindings": ctx.bindings, "plan": ctx.plan, "shards": ctx.shards})


def init_run(out: Path, ctx: Context) -> dict:
    """Publish the run manifest exclusively, or verify the existing one equals what this context expects."""
    expected, path = expected_run_manifest(ctx), run_manifest_path(out)
    try:
        publish_exclusive(path, _pretty(expected))
    except ShardError:
        pass
    try:
        existing = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ShardError("run_manifest_unreadable") from None
    if canon(existing) != canon(expected):
        old = existing.get("bindings", {}) if isinstance(existing, dict) else {}
        keys = sorted(k for k in set(old) | set(expected["bindings"]) if old.get(k) != expected["bindings"].get(k))
        raise ShardError(f"run_manifest_mismatch:{','.join(keys) or 'plan_or_shards'}")
    return expected


def emit(out: Path, worker, event: str, **fields) -> None:
    """Structured, reason-coded event line (no secrets, no seed material).  ``worker`` is a worker index or, for the
    merger, a stream name."""
    d = Path(out) / "events"
    d.mkdir(parents=True, exist_ok=True)
    label = f"worker_{worker:04d}" if isinstance(worker, int) else str(worker)
    with open(d / f"{label}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(canon({"ts": time.time(), "host": host_token(), "pid": os.getpid(), "event": event, **fields}) + "\n")


def _cpuinfo() -> tuple:
    model, flags = None, []
    with contextlib.suppress(OSError):
        for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
            key, _, value = line.partition(":")
            key = key.strip()
            if model is None and key in ("model name", "Model", "Hardware", "cpu model"):
                model = value.strip()
            elif not flags and key in ("flags", "Features"):
                flags = sorted(value.split())
            if model and flags:
                break
    return model, flags


def _blas_identity() -> dict:
    """Build-time and runtime OpenBLAS identity (informational; every probe is best-effort and never raises)."""
    info: dict = {"build": None, "runtime_library": None, "runtime_library_sha256": None, "runtime_corename": None,
                  "runtime_config": None}
    try:
        import numpy as np
        blas = np.show_config(mode="dicts")["Build Dependencies"]["blas"]
        info["build"] = {k: blas.get(k) for k in ("name", "version", "openblas configuration")}
    except Exception:                                                  # noqa: BLE001 - informational only
        pass
    try:
        import ctypes
        libs = sorted({line.split()[-1] for line in Path("/proc/self/maps").read_text().splitlines() if "openblas" in line})
        if libs:
            info["runtime_library"] = Path(libs[0]).name
            info["runtime_library_sha256"] = sha256_file(libs[0])
            lib = ctypes.CDLL(libs[0])
            for field, names in (("runtime_corename", ("scipy_openblas_get_corename64_", "scipy_openblas_get_corename",
                                                       "openblas_get_corename64_", "openblas_get_corename")),
                                 ("runtime_config", ("scipy_openblas_get_config64_", "scipy_openblas_get_config",
                                                     "openblas_get_config64_", "openblas_get_config"))):
                for name in names:
                    fn = getattr(lib, name, None)
                    if fn is not None:
                        fn.restype = ctypes.c_char_p
                        info[field] = fn().decode()
                        break
    except Exception:                                                  # noqa: BLE001 - informational only
        pass
    return info


def collect_provenance(environment_lock_sha256: str | None) -> dict:
    """Informational host / CPU / BLAS record.  Never part of a shard binding or of any deterministic artifact; the
    ``hardware_identity`` (host name excluded) tells the merger whether the run mixed different numeric environments."""
    import platform
    model, flags = _cpuinfo()
    try:
        from numpy._core._multiarray_umath import __cpu_features__ as features
        numpy_features = sorted(k for k, v in features.items() if v)
    except Exception:                                                  # noqa: BLE001 - informational only
        numpy_features = None
    blas = _blas_identity()
    env = {**_live_environment(), "openblas_coretype": os.environ.get("OPENBLAS_CORETYPE")}
    hardware = {"machine": platform.machine(), "cpu_model": model, "cpu_flags_sha256": sha256_bytes(canon(flags).encode()),
                "numpy_cpu_features": numpy_features, "blas_runtime_corename": blas["runtime_corename"],
                "blas_runtime_library_sha256": blas["runtime_library_sha256"], **env}
    return self_hashed({"schema": SCHEMA_PROVENANCE, "informational": True, "hostname": socket.gethostname(),
                        "environment_lock_sha256": environment_lock_sha256, "cpu_model": model, "cpu_flags": flags,
                        "numpy_cpu_features": numpy_features, "blas": blas, "environment": env,
                        "hardware_identity": sha256_bytes(canon(hardware).encode())})


def publish_provenance(out: Path, ctx: "Context") -> dict | None:
    """Best-effort: one exclusive ``provenance/prov_<sha16>.json`` per distinct record.  Provenance is informational, so
    a failure is reported as an event and never blocks or changes any output."""
    try:
        record = collect_provenance(ctx.bindings.get("environment_lock_sha256"))
        with contextlib.suppress(ShardError):
            publish_exclusive(Path(out) / "provenance" / f"prov_{record['sha256'][:16]}.json", _pretty(record))
        return record
    except Exception as exc:                                           # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def read_provenance(out: Path) -> tuple[list[dict], int]:
    """(valid provenance records sorted by sha, count of unreadable / self-hash-invalid files)."""
    records, invalid = [], 0
    for path in sorted((Path(out) / "provenance").glob("prov_*.json")) if (Path(out) / "provenance").is_dir() else []:
        try:
            rec = json.loads(path.read_bytes())
            if rec.get("sha256") != sha256_bytes(canon({k: v for k, v in rec.items() if k != "sha256"}).encode()):
                raise ValueError("hash")
            records.append(rec)
        except (OSError, ValueError, AttributeError):
            invalid += 1
    return sorted(records, key=lambda r: r["sha256"]), invalid


# ------------------------------------------------------------------------------------------------ receipts
def encode_receipt(receipt: dict) -> bytes:
    return (canon(receipt) + "\n").encode()


def check_receipt_line(line: bytes, cell: dict, world: int, ctx: Context) -> dict:
    """Validate one stored world receipt against everything a sequential run would have produced for (cell, world)."""
    if not line.endswith(b"\n"):
        raise ShardError("receipt_torn_line")
    try:
        receipt = json.loads(line)
    except ValueError:
        raise ShardError("receipt_unparseable") from None
    if not isinstance(receipt, dict) or encode_receipt(receipt) != line:
        raise ShardError("receipt_not_canonical")
    if receipt.get("sha256") != sha256_bytes(canon({k: v for k, v in receipt.items() if k != "sha256"}).encode()):
        raise ShardError("receipt_internal_hash_mismatch")
    if (receipt.get("schema") != WORLD_SCHEMA or receipt.get("cell") != cell["name"] or receipt.get("phase") != cell["phase"]
            or receipt.get("world") != world or receipt.get("seed_tuples") != expected_tuples(cell, world)):
        raise ShardError(f"receipt_identity_mismatch:{cell['name']}:{world}")
    if "world_refused" not in receipt and (receipt.get("draws") != ctx.draws or receipt.get("statistic_path") != "fast"):
        raise ShardError(f"receipt_protocol_mismatch:{cell['name']}:{world}")
    return receipt


def scan_receipts(data: bytes, cell: dict, lo: int, hi: int, ctx: Context, *, allow_torn_tail: bool):
    """Return (valid_prefix_bytes, [(receipt_sha256, refused)]).  A trailing line without newline is a crash tear
    (dropped when allowed); a complete but invalid line is corruption and always fails closed."""
    pos, entries = 0, []
    while pos < len(data):
        end = data.find(b"\n", pos)
        if end == -1:
            if allow_torn_tail:
                break
            raise ShardError("receipt_torn_line")
        world = lo + len(entries)
        if world >= hi:
            raise ShardError("receipts_beyond_shard_range")
        receipt = check_receipt_line(data[pos:end + 1], cell, world, ctx)
        entries.append((receipt["sha256"], "world_refused" in receipt))
        pos = end + 1
    return pos, entries


# ------------------------------------------------------------------------------------------------ shards
@contextlib.contextmanager
def shard_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise
        yield held
    finally:
        if held:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _completion(out: Path, ctx: Context, shard: dict, data: bytes, entries) -> dict:
    return self_hashed({
        "schema": SCHEMA_SHARD, "shard": shard, "worlds": len(entries), "refused_worlds": sum(1 for _, r in entries if r),
        "receipts_file": shard_paths(out, shard["id"])["receipts"].name, "receipts_bytes": len(data),
        "receipts_sha256": sha256_bytes(data), "world_receipt_sha256": [h for h, _ in entries],
        "bindings": ctx.bindings, "run_manifest_sha256": sha256_file(run_manifest_path(out))})


def read_completion(path: Path) -> dict:
    raw = Path(path).read_bytes()
    try:
        completion = json.loads(raw)
    except ValueError:
        raise ShardError("completion_unparseable") from None
    if not isinstance(completion, dict) or _pretty(completion) != raw:
        raise ShardError("completion_not_canonical")
    if completion.get("sha256") != sha256_bytes(canon({k: v for k, v in completion.items() if k != "sha256"}).encode()):
        raise ShardError("completion_internal_hash_mismatch")
    return completion


def verify_shard(out: Path, ctx: Context, shard: dict) -> tuple[dict, bytes]:
    """Full verification of a completed shard: bindings, checksums and every world receipt."""
    paths = shard_paths(out, shard["id"])
    completion = read_completion(paths["complete"])
    if completion.get("schema") != SCHEMA_SHARD or completion.get("shard") != shard:
        raise ShardError(f"completion_shard_mismatch:{shard['id']}")
    if completion.get("bindings") != ctx.bindings:
        keys = sorted(k for k in set(completion["bindings"]) | set(ctx.bindings)
                      if completion["bindings"].get(k) != ctx.bindings.get(k))
        raise ShardError(f"completion_binding_mismatch:{','.join(keys)}")
    if completion.get("run_manifest_sha256") != sha256_file(run_manifest_path(out)):
        raise ShardError("completion_run_manifest_mismatch")
    if not paths["receipts"].is_file():
        raise ShardError(f"receipts_missing:{shard['id']}")
    data = paths["receipts"].read_bytes()
    if len(data) != completion["receipts_bytes"] or sha256_bytes(data) != completion["receipts_sha256"]:
        raise ShardError(f"receipts_checksum_mismatch:{shard['id']}")
    cell = ctx.plan[shard["cell_index"]]
    valid, entries = scan_receipts(data, cell, shard["lo"], shard["hi"], ctx, allow_torn_tail=False)
    if (len(entries) != shard["hi"] - shard["lo"] or [h for h, _ in entries] != completion["world_receipt_sha256"]
            or completion["worlds"] != len(entries) or completion["refused_worlds"] != sum(1 for _, r in entries if r)):
        raise ShardError(f"receipts_world_set_mismatch:{shard['id']}")
    return completion, data


def _finalise(out: Path, ctx: Context, shard: dict) -> str:
    """partial -> receipts (exclusive link) -> completion (exclusive) -> drop partial.  Every step is crash-safe."""
    paths, cell = shard_paths(out, shard["id"]), ctx.plan[shard["cell_index"]]
    data = paths["partial"].read_bytes()
    _, entries = scan_receipts(data, cell, shard["lo"], shard["hi"], ctx, allow_torn_tail=False)
    if len(entries) != shard["hi"] - shard["lo"]:
        raise ShardError(f"finalise_incomplete:{shard['id']}")
    try:
        os.link(paths["partial"], paths["receipts"])
    except FileExistsError:
        raise ShardError(f"refuse_overwrite:{paths['receipts'].name}") from None
    _fsync_dir(paths["receipts"].parent)
    return _complete_from_receipts(out, ctx, shard)


def _complete_from_receipts(out: Path, ctx: Context, shard: dict) -> str:
    paths, cell = shard_paths(out, shard["id"]), ctx.plan[shard["cell_index"]]
    data = paths["receipts"].read_bytes()
    _, entries = scan_receipts(data, cell, shard["lo"], shard["hi"], ctx, allow_torn_tail=False)
    if len(entries) != shard["hi"] - shard["lo"]:
        raise ShardError(f"finalise_incomplete:{shard['id']}")
    publish_exclusive(paths["complete"], _pretty(_completion(out, ctx, shard, data, entries)))
    _drop_identical_partial(paths)
    return "completed"


def _drop_identical_partial(paths: dict) -> None:
    if paths["partial"].exists():
        if paths["partial"].read_bytes() != paths["receipts"].read_bytes():
            raise ShardError(f"partial_differs_from_final:{paths['partial'].name}")
        paths["partial"].unlink()
        _fsync_dir(paths["partial"].parent)


def run_shard(out: Path, ctx: Context, shard: dict, worker: int = 0) -> dict:
    """Run or resume one shard.  Completed receipts are verified, never rewritten."""
    paths, cell = shard_paths(out, shard["id"]), ctx.plan[shard["cell_index"]]
    with shard_lock(paths["lock"]) as held:
        if not held:
            emit(out, worker, "shard_skipped", shard=shard["id"], reason="locked_by_another_process")
            return {"shard": shard["id"], "status": "locked"}
        for stale in paths["lock"].parent.glob(f"{shard_stem(shard['id'])}.*.tmp.*"):
            stale.unlink()                                             # unpublished temporary files only
        if paths["complete"].exists():
            verify_shard(out, ctx, shard)
            _drop_identical_partial(paths)
            emit(out, worker, "shard_skipped", shard=shard["id"], reason="already_complete_verified")
            return {"shard": shard["id"], "status": "already_complete"}
        if paths["receipts"].exists():
            _complete_from_receipts(out, ctx, shard)
            emit(out, worker, "shard_completed", shard=shard["id"], reason="finalised_receipts_without_completion")
            return {"shard": shard["id"], "status": "completed_from_finalised"}
        done, dropped = 0, 0
        if paths["partial"].exists():
            data = paths["partial"].read_bytes()
            valid, entries = scan_receipts(data, cell, shard["lo"], shard["hi"], ctx, allow_torn_tail=True)
            dropped = len(data) - valid
            if dropped:
                with open(paths["partial"], "r+b") as fh:
                    fh.truncate(valid)
                    fh.flush()
                    os.fsync(fh.fileno())
            done = len(entries)
            emit(out, worker, "shard_resumed", shard=shard["id"], reason="partial_receipts_found", worlds_kept=done,
                 torn_tail_bytes_dropped=dropped)
        started, evaluated = time.perf_counter(), 0
        emit(out, worker, "shard_started", shard=shard["id"], cell=cell["name"], lo=shard["lo"] + done, hi=shard["hi"])
        paths["partial"].parent.mkdir(parents=True, exist_ok=True)
        with open(paths["partial"], "ab") as fh:
            for w in range(shard["lo"] + done, shard["hi"]):
                receipt = ctx.evaluator(cell, w)
                ctx.evaluations += 1
                evaluated += 1
                line = encode_receipt(receipt)
                check_receipt_line(line, cell, w, ctx)                 # never persist a receipt the merger would reject
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
        _finalise(out, ctx, shard)
        emit(out, worker, "shard_completed", shard=shard["id"], reason="all_worlds_written", worlds=shard["hi"] - shard["lo"],
             worlds_evaluated=evaluated, seconds=round(time.perf_counter() - started, 3))
        return {"shard": shard["id"], "status": "completed", "worlds_evaluated": evaluated, "worlds_resumed": done}


def assigned_shards(ctx: Context, worker: int, workers: int, assign: str) -> list[dict]:
    if not (isinstance(workers, int) and workers >= 1 and 0 <= worker < workers):
        raise ShardError("worker_assignment_invalid")
    if assign == "static":
        return [s for s in ctx.shards if s["id"] % workers == worker]
    if assign == "dynamic":                                            # rotated full scan; locks arbitrate
        start = worker * len(ctx.shards) // workers
        return ctx.shards[start:] + ctx.shards[:start]
    raise ShardError("assignment_mode_invalid")


def work(out: Path, ctx: Context, worker: int = 0, workers: int = 1, assign: str = "static") -> dict:
    started = time.perf_counter()
    init_run(out, ctx)
    todo = assigned_shards(ctx, worker, workers, assign)
    prov = publish_provenance(out, ctx) or {}
    emit(out, worker, "worker_started", assign=assign, workers=workers, shards=len(todo),
         provenance_sha256=prov.get("sha256"), hardware_identity=prov.get("hardware_identity"),
         provenance_error=prov.get("error"))
    counts: dict = {}
    for shard in todo:
        status = run_shard(out, ctx, shard, worker)["status"]
        counts[status] = counts.get(status, 0) + 1
    stats = {"worker": worker, "workers": workers, "assign": assign, "shards": counts,
             "worlds_evaluated": ctx.evaluations, "wall_seconds": time.perf_counter() - started,
             "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    emit(out, worker, "worker_finished", **{k: v for k, v in stats.items() if k != "worker"})
    return stats


# ------------------------------------------------------------------------------------------------ audit and merge
def audit(out: Path, ctx: Context) -> dict:
    """Missing / partial / invalid / unexpected files, plus an independent overlap-gap-duplicate check of the world
    ranges the completion receipts *claim*.  ``ok`` only when every shard verifies and the ranges tile the plan."""
    out = Path(out)
    report = {"ok": False, "shards_total": len(ctx.shards), "complete": 0, "missing": [], "partial": [], "invalid": {},
              "unexpected_files": [], "overlaps": [], "gaps": [], "duplicate_worlds": []}
    try:
        existing = json.loads(run_manifest_path(out).read_text())
        if canon(existing) != canon(expected_run_manifest(ctx)):
            report["invalid"]["run_manifest"] = "run_manifest_mismatch"
    except (OSError, ValueError):
        report["invalid"]["run_manifest"] = "run_manifest_missing_or_unreadable"
        return report
    known = {s["id"] for s in ctx.shards}
    present: dict[int, set] = {}
    shard_dir = out / "shards"
    for path in sorted(shard_dir.iterdir()) if shard_dir.is_dir() else []:
        m = FILE_RE.match(path.name)
        if not m or int(m.group(1)) not in known:
            report["unexpected_files"].append(path.name)
        else:
            present.setdefault(int(m.group(1)), set()).add(m.group(2))
    claims = []
    for shard in ctx.shards:
        kinds = present.get(shard["id"], set())
        if "complete.json" not in kinds:
            (report["partial"] if kinds - {"lock"} else report["missing"]).append(shard["id"])
            continue
        if "receipts.jsonl.partial" in kinds:
            report["unexpected_files"].append(shard_paths(out, shard["id"])["partial"].name)
        try:
            claimed = read_completion(shard_paths(out, shard["id"])["complete"])["shard"]
            claims.append((claimed["global_lo"], claimed["global_hi"], shard["id"]))
        except (ShardError, KeyError, TypeError) as exc:
            report["invalid"][str(shard["id"])] = str(exc)
            continue
        try:
            verify_shard(out, ctx, shard)
            report["complete"] += 1
        except ShardError as exc:
            report["invalid"][str(shard["id"])] = str(exc)
    cursor = 0                                                         # tiling of [0, N) by the claimed ranges
    for lo, hi, sid in sorted(claims):
        if lo < cursor:
            report["overlaps"].append({"shard": sid, "from": lo, "to": min(hi, cursor)})
        elif lo > cursor:
            report["gaps"].append({"from": cursor, "to": lo})
        cursor = max(cursor, hi)
    if cursor < ctx.total_worlds:
        report["gaps"].append({"from": cursor, "to": ctx.total_worlds})
    counts: dict = {}
    for lo, hi, _ in claims:
        for g in range(lo, hi):
            counts[g] = counts.get(g, 0) + 1
    report["duplicate_worlds"] = sorted(g for g, n in counts.items() if n > 1)[:100]
    report["ok"] = (report["complete"] == len(ctx.shards) and not (report["missing"] or report["partial"]
                    or report["invalid"] or report["unexpected_files"] or report["overlaps"] or report["gaps"]
                    or report["duplicate_worlds"]))
    return report


class _StageFile:
    """Staged output file: created exclusively, fsynced on a clean close (durable before any publish)."""

    def __init__(self, directory: Path, name: str):
        self.name = name
        self.fh = os.fdopen(os.open(Path(directory) / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), "wb")

    def write(self, data: bytes) -> None:
        self.fh.write(data)

    def close(self, ok: bool) -> None:
        try:
            if ok:
                self.fh.flush()
                os.fsync(self.fh.fileno())
        finally:
            self.fh.close()


class _CompareFile:
    """Read side of a crash-recovery finalise: an already published file must equal what the shards regenerate."""

    def __init__(self, directory: Path, name: str):
        self.name = name
        try:
            self.fh = open(Path(directory) / name, "rb")
        except OSError:
            raise ShardError(f"merged_content_mismatch:{name}:missing") from None

    def write(self, data: bytes) -> None:
        if self.fh.read(len(data)) != data:
            raise ShardError(f"merged_content_mismatch:{self.name}")

    def close(self, ok: bool) -> None:
        try:
            if ok and self.fh.read(1):
                raise ShardError(f"merged_content_mismatch:{self.name}:trailing_bytes")
        finally:
            self.fh.close()


@contextlib.contextmanager
def _sink_file(factory, directory: Path, name: str):
    f = factory(directory, name)
    try:
        yield f
    except BaseException:
        f.close(False)
        raise
    f.close(True)


def _merge_stream(out: Path, ctx: Context, factory, directory: Path) -> dict:
    """Deterministic merge content (plan order, shard order, world order), streamed through ``factory`` files."""
    R = runner()
    cells, cell_files, completion_hashes = {}, {}, {}
    for cell_index, cell in enumerate(ctx.plan):
        name = f"{cell['name'].replace('/', '_')}.receipts.jsonl"
        summaries, digest = [], hashlib.sha256()
        with _sink_file(factory, directory, name) as fh:
            for shard in (s for s in ctx.shards if s["cell_index"] == cell_index):
                completion, data = verify_shard(out, ctx, shard)
                completion_hashes[str(shard["id"])] = sha256_file(shard_paths(out, shard["id"])["complete"])
                fh.write(data)
                digest.update(data)
                summaries += [R.world_summary(json.loads(line)) for line in data.split(b"\n")[:-1]]
        if len(summaries) != cell["worlds"]:
            raise ShardError(f"merge_world_count_mismatch:{cell['name']}")
        cell_files[cell["name"]] = {"file": name, "sha256": digest.hexdigest(), "worlds": len(summaries)}
        cells[cell["name"]] = R.acceptance.evaluate_cell(cell["kind"], cell["scenario"], summaries, ctx.bootstrap(cell))
    overall = R.acceptance.overall(cells)
    result_bytes = (json.dumps({"overall": overall, "cells": cells}, sort_keys=True, default=str) + "\n").encode()
    with _sink_file(factory, directory, "run_result.json") as fh:
        fh.write(result_bytes)
    return {"overall": overall, "cell_files": cell_files, "completion_hashes": completion_hashes,
            "result_sha256": sha256_bytes(result_bytes), "files": sorted([v["file"] for v in cell_files.values()] + ["run_result.json"])}


def _merge_manifest(out: Path, ctx: Context, content: dict, informational: dict) -> dict:
    """``deterministic_sha256`` covers everything except the informational block (host, CPU, BLAS, rename primitive)."""
    body = {"schema": SCHEMA_MERGE, "bindings": ctx.bindings, "run_manifest_sha256": sha256_file(run_manifest_path(out)),
            "non_inferential": bool(ctx.bindings.get("non_inferential")), "cells": content["cell_files"],
            "run_result_sha256": content["result_sha256"], "overall": content["overall"],
            "shard_completion_sha256": content["completion_hashes"], "shards": len(ctx.shards), "worlds": ctx.total_worlds}
    return self_hashed({**body, "deterministic_sha256": sha256_bytes(canon(body).encode()), "informational": informational})


def _check_published(final: Path, content: dict) -> None:
    """The published directory must hold exactly the merged files, with the hashes just computed."""
    if sorted(p.name for p in final.iterdir() if p.name != "MERGE_MANIFEST.json") != content["files"]:
        raise ShardError("merged_content_mismatch:file_set")
    for meta in content["cell_files"].values():
        if sha256_file(final / meta["file"]) != meta["sha256"]:
            raise ShardError(f"merged_content_mismatch:{meta['file']}")
    if sha256_file(final / "run_result.json") != content["result_sha256"]:
        raise ShardError("merged_content_mismatch:run_result.json")


def merge(out: Path, ctx: Context) -> dict:
    """Deterministic merge.  One merger at a time (``merge.lock``; another merger exits with
    ``merge_locked_by_another_process`` and touches nothing).  The merger writes a private staging directory, fsyncs every
    staged file and the directory, publishes it with a no-replace atomic rename, fsyncs the published directory and its
    parent, verifies the published files, and only then publishes ``MERGE_MANIFEST.json`` (the commit marker).  A
    ``merged/`` without a manifest (crash before the marker) is re-verified against the shards and finalised, never
    overwritten; a finalised ``merged/`` is never touched."""
    out = Path(out)
    with shard_lock(out / "merge.lock") as held:
        if not held:
            emit(out, "merge", "merge_skipped", reason="locked_by_another_process")
            raise ShardError("merge_locked_by_another_process")
        return _merge_locked(out, ctx)


def _merge_locked(out: Path, ctx: Context) -> dict:
    final = out / "merged"
    if final.exists() and ((final / "MERGE_MANIFEST.json").exists() or not final.is_dir()):
        raise ShardError("refuse_overwrite:merged")
    report = audit(out, ctx)
    if not report["ok"]:
        raise ShardError("merge_refused_audit_failed:" + canon({k: v for k, v in report.items()
                                                                 if k not in ("ok", "shards_total", "complete") and v}))
    merger = collect_provenance(ctx.bindings.get("environment_lock_sha256"))
    workers, invalid = read_provenance(out)
    stale = sorted(p.name for p in out.iterdir() if p.name.startswith(("merged.staging.", "merged.partial")))
    hardware = sorted({r["hardware_identity"] for r in workers})
    informational = {"merger": merger, "worker_provenance": [{"sha256": r["sha256"], "hostname": r["hostname"],
                                                              "hardware_identity": r["hardware_identity"]} for r in workers],
                     "worker_hardware_identities_distinct": len(hardware), "invalid_provenance_files": invalid,
                     "stale_staging_left_untouched": stale}
    if final.exists():                                                 # crash between the publish rename and the marker
        content = _merge_stream(out, ctx, _CompareFile, final)
        _check_published(final, content)
        informational["publish"] = "finalised_existing_unmarked_merged_dir"
    else:
        staging = out / f"merged.staging.{host_token()}.{os.getpid()}.{time.time_ns()}.{next(_TMP_COUNTER)}"
        os.mkdir(staging)                                              # exclusive: private to this merger
        renamed = False
        try:
            content = _merge_stream(out, ctx, _StageFile, staging)
            _fsync_dir(staging)
            informational["publish"] = rename_noreplace(staging, final)
            renamed = True
        finally:
            if not renamed:
                shutil.rmtree(staging, ignore_errors=True)             # our own staging only
        _fsync_dir(final)
        _fsync_dir(out)
        _check_published(final, content)
    manifest = _merge_manifest(out, ctx, content, informational)
    publish_exclusive(final / "MERGE_MANIFEST.json", _pretty(manifest))
    _fsync_dir(final)
    emit(out, "merge", "merge_published", shards=len(ctx.shards), worlds=ctx.total_worlds, publish=informational["publish"],
         stale_staging=len(stale), worker_hardware_identities=len(hardware))
    return manifest


# ------------------------------------------------------------------------------------------------ command line
def _parse_cells(text: str | None):
    if not text:
        return None
    return tuple((name, int(worlds)) for name, worlds in (part.split(":") for part in text.split(",")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=("init", "work", "status", "audit", "merge"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--fixture", action="store_true", help="RNG-free non-inferential fixture (never a validation)")
    ap.add_argument("--fixture-cells", help="e.g. N1/C0:8,P1/C0:4 (fixture only)")
    ap.add_argument("--draws", type=int, help="fixture only; production always uses the protocol draw count")
    ap.add_argument("--authorization", type=Path)
    ap.add_argument("--shard-worlds", type=int, required=True)
    ap.add_argument("--worker", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--assign", choices=("static", "dynamic"), default="static")
    ap.add_argument("--crash-after-worlds", type=int, help="fixture only: hard-exit after K evaluations (crash test)")
    args = ap.parse_args(argv)
    try:
        if args.fixture:
            os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
            F = fixture_module()
            R = runner()
            with F.installed(R):
                ctx = fixture_context(_parse_cells(args.fixture_cells), args.shard_worlds, args.draws)
                if args.crash_after_worlds is not None:
                    inner, calls = ctx.evaluator, [0]

                    def crashing(cell, w):
                        if calls[0] >= args.crash_after_worlds:
                            os._exit(137)
                        calls[0] += 1
                        return inner(cell, w)
                    ctx.evaluator = crashing
                return _dispatch(args, ctx)
        if args.crash_after_worlds is not None or args.draws is not None or args.fixture_cells:
            raise ShardError("fixture_only_option_in_production")
        if not args.authorization:
            raise ShardError("authorization_required_for_production")
        R = runner()
        ctx = production_context(args.authorization, args.shard_worlds)
        with R.V.blocked_network():
            return _dispatch(args, ctx)
    except Exception as exc:                                            # noqa: BLE001 - fail closed with a reason code
        print(f"fail closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _dispatch(args, ctx: Context) -> int:
    if args.command == "init":
        init_run(args.out, ctx)
        result = {"run_manifest_sha256": sha256_file(run_manifest_path(args.out)), "shards": len(ctx.shards)}
    elif args.command == "work":
        result = work(args.out, ctx, args.worker, args.workers, args.assign)
    elif args.command in ("status", "audit"):
        result = audit(args.out, ctx)
    else:
        result = merge(args.out, ctx)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if args.command not in ("audit",) or result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
