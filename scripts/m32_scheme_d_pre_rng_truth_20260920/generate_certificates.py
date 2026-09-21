#!/usr/bin/env python3
"""Generate deterministic Scheme D pre-RNG truth certificates.

This program intentionally uses only the Python standard library.  It does not
construct an RNG, generate a world, integrate numerically, or optimize.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "certificates"
PROTOCOL = "docs/superpowers/specs/2026-09-20-m32-scheme-d-actual-statistic-validation-protocol-pre-rng-correction.md"
SOURCES = (
    PROTOCOL,
    "trader/cognition/m32_scheme_d_validation.py",
    "trader/cognition/m32_search.py",
)
IMPLEMENTATIONS = (
    "scripts/m32_scheme_d_pre_rng_truth_20260920/generate_certificates.py",
    "scripts/m32_scheme_d_pre_rng_truth_20260920/verify_certificates.py",
)
HYPOTHESES = 384
DELTA_TRUTH = "1e-10"
MAX_INTERVAL_RADIUS = "1e-12"
SCENARIO_ROLES = {
    1: "calibration", 2: "power", 3: "calibration", 4: "power",
    5: "power", 6: "power", 7: "sensitivity",
}

INJECTIONS = {
    1: (0,),
    2: (128,),
    3: (320,),
    4: (0, 33, 130, 163, 260, 293, 326, 359),
    5: tuple(x for c in range(4) for x in
             (c, c + 32, 128 + c, 160 + c, 256 + c, 288 + c, 320 + c, 352 + c)),
    6: tuple(range(0, 8)) + tuple(range(136, 144)) + tuple(range(272, 280)) + tuple(range(344, 352)),
    7: (tuple(range(0, 24)) + tuple(range(136, 160)) + tuple(range(272, 288))
        + tuple(range(288, 296)) + tuple(range(320, 336)) + tuple(range(344, 352))),
}
OR = {1: Fraction(3), 3: Fraction(3), 4: Fraction(2), 5: Fraction(2),
      6: Fraction(2), 7: Fraction(3, 2)}


def canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hypothesis(h: int) -> dict:
    if h < 128:
        kind, label, threshold = "state", "case_kind", "0.6744897501960817"
    elif h < 256:
        kind, label, threshold = "state", "persistence", "0.6744897501960817"
    elif h < 320:
        kind, label, threshold = "transition", "continuation", "0.8416212335729143"
    else:
        kind, label, threshold = "sequence", "continuation", "0.8416212335729143"
    cluster = h % 32
    return {"hypothesis": h, "kind": kind, "label": label, "threshold": threshold,
            "cluster": cluster, "sector": "A" if cluster < 16 else "B"}


def fstr(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def bernoulli_count(probabilities: list[Fraction]) -> list[Fraction]:
    dist = [Fraction(1)]
    for p in probabilities:
        nxt = [Fraction(0)] * (len(dist) + 1)
        for n, mass in enumerate(dist):
            nxt[n] += mass * (1 - p)
            nxt[n + 1] += mass * p
        dist = nxt
    return dist


def class_probs_after_or(count: int, odds_ratio: Fraction) -> tuple[Fraction, ...]:
    survival = (Fraction(10, 9 + odds_ratio)) ** count
    return (Fraction(13, 20) * survival, Fraction(1, 4) * survival,
            1 - Fraction(9, 10) * survival)


def mixture_probs(probabilities: list[Fraction], odds_ratio: Fraction,
                  forced: int = 0) -> tuple[Fraction, ...]:
    result = [Fraction(0), Fraction(0), Fraction(0)]
    for count, mass in enumerate(bernoulli_count(probabilities)):
        probs = class_probs_after_or(count + forced, odds_ratio)
        for cls in range(3):
            result[cls] += mass * probs[cls]
    return tuple(result)


def c0_categorical_target_witness(scenario: int, h: int) -> dict:
    targets = [x for x in INJECTIONS[scenario] if hypothesis(x)["label"] != "persistence"]
    q = Fraction(1, 4) if h < 256 else Fraction(1, 5)
    remaining = [Fraction(1, 4) if x < 256 else Fraction(1, 5)
                 for x in targets if x != h]
    hit = mixture_probs(remaining, OR[scenario], forced=1)
    miss = mixture_probs(remaining, OR[scenario], forced=0)
    population = tuple(q * hit[c] + (1 - q) * miss[c] for c in range(3))
    majorities = [max(range(3), key=lambda c: probs[c]) for probs in (hit, miss, population)]
    if len(set(majorities)) != 1:
        raise AssertionError(f"unexpected C0 majority split P{scenario} h{h}: {majorities}")
    return {
        "rule": "C0_TARGET_EXACT_RATIONAL_MAJORITY",
        "membership_probability": fstr(q),
        "remaining_target_probabilities": [fstr(x) for x in remaining],
        "odds_ratio": fstr(OR[scenario]),
        "conditional_hit_class_probabilities": [fstr(x) for x in hit],
        "conditional_miss_class_probabilities": [fstr(x) for x in miss],
        "population_class_probabilities": [fstr(x) for x in population],
        "majorities_hit_miss_population": majorities,
        "macro_f1_lift": "0/1",
    }


PROOF_LIBRARY = {
    "P1_P3_SINGLE_OR_MAJORITY": {
        "type": "exact_algebra",
        "statement": "A=E[(5/6)^K|G] with K in {0,1}, hence A in [5/6,1]. Class 0 minus class 2 is (31/20)A-1 >= 7/24 > 0, and class 0 exceeds class 1 by (2/5)A > 0. Matched, nonmatched and population majorities are therefore all class 0, so the categorical macro-F1 lift is exactly zero.",
    },
    "NO_CATEGORICAL_INJECTION": {
        "type": "exact_independence",
        "statement": "The categorical outcome is independent of every membership and has probabilities (13/20,1/4,1/10). Every conditional and population majority is class 0, so the lift is exactly zero.",
    },
    "OUTCOME_COLUMN_INDEPENDENCE": {
        "type": "exact_independence",
        "statement": "No persistence injection is applied. The continuous persistence outcome is independent of all membership latents; conditional and population medians coincide exactly.",
    },
    "C0_NON_TARGET_INDEPENDENCE": {
        "type": "exact_independence",
        "statement": "Under C0, a non-target membership is independent of the base outcome and every injected target membership. Its conditional outcome law equals the population law exactly.",
    },
    "C0_TARGET_EXACT_RATIONAL_MAJORITY": {
        "type": "exact_rational",
        "statement": "Sequential realized-label switching composes survival factors. Exact Bernoulli convolution gives the recorded hit, miss and population class probabilities; all three have the same unique majority (class 0 or class 2 as recorded), so both classifiers are identical and macro-F1 lift is exactly zero.",
    },
    "PERSISTENCE_STRICT_MONOTONE_SHIFT": {
        "type": "exact_sign_proof",
        "statement": "Write Y=X+a*S with a>0, X independent and possessing an everywhere-positive continuous density, and S the sum of target indicators. If h is itself a target, conditioning forces that Bernoulli term from its population mixture to one. For every other target, conditional on the scalar Gaussian latent Z_h=z, its Gaussian mean is rho_ht*z with fixed joint conditional covariance. If all non-self rho_ht are positive (negative), S|Z_h=z is coordinatewise stochastically increasing (decreasing) in z, strictly through the self term or at least one correlated target. Truncating Z_h above its finite threshold therefore makes S, then Y, strictly first-order stochastically larger (smaller) than its population law. The unique conditional median is strictly above (below) the unique population median. Division by MAD+1>0 preserves the sign.",
    },
    "ORTHANT_CERTIFICATE_MISSING": {
        "type": "fail_closed",
        "statement": "The correlated categorical estimand depends on Gaussian threshold-event probabilities. No directed-rounding orthant enclosure and propagated signed-metric interval of radius <=1e-12 is supplied; classification is refused under corrected protocol section 4.4.",
    },
    "OPPOSED_SECTOR_CANCELLATION_UNRESOLVED": {
        "type": "fail_closed",
        "statement": "P7/C4 has persistence targets in both sectors. Conditioning shifts same-sector and opposite-sector target indicators in opposed directions, so the coordinatewise monotonicity proof does not apply. No certified orthant-derived interval separates the signed median metric from zero; classification is refused.",
    },
}


def record(scenario: int, correlation: int, h: int) -> dict:
    meta = hypothesis(h)
    label = meta["label"]
    targets = set(INJECTIONS[scenario])
    persistence_targets = {x for x in targets if hypothesis(x)["label"] == "persistence"}
    categorical_targets = {x for x in targets if hypothesis(x)["label"] != "persistence"}
    witness = None

    if label == "persistence":
        if not persistence_targets:
            classification, sign, proof = "true_null", None, "OUTCOME_COLUMN_INDEPENDENCE"
        elif correlation == 0 and h not in persistence_targets:
            classification, sign, proof = "true_null", None, "C0_NON_TARGET_INDEPENDENCE"
        elif scenario == 7 and correlation == 4:
            classification, sign, proof = "unclassifiable", None, "OPPOSED_SECTOR_CANCELLATION_UNRESOLVED"
        else:
            if correlation == 4:
                target_sectors = {hypothesis(x)["sector"] for x in persistence_targets}
                if len(target_sectors) != 1:
                    raise AssertionError("mixed-sector persistence escaped refusal")
                sign = 1 if meta["sector"] in target_sectors else -1
                correlations = ["1/1" if x == h else ("3/5" if sign > 0 else "-3/10")
                                for x in sorted(persistence_targets)]
            elif correlation == 0:
                sign = 1
                correlations = ["1/1" if x == h else "0/1" for x in sorted(persistence_targets)]
            elif correlation == 1:
                sign = 1
                correlations = ["1/1" if x == h else "3/10" for x in sorted(persistence_targets)]
            elif correlation == 2:
                sign = 1
                correlations = ["1/1" if x == h else "7/10" for x in sorted(persistence_targets)]
            else:
                sign = 1
                correlations = ["1/1" if x == h else
                                ("7/10" if hypothesis(x)["cluster"] == meta["cluster"] else "1/10")
                                for x in sorted(persistence_targets)]
            classification, proof = "analytic_non_null", "PERSISTENCE_STRICT_MONOTONE_SHIFT"
            witness = {"target_hypotheses": sorted(persistence_targets),
                       "conditioning_to_target_correlations": correlations,
                       "shift_constant_positive": True,
                       "base_density_everywhere_positive": True}
    else:
        if scenario in (1, 3):
            classification, sign, proof = "true_null", None, "P1_P3_SINGLE_OR_MAJORITY"
        elif scenario == 2:
            classification, sign, proof = "true_null", None, "NO_CATEGORICAL_INJECTION"
        elif correlation > 0:
            classification, sign, proof = "unclassifiable", None, "ORTHANT_CERTIFICATE_MISSING"
        elif h not in categorical_targets:
            classification, sign, proof = "true_null", None, "C0_NON_TARGET_INDEPENDENCE"
        else:
            classification, sign, proof = "true_null", None, "C0_TARGET_EXACT_RATIONAL_MAJORITY"
            witness = c0_categorical_target_witness(scenario, h)

    return {**meta, "classification": classification, "sign": sign,
            "proof_id": proof, "witness": witness}


def main() -> None:
    (OUT / "tables").mkdir(parents=True, exist_ok=True)
    source_hashes = {name: sha256(ROOT / name) for name in SOURCES}
    table_hashes = {}
    summaries = []
    for scenario in range(1, 8):
        for correlation in range(5):
            records = [record(scenario, correlation, h) for h in range(HYPOTHESES)]
            unresolved = [r["hypothesis"] for r in records if r["classification"] == "unclassifiable"]
            payload = {
                "schema": "m3.2-scheme-d-pre-rng-truth-table.v3",
                "scenario": f"P{scenario}",
                "role": SCENARIO_ROLES[scenario],
                "correlation": f"C{correlation}",
                "hypotheses": HYPOTHESES,
                "unresolved_hypotheses": unresolved,
                "records": records,
            }
            relative = f"tables/P{scenario}_C{correlation}.json"
            path = OUT / relative
            path.write_text(canonical(payload), encoding="utf-8")
            table_hashes[relative] = sha256(path)
            counts = Counter((r["classification"], str(r["sign"])) for r in records)
            summaries.append({
                "cell": f"P{scenario}/C{correlation}",
                "role": SCENARIO_ROLES[scenario],
                "unresolved_count": len(unresolved),
                "classification_counts": {f"{k[0]}:{k[1]}": v for k, v in sorted(counts.items())},
            })

    certificate = {
        "schema": "m3.2-scheme-d-pre-rng-proof-certificate.v3",
        "authority": PROTOCOL,
        "scope": "P1-P7 x C0-C4 only",
        "rng_constructed": False,
        "simulation_worlds_constructed": 0,
        "optimization_performed": False,
        "delta_truth": DELTA_TRUTH,
        "maximum_interval_radius": MAX_INTERVAL_RADIUS,
        "certified_numerical_intervals_used": 0,
        "frozen_inputs": {
            "source_sha256": source_hashes,
            "implementation_sha256": {name: sha256(ROOT / name) for name in IMPLEMENTATIONS},
            "family_size": HYPOTHESES,
            "thresholds": {"state": "0.6744897501960817", "linked": "0.8416212335729143"},
            "C0": {"off_diagonal": "0/1"},
            "C1": {"off_diagonal": "3/10", "factor_variance": "3/10", "residual_variance": "7/10"},
            "C2": {"off_diagonal": "7/10", "factor_variance": "7/10", "residual_variance": "3/10"},
            "C3": {"global": "1/10", "cluster": "3/5", "residual": "3/10",
                   "same_cluster_correlation": "7/10", "cross_cluster_correlation": "1/10"},
            "C4": {"systematic": "3/5", "residual": "2/5",
                   "same_sector_correlation": "3/5", "opposite_sector_correlation": "-3/10"},
            "base_categorical_probabilities": ["13/20", "1/4", "1/10"],
            "persistence_shift_scale": "0.47472299235208826",
            "injections": {f"P{k}": list(v) for k, v in INJECTIONS.items()},
        },
        "proof_library": PROOF_LIBRARY,
        "cell_summaries": summaries,
        "table_sha256": table_hashes,
        "freeze_possible": False,
        "freeze_blocker": "16 cells contain at least one fail-closed unclassifiable row",
    }
    cert_path = OUT / "proof_certificate.json"
    cert_path.write_text(canonical(certificate), encoding="utf-8")
    manifest_lines = [f"{sha256(cert_path)}  proof_certificate.json"]
    manifest_lines.extend(f"{digest}  {name}" for name, digest in sorted(table_hashes.items()))
    (OUT / "manifest.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="ascii")
    print(f"generated {len(summaries)} cells, {len(summaries) * HYPOTHESES} rows")


if __name__ == "__main__":
    main()
