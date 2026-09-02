"""An empty regime filter means EVERY regime, not none.

`if snap.regime not in genome.regime_filter: continue` — with an empty
filter that test is always true, so the strategy was skipped before its
evaluator was ever called. A spec written with `regime_filter: []`, which is
how both the Strategist and a hand-authored spec express "no restriction",
could therefore never trade in any regime at all.

auth_donchian_breakout_trail carried exactly that, so the only strategy in
the book was unreachable long AND short, and its zero live trades read as
"the setup has not appeared" rather than "this is never evaluated".

The legacy genomes all carry non-empty filters, which is why this survived.
"""
from trader.engine.orchestrator import regime_allows


class _G:
    def __init__(self, rf):
        self.regime_filter = frozenset(rf)


def test_an_empty_filter_admits_every_regime():
    for regime in ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE",
                   "UNKNOWN"):
        assert regime_allows(_G([]), regime) is True


def test_a_populated_filter_still_restricts():
    g = _G(["RANGING"])
    assert regime_allows(g, "RANGING") is True
    assert regime_allows(g, "TRENDING_UP") is False


def test_several_named_regimes_are_all_admitted():
    g = _G(["TRENDING_UP", "TRENDING_DOWN"])
    assert regime_allows(g, "TRENDING_UP") is True
    assert regime_allows(g, "TRENDING_DOWN") is True
    assert regime_allows(g, "RANGING") is False


def test_a_missing_attribute_is_treated_as_unrestricted():
    class _Bare:
        pass
    assert regime_allows(_Bare(), "RANGING") is True


def test_the_live_spec_is_reachable_in_every_regime():
    """The regression itself: the book's only strategy must be evaluable."""
    import json
    from trader.strategy.spec import StrategySpec
    spec = StrategySpec.from_dict(
        json.load(open("data/authored_specs/donchian_breakout_trail.json")))
    g = _G(spec.regime_filter)
    assert all(regime_allows(g, r) for r in
               ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"))
