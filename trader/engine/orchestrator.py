"""Strategy-led decisions with typed analyst evidence and existing market guards.

No analyst or strategy vote counts, weighted signal netting, confidence-based
owner ranking, or legacy meta-model sizing. Unresolved owners/conflicts HOLD.
Risk and Execution remain the final exposure authorities.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..agents.base import Analyst
from ..agents.regime import classify
from ..core.journal import Journal
from ..core.types import (Action, Decision, Side, Snapshot, StrategySignal,
                          Vote, new_id)
from ..strategy import library as strat_lib
from ..strategy.plan_owner import select

log = logging.getLogger(__name__)

def regime_allows(genome, regime: str) -> bool:
    """Is this strategy eligible in `regime`?

    An EMPTY regime_filter means every regime, not none. The gate read
    `regime not in genome.regime_filter`, which an empty frozenset always
    satisfies, so a spec written with `regime_filter: []` — how both the
    Strategist and a hand-authored spec say "no restriction" — was skipped
    before its evaluator was ever called, in every regime.

    Every legacy genome carries a non-empty filter, which is why this went
    unnoticed until a spec was the only thing in the book.
    """
    rf = getattr(genome, "regime_filter", None)
    if not rf:
        return True
    return regime in rf

def symbol_allows(genome, symbol: str) -> bool:
    """Is this strategy allowed to speak about this market?

    The scan is a UNION over what every live strategy asked for, so it
    contains symbols some other strategy wanted. `ScanPlan.wants()` was
    written to enforce per-strategy membership and had zero callers, so a
    spec was evaluated on every symbol in the union — which is how the one
    strategy with cross-symbol evidence over sixteen declared markets came to
    be scanned over twenty-three, tokenized equities included.

    An EMPTY set means EVERY symbol, not none — the same trap the regime
    filter fell into, where `regime_filter: []` silently stopped the book
    from trading at all.
    """
    explicit = getattr(genome, 'eligible_symbols', None)
    if explicit is not None:
        return _base_pair(symbol) in {_base_pair(s) for s in explicit}
    want = getattr(genome, "symbols", None)
    if not want:
        return True
    return _base_pair(symbol) in {_base_pair(s) for s in want}


def _base_pair(symbol: str) -> str:
    """`XAU/USDT:USDT` and `XAU/USDT` are one market. The venue quotes the
    settled form and a spec names the plain one."""
    return (symbol or "").split(":")[0]



def htf_trend_score(df_4h) -> float:
    """4h trend strength s ∈ [-1,+1]: EMA50 side × ADX-normalized slope."""
    import numpy as np
    if df_4h is None or len(df_4h) < 60:
        return 0.0
    c = df_4h["close"]
    ema50 = c.ewm(span=50, adjust=False).mean()
    dist = (float(c.iloc[-1]) - float(ema50.iloc[-1])) / (float(ema50.iloc[-1]) + 1e-12)
    slope = float(ema50.iloc[-1] - ema50.iloc[-13]) / (float(ema50.iloc[-1]) + 1e-12)
    atr = float((c.diff().abs().rolling(14).mean().iloc[-1]))
    norm = max(atr * 3 / float(c.iloc[-1]), 1e-6)      # ~how far is "far" in 4h terms
    s = np.tanh((0.7 * dist + 0.3 * slope) / norm)
    return float(max(-1.0, min(1.0, s)))


class Orchestrator:
    def __init__(self, analysts: list[Analyst], journal: Journal,
                 base_threshold: float = 0.24,
                 cfg: dict | None = None):
        from ..observability.diagnostics import Receipts
        self._receipts_type = Receipts
        attention = (cfg or {}).get("attention") or {}
        self.diagnostics_enabled = attention.get("enabled") is True
        cap = attention.get("max_causes", 64)
        self.diagnostics_cap = cap if type(cap) is int and 1 <= cap <= 256 else 64
        self.analysts = {a.name: a for a in analysts}
        self.journal = journal
        self.base_threshold = base_threshold
        sc = ((cfg or {}).get("scouts") or {})
        self.htf_soft_bump = float(sc.get("htf_soft_bump", 0.07))
        self.htf_hard_veto = float(sc.get("htf_hard_veto", 0.80))
        self._shadow_last: dict[str, float] = {}
        self.shadow_sample_rate: float = 0.15

    def decide(self, snap: Snapshot, population: list[tuple],
               entry_allowed: bool = True,
               blocked_reason: str = "", *, measurements: bool = True,
               persist_cooldown: bool = True) -> Decision:
        receipts = self._receipts_type(self.diagnostics_enabled, self.diagnostics_cap)
        cycle_id = new_id("cyc")
        regime_info = classify(snap.df("15m"), snap.df("1h"))
        snap.regime = regime_info["regime"]
        snap.adx = regime_info["adx"]

        # ── guards ───────────────────────────────────────────────────────
        htf = htf_trend_score(snap.df("4h"))

        # ── collect votes ────────────────────────────────────────────────
        votes: list[Vote] = []
        for name, analyst in (self.analysts.items() if measurements else ()):
            try:
                v = analyst.evaluate(snap)
            except Exception as e:
                receipts.add("analyst", name, "evaluation_failed", e)
                log.warning(f"analyst {name} failed on {snap.symbol}: {e}")
                continue
            if v is None:
                receipts.add("analyst", name, "returned_none")
                continue
            try:
                packet = v.measurement
                if packet is None or (packet.analyst, packet.instrument, packet.observed_at) != (name, snap.symbol, snap.ts):
                    raise ValueError('typed analyst evidence missing or identity mismatch')
                # Decision consumes the packet, never an independent legacy opinion.
                v.conviction = packet.strength if packet.strength is not None else 0.0
                v.confidence = packet.uncertainty['confidence'] if packet.strength is not None else 0.0
                v.side = Side.LONG if v.conviction > .05 else Side.SHORT if v.conviction < -.05 else Side.FLAT
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                receipts.add('analyst', name, 'typed_evidence_unavailable', exc)
                log.warning('analyst %s has unavailable typed evidence on %s', name, snap.symbol)
                continue
            receipts.add("analyst", name, "emitted_vote")
            v.meta['evidence_authority'] = 'MEASUREMENT_ONLY'
            v.meta['htf'] = round(htf, 3)
            from ..learning.consumers import govern_vote
            govern_vote(self.journal, v, snap.symbol, snap.regime,
                        getattr(analyst, 'evidence_timeframe', None) or v.meta.get('horizon'))
            votes.append(v)

        sigs: list[StrategySignal] = []
        for st, genome in population:
            if not st.is_trade_eligible:
                receipts.add("strategy", st.id, "ineligible_state")
                continue
            if snap.market_type not in genome.markets:
                receipts.add("strategy", st.id, "ineligible_market")
                continue
            if not regime_allows(genome, snap.regime):
                receipts.add("strategy", st.id, "ineligible_regime")
                continue
            # a strategy may only speak about the markets it was validated on
            if not symbol_allows(genome, snap.symbol):
                receipts.add("strategy", st.id, "ineligible_symbol")
                continue
            try:
                if receipts.enabled:
                    sig = strat_lib.evaluate(genome, snap, diagnostic=lambda reason, exc=None:
                                             receipts.add("strategy", st.id, reason, exc))
                else:
                    sig = strat_lib.evaluate(genome, snap)
            except Exception as e:
                receipts.add("strategy", st.id, "evaluation_failed", e)
                log.warning(f"strategy {st.id} failed on {snap.symbol}: {e}")
                continue
            if sig is not None:
                sigs.append(sig)

        # One strategy supplies the proposal. Supporters are not votes.
        selection = select(sigs)
        owner = selection['owner']
        chosen = next((s for s in sigs if s.strategy_id == owner), None)
        net = (chosen.confidence * (1 if chosen.action == Action.BUY else -1)) if chosen else 0.0
        threshold = self.base_threshold
        strat_veto = selection['basis'] if selection['status'] == 'REFUSED' else ''
        gated_lean = None

        veto_reason = ""
        if abs(htf) > 0.35 and snap.regime != "VOLATILE":
            opposing = (net < 0 < htf) or (net > 0 > htf)
            if opposing:
                if abs(htf) >= self.htf_hard_veto:
                    veto_reason = (f"4h trend {'UP' if htf > 0 else 'DOWN'} "
                                   f"s={htf:+.2f} — hard veto")
                    net = 0.0
                else:
                    threshold += self.htf_soft_bump

        if net > threshold:
            action = Action.BUY
        elif net < -threshold:
            action = Action.SELL
        else:
            action = Action.HOLD

        # NaN from indicator edge cases must never reach SQLite (it becomes
        # NULL → NOT NULL violation → crash-loop → duplicate entries)
        def _clean(x: float, default: float = 0.0) -> float:
            x = float(x)
            return default if (x != x or x in (float("inf"), float("-inf"))) \
                else round(x, 6)

        # ── signal cooldown: one decision per setup, not per minute ─────
        # A persisting condition would re-fire identical signals every
        # cycle, flooding the journal with pseudo-replicated outcomes.
        # Same-direction decisions re-arm after 45 min; opposite direction resets it.
        if action != Action.HOLD:
            try:
                last = self.journal.query(
                    "SELECT action, ts FROM decisions WHERE symbol=? "
                    "AND action!='HOLD' ORDER BY ts DESC LIMIT 1",
                    (snap.symbol,))
                if last and last[0]["action"] == action.value:
                    age_min = (datetime.now(timezone.utc)
                               - datetime.fromisoformat(last[0]["ts"])
                               ).total_seconds() / 60
                    if age_min < 45:
                        action = Action.HOLD
                        net = _clean(net)  # keep score for transparency
                        if persist_cooldown:
                            self.journal.log_control_event(
                                "signal_cooldown", "orchestrator",
                                detail={"symbol": snap.symbol,
                                        "since": last[0]["ts"][:19],
                                        "action": action.value})
            except Exception as e:
                log.debug(f"cooldown check failed: {e}")

        net = _clean(net)
        threshold = max(_clean(threshold, 0.24), 0.05)
        confidence = _clean(chosen.confidence) if chosen else 0.0
        d_ts = datetime.now(timezone.utc).isoformat()

        d = Decision(
            id=new_id("dec"), cycle_id=cycle_id, symbol=snap.symbol,
            action=action, score=_clean(net), threshold=threshold,
            confidence=confidence, ts=d_ts,
            strategy_selection=selection,
            instrument_binding_json=getattr(snap, 'instrument_binding_json', None),
            votes=[v.as_dict() for v in votes],
            strategy_signals=[vars(s) | {"action": s.action.value}
                              for s in sigs])
        d.evaluation_causes = receipts.items
        d.omitted_causes = receipts.omitted
        d.executed = False
        d.gated_lean = gated_lean            # set only when the gate fired
        skip_bits = []
        if strat_veto:
            skip_bits.append(strat_veto)
        if veto_reason:
            skip_bits.append(veto_reason)
        if action != Action.HOLD and not entry_allowed:
            skip_bits.append(blocked_reason or "entries not allowed")
        d.skip_reason = "; ".join(skip_bits)
        return d

    def journalize(self, snap: Snapshot, decision: Decision,
                   market_type: str, mode: str, *, schedule_outcomes: bool = True) -> None:
        receipt = getattr(snap, 'market_provenance_parts', None)
        if receipt is None and snap.market_provenance_json:
            import json
            receipt = json.loads(snap.market_provenance_json)
        if receipt is not None:
            self.journal.log_brain_event('market_provenance', 'market_data', dict(
                decision_id=decision.id, cycle_id=decision.cycle_id,
                receipt=receipt))
        self.journal.log_cycle(snap, decision.cycle_id, mode)
        votes = [Vote(**{**v, "side": Side(v["side"])})
                 for v in decision.votes]
        self.journal.log_votes(decision.cycle_id, decision.symbol, votes)
        self.journal.log_decision(decision)
        # every directional decision — taken OR skipped — becomes a
        # falsifiable prediction with resolved outcomes. Skipped setups are
        # the majority of evidence; without them the learning loop starves.
        if not schedule_outcomes:
            return
        lean = getattr(decision, "gated_lean", None)
        if decision.action != Action.HOLD:
            self.journal.schedule_outcome(
                decision.id, decision.cycle_id, decision.symbol,
                decision.ts, decision.action.value, snap.price)
        elif lean is not None:
            # The strategy gate turned a directional call into a HOLD. Grade
            # it anyway, always — not at the shadow sampler's 15%. If the
            # analyst blend does have edge the gate is throwing away, this
            # column is where that shows up, and the gate can be reopened on
            # evidence instead of opinion.
            self.journal.schedule_outcome(
                decision.id, decision.cycle_id, decision.symbol,
                decision.ts, lean.value, snap.price)
        else:
            self._maybe_shadow_outcome(decision, snap)

    def _maybe_shadow_outcome(self, decision: Decision, snap) -> None:
        """Retain bounded outcome observation for a justified near-threshold
        strategy lean. Refused conflicts have zero score and are never netted
        into a shadow prediction. Historical Decisions keep their old contract.
        """
        import random
        lo = 0.6 * decision.threshold
        if abs(decision.score) < lo:
            return
        now = time.time()
        last = self._shadow_last.get(decision.symbol, 0.0)
        if now - last < 5400:                     # ≥90 min per symbol
            return
        if random.random() > self.shadow_sample_rate:
            return
        self._shadow_last[decision.symbol] = now
        self.journal.schedule_outcome(
            decision.id, decision.cycle_id, decision.symbol,
            decision.ts,
            "BUY" if decision.score > 0 else "SELL",   # the lean's side
            snap.price)


def __getattr__(name):
    # Old research imports remain available, but decide() never calls them.
    if name in {'net_score', 'strategy_score', 'agreement_fraction',
                'strategy_gate', 'STRATEGY_VOTE_WEIGHT', 'ABSTAIN_BELOW'}:
        from ..strategy import legacy_blend
        return getattr(legacy_blend, name)
    raise AttributeError(name)
