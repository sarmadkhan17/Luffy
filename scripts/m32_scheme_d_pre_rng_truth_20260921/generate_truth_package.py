#!/usr/bin/env python3
"""Generate the merged Scheme D pre-RNG truth package (55 cells x 384 rows).

Standard library only.  No RNG, no simulation, no numerical integration and no
optimisation: every categorical kernel row is merged from an already-frozen
family certificate; every persistence row is either an exact-arithmetic premise
check of the Gaussian-coupling theorem below or the frozen P7/C4 Arb certificate.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = HERE / "package"
PROTOCOL = "docs/superpowers/specs/2026-09-20-m32-scheme-d-actual-statistic-validation-protocol-pre-rng-correction.md"
BASE_PROTOCOL = "docs/superpowers/specs/2026-09-19-m32-scheme-d-actual-statistic-validation-protocol.md"
H = 384
MAJ = Fraction(20, 31)
DELTA = Fraction(1, 10**10)
RADIUS = Fraction(1, 10**12)
CAT_H = tuple(range(128)) + tuple(range(256, 384))
PERSIST = frozenset(range(128, 256))
INJECTIONS = {
    1: (0,), 2: (128,), 3: (320,),
    4: (0, 33, 130, 163, 260, 293, 326, 359),
    5: tuple(x for c in range(4) for x in (c, c + 32, 128 + c, 160 + c, 256 + c, 288 + c, 320 + c, 352 + c)),
    6: tuple(range(0, 8)) + tuple(range(136, 144)) + tuple(range(272, 280)) + tuple(range(344, 352)),
    7: (tuple(range(0, 24)) + tuple(range(136, 160)) + tuple(range(272, 288))
        + tuple(range(288, 296)) + tuple(range(320, 336)) + tuple(range(344, 352))),
}
ODDS = {1: Fraction(3), 3: Fraction(3), 4: Fraction(2), 5: Fraction(2), 6: Fraction(2), 7: Fraction(3, 2)}
ROLES = {1: "calibration", 2: "power", 3: "calibration", 4: "power", 5: "power", 6: "power", 7: "sensitivity"}
STATE, LINKED = "0.6744897501960817", "0.8416212335729143"

# (id, path, role, manifest, manifest_key) -- manifest_key None: file is the manifest's own anchor.
SOURCES = (
    ("estimand_spec", "scripts/m32_categorical_equivalence_20260920/estimand_spec.json"),
    ("base_certificate", "scripts/m32_categorical_equivalence_20260920/certificate.json"),
    ("base_replay", "scripts/m32_categorical_equivalence_20260920/replay.json"),
    ("completion_certificate", "scripts/m32_categorical_arb_completion_20260920/certificate.json"),
    ("completion_replay", "scripts/m32_categorical_arb_completion_20260920/replay.json"),
    ("p4_c3_primary", "scripts/m32_p4_c3_c4_taylor_20260921/primary_c3.json"),
    ("p4_c3_replay", "scripts/m32_p4_c3_c4_taylor_20260921/replay_c3.json"),
    ("p4_c4_primary", "scripts/m32_p4_c3_c4_taylor_20260921/primary_c4.json"),
    ("p4_c4_replay", "scripts/m32_p4_c3_c4_taylor_20260921/replay_c4.json"),
    ("p5_c3_primary", "scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_hit_miss_primary.json"),
    ("p5_c3_replay", "scripts/m32_p5_c3_taylor_canary_20260920/p5_c3_hit_miss_replay.json"),
    ("p5_c3_certifier", "scripts/m32_p5_c3_taylor_canary_20260920/certify_hit_miss_taylor.py"),
    ("p5_c4_primary", "scripts/m32_p5_c4_taylor_20260921/p5_c4_primary.json"),
    ("p5_c4_replay", "scripts/m32_p5_c4_taylor_20260921/p5_c4_replay.json"),
    ("p6_c3_primary", "scripts/m32_p6_c3_taylor_20260921/p6_c3_primary.json"),
    ("p6_c3_replay", "scripts/m32_p6_c3_taylor_20260921/p6_c3_replay.json"),
    ("p6_c4_primary", "scripts/m32_p6_c4_taylor_20260921/p6_c4_primary.json"),
    ("p6_c4_replay", "scripts/m32_p6_c4_taylor_20260921/p6_c4_replay.json"),
    ("p7c4_persistence_primary", "scripts/m32_p7c4_persistence_20260921/primary_certificate.json"),
    ("p7c4_persistence_replay", "scripts/m32_p7c4_persistence_20260921/replay_certificate.json"),
    ("p7c4_persistence_replay_check", "scripts/m32_p7c4_persistence_20260921/replay_result.json"),
    ("remaining_primary", "scripts/m32_p5c3_p6c4_remaining_20260921/primary.json"),
    ("remaining_replay", "scripts/m32_p5c3_p6c4_remaining_20260921/replay.json"),
    ("remaining_replay_check", "scripts/m32_p5c3_p6c4_remaining_20260921/replay_result.json"),
    ("remaining_certifier", "scripts/m32_p5c3_p6c4_remaining_20260921/certify_remaining.py"),
)
P5C3_KERNEL = "3b715db927f6e887cfda"   # focal linked target in a (2 state, 4 linked) cluster; see registry rule
PERSISTENCE_THEOREM = (
    "Let Z be the centred Gaussian membership-latent vector with unit variances and a positive-definite "
    "covariance, member(t) = 1[Z_t > tau], T the persistence target set, S = sum_{t in T} member(t), and "
    "Y = X + a*S with a > 0 and X independent of Z with an everywhere-positive continuous density. Fix a "
    "persistence hypothesis h and put rho_t = corr(Z_h, Z_t) for t in T, t != h, all with |rho_t| < 1. "
    "(1) Gaussian regression: Z_t = rho_t Z_h + W_t with W independent of Z_h (W_t has variance 1-rho_t^2 > 0). "
    "(2) Population: Z_h ~ N(0,1) and S^p = sum_t 1[rho_t Z_h + W_t > tau] (+ 1[Z_h > tau] if h in T). Members: "
    "Z_h is drawn from N(0,1) truncated to (tau, inf). With one uniform U both are quantile transforms, "
    "Z^m = Phi^-1(c + (1-c)U) >= Phi^-1(U) = Z^p, strictly on {U < 1}, and W is shared. (3) If every rho_t >= 0 "
    "then every summand is pathwise nondecreasing in Z_h, so S^m >= S^p a.s.; if every rho_t <= 0 and h not in T "
    "then S^m <= S^p a.s. (4) Strictness: if h in T (self term) or some rho_t != 0, then P(S^m != S^p) > 0 "
    "because W_t has full support. (5) F_Y(y) = E[F_X(y - a S)] is continuous and strictly increasing in y, "
    "so the population median m_p is unique with F_Y^p(m_p) = 1/2. For y fixed, F^p(y) - F^m(y) = "
    "P(X + a S^p <= y < X + a S^m) > 0 in the nondecreasing case (the reverse strict inequality in the "
    "nonincreasing case), because X has positive density given (S^p, S^m). Hence F^m(m_p) < 1/2 (> 1/2), and "
    "the unique member median is strictly above (below) m_p. (6) The signed metric divides by MAD+1 >= 1, "
    "so its sign is +1 (-1). Mixed-sign rho_t, or h in T with negative rho_t, are outside the theorem and "
    "must be certified numerically or refused."
)
PROOF_LIBRARY = {
    "P1_P3_SINGLE_OR_MAJORITY": "A=E[(5/6)^K|G] with K in {0,1}, hence A in [5/6,1]. Class 0 minus class 2 is (31/20)A-1 >= 7/24 > 0 and class 0 exceeds class 1 by (2/5)A > 0, so matched, unmatched and population majorities are class 0 and the macro-F1 lift is exactly zero.",
    "NO_CATEGORICAL_INJECTION": "The categorical outcome is independent of every membership with probabilities (13/20,1/4,1/10); every conditional and population majority is class 0, so the lift is exactly zero.",
    "OUTCOME_COLUMN_INDEPENDENCE": "No persistence injection is applied; the persistence outcome is independent of all membership latents, so conditional and population medians coincide exactly.",
    "C0_NON_TARGET_INDEPENDENCE": "Under C0 a non-target membership is independent of the base outcome and of every injected target membership; its conditional outcome law equals the population law exactly.",
    "C0_TARGET_EXACT_RATIONAL_MAJORITY": "Sequential label switching composes survival factors; exact Bernoulli convolution gives the recorded hit, miss and population class probabilities, which share one unique majority, so both classifiers coincide and the lift is exactly zero.",
    "NULL_CELL_TRUE_NULL_BY_CONSTRUCTION": "Protocol 2026-09-19 section 11.2: 'For the 20 null cells, the table is also emitted. Every hypothesis in them is a true null by construction.' No injection is applied in N0-N3.",
    "PERSISTENCE_GAUSSIAN_COUPLING_SIGN_V1": PERSISTENCE_THEOREM,
    "P7_C4_PERSISTENCE_ARB_INTERVAL": "Frozen P7/C4 Arb certificate (24 targets: 8 sector A, 16 sector B). The row's (sector, target) class signed-effect interval has radius <= 1e-12 and excludes [-1e-10, 1e-10]; primary (224-bit) and replay (384-bit) intervals overlap.",
    "CATEGORICAL_KERNEL_COMMON_MAJORITY_NULL": "Frozen certificate proves the hit, miss and population majorities are the same strict class, so both classifiers are one constant classifier and the macro-F1 lift is exactly zero (algebraic identity, not an interval).",
    "CATEGORICAL_KERNEL_SIGNED_LIFT": "Frozen certificate proves strict hit/miss/population majorities that differ and a signed macro-F1 lift enclosure of radius <= 1e-12 that excludes [-1e-10, 1e-10]; primary and higher-precision replay agree.",
    "CATEGORICAL_KERNEL_UNCERTIFIED": "No frozen certificate covers this categorical kernel; fail closed under corrected protocol section 4.4.",
}


def canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fstr(x: Fraction) -> str:
    return f"{x.numerator}/{x.denominator}"


def frac_of(text: str) -> Fraction:
    n, _, d = text.partition("/")
    return Fraction(int(n), int(d or 1))


def label(h: int) -> str:
    return "persistence" if h in PERSIST else ("case_kind" if h < 128 else "continuation")


def meta(h: int) -> dict:
    kind = "state" if h < 256 else ("transition" if h < 320 else "sequence")
    return {"hypothesis": h, "kind": kind, "label": label(h),
            "threshold": STATE if h < 256 else LINKED, "cluster": h % 32,
            "sector": "A" if h % 32 < 16 else "B"}


# ------------------------------------------------------------------ covariance
def rho(c: int, i: int, j: int) -> Fraction:
    """Exact latent correlation of two distinct hypotheses under C0-C4."""
    if c == 0:
        return Fraction(0)
    if c == 1:
        return Fraction(3, 10)
    if c == 2:
        return Fraction(7, 10)
    if c == 3:
        return Fraction(1, 10) + (Fraction(3, 5) if i % 32 == j % 32 else Fraction(0))
    sign = 1 if (i % 32 < 16) == (j % 32 < 16) else -1
    return Fraction(3, 5) * (Fraction(1, 4) + sign * Fraction(3, 4))


FOUNDATIONS = {
    "form": "Sigma = sum_k w_k s_k s_k^T + d I with w_k >= 0, d > 0, hence positive definite; unit variance is sum_k w_k + d = 1",
    "C0": {"weights": [], "residual": "1/1"},
    "C1": {"weights": ["3/10"], "residual": "7/10", "loading_signs": "all +"},
    "C2": {"weights": ["7/10"], "residual": "3/10", "loading_signs": "all +"},
    "C3": {"weights": ["1/10", "3/5"], "residual": "3/10", "loading_signs": "global all +; cluster indicator by h mod 32"},
    "C4": {"weights": ["3/20", "9/20"], "residual": "2/5",
           "loading_signs": "factor 1 all +; factor 2 + for h mod 32 < 16 else -"},
    "float_rounding": "implementation loadings are binary64; a perturbation of <= 1e-15 cannot change any recorded "
                      "correlation sign because every nonzero |rho| >= 1/10 and 1-|rho| >= 3/10",
}
for _c in ("C1", "C2", "C3", "C4"):
    FOUNDATIONS[_c]["variance_identity"] = fstr(sum((frac_of(w) for w in FOUNDATIONS[_c]["weights"]), Fraction(0))
                                                 + frac_of(FOUNDATIONS[_c]["residual"]))


# ------------------------------------------------------------------ exact C0 categorical
def convolve(ps):
    out = [Fraction(1)]
    for p in ps:
        nxt = [Fraction(0)] * (len(out) + 1)
        for k, mass in enumerate(out):
            nxt[k] += mass * (1 - p)
            nxt[k + 1] += mass * p
        out = nxt
    return out


def mixture(ps, odds, forced):
    out = [Fraction(0)] * 3
    for k, mass in enumerate(convolve(ps)):
        a = (Fraction(10) / (9 + odds)) ** (k + forced)
        vals = (Fraction(13, 20) * a, Fraction(1, 4) * a, 1 - Fraction(9, 10) * a)
        for i in range(3):
            out[i] += mass * vals[i]
    return tuple(out)


def c0_witness(s: int, h: int) -> dict:
    targets = [x for x in INJECTIONS[s] if x not in PERSIST]
    q = Fraction(1, 4) if h < 256 else Fraction(1, 5)
    rest = [Fraction(1, 4) if x < 256 else Fraction(1, 5) for x in targets if x != h]
    hit, miss = mixture(rest, ODDS[s], 1), mixture(rest, ODDS[s], 0)
    pop = tuple(q * hit[i] + (1 - q) * miss[i] for i in range(3))
    maj = [max(range(3), key=lambda i: v[i]) for v in (hit, miss, pop)]
    if len(set(maj)) != 1:
        raise AssertionError(f"C0 majority split P{s} h{h}")
    return {"membership_probability": fstr(q), "remaining_target_probabilities": [fstr(x) for x in rest],
            "odds_ratio": fstr(ODDS[s]),
            "conditional_hit_class_probabilities": [fstr(x) for x in hit],
            "conditional_miss_class_probabilities": [fstr(x) for x in miss],
            "population_class_probabilities": [fstr(x) for x in pop],
            "majorities_hit_miss_population": maj, "macro_f1_lift": "0/1"}


# ------------------------------------------------------------------ kernel identity
def kind01(h: int) -> int:
    return 0 if h < 256 else 1


def composition(values):
    return sum(kind01(h) == 0 for h in values), sum(kind01(h) == 1 for h in values)


def kernel_spec(s: int, c: int, h: int) -> dict:
    targets = tuple(x for x in INJECTIONS[s] if x not in PERSIST)
    r = Fraction(10) / (9 + ODDS[s])
    spec = {"scenario": s, "correlation": c, "h_kind": kind01(h), "is_target": h in targets,
            "r_n": r.numerator, "r_d": r.denominator}
    if c in (1, 2):
        spec["target_counts"] = composition(targets)
    elif c == 3:
        raw = tuple(composition(tuple(x for x in targets if x % 32 == k)) for k in range(32))
        f = h % 32
        spec["h_cluster"] = 0
        spec["cluster_counts"] = (raw[f],) + tuple(sorted(raw[:f] + raw[f + 1:]))
    else:
        spec["h_sector"] = 0 if h % 32 < 16 else 1
        spec["sector_counts"] = tuple(composition(tuple(x for x in targets if (x % 32 < 16) == (sec == 0)))
                                      for sec in range(2))
    return spec


def kernel_id(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]


# ------------------------------------------------------------------ frozen categorical evidence
def dyadic(rec: dict, which: str) -> Fraction:
    d = rec[which]
    return Fraction(int(d["mantissa"])) * Fraction(2) ** int(d["exponent"])


def iv(rec: dict) -> tuple[Fraction, Fraction]:
    return dyadic(rec, "lower_dyadic"), dyadic(rec, "upper_dyadic")


def survival_class(rec: dict):
    lo, hi = iv(rec)
    return 0 if lo > MAJ else 2 if hi < MAJ else None


def overlap(a: dict, b: dict) -> bool:
    (al, ah), (bl, bh) = iv(a), iv(b)
    return max(al, bl) <= min(ah, bh)


def assess(p: dict, r: dict, keys: tuple[str, str, str], lift_key: str | None) -> dict | None:
    """Accept one kernel from primary+replay records; None means refused."""
    tp = tuple(survival_class(p[k]) for k in keys)
    tr = tuple(survival_class(r[k]) for k in keys)
    if None in tp or tp != tr or not all(overlap(p[k], r[k]) for k in keys):
        return None
    lifts = [x.get(lift_key) if lift_key else None for x in (p, r)]
    if len(set(tp)) == 1:
        if any(l is not None and iv(l) != (0, 0) for l in lifts):
            return None
        return {"classification": "true_null", "sign": None, "triple": list(tp)}
    if any(l is None for l in lifts) or not overlap(*lifts):
        return None
    signs = set()
    for lo_hi in (iv(l) for l in lifts):
        lo, hi = lo_hi
        if (hi - lo) / 2 > RADIUS:
            return None
        signs.add(1 if lo > DELTA else -1 if hi < -DELTA else 0)
    if len(signs) != 1 or 0 in signs:
        return None
    return {"classification": "analytic_non_null", "sign": signs.pop(), "triple": list(tp)}


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def parse_ball(text: str) -> tuple[Fraction, Fraction]:
    from decimal import Decimal
    mid, _, rad = text.strip("[]").partition(" +/- ")
    return Fraction(Decimal(mid)), Fraction(Decimal(rad))


def frozen_kernel_evidence() -> tuple[dict[str, dict], dict[str, str]]:
    """kernel_id -> accepted evidence, and the source label used for each."""
    out: dict[str, dict] = {}

    def add(kid: str, source: str, ev: dict | None) -> None:
        if ev is None:
            return
        if kid in out and (out[kid]["classification"], out[kid]["sign"]) != (ev["classification"], ev["sign"]):
            raise AssertionError(f"conflicting frozen evidence for {kid}")
        out.setdefault(kid, {**ev, "source": source})

    base, base_replay = load(SOURCES[1][1]), load(SOURCES[2][1])
    if base_replay["result"] != "PASS":
        raise AssertionError("base replay not PASS")
    for kid, k in base["kernels"].items():
        if k["classification"] != "true_null":
            continue
        signs = [1 if iv(k["majority_margins"][n])[0] > 0 else -1 if iv(k["majority_margins"][n])[1] < 0 else 0
                 for n in ("population", "hit", "miss")]
        rr = base_replay["kernel_results"][kid]
        if len(set(signs)) == 1 and signs[0] and rr["classification"] == "true_null" and rr["majority_signs"] == signs \
                and rr["primary_certified_conclusion_reproduced"] and rr["primary_replay_intervals_overlap"]:
            cls = 2 if signs[0] > 0 else 0
            add(kid, "base_certificate", {"classification": "true_null", "sign": None, "triple": [cls] * 3})

    comp, comp_replay = load(SOURCES[3][1]), load(SOURCES[4][1])
    rows = {row["kernel_id"]: row for row in comp_replay["rows"]}
    if comp_replay["result"] != "PASS":
        raise AssertionError("completion replay not PASS")
    for kid, k in comp["kernels"].items():
        if k["classification"] != "non_null" or kid not in rows or rows[kid]["result"] != "PASS":
            continue
        tp = tuple(survival_class(k[n]) for n in ("hit_pgf", "miss_pgf", "population_pgf"))
        (lo, hi), (rmid, rrad) = iv(k["signed_lift_enclosure"]), parse_ball(rows[kid]["signed_lift_enclosure"])
        sign = 1 if lo > DELTA else -1 if hi < -DELTA else 0
        if None in tp or len(set(tp)) == 1 or list(tp) != rows[kid]["argmax"] or (hi - lo) / 2 > RADIUS or not sign \
                or not (max(lo, rmid - rrad) <= min(hi, rmid + rrad)) or not rows[kid]["overlaps_primary_enclosure"]:
            continue
        add(kid, "completion_certificate", {"classification": "analytic_non_null", "sign": sign, "triple": list(tp)})

    for tag, cell in (("p4_c3", (4, 3)), ("p4_c4", (4, 4))):
        p, r = load(f"scripts/m32_p4_c3_c4_taylor_20260921/primary_c{cell[1]}.json"), \
               load(f"scripts/m32_p4_c3_c4_taylor_20260921/replay_c{cell[1]}.json")
        if p["status"] == r["status"] == "certified" and p["kernel_id"] == r["kernel_id"] and not p["rng_used"]:
            ev = assess(p, r, ("conditional_hit_interval", "conditional_miss_interval", "population_interval"),
                        "signed_lift_interval")
            derived = {kernel_id(kernel_spec(*cell, h)) for h in CAT_H}
            if p["kernel_id"] in derived:
                add(p["kernel_id"], f"{tag}_primary", ev)
    spec_by_id = {kernel_id(sp): json.loads(json.dumps(sp))
                  for sp in (kernel_spec(s, c, h) for s in range(4, 8) for c in range(1, 5) for h in CAT_H)}
    for tag in ("p5_c4", "p6_c3"):
        p, r = load(f"scripts/m32_{tag}_taylor_20260921/{tag}_primary.json"), \
               load(f"scripts/m32_{tag}_taylor_20260921/{tag}_replay.json")
        cell = int(tag[1])
        for kid, pk in p["kernels"].items():
            rk = r["kernels"].get(kid)
            if rk and pk["status"] == rk["status"] == "certified" and pk["spec"] == rk["spec"] == spec_by_id.get(kid) \
                    and pk["spec"]["scenario"] == cell and kernel_id(pk["spec"]) == kid:
                add(kid, f"{tag}_primary", assess(pk, rk, ("conditional_hit_interval", "conditional_miss_interval",
                                                              "population_interval"), "signed_lift_interval"))
    # the nine remaining P5/C3 and P6/C4 kernels: multi-kernel primary + higher-precision replay
    p, r, chk = load(SOURCES[21][1]), load(SOURCES[22][1]), load(SOURCES[23][1])
    if (chk["result"] == "PASS" and chk["kernels_checked"] == 9 and set(p["kernels"]) == set(r["kernels"])
            and not p["rng_used"] and not r["rng_used"] and not p["search_performed"]):
        for kid, pk in p["kernels"].items():
            rk = r["kernels"][kid]
            if pk["status"] == rk["status"] == "certified" and pk["spec"] == rk["spec"] == spec_by_id.get(kid) \
                    and kernel_id(pk["spec"]) == kid:
                add(kid, "remaining_primary", assess(pk, rk, ("conditional_hit_interval", "conditional_miss_interval",
                                                                "population_interval"), "signed_lift_interval"))
    # canaries: one kernel each.  P6/C4 carries its spec; P5/C3 is matched structurally (registry rule).
    p, r = load(SOURCES[16][1]), load(SOURCES[17][1])
    if p["accepted"] and r["accepted"] and p["spec"] == r["spec"] and not p["rng_constructed"]:
        add(kernel_id(p["spec"]), "p6_c4_primary",
            assess(p, r, ("hit", "miss", "population"), "macro_f1_lift"))
    p, r = load(SOURCES[9][1]), load(SOURCES[10][1])
    text = (ROOT / SOURCES[11][1]).read_text(encoding="utf-8")
    spec5 = next(kernel_spec(5, 3, h) for h in CAT_H if kernel_id(kernel_spec(5, 3, h)) == P5C3_KERNEL)
    structural = (spec5["is_target"] and spec5["h_kind"] == 1 and spec5["cluster_counts"][0] == (2, 4)
                  and (spec5["r_n"], spec5["r_d"]) == (10, 11)
                  and "return d0**2*d1**4, self.r*ps[1]*other, (1-ps[1])*other" in text
                  and "other = d0**2*d1**3" in text and "p**4, h*p**3, m*p**3" in text)
    if structural and p["accepted"] and r["accepted"] and not p["rng_constructed"]:
        add(P5C3_KERNEL, "p5_c3_primary", assess(p, r, ("hit", "miss", "population"), "macro_f1_lift"))
    return out, {k: v["source"] for k, v in out.items()}


# ------------------------------------------------------------------ P7/C4 persistence certificate
def p7c4_class(h: int) -> str:
    return f"{'A' if h % 32 < 16 else 'B'}_{'target' if h in INJECTIONS[7] else 'non_target'}"


def p7c4_ok() -> dict[str, dict]:
    p, r = load(SOURCES[18][1]), load(SOURCES[19][1])
    chk = load(SOURCES[20][1])
    if not (p["status"] == r["status"] == chk["result"] == "PASS" and p["rng_constructed"] is False):
        raise AssertionError("P7/C4 persistence certificate not accepted")
    counts = Counter(p7c4_class(h) for h in PERSIST)
    if counts != Counter({"A_target": 8, "A_non_target": 56, "B_target": 16, "B_non_target": 48}):
        raise AssertionError(f"unexpected P7/C4 class counts {counts}")
    out = {}
    for name in ("A_target", "A_non_target", "B_target", "B_non_target"):
        ep, er = p["signed_persistence_effect_intervals"][name], r["signed_persistence_effect_intervals"][name]
        (lo, hi), (rl, rh) = iv(ep), iv(er)
        if not (lo > DELTA and rl > DELTA and (hi - lo) / 2 <= RADIUS and (rh - rl) / 2 <= RADIUS and overlap(ep, er)):
            raise AssertionError(f"P7/C4 persistence class {name} not accepted")
        out[name] = {"sign": 1, "lower": fstr(lo), "radius": fstr((hi - lo) / 2)}
    return out


# ------------------------------------------------------------------ rows
def persistence_record(s: int, c: int, h: int, p7c4: dict) -> tuple[str, int | None, str, dict | None]:
    targets = sorted(x for x in INJECTIONS[s] if x in PERSIST)
    if not targets:
        return "true_null", None, "OUTCOME_COLUMN_INDEPENDENCE", None
    if c == 0 and h not in targets:
        return "true_null", None, "C0_NON_TARGET_INDEPENDENCE", None
    if s == 7 and c == 4:
        cls = p7c4_class(h)
        return "analytic_non_null", p7c4[cls]["sign"], "P7_C4_PERSISTENCE_ARB_INTERVAL", {
            "class": cls, "certificate": SOURCES[18][1], "effect_lower_bound": p7c4[cls]["lower"],
            "effect_radius": p7c4[cls]["radius"]}
    rhos = [rho(c, h, t) for t in targets if t != h]
    self_target = h in targets
    if all(x >= 0 for x in rhos) and (self_target or any(x > 0 for x in rhos)):
        sign = 1
    elif all(x <= 0 for x in rhos) and not self_target and any(x < 0 for x in rhos):
        sign = -1
    else:
        return "unclassifiable", None, "PERSISTENCE_GAUSSIAN_COUPLING_PREMISE_FAILED", {"self_target": self_target}
    counts = Counter(fstr(x) for x in rhos)
    return "analytic_non_null", sign, "PERSISTENCE_GAUSSIAN_COUPLING_SIGN_V1", {
        "targets": targets, "self_target": self_target,
        "rho_counts": {k: counts[k] for k in sorted(counts)},
        "max_abs_rho": fstr(max((abs(x) for x in rhos), default=Fraction(0))),
        "strict_witness": "self_target" if self_target else "nonzero_rho",
        "shift_positive": True, "base_density_everywhere_positive": True}


def record(scope: str, s: int, c: int, h: int, evidence: dict, p7c4: dict) -> dict:
    m = meta(h)
    witness = None
    if scope == "N":
        cls, sign, proof = "true_null", None, "NULL_CELL_TRUE_NULL_BY_CONSTRUCTION"
    elif label(h) == "persistence":
        cls, sign, proof, witness = persistence_record(s, c, h, p7c4)
    elif s in (1, 3):
        cls, sign, proof = "true_null", None, "P1_P3_SINGLE_OR_MAJORITY"
    elif s == 2:
        cls, sign, proof = "true_null", None, "NO_CATEGORICAL_INJECTION"
    elif c == 0:
        if h in INJECTIONS[s]:
            cls, sign, proof, witness = "true_null", None, "C0_TARGET_EXACT_RATIONAL_MAJORITY", c0_witness(s, h)
        else:
            cls, sign, proof = "true_null", None, "C0_NON_TARGET_INDEPENDENCE"
    else:
        kid = kernel_id(kernel_spec(s, c, h))
        ev = evidence.get(kid)
        if ev is None:
            cls, sign, proof, witness = "unclassifiable", None, "CATEGORICAL_KERNEL_UNCERTIFIED", {"kernel_id": kid}
        else:
            cls, sign = ev["classification"], ev["sign"]
            proof = ("CATEGORICAL_KERNEL_COMMON_MAJORITY_NULL" if cls == "true_null"
                     else "CATEGORICAL_KERNEL_SIGNED_LIFT")
            witness = {"kernel_id": kid, "source": ev["source"], "hit_miss_population_classes": ev["triple"]}
    return {**m, "classification": cls, "sign": sign, "proof_id": proof, "witness": witness}


def main() -> None:
    (OUT / "tables").mkdir(parents=True, exist_ok=True)
    evidence, _ = frozen_kernel_evidence()
    p7c4 = p7c4_ok()
    sources = {name: {"path": path, "sha256": sha256(ROOT / path)} for name, path in SOURCES}
    cells, table_hashes, summaries = [], {}, []
    for scope, ids in (("N", range(4)), ("P", range(1, 8))):
        for s in ids:
            for c in range(5):
                name = f"{scope}{s}_C{c}"
                role = "null" if scope == "N" else ROLES[s]
                recs = [record(scope, s, c, h, evidence, p7c4) for h in range(H)]
                unresolved = [r["hypothesis"] for r in recs if r["classification"] == "unclassifiable"]
                payload = {"schema": "m3.2-scheme-d-pre-rng-truth-table.v4", "cell": f"{scope}{s}/C{c}",
                           "role": role, "hypotheses": H, "unresolved_hypotheses": unresolved, "records": recs}
                rel = f"tables/{name}.json"
                (OUT / rel).write_text(canonical(payload), encoding="utf-8")
                table_hashes[rel] = sha256(OUT / rel)
                counts = Counter(f"{r['classification']}:{r['sign']}" for r in recs)
                summaries.append({"cell": f"{scope}{s}/C{c}", "role": role, "unresolved_count": len(unresolved),
                                  "classification_counts": dict(sorted(counts.items()))})
    unresolved_cells = [x["cell"] for x in summaries if x["unresolved_count"]]
    unresolved_rows = sum(x["unresolved_count"] for x in summaries)
    kernel_gaps = Counter()
    for s in range(4, 8):
        for c in range(1, 5):
            for h in CAT_H:
                kid = kernel_id(kernel_spec(s, c, h))
                if kid not in evidence:
                    kernel_gaps[(f"P{s}/C{c}", kid)] += 1
    certificate = {
        "schema": "m3.2-scheme-d-pre-rng-proof-certificate.v4",
        "authority": PROTOCOL, "base_protocol": BASE_PROTOCOL,
        "scope": "N0-N3 and P1-P7 x C0-C4: 55 cells x 384 rows",
        "rng_constructed": False, "simulation_worlds_constructed": 0, "optimization_performed": False,
        "delta_truth": "1e-10", "maximum_interval_radius": "1e-12",
        "acceptance_partition": {"null_cells": 20, "p1_p3_calibration_cells": 10, "p2_p4_p5_p6_power_cells": 20,
                                 "p7_sensitivity_cells": 5, "total_cells": 55, "total_worlds": 75000},
        "sources": sources,
        "foundations": FOUNDATIONS,
        "proof_library": PROOF_LIBRARY,
        "p5_c3_canary_kernel_rule": {"kernel_id": P5C3_KERNEL,
                                     "rule": "focal linked target (h_kind 1, is_target) in a (2 state, 4 linked) cluster, r=10/11; matches certifier constants d0**2*d1**4 population, r*p1*d0**2*d1**3 hit, p**4/h*p**3/m*p**3 assembly"},
        "cell_summaries": summaries,
        "cells_total": len(summaries), "rows_total": len(summaries) * H,
        "unresolved_cells": unresolved_cells, "unresolved_rows": unresolved_rows,
        "uncertified_categorical_kernels": [
            {"cell": cell, "kernel_id": kid, "rows": n} for (cell, kid), n in sorted(kernel_gaps.items())],
        "implementation_sha256": {n: sha256(HERE / n) for n in ("generate_truth_package.py", "verify_truth_package.py")},
        "table_sha256": table_hashes,
        "freeze_possible": unresolved_rows == 0,
        "freeze_blocker": None if unresolved_rows == 0 else
        f"{unresolved_rows} categorical rows in {len(unresolved_cells)} cells lack a frozen kernel certificate",
    }
    cert = OUT / "proof_certificate.json"
    cert.write_text(canonical(certificate), encoding="utf-8")
    lines = [f"{sha256(cert)}  proof_certificate.json"]
    lines += [f"{d}  {n}" for n, d in sorted(table_hashes.items())]
    (OUT / "manifest.sha256").write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"cells={len(summaries)} rows={len(summaries) * H} unresolved_rows={unresolved_rows} "
          f"unresolved_cells={unresolved_cells}")


if __name__ == "__main__":
    main()
