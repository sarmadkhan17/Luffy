"""Judge a diversified mechanism as one account, in time order.

The worst-symbol gate in `spec_evidence` asks whether a spec works on every
symbol independently. That is the right question for a single-symbol claim
and the wrong one for a mechanism built on diversification: trend following
is MEANT to lose on most symbols most of the time and pay for it with a few
long runs. Donchian Breakout Trail cleared 1.0 on four of five symbols and
failed the gate on the fifth.

The honest portfolio number needs two things the per-symbol path cannot give:

  * ONE balance. N separate sleeves divide the profit across N notional
    accounts and understate return on capital by roughly N.
  * A concurrency cap. An uncapped shared account overstates it, because
    eighteen simultaneous 1%-risk positions in correlated markets is a single
    18%-risk position, and its drawdown is not eighteen small ones.

This module does not replace the worst-symbol gate. It reports the number
that gate cannot see, so a portfolio mechanism is neither admitted on a
symbol that got lucky nor rejected on a symbol that did not.
"""
from __future__ import annotations

from copy import deepcopy

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from heapq import heappop, heappush
from math import fsum, isfinite
from pathlib import Path


ACCOUNTING_VERSION = "portfolio-exit-realized.v1"
LEGACY_ACCOUNTING_VERSION = "portfolio-entry-booked.legacy"
RESEARCH_CLOCK = "source bar timestamps in UTC seconds relative to one shared origin; no grid flooring"


class AccountingMismatch(ValueError):
    """Historical numbers cannot be compared under different accounting."""


@lru_cache(maxsize=1)
def _accounting_digest() -> str:
    return sha256(Path(__file__).read_bytes()).hexdigest()


def accounting_provenance(equity: float, risk_pct: float, max_open: int,
                         clock: str = "caller-supplied common ordered indices") -> dict:
    """Describe the numerical model; this is never live monetary authority."""
    return {"version": ACCOUNTING_VERSION, "implementation_sha256": _accounting_digest(),
            "clock": clock, "entry_order": "entry timestamp, symbol, stable input order",
            "events": "aggregate prior-position exits before same-timestamp entries",
            "stake": "closed equity at entry times risk_pct/100; fixed until exit",
            "pnl": "cost-inclusive simulated net R recognized once at exit",
            "drawdown": "closed equity only; simultaneous exits aggregated",
            "initial_equity": float(equity), "risk_pct": float(risk_pct), "max_open": max_open,
            "limitations": ["No open-position mark-to-market or intrabar drawdown",
                            "No margin reservation or available-capital calculation",
                            "Entry fees and funding cashflows included in net R at exit",
                            "Censored open positions absent from Fill records",
                            "Nonnegative equity floor is not a liquidation model"]}


def is_current_accounting(portfolio: dict) -> bool:
    a = portfolio.get("accounting") or {}
    if not isinstance(a, dict):
        return False
    try:
        return (a.get("version") == ACCOUNTING_VERSION
                and a == accounting_provenance(a["initial_equity"], a["risk_pct"],
                                              a["max_open"], a["clock"]))
    except (KeyError, TypeError, ValueError):
        return False


def require_current_accounting(portfolio: dict) -> None:
    if not is_current_accounting(portfolio):
        raise AccountingMismatch(f"portfolio_accounting_incompatible: {LEGACY_ACCOUNTING_VERSION} or unsupported implementation")


def require_compatible_accounting(*portfolios: dict) -> None:
    """Allow legacy replay, but never compare legacy and corrected numbers.

    Missing metadata identifies legacy evidence; it is not upgraded in place.
    New comparisons also require identical accounting inputs and clock semantics.
    """
    if not portfolios:
        return
    first = portfolios[0].get("accounting")
    if any(p.get("accounting") != first for p in portfolios[1:]):
        raise AccountingMismatch("portfolio_accounting_incompatible: legacy/version/clock/inputs differ")


@dataclass(frozen=True)
class Fill:
    """One realized trade, in R multiples of the risk staked."""
    entry_i: int
    exit_i: int
    r_multiple: float
    symbol: str


def portfolio_curve(fills: list[Fill], equity: float, risk_pct: float,
                    max_open: int, *, evaluation_identity: dict | None = None) -> dict:
    """Size at entry, realize cost-inclusive net R at exit on one shared clock.

    Drawdown is CLOSED equity, not mark-to-market. Prior-position exits at a
    timestamp settle as one batch before entries there. Accepted stakes stay
    fixed; refused signals are not queued. Fill must retain a positive holding
    interval: collapsed/malformed clocks cannot establish realization order.
    """
    eq = float(equity)
    peak, max_dd = eq, 0.0
    pending: list[tuple[int, int, float]] = []
    taken, order = 0, []

    def settle(until):
        nonlocal eq, peak, max_dd
        while pending and (until is None or pending[0][0] <= until):
            at = pending[0][0]
            pnl = []
            while pending and pending[0][0] == at:
                pnl.append(heappop(pending)[2])
            eq = max(eq + fsum(pnl), 1e-9)
            peak = max(peak, eq)
            if peak > 0:
                max_dd = max(max_dd, (peak - eq) / peak * 100.0)

    for f in sorted(fills, key=lambda x: (x.entry_i, x.symbol)):
        if f.exit_i <= f.entry_i or not isfinite(f.r_multiple):
            raise ValueError("portfolio_fill_invalid: positive holding interval and finite net R required")
        settle(f.entry_i)
        if len(pending) >= max_open:
            continue
        # R already contains modeled fees, slippage and funding. No cost is
        # recalculated here, and later equity changes cannot resize this stake.
        pnl = eq * (risk_pct / 100.0) * f.r_multiple
        heappush(pending, (f.exit_i, taken, pnl))
        taken += 1
        order.append(f.symbol)
    settle(None)
    return {"final": eq, "taken": taken, "max_dd_pct": max_dd,
            "total_pct": (eq / equity - 1.0) * 100.0 if equity else 0.0,
            "order": order,
            "accounting": accounting_provenance(equity, risk_pct, max_open),
            **({'evaluation_identity': deepcopy(evaluation_identity)} if evaluation_identity is not None else {})}
