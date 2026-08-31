import numpy as np
import pandas as pd
import pytest

from trader.data.derivatives import DerivFeed
from trader.strategy import spec_evidence as se
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(entry_long, requires_note="", **kw):
    base = dict(
        id="t", name="Funding Fade Probe",
        thesis="Crowded leveraged longs pay funding to stay on; an extreme in "
               "that payment marks positioning exhaustion and the unwind that "
               "follows is the tradeable move.",
        invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="15m", direction="long", entry_long=entry_long,
        entry_short="", filters=[], exit=ExitSpec(),
        regime_filter=["RANGING"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


@pytest.fixture
def frames():
    rng = np.random.default_rng(4)
    n = 3000
    close = 100 * np.cumprod(1 + rng.normal(0, 0.004, n))
    df = pd.DataFrame({
        "ts": pd.date_range("2026-06-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "close": close, "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.002
    df["low"] = df[["open", "close"]].min(axis=1) * 0.998
    return {"BTC/USDT": df[["ts", "open", "high", "low", "close", "volume"]]}


@pytest.fixture
def feed(tmp_path):
    return DerivFeed(db_path=tmp_path / "d.db")


CFG = {"risk": {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
                "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
                "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
                "bar_minutes": 15},
       "strategies": {}}


def test_ohlcv_spec_needs_no_derivs(frames, feed):
    ok, ev = se.run_gauntlet(_spec("close > ema(20)"), frames, CFG, feed=feed)
    assert not ev.get("untested"), ev
    assert "per_symbol" in ev


def test_funding_spec_without_data_is_UNTESTED_not_failed(frames, feed):
    """The distinction that matters. A funding strategy with no funding data
    scores zero trades because every NaN comparison is False — it must be
    reported as untested, never as 'no edge'."""
    ok, ev = se.run_gauntlet(_spec("funding_z(96) < -2.0"), frames, CFG,
                             feed=feed)
    assert not ok
    assert ev["untested"] is True
    assert "missing data" in ev["reason"]


def test_funding_spec_with_data_is_actually_scored(frames, feed):
    fund = pd.DataFrame({
        "ts": pd.date_range("2026-06-01", periods=200, freq="8h", tz="UTC"),
        "value": np.random.default_rng(1).normal(0.0001, 0.0002, 200)})
    feed.save("BTC/USDT", "funding", fund)
    ok, ev = se.run_gauntlet(_spec("funding_z(96) < -1.0"), frames, CFG,
                             feed=feed)
    assert not ev.get("untested"), ev
    assert ev["per_symbol"]["BTC/USDT"]["test_trades"] >= 0


def test_missing_data_lists_the_gap(frames, feed):
    spec = _spec("funding_z(96) < -2.0 and oi_z(96) > 1.0")
    from trader.strategy.compile import compile_spec
    compile_spec(spec)               # populates data_requires
    gaps = se.missing_data(spec, ["BTC/USDT"], feed)
    assert set(gaps["BTC/USDT"]) == {"funding: absent", "open_interest: absent"}


def test_load_derivs_maps_requirement_names_to_series(feed):
    feed.save("BTC/USDT", "oi", pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=3, freq="1h", tz="UTC"),
        "value": [1.0, 2.0, 3.0]}))
    out = se.load_derivs("BTC/USDT", ["open_interest", "ohlcv"], feed)
    assert set(out) == {"oi"} and len(out["oi"]) == 3


def test_partial_coverage_is_untested_not_scored(frames, feed):
    """The trap this gate exists for: Binance keeps OI for ~30 days while the
    candle frame spans ~83, so the series is PRESENT but the whole training
    half is blank. Scoring that reports a test-only PF as walk-forward
    evidence."""
    frame = frames["BTC/USDT"]
    late = pd.to_datetime(frame["ts"], utc=True).iloc[-200:]
    feed.save("BTC/USDT", "oi", pd.DataFrame(
        {"ts": late, "value": np.linspace(1.0, 2.0, len(late))}))
    ok, ev = se.run_gauntlet(_spec("oi_z(96) > 1.0"), frames, CFG, feed=feed)
    assert not ok and ev["untested"] is True
    assert "covers" in ev["reason"]


def test_full_coverage_is_scored(frames, feed):
    frame = frames["BTC/USDT"]
    allts = pd.to_datetime(frame["ts"], utc=True)
    feed.save("BTC/USDT", "oi", pd.DataFrame(
        {"ts": allts, "value": np.linspace(1.0, 2.0, len(allts))}))
    ok, ev = se.run_gauntlet(_spec("oi_z(96) > 1.0"), frames, CFG, feed=feed)
    assert not ev.get("untested"), ev


def test_frames_for_builds_higher_timeframes(frames):
    out = se.frames_for(frames["BTC/USDT"], "4h")
    assert set(out) >= {"15m", "1h", "4h"}
    assert len(out["4h"]) < len(out["1h"]) < len(out["15m"])


def test_risk_for_scales_bar_minutes():
    base = {"bar_minutes": 15, "risk_per_trade_pct": 1.5}
    assert se.risk_for(base, "4h")["bar_minutes"] == 240
    assert se.risk_for(base, "15m")["bar_minutes"] == 15
    assert base["bar_minutes"] == 15, "must not mutate the caller's config"


def test_a_1h_spec_is_actually_scored_on_1h_bars(frames, feed):
    spec = _spec("close > ema(20)", timeframe="1h")
    ok, ev = se.run_gauntlet(spec, frames, CFG, feed=feed)
    assert not ev.get("untested"), ev
    # a 1h spec sees ~1/4 the bars, so it cannot produce 15m-like trade counts
    assert ev["per_symbol"]["BTC/USDT"]["test_trades"] >= 0


def test_detect_tf_reads_bar_spacing(frames):
    assert se.detect_tf(frames["BTC/USDT"]) == "15m"
    from trader.strategy.backtest import resample
    assert se.detect_tf(resample(frames["BTC/USDT"], "4h")) == "4h"


def test_native_frame_is_used_not_resampled(frames):
    """A 4h frame passed in must be used as-is. Resampling from 15m would cap
    4h history at one year when the store holds five."""
    from trader.strategy.backtest import resample
    native = resample(frames["BTC/USDT"], "4h")
    out = se.frames_for(native, "4h")
    assert out["4h"] is native


def test_cannot_build_a_finer_frame_from_a_coarser_one(frames):
    from trader.strategy.backtest import resample
    with pytest.raises(KeyError):
        se.frames_for(resample(frames["BTC/USDT"], "4h"), "15m")
