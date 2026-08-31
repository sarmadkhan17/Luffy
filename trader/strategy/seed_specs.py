"""Seed specs — the eight legacy families, ported.

These exist so the equivalence harness has something to compare and so the
book is never empty. They are NOT the interesting output of this system: the
authored strategy library is. Each carries its OWN exit geometry rather than
inheriting the global 2.5/4.5/32 that every strategy was previously judged at,
and each states its own inefficiency instead of the seed's copy-pasted one.

Ports that are approximate say so in `provenance.note` — an honest delta beats
a silent one.
"""
from __future__ import annotations

import json

from ..core.config import ROOT
from .spec import StrategySpec

SEED_DIR = ROOT / "data" / "seed_specs"


def load_seed_specs() -> list[StrategySpec]:
    if not SEED_DIR.exists():
        return []
    return [StrategySpec.from_dict(json.loads(p.read_text()))
            for p in sorted(SEED_DIR.glob("*.json"))]
