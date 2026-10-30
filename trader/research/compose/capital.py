"""Shared initial-margin arithmetic; no approval, venue or account access.

One economic position's marked net equity pays for both legs and new costs.
Reductions do not need spare initial margin. This bounds an increase at its
observed price; it cannot guarantee a subsequent market fill or liquidation.
"""
import math

from ..closure import digest

CONTRACT=dict(version='combined-position-initial-margin.v1',
    equity='allocated capital plus both legs realized/unrealized PnL minus observed or explicitly modelled costs and signed funding',
    gross='sum of both remaining legs at the current executable observation',
    rule='gross_after/leverage + incremental_cost <= marked_net_equity_before',
    sizing='shrink increases to remaining shared initial-margin headroom; never enlarge an intent',
    incremental_cost='frozen declared fee and slippage reserve; actual receipts govern the eventual outcome',
    reductions='remain available without initial-margin headroom',
    clock='recompute at causal fill/submit observation, after pre-fill gaps, carry and preceding fills',
    unknown='no increase with unknown held marks or owned cashflows')
POLICY_ID=CONTRACT['version']+':'+digest(CONTRACT)


def capacity(*,equity,gross,leverage,unit_notional,requested,cost_rate):
    values=(equity,gross,leverage,unit_notional,requested,cost_rate)
    if (any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values)
            or gross<0 or leverage<=0 or unit_notional<=0 or requested<0 or cost_rate<0):
        raise ValueError('coordinated_capital_inputs_unavailable')
    # Solving (gross + q*unit)/leverage + q*unit*cost <= equity
    # reserves the same fee/slippage model used by the actual simulator fill.
    room=max(0.,equity-gross/leverage)
    unit_cost=unit_notional*(1./leverage+cost_rate)
    allowed=min(requested,room/unit_cost)
    if allowed and allowed<requested:allowed=math.nextafter(allowed,0.)
    return dict(policy_id=POLICY_ID,quantity=allowed,requested=requested,equity=equity,
        gross_before=gross,initial_margin_before=gross/leverage,margin_headroom=room,
        leverage=leverage,unit_notional=unit_notional,cost_rate=cost_rate,
        initial_margin_after=(gross+allowed*unit_notional)/leverage,
        incremental_cost=allowed*unit_notional*cost_rate)
