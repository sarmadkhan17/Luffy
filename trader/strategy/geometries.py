"""The exit geometries every screen and every search is scored under.

Geometry is a screen DIMENSION, not a constant. Screening one exit shape
asks "which entries pay under THIS exit", never "which mechanism is real" —
and an ATR multiple is meaningless without the bar it was measured on, so
these travel with the spec's own timeframe.

Two shapes, deliberately few:

- `fixed`  a 3 ATR stop and a 3R target. A fixed target amputates the tail a
           continuation mechanism lives on, which is exactly why it is kept:
           it is the control for the other one.
- `trail`  a 2 ATR stop with a 4 ATR trail armed at 1R. The shape Donchian
           Breakout Trail — the only mechanism in the book with evidence —
           was admitted under.

Defined here rather than in a script so the search and the screen cannot
drift into scoring candidates under different yardsticks.
"""
from __future__ import annotations

from .spec import ExitSpec

GEOS: dict[str, ExitSpec] = {
    "fixed": ExitSpec(stop={"kind": "atr", "mult": 3.0},
                      target={"kind": "rr", "v": 3.0},
                      trail={"kind": "none"}, time={"max_bars": 96}),
    "trail": ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "none"},
                      trail={"kind": "atr", "mult": 4.0, "arm_at_r": 1.0},
                      time={"max_bars": 500}),
}
