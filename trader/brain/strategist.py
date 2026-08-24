"""Strategist — the evolving brain of the playbook.

Event-driven reviews (REQUIREMENTS §6): fires when ≥8 closed trades since
last review OR 7 days elapsed. For each strategy it asks DeepSeek for a
verdict (keep / mutate / retire + rationale); mutations perturb genes
within family bounds and enter paper probation again. Falls back to
deterministic verdicts when the LLM is unavailable or out of budget.

Anti-overfit: ≤6 proposals/day; every mutation carries parent_id +
generation so lineage failures can be flagged later.
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone

from ..core.journal import Journal
from ..knowledge.vault import Vault
from ..strategy.genome import FAMILY_GENE_SPECS
from .llm import BrainLLM

log = logging.getLogger(__name__)

REVIEW_TRADES_TRIGGER = 8
REVIEW_DAYS_TRIGGER = 7
MAX_MUTATIONS_PER_DAY = 6


class Strategist:
    def __init__(self, journal: Journal, cfg: dict, notifier=None):
        self.journal = journal
        self.cfg = cfg
        self.notifier = notifier
        self.llm = BrainLLM(cfg)
        self.rng = random.Random()

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

        snap = population_snapshot = self._population_report()
        prompt = self._build_prompt(snap)
        verdicts = None
        if self.llm.available:
            raw = self.llm.chat_json(prompt, deep=True)
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

    def _population_report(self) -> list[dict]:
        from ..strategy.promotion import population_snapshot
        return population_snapshot(self.journal)

    def _build_prompt(self, pop: list[dict]) -> str:
        return (
            "You are the strategy brain of an autonomous crypto trading "
            "system. Below is each live trading strategy with its measured "
            "record. For EACH strategy output a verdict.\n\n"
            "Rules:\n"
            "- mutate only when record is marginal (PF 0.9-1.15): propose ONE "
            "parameter change within reason\n"
            "- retire clear losers (PF<0.85 with ≥20 trades or ≥6 straight losses)\n"
            "- keep winners untouched ('if it earns, don't touch it')\n"
            "- never invent new parameter names\n\n"
            'Output JSON: {"verdicts":[{"id","action":"keep|mutate|retire",'
            '"param": "<name or null>", "new_value": <number or null>, '
            '"rationale":"one sentence"}]}\n\n'
            f"STRATEGIES:\n{json.dumps(pop, indent=1)}")

    # ── actions ─────────────────────────────────────────────────────────
    def _mutations_today(self) -> int:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        evts = self.journal.query(
            "SELECT COUNT(*) n FROM brain_events WHERE kind='mutate' AND ts LIKE ?",
            (f"{day}%",))
        return evts[0]["n"]

    def _apply(self, verdicts: dict[str, dict]) -> list[dict]:
        applied = []
        for sid, v in verdicts.items():
            row = next((r for r in self.journal.list_strategies()
                        if r["id"] == sid), None)
            if not row:
                continue
            action = v.get("action")
            rationale = (v.get("rationale") or "")[:300]
            if action == "retire":
                self.journal.query(
                    "UPDATE strategies SET state='retired', "
                    "retire_reason=? WHERE id=?", (rationale, sid))
                self.journal.log_brain_event("retire", sid, rationale)
                applied.append({"strategy": row["name"], "action": "retire",
                                "rationale": rationale})
            elif action == "mutate":
                if self._mutations_today() >= MAX_MUTATIONS_PER_DAY:
                    continue
                mut = self._mutate_row(row, v.get("param"),
                                       v.get("new_value"), rationale)
                if mut:
                    applied.append({"strategy": row["name"], "action": "mutate",
                                    "rationale": mut["detail"]})
                    self.journal.log_brain_event("mutate", sid, mut)
            elif action == "keep":
                self.journal.log_brain_event("keep_verdict", sid, rationale)
                applied.append({"strategy": row["name"], "action": "keep",
                                "rationale": rationale})
        return applied

    def _mutate_row(self, row: dict, param: str | None,
                    new_value, rationale: str) -> dict | None:
        spec = FAMILY_GENE_SPECS.get(row["kind"], {})
        params = json.loads(row["params"])
        if param and param in spec:
            typ, lo, hi, _def = spec[param]
            val = new_value if isinstance(new_value, (int, float)) \
                else params.get(param)
            if val is None:
                return None
            val = max(lo, min(hi, float(val)))
        else:
            numeric = [(k, s) for k, s in spec.items() if s[0] in (int, float)]
            if not numeric:
                return None
            k, s = self.rng.choice(numeric)
            typ, lo, hi, cur_def = s
            cur = float(params.get(k, cur_def))
            delta = (hi - lo) * self.rng.uniform(0.08, 0.25) * \
                self.rng.choice((-1, 1))
            val = max(lo, min(hi, round(cur + delta, 4)))
            param = k
        new_params = {**params, param: int(val) if typ is int else float(val)}

        new_id = f"{row['id']}_m{self.rng.randint(100, 999)}"
        self.journal.query(
            "INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id, f"{row['name']} ·{param}={new_params[param]}",
             row["kind"], json.dumps(new_params), "paper",
             row["description"], "mutation",
             row["hypothesis"], row["invalidation"],
             row["regime_filter"], row["markets"],
             (row["generation"] or 0) + 1, row["id"],
             datetime.now(timezone.utc).isoformat(), "{}"))
        detail = {"parent": row["id"], "child": new_id, "param": param,
                  "value": new_params[param],
                  "rationale": rationale, "state": "paper"}
        return detail

    def _fallback(self) -> list[dict]:
        """Deterministic verdicts when no LLM: mirror statistical engine."""
        from ..strategy.promotion import evaluate_population
        actions = evaluate_population(self.journal, self.notifier)
        return [{"strategy": a["strategy"], "action": a["to"], "rationale":
                 a["reason"]} for a in actions]
