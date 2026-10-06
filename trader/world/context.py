"""Shared, immutable, exact-cut WorldModel queries for strategy and research.

Only retained WorldModelRecords can supply historical context. An absent cut
is UNKNOWN; a current model is never broadcast over older bars.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Horizon, Scope
from .observation import Observation, Quality, _timestamp
from .replay import WorldHistory


@dataclass(frozen=True, slots=True)
class ObservationQuery:
    as_of_ms: int
    scope: Scope
    horizon: Horizon
    kind: str
    quality: Quality
    reason: str
    model_id: str | None = None
    observations: tuple[Observation, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldContext:
    history: WorldHistory
    source_status: str = "RETAINED"
    as_of_ms: int | None = None

    def __post_init__(self):
        if not isinstance(self.history, WorldHistory):
            raise TypeError("history must be WorldHistory")
        _timestamp(self.as_of_ms, "as_of_ms", optional=True)

    @property
    def context_id(self):
        return (self.history.history_id, self.source_status, self.as_of_ms)

    def observations(self, as_of_ms: int, scope: Scope, horizon: Horizon,
                     *, kind: str) -> ObservationQuery:
        _timestamp(as_of_ms, "as_of_ms")
        if not isinstance(scope, Scope) or not isinstance(horizon, Horizon):
            raise TypeError("typed scope and horizon required")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must be nonempty")
        def unknown(reason):
            return ObservationQuery(as_of_ms, scope, horizon, kind, Quality.UNKNOWN, reason)
        if self.as_of_ms is not None and as_of_ms > self.as_of_ms:
            return unknown("QUERY_AFTER_CONTEXT_CUT")
        try:
            model = self.history.get_exact(as_of_ms)
        except LookupError:
            return unknown("HISTORICAL_CONTEXT_UNAVAILABLE:" + self.source_status)
        try:
            records = model.get_observations(scope, horizon, kind=kind)
        except (LookupError, ValueError):
            return unknown("SCOPE_OR_HORIZON_UNAVAILABLE")
        # Preserve conflicts and qualities in the typed result. A scalar
        # consumer must not choose one contradictory record or fill a null.
        quality = records[0].quality if len(records) == 1 else Quality.UNKNOWN
        reason = "EXACT" if len(records) == 1 else "MISSING_OR_AMBIGUOUS_OBSERVATION"
        if len(records) == 1 and not records[0].is_fresh_at(as_of_ms):
            quality, reason = Quality.UNKNOWN, "OBSERVATION_NOT_USABLE_AT_CUT"
        return ObservationQuery(as_of_ms, scope, horizon, kind, quality, reason,
                                model.model_id, records)


def load_context(path=None, *, as_of_ms=None) -> WorldContext:
    """Read-only retained source adapter. No latest/live/network fallback."""
    from ..observability.store import read_world_history
    if path is None:
        from ..core.config import ROOT
        path = ROOT / "data" / "attention.db"
    history, status = read_world_history(path, as_of_ms=as_of_ms)
    return WorldContext(history, status, as_of_ms)
