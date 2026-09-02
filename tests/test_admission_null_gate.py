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
def _admit_with(monkeypatch, percentiles):
    a = Analyst.__new__(Analyst)
    a.null_max_p = 0.01
    monkeypatch.setattr(Analyst, "evaluate",
                        lambda self, spec: (True, {"chosen_timeframe": "4h"}))
    monkeypatch.setattr(Analyst, "redundancy",
                        lambda self, spec, tf, book: (0.1, ""))
    monkeypatch.setattr(Analyst, "persistence", lambda self, spec, tf: {})
    monkeypatch.setattr(Analyst, "_null_percentiles",
                        lambda self, spec, tf: percentiles)
    return a.admit(_spec([]), [])


DONCHIAN = dict(enumerate([0.60, 0.88, 0.82, 0.90, 0.90, 0.92, 0.60, 0.97,
                           0.88, 0.73, 0.92, 0.48, 0.95, 0.78, 0.90]))


def test_a_spec_that_beats_its_own_rotation_is_admitted(monkeypatch):
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


def test_too_few_symbols_does_not_block_admission_on_its_own(monkeypatch):
    """Silence is not a failing verdict — the PF and overlap gates still run."""
    ok, ev = _admit_with(monkeypatch, {0: 0.5, 1: 0.5})
    assert ok is True
    assert ev["null_consistency_p"] is None
    assert ev["null_symbols"] == 2


def test_the_record_keeps_the_readable_summary_too(monkeypatch):
    _, ev = _admit_with(monkeypatch, DONCHIAN)
    assert ev["null_median_percentile"] == pytest.approx(0.88)
    assert ev["null_beats_90pct_on"] == "7/15"
