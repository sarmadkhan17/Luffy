import numpy as np
import pandas as pd
import pytest

from trader.brain.analyst import MAX_SIGNAL_OVERLAP, Analyst, signal_overlap
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(sid="a1", name="Analyst Probe", entry="close > ema(5)", **kw):
    base = dict(
        id=sid, name=name,
        thesis="A hypothesis of sufficient length for the validator, naming a "
               "plausible short-horizon inefficiency that a rolling gate can "
               "confirm or refute on recent data.",
        invalidation="Retire below profit factor 0.85 over the last 30 days.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="1h", direction="long", entry_long=entry, entry_short="",
        filters=[],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"}, time={"max_bars": 24}),
        regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


def _frame(n=4000, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.006, n))
    df = pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
        "open": close, "close": close, "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.003
    df["low"] = df[["open", "close"]].min(axis=1) * 0.997
    return df[["ts", "open", "high", "low", "close", "volume"]]


CFG = {"risk": {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
                "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
                "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
                "bar_minutes": 60},
       "strategies": {"select_recent_days": 60, "select_min_trades": 3,
                      "select_min_pf": 1.05, "decay_recent_days": 60,
                      "decay_min_trades": 3, "decay_floor_pf": 0.85}}


class _Journal:
    def __init__(self):
        self.events = []

    def log_brain_event(self, kind, subject, detail):
        self.events.append((kind, subject, detail))


@pytest.fixture
def analyst(monkeypatch):
    a = Analyst(_Journal(), CFG)
    up = _frame(4000, drift=0.004, seed=2)
    monkeypatch.setattr(a, "frames", lambda tf, extra=(): {"BTC/USDT": up,
                                                 "_btc_1h": up})
    return a


# ── signal overlap ───────────────────────────────────────────────────────
def test_identical_signals_are_fully_redundant():
    lo = np.array([True, False, True, False, True])
    sh = np.zeros(5, bool)
    assert signal_overlap(lo, sh, lo, sh) == pytest.approx(1.0)


def test_opposite_signals_count_as_redundant():
    """Perfectly anti-correlated is the same information, inverted."""
    lo = np.array([True, False, True, False, True])
    sh = np.zeros(5, bool)
    assert signal_overlap(lo, sh, sh, lo) == pytest.approx(1.0)


def test_unrelated_signals_are_not_redundant():
    a = np.array([True, True, False, False, False, False])
    b = np.array([False, False, False, True, False, False])
    z = np.zeros(6, bool)
    assert signal_overlap(a, z, b, z) < MAX_SIGNAL_OVERLAP


def test_constant_signal_has_no_correlation():
    z = np.zeros(5, bool)
    assert signal_overlap(z, z, z, z) == 0.0


def test_overlap_handles_different_lengths():
    a = np.array([True, False, True, False])
    b = np.array([True, False, True])
    assert 0.0 <= signal_overlap(a, np.zeros(4, bool),
                                 b, np.zeros(3, bool)) <= 1.0


# ── selection ────────────────────────────────────────────────────────────
def test_evaluate_picks_a_timeframe_and_reports_all(analyst):
    ok, ev = analyst.evaluate(_spec(), timeframes=("1h",))
    assert "chosen_timeframe" in ev and "by_timeframe" in ev
    assert ev["chosen_timeframe"] == "1h"


def test_evaluate_reports_untested_when_data_is_missing(analyst):
    ok, ev = analyst.evaluate(_spec(entry="funding_z(1920) > 2.0"),
                              timeframes=("1h",))
    assert not ok


def test_admit_rejects_a_redundant_duplicate(analyst):
    """A twin of something already trading adds no diversification, however
    well it scores alone."""
    original = _spec("orig", "Original")
    twin = _spec("twin", "Twin Of Original")      # identical entry logic
    ok, ev = analyst.evaluate(twin, timeframes=("1h",))
    if not ok:
        pytest.skip("probe strategy does not pass the gate on this fixture")
    admitted, ev2 = analyst.admit(twin, [original])
    assert not admitted and "overlap" in ev2["reason"]


def test_persistence_is_context_not_a_gate(analyst):
    st = analyst.persistence(_spec(), tf="1h")
    assert "hit_rate" in st and "windows" in st


# ── retirement ───────────────────────────────────────────────────────────
def test_review_retires_a_decayed_strategy(monkeypatch):
    a = Analyst(_Journal(), CFG)
    down = _frame(4000, drift=-0.004, seed=9)
    monkeypatch.setattr(a, "frames", lambda tf, extra=(): {"BTC/USDT": down,
                                                 "_btc_1h": down})
    actions = a.review_deployed([_spec()])
    assert len(actions) == 1 and actions[0]["action"] == "retire"
    assert a.journal.events[0][0] == "spec_decayed"


def test_review_keeps_a_working_strategy(analyst):
    assert analyst.review_deployed([_spec()]) == []


def test_idle_strategy_is_not_retired(analyst):
    """Silence means the regime has not come back, not that the edge broke."""
    assert analyst.review_deployed(
        [_spec(entry="close > ema(200) and rsi(2) < 1")]) == []


# ── regime awareness ─────────────────────────────────────────────────────
def test_current_regime_reports_overall_and_per_symbol(analyst):
    r = analyst.current_regime("1h")
    assert "overall" in r and "per_symbol" in r
    assert r["overall"] in {"TRENDING_UP", "TRENDING_DOWN", "RANGING",
                            "VOLATILE", "UNKNOWN"}


def test_regime_fitness_reports_declared_and_measured(analyst):
    rf = analyst.regime_fitness(_spec(), tf="1h")
    assert "fit" in rf and "declared" in rf and "by_regime" in rf
    assert rf["declared"] == ["TRENDING_UP"]


def test_set_measured_regimes_replaces_the_guess(analyst, monkeypatch):
    spec = _spec(regime_filter=["VOLATILE"])
    monkeypatch.setattr(analyst, "regime_fitness",
                        lambda s, tf=None: {"fit": ["TRENDING_UP", "RANGING"],
                                            "declared": ["VOLATILE"],
                                            "by_regime": {}})
    rf = analyst.set_measured_regimes(spec)
    assert spec.regime_filter == ["RANGING", "TRENDING_UP"]
    assert rf["changed"] is True


def test_unproven_regime_keeps_the_declared_filter(analyst, monkeypatch):
    """An empty filter would silence the strategy entirely — a stronger claim
    than 'we have not proven it yet'."""
    spec = _spec(regime_filter=["TRENDING_UP"])
    monkeypatch.setattr(analyst, "regime_fitness",
                        lambda s, tf=None: {"fit": [], "declared": ["TRENDING_UP"],
                                            "by_regime": {}})
    rf = analyst.set_measured_regimes(spec)
    assert spec.regime_filter == ["TRENDING_UP"]
    assert rf["changed"] is False and "unproven" in rf["note"]


def test_evaluate_reports_fitness_for_the_current_regime(analyst):
    ok, ev = analyst.evaluate(_spec(), timeframes=("1h",))
    assert "regime" in ev
    r = ev["regime"]
    assert "current" in r and "fit" in r and "fit_for_now" in r
    assert r["fit_for_now"] == (r["current"] in r["fit"])
