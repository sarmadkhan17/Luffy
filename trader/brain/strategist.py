"""Strategist (legacy) — LLM verdicts over the legacy genome book.

Event-driven reviews (REQUIREMENTS §6): fires when ≥8 closed trades since
last review OR 7 days elapsed. For each LIVE legacy genome it asks DeepSeek
for a verdict (keep / retire + rationale). Falls back to the deterministic
promotion engine when the LLM is unavailable or out of budget.

It CREATES NOTHING. It used to answer a "mutate" verdict by inserting a
perturbed child straight into `paper`, and to run the legacy Proposer and its
invention pass — three population-writing paths beside the one the firm
allows (Scraper queues → spec_writer writes a spec → Analyst admits). On
2026-09-11 a mutate verdict on a RETIRED genome (strat_606048ec95, live PF
0.168) produced a trade-eligible child with no backtest and stats={}, and the
next cycle opened 7 correlated shorts on it. The kernel no longer runs this
review; the restrictions below hold if anything wires it back in.

It never sees or touches a spec (`kind='spec'`) — the Analyst's rolling gate
owns those — nor a retired row.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.journal import Journal
from ..knowledge.vault import Vault
from .llm import BrainLLM

log = logging.getLogger(__name__)

REVIEW_TRADES_TRIGGER = 8
REVIEW_DAYS_TRIGGER = 7
#: the only rows a legacy verdict may read or write
LIVE_STATES = ["paper", "active", "demoted"]


class Strategist:
    def __init__(self, journal: Journal, cfg: dict, notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.notifier = notifier
        self.llm = BrainLLM(cfg)

    # ── trigger ─────────────────────────────────────────────────────────
    def should_review(self) -> tuple[bool, str]:
        closed = self.journal.query(
            "SELECT COUNT(*) n FROM trades WHERE status='closed'")[0]["n"]
        last = self.journal.query(
            "SELECT ts FROM brain_events WHERE kind IN ('review_complete','skipped_review') "
            "ORDER BY id DESC LIMIT 1")
        if not last:
            return True, "no review ever run"
        since = self.journal.query(
            "SELECT COUNT(*) n FROM trades WHERE status='closed' AND closed_at > ?",
            (last[0]["ts"],))[0]["n"]
        from datetime import datetime as dt
        days = (dt.now(timezone.utc)
                - dt.fromisoformat(last[0]["ts"])).days
        if since >= REVIEW_TRADES_TRIGGER:
            return True, f"{since} closed trades since last review"
        if days >= REVIEW_DAYS_TRIGGER:
            return True, f"{days} days since last review"
        return False, (f"{since}/{REVIEW_TRADES_TRIGGER} trades, "
                       f"{days}/{REVIEW_DAYS_TRIGGER} days")

    # ── review ──────────────────────────────────────────────────────────
    def review(self) -> dict:
        should, why = self.should_review()
        if not should:
            return {"reviewed": False, "reason": why}

        snap = self._population_report()
        verdicts = None
        if self.llm.available and snap:
            raw = self.llm.chat_json(self._build_prompt(snap), deep=True)
            if raw and isinstance(raw.get("verdicts"), list):
                verdicts = {v["id"]: v for v in raw["verdicts"]
                            if isinstance(v, dict) and "id" in v}
        applied = self._apply(verdicts) if verdicts else self._fallback()

        self.journal.log_brain_event("review_complete", "strategist", {
            "why": why,
            "llm": bool(verdicts),
            "actions": applied,
            "budget_left": self.llm.budget_left(),
        })
        try:
            Vault(self.journal).run_full_refresh()
        except Exception as e:
            log.debug(f"vault refresh after review failed: {e}")
        if applied and self.notifier:
            lines = "\n".join(f"• {a['strategy']}: {a['action']} — {a['rationale'][:80]}"
                              for a in applied)
            self.notifier.send(f"🧠 Strategist review ({why}):\n{lines}")
        return {"reviewed": True, "why": why,
                "used_llm": bool(verdicts), "actions": applied}

    def _live_legacy(self) -> list[dict]:
        return [r for r in self.journal.list_strategies(LIVE_STATES)
                if r["kind"] != "spec"]

    def _population_report(self) -> list[dict]:
        from ..strategy.promotion import population_snapshot
        live = {r["id"] for r in self._live_legacy()}
        return [p for p in population_snapshot(self.journal) if p["id"] in live]

    def _build_prompt(self, pop: list[dict]) -> str:
        return (
            "You are the strategy brain of an autonomous crypto trading "
            "system. Below is each live trading strategy with its measured "
            "record. For EACH strategy output a verdict.\n\n"
            "Rules:\n"
            "- retire clear losers (PF<0.85 with ≥20 trades or ≥6 straight losses)\n"
            "- keep everything else untouched\n\n"
            'Output JSON: {"verdicts":[{"id","action":"keep|retire",'
            '"rationale":"one sentence"}]}\n\n'
            f"STRATEGIES:\n{json.dumps(pop, indent=1)}")

    # ── actions ─────────────────────────────────────────────────────────
    def _apply(self, verdicts: dict[str, dict]) -> list[dict]:
        rows = {r["id"]: r for r in self._live_legacy()}
        applied = []
        for sid, v in verdicts.items():
            row = rows.get(sid)
            if not row:
                continue        # a spec, a retired row, or an id the LLM made up
            action = v.get("action")
            rationale = (v.get("rationale") or "")[:300]
            if action == "retire":
                with self.journal._tx() as c:
                    c.execute("UPDATE strategies SET state='retired', "
                              "retire_reason=?, state_changed_at=? WHERE id=?",
                              (rationale,
                               datetime.now(timezone.utc).isoformat(), sid))
                self.journal.log_brain_event("retire", sid, rationale)
                applied.append({"strategy": row["name"], "action": "retire",
                                "rationale": rationale})
            elif action == "keep":
                self.journal.log_brain_event("keep_verdict", sid, rationale)
                applied.append({"strategy": row["name"], "action": "keep",
                                "rationale": rationale})
            elif action:
                log.info(f"strategist: refused '{action}' verdict on {sid} — "
                         f"only keep/retire are applied")
        return applied

    def _fallback(self) -> list[dict]:
        """Deterministic verdicts when no LLM: mirror statistical engine."""
        from ..strategy.promotion import evaluate_population
        actions = evaluate_population(self.journal, self.notifier)
        return [{"strategy": a["strategy"], "action": a["to"], "rationale":
                 a["reason"]} for a in actions]
