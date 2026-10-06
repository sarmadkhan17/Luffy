"""QNT-01: reproducible, cost-aware definitions; no future in labels/features; engine parity."""
import numpy as np
import pandas as pd
import pytest

from trader.strategy import quant_contract as Q
from trader.strategy import spec_evidence as se
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec
from trader.strategy.vector_backtest import (WARMUP, simulate, trade_table,
                                             vector_walk_forward, walk_table)

RISK = {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5, "taker_fee_pct": 0.05,
        "slippage_atr_frac": 0.06, "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
        "bar_minutes": 15}
EXPRS = ["close > ema(20) and rsi(14) < 60", "zscore(close, 96) < -1.0",
         "adx(14) > 20 and close > sma_of(close, 50)"]


def _df(n=1500, seed=3):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.004, n))
    df = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
                       "open": close, "close": close, "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.003
    df["low"] = df[["open", "close"]].min(axis=1) * 0.997
    return df[["ts", "open", "high", "low", "close", "volume"]]


def _spec(expr, **kw):
    base = dict(id="q1", name="Quant Contract Probe",
                thesis="Crowded leveraged longs pay funding to stay on; an extreme in that payment marks positioning exhaustion and the unwind that follows is the tradeable move.",
                invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
                provenance={"source_kind": "test"}, universe={"include": []}, timeframe="15m",
                direction="long", entry_long=expr, entry_short="", filters=[], exit=ExitSpec(),
                regime_filter=[], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


@pytest.mark.parametrize("expr", EXPRS)
def test_entries_use_no_future_bars(expr):
    """Signals on bar i are identical whether or not later bars exist."""
    df = _df()
    c = compile_spec(_spec(expr))
    full, _ = c.entries({"15m": df})
    for k in (700, 1100):
        part, _ = c.entries({"15m": df.iloc[:k].reset_index(drop=True)})
        assert np.array_equal(np.asarray(full)[:k], np.asarray(part)), (expr, k)


def test_trade_outcome_uses_only_bars_up_to_its_exit():
    """Truncating the data right after a trade's exit cannot change its label or P&L."""
    df = _df()
    n = len(df)
    lo = np.zeros(n, bool)
    lo[300::97] = True
    sh = np.zeros(n, bool)
    exit_spec = ExitSpec()
    ref = simulate(lo, sh, df, exit_spec, RISK)
    fills = []
    simulate(lo, sh, df, exit_spec, RISK, fills_out=fills)
    assert ref.trades > 3
    f = fills[2]
    k = f.exit_i + 1
    part = simulate(lo[:k], sh[:k], df.iloc[:k].reset_index(drop=True), exit_spec, RISK, fills_out=(pf := []))
    assert [(x.entry_i, x.exit_i, round(x.r_multiple, 12)) for x in pf[:3]] == \
           [(x.entry_i, x.exit_i, round(x.r_multiple, 12)) for x in fills[:3]]


def test_walk_table_and_simulate_agree_on_trades_and_r():
    df = _df()
    n = len(df)
    rng = np.random.default_rng(9)
    lo = rng.random(n) < 0.03
    sh = rng.random(n) < 0.03
    fills = []
    simulate(lo, sh, df, ExitSpec(), RISK, fills_out=fills)
    got = walk_table(trade_table(df, ExitSpec(), RISK, None), lo, sh, RISK)
    assert [(f.entry_i, f.exit_i) for f in fills] == [(a, b) for a, b, _ in got]
    assert np.allclose([f.r_multiple for f in fills], [r for *_, r in got])


def test_costs_are_inside_the_return_and_monotonic():
    df = _df()
    n = len(df)
    lo = np.zeros(n, bool)
    lo[300::50] = True
    sh = np.zeros(n, bool)
    free = simulate(lo, sh, df, ExitSpec(), {**RISK, "taker_fee_pct": 0.0, "slippage_atr_frac": 0.0})
    paid = simulate(lo, sh, df, ExitSpec(), RISK)
    assert free.trades == paid.trades and paid.pnl_usdt < free.pnl_usdt


def test_cost_model_is_labelled_estimated_never_actual():
    cm = Q.cost_model({})
    assert cm["basis"] == "ESTIMATED_MODEL" and "actual" not in str(cm).lower().replace("actual/null", "")
    assert Q.DEFINITIONS["cost_basis"] == "ESTIMATED_MODEL"
    for key in ("entry", "label", "slippage", "fees", "funding", "return", "expectancy", "max_drawdown"):
        assert Q.DEFINITIONS[key]


def test_expectancy_zero_trades_is_none_not_zero():
    df = _df()
    r = simulate(np.zeros(len(df), bool), np.zeros(len(df), bool), df, ExitSpec(), RISK)
    assert Q.expectancy(r)["usdt_per_trade"] is None


def test_drawdown_matches_definition_closed_trade_equity_from_starting_equity():
    df = _df()
    n = len(df)
    rng = np.random.default_rng(1)
    lo = rng.random(n) < 0.05
    fills = []
    r = simulate(lo, np.zeros(n, bool), df, ExitSpec(), RISK, equity=2000.0, fills_out=fills)
    eq, peak, dd = 2000.0, 2000.0, 0.0
    for f in fills:
        eq += f.r_multiple * eq * 0.015
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak * 100)
    assert r.max_dd_pct == pytest.approx(dd, rel=1e-9)


def test_pinned_data_code_config_reproduce_identical_result_and_identity():
    df = _df()
    spec = _spec(EXPRS[0])
    c = compile_spec(spec)
    a = vector_walk_forward(c, {"15m": df}, RISK)
    b = vector_walk_forward(c, {"15m": df.copy()}, RISK)
    for part in ("train", "test"):
        assert a[part].__dict__ == b[part].__dict__
    ia, ib = Q.run_identity(spec, {"15m": df}, RISK), Q.run_identity(spec, {"15m": df.copy()}, RISK)
    assert ia == ib


def test_identity_changes_with_data_config_or_spec():
    df = _df()
    spec = _spec(EXPRS[0])
    base = Q.run_identity(spec, {"15m": df}, RISK)["identity"]
    d2 = df.copy()
    d2.loc[500, "close"] *= 1.0001
    assert Q.run_identity(spec, {"15m": d2}, RISK)["identity"] != base
    assert Q.run_identity(spec, {"15m": df}, {**RISK, "taker_fee_pct": 0.06})["identity"] != base
    assert Q.run_identity(_spec(EXPRS[1]), {"15m": df}, RISK)["identity"] != base


def test_gauntlet_evidence_carries_contract_and_estimated_cost(tmp_path):
    from trader.data.derivatives import DerivFeed
    df = _df(3000)
    ok, ev = se.run_gauntlet(_spec(EXPRS[0]), {"BTC/USDT": df, "_btc_1h": df}, {"risk": RISK},
                             feed=DerivFeed(db_path=tmp_path / "d.db"))
    qc = ev["quant_contract"]
    assert qc["cost_model"]["basis"] == "ESTIMATED_MODEL"
    assert qc["run"]["identity"] and qc["definitions"]["max_drawdown"]
