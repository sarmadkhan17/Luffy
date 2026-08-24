"""Analyst agent contract.

Every analyst measures ONE market mechanism and returns a Vote with
evidence + conviction. Analysts never veto and never decide.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.types import Side, Snapshot, Vote


class Analyst(ABC):
    name: str = "analyst"
    #: regimes where this mechanism is historically paid; orchestrator scales fit
    regime_affinity: tuple = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE")

    @abstractmethod
    def evaluate(self, snap: Snapshot) -> Vote: ...

    @staticmethod
    def _vote(name, snap, conviction: float, confidence: float,
              rationale: str, **meta) -> Vote:
        side = (Side.LONG if conviction > 0.05 else
                Side.SHORT if conviction < -0.05 else Side.FLAT)
        return Vote(agent=name, symbol=snap.symbol, side=side,
                    conviction=max(-1.0, min(1.0, conviction)),
                    confidence=max(0.0, min(1.0, confidence)),
                    rationale=rationale, meta=meta)
