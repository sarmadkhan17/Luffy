"""Historical import facade. No current decision selection imports this module.

Keep the established research API; frozen state is diagnostic compatibility,
not calibrated reliability. Dynamic helper arithmetic is historical only.
"""
from .legacy_blend import (MIN_W, MAX_W, F_MIN, F_MAX, PRIOR_TRADES,
    PRIOR_WINDOWS, MIN_WINDOWS, PF_CAP, _shrink, performance_multiplier,
    regime_multiplier, strategy_weights)
