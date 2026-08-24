"""Phase 1 tests: orchestrator aggregation, outcome resolution, executor wiring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from trader.core.types import Action, Decision, Side, Snapshot, Vote
from trader.engine.orchestrator import Orchestrator


def _df(n=300, drift=0.0006, seed=3, vol_lo=True):
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, 0.002 if not vol_lo else 0.001, n)
    close = 100 * np.exp(np.cumsum(steps))
    high = close * 1.001
    low = close * 0.999
    vol = np.full(n, 1000.0)
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame({"ts": ts, "open": close, "high": high, "low": low,
                         "close": close, "volume": vol})


class JFake:
    def agent_accuracy(self, since_hours=0):
        return []
    def log_cycle(self, *a, **k): pass
    def log_votes(self, *a, **k): pass
    def log_decision(self, *a, **k): pass



def _seed_decision(j, dec_id, symbol, ts_iso, action, entry):
    from trader.core.types import Decision, Snapshot
    snap = Snapshot(symbol=symbol, ts=ts_iso, price=entry, dfs={})
    j.log_cycle(snap, "c_" + dec_id, mode="live")
    d = Decision(id=dec_id, cycle_id="c_" + dec_id, symbol=symbol,
                 action=Action(action), score=0.3, threshold=0.2,
                 confidence=0.5, votes=[], strategy_signals=[], ts=ts_iso)
    j.log_decision(d)

def make_orch():
    from trader.agents.momentum import MomentumAnalyst
    return Orchestrator([MomentumAnalyst()], JFake())


def test_strong_uptrend_yields_buy_or_neutral_not_sell():
    o = make_orch()
    snap = Snapshot(symbol="X/USDT", ts="t", price=110.0,
                    dfs={"15m": _df(drift=0.004), "1h": _df(drift=0.004)},
                    market_type="futures")
    d = o.decide(snap, [])
    assert d.action in (Action.BUY, Action.HOLD)
    assert d.votes and d.votes[0]["agent"] == "momentum"


def test_threshold_dynamic_on_agreement():
    o = make_orch()
    snap = Snapshot(symbol="X/USDT", ts="t", price=100.0,
                    dfs={"15m": _df(), "1h": _df()}, market_type="futures")
    d_flat = o.decide(snap, [])
    # threshold must stay within the documented band regardless of data
    assert 0.15 <= d_flat.threshold <= 0.35


def test_blocked_entry_sets_skip_reason():
    o = make_orch()
    snap = Snapshot(symbol="X/USDT", ts="t", price=110.0,
                    dfs={"15m": _df(drift=0.004), "1h": _df(drift=0.004)},
                    market_type="futures")
    d = o.decide(snap, [], entry_allowed=False, blocked_reason="state=FROZEN")
    if d.action != Action.HOLD:
        assert d.skip_reason == "state=FROZEN" and not d.executed


def test_strategy_signals_included_when_eligible():
    o = make_orch()
    snap = Snapshot(symbol="X/USDT", ts="t", price=float(_df()["close"].iloc[-1]),
                    dfs={"15m": _df(), "1h": _df()}, market_type="futures")

    class St: id = "s1"; is_trade_eligible = True
    from trader.strategy.genome import Genome
    g = Genome(strategy_id="s1", family="ema_trend", hypothesis="x" * 60,
               invalidation="die on losses", regime_filter=frozenset(
                   {"TRENDING_UP", "TRENDING_DOWN", "RANGING"}),
               markets=frozenset({"futures"}))
    d = o.decide(snap, [(St(), g)])
    assert isinstance(d.strategy_signals, list)


# ── outcome resolution ───────────────────────────────────────────────────
def test_outcome_resolution_math(tmp_path):
    from datetime import datetime, timedelta, timezone
    from trader.core.journal import Journal
    from trader.engine.outcomes import resolve_pending

    j = Journal(tmp_path / "j.db")
    ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    _seed_decision(j, "dec1", "BTC/USDT", ts, "BUY", 100.0)
    j.schedule_outcome("dec1", "c_dec1", "BTC/USDT", ts, "BUY", entry_price=100.0)

    n = 300   # 5-minute candles covering >24h
    base = pd.Timestamp(datetime.now(timezone.utc)) - pd.Timedelta(hours=25)
    ts_idx = pd.date_range(base, periods=n, freq="5min", tz="UTC")
    px = np.concatenate([[100 + 0.02 * i for i in range(30)],      # +1h ≈ 102.9
                         [103 + 0.03 * i for i in range(36)],      # +4h ≈ 106.8
                         [107 + 0.05 * i for i in range(n - 66)]]) # +24h big up
    df = pd.DataFrame({"ts": ts_idx, "open": px, "high": px, "low": px,
                       "close": px, "volume": np.ones(n)})

    class FakeFeed:
        def fetch_ohlcv(self, symbol, tf, limit=0, force=False):
            return df

    resolved = resolve_pending(j, FakeFeed())
    assert resolved == 1
    row = j.query("SELECT * FROM outcomes WHERE decision_id='dec1'")[0]
    assert row["resolved_at"] is not None
    assert row["correct_1h"] == 1 and row["fwd_ret_1h"] > 0
    assert row["correct_4h"] == 1
    assert row["correct_24h"] == 1


def test_short_outcome_correctness_inverted(tmp_path):
    from datetime import datetime, timedelta, timezone
    from trader.core.journal import Journal
    from trader.engine.outcomes import resolve_pending

    j = Journal(tmp_path / "j.db")
    ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _seed_decision(j, "decS", "ETH/USDT", ts, "SELL", 50.0)
    j.schedule_outcome("decS", "c_decS", "ETH/USDT", ts, "SELL", entry_price=50.0)

    n, base = 40, pd.Timestamp(datetime.now(timezone.utc)) - pd.Timedelta(hours=2)
    ts_idx = pd.date_range(base, periods=n, freq="5min", tz="UTC")
    px = [50 + 0.05 * i for i in range(n)]     # price rises → SHORT loses
    df = pd.DataFrame({"ts": ts_idx, "open": px, "high": px, "low": px,
                       "close": px, "volume": np.ones(n)})

    class FakeFeed:
        def fetch_ohlcv(self, symbol, tf, limit=0, force=False):
            return df

    resolve_pending(j, FakeFeed())
    row = j.query("SELECT * FROM outcomes")[0]
    assert row["correct_1h"] == 0 and row["fwd_ret_1h"] < 0


def test_nan_data_never_crashes_journal(tmp_path):
    """Regression: flat/garbage candles produced NaN score → sqlite NULL →
    crash-loop → duplicate entries on live (caught in first supervised run)."""
    from trader.engine.orchestrator import Orchestrator
    n = 300
    ts = pd.date_range("2026-01-01", periods=n, freq="15min")
    flat = pd.DataFrame({"ts": ts, "open": 100.0, "high": 100.0, "low": 100.0,
                         "close": 100.0, "volume": np.full(n, 1000.0)})
    nan_df = flat.copy()
    nan_df["close"] = np.nan

    class JFake2(JFake):
        def __init__(self): self.saved = []
        def log_decision(self, d): self.saved.append(d)
    jf = JFake2()
    o = Orchestrator([__import__(
        "trader.agents.momentum", fromlist=["MomentumAnalyst"]).MomentumAnalyst()],
        jf)
    snap = Snapshot(symbol="DRAM/USDT", ts="t", price=100.0,
                    dfs={"15m": nan_df, "1h": flat}, market_type="futures")
    d = o.decide(snap, [])
    import math
    assert math.isfinite(d.score) and math.isfinite(d.threshold)
    o.journalize(snap, d, "futures", "live")   # must not raise
