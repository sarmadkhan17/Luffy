"""StrategySpec — the company's work product.

A strategy is ONE self-contained artifact: a named, falsifiable hypothesis
with its own entry logic, its own filters and its OWN EXITS. It replaces
`Genome`, whose family+params shape forced every scraped idea into one of
eight templates (harvester.py:299-315 literally instructed the extraction LLM
to "map it to the NEAREST family") and read exit geometry from global config,
so every strategy ever tested was judged at one arbitrary risk/reward.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

TIMEFRAMES = {"5m", "15m", "1h", "4h"}
DIRECTIONS = {"long", "short", "both"}
REGIMES = {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"}
MARKETS = {"spot", "futures"}

STOP_KINDS = {"atr", "pct", "swing"}
TARGET_KINDS = {"atr", "rr", "pct", "none"}
TRAIL_KINDS = {"none", "atr"}

#: names the old pipeline auto-generated; a spec must be named, not derived
_AUTONAME = re.compile(r"variant \(|harvested$|^\w+ variant", re.I)

MIN_THESIS = 80
MIN_INVALIDATION = 30


@dataclass
class ExitSpec:
    stop: dict = field(default_factory=lambda: {"kind": "atr", "mult": 2.0})
    target: dict = field(default_factory=lambda: {"kind": "rr", "v": 2.0})
    trail: dict = field(default_factory=lambda: {"kind": "none"})
    time: dict = field(default_factory=lambda: {"max_bars": 32})
    signal_exit: str = ""


@dataclass
class StrategySpec:
    id: str
    name: str
    thesis: str
    invalidation: str
    provenance: dict
    universe: dict
    timeframe: str
    direction: str
    entry_long: str
    entry_short: str
    filters: list
    exit: ExitSpec
    regime_filter: list
    markets: list
    generation: int = 0
    parent_id: str = ""
    #: derived by the compiler from the features actually used — never
    #: declared by the author, so it cannot be misreported
    data_requires: list = field(default_factory=lambda: ["ohlcv"])

    # ── serialisation ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @staticmethod
    def from_dict(d: dict) -> "StrategySpec":
        d = dict(d)
        ex = d.get("exit") or {}
        d["exit"] = ex if isinstance(ex, ExitSpec) else ExitSpec(**ex)
        return StrategySpec(**d)

    @staticmethod
    def from_json(s: str) -> "StrategySpec":
        return StrategySpec.from_dict(json.loads(s))

    # ── validation ───────────────────────────────────────────────────────
    @staticmethod
    def validate(s: "StrategySpec") -> list[str]:
        """Structural validation only. Expression validity is the DSL's job
        (`dsl.parse`), so a spec can be checked before features are loaded."""
        errs: list[str] = []
        if not (s.id or "").strip():
            errs.append("id is required")
        name = (s.name or "").strip()
        if len(name) < 4:
            errs.append("name must be a real name (>=4 chars)")
        elif _AUTONAME.search(name):
            errs.append(f"name '{name}' looks auto-generated — the Strategist "
                        f"must name the strategy")
        if len((s.thesis or "").strip()) < MIN_THESIS:
            errs.append(f"thesis must state the inefficiency "
                        f"(>={MIN_THESIS} chars)")
        if len((s.invalidation or "").strip()) < MIN_INVALIDATION:
            errs.append(f"invalidation required (>={MIN_INVALIDATION} chars)")
        if s.timeframe not in TIMEFRAMES:
            errs.append(f"timeframe '{s.timeframe}' not in {sorted(TIMEFRAMES)}")
        if s.direction not in DIRECTIONS:
            errs.append(f"direction '{s.direction}' not in {sorted(DIRECTIONS)}")
        if s.direction in ("long", "both") and not (s.entry_long or "").strip():
            errs.append("direction includes long but entry_long is empty")
        if s.direction in ("short", "both") and not (s.entry_short or "").strip():
            errs.append("direction includes short but entry_short is empty")
        bad_reg = set(s.regime_filter or []) - REGIMES
        if bad_reg:
            errs.append(f"unknown regimes: {sorted(bad_reg)}")
        if not s.markets or not set(s.markets) <= MARKETS:
            errs.append(f"markets must be a non-empty subset of {sorted(MARKETS)}")
        errs.extend(_validate_exit(s.exit))
        return errs


def _validate_exit(e: ExitSpec) -> list[str]:
    errs: list[str] = []
    if not isinstance(e, ExitSpec):
        return ["exit must be an ExitSpec"]
    if e.stop.get("kind") not in STOP_KINDS:
        errs.append(f"exit.stop kind must be one of {sorted(STOP_KINDS)}")
    elif e.stop["kind"] == "atr" and not (
            0.2 <= float(e.stop.get("mult", 0)) <= 6.0):
        errs.append("exit.stop atr mult must be in [0.2, 6.0]")
    elif e.stop["kind"] == "pct" and not (
            0.001 <= float(e.stop.get("v", 0)) <= 0.2):
        errs.append("exit.stop pct v must be in [0.001, 0.2]")
    if e.target.get("kind") not in TARGET_KINDS:
        errs.append(f"exit.target kind must be one of {sorted(TARGET_KINDS)}")
    if e.trail.get("kind") not in TRAIL_KINDS:
        errs.append(f"exit.trail kind must be one of {sorted(TRAIL_KINDS)}")
    mb = int(e.time.get("max_bars", 0) or 0)
    if not (1 <= mb <= 500):
        errs.append("exit.time.max_bars must be in [1, 500]")
    if e.target.get("kind") == "none" and e.trail.get("kind") == "none" \
            and mb > 200:
        errs.append("a spec with no target and no trail needs a tighter "
                    "max_bars (<=200)")
    return errs
