"""The error budget: LORD++ online false-discovery control, plus the brake.

Every held-out look is one test in a single sequence that never resets. The
level a test is held to is paid for by the budget, and every discovery earns
budget back — so a pipeline that keeps sending junk faces a falling level and
one that finds real edges can keep testing (spec Part 4, decided by Sarmad
as "a budget that tightens on junk and loosens on real finds").

    alpha_t = gamma_t * W0
            + (alpha - W0) * gamma_{t - tau_1}
            + alpha * sum_{j >= 2} gamma_{t - tau_j}

tau_j is the step of the j-th rejection. Ramdas, Yang, Wainwright & Jordan
(2017), "Online control of the false discovery rate with decaying memory";
gamma from Javanmard & Montanari (2018). Valid for independent p-values;
held-out looks on different combinations reuse the same markets, so treat
the realised rate as a target the forward brake enforces, not a theorem.
"""
from __future__ import annotations

import math

#: Javanmard & Montanari's constant: sum_j gamma_j <= 1 with it
GAMMA_C = 0.0722


def gamma(j: int) -> float:
    """The j-th term (j >= 1) of the spending sequence."""
    if j < 1:
        return 0.0
    lj = math.log(max(j, 2))
    return GAMMA_C * lj / (j * math.exp(math.sqrt(math.log(j))))


def alpha_at(t: int, rejections, alpha: float = 0.10,
             w0: float = 0.05) -> float:
    """The level test `t` (1-based) is held to, given earlier rejection
    steps. `w0` must not exceed `alpha`."""
    if not 0 < w0 <= alpha:
        raise ValueError(f"need 0 < w0 <= alpha, got w0={w0} alpha={alpha}")
    taus = sorted(int(r) for r in rejections if int(r) < t)
    a = gamma(t) * w0
    if taus:
        a += (alpha - w0) * gamma(t - taus[0])
        a += alpha * sum(gamma(t - tau) for tau in taus[1:])
    return a


def run(pvalues, alpha: float = 0.10, w0: float = 0.05) -> list[bool]:
    """Walk a whole sequence; True where the p-value is rejected."""
    rej_steps, out = [], []
    for t, p in enumerate(pvalues, start=1):
        r = p is not None and float(p) <= alpha_at(t, rej_steps, alpha, w0)
        out.append(bool(r))
        if r:
            rej_steps.append(t)
    return out


#: machine admissions that must finish their forward window before the
#: brake reads them
BRAKE_MIN_WINDOWS = 5


def brake_factor(failed, target: float = 0.10,
                 min_windows: int = BRAKE_MIN_WINDOWS) -> float:
    """0.5 while more than `target` of finished forward windows failed.

    `failed` is one bool per machine-admitted spec that has completed its
    forward window. Below `min_windows` there is nothing to read yet.
    """
    failed = [bool(f) for f in failed]
    if len(failed) < min_windows:
        return 1.0
    return 0.5 if sum(failed) / len(failed) > target else 1.0
