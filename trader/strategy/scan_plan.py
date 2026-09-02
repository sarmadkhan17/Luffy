"""Which markets get scanned is a question the strategies answer.

The cycle scanned three majors plus the top alts by quote volume, at one
fixed execution timeframe, whatever the book happened to contain. Every spec
already carried a `universe` block — include, exclude, min_volume_usdt — and
a `timeframe`, and no code read either.

That is the coupling that makes a second strategy a kernel change. A spec
that wants gold, or 1h bars, or only the twenty deepest perps, should say so
and be scanned accordingly. The plan is a union over whatever is live, so
adding a strategy widens the scan and retiring one narrows it, with nothing
else to edit.

Precedence, per strategy:
  a non-empty `include` IS the universe  >  `exclude`  >  liquidity floor

`include` beats the liquidity floor because naming a symbol is a decision,
not a suggestion; `exclude` beats `include` because a refusal should be
impossible to lose by accident. One strategy's exclusion never vetoes another
strategy's symbol — that is what per-strategy membership is for.

A non-empty `include` DEFINES the universe rather than adding to it. This
read `(liquid | include) - exclude`, so a spec that named sixteen symbols was
also handed every venue candidate clearing its volume floor, and there was no
way to say "only these". That is not a preference lost, it is the validated
universe lost: Donchian Breakout Trail is admitted on cross-symbol evidence
measured over its declared sixteen, and on nineteen comparable liquid perps
it has never been scored against the same rule reads median PF 0.93 and a
median null percentile of 53% — the no-edge line. Live on 2026-09-02 the
kernel was scanning it over 23 symbols including XAU, XAG, SAMSUNG and
SKHYNIX.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScanPlan:
    """The union of what every live strategy asked for."""
    symbols: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    #: strategy id -> the symbols that strategy actually wants
    by_strategy: dict = field(default_factory=dict)

    def wants(self, strategy_id: str, symbol: str) -> bool:
        """Did this strategy ask for this market?

        A strategy must never be evaluated on a market it refused: the scan
        is a union, so it contains symbols that some OTHER strategy wanted.
        """
        return symbol in self.by_strategy.get(strategy_id, frozenset())


def _universe_of(spec) -> dict:
    u = getattr(spec, "universe", None)
    return u if isinstance(u, dict) else {}


def plan_scan(specs, candidates, volumes: dict) -> ScanPlan:
    """Build the scan plan for `specs` over `candidates`.

    `volumes` is {symbol: 24h quote volume}. A symbol with no reading does
    NOT pass a liquidity floor — absent data is not evidence of depth, the
    same rule the feature layer applies to NaN.
    """
    symbols: set = set()
    tfs: set = set()
    by_strategy: dict = {}
    for spec in specs:
        u = _universe_of(spec)
        include = set(u.get("include") or ())
        exclude = set(u.get("exclude") or ())
        floor = float(u.get("min_volume_usdt") or 0.0)
        liquid = {s for s in candidates
                  if float(volumes.get(s, 0.0) or 0.0) >= floor
                  and s in volumes}
        # a spec that named symbols gets those symbols and no others; one
        # that named none keeps the liquidity-filtered venue candidates, so
        # the legacy genomes behave exactly as before
        mine = (include if include else liquid) - exclude
        by_strategy[spec.id] = frozenset(mine)
        symbols |= mine
        tf = getattr(spec, "timeframe", None)
        if tf:
            tfs.add(tf)
    return ScanPlan(symbols=tuple(sorted(symbols)),
                    timeframes=tuple(sorted(tfs)),
                    by_strategy=by_strategy)
