"""What gets scanned is decided by the strategies, not by the kernel.

The cycle scanned a fixed list — three majors plus the top alts by volume —
at one fixed timeframe, regardless of what any strategy actually needed.
Every spec already carries a `universe` block (include / exclude /
min_volume_usdt) and a `timeframe`, and nothing read either of them.

That coupling is what makes adding a second strategy a kernel change. A spec
that wants gold, or 1h bars, or only the twenty most liquid perps, should
declare it and be scanned for it. The plan is the union across whatever is
live, so removing a strategy narrows the scan by itself.
"""
import pytest

from trader.strategy.scan_plan import ScanPlan, plan_scan


class _Spec:
    def __init__(self, sid, tf="4h", include=(), exclude=(), min_vol=0):
        self.id, self.timeframe = sid, tf
        self.universe = {"include": list(include), "exclude": list(exclude),
                         "min_volume_usdt": min_vol}


VOL = {"BTC/USDT": 5e9, "ETH/USDT": 3e9, "SOL/USDT": 8e8,
       "DOGE/USDT": 9e7, "TINY/USDT": 1e6, "XAU/USDT:USDT": 2e9}
CANDIDATES = list(VOL)


def test_a_spec_with_no_preferences_gets_the_liquid_candidates():
    p = plan_scan([_Spec("s")], CANDIDATES, VOL)
    assert set(p.symbols) == set(CANDIDATES)


def test_min_volume_filters_the_illiquid_out():
    p = plan_scan([_Spec("s", min_vol=1e8)], CANDIDATES, VOL)
    assert "TINY/USDT" not in p.symbols and "DOGE/USDT" not in p.symbols
    assert "BTC/USDT" in p.symbols


def test_an_explicit_include_is_scanned_even_when_illiquid():
    """A declared symbol is a decision, not a suggestion."""
    p = plan_scan([_Spec("s", include=["TINY/USDT"], min_vol=1e8)],
                  CANDIDATES, VOL)
    assert "TINY/USDT" in p.symbols


def test_exclude_wins_over_the_candidate_list():
    p = plan_scan([_Spec("s", exclude=["DOGE/USDT"])], CANDIDATES, VOL)
    assert "DOGE/USDT" not in p.symbols


def test_exclude_wins_over_include_for_the_same_spec():
    p = plan_scan([_Spec("s", include=["DOGE/USDT"], exclude=["DOGE/USDT"])],
                  CANDIDATES, VOL)
    assert "DOGE/USDT" not in p.symbols


def test_the_plan_is_the_UNION_across_strategies():
    """Adding a strategy must widen the scan without touching the kernel."""
    gold = _Spec("gold", include=["XAU/USDT:USDT"], min_vol=1e12)
    major = _Spec("major", min_vol=1e9)
    p = plan_scan([gold, major], CANDIDATES, VOL)
    assert "XAU/USDT:USDT" in p.symbols and "BTC/USDT" in p.symbols


def test_one_specs_exclusion_does_not_veto_another_specs_symbol():
    a = _Spec("a", exclude=["SOL/USDT"])
    b = _Spec("b")
    assert "SOL/USDT" in plan_scan([a, b], CANDIDATES, VOL).symbols


def test_timeframes_are_the_union_of_what_strategies_trade():
    p = plan_scan([_Spec("a", tf="4h"), _Spec("b", tf="1h")], CANDIDATES, VOL)
    assert set(p.timeframes) >= {"1h", "4h"}


def test_per_strategy_symbols_are_reported_separately():
    """The cycle must know which spec wanted which symbol, so a spec is never
    evaluated on a market it explicitly refused."""
    a = _Spec("a", include=["XAU/USDT:USDT"], min_vol=1e12)
    b = _Spec("b", min_vol=1e9)
    p = plan_scan([a, b], CANDIDATES, VOL)
    assert p.wants("a", "XAU/USDT:USDT") is True
    assert p.wants("a", "SOL/USDT") is False
    assert p.wants("b", "BTC/USDT") is True


def test_an_unknown_strategy_is_assumed_to_want_nothing():
    p = plan_scan([_Spec("a")], CANDIDATES, VOL)
    assert p.wants("ghost", "BTC/USDT") is False


def test_no_strategies_yields_an_empty_plan_not_a_crash():
    p = plan_scan([], CANDIDATES, VOL)
    assert p.symbols == () and p.timeframes == ()


def test_symbols_are_ordered_and_deduplicated():
    p = plan_scan([_Spec("a"), _Spec("b")], CANDIDATES, VOL)
    assert list(p.symbols) == sorted(set(p.symbols))


def test_a_missing_volume_reading_is_not_treated_as_liquid():
    """Absent data must not pass a liquidity floor — that is the NaN rule."""
    p = plan_scan([_Spec("s", min_vol=1e8)], CANDIDATES + ["NEW/USDT"], VOL)
    assert "NEW/USDT" not in p.symbols
