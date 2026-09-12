"""Does this part earn its place?

Measured on the book's one working mechanism, same base rule, same geometry,
with and without each condition:

    variant    PF     null p    OOS trades   CAGR    maxDD
    baseline   1.42   3.5e-04   487          17.3%   27.3%
    carry      1.43   3.5e-04   415           7.6%   25.8%
    hivol      1.44   1.1e-02   307           6.3%   25.0%
    breadth    1.42   3.8e-02   466          17.1%   25.3%
    rel_btc    1.33   1.8e-04   426           9.6%   23.3%

Every filter held or improved the profit factor and most of them halved the
compounded return: the trades they cut were near break-even individually and
carried a disproportionate share of the fat right tail. So a part must
improve BOTH the rotation null and compounded return over one account, and
neither alone is evidence of anything.

A SURVIVOR must beat every one of its one-part ablations, not merely the
parent it grew from — a combination can be reached by several paths, and the
ablation is what shows which parts actually carry the edge. It is also what
the Reason step (phase 4) explains.
"""
from __future__ import annotations

#: above this, a rule is not distinguishable enough from its own rotation to
#: be worth spending the search's time extending. Deliberately loose: a piece
#: may be useless alone and valuable in company, and this only decides where
#: compute goes, never what is admitted.
GROW_MAX_P = 0.25


def _p(res) -> float:
    """A rule that could not be scored is treated as no-edge for comparison
    purposes: 1.0 is the worst a consistency p can be."""
    if not res:
        return 1.0
    p = res.get("consistency_p")
    return 1.0 if p is None else float(p)


def _pct(res) -> float:
    """Compounded return. A rule that never traded did not make money, and
    must not read as 'zero, which beats a loss'."""
    if not res or res.get("verdict") in ("empty", "error", None):
        return float("-inf")
    if res.get("verdict") == "untestable":
        return float("-inf")
    return float((res.get("portfolio") or {}).get("total_pct", 0.0))


def carries_information(res, max_p: float = GROW_MAX_P) -> bool:
    """Worth extending. Not 'is an edge' — worth spending compute on."""
    if not res or res.get("verdict") != "scored":
        return False
    if not res.get("testable"):
        return False
    p = res.get("consistency_p")
    return p is not None and float(p) <= float(max_p)


def earns_place(child, other) -> tuple[bool, str]:
    """Is `child` better than `other` on BOTH measures?"""
    cp, op = _p(child), _p(other)
    cpct, opct = _pct(child), _pct(other)
    if cp >= op:
        return False, (f"the null does not improve "
                       f"(p {cp:.2g} vs {op:.2g})")
    if cpct <= opct:
        return False, (f"compounded return does not improve "
                       f"({cpct:.1f}% vs {opct:.1f}%)")
    return True, (f"improves both: p {op:.2g} -> {cp:.2g}, "
                  f"return {opct:.1f}% -> {cpct:.1f}%")


def ablation(child, subset_results: dict) -> dict:
    """{removed part key: what the rule loses without it}."""
    out = {}
    for key, res in (subset_results or {}).items():
        out[key] = {
            "p": res.get("consistency_p"),
            "total_pct": (res.get("portfolio") or {}).get("total_pct"),
            "verdict": res.get("verdict"),
            "drop_p": round(_p(child) - _p(res), 8),
            "drop_pct": round(_pct(child) - _pct(res), 4)
            if _pct(res) != float("-inf") else None,
        }
    return out


def decide(child, parent=None, subset_results: dict | None = None,
           max_p: float = GROW_MAX_P) -> tuple[str, str, dict]:
    """(verdict, reason, ablation).

    survivor — beats every one-part ablation on both measures; a candidate
               for the held-out look phase 3 will spend evidence on.
    grow     — carries information and may be extended, but at least one of
               its parts does not earn its place yet.
    prune    — nothing here.
    """
    if not child or child.get("verdict") == "error":
        return "prune", f"evaluation failed: {(child or {}).get('error', '')}", {}
    if child.get("verdict") == "empty" or not child.get("trades"):
        return "prune", "no trades on the discovery slice", {}
    if child.get("verdict") == "untestable":
        return "prune", (f"only {child.get('scored_symbols', 0)} symbols "
                         f"carried a percentile"), {}
    if not child.get("testable"):
        return "prune", ("too picky to be judged: projects fewer than 8 "
                         "trades on 4 held-out markets"), {}
    if parent is not None:
        ok, why = earns_place(child, parent)
        if not ok:
            return "prune", f"does not beat its parent — {why}", {}
    if not carries_information(child, max_p):
        return "prune", (f"p={_p(child):.2g} > {max_p} — not distinguishable "
                         f"enough from its own rotation to extend"), {}
    abl = ablation(child, subset_results or {})
    weak = []
    for key, res in (subset_results or {}).items():
        ok, _why = earns_place(child, res)
        if not ok:
            weak.append(key)
    if subset_results and not weak:
        return "survivor", "every part earns its place", abl
    if weak:
        return "grow", (f"carries information, but these parts do not earn "
                        f"their place: {sorted(weak)}"), abl
    return "grow", f"carries information (p={_p(child):.2g})", abl
