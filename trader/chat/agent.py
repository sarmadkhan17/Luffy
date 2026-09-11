"""Bounded tool-calling analyst loop. Read-only; never triggers ops."""
from __future__ import annotations

import json
import logging

from ..core.journal import Journal
from . import tools as T

log = logging.getLogger(__name__)

SYSTEM = (
    "You are Luffy, a sharp, concise crypto trading analyst embedded in an "
    "autonomous trading system. Answer the operator's questions about the "
    "system's own trading using the tools to fetch real data. Cite concrete "
    "numbers from tool results — never invent figures. If the data doesn't "
    "answer it, say so. You are read-only: you cannot place, freeze, or close "
    "trades. Be direct, a little witty, no generic financial advice.")

FALLBACK = ("Brain offline (no budget or API error). Ops still work: try "
            "'freeze', 'close all', or ask again later.")


class AnalystAgent:
    def __init__(self, journal: Journal, llm, max_steps: int = 5):
        self.journal = journal
        self.llm = llm
        self.max_steps = max_steps

    def run(self, message: str, history: list[dict] | None = None) -> str:
        messages = [{"role": "system", "content": SYSTEM}]
        for h in (history or [])[-6:]:
            role = "assistant" if h.get("who") == "Luffy" else "user"
            messages.append({"role": role,
                             "content": (h.get("text") or "")[:400]})
        messages.append({"role": "user", "content": message})

        for _ in range(self.max_steps):
            msg = self.llm.chat_tools(messages, T.TOOL_SCHEMAS, purpose="chat")
            if msg is None:
                return FALLBACK
            calls = getattr(msg, "tool_calls", None)
            if not calls:
                return (msg.content or "").strip() or "…"
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name,
                                  "arguments": c.function.arguments}}
                    for c in calls]})
            for c in calls:
                result = self._exec(c.function.name, c.function.arguments)
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": json.dumps(result, default=str)})

        # ran out of steps — force a final answer without tools
        msg = self.llm.chat_tools(
            messages + [{"role": "user",
                         "content": "Answer now with what you have."}], [],
            purpose="chat")
        text = (getattr(msg, "content", None) or "").strip() if msg else ""
        return text or FALLBACK

    def _exec(self, name: str, raw_args: str):
        fn = T.TOOLS.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        try:
            args = json.loads(raw_args) if raw_args else {}
        except Exception:
            args = {}
        try:
            return fn(self.journal, **args)
        except Exception as e:
            log.warning(f"tool {name} failed: {e}")
            return {"error": str(e)}
