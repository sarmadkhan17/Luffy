"""Legacy backtest evidence correctly measures loss.

The TV family-proxy duplicate tests are retired with trader/brain/tv.py (removed by Review 07).
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from trader.core.types import Action
from trader.strategy import backtest as legacy
from trader.strategy.genome import Genome


def test_initial_loss_counts_toward_legacy_drawdown_gate(monkeypatch):
    frame = pd.DataFrame(dict(
        ts=pd.date_range('2026-01-01', periods=214, freq='15min', tz='UTC'),
        open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0))
    frame.loc[211, 'low'] = 96.0
    genome = Genome('synthetic', 'ema_trend', 'x' * 60, 'y' * 20,
                    frozenset({'RANGING'}), frozenset({'futures'}))
    monkeypatch.setattr(legacy, '_atr', lambda _: 1.0)
    monkeypatch.setattr(legacy.strat_lib, 'evaluate', lambda _, snap:
                        SimpleNamespace(action=Action.BUY) if len(snap.dfs['15m']) == 211 else None)
    result = legacy.backtest(genome, frame, dict(stop_loss_atr_mult=2.5,
        take_profit_atr_mult=4.5, taker_fee_pct=0, slippage_atr_frac=0,
        funding_rate_8h=0, risk_per_trade_pct=5))
    assert result.trades == 1 and result.losses == 1
    assert result.pnl_usdt == pytest.approx(-100)
    assert result.max_dd_pct == pytest.approx(5)
    assert any('DD' in failure for failure in result.passes(max_dd=4)[1])
