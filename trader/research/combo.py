"""One searched rule: a set of AND-ed parts, both directions, one geometry.

`and` is commutative, so a combination is a SET and its identity is a
canonical hash over the sorted part keys plus the timeframe, the geometry and
the direction. Two growth paths that arrive at the same set arrive at the
same hash, are evaluated once, and count as one look — which is what makes
the ledger's count of looks a true count.

The hash carries each threshold's percentile RANK ("ret24>p90"), never its
value, so the rule keeps its identity if the percentiles are ever
re-measured; the row stores the rendered expressions that were actually
traded, so any result can be reproduced exactly.

At most ONE event part: two breakouts landing on the same bar is a rule that
never fires, and the spec's shape is one trigger plus context anyway.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..strategy import dsl
from ..strategy.geometries import GEOS
from ..strategy.spec import StrategySpec
from .vocab import Part

#: 1 trigger + 5 context, per the spec
MAX_PARTS = 6
#: bump only if the canonical form itself changes; it invalidates the ledger
SCHEMA_VERSION = 1

_THESIS = (
    "Machine-generated combination scored against a rotation of its own "
    "entries, so that market drift and exit-geometry payout odds cannot be "
    "mistaken for entry skill. Discovery evidence only; held-out markets and "
    "the later era have not been looked at.")
_INVALIDATION = (
    "Retire when the pooled profit factor falls below 0.85 over 10 trades in "
    "30 days, or when the rotation null can no longer be beaten.")


def window_key(requires) -> str:
    """What this rule needs BEYOND candles — the window it can be seen in.

    Every window carries its own control: a gate that cannot detect the
    known-good rule inside a window cannot refute a challenger inside it.
    """
    extra = sorted(r for r in (requires or ()) if r != "ohlcv")
    return "|".join(extra) if extra else "ohlcv"


def compatible(parts) -> bool:
    """Can these parts stand in one rule?"""
    parts = tuple(parts)
    if not parts or len(parts) > MAX_PARTS:
        return False
    gauges = [p.gauge for p in parts]
    if len(set(gauges)) != len(gauges):
        return False                      # two cuts of one quantity
    if sum(1 for p in parts if p.kind == "event") > 1:
        return False                      # two triggers on one bar
    return True


@dataclass(frozen=True)
class Combination:
    parts: tuple
    tf: str
    geo: str
    trigger: str = ""          # the single this grew from — recorded, not used
    round: str = "singles"     # singles | grow | seeded | control | ablation
    parent: str = ""           # parent hash, for the growth comparison

    # ── identity ─────────────────────────────────────────────────────────
    @property
    def keys(self) -> tuple:
        return tuple(sorted(p.key for p in self.parts))

    @property
    def hash(self) -> str:
        blob = json.dumps({"v": SCHEMA_VERSION, "tf": self.tf,
                           "geo": self.geo, "dir": "both",
                           "parts": list(self.keys)}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def k(self) -> int:
        return len(self.parts)

    # ── the rule ─────────────────────────────────────────────────────────
    @property
    def long(self) -> str:
        return " and ".join(p.long for p in self._ordered)

    @property
    def short(self) -> str:
        return " and ".join(p.short for p in self._ordered)

    @property
    def _ordered(self) -> tuple:
        """Rendering order: as given, so a grown rule reads parent-first."""
        return tuple(self.parts)

    @property
    def requires(self) -> tuple:
        req: set = set()
        for p in self.parts:
            req.update(dsl.data_requires(dsl.parse(p.long), dsl.parse(p.short)))
        return tuple(sorted(req or {"ohlcv"}))

    @property
    def window(self) -> str:
        return window_key(self.requires)

    def to_spec(self) -> StrategySpec:
        """A StrategySpec the existing engine can compile and score.

        `universe.include` stays empty: the search hands the evaluator its
        symbols explicitly, and a declared universe here would be a claim
        nothing has measured yet.
        """
        h = self.hash
        return StrategySpec(
            id=f"research_{h}", name=f"search {h} {self.tf} {self.geo}",
            thesis=_THESIS, invalidation=_INVALIDATION,
            provenance={"source_kind": "research", "round": self.round,
                        "parts": list(self.keys), "trigger": self.trigger,
                        "parent": self.parent},
            universe={"include": [], "exclude": []},
            timeframe=self.tf, direction="both",
            entry_long=self.long, entry_short=self.short, filters=[],
            exit=GEOS[self.geo], regime_filter=[], markets=["futures"],
            data_requires=list(self.requires))

    # ── transport ────────────────────────────────────────────────────────
    def as_dict(self) -> dict:
        return {"parts": [p.as_dict() for p in self.parts], "tf": self.tf,
                "geo": self.geo, "trigger": self.trigger,
                "round": self.round, "parent": self.parent}

    @staticmethod
    def from_dict(d: dict) -> "Combination":
        return Combination(
            parts=tuple(Part.from_dict(p) for p in d["parts"]),
            tf=d["tf"], geo=d["geo"], trigger=d.get("trigger", ""),
            round=d.get("round", "singles"), parent=d.get("parent", ""))


def subsets(c: Combination) -> list:
    """Every rule reachable by removing exactly one part — the ablation.

    A survivor must beat all of these: that is what "a part earns its place"
    means, and it is how ties go to the simpler rule.
    """
    if c.k < 2:
        return []
    out = []
    for i in range(c.k):
        parts = c.parts[:i] + c.parts[i + 1:]
        out.append(Combination(parts=parts, tf=c.tf, geo=c.geo,
                               trigger=c.trigger, round="ablation",
                               parent=c.hash))
    return out
