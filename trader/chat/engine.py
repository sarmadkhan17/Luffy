"""Chat brain — conversational access to everything Luffy knows.

Safety model (non-negotiable):
- OPS COMMANDS (freeze/halt/resume/close-all/panic) are matched by
  deterministic patterns and executed directly. The LLM is NEVER allowed
  to trigger them.
- The LLM only ever ANSWERS questions, grounded in a situation brief built
  from the journal. It may not invent numbers; every claim should cite
  what's in the brief or say it doesn't know.
"""
from __future__ import annotations

import json
import logging
import re

from ..core.journal import Journal
from ..brain.llm import BrainLLM
from .agent import AnalystAgent

log = logging.getLogger(__name__)

OPS_PATTERNS = [
    (r"\b(panic|flatten|close\s*all|close\s*everything)\b", "panic"),
    (r"\bfreeze\b|\bno new trades\b|stop.{0,12}(entries|trading)", "frozen"),
    (r"\bhalt\b|(everything|all)\s+stop", "halted"),
    (r"\b(resume|activate|full autonomy|go live)\b", "active"),
]


def detect_ops(message: str) -> str | None:
    m = message.lower()
    for pat, action in OPS_PATTERNS:
        if re.search(pat, m):
            return action
    return None


class ChatEngine:
    def __init__(self, journal: Journal, cfg: dict):
        self.journal = journal
        self.llm = BrainLLM(cfg)
        self.agent = AnalystAgent(
            journal, self.llm,
            max_steps=int(cfg.get("chat", {}).get("max_steps", 5)))

    # ── grounding ───────────────────────────────────────────────────────
    def situation_brief(self) -> str:
        j = self.journal
        eq = j.query("SELECT * FROM equity ORDER BY ts DESC LIMIT 1")
        opens = j.open_trades()
        day_stats = j.query(
            "SELECT "
            " SUM(executed=1 AND action!='HOLD') taken,"
            " SUM(executed=0 AND action!='HOLD') skipped,"
            " SUM(action='HOLD') holds FROM decisions WHERE ts LIKE ?",
            (datetime_today(),))
        pnl_today = j.query(
            "SELECT COALESCE(SUM(realized_pnl),0) s FROM trades "
            "WHERE closed_at LIKE ?", (datetime_today(),))[0]["s"]
        recent = j.query("""
            SELECT d.ts,d.symbol,d.action,d.score,d.executed,d.skip_reason,
                   GROUP_CONCAT(v.agent||' '||printf('%+.2f',v.conviction),' · ') votes
            FROM decisions d LEFT JOIN votes v ON v.cycle_id=d.cycle_id
            WHERE d.action!='HOLD' GROUP BY d.id ORDER BY d.ts DESC LIMIT 5""")
        strategies = j.query(
            "SELECT name,state,kind,params FROM strategies "
            "WHERE state IN ('paper','active','demoted') LIMIT 8")
        agents = j.agent_accuracy(since_hours=336)
        brief = {
            "control_state": j.kv_get("control_state", "ACTIVE"),
            "equity": eq[0]["equity"] if eq else None,
            "open_positions": [
                {k: t[k] for k in ("symbol", "side", "amount",
                                   "entry_price", "strategy_name")}
                for t in opens],
            "today": {"taken": day_stats[0]["taken"],
                      "skipped": day_stats[0]["skipped"],
                      "holds": day_stats[0]["holds"],
                      "realized_pnl": round(pnl_today, 2)},
            "recent_signals": recent,
            "strategies": [dict(r) for r in strategies],
            "agent_accuracy_4h": [
                {"agent": a["agent"], "n": a["n"],
                 "acc": round(a["accuracy"] or 0, 2)} for a in agents],
        }
        return json.dumps(brief, default=str)

    def ask(self, message: str, history: list[dict] | None = None) -> str:
        prompt = (
            "You are Luffy, an autonomous crypto trading system's voice. "
            "Answer the operator's question using ONLY the SITUATION data "
            "below — cite concrete numbers from it. If something isn't in "
            "the data, say you don't know. Be concise (≤120 words), direct, "
            "slightly witty, never give financial advice beyond reporting "
            "what the system does.\n\n"
            f"SITUATION:\n{self.situation_brief()}\n\n")
        if history:
            prompt += "RECENT CONVERSATION:\n" + "\n".join(
                f"{h['who']}: {h['text'][:300]}" for h in history[-6:]) + "\n\n"
        prompt += f"OPERATOR ASKS: {message}"

        answer = self.llm.chat(prompt, deep=False)
        if not answer:
            return ("Brain offline (no budget or API error). "
                    "Ops still work: try 'freeze', 'close all', 'briefing'.")
        return answer.strip()

    # ── entrypoint ──────────────────────────────────────────────────────
    def handle(self, message: str, history: list[dict] | None = None,
               do_ops: bool = True) -> str:
        ops = detect_ops(message) if do_ops else None
        if ops == "panic":
            self.journal.kv_set("panic_requested", "1")
            return ("🚨 Panic queued. I flatten every position next cycle "
                    "(~60s) and go FROZEN.")
        if ops == "frozen":
            self._set_state("FROZEN", "chat")
            return ("🥶 FROZEN — no new entries. Open positions keep being "
                    "managed to their natural close.")
        if ops == "halted":
            self._set_state("HALTED", "chat")
            return "😴 HALTED. Exchange-native stops stay armed."
        if ops == "active":
            self._set_state("ACTIVE", "chat")
            return "🙂 ACTIVE — full autonomy restored."
        return self.agent.run(message, history)

    def _set_state(self, state: str, actor: str) -> None:
        from ..engine.state import ControlStateMachine
        from ..core.types import ControlState
        sm = ControlStateMachine(self.journal)
        sm.set(ControlState(state), actor)


def datetime_today() -> str:
    """'2026-08-25%' — ready for SQL LIKE."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d") + "%"
