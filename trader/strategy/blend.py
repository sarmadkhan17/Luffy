"""How much each strategy in the active set gets to say.

The orchestrator has always COMBINED strategies — it sums every eligible
signal alongside the analyst votes, which is right, because no edge lasts and
a book concentrated on one mechanism dies with it. What it did not do is
WEIGH them: every strategy entered the sum at a flat `STRATEGY_VOTE_WEIGHT`,
so a mechanism printing PF 1.8 in the live regime counted exactly as much as
one limping at 0.9.

Analysts were never treated that way. They carry `base_weights × acc_mult ×
regime_fit` — measured accuracy and measured regime fit. This module gives
strategies the same treatment, from the same kind of evidence:

    weight = performance_multiplier(recent closed trades)
           × regime_multiplier(measured regime evidence, live regime)

Both are shrunk toward 1.0 by how much evidence actually exists, because the
failure mode here is not "we weighted it slightly wrong" — it is "two lucky
trades doubled a strategy's voice". And both are bounded away from zero: a
poor performer is quieter, never silent, because the thing that looks broken
in this regime is often what pays in the next one. Silencing is the
Analyst's job (retirement), not the blender's.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

MIN_W = 0.25             # quietest a live strategy can get — never mute
MAX_W = 2.0              # loudest, however good the record looks
#: Each FACTOR is bounded to half this span, so the product spans exactly
#: [MIN_W, MAX_W] without either factor alone pinning the result at the
#: ceiling. Pinned weights are worse than no weights: every strong strategy
#: collapses to the same number and the ranking inside the set disappears.
F_MIN, F_MAX = 0.5, 1.5
PRIOR_TRADES = 10.0      # evidence is worth half at this many closed trades
PRIOR_WINDOWS = 5.0      # regime evidence is worth half at this many windows
MIN_WINDOWS = 3          # below this, a regime measurement is not evidence
PF_CAP = 2.5             # a profit factor above this is small-sample luck


def _shrink(raw: float, n: float, prior: float) -> float:
    """Pull `raw` toward 1.0 by how thin the evidence is."""
    return 1.0 + (raw - 1.0) * (n / (n + prior))


def performance_multiplier(trades: list) -> float:
    """From realized profit factor on CLOSED trades.

    Unrealized P&L is not a result, so open trades are ignored entirely.
    """
    wins = losses = 0.0
    n = 0
    for t in trades or []:
        pnl = t.get("realized_pnl")
        if pnl is None:
            continue                       # still open — not a result yet
        pnl = float(pnl)
        n += 1
        if pnl >= 0:
            wins += pnl
        else:
            losses += -pnl
    if n == 0:
        return 1.0                         # unproven is not the same as bad
    if losses <= 0:
        pf = PF_CAP if wins > 0 else 1.0
    else:
        pf = min(wins / losses, PF_CAP)
    return max(F_MIN, min(F_MAX, _shrink(pf, float(n), PRIOR_TRADES)))


def regime_multiplier(evidence: dict, regime: str) -> float:
    """From the regime fitness the Analyst already measured.

    `evidence` is `spec.provenance["regime_evidence"]`:
    {regime: {hit_rate, median_pf, windows}}. Only the LIVE regime's row
    counts — fitness in a regime we are not in says nothing about now.
    """
    row = (evidence or {}).get(regime)
    if not isinstance(row, dict):
        return 1.0
    try:
        windows = float(row.get("windows") or 0)
        if windows < MIN_WINDOWS:
            return 1.0                     # measured too thinly to trust
        pf = min(float(row.get("median_pf") or 1.0), PF_CAP)
        hit = float(row.get("hit_rate") or 0.5)
    except (TypeError, ValueError):
        return 1.0
    # hit rate and profit factor say different things: how OFTEN it works,
    # and how well when it does. Use both, centred on 1.0.
    raw = pf * (0.5 + hit)
    return max(F_MIN, min(F_MAX, _shrink(raw, windows, PRIOR_WINDOWS)))


def strategy_weights(journal, strategies: list, regime: str) -> dict:
    """{strategy_id: weight} for the currently eligible set.

    Never drops a member: this weighs the set, it does not select within it.
    """
    out: dict = {}
    for st in strategies or []:
        sid = getattr(st, "id", None) or (
            st.get("id") if isinstance(st, dict) else None)
        if not sid:
            continue
        try:
            trades = journal.trades_for_strategy(sid)
        except Exception as e:
            log.debug(f"blend: no trades for {sid}: {e}")
            trades = []
        prov = getattr(st, "provenance", None)
        if prov is None and isinstance(st, dict):
            prov = st.get("provenance")
        ev = (prov or {}).get("regime_evidence") if isinstance(prov, dict) else None
        w = performance_multiplier(trades) * regime_multiplier(ev, regime)
        out[sid] = round(max(MIN_W, min(MAX_W, w)), 4)
    return out
