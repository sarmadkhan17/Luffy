"""Chat brain — conversational access to everything Luffy knows.

Safety model (non-negotiable):
- Chat is conversation only. No text — "freeze entries", "what would make
  you halt?", "should I unhalt?" — ever changes control state or queues a
  panic. Owner controls go through explicit command/UI paths into the
  kernel's Owner Interface (trader/owner/), never through chat.
- The LLM only ever ANSWERS questions, grounded in a situation brief built
  from the journal. It may not invent numbers; every claim should cite
  what's in the brief or say it doesn't know.
"""
from __future__ import annotations

import json
import logging

from ..core.journal import Journal
from ..brain.llm import BrainLLM
from .agent import FALLBACK, AnalystAgent

log = logging.getLogger(__name__)


class ChatEngine:
    def __init__(self, journal: Journal, cfg: dict):
        self.journal = journal
        self.llm = BrainLLM(cfg)
        self.agent = AnalystAgent(
            journal, self.llm,
            max_steps=int(cfg.get("chat", {}).get("max_steps", 5)))
        self.agent.query_cfg = cfg

    # Compatibility entrypoints use the same typed evidence authority.
    def situation_brief(self) -> str:
        from ..owner.queries import query
        return json.dumps(query(self.journal, 'decision', cfg=self.agent.query_cfg), default=str)

    def ask(self, message: str, history: list[dict] | None = None) -> str:
        return self.handle(message, history)

    @property
    def evidence(self) -> list[dict]:
        return list(self.agent.evidence)

    @property
    def consulted(self) -> list[dict]:
        """Tools the last handle() consulted (read-only journal reads)."""
        return list(self.agent.consulted)

    # ── entrypoint ──────────────────────────────────────────────────────
    def handle(self, message: str, history: list[dict] | None = None) -> str:
        """Answer a message. Conversation only: never an owner-control request."""
        return self.agent.run(message, history)


def datetime_today() -> str:
    """'2026-08-25%' — ready for SQL LIKE."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d") + "%"
