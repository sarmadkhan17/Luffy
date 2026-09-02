"""Risk sizing must cap notional and margin, not only distance-to-stop.

`amount = allowed / stop_dist` bounds the loss if the stop holds, and bounds
nothing else. With the 0.4% stop floor, a $23 risk budget becomes $5,750 of
notional — $1,150 of margin at 5x, a quarter of a $4,683 account, in ONE
position. Eight of those needs $9,200 of margin the account does not have.

That was survivable at a 4-position cap with wide ATR stops. At 8 positions
it is reachable, and a stop only bounds the loss when it fills: a gap through
it loses the notional, not the risk budget.

Sizing is reduced to fit rather than refused, because a smaller correct
position is better than no position — and refused only when the fit falls
under the venue minimum.
"""
import pytest

from trader.core.types import ControlState, Position, Side
from trader.engine.risk import RiskManager

CFG = {"risk": {
    "risk_per_trade_pct": 0.5, "portfolio_heat_cap_pct": 15.0,
    "per_symbol_risk_cap_pct": 8.0, "max_open_positions": 8,
    "max_daily_loss_pct": 6.0, "halt_drawdown_pct": 20.0, "leverage": 5,
    "stop_loss_atr_mult": 2.5, "min_notional_usdt": 10.0,
    "max_position_margin_pct": 20.0, "max_total_margin_pct": 70.0,
    "proving_period_trades": 0, "drawdown_derisk_steps": []}}
EQ = 4683.0


def _rm():
    return RiskManager(CFG, None)


def _pos(notional, symbol="OTHER/USDT"):
    return Position(id=symbol, symbol=symbol, side=Side.LONG,
                    amount=notional / 100.0, entry_price=100.0,
                    notional_usdt=notional, stop_loss=98.0)


def _entry(rm, stop_frac=0.004, open_positions=()):
    return rm.check_entry(ControlState.ACTIVE, "BTC/USDT", 100.0, 1.0,
                          stop_frac, list(open_positions), EQ, 100, "futures")


def test_a_tight_stop_no_longer_buys_an_unbounded_notional():
    r = _entry(_rm())
    assert r.ok
    assert r.size_usdt <= EQ * 0.20 + 0.01, \
        "one position must not command a quarter of the account"


def test_a_normal_atr_stop_is_left_alone():
    """The cap must bind on pathologies, not on ordinary sizing."""
    rm = _rm()
    r = _entry(rm, stop_frac=0.05)          # a 2 ATR stop on 4h crypto
    assert r.ok
    assert r.size_usdt < EQ * 0.20, "ordinary sizing is far under the cap"
    expected = (EQ * rm.risk_pct / 0.05) / rm.leverage
    assert r.size_usdt == pytest.approx(expected, rel=0.01)


def test_total_margin_across_open_positions_is_capped():
    """Eight tight-stopped positions must not need more margin than exists."""
    rm = _rm()
    # 65% of equity already committed as margin (notional = margin x leverage)
    committed = [_pos(EQ * 0.65 * rm.leverage, symbol="A/USDT")]
    r = _entry(rm, open_positions=committed)
    if r.ok:
        assert r.size_usdt <= EQ * 0.70 - EQ * 0.65 + 0.01
    else:
        assert "margin" in r.reason


def test_no_margin_headroom_refuses_the_entry():
    rm = _rm()
    full = [_pos(EQ * 0.70 * rm.leverage, symbol="A/USDT")]
    r = _entry(rm, open_positions=full)
    assert not r.ok and "margin" in r.reason


def test_a_position_shrunk_below_the_venue_minimum_is_refused():
    rm = _rm()
    nearly_full = [_pos(EQ * 0.6999 * rm.leverage, symbol="A/USDT")]
    r = _entry(rm, open_positions=nearly_full)
    assert not r.ok


def test_the_risk_budget_still_binds_when_it_is_the_tighter_one():
    """Capping margin must not quietly enlarge a position."""
    rm = _rm()
    r = _entry(rm, stop_frac=0.05)
    assert r.risk_usdt <= EQ * rm.risk_pct + 0.01
