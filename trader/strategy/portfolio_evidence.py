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

from dataclasses import dataclass


@dataclass(frozen=True)
class Fill:
    """One realized trade, in R multiples of the risk staked."""
    entry_i: int
    exit_i: int
    r_multiple: float
    symbol: str


def portfolio_curve(fills: list[Fill], equity: float, risk_pct: float,
                    max_open: int) -> dict:
    """Walk `fills` chronologically against a single compounding balance.

    Risk is a fraction of CURRENT equity, so gains and losses compound the
    way a real account does. A signal arriving while `max_open` positions are
    already working is refused, not queued — that is what the live risk
    manager does, and a backtest that queues it reports trades the account
    could never have taken.
    """
    eq = float(equity)
    peak, max_dd = eq, 0.0
    open_until: list[int] = []
    taken, order = 0, []
    for f in sorted(fills, key=lambda x: (x.entry_i, x.symbol)):
        open_until = [x for x in open_until if x > f.entry_i]
        if len(open_until) >= max_open:
            continue
        eq += eq * (risk_pct / 100.0) * f.r_multiple
        eq = max(eq, 1e-9)                    # an account cannot go negative
        open_until.append(f.exit_i)
        taken += 1
        order.append(f.symbol)
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)
    return {"final": eq, "taken": taken, "max_dd_pct": max_dd,
            "total_pct": (eq / equity - 1.0) * 100.0 if equity else 0.0,
            "order": order}
