"""Phase 0 tests: risk envelope, control state machine, genomes, seed strategies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from trader.core.types import ControlState, Position, Side, Snapshot
from trader.engine.risk import RiskManager
from trader.engine.state import ControlStateMachine
from trader.strategy.genome import Genome
from trader.strategy import library


# ── fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture
def journal(tmp_path):
    from trader.core.journal import Journal
    return Journal(tmp_path / "j.db")


@pytest.fixture
def rm(journal):
    from trader.core.config import load_config
    cfg = load_config()
    return RiskManager(cfg, journal)


def _pos(symbol="BTC/USDT", entry=100.0, sl=98.0, notional=100.0):
    return Position(id=f"t_{symbol}", symbol=symbol, side=Side.LONG,
                    amount=notional / entry, entry_price=entry,
                    notional_usdt=notional, stop_loss=sl)


def _df(n=300, drift=0.0004, seed=7):
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, 0.003, n)
    close = 100 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.0015, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0015, n)))
    vol = rng.uniform(800, 1200, n)
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({"ts": ts, "open": close, "high": high, "low": low,
                         "close": close, "volume": vol})


# ── state machine ────────────────────────────────────────────────────────
def test_state_transitions_and_persistence(journal):
    sm = ControlStateMachine(journal)
    assert sm.state == ControlState.ACTIVE and sm.can_enter()

    sm.set(ControlState.FROZEN, "operator", "test freeze")
    assert not sm.can_enter() and sm.manages_exits()
    assert ControlStateMachine(journal).state == ControlState.FROZEN  # persisted

    sm.set(ControlState.HALTED, "operator", "panic")
    assert not sm.can_enter() and not sm.manages_exits()
    assert ControlStateMachine(journal).state == ControlState.HALTED

    sm.set(ControlState.ACTIVE, "operator", "resume")
    assert sm.can_enter()
    # same-state set is a no-op, not an error
    sm.set(ControlState.ACTIVE, "operator")


def test_halted_semantics(journal):
    class FakeJ:  # minimal duck
        def kv_get(self, k, d=None): return "HALTED"
        def kv_set(self, k, v): pass
        def log_control_event(self, *a, **k): pass
    sm = ControlStateMachine(FakeJ())  # type: ignore
    assert not sm.can_enter() and not sm.manages_exits()


# ── risk manager ─────────────────────────────────────────────────────────
def test_sizing_basic(rm):
    r = rm.check_entry(ControlState.ACTIVE, "BTC/USDT", price=100.0, atr=1.0,
                       side_risk_frac=0.02, open_positions=[], equity=2000.0,
                       closed_trades_count=100, market_type="futures")
    assert r.ok
    # 5% of 2000 = $100 allowed risk; amount = 100 / (100*0.02) = 50 units → $5000 notional
    assert abs(r.amount - 50.0) < 0.01
    assert r.size_usdt == pytest.approx(5000 / rm.leverage, rel=0.02)
    assert r.size_mult == 1.0


def test_proving_period_halves_size(rm):
    r = rm.check_entry(ControlState.ACTIVE, "BTC/USDT", 100.0, 1.0, 0.02,
                       [], 2000.0, closed_trades_count=5, market_type="futures")
    assert r.size_mult == pytest.approx(rm.proving_mult)


def test_heat_cap_blocks(rm):
    # two open positions already consuming heat near cap
    big = [_pos(notional=5000) for _ in range(9)]      # 9 × (2% stop dist × 5000) = 900 risk
    r = rm.check_entry(ControlState.ACTIVE, "ETH/USDT", 100.0, 1.0, 0.02,
                       big, equity=2000.0, closed_trades_count=100,
                       market_type="futures")
    # open risk 900 = 45% > cap → blocked
    assert not r.ok and "heat" in r.reason or "budget" in r.reason


def test_frozen_blocks_entries(rm):
    r = rm.check_entry(ControlState.FROZEN, "BTC/USDT", 100.0, 1.0, 0.02,
                       [], 2000.0, 100, "futures")
    assert not r.ok and "FROZEN" in r.reason


def test_daily_breaker_blocks(rm):
    rm.update_equity(2000.0)
    st = rm.update_equity(2000.0 * (1 - 0.061))        # −6.1% intraday
    assert st["daily_pnl_pct"] <= -6.0
    r = rm.check_entry(ControlState.ACTIVE, "BTC/USDT", 100.0, 1.0, 0.02,
                       [], 1878.0, 100, "futures")
    assert not r.ok and "daily" in r.reason


def test_halt_breach_raises(rm):
    rm.update_equity(2000.0)
    rm.update_equity(2000.0 * 0.79)                    # −21% DD
    with pytest.raises(Exception):
        rm.check_entry(ControlState.ACTIVE, "BTC/USDT", 100.0, 1.0, 0.02,
                       [], 1580.0, 100, "futures")


def test_derisk_steps(rm):
    assert rm.derisk_multiplier(5) == 1.0
    assert rm.derisk_multiplier(9) == 0.5
    assert rm.derisk_multiplier(15) == 0.25


def test_protection_levels(rm):
    sl, tp = rm.protection_levels(price=100.0, atr=1.0, side="long", tp_mult=4.5)
    assert sl < 100 < tp and (100 - sl) >= 100 * 0.004
    sl2, tp2 = rm.protection_levels(100.0, 1.0, "short", 4.5)
    assert sl2 > 100 > tp2


# ── genomes ──────────────────────────────────────────────────────────────
def test_genome_validation():
    g = Genome(strategy_id="s1", family="vwap_fade", hypothesis="x" * 60,
               invalidation="die when losing a lot", regime_filter={"RANGING"},
               markets={"spot"}, params={})
    errs = Genome.validate(g)
    assert errs == [] and g.params["z_entry"] == 2.5          # default injected
    bad = Genome(strategy_id="s2", family="nope", hypothesis="", invalidation="",
                 regime_filter={"MOON"}, markets={"perp"}, params={"z_entry": 99})
    errs2 = Genome.validate(bad)
    assert any("family" in e for e in errs2)
    assert any("hypothesis" in e for e in errs2)
    assert any("regimes" in e for e in errs2)
    assert any("markets" in e for e in errs2)
    # out-of-range gene on a KNOWN family
    oor = Genome(strategy_id="s3", family="vwap_fade", hypothesis="x" * 60,
                 invalidation="die when losing", regime_filter={"RANGING"},
                 markets={"spot"}, params={"z_entry": 99})
    errs3 = Genome.validate(oor)
    assert any("outside" in e for e in errs3)


def test_seed_population_valid():
    seeds = library.build_seed_population()
    assert len(seeds) == 5
    seen_kinds = set()
    for st, g in seeds:
        assert Genome.validate(g) == [], f"{st.name}: {Genome.validate(g)}"
        assert st.state.value == "paper"
        seen_kinds.add(g.family)
    assert seen_kinds == {"ema_trend", "vwap_fade", "breakout_retest",
                          "sweep_reversal", "rotation_momo"}


# ── seed evaluators produce sane signals on synthetic data ───────────────
def test_evaluators_run_without_error():
    df15, df1h = _df(), _df()
    snap = Snapshot(symbol="SOL/USDT", ts="t", price=float(df15["close"].iloc[-1]),
                    dfs={"15m": df15, "1h": df1h, "BTC_1h": df1h},
                    regime="TRENDING_UP", adx=30.0)
    for st, g in library.build_seed_population():
        sig = library.evaluate(g, snap)
        assert sig is None or sig.action in (Action.BUY, Action.SELL, Action.HOLD)
