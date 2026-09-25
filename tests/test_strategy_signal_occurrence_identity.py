"""SDD-STAGE-3-STRATEGY-SIGNAL-OCCURRENCE-IDENTITY-V1.

A signal occurrence is one signal from this exact strategy-spec version, for
this canonical instrument/action, evaluated on this exact closed signal bar.
"""
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.core.journal import Journal
from trader.core.types import TF_MS, Action, Decision, Snapshot, StrategySignal
from trader.strategy import signal_occurrence as so
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec

TF = "15m"


def _spec(**kw) -> StrategySpec:
    base = dict(
        id="occ1", name="Occurrence Probe",
        thesis="Short-horizon trends persist because discretionary entries lag "
               "the impulse; buying a shallow pullback inside an aligned stack "
               "captures the continuation at reduced adverse excursion.",
        invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe=TF, direction="both",
        entry_long="close > open", entry_short="close < open", filters=[],
        exit=ExitSpec(), regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


def _frame(n=300, up=True, freq="15min"):
    """Closed bars (2026-01) — every bar is either up or down, so the spec
    fires on every bar."""
    close = np.linspace(100, 130, n) if up else np.linspace(130, 100, n)
    open_ = close * (0.999 if up else 1.001)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"),
        "open": open_, "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999, "close": close,
        "volume": np.full(n, 100.0)})


def _eval(spec, frame, symbol="BTC/USDT", tf=TF):
    c = compile_spec(spec)
    snap = Snapshot(symbol=symbol, ts="", price=float(frame["close"].iloc[-1]),
                    dfs={tf: frame}, market_type="futures")
    return c.to_evaluator()(c.spec, snap)


def _persisted(sig):
    """Exactly the orchestrator's serialisation, through JSON."""
    return json.loads(json.dumps([vars(sig) | {"action": sig.action.value}]))[0]


# 1 ───────────────────────────────────────────────────────────────────────
def test_repeated_scans_of_the_same_closed_bar_share_one_identity():
    f = _frame()
    a, b = _eval(_spec(), f), _eval(_spec(), f.copy())
    ka, kb = so.occurrence_key(a), so.occurrence_key(b)
    assert ka is not None and ka == kb
    assert so.occurrence_key(_persisted(a)) == ka      # survives signals_json


# 2 ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("change", [
    {"entry_long": "close > open * 0.9999"},
    {"filters": ["volume > 0"]},
    {"universe": {"include": ["BTC/USDT"]}},
    {"direction": "long", "entry_short": ""},
])
def test_changed_spec_content_under_same_id_changes_identity(change):
    f = _frame()
    base = so.occurrence_key(_eval(_spec(), f))
    other = so.occurrence_key(_eval(_spec(**change), f))
    assert other is not None and base[0] == other[0] == "occ1"
    assert other != base and other[1] != base[1]


def test_timeframe_is_part_of_the_fingerprint():
    assert so.spec_fingerprint(_spec()) != so.spec_fingerprint(_spec(timeframe="1h"))


def test_fingerprint_ignores_non_signal_content():
    fp = so.spec_fingerprint(_spec())
    assert fp == so.spec_fingerprint(_spec(
        name="Renamed Probe", provenance={"regime_evidence": {"x": 1}},
        regime_filter=["RANGING"], exit=ExitSpec(time={"max_bars": 9}),
        generation=3))
    assert len(fp) == 64 and int(fp, 16) >= 0


def test_compiled_fingerprint_is_frozen_at_compile_time():
    """The trees were parsed from the compiled content; a later mutation of
    the spec object must not relabel signals those trees emit."""
    c = compile_spec(_spec())
    fp = c.fingerprint
    c.spec.entry_long = "close > open * 2"
    snap = Snapshot(symbol="BTC/USDT", ts="", price=1.0, dfs={TF: _frame()},
                    market_type="futures")
    assert c.to_evaluator()(c.spec, snap).params["spec_fingerprint"] == fp


# 3 ───────────────────────────────────────────────────────────────────────
def test_next_bar_is_a_different_occurrence_even_if_condition_holds():
    f = _frame()
    k1 = so.occurrence_key(_eval(_spec(), f.iloc[:-1]))
    k2 = so.occurrence_key(_eval(_spec(), f))
    assert k1 is not None and k2 is not None and k1 != k2
    assert k2[5] - k1[5] == TF_MS[TF]
    assert k1[:5] == k2[:5]


# 4 ───────────────────────────────────────────────────────────────────────
def test_symbol_spellings_cannot_split_an_occurrence():
    f = _frame()
    keys = {so.occurrence_key(_eval(_spec(), f, symbol=s))
            for s in ("BTC/USDT", "BTC/USDT:USDT", "BTCUSDT", "btc/usdt")}
    assert len(keys) == 1 and next(iter(keys))[2] == "BTCUSDT"
    assert so.occurrence_key(_eval(_spec(), f, symbol="ETH/USDT")) not in keys


# 5 ───────────────────────────────────────────────────────────────────────
def test_action_participates_in_identity():
    up, down = _frame(up=True), _frame(up=False)
    buy, sell = _eval(_spec(), up), _eval(_spec(), down)
    assert buy.action == Action.BUY and sell.action == Action.SELL
    kb, ks = so.occurrence_key(buy), so.occurrence_key(sell)
    assert kb[5] == ks[5]                              # same bar
    assert kb != ks and (kb[3], ks[3]) == ("BUY", "SELL")


# 6 ───────────────────────────────────────────────────────────────────────
def test_timeframe_participates_in_identity():
    """Same bar-close instant, same everything else in the persisted
    signal, different timeframe → different key."""
    sig = _persisted(_eval(_spec(), _frame()))
    other = json.loads(json.dumps(sig))
    other["params"]["signal_timeframe"] = "1h"
    assert so.occurrence_key(sig) != so.occurrence_key(other)
    assert so.occurrence_key(sig)[4] == TF


def test_signal_timeframe_is_the_spec_timeframe():
    f = _frame(freq="1h")
    sig = _eval(_spec(timeframe="1h"), f, tf="1h")
    assert sig.params["signal_timeframe"] == "1h"
    last_open = int(f["ts"].iloc[-1].value // 1_000_000)
    assert sig.params["signal_bar_close_ms"] == last_open + TF_MS["1h"]


# 7 ───────────────────────────────────────────────────────────────────────
def test_close_is_an_exact_int_from_the_evaluated_closed_bar():
    f = _frame()
    # a forming bar appended now must be dropped by closed_bars; the
    # identity names the last CLOSED bar that the entry series judged.
    now = pd.Timestamp.now(tz="UTC").floor("15min")
    forming = f.iloc[[-1]].assign(ts=[now])
    live = pd.concat([f, forming], ignore_index=True)
    sig = _eval(_spec(), live)
    close_ms = sig.params["signal_bar_close_ms"]
    assert type(close_ms) is int
    assert close_ms == int(f["ts"].iloc[-1].value // 1_000_000) + TF_MS[TF]
    assert close_ms == 1767225600000 + 300 * TF_MS[TF]  # 2026-01-01 + 300 bars


def test_ms_resolution_ts_column_gives_the_same_exact_close():
    f = _frame()
    g = f.assign(ts=f["ts"].astype("datetime64[ms, UTC]"))
    assert (_eval(_spec(), f).params["signal_bar_close_ms"]
            == _eval(_spec(), g).params["signal_bar_close_ms"])


def test_float_close_is_not_an_identity():
    sig = _persisted(_eval(_spec(), _frame()))
    sig["params"]["signal_bar_close_ms"] = float(sig["params"]["signal_bar_close_ms"])
    assert so.signal_occurrence(sig) == (None, so.INVALID_OCCURRENCE_FIELDS)


# 8 ───────────────────────────────────────────────────────────────────────
def test_age_and_wall_clock_cannot_substitute_for_a_missing_close(monkeypatch):
    f = _frame()
    import trader.strategy.compile as comp
    monkeypatch.setattr(comp, "bar_open_ms", lambda v: None)
    sig = _eval(_spec(), f)
    assert sig is not None and sig.action == Action.BUY        # still fires
    assert sig.params["signal_bar_close_ms"] is None
    assert sig.params["signal_bar_age_min"] is not None        # age recorded
    assert sig.params["signal_occurrence_unavailable"] == so.SIGNAL_BAR_CLOSE_UNAVAILABLE
    assert so.signal_occurrence(sig) == (None, so.SIGNAL_BAR_CLOSE_UNAVAILABLE)
    assert so.signal_occurrence(_persisted(sig)) == (None, so.SIGNAL_BAR_CLOSE_UNAVAILABLE)


def test_unreadable_bar_time_gives_no_identity():
    assert so.bar_open_ms(pd.NaT) is None
    assert so.bar_open_ms(1.7e12) is None
    assert so.bar_open_ms(True) is None
    assert so.bar_open_ms("2026-01-01") is None
    assert so.bar_open_ms(np.int64(1767225600000)) == 1767225600000
    assert so.bar_open_ms(pd.Timestamp("2026-01-01T00:00:00.0000005Z")) is None


def test_misaligned_entry_series_gives_no_identity(monkeypatch):
    c = compile_spec(_spec())
    real = c.entries
    monkeypatch.setattr(c, "entries", lambda *a, **k: tuple(x[1:] for x in real(*a, **k)))
    snap = Snapshot(symbol="BTC/USDT", ts="", price=1.0, dfs={TF: _frame()},
                    market_type="futures")
    sig = c.to_evaluator()(c.spec, snap)
    assert sig.params["signal_bar_close_ms"] is None
    assert so.signal_occurrence(sig) == (None, so.ENTRY_SERIES_MISALIGNED)


# 9 ───────────────────────────────────────────────────────────────────────
def test_legacy_library_signal_gets_no_identity():
    legacy = StrategySignal(strategy_id="g1", strategy_name="donchian",
                            symbol="BTC/USDT", action=Action.BUY,
                            confidence=0.7, rationale="breakout",
                            params={"period": 20})
    assert so.signal_occurrence(legacy) == (None, so.NO_CLOSED_BAR_IDENTITY)
    assert so.signal_occurrence(_persisted(legacy)) == (None, so.NO_CLOSED_BAR_IDENTITY)
    assert so.signal_occurrence(None) == (None, so.NO_CLOSED_BAR_IDENTITY)
    # a legacy row persisted before v1 carried only spec_id and age
    old = {"symbol": "BTC/USDT", "action": "BUY",
           "params": {"spec_id": "occ1", "signal_bar_age_min": 3.4}}
    assert so.signal_occurrence(old) == (None, so.NO_CLOSED_BAR_IDENTITY)


def test_real_legacy_evaluators_emit_no_identity_fields():
    from trader.strategy import library
    from trader.strategy.genome import Genome
    f = _frame(n=400)
    snap = Snapshot(symbol="BTC/USDT", ts="", price=float(f["close"].iloc[-1]),
                    dfs={"15m": f, "1h": f, "4h": f}, market_type="futures")
    for fam in [k for k in library.EVALUATORS if not k.startswith("spec:")]:
        g = Genome(strategy_id="g", family=fam, hypothesis="x" * 70,
                   invalidation="y" * 30, regime_filter=frozenset(),
                   markets=frozenset({"futures"}), params={})
        sig = library.evaluate(g, snap)
        if sig is not None:
            assert "signal_bar_close_ms" not in (sig.params or {})
            assert so.signal_occurrence(sig) == (None, so.NO_CLOSED_BAR_IDENTITY)


# 10 ──────────────────────────────────────────────────────────────────────
def test_signal_behavior_is_unchanged_bar_by_bar():
    """Direction still equals the last closed bar of entries(); the existing
    fields keep their values; only the identity params are added."""
    spec = _spec(entry_long="close > ema(5)", entry_short="close < ema(5)")
    rng = np.random.default_rng(3)
    f = _frame(n=260)
    f["close"] = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, len(f))))
    c = compile_spec(spec)
    for end in range(230, 261, 3):
        sub = f.iloc[:end]
        lo, sh = c.entries({TF: sub})
        sig = _eval(spec, sub)
        want = Action.BUY if lo[-1] else Action.SELL if sh[-1] else None
        assert (sig.action if sig else None) == want
        if sig:
            assert sig.strategy_id == "occ1" and sig.confidence == 0.6
            assert sig.symbol == "BTC/USDT"
            assert sig.rationale == f"Occurrence Probe: {spec.entry_long if lo[-1] else spec.entry_short}"
            assert set(sig.params) == {"spec_id", "signal_bar_age_min",
                                       "spec_fingerprint", "signal_timeframe",
                                       "signal_bar_close_ms"}
            assert sig.params["spec_id"] == "occ1"
            assert isinstance(sig.params["signal_bar_age_min"], float)


def test_no_signal_is_still_none():
    spec = _spec(entry_long="close > open * 2", entry_short="close < open * 0.5")
    assert _eval(spec, _frame()) is None


def test_journal_signals_json_round_trip(tmp_path):
    sig = _eval(_spec(), _frame())
    j = Journal(tmp_path / "j.db")
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol) VALUES ('c','t','BTC/USDT')")
    d = Decision("d1", "c", "BTC/USDT", Action.BUY, .5, .2, .6, [],
                 [vars(sig) | {"action": sig.action.value}])
    j.log_decision(d)
    con = sqlite3.connect(tmp_path / "j.db")
    stored = json.loads(con.execute(
        "SELECT signals_json FROM decisions WHERE id='d1'").fetchone()[0])
    assert so.occurrence_key(stored[0]) == so.occurrence_key(sig) is not None
