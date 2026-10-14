"""Historical blending arithmetic and frozen-state compatibility ONLY.

Not used for current strategy-led decisions. These functions retain old research
and replay imports; original complete source/hashes remain in fc6c0b2 history.
Never use these values to select a new owner or authorize an exposure.
"""
from __future__ import annotations
from ..core.types import Action

STRATEGY_VOTE_WEIGHT = 0.45      # strategies speak louder than any analyst


def agreement_fraction(votes: list, sigs: list, net: float) -> float:
    """Share of directional opinions pointing the same way as `net`.

    Feeds the dynamic threshold (unanimous -> 0.18, conflicted -> 0.32) and
    the reported confidence.

    Strategy signals contribute their OWN direction. They previously entered
    the tally as `None` and were scored with `(v.conviction if v else net) *
    sign(net) > 0`, which for a None reduces to `abs(net) > 0` — always True.
    Every strategy signal therefore counted as agreement even when it pointed
    the opposite way, so ADDING an opposing signal LOWERED the bar to trade.
    """
    directional = [v.conviction for v in votes if abs(v.conviction) > 0.05]
    directional += [1.0 if s.action == Action.BUY else -1.0 for s in sigs]
    if len(directional) < 2:
        return 0.5
    sign = 1 if net >= 0 else -1
    return sum(1 for d in directional if d * sign > 0) / len(directional)


#: below this a conviction is not an opinion — the same line
#: agreement_fraction uses to decide what counts as directional
ABSTAIN_BELOW = 0.05


def net_score(votes: list, sigs: list, base_weights: dict,
              strategy_weights: dict) -> float:
    """Weighted opinion in [-1, 1]. Silence abstains.

    An analyst with no conviction used to add nothing above the line and its
    full weight below it, so seven quiet analysts dragged any score toward
    zero exactly as seven opposing ones would. With a combined analyst weight
    of 1.0 against STRATEGY_VOTE_WEIGHT 0.45, one strategy signal at its
    typical 0.6 confidence reached 0.6*0.45 / 1.45 = 0.186 — under every
    threshold the live system produces (0.22-0.47). No amount of evidence
    could let a validated strategy trade on its own, which is the opposite of
    what STRATEGY_VOTE_WEIGHT's own comment promises.

    Disagreement still counts fully. Only abstention is excused.
    """
    from ..learning.consumers import vote_confidence, evidence_factor
    num = den = 0.0
    for v in votes:
        if abs(v.conviction) <= ABSTAIN_BELOW:
            continue
        w = base_weights.get(v.agent, 0.10) * v.meta.get("acc_mult", 1.0)
        num += (v.conviction * abs(v.conviction) * vote_confidence(v) * evidence_factor(v)
                * v.meta.get("regime_fit", 1.0)) * w
        den += w
    # the SET speaks, weighted — never reduced to a single winner
    for s in sigs:
        dirn = 1.0 if s.action == Action.BUY else -1.0
        w = STRATEGY_VOTE_WEIGHT * strategy_weights.get(
            getattr(s, "strategy_id", ""), 1.0)
        num += dirn * s.confidence * w
        den += w
    return num / den if den > 0 else 0.0


def strategy_score(sigs: list, strategy_weights: dict) -> float:
    """The book's own opinion, with no analyst in it.

    Pooling the two put the one mechanism with measured edge in a weighted
    average against seven that have none. Live, on 2026-09-02, that read:

        UNI/USDT  HOLD  score=+0.2454  thr=0.2629
           signal: Donchian Breakout Trail -> BUY conf=0.60

    Donchian was firing, correctly, and the blend held it under threshold.
    With a combined analyst weight of 1.0 against STRATEGY_VOTE_WEIGHT 0.45,
    a lone signal at 0.6 confidence can reach at most 0.186 on its own, and
    even with analysts leaning the same way it lands short. The strategy had
    signalled for a week and taken zero trades.

    The analysts are not neutral ballast either: measured over 204 live
    decisions they returned -0.695% a call at the 4h horizon and won 34.6%
    (t=-3.84), losing on BOTH sides. Averaging a measured edge against a
    measured anti-edge is not diversification.
    """
    num = den = 0.0
    for s in sigs:
        dirn = 1.0 if s.action == Action.BUY else -1.0
        w = strategy_weights.get(getattr(s, "strategy_id", ""), 1.0)
        num += dirn * s.confidence * w
        den += w
    return num / den if den > 0 else 0.0



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
    import json
    getter = getattr(journal,'kv_get',lambda *a:'{}')
    frozen = json.loads(getter('legacy_frozen_strategy_weights', '{}'))
    out = {}
    for st in strategies or []:
        sid = getattr(st, 'id', None) or (st.get('id') if isinstance(st, dict) else None)
        if sid:
            out[sid] = frozen.get(regime, {}).get(sid, 1.0)
    return out


def frozen_accuracy(journal):
    import json
    return json.loads(journal.kv_get('legacy_frozen_accuracy_multipliers', '{}'))


def frozen_base(journal, regime, base=0.24):
    import json
    return json.loads(journal.kv_get('legacy_frozen_adaptive_base', '{}')).get(regime, base)


def strategy_gate(action, sigs: list) -> tuple:
    """A trade needs a strategy that says so. -> (action, veto, gated_lean)

    Measured on 204 live decisions (2026-08-24..09-02): at the 4h horizon the
    blended score returned -0.695% a call and won 34.6%, t=-3.84 — and BOTH
    sides lost there (BUY -0.335%, SELL -1.117%). Market drift can make one
    side lose; it cannot make both lose at the same horizon. That is negative
    skill, and until this gate the analyst blend could open a position with no
    strategy behind it at all.

    Analysts still weigh in: they set the score that decides WHICH of the
    proposed setups is worth taking, and they can still talk one down below
    threshold. What they may no longer do is invent a direction of their own.
    That is the shape CLAUDE.md describes — strategies own entry, analysts
    supply coins and direction — and the cutover it says is manual.

    `gated_lean` is the direction that was refused, kept so the veto is
    graded like any other prediction. Reopening this must be an evidence
    decision, not an opinion.
    """
    if action == Action.HOLD:
        return action, "", None
    if not sigs:
        return Action.HOLD, "no strategy signal", action
    if not any(s.action == action for s in sigs):
        return (Action.HOLD,
                f"no strategy agrees with {action.value} "
                f"({len(sigs)} signalled the other way)", action)
    return action, "", None
