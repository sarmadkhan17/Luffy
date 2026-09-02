"""Orchestrator — aggregates votes + strategy signals into one Decision.

No vetoes — except two explicit, evidence-based ones:
  1. higher-timeframe veto: strong 4h trend refuses counter-trend entries
     (soft bump at |s|>0.35, hard refusal at |s|≥htf_hard_veto)
  2. news blackout: while macro headlines churn, threshold rises

    net = Σ(conv' × |conv'|·confidence · w_agent · fit) / Σw
    where conv' is the journal-calibrated conviction (calibration.py).

Regime-fit scales each analyst's conviction; measured agent accuracy
(from validation replay + live outcomes) modulates weight.

Dynamic threshold: agreement tightens it (unanimous 0.18), conflict
loosens it (0.32). Output is always a Decision — executed or skipped
with reason.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from ..agents import calibration
from ..agents.base import Analyst
from ..agents.regime import classify, fit_multiplier
from ..core.journal import Journal
from ..core.types import (Action, Decision, Side, Snapshot, StrategySignal,
                          Vote, new_id)
from ..strategy import library as strat_lib

log = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = {"structure": 0.24, "flow": 0.15, "momentum": 0.17,
                    "value": 0.11, "rotation": 0.13,
                    "positioning": 0.11, "depth": 0.09}
STRATEGY_VOTE_WEIGHT = 0.45      # strategies speak louder than any analyst


def _measured_weights() -> tuple[dict, dict]:
    """Evidence-based weights from the validation harness, if available."""
    try:
        from ..agents.validate import load_weights
        w = load_weights()
        if w and w.get("base_weights"):
            fit = {}
            for agent, by_regime in (w.get("agents") or {}).items():
                fit[agent] = {r: (round(2 * (v["acc_1h"] - 0.5) + 0.6, 2)
                                  if v.get("acc_1h") is not None else 0.55)
                              for r, v in (by_regime.get("by_regime")
                                           or {}).items()}
            return w["base_weights"], fit
    except Exception:
        pass
    return {}, {}


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
    num = den = 0.0
    for v in votes:
        if abs(v.conviction) <= ABSTAIN_BELOW:
            continue
        w = base_weights.get(v.agent, 0.10) * v.meta.get("acc_mult", 1.0)
        num += (v.conviction * abs(v.conviction) * v.confidence
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
                 news_guard=None, cfg: dict | None = None):
        self.analysts = {a.name: a for a in analysts}
        self.journal = journal
        self.base_threshold = base_threshold
        self.news_guard = news_guard
        sc = ((cfg or {}).get("scouts") or {})
        self.htf_soft_bump = float(sc.get("htf_soft_bump", 0.07))
        self.htf_hard_veto = float(sc.get("htf_hard_veto", 0.80))
        self.calib_cfg = (sc.get("calibration") or {})
        self.ewa_cfg = (sc.get("ewa") or {})
        self.meta_cfg = (sc.get("meta") or {})
        self._adapt_cfg = (sc.get("adaptive_threshold") or {})
        self.require_strategy_signal = bool(
            sc.get("require_strategy_signal", True))
        self._hit_cache: tuple[float, dict] = (0.0, {})   # ts, regime→(hr, n)
        self._blend_cache: tuple[float, str, dict] = (0.0, "", {})
        self._acc_cache: tuple[float, dict] = (0.0, {})   # ts, {agent: mult}
        self._lock = threading.Lock()
        self._shadow_last: dict[str, float] = {}          # symbol → ts
        self.shadow_sample_rate: float = 0.15             # of leaners
        self.base_weights, self.measured_fit = {}, {}
        self.reload_weights()

    def reload_weights(self) -> None:
        """(Re)load measured weights + regime fit and renormalize.

        Merging a 5-agent measured set (Σ=1.0) over the 7-agent defaults
        left Σ=1.2 — a ~17% systematic dilution of every net score.
        Hot-reloadable so the weekly validation harness takes effect
        within one brain tick instead of requiring a restart."""
        mw, mfit = _measured_weights()
        merged = {**_DEFAULT_WEIGHTS, **mw}
        tot = sum(merged.values()) or 1.0
        merged = {k: v / tot for k, v in merged.items()}
        # EWA online aggregation: evidence-weighted blend toward the
        # per-agent exponential weights (theory: cumulative loss tracks
        # the best expert under non-stationarity)
        if self.ewa_cfg.get("enabled", True):
            try:
                from ..agents import weights_online
                merged = weights_online.blended_weights(
                    merged, prior_k=float(self.ewa_cfg.get("prior", 30.0)))
            except Exception as e:
                log.debug(f"ewa blend skipped: {e}")
        self.base_weights = merged
        self.measured_fit = mfit
        with self._lock:
            self._acc_cache = (0.0, {})
        if any(a != d for a, d in zip(sorted(self.base_weights),
                                      sorted(_DEFAULT_WEIGHTS))):
            log.info(f"orchestrator weights: "
                     f"{ {k: round(v, 3) for k, v in self.base_weights.items()} }")

    # ── accuracy → weight multiplier (bounded 0.7..1.3, evidence-scaled) ──
    def _accuracy_multipliers(self, ttl: float = 1800.0) -> dict[str, float]:
        now = time.time()
        with self._lock:
            ts, cache = self._acc_cache
            if now - ts < ttl:
                return cache
        try:
            rows = self.journal.agent_accuracy(since_hours=336)
            fresh = {}
            for r in rows:
                n, acc = int(r["n"] or 0), r["accuracy"]
                if n >= 5 and acc is not None:
                    # deviation from 1.0 scaled by how much we trust the
                    # estimate: n=5 → ±29% weight, n=60 → ±75%, n→∞ → full.
                    # Small clusters can no longer pin an agent to the floor
                    # (the old 2·acc clamp crushed everyone to 0.6 on the
                    # first 54-outcome cluster and never recovered).
                    conf = n / (n + 20.0)
                    mult = 1.0 + (2.0 * float(acc) - 1.0) * conf
                    fresh[r["agent"]] = round(max(0.7, min(1.3, mult)), 2)
        except Exception:
            fresh = {}
        with self._lock:
            self._acc_cache = (now, fresh)
        return fresh

    # ── adaptive base threshold (per-regime hit rates × vol scale) ──────
    def _adaptive_base(self, regime: str, vol_ratio: float = 1.0) -> float:
        """Regime-conditional threshold from realized 30d hit rates: when
        recent signals in this regime paid (hr>0.5) the base relaxes; when
        they lost it tightens. Falls back to the static base until
        min_outcomes labels exist for the regime. Scaled modestly by
        relative volatility expansion."""
        if not self._adapt_cfg.get("enabled", True):
            return self.base_threshold
        now = time.time()
        if now - self._hit_cache[0] > 1800:
            try:
                rows = self.journal.query(
                    "SELECT c.regime r, AVG(o.correct_4h) hr, COUNT(*) n "
                    "FROM outcomes o JOIN cycles c ON c.id=o.cycle_id "
                    "WHERE o.resolved_at IS NOT NULL "
                    "AND o.correct_4h IS NOT NULL "
                    "AND o.ts >= datetime('now','-30 days') "
                    "GROUP BY c.regime")
                self._hit_cache = (now, {x["r"]: (x["hr"], x["n"])
                                         for x in rows})
            except Exception:
                self._hit_cache = (now, {})
        hr, n = self._hit_cache[1].get(regime, (None, 0))
        if hr is None or n < int(self._adapt_cfg.get("min_outcomes", 30)):
            base = self.base_threshold
        else:
            base = self.base_threshold * (0.5 / max(float(hr), 0.35))
        base = max(0.16, min(0.34, base))
        vscale = max(0.9, min(1.15,
                              1.0 + (float(vol_ratio or 1.0) - 1.0) * 0.15))
        return round(base * vscale, 4)

    # ── weighing the active strategy set ───────────────────────────────
    def _strategy_weights(self, population: list[tuple],
                          regime: str) -> dict:
        """{strategy_id: weight} for this regime, recomputed every 30 min.

        Analysts have always entered the sum weighted by measured accuracy
        and regime fit. Strategies entered it flat, so the book averaged its
        mechanisms instead of combining them — a strategy paying in the live
        regime spoke no louder than one that had stopped working. This is
        the same treatment, from the same kind of evidence.
        """
        now = time.time()
        ts, cached_regime, cached = self._blend_cache
        if cached and cached_regime == regime and now - ts < 1800:
            return cached
        try:
            from ..strategy.blend import strategy_weights
            specs = [st for st, _g in population]
            fresh = strategy_weights(self.journal, specs, regime)
        except Exception as e:
            log.debug(f"strategy blend skipped: {e}")
            fresh = {}
        with self._lock:
            self._blend_cache = (now, regime, fresh)
        return fresh

    def decide(self, snap: Snapshot, population: list[tuple],
               entry_allowed: bool = True,
               blocked_reason: str = "") -> Decision:
        cycle_id = new_id("cyc")
        regime_info = classify(snap.df("15m"), snap.df("1h"))
        snap.regime = regime_info["regime"]
        snap.adx = regime_info["adx"]

        # ── guards ───────────────────────────────────────────────────────
        news = self.news_guard.check() if self.news_guard else \
            {"active": False, "why": ""}
        htf = htf_trend_score(snap.df("4h"))

        # ── collect votes ────────────────────────────────────────────────
        votes: list[Vote] = []
        acc_mults = self._accuracy_multipliers()
        cal_state = calibration.load() if \
            self.calib_cfg.get("enabled", True) else {}
        for name, analyst in self.analysts.items():
            try:
                v = analyst.evaluate(snap)
            except Exception as e:
                log.warning(f"analyst {name} failed on {snap.symbol}: {e}")
                continue
            if v is None:
                continue
            measured = (self.measured_fit.get(v.agent) or {}).get(snap.regime)
            v.meta["regime_fit"] = measured if measured is not None else \
                fit_multiplier(analyst.regime_affinity, snap.regime)
            v.meta["acc_mult"] = round(acc_mults.get(name, 1.0), 2)
            v.meta["htf"] = round(htf, 3)
            v.meta["news_blackout"] = bool(news.get("active"))
            raw_c = v.conviction
            new_c, did = calibration.apply(
                cal_state, v.agent, v.conviction, v.confidence,
                int(self.calib_cfg.get("min_samples", 60)))
            if did:
                v.conviction = new_c
                v.meta["raw_conviction"] = round(raw_c, 3)
                v.meta["calibrated"] = True
            votes.append(v)

        sigs: list[StrategySignal] = []
        for st, genome in population:
            if not st.is_trade_eligible:
                continue
            if snap.market_type not in genome.markets:
                continue
            if not regime_allows(genome, snap.regime):
                continue
            try:
                sig = strat_lib.evaluate(genome, snap)
            except Exception as e:
                log.warning(f"strategy {st.id} failed on {snap.symbol}: {e}")
                continue
            if sig is not None:
                sigs.append(sig)

        # ── aggregate ────────────────────────────────────────────────────
        sw = self._strategy_weights(population, snap.regime)
        net = net_score(votes, sigs, self.base_weights, sw)

        frac = agreement_fraction(votes, sigs, net)
        threshold = self._adaptive_base(snap.regime,
                                        regime_info.get("vol_ratio", 1.0)) \
            * (1.45 - 0.55 * frac)   # 0.18..0.32 band around the base

        if news.get("active"):
            threshold += 0.08
            net *= 0.75
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

        # ── a trade needs a strategy that says so ───────────────────────
        # Measured on the live journal (204 decisions, 2026-08-24..09-02):
        # at the 4h horizon the blended score returned -0.695% a call and
        # won 34.6%, t=-3.84 — and BOTH sides lost there (BUY -0.335%,
        # SELL -1.117%). Market drift can make one side lose; it cannot
        # make both lose at the same horizon. That is negative skill, and
        # the analyst blend was free to open a position with no strategy
        # behind it at all. It no longer is: analysts choose among what a
        # validated mechanism has already proposed, which is the shape
        # CLAUDE.md describes and the cutover it says is manual.
        strat_veto, gated_lean = "", None
        if self.require_strategy_signal:
            action, strat_veto, gated_lean = strategy_gate(action, sigs)

        # NaN from indicator edge cases must never reach SQLite (it becomes
        # NULL → NOT NULL violation → crash-loop → duplicate entries)
        def _clean(x: float, default: float = 0.0) -> float:
            x = float(x)
            return default if (x != x or x in (float("inf"), float("-inf"))) \
                else round(x, 6)

        # ── signal cooldown: one decision per setup, not per minute ─────
        # A persisting condition would re-fire identical signals every
        # cycle, flooding the journal with pseudo-replicated outcomes.
        # Re-arm only after 45 min, an opposite signal, or a HOLD break.
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
                        self.journal.log_control_event(
                            "signal_cooldown", "orchestrator",
                            detail={"symbol": snap.symbol,
                                    "since": last[0]["ts"][:19],
                                    "action": action.value})
            except Exception as e:
                log.debug(f"cooldown check failed: {e}")

        net = _clean(net)
        threshold = max(_clean(threshold, 0.24), 0.05)
        confidence = min(0.95, max(0.30, _clean(
            abs(net) / max(threshold, 1e-9) * 0.62 * (0.7 + 0.3 * frac), 0.3)))
        d_ts = datetime.now(timezone.utc).isoformat()

        # ── meta-labeling: the secondary model judges the primary signal ─
        # LIVE with hard bounds (operator choice): veto below META_FLOOR,
        # shrink-only sizing above it; passthrough (no meta_p) until the
        # model has ≥40 training labels or if auto-disabled. Vetoed calls
        # stay directional so their outcome grades the veto itself.
        meta_p, meta_size = 0.0, 1.0
        if action != Action.HOLD and self.meta_cfg.get("enabled", True):
            try:
                from ..brain import meta_label
                lean_buy = action == Action.BUY
                n_agree = sum(1 for s in sigs
                              if (s.action == Action.BUY) == lean_buy)
                sig_agree = (n_agree / len(sigs)) if sigs else 0.5
                feat = meta_label.features(
                    net, threshold, frac, snap.regime, htf, snap.adx,
                    d_ts, len(sigs), sig_agree, bool(news.get("active")))
                p = meta_label.judge(feat)
                if p is not None:
                    meta_p = p
                    if p < meta_label.META_FLOOR:
                        veto_reason = (f"meta: p={p:.2f} "
                                       f"< {meta_label.META_FLOOR}")
                    else:
                        meta_size = meta_label.size_mult(p)
            except Exception as e:
                log.debug(f"meta judge failed: {e}")

        d = Decision(
            id=new_id("dec"), cycle_id=cycle_id, symbol=snap.symbol,
            action=action, score=_clean(net), threshold=threshold,
            confidence=confidence, ts=d_ts, meta_p=meta_p,
            meta_size=meta_size,
            votes=[v.as_dict() for v in votes],
            strategy_signals=[vars(s) | {"action": s.action.value}
                              for s in sigs])
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
                   market_type: str, mode: str) -> None:
        self.journal.log_cycle(snap, decision.cycle_id, mode)
        votes = [Vote(**{**v, "side": Side(v["side"])})
                 for v in decision.votes]
        self.journal.log_votes(decision.cycle_id, decision.symbol, votes)
        self.journal.log_decision(decision)
        # every directional decision — taken OR skipped — becomes a
        # falsifiable prediction with resolved outcomes. Skipped setups are
        # the majority of evidence; without them the learning loop starves.
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
        """Near-threshold HOLDs are the cheapest training data: the vote
        stack leaned a direction and the 4h move grades every agent's vote
        against its own direction. Sampled (probabilistic + per-symbol
        cooldown) so persistent lean conditions can't flood the journal —
        this is what finally feeds calibration's 60-sample gate."""
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
