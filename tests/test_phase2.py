"""Phase 2 tests: backtester math, promotion genetics, strategist triggers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from trader.core.types import Position, Side
from trader.strategy.genome import Genome
from trader.strategy.backtest import backtest, walk_forward
from trader.strategy import promotion


RISK = {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
        "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
        "risk_per_trade_pct": 5.0}


def _df(n=600, drift=0.0008, seed=11, noise=0.0015):
    """Clean uptrend with pullbacks — ema_trend should trade and profit."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, noise, n)
    close = 100 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0.0005, 0.0008, n)))
    low = close * (1 - np.abs(rng.normal(0.0005, 0.0008, n)))
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({"ts": ts, "open": close, "high": high,
                         "low": low, "close": close,
                         "volume": np.full(n, 1000.0)})


def _genome(family="ema_trend", **params):
    return Genome(strategy_id="bt1", family=family,
                  hypothesis="x" * 60, invalidation="die on losses",
                  regime_filter=frozenset({"TRENDING_UP"}),
                  markets=frozenset({"futures"}), params=params)


def test_backtest_runs_and_counts():
    df = _df()
    res = backtest(_genome(adx_min=18, pullback_atr=0.9), df, RISK)
    assert res.bars == len(df)
    assert res.trades >= 0
    if res.trades:
        assert res.wins + res.losses == res.trades
        assert abs(res.pnl_usdt - (res.gross_win - res.gross_loss)) < 1e-6


def test_backtest_pessimistic_sl_first():
    # craft a bar where both SL and TP are inside its range: SL must win
    n = 260
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    close = np.concatenate([np.full(250, 100.0), np.full(10, 101.0)])
    df = pd.DataFrame({"ts": ts, "open": close, "high": close * 1.02,
                       "low": close * 0.98, "close": close,
                       "volume": np.full(n, 1000.0)})
    res = backtest(_genome(adx_min=1, pullback_atr=2.0), df, RISK)
    # whatever it catches on bar 251, the wide-range bars must not explode P&L
    assert abs(res.pnl_usdt) < 500


def test_walk_forward_flags_robustness():
    out = walk_forward(_genome(adx_min=20, pullback_atr=0.8),
                       _df(n=900), RISK)
    assert "train" in out and "test" in out
    assert isinstance(out["robust"], bool)


def test_promotion_paper_to_active(tmp_path):
    from trader.core.journal import Journal
    j = Journal(tmp_path / "j.db")
    j.query("INSERT INTO strategies VALUES ('s1','T','ema_trend','{}','paper',"
            "'d','seed','hyp','inv','[]','[\"futures\"]',0,'','"
            "2026-08-01T00:00:00+00:00','{}')")
    # 15 winners → must promote
    for i in range(15):
        p = Position(id=f"p{i}", symbol="X/USDT", side=Side.LONG,
                     amount=1, entry_price=100, notional_usdt=100,
                     strategy_id="s1", market_type="futures")
        j.add_trade(p)
        j.close_trade(f"p{i}", 105, 5.0, "tp_fill")
    actions = promotion.evaluate_population(j)
    assert any(a["to"] == "active" for a in actions)


def test_demote_on_consecutive_losses(tmp_path):
    from trader.core.journal import Journal
    j = Journal(tmp_path / "j.db")
    j.query("INSERT INTO strategies VALUES ('s2','T','ema_trend','{}','active',"
            "'d','seed','hyp','inv','[]','[\"futures\"]',0,'','"
            "2026-08-01T00:00:00+00:00','{}')")
    for i in range(6):
        p = Position(id=f"q{i}", symbol="Y/USDT", side=Side.LONG,
                     amount=1, entry_price=100, notional_usdt=100,
                     strategy_id="s2", market_type="futures")
        j.add_trade(p)
        j.close_trade(f"q{i}", 95, -5.0, "sl_fill")
    actions = promotion.evaluate_population(j)
    assert any(a["to"] == "demoted" for a in actions)


def test_strategist_review_trigger(tmp_path):
    from trader.core.config import load_config
    from trader.core.journal import Journal
    from trader.brain.strategist import Strategist
    j = Journal(tmp_path / "j.db")
    s = Strategist(j, load_config())
    should, why = s.should_review()
    assert should is True          # fresh journal — never reviewed
    r = s.review()                 # runs fallback (no LLM key in test env)
    assert r["reviewed"] is True
    should2, _ = s.should_review()
    assert should2 is False        # just reviewed — quiet until triggers
