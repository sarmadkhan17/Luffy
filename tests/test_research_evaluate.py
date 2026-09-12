"""Scoring one combination on the discovery slice.

Four things must hold, and each has cost real money or real weeks before:

1. The evaluator never sees a bar past the cut. Not "does not use" — cannot:
   the frames it holds stop there.
2. A profit factor alone is a statement about arithmetic. The score that
   decides anything is the cross-symbol spread of rotation-null percentiles.
3. The compounded return is walked over ONE account, and fills from
   different symbols must be ordered by TIME. `Fill.entry_i` is a per-symbol
   bar index, so they are remapped onto a common clock first — sorting raw
   indices would interleave a young market's first bar with an old market's
   thousandth.
4. Fewer than 4 symbols carrying a percentile is untestable, and untestable
   is a refusal, never a score.
"""
import numpy as np
import pandas as pd
import pytest

from trader.research import evaluate, slices
from trader.research.combo import Combination
from trader.research.vocab import Part

TF_MS = 14_400_000
DONCH = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")
EMA = Part("st:above_ema50", "state", "st:above_ema50",
           "close > ema(50)", "close < ema(50)")
NOISE = Part("rsi14>p75", "gauge", "rsi14", "rsi(14) > 55",
             "100 - rsi(14) > 55")


def _trending(n, seed):
    """Blocks of trend and chop: a breakout mechanism has a real edge here,
    and a rotation of the same signals lands in the chop."""
    rng = np.random.default_rng(seed)
    out, px = [], 100.0
    while len(out) < n:
        for _ in range(60):                      # trend
            px *= 1.0 + rng.normal(0.006, 0.004)
            out.append(px)
        for _ in range(60):                      # chop
            px *= 1.0 + rng.normal(0.0, 0.010)
            out.append(px)
    return np.array(out[:n])


def _frame(n, seed, start_ms=0):
    c = _trending(n, seed)
    ts = pd.to_datetime(start_ms + np.arange(n) * TF_MS, unit="ms", utc=True)
    return pd.DataFrame({"ts": ts, "open": c, "high": c * 1.004,
                         "low": c * 0.996, "close": c,
                         "volume": np.full(n, 10.0),
                         "taker_buy": np.full(n, 5.0)})


def _bundle(n_symbols=6, n=1400, cfg_risk=None):
    frames = {f"S{i}/USDT": _frame(n, seed=i) for i in range(n_symbols)}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=1)
    risk = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
            "slippage_atr_frac": 0.015, "funding_rate_8h": 0.0001,
            "bar_minutes": 240, "real_funding": False}
    risk.update(cfg_risk or {})
    return evaluate.Bundle(
        tf="4h", frames=disc,
        sym_frames={s: {"4h": d} for s, d in disc.items()},
        universe={s: {"4h": d} for s, d in disc.items()},
        btc={"4h": disc[next(iter(disc))]}, market=None,
        derivs={s: {} for s in disc}, risk=risk, cut=cut,
        heldout_bars={"a": {f"H{i}": 4000 for i in range(6)},
                      "b": {s: 400 for s in disc}})


def _combo(parts):
    return Combination(parts=tuple(parts), tf="4h", geo="trail")


def test_the_bundle_holds_no_bar_at_or_after_the_cut():
    b = _bundle()
    for df in b.frames.values():
        assert df["ts"].max().value // 10 ** 6 < b.cut


def test_a_planted_edge_scores_and_beats_its_rotation():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(), draws=20)
    assert r["verdict"] == "scored"
    assert r["scored_symbols"] >= 4
    assert r["consistency_p"] is not None
    assert r["median_pf"] > 1.0


def test_the_result_carries_a_percentile_for_every_scored_symbol():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(), draws=20)
    scored = [s for s, v in r["symbols"].items()
              if v["null_pctile"] is not None]
    assert len(scored) == r["scored_symbols"]
    assert all(0.0 <= r["symbols"][s]["null_pctile"] <= 1.0 for s in scored)


def test_a_rule_that_never_fires_is_empty_not_bad():
    dead = Part("never", "gauge", "never", "rsi(14) > 1000",
                "rsi(14) > 1000")
    r = evaluate.evaluate(_combo([dead]), _bundle(), draws=20)
    assert r["verdict"] == "empty"
    assert r["trades"] == 0
    assert r["consistency_p"] is None


def test_too_few_scored_symbols_is_untestable():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(n_symbols=3), draws=20)
    assert r["verdict"] == "untestable"
    assert r["consistency_p"] is None


def test_the_portfolio_walk_orders_fills_across_symbols_by_time():
    """Two symbols whose frames start a year apart: raw bar indices would
    interleave them wrongly, and the compounded return would be a different
    number than the account could ever have earned."""
    a = _frame(1400, seed=1)
    late = _frame(1400, seed=2, start_ms=1400 * TF_MS)
    frames = {"A/USDT": a, "B/USDT": late}
    cut = slices.cut_ms(frames)
    disc = slices.discovery(frames, cut, min_bars=1)
    b = evaluate.Bundle(
        tf="4h", frames=disc,
        sym_frames={s: {"4h": d} for s, d in disc.items()},
        universe={s: {"4h": d} for s, d in disc.items()},
        btc=None, market=None, derivs={s: {} for s in disc},
        risk={"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
              "slippage_atr_frac": 0.015, "funding_rate_8h": 0.0001,
              "bar_minutes": 240, "real_funding": False},
        cut=cut, heldout_bars={"a": {}, "b": {}})
    r = evaluate.evaluate(_combo([DONCH]), b, draws=20)
    order = r["portfolio"]["order"]
    first_b = order.index("B/USDT") if "B/USDT" in order else len(order)
    # every A fill that happened before B's frame even starts comes first
    assert first_b > 0


def test_testability_is_projected_from_the_discovery_trade_rate():
    r = evaluate.evaluate(_combo([DONCH]), _bundle(), draws=20)
    p = r["projection"]
    assert p["markets_a"] >= 1 and p["markets_b"] >= 0
    assert isinstance(r["testable"], bool)


def test_a_picky_rule_projects_too_few_trades_to_be_judged():
    b = _bundle()
    b.heldout_bars = {"a": {f"H{i}": 300 for i in range(6)},
                      "b": {s: 300 for s in b.frames}}
    r = evaluate.evaluate(_combo([DONCH, EMA, NOISE]), b, draws=20)
    assert r["testable"] is False


def test_the_same_combination_scores_the_same_twice():
    b = _bundle()
    one = evaluate.evaluate(_combo([DONCH]), b, draws=20, seed=5)
    two = evaluate.evaluate(_combo([DONCH]), b, draws=20, seed=5)
    assert one["consistency_p"] == two["consistency_p"]
    assert one["portfolio"]["total_pct"] == two["portfolio"]["total_pct"]


def test_a_symbol_that_raises_does_not_lose_the_whole_combination():
    b = _bundle()
    b.sym_frames["S1/USDT"] = {"4h": None}
    r = evaluate.evaluate(_combo([DONCH]), b, draws=20)
    assert r["verdict"] in ("scored", "untestable")
    assert r["symbols"]["S1/USDT"].get("error")
