"""Strategy judging + Brain-Judge — where Luffy's brain picks the book.

Two layers live here:

1. StrategyJudge — the dual-judge gauntlet gate. Cheap Yahoo pre-filter
   and internal sanity run first; only survivors earn REAL TradingView
   Strategy Tester runs through the browser harness (budget-aware).
   Harness down → Yahoo policy verdict stands alone, system unblocked.

2. BrainJudge — DeepSeek reviews candidate + population dossiers and
   DECIDES the book: what stays paper, what promotes, what dies. Hard
   statistical guardrails are non-negotiable (the brain cannot bypass
   probation gates — it chooses among eligible actions only). Every
   judgment journaled as 'brain_judgement' with full reasoning; weekly
   meta-review answers the operator's standing questions (right
   direction? better script available? keep or kill?) into the vault.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from .llm import BrainLLM
from .pine import forge_and_store
from .tv import TVClient
from .tv_harness import TVHarness, evaluate_manifest

log = logging.getLogger(__name__)

MAX_ACTIVE_DEFAULT = 6


class StrategyJudge:
    def __init__(self, journal, cfg: dict, notifier=None):
        self.journal = journal
        self.cfg = cfg
        s = cfg.get("strategies", {})
        self.tv = TVClient(
            interval=s.get("tv_interval", "1h"),
            period=s.get("tv_period", "1y"),
            min_oos_trades=int(s.get("tv_min_oos_trades", 5)),
            require_positive_oos=bool(
                s.get("tv_require_positive_oos", True)))
        self.harness = TVHarness(journal, cfg)
        self.enabled = bool(cfg.get("tv_harness", {}).get("enabled", True))
        self.notifier = notifier
        self._llm = None               # lazily built; refine_with_llm
                                       # self-guards on availability+budget

    def _get_llm(self):
        if self._llm is None:
            try:
                from .llm import BrainLLM
                self._llm = BrainLLM(self.cfg)
            except Exception as e:
                log.warning(f"brain llm unavailable for pine refine: {e}")
                self._llm = False
        return self._llm or None

    def prefilter(self, genome) -> tuple[bool, dict]:
        """Stage 1 (cheap): Yahoo walk-forward pre-filter."""
        yv = self.tv.walk_forward("BTC/USDT", genome.family)
        return bool(yv.get("valid")), {
            "stage": "yahoo_prefilter",
            "verdict": yv.get("verdict"),
            "checks": yv.get("checks"),
            "oos_return_pct": yv.get("oos_return_pct"),
            "oos_trades": yv.get("oos_trades"),
            "oos_sharpe": yv.get("oos_sharpe"),
            "beats_buy_hold": yv.get("beats_buy_hold")}

    def final_verdict(self, genome) -> tuple[bool, dict]:
        """Stage 3 (expensive): the REAL TradingView tester, which is the only
        judge that sees the genome's actual genes.

        Fails CLOSED. This used to end in `return self.prefilter(genome)`,
        so a disabled harness, an exhausted budget, a 'down' health state, a
        malformed tester read, or any exception all silently handed the
        verdict to the Yahoo family-proxy — a stock TradingView strategy that
        never sees g.params. That proxy could single-handedly accept a
        strategy. It is advisory only; it can no longer approve anything.
        """
        if self.enabled:
            health = self.harness.health()
            reserve = 5                     # leave room for fold variants
            budget_ok = (not getattr(self.harness, "budget_enabled", True)
                         or health["runs_today"] + reserve <=
                         health["budget"])
            if health["state"] != "down" and budget_ok:
                try:
                    manifest = forge_and_store(genome, self.journal,
                                               llm=self._get_llm())
                    rv = evaluate_manifest(self.harness, manifest,
                                           min_oos_trades=int(
                                               self.cfg.get("strategies", {})
                                               .get("tv_min_oos_trades", 5)))
                    if "reason" not in rv or rv.get("valid"):
                        return bool(rv.get("valid")), \
                            {"stage": "real_tv", **rv}
                    reason = f"real-TV inconclusive: {rv['reason']}"
                except Exception as e:
                    reason = f"real-TV pipeline error: {e}"
            else:
                reason = (f"harness unavailable "
                          f"(state={health['state']}, budget_ok={budget_ok})")
        else:
            reason = "tv_harness disabled in config"

        log.warning(f"{reason} — no verdict (advisory Yahoo prefilter "
                    f"cannot approve a strategy)")
        _, advisory = self.prefilter(genome)
        return False, {"stage": "no_verdict", "reason": reason,
                       "yahoo_advisory": advisory}

    def judge(self, genome) -> tuple[bool, dict]:
        """One-shot convenience: advisory prefilter, then the real verdict.

        The prefilter no longer REJECTS. It maps a family to a stock
        TradingView strategy and never sees the genome's params, so it cannot
        distinguish two strategies of the same family — using it as a gate
        killed candidates on evidence about a different system entirely. Its
        opinion is attached to the verdict for context instead.

        (No production caller today; the harvester composes the stages
        itself via prefilter() + run_gauntlet() + final_verdict().)
        """
        _, advisory = self.prefilter(genome)
        ok, ev = self.final_verdict(genome)
        return ok, {**ev, "yahoo_advisory": advisory}


class BrainJudge:
    def __init__(self, journal, cfg: dict, notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.llm = BrainLLM(cfg)
        self.notifier = notifier
        s = cfg.get("strategies", {})
        self.max_active = int(s.get("max_active", MAX_ACTIVE_DEFAULT))
        self.probation_trades = int(s.get("paper_probation_trades", 15))
        self.min_winrate = float(s.get("paper_min_winrate", 0.40))
        self.min_pf = float(s.get("paper_min_profit_factor", 1.15))

    def _promotion_earned(self, row: dict) -> bool:
        """HARD GUARDRAIL: the brain may only promote strategies whose
        closed-trade record already meets probation thresholds. It picks
        among the eligible; it never bends the gates."""
        n = row.get("live_trades") or 0
        wr = row.get("win_rate")
        st = row.get("stats") or {}
        pf = st.get("profit_factor")
        if n < self.probation_trades or wr is None:
            return False
        return wr >= self.min_winrate or (
            isinstance(pf, (int, float)) and pf >= self.min_pf)

    # ── dossier assembly ────────────────────────────────────────────────
    @staticmethod
    def _expected_winrate(row: dict) -> float | None:
        """The out-of-sample win rate this strategy was admitted on.

        Without it the reviewer sees "0 wins from 3" with nothing to weigh it
        against, and a fixed threshold reads an ordinary run of losses as a
        broken mechanism.
        """
        try:
            prov = (json.loads(row["spec_json"] or "{}")
                    .get("provenance") or {})
        except Exception:
            return None
        wr = prov.get("expected_winrate")
        return float(wr) if isinstance(wr, (int, float)) else None

    def _health_of(self, row: dict, wins: int, n: int) -> dict | None:
        """Is the live record still consistent with the admitted envelope?"""
        exp = self._expected_winrate(row)
        if exp is None:            # legacy genomes carry no envelope
            return None
        from ..strategy.health import assess_health
        h = assess_health(exp, wins, max(0, n - wins))
        return {"verdict": h.verdict, "expected_winrate": exp,
                "observed_winrate": round(h.observed_winrate, 4),
                "p_underperform": round(h.p_underperform, 4),
                "trades": h.trades, "summary": h.summary}

    def _strategy_rows(self) -> list[dict]:
        out = []
        for r in self.journal.list_strategies(["paper", "active", "demoted"]):
            t = self.journal.query(
                "SELECT COUNT(*) n, "
                "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) w, "
                "SUM(realized_pnl) pnl FROM trades "
                "WHERE strategy_id=? AND status='closed'", (r["id"],))[0]
            n, wins = int(t["n"] or 0), int(t["w"] or 0)
            out.append({
                "id": r["id"], "name": r["name"], "state": r["state"],
                "kind": r["kind"], "origin": r["origin"],
                "hypothesis": (r["hypothesis"] or "")[:160],
                "stats": json.loads(r["stats_json"] or "{}"),
                "live_trades": n, "win_rate": round(wins / n, 2) if n else None,
                "pnl_usdt": round(float(t["pnl"] or 0), 2),
                "health": self._health_of(r, wins, n)})
        return out

    def _last_judgement(self) -> dict | None:
        r = self.journal.query(
            "SELECT detail, ts FROM brain_events WHERE "
            "kind='brain_judgement' ORDER BY ts DESC LIMIT 1")
        if not r:
            return None
        try:
            return json.loads(r[0]["detail"])
        except Exception:
            return None

    # ── the review ───────────────────────────────────────────────────────
    def review(self, meta_review: bool = False) -> dict:
        rows = self._strategy_rows()
        if not rows:
            return {"reviewed": False, "why": "empty population"}
        active_n = sum(1 for r in rows if r["state"] == "active")
        payload = {
            "population": rows,
            "max_active": self.max_active,
            "currently_active": active_n,
            "previous_judgement": self._last_judgement(),
            "meta_review": meta_review,
        }
        prompt = (
            "You are the trading brain of an autonomous crypto futures "
            "system. Review this strategy portfolio and DECIDE.\n"
            f"{json.dumps(payload)[:3500]}\n\n"
            "Rules: you may PROMOTE a paper strategy to active ONLY if its "
            f"record justifies it and active count would stay <= {self.max_active}. "
            "You may DEMOTE anything degrading. You may HOLD everything. "
            "Never invent ids. Cite numbers in rationales.\n"
            'Reply as one JSON object: {"decisions":[{"id","action":'
            '"promote|demote|hold","rationale"}],'
            '"direction_assessment":"one paragraph: are we going in the '
            'right direction?",'
            '"portfolio_note":"what you want next from discovery"}')
        raw = self.llm.chat_json(prompt, deep=False) if self.llm.available \
            else None
        decisions = (raw or {}).get("decisions") or []
        applied = []
        by_id = {r["id"]: r for r in rows}
        for d in decisions:
            row = by_id.get(d.get("id"))
            act = d.get("action")
            if not row or act not in ("promote", "demote"):
                continue
            cur = row["state"]
            if act == "promote" and cur == "paper" and \
                    active_n < self.max_active and \
                    self._promotion_earned(row):
                self.journal.query(
                    "UPDATE strategies SET state='active' WHERE id=?",
                    (row["id"],))
                active_n += 1
                applied.append({**d, "from": cur, "to": "active"})
            elif act == "demote" and cur in ("active", "paper"):
                self.journal.query(
                    "UPDATE strategies SET state='demoted', "
                    "retire_reason=? WHERE id=?",
                    (f"brain: {str(d.get('rationale'))[:150]}", row["id"]))
                applied.append({**d, "from": cur, "to": "demoted"})
        judgement = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "meta_review": meta_review,
            "decisions_applied": applied,
            "direction": (raw or {}).get("direction_assessment", ""),
            "portfolio_note": (raw or {}).get("portfolio_note", ""),
            "used_llm": raw is not None,
        }
        self.journal.log_brain_event("brain_judgement", "brain", judgement)
        if applied and self.notifier:
            summary = "; ".join(f"{a['id'][:12]}→{a['to']}" for a in applied)
            self.notifier.send(f"🧠 Brain judged the book: {summary}")
        if meta_review and judgement["direction"]:
            try:
                from ..knowledge.vault import Vault
                Vault(self.journal).incident_note(
                    f"Meta-review {judgement['ts'][:10]}",
                    f"**Direction:** {judgement['direction']}\n\n"
                    f"**Discovery ask:** {judgement['portfolio_note']}")
            except Exception:
                pass
        log.info(f"brain judge: {len(applied)} applied "
                 f"(llm={judgement['used_llm']})")
        return {"reviewed": True, "applied": len(applied),
                "meta": meta_review}
