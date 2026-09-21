#!/usr/bin/env python3
"""Independent deterministic replay for Scheme D pre-RNG certificates.

The verifier deliberately does not import the generator or trader modules.
It reconstructs the expected partition and exact C0 arithmetic separately.
"""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent / "certificates"
H = 384
PERSISTENCE = set(range(128, 256))
INJECTED = {
    1: {0}, 2: {128}, 3: {320},
    4: {0, 33, 130, 163, 260, 293, 326, 359},
    5: {x for c in range(4) for x in (c, c+32, 128+c, 160+c, 256+c, 288+c, 320+c, 352+c)},
    6: set(range(0, 8)) | set(range(136, 144)) | set(range(272, 280)) | set(range(344, 352)),
    7: set(range(0, 24)) | set(range(136, 160)) | set(range(272, 288)) |
       set(range(288, 296)) | set(range(320, 336)) | set(range(344, 352)),
}
ODDS = {1: Fraction(3), 3: Fraction(3), 4: Fraction(2), 5: Fraction(2),
        6: Fraction(2), 7: Fraction(3, 2)}
ROLES = {1: "calibration", 2: "power", 3: "calibration", 4: "power",
         5: "power", 6: "power", 7: "sensitivity"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frac(text: str) -> Fraction:
    n, d = text.split("/")
    return Fraction(int(n), int(d))


def label(h: int) -> str:
    return "persistence" if h in PERSISTENCE else ("case_kind" if h < 128 else "continuation")


def sector(h: int) -> str:
    return "A" if h % 32 < 16 else "B"


def convolve(ps: list[Fraction]) -> list[Fraction]:
    out = [Fraction(1)]
    for p in ps:
        nxt = [Fraction(0)] * (len(out) + 1)
        for k, mass in enumerate(out):
            nxt[k] += mass * (1-p)
            nxt[k+1] += mass * p
        out = nxt
    return out


def probs(ps: list[Fraction], odds: Fraction, forced: int) -> tuple[Fraction, ...]:
    out = [Fraction(0)] * 3
    for k, mass in enumerate(convolve(ps)):
        a = Fraction(10, 9 + odds) ** (k + forced)
        values = (Fraction(13, 20)*a, Fraction(1, 4)*a, 1-Fraction(9, 10)*a)
        for c in range(3):
            out[c] += mass * values[c]
    return tuple(out)


def expected(s: int, c: int, h: int) -> tuple[str, int | None]:
    ptargets = INJECTED[s] & PERSISTENCE
    ctargets = INJECTED[s] - PERSISTENCE
    if label(h) == "persistence":
        if not ptargets:
            return "true_null", None
        if c == 0 and h not in ptargets:
            return "true_null", None
        if s == 7 and c == 4:
            return "unclassifiable", None
        sign = 1
        if c == 4:
            target_sectors = {sector(x) for x in ptargets}
            assert len(target_sectors) == 1
            sign = 1 if sector(h) in target_sectors else -1
        return "analytic_non_null", sign
    if s in (1, 3):
        return "true_null", None
    if s == 2:
        return "true_null", None
    if c:
        return "unclassifiable", None
    if h not in ctargets:
        return "true_null", None
    return "true_null", None


def replay_p1_p3_majority() -> None:
    """Replay the exact bound instead of trusting a proof-name string."""
    for a in (Fraction(5, 6), Fraction(1)):
        assert Fraction(31, 20) * a - 1 >= Fraction(7, 24)
        assert Fraction(2, 5) * a > 0


def expected_persistence_correlations(s: int, c: int, h: int) -> list[str]:
    targets = sorted(INJECTED[s] & PERSISTENCE)
    if c == 0:
        return ["1/1" if x == h else "0/1" for x in targets]
    if c == 1:
        return ["1/1" if x == h else "3/10" for x in targets]
    if c == 2:
        return ["1/1" if x == h else "7/10" for x in targets]
    if c == 3:
        return ["1/1" if x == h else ("7/10" if x % 32 == h % 32 else "1/10")
                for x in targets]
    sign = 1 if sector(h) == sector(targets[0]) else -1
    return ["1/1" if x == h else ("3/5" if sign > 0 else "-3/10") for x in targets]


def replay_c0_witness(s: int, h: int, witness: dict) -> None:
    targets = sorted(x for x in INJECTED[s] if x not in PERSISTENCE)
    q = Fraction(1, 4) if h < 256 else Fraction(1, 5)
    remaining = [Fraction(1, 4) if x < 256 else Fraction(1, 5) for x in targets if x != h]
    hit, miss = probs(remaining, ODDS[s], 1), probs(remaining, ODDS[s], 0)
    pop = tuple(q*hit[i] + (1-q)*miss[i] for i in range(3))
    majorities = [max(range(3), key=lambda i: x[i]) for x in (hit, miss, pop)]
    assert len(set(majorities)) == 1
    assert witness["majorities_hit_miss_population"] == majorities
    assert [frac(x) for x in witness["conditional_hit_class_probabilities"]] == list(hit)
    assert [frac(x) for x in witness["conditional_miss_class_probabilities"]] == list(miss)
    assert [frac(x) for x in witness["population_class_probabilities"]] == list(pop)
    assert frac(witness["macro_f1_lift"]) == 0


def main() -> None:
    certificate = json.loads((BASE / "proof_certificate.json").read_text())
    assert certificate["rng_constructed"] is False
    assert certificate["simulation_worlds_constructed"] == 0
    assert certificate["optimization_performed"] is False
    assert certificate["certified_numerical_intervals_used"] == 0
    for relative, expected_hash in certificate["frozen_inputs"]["source_sha256"].items():
        assert digest(ROOT / relative) == expected_hash, f"source drift: {relative}"
    for relative, expected_hash in certificate["frozen_inputs"]["implementation_sha256"].items():
        assert digest(ROOT / relative) == expected_hash, f"implementation drift: {relative}"

    manifest = {}
    for line in (BASE / "manifest.sha256").read_text().splitlines():
        expected_hash, relative = line.split("  ", 1)
        assert digest(BASE / relative) == expected_hash, f"manifest mismatch: {relative}"
        manifest[relative] = expected_hash
    assert digest(BASE / "proof_certificate.json") == manifest["proof_certificate.json"]

    replay_p1_p3_majority()
    certified, refused, checked = [], [], 0
    for s in range(1, 8):
        for c in range(5):
            relative = f"tables/P{s}_C{c}.json"
            table = json.loads((BASE / relative).read_text())
            assert table["scenario"] == f"P{s}" and table["correlation"] == f"C{c}"
            assert table["role"] == ROLES[s]
            assert len(table["records"]) == H
            assert [r["hypothesis"] for r in table["records"]] == list(range(H))
            unresolved = []
            for h, row in enumerate(table["records"]):
                exp = expected(s, c, h)
                got = (row["classification"], row["sign"])
                assert got == exp, f"classification mismatch P{s}/C{c}/h{h}: {got} != {exp}"
                categorical_target = label(h) != "persistence" and h in (INJECTED[s] - PERSISTENCE)
                if c == 0 and s >= 4 and categorical_target:
                    replay_c0_witness(s, h, row["witness"])
                if label(h) == "persistence" and row["classification"] == "analytic_non_null":
                    assert row["witness"]["target_hypotheses"] == sorted(INJECTED[s] & PERSISTENCE)
                    assert row["witness"]["conditioning_to_target_correlations"] == expected_persistence_correlations(s, c, h)
                    assert row["witness"]["shift_constant_positive"] is True
                    assert row["witness"]["base_density_everywhere_positive"] is True
                if row["classification"] == "unclassifiable":
                    unresolved.append(h)
                checked += 1
            assert table["unresolved_hypotheses"] == unresolved
            (certified if not unresolved else refused).append(f"P{s}/C{c}")

    assert len(certified) == 19 and len(refused) == 16 and checked == 13_440
    assert certificate["freeze_possible"] is False
    report = {
        "schema": "m3.2-scheme-d-pre-rng-certificate-replay.v3",
        "result": "PASS",
        "independent_implementation": True,
        "rng_constructed": False,
        "cells_checked": 35,
        "hypothesis_rows_checked": checked,
        "certified_cells": certified,
        "refused_cells": refused,
        "manifest_entries_checked": len(manifest),
        "source_hashes_checked": len(certificate["frozen_inputs"]["source_sha256"]),
        "implementation_hashes_checked": len(certificate["frozen_inputs"]["implementation_sha256"]),
        "freeze_possible": False,
    }
    (BASE / "replay_result.json").write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
