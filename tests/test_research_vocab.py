"""The search's vocabulary: what a condition may say, and how it mirrors.

Two rules do the work here.

THRESHOLDS ARE MEASURED, NEVER GUESSED. A hard-coded `> 0.62` on
taker_buy/volume fired on 0.005% of bars and made the whole flow family look
tested while it never traded. Every threshold in this catalogue is the 10th,
25th, 75th or 90th percentile of that expression on the discovery slice.

THE MIRROR IS WRITTEN, NOT COMPUTED. Each gauge carries a long expression
and a short expression composed so the SAME rank means the mirror state:
ret(24) mirrors to 0 - ret(24), lower_wick() to upper_wick(), and a deep
pullback (dd_from_high low) to a deep bounce (0 - runup_from_low low).
Reflecting a threshold arithmetically would assume a symmetric distribution
that crypto returns do not have.
"""
import pytest

from trader.research import vocab
from trader.strategy import dsl


def _thresholds(exprs):
    """Every expression usable, with placeholder percentiles.

    Non-integral so a rendering assertion tests the MIRROR, not the
    formatter's integral-float shortcut (see the dedicated test for that).
    """
    return {e: {"p10": -1.25, "p25": -0.5, "p75": 0.5, "p90": 1.25,
                "usable": True} for e in exprs}


def test_every_gauge_expression_parses_on_both_sides():
    for g in vocab.GAUGES:
        for side in (g.long, g.short):
            dsl.parse(side)          # raises SpecError if it does not


def test_every_bool_part_parses_on_both_sides():
    for p in vocab.BOOL_PARTS:
        dsl.parse(p.long)
        dsl.parse(p.short)


def test_part_keys_are_unique():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    keys = [p.key for p in parts]
    assert len(keys) == len(set(keys))


def test_a_gauge_yields_four_parts_two_each_way():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    ret24 = [p for p in parts if p.gauge == "ret24"]
    assert len(ret24) == 4
    assert {p.key for p in ret24} == {"ret24<p10", "ret24<p25",
                                      "ret24>p75", "ret24>p90"}


def test_the_short_side_reads_the_mirror_expression():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    hi = next(p for p in parts if p.key == "ret24>p90")
    assert hi.long == "ret(24) > 1.25"
    # the mirror's own measured p90, not the long side's negated p10
    assert hi.short == "0 - ret(24) > 1.25"


def test_an_integral_threshold_renders_without_a_trailing_zero():
    """A card reads `rsi(14) > 30`, not `30.0` — and the text must re-parse."""
    th = {e: {"p10": -30.0, "p25": -1.0, "p75": 1.0, "p90": 30.0,
              "usable": True} for e in vocab.expressions("4h")}
    p = next(x for x in vocab.parts_for("4h", th) if x.key == "rsi14>p90")
    assert p.long == "rsi(14) > 30"
    dsl.parse(p.long)


def test_a_direction_neutral_gauge_reads_the_same_both_ways():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    p = next(p for p in parts if p.key == "atrrank200>p90")
    assert p.long == p.short


def test_an_unusable_gauge_contributes_no_parts():
    exprs = vocab.expressions("4h")
    th = _thresholds(exprs)
    for e in (g := next(x for x in vocab.GAUGES if x.key == "oiret24")).long, g.short:
        th[e]["usable"] = False
    parts = vocab.parts_for("4h", th)
    assert not [p for p in parts if p.gauge == "oiret24"]


def test_a_gauge_with_no_measurement_contributes_no_parts():
    parts = vocab.parts_for("4h", {})
    assert not [p for p in parts if p.kind == "gauge"]
    assert [p for p in parts if p.kind in ("state", "event")]


def test_horizon_restricted_gauges_only_appear_where_they_mean_something():
    """htf("4h", …) on a 4h base reads its own frame — no information."""
    four = {p.gauge for p in
            vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))}
    fifteen = {p.gauge for p in
               vocab.parts_for("15m", _thresholds(vocab.expressions("15m")))}
    assert "htf4h_dist" not in four
    assert "htf4h_dist" in fifteen


def test_every_part_carries_its_requirements_derived_from_the_dsl():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    funding = next(p for p in parts if p.gauge == "fundz360")
    assert "funding" in vocab.part_requires(funding)
    ref = next(p for p in parts if p.gauge.startswith("r_spx"))
    assert "ref:spx" in vocab.part_requires(ref)
    plain = next(p for p in parts if p.key == "ev:donch100")
    assert vocab.part_requires(plain) == ("ohlcv",)


def test_the_catalogue_is_large_enough_to_be_a_search():
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    assert len(parts) >= 150
    assert sum(1 for p in parts if p.kind == "event") >= 5


def test_a_cut_at_or_beyond_the_observed_range_is_dropped():
    """`streak()` is non-negative, so a p10/p25 of 0 renders `< 0`, which can
    never fire — measured 2026-09-11 on live thresholds. That must drop the
    part entirely rather than enter the vocabulary as a testable rule that
    happens to take zero trades."""
    th = _thresholds(vocab.expressions("4h"))
    for e in ("streak(close > prev(close, 1))",
              "streak(close < prev(close, 1))"):
        th[e].update({"min": 0.0, "max": 3.0, "p10": 0.0, "p25": 0.0,
                     "p75": 1.0, "p90": 3.0})
    parts = vocab.parts_for("4h", th)
    streak_parts = {p.key: p for p in parts if p.gauge == "streak"}
    # the `<` cuts at the floor (0) never fire and must be absent
    assert "streak<p10" not in streak_parts
    assert "streak<p25" not in streak_parts
    # the `>` cut at the ceiling (3) never fires either
    assert "streak>p90" not in streak_parts
    # a `>` cut strictly inside the range is unaffected
    assert "streak>p75" in streak_parts


def test_a_gauge_at_the_extremes_of_a_bounded_range_contributes_nothing():
    """`breadth(...)` is bounded [0,1] — measured 2026-09-11: p10=0 and
    p90=1 are both the edges of the range and neither cut can ever fire."""
    th = _thresholds(vocab.expressions("4h"))
    for e in ("breadth(close > ema(50))", "1 - breadth(close > ema(50))"):
        th[e].update({"min": 0.0, "max": 1.0, "p10": 0.0, "p25": 0.157895,
                      "p75": 0.894737, "p90": 1.0})
    parts = vocab.parts_for("4h", th)
    breadth_parts = {p.key for p in parts if p.gauge == "breadth50"}
    assert breadth_parts == {"breadth50<p25", "breadth50>p75"}


def test_duplicate_rendered_cuts_on_one_side_keep_only_the_first():
    """Two different quantiles of one gauge can render identical text (the
    underlying values tie) without either being at the edge of the range —
    that must still mint only ONE canonical part, not two hashes for one
    rule."""
    th = _thresholds(vocab.expressions("4h"))
    th["ret(24)"].update({"min": -10.0, "max": 10.0,
                          "p10": -0.5, "p25": -0.5,     # tie: same rendering
                          "p75": 0.5, "p90": 1.25})
    parts = vocab.parts_for("4h", th)
    ret24 = {p.key: p for p in parts if p.gauge == "ret24"}
    assert "ret24<p10" in ret24
    assert "ret24<p25" not in ret24     # duplicate of p10's rendered text
    assert ret24["ret24<p10"].long == "ret(24) < -0.5"


def test_a_measurement_from_before_this_check_existed_rules_out_nothing():
    """An old ledger row has no `min`/`max` at all — that must not be read
    as 'everything is unattainable'."""
    th = _thresholds(vocab.expressions("4h"))
    for v in th.values():
        v.pop("min", None)
        v.pop("max", None)
    parts = vocab.parts_for("4h", th)
    assert len(parts) >= 150


def test_exactly_one_event_may_appear_in_a_combination_later():
    """The compatibility rule lives in combo.py; the KIND it reads lives
    here, so an event must be labelled as one."""
    parts = vocab.parts_for("4h", _thresholds(vocab.expressions("4h")))
    donch = next(p for p in parts if p.key == "ev:donch100")
    assert donch.kind == "event"
    assert donch.long == "close > donchian_hi(100)"
    assert donch.short == "close < donchian_lo(100)"
