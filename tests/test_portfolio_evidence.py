"""A diversified mechanism must be judged as a portfolio, not symbol by symbol.

The worst-symbol gate is right for a single-symbol claim. It is wrong for a
mechanism whose whole design is diversification: trend following takes many
small losses and a few large wins spread across markets, so individual
symbols are SUPPOSED to have losing windows. Donchian Breakout Trail was
profitable on 4 of 5 symbols (ETH 1.32, XRP 1.56, SUI 2.33, SOL 1.05) and
failed the gate on BTC's 0.84 alone.

Judging it correctly means walking every signal in time order against ONE
shared balance with a cap on concurrent positions. Per-sleeve arithmetic
divides profit across N notional accounts and understates return on capital
by ~N; an uncapped shared account overstates it, because 18 simultaneous
1%-risk positions in correlated markets is one position of 18% risk.
"""
import numpy as np
import pytest

from trader.strategy.portfolio_evidence import Fill, portfolio_curve


def _fills(n, r, start=0, step=10, hold=5, sym="X"):
    return [Fill(entry_i=start + i * step, exit_i=start + i * step + hold,
                 r_multiple=r, symbol=sym) for i in range(n)]


def test_a_flat_edge_compounds_upward():
    res = portfolio_curve(_fills(50, 0.2), equity=1000.0, risk_pct=1.0,
                          max_open=4)
    assert res["final"] > 1000.0
    assert res["taken"] == 50


def test_losses_compound_downward_and_never_go_negative():
    res = portfolio_curve(_fills(200, -1.0), equity=1000.0, risk_pct=1.0,
                          max_open=4)
    assert 0.0 < res["final"] < 1000.0


def test_position_cap_refuses_overlapping_signals():
    """Ten signals open on the same bar; a 4-position cap takes 4."""
    fills = [Fill(entry_i=0, exit_i=100, r_multiple=1.0, symbol=f"S{i}")
             for i in range(10)]
    assert portfolio_curve(fills, 1000.0, 1.0, max_open=4)["taken"] == 4
    assert portfolio_curve(fills, 1000.0, 1.0, max_open=10)["taken"] == 10


def test_a_freed_slot_is_reusable():
    fills = [Fill(entry_i=0, exit_i=5, r_multiple=1.0, symbol="A"),
             Fill(entry_i=10, exit_i=15, r_multiple=1.0, symbol="B")]
    assert portfolio_curve(fills, 1000.0, 1.0, max_open=1)["taken"] == 2


def test_fills_are_taken_in_time_order_not_symbol_order():
    fills = [Fill(entry_i=50, exit_i=60, r_multiple=1.0, symbol="late"),
             Fill(entry_i=1, exit_i=5, r_multiple=1.0, symbol="early")]
    assert portfolio_curve(fills, 1000.0, 1.0, max_open=1)["order"] == \
        ["early", "late"]


def test_drawdown_is_measured_from_the_running_peak():
    fills = [Fill(entry_i=0, exit_i=1, r_multiple=1.0, symbol="A"),
             Fill(entry_i=2, exit_i=3, r_multiple=-1.0, symbol="A"),
             Fill(entry_i=4, exit_i=5, r_multiple=-1.0, symbol="A")]
    res = portfolio_curve(fills, 1000.0, 10.0, max_open=1)
    # 1000 -> 1100 -> 990 -> 891 ; peak 1100, trough 891
    assert res["max_dd_pct"] == pytest.approx((1100 - 891) / 1100 * 100, abs=0.1)


def test_risk_is_a_fraction_of_CURRENT_equity_so_it_compounds():
    a = portfolio_curve(_fills(30, 0.5), 1000.0, 1.0, max_open=1)["final"]
    b = portfolio_curve(_fills(30, 0.5), 2000.0, 1.0, max_open=1)["final"]
    assert b == pytest.approx(2 * a, rel=1e-9)


def test_empty_input_is_not_a_crash():
    res = portfolio_curve([], 1000.0, 1.0, max_open=4)
    assert res["final"] == 1000.0 and res["taken"] == 0
