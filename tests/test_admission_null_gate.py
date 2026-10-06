"""Admission judges a spec over ITS universe, and refuses a spec that beats
its own rotation no more often than chance.

Two faults met here. The Analyst loaded five backtest symbols from config
whatever the spec declared, so a sixteen-market strategy was judged — pooled
PF, redundancy, and the cross-symbol null — on five of them. And the null
result was recorded but never gated, so a spec could be admitted on a profit
factor that exit geometry alone can manufacture.

Donchian Breakout Trail reads p=0.094 on those five symbols and p=9.2e-05 on
the sixteen it actually trades. Same strategy, same data, different question.
"""
import pytest

from trader.brain.analyst import Analyst
from trader.strategy.spec import ExitSpec, StrategySpec


class _Cfg(dict):
    pass


def _analyst(monkeypatch, calls, null_p=None):
    a = Analyst.__new__(Analyst)
    a.cfg = {"strategy": {}, "risk": {}}
    a._frames = {}
    a.null_max_p = 0.01
    monkeypatch.setattr(Analyst, "frames",
                        lambda self, tf, extra=(): calls.append(extra) or {})
    return a


def _spec(markets):
    return StrategySpec(
        id="probe", name="probe", thesis="t", invalidation="i",
        provenance={}, universe={"include": list(markets)},
        timeframe="4h", direction="both",
        entry_long="close > ema(50)", entry_short="close < ema(50)",
        filters=[], exit=ExitSpec(), regime_filter=[],
        markets=["futures"])


def test_the_spec_s_declared_universe_reaches_the_frame_loader(monkeypatch):
    calls = []
    a = _analyst(monkeypatch, calls)
    a._ctx("4h", _spec(["UNI/USDT", "SUI/USDT", "ZEC/USDT"]))
    assert calls == [("UNI/USDT", "SUI/USDT", "ZEC/USDT")]


def test_a_spec_that_declares_nothing_asks_for_nothing_extra(monkeypatch):
    calls = []
    a = _analyst(monkeypatch, calls)
    a._ctx("4h", _spec([]))
    assert calls == [()]


# ── the gate itself ────────────────────────────────────────────────────
def _admit_with(monkeypatch, percentiles, rho=0.0, common_p=0.001):
    a = Analyst.__new__(Analyst)
    a.null_max_p = 0.01
    monkeypatch.setattr(Analyst, "evaluate",
                        lambda self, spec: (True, {"chosen_timeframe": "4h"}))
    monkeypatch.setattr(Analyst, "redundancy",
                        lambda self, spec, tf, book: (0.1, ""))
    monkeypatch.setattr(Analyst, "persistence", lambda self, spec, tf: {})
    from trader.strategy import null_baseline as nb
    monkeypatch.setattr(Analyst, "_null_evidence",
        lambda self, spec, tf: {"percentiles": percentiles,
            "consistency_p_dep": nb.consistency_p_dependent(percentiles.values(), rho),
            "common_rotation": {"p": common_p}})
    return a.admit(_spec([]), [])


DONCHIAN = dict(enumerate([0.60, 0.88, 0.82, 0.90, 0.90, 0.92, 0.60, 0.97,
                           0.88, 0.73, 0.92, 0.48, 0.95, 0.78, 0.90]))


def test_independent_spec_with_common_control_is_admitted(monkeypatch):
    ok, ev = _admit_with(monkeypatch, DONCHIAN)
    assert ok is True
    assert ev["null_consistency_p"] < 0.01
    assert ev["null_symbols"] == 15


def test_a_spec_that_does_not_is_refused_with_the_number(monkeypatch):
    noise = dict(enumerate([0.63, 0.60, 0.80, 0.87, 0.78, 0.15, 0.97, 0.40,
                            0.65]))
    ok, ev = _admit_with(monkeypatch, noise)
    assert ok is False
    assert "rotation" in ev["reason"] and "p=0.27" in ev["reason"]


def test_too_few_symbols_to_test_is_refused_as_untestable(monkeypatch):
    """2026-09-11: spec_funding_filtered_trend_pullback was admitted on 20
    trades with the rotation null run on 0 symbols, because silence did not
    block. A spec nobody can test is refused, not waved through."""
    ok, ev = _admit_with(monkeypatch, {0: 0.5, 1: 0.5})
    assert ok is False
    assert ev["null_consistency_p"] is None
    assert ev["null_symbols"] == 2
    assert ev["untestable"] is True
    assert "untestable" in ev["reason"]


def test_no_symbol_carrying_a_percentile_is_refused(monkeypatch):
    ok, ev = _admit_with(monkeypatch, {})
    assert ok is False
    assert ev["null_symbols"] == 0
    assert ev["untestable"] is True


def test_the_record_keeps_the_readable_summary_too(monkeypatch):
    _, ev = _admit_with(monkeypatch, DONCHIAN)
    assert ev["null_median_percentile"] == pytest.approx(0.88)
    assert ev["null_beats_90pct_on"] == "7/15"


def test_correlated_votes_do_not_pass_admission(monkeypatch):
    ok, ev = _admit_with(monkeypatch, DONCHIAN, rho=0.9)
    assert not ok
    assert ev["null_consistency_p_raw"] < .01
    assert ev["null_consistency_p"] > .01


def test_independent_votes_cannot_replace_common_control(monkeypatch):
    ok, ev = _admit_with(monkeypatch, DONCHIAN, common_p=None)
    assert not ok and ev["untestable"]
    ok, ev = _admit_with(monkeypatch, DONCHIAN, common_p=.25)
    assert not ok and 'p=0.25' in ev['reason']


def test_missing_dependence_has_no_independence_fallback(monkeypatch):
    ok, ev = _admit_with(monkeypatch, DONCHIAN, rho=None)
    assert not ok and ev["untestable"]
