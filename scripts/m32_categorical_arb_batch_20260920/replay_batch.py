#!/usr/bin/env python3
"""Independent-process 320-bit replay of every accepted batch kernel."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import resource
import time
from pathlib import Path

from flint import arb, ctx

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "certificate.json"
OUTPUT = HERE / "replay.json"
MANIFEST = HERE / "manifest.sha256"


def load_integrator():
    path = HERE / "replay_integrator.py"
    spec = importlib.util.spec_from_file_location("independent_replay_integrator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, path


def ball(record: dict) -> arb:
    lo = record["lower_dyadic"]
    hi = record["upper_dyadic"]
    left = arb((int(lo["mantissa"]), int(lo["exponent"])))
    right = arb((int(hi["mantissa"]), int(hi["exponent"])))
    return arb((left+right)/2, (right-left)/2)


def overlaps(x: arb, record: dict) -> bool:
    prior = ball(record)
    return not (x.upper() < prior.lower() or x.lower() > prior.upper())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ctx.prec = 320
    impl, impl_path = load_integrator()
    # Reassert after module import; no certificate configuration is trusted.
    impl.ctx.prec = 320
    source = json.loads(SOURCE.read_text())
    accepted = {k: v for k, v in source["kernels"].items()
                if v["classification"] != "refused"}
    thresholds = (impl.frozen(impl.STATE_TEXT)[0], impl.frozen(impl.LINKED_TEXT)[0])
    engine = impl.BatchIntegrator(thresholds, impl.A("2e-15"))
    specs = {k: v["spec"] for k, v in accepted.items()}
    started = time.perf_counter()
    values = {}
    values.update(engine.c3_batch({k: v for k, v in specs.items() if v["correlation"] == 3}))
    values.update(engine.c4_batch({k: v for k, v in specs.items() if v["correlation"] == 4}))
    rows = []
    for kid, prior in sorted(accepted.items()):
        classification, sign, lift, winners, reason = impl.classify(values[kid], prior["spec"], thresholds)
        expected = (prior["strict_argmax"]["hit"], prior["strict_argmax"]["miss"],
                    prior["strict_argmax"]["population"])
        pgf_overlap = all(overlaps(value, prior[name]) for value, name in zip(
            values[kid][:3], ("population_pgf", "hit_pgf", "miss_pgf")))
        lift_overlap = lift is not None and overlaps(lift, prior["signed_lift_enclosure"])
        ok = (classification == prior["classification"] and sign == prior["sign"]
              and winners == expected and pgf_overlap and lift_overlap)
        rows.append({"kernel_id": kid, "result": "PASS" if ok else "FAIL_CLOSED",
                     "classification": classification, "sign": sign, "reason": reason,
                     "strict_argmax": list(winners), "pgf_enclosures_overlap": pgf_overlap,
                     "signed_lift_enclosures_overlap": lift_overlap,
                     "signed_lift_enclosure": impl.interval(lift) if lift is not None else None})
    complete = len(rows) == source["counts"]["kernels_certified"]
    passed = complete and all(row["result"] == "PASS" for row in rows)
    payload = {
        "schema": "m3.2-categorical-arb-batch-replay.v1",
        "result": "PASS" if passed else "FAIL_CLOSED",
        "independent_process_and_artifact": True,
        "higher_precision": True,
        "precision_bits": 320,
        "absolute_tolerance": "2e-15",
        "relative_tolerance": "2e-15",
        "kernels_replayed": len(rows),
        "all_accepted_classifications_replayed": complete,
        "rng_constructed": False,
        "simulation_worlds_constructed": 0,
        "runtime_seconds": time.perf_counter()-started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "source_certificate_sha256": sha(SOURCE),
        "implementation_sha256": sha(Path(__file__)),
        "integrator_implementation_sha256": sha(impl_path),
        "rows": rows,
    }
    OUTPUT.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"))+"\n")
    files = ("certificate.json", "certify_batch.py", "record_refusal.py",
             "replay.json", "replay_batch.py", "replay_integrator.py")
    MANIFEST.write_text("".join(f"{sha(HERE/name)}  {name}\n" for name in files))
    print(json.dumps({k: payload[k] for k in ("result", "kernels_replayed",
                                               "all_accepted_classifications_replayed",
                                               "runtime_seconds", "peak_rss_kib")}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
