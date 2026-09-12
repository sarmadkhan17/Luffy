"""A horizon may only be searched if its universe can carry the test.

`consistency_p` is a Bonferroni-corrected binomial tail over per-symbol null
percentiles, so the number of symbols sets what the test can DETECT. At 8
symbols, ALL EIGHT clearing the no-edge median still scores 1.2e-02 and the
0.01 admission gate is mathematically unreachable. Measured 2026-09-11 the
store held 19 discovery symbols at 4h and 8 at 1h and 15m — so a search at
1h would have spent days producing numbers that could never pass.

This test does not check that the store is deep. It checks that what the
config ENABLES and what the store CARRIES agree.
"""
import pytest

from trader.core.config import ROOT, load_config

pytest.importorskip("pandas")


def _store_counts(tf):
    if not (ROOT / "data" / "candles.db").exists():
        pytest.skip("no candle store (data/candles.db absent)")
    from trader.research.universe import coverage
    return coverage([tf]).get(tf, {})


def test_the_universes_do_not_overlap():
    from trader.research.universe import DISCOVERY, HELDOUT
    assert not set(DISCOVERY) & set(HELDOUT)
    assert len(DISCOVERY) >= 19 and len(HELDOUT) >= 17


def test_every_enabled_horizon_carries_enough_discovery_symbols():
    cfg = load_config()
    rcfg = cfg.get("research") or {}
    horizons = rcfg.get("horizons") or []
    need = int(rcfg.get("min_discovery_symbols", 16))
    from trader.research.universe import DISCOVERY
    if not horizons:
        pytest.skip("no research horizons enabled yet")
    for tf in horizons:
        have = _store_counts(tf)
        n = sum(1 for s in DISCOVERY if have.get(s, 0) >= 500)
        assert n >= need, (
            f"{tf} is enabled for research but only {n} of "
            f"{len(DISCOVERY)} discovery symbols carry >=500 bars "
            f"(need {need}); deepen the store or drop the horizon")


def test_four_hour_is_deep_enough_today():
    """The horizon the book already trades. If this regresses, the candle
    store lost history and every Phase 2 number is suspect."""
    from trader.research.universe import DISCOVERY
    have = _store_counts("4h")
    n = sum(1 for s in DISCOVERY if have.get(s, 0) >= 500)
    assert n >= 19, f"only {n} discovery symbols at 4h"
