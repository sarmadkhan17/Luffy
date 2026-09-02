"""The drawdown ladder must survive a restart.

`_peak_equity` and `_day_start_equity` lived only in memory. On boot the
first equity reading became the new peak, so an account 20% underwater
believed it was at its high-water mark: the derisk steps read 0% drawdown and
`halt_drawdown_pct` could never fire. The daily loss breaker reset the same
way — a bad day was forgiven by a restart, which is exactly when a restart is
most likely to happen.
"""
import json

from trader.engine.risk import RiskManager

CFG = {"risk": {
    "risk_per_trade_pct": 1.0, "portfolio_heat_cap_pct": 6.0,
    "per_symbol_risk_cap_pct": 2.0, "max_open_positions": 4,
    "max_daily_loss_pct": 3.0, "halt_drawdown_pct": 20.0, "leverage": 3,
    "stop_loss_atr_mult": 2.0, "min_notional_usdt": 10.0,
    "drawdown_derisk_steps": [{"dd_pct": 5.0, "size_mult": 0.5}]}}


class _Journal:
    def __init__(self): self.kv = {}
    def kv_get(self, key, default=None): return self.kv.get(key, default)
    def kv_set(self, key, value): self.kv[key] = value


def test_the_peak_survives_a_restart():
    j = _Journal()
    RiskManager(CFG, j).update_equity(10000.0)
    RiskManager(CFG, j).update_equity(9000.0)          # restarted, down 10%
    st = RiskManager(CFG, j).update_equity(9000.0)
    assert st["drawdown_pct"] == 10.0, \
        "a restart must not reset the high-water mark"


def test_the_halt_breaker_still_fires_after_a_restart():
    j = _Journal()
    RiskManager(CFG, j).update_equity(10000.0)
    st = RiskManager(CFG, j).update_equity(7900.0)     # -21%
    assert st["halt_breached"] is True


def test_a_new_high_advances_the_stored_peak():
    j = _Journal()
    RiskManager(CFG, j).update_equity(10000.0)
    RiskManager(CFG, j).update_equity(12000.0)
    assert RiskManager(CFG, j).update_equity(11000.0)["drawdown_pct"] == \
        round((12000 - 11000) / 12000 * 100, 2)


def test_the_daily_baseline_survives_a_restart_within_the_same_day():
    j = _Journal()
    RiskManager(CFG, j).update_equity(10000.0)
    st = RiskManager(CFG, j).update_equity(9700.0)
    assert st["daily_pnl_pct"] == -3.0, \
        "a losing day must not be forgiven by a restart"


def test_a_new_utc_day_rebases_the_daily_baseline():
    j = _Journal()
    RiskManager(CFG, j).update_equity(10000.0)
    j.kv["risk_state"] = json.dumps({
        **json.loads(j.kv["risk_state"]), "day_key": "1999-01-01"})
    assert RiskManager(CFG, j).update_equity(9700.0)["daily_pnl_pct"] == 0.0


def test_absent_state_starts_clean():
    st = RiskManager(CFG, _Journal()).update_equity(10000.0)
    assert st["drawdown_pct"] == 0.0 and st["halt_breached"] is False


def test_corrupt_state_does_not_crash_the_risk_manager():
    j = _Journal()
    j.kv["risk_state"] = "{not json"
    assert RiskManager(CFG, j).update_equity(10000.0)["drawdown_pct"] == 0.0


def test_a_journal_without_kv_is_tolerated():
    """Backtests construct a RiskManager with no journal at all."""
    assert RiskManager(CFG, None).update_equity(10000.0)["drawdown_pct"] == 0.0
