"""When does a part earn its place?

The measurement this encodes: every filter ever added to the book's one
working mechanism held its profit factor and halved its compounded return.

  baseline  PF 1.42  p 3.5e-04  487 trades  CAGR 17.3%
  carry     PF 1.43  p 3.5e-04  415 trades  CAGR  7.6%
  hivol     PF 1.44  p 1.1e-02  307 trades  CAGR  6.3%

Removing 15% of trades removed more than half the compounded return, because
the cut trades carried a disproportionate share of the fat right tail and PF
barely moves since they are near break-even individually. So a part must
improve BOTH the rotation null AND compounded return over one account — and
a survivor must beat every one of its one-part ablations, not only the
parent it happened to grow from. That is how ties go to the simpler rule.
"""
from trader.research import growth


def _res(p=0.01, pct=20.0, verdict="scored", testable=True, trades=100):
    return {"consistency_p": p, "portfolio": {"total_pct": pct},
            "verdict": verdict, "testable": testable, "trades": trades,
            "scored_symbols": 8}


def test_a_scored_testable_rule_with_signal_carries_information():
    assert growth.carries_information(_res(p=0.05))


def test_a_rule_beyond_the_information_threshold_does_not():
    assert not growth.carries_information(_res(p=0.6))


def test_an_untestable_rule_never_carries_information():
    assert not growth.carries_information(_res(p=0.001, testable=False))
    assert not growth.carries_information(_res(verdict="untestable", p=None))


def test_a_part_must_improve_both_measures():
    parent = _res(p=0.05, pct=20.0)
    better = _res(p=0.01, pct=25.0)
    ok, why = growth.earns_place(better, parent)
    assert ok and "both" in why.lower() or ok


def test_a_part_that_only_improves_the_null_is_refused():
    ok, why = growth.earns_place(_res(p=0.001, pct=8.0),
                                 _res(p=0.05, pct=20.0))
    assert not ok
    assert "compounded" in why


def test_a_part_that_only_improves_compounded_return_is_refused():
    ok, why = growth.earns_place(_res(p=0.2, pct=40.0),
                                 _res(p=0.05, pct=20.0))
    assert not ok
    assert "null" in why


def test_the_filter_result_that_motivated_this_rule_is_refused():
    """`carry`: PF holds, p identical, CAGR 17.3 -> 7.6."""
    baseline = _res(p=3.5e-04, pct=17.3)
    carry = _res(p=3.5e-04, pct=7.6)
    ok, _ = growth.earns_place(carry, baseline)
    assert not ok


def test_a_subset_that_cannot_be_scored_counts_as_beaten():
    """If removing a part makes the rule untestable, the part is doing the
    work that makes it judgeable at all."""
    child = _res(p=0.01, pct=20.0)
    dead = _res(p=None, pct=0.0, verdict="untestable", testable=False)
    ok, _ = growth.earns_place(child, dead)
    assert ok


def test_a_survivor_beats_every_ablation():
    child = _res(p=0.001, pct=30.0)
    subs = {"a": _res(p=0.01, pct=20.0), "b": _res(p=0.02, pct=25.0)}
    verdict, reason, abl = growth.decide(child, parent=subs["a"],
                                         subset_results=subs)
    assert verdict == "survivor"
    assert set(abl) == {"a", "b"}
    assert abl["a"]["drop_pct"] == 10.0


def test_a_rule_carried_by_one_part_is_not_a_survivor_but_still_grows():
    child = _res(p=0.001, pct=30.0)
    subs = {"a": _res(p=0.0005, pct=31.0), "b": _res(p=0.02, pct=25.0)}
    verdict, reason, _ = growth.decide(child, parent=subs["b"],
                                       subset_results=subs)
    assert verdict == "grow"
    assert "a" in reason


def test_a_child_that_does_not_beat_its_parent_is_pruned():
    child = _res(p=0.30, pct=5.0)
    verdict, reason, _ = growth.decide(child, parent=_res(p=0.05, pct=20.0),
                                       subset_results={})
    assert verdict == "prune"


def test_a_single_with_signal_grows_and_has_no_ablation():
    verdict, reason, abl = growth.decide(_res(p=0.05), parent=None,
                                         subset_results=None)
    assert verdict == "grow" and abl == {}


def test_a_single_without_signal_is_pruned():
    verdict, _, _ = growth.decide(_res(p=0.8), parent=None,
                                  subset_results=None)
    assert verdict == "prune"


def test_an_empty_rule_is_pruned_without_pretending_to_be_evidence():
    verdict, reason, _ = growth.decide(
        _res(verdict="empty", p=None, trades=0), parent=None,
        subset_results=None)
    assert verdict == "prune"
    assert "no trades" in reason
