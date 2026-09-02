"""A strategy is only evaluated on the markets it was validated on.

Donchian Breakout Trail declares 16 symbols and its admission evidence —
median PF 1.42, cross-symbol null consistency p=3.5e-04 — was measured on
exactly those. Measured 2026-09-02 on 19 comparable liquid perps it has never
been scored against, the same rule reads median PF 0.93, median null
percentile 53% and compounds at -4.6%: the mechanism does not generalise off
its declared universe.

Two separate holes let it trade there anyway.

`plan_scan` computed `(liquid | include) - exclude`, so `include` ADDED to
the liquidity-filtered venue candidates rather than defining the universe.
There was no way for a spec to say "only these". And `ScanPlan.wants()` —
written with the docstring "A strategy must never be evaluated on a market it
refused" — had zero callers, so every spec was evaluated on the union across
the whole book. Live at 14:00 on 2026-09-02 the kernel was scanning Donchian
over 23 symbols including XAU, XAG, SAMSUNG and SKHYNIX.

This is the same class of fault as the 15m-ATR one: the thing that trades
must be the thing that was measured.
"""
import pytest

from trader.strategy.scan_plan import plan_scan


class _Spec:
    def __init__(self, sid, universe, timeframe="4h"):
        self.id, self.universe, self.timeframe = sid, universe, timeframe


CANDIDATES = ["BTC/USDT", "ETH/USDT", "XAU/USDT", "SAMSUNG/USDT", "DOGE/USDT"]
VOLUMES = {s: 5e8 for s in CANDIDATES}


def test_a_declared_include_is_the_universe_not_an_addition():
    spec = _Spec("s1", {"include": ["BTC/USDT", "ETH/USDT"],
                        "min_volume_usdt": 1e8})
    plan = plan_scan([spec], CANDIDATES, VOLUMES)
    assert set(plan.symbols) == {"BTC/USDT", "ETH/USDT"}
    for off in ("XAU/USDT", "SAMSUNG/USDT", "DOGE/USDT"):
        assert not plan.wants("s1", off)


def test_naming_a_symbol_still_beats_the_liquidity_floor():
    """`include` remains a decision, not a suggestion: a named symbol is in
    even when it would fail the spec's own volume floor."""
    spec = _Spec("s1", {"include": ["BTC/USDT", "TINY/USDT"],
                        "min_volume_usdt": 1e8})
    plan = plan_scan([spec], CANDIDATES + ["TINY/USDT"],
                     {**VOLUMES, "TINY/USDT": 1.0})
    assert plan.wants("s1", "TINY/USDT")


def test_exclude_still_beats_include():
    spec = _Spec("s1", {"include": ["BTC/USDT", "ETH/USDT"],
                        "exclude": ["ETH/USDT"]})
    plan = plan_scan([spec], CANDIDATES, VOLUMES)
    assert set(plan.symbols) == {"BTC/USDT"}


def test_an_empty_include_keeps_the_liquidity_filtered_universe():
    """A spec that names nothing behaves exactly as before — the legacy
    genomes must not be narrowed by this."""
    spec = _Spec("s1", {"include": [], "min_volume_usdt": 1e8})
    plan = plan_scan([spec], CANDIDATES, VOLUMES)
    assert set(plan.symbols) == set(CANDIDATES)


def test_the_scan_is_a_union_but_membership_is_per_strategy():
    a = _Spec("a", {"include": ["BTC/USDT"]})
    b = _Spec("b", {"include": ["DOGE/USDT"]})
    plan = plan_scan([a, b], CANDIDATES, VOLUMES)
    assert set(plan.symbols) == {"BTC/USDT", "DOGE/USDT"}
    assert plan.wants("a", "BTC/USDT") and not plan.wants("a", "DOGE/USDT")
    assert plan.wants("b", "DOGE/USDT") and not plan.wants("b", "BTC/USDT")


# ── the orchestrator must actually enforce it ────────────────────────────

def test_the_orchestrator_refuses_a_symbol_the_strategy_never_declared():
    from trader.engine.orchestrator import symbol_allows

    g = type("G", (), {})()
    g.symbols = frozenset({"BTC/USDT", "ETH/USDT"})
    assert symbol_allows(g, "BTC/USDT")
    assert not symbol_allows(g, "XAU/USDT")


def test_an_empty_symbol_set_means_every_symbol():
    """Same shape as the regime filter, where an empty set meant NO regimes
    and silently stopped the book from trading."""
    from trader.engine.orchestrator import symbol_allows

    g = type("G", (), {})()
    g.symbols = frozenset()
    assert symbol_allows(g, "ANYTHING/USDT")
    assert symbol_allows(type("G", (), {})(), "ANYTHING/USDT")


def test_the_venue_suffix_does_not_defeat_the_gate():
    """The venue quotes `XAU/USDT:USDT` where the spec names `XAU/USDT`."""
    from trader.engine.orchestrator import symbol_allows

    g = type("G", (), {})()
    g.symbols = frozenset({"BTC/USDT"})
    assert symbol_allows(g, "BTC/USDT:USDT")
    assert not symbol_allows(g, "XAU/USDT:USDT")
