"""Scout upgrade tests: calibration, positioning, depth, news guard,
BTC context, HTF veto."""
import numpy as np
import pandas as pd
import pytest

from trader.agents.base import Analyst
from trader.agents.calibration import (_fit_logistic, apply, load,
                                        refit)
from trader.agents.orderbook_depth import DepthScout
from trader.agents.positioning import PositioningAnalyst
from trader.agents.regime import btc_context
from trader.core.types import Side, Snapshot


def _df(closes, tf="15m"):
    n = len(closes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=n, freq="min"),
        "open": closes, "high": np.array(closes) * 1.001,
        "low": np.array(closes) * 0.999, "close": closes,
        "volume": np.full(n, 100.0),
        "taker_buy": np.full(n, 50.0)})


def _snap(symbol="ETH/USDT", price=2000.0, **kw):
    return Snapshot(symbol=symbol,
                    ts="2026-08-25T00:00:00+00:00",
                    price=price,
                    dfs={"15m": _df(np.linspace(price, price * 1.01, 300)),
                         "1h": _df(np.linspace(price, price * 1.01, 120))},
                    market_type="futures", **kw)


# ── 1. calibration ────────────────────────────────────────────────────────
def test_logistic_fit_separable():
    zs = [0.1] * 40 + [0.4] * 40
    ys = [0.0] * 40 + [1.0] * 40
    w0, w1 = _fit_logistic(zs, ys)
    p_low = 1 / (1 + pow(2.718, -(w0 + w1 * 0.1)))
    p_high = 1 / (1 + pow(2.718, -(w0 + w1 * 0.4)))
    assert p_high > p_low + 0.5


def test_apply_identity_below_min_samples():
    state = {"x": {"w0": 2.0, "w1": 20.0, "n": 10}}
    c, did = apply(state, "x", 0.45, 0.5, min_samples=60)
    assert not did and c == 0.45


def test_refit_and_apply_recalibrates():
    import tempfile, pathlib
    from trader.core.journal import Journal
    j = Journal(pathlib.Path(tempfile.mkdtemp()) / "cal.db")
    # fake resolved votes: high-z votes wrong 80% (overconfident scout)
    rows = []
    for i in range(100):
        z_conv = 0.6 if i % 2 == 0 else 0.2
        correct = (i % 10 < 2) if z_conv == 0.6 else (i % 10 < 6)
        rows.append((z_conv, 1.0 if correct else 0.0))
    import json as _json
    j.query("INSERT INTO cycles(id,ts,symbol,price,regime,adx,btc_trend,"
            "market_type,mode) VALUES ('c','t','S',1,'RANGING',0,'NEUTRAL',"
            "'futures','live')")
    j.query("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,"
            "threshold,confidence) VALUES ('base','c','t','S','BUY',0.3,"
            "0.24,0.5)")
    def ins(idx_r):
        idx, r = idx_r
        cid = f"c{idx}"
        j.query("INSERT INTO cycles(id,ts,symbol,price,regime,adx,btc_trend,"
                "market_type,mode) VALUES (?,'t','S',1,'RANGING',0,'NEUTRAL',"
                "'futures','live')", (cid,))
        j.query("INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,"
                "confidence,rationale,meta) VALUES (?,'t','S','scout','long',"
                "?,?,'r','{}')", (cid, r[0], r[1]))
        did = f"d{idx}"
        j.query(
            "INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,"
            "threshold,confidence) VALUES (?,?,'t','S','BUY',0.3,0.24,0.5)",
            (did, cid))
        j.query(
            "INSERT INTO outcomes(decision_id,cycle_id,symbol,ts,action,"
            "entry_price,resolved_at,fwd_ret_1h,fwd_ret_4h,fwd_ret_24h,"
            "correct_1h,correct_4h,correct_24h) VALUES "
            "(?,?,\"S\",'t','BUY',1,'now',0,0,0,NULL,?,NULL)",
            (did, cid, r[1]))
    for i, r in enumerate(rows):
        ins((i, r))
    # fix convictions so z matches intended magnitudes
    state = refit(j, min_samples=60)
    fit = state["scout"]
    assert fit["n"] == 100
    # high-conviction vote gets shrunk toward reality
    new_c, did = apply(state, "scout", 0.6, 0.85, min_samples=60)
    assert did and abs(new_c) < 0.6


# ── 2a. positioning ───────────────────────────────────────────────────────
def test_positioning_fades_crowded_longs():
    a = PositioningAnalyst()
    snap = _snap()
    a.set_context("ETH/USDT", funding_rate=0.0008,
                  oi={"now": 1e6, "chg_24h": 0.10})
    v = a.evaluate(snap)
    assert v.side == Side.SHORT and v.conviction <= -0.3


def test_positioning_fades_crowded_shorts():
    a = PositioningAnalyst()
    a.set_context("ETH/USDT", funding_rate=-0.0008,
                  oi={"now": 1e6, "chg_24h": -0.02})
    v = a.evaluate(_snap(price=2000.0))
    assert v.side == Side.LONG


def test_positioning_detects_short_covering():
    a = PositioningAnalyst()
    snap = _snap(price=2000.0)
    snap.dfs["1h"] = _df(np.linspace(2000, 2300, 120), tf="1h")  # >2% last 24h
    a.set_context("ETH/USDT", funding_rate=0.0001,
                  oi={"now": 1e6, "chg_24h": -0.12})
    v = a.evaluate(snap)   # price rose while OI fell
    assert "short-covering" in v.rationale and v.conviction < 0


def test_positioning_flat_without_data():
    a = PositioningAnalyst()
    v = a.evaluate(_snap())
    assert v.side == Side.FLAT


# ── 2b. depth ─────────────────────────────────────────────────────────────
def test_depth_imbalance_sign():
    d = DepthScout()
    book = {"bids": [[1999 + i * 0.5, 50 - i] for i in range(8)],
            "asks": [[2001 + i * 0.5, 2 + i] for i in range(8)]}
    d.set_context("ETH/USDT", book)
    v = d.evaluate(_snap(price=2000.0))
    assert v.side == Side.LONG and "imbalance" in v.rationale


def test_depth_wall_bonus():
    d = DepthScout()
    book = {"bids": [[1999.9, 500], *[1999 - i * 0.5 for i in range(1)]],
            "asks": []}
    book = {"bids": [[1999.9, 500], [1999.0, 3], [1998.5, 4], [1998.0, 5]],
            "asks": [[2000.1, 3], [2000.6, 4], [2001.1, 5]]}
    d.set_context("X/USDT", book)
    v = d.evaluate(_snap(price=2000.0))
    assert "bid wall" in v.rationale


def test_depth_no_book():
    d = DepthScout()
    d.set_context("X/USDT", None)
    assert d.evaluate(_snap()).side == Side.FLAT


# ── 4. news guard ─────────────────────────────────────────────────────────
def test_news_guard_arms_on_severe(monkeypatch):
    from trader.agents import news_guard as ng
    g = ng.NewsGuard({"scouts": {"news_guard": {"enabled": True}}})
    monkeypatch.setattr(ng, "scrape_feed", lambda url: [
        {"title": "DeFi protocol hacked, $50M drained", "text": "x" * 60,
         "age_h": 0.5}])
    st = g.check()
    assert st["active"]


def test_news_guard_needs_two_impact(monkeypatch):
    from trader.agents import news_guard as ng
    g = ng.NewsGuard({"scouts": {"news_guard": {"enabled": True}}})
    monkeypatch.setattr(ng, "scrape_feed", lambda url: [
        {"title": "Fed minutes hint at rate decision debate", "text": "y" * 60,
         "age_h": 1.0}])
    assert not g.check()["active"]


def test_news_guard_ignores_old_headlines(monkeypatch):
    from trader.agents import news_guard as ng
    g = ng.NewsGuard({"scouts": {"news_guard": {"enabled": True}}})
    monkeypatch.setattr(ng, "scrape_feed", lambda url: [
        {"title": "Exchange hacked", "text": "z" * 60, "age_h": 48}])
    assert not g.check()["active"]


# ── 3. BTC context ────────────────────────────────────────────────────────
def test_btc_context_trend():
    up = _df(np.linspace(100, 130, 200))
    ctx = btc_context(up, up)
    assert ctx["trend"] == "UP" and ctx["ret_1h"] > 0
    dn = _df(np.linspace(130, 100, 200))
    assert btc_context(dn, dn)["trend"] == "DOWN"


def test_value_suppressed_by_btc_impulse():
    from trader.agents.momentum import ValueAnalyst
    va = ValueAnalyst()
    base = _df(np.linspace(2000, 2100, 300))
    base.loc[len(base) - 1, "close"] *= 1.06       # force extreme deviation
    snap = _snap()
    snap.dfs["15m"] = base
    snap.btc_ctx = {"trend": "UP", "ret_1h": 0.02, "ret_4h": 0.03}
    v_hot = va.evaluate(snap)
    snap.btc_ctx = {}
    v_calm = va.evaluate(snap)
    if abs(v_calm.conviction) > 0.05:              # only comparable if it voted
        assert abs(v_hot.conviction) < abs(v_calm.conviction)


# ── 5. HTF veto ───────────────────────────────────────────────────────────
class Stub(Analyst):
    name = "stub"

    def __init__(self, conv):
        self.conv = conv

    def evaluate(self, snap):
        return self._vote(self.name, snap, self.conv, 0.8, "stub")


def _orch(conv, tmp_path, **kw):
    import tempfile, pathlib
    from trader.core.journal import Journal
    from trader.engine.orchestrator import Orchestrator
    j = Journal(pathlib.Path(tempfile.mkdtemp()) / "o.db")
    return Orchestrator([Stub(conv)], j, **kw)


def test_htf_score_math():
    from trader.engine.orchestrator import htf_trend_score
    up = _df(list(np.linspace(100, 170, 400)), tf="4h")
    dn = _df(list(np.linspace(170, 100, 400)), tf="4h")
    flat = _df([100 + 0.0 * i for i in range(400)], tf="4h")
    assert htf_trend_score(up) > 0.8
    assert htf_trend_score(dn) < -0.8
    assert abs(htf_trend_score(flat)) < 0.05


def test_htf_hard_veto_blocks_counter_trend(monkeypatch):
    import trader.engine.orchestrator as om
    monkeypatch.setattr(om, "htf_trend_score", lambda df: 0.9)
    o = _orch(-0.9, None)                          # strongly SHORT vote
    d = o.decide(_snap(), population=[], entry_allowed=True)
    assert d.action != "SELL"
    assert "hard veto" in d.skip_reason


def test_htf_soft_bump_raises_threshold(monkeypatch):
    import trader.engine.orchestrator as om
    monkeypatch.setattr(om, "htf_trend_score", lambda df: 0.5)
    o_ct = _orch(-0.55, None)                      # counter-trend short
    d_ct = o_ct.decide(_snap(), population=[], entry_allowed=True)
    o_wt = _orch(+0.55, None)                      # with-trend long
    d_wt = o_wt.decide(_snap(), population=[], entry_allowed=True)
    assert d_ct.threshold - d_wt.threshold == pytest.approx(0.07)


def test_news_blackout_raises_threshold():
    quiet = _orch(0.5, None)
    d_quiet = quiet.decide(_snap(), population=[], entry_allowed=True)
    loud = _orch(0.5, None, news_guard=type("G", (), {
        "check": staticmethod(lambda: {"active": True, "why": "test"})})())
    d_loud = loud.decide(_snap(), population=[], entry_allowed=True)
    assert d_loud.threshold > d_quiet.threshold
    assert abs(d_loud.score) <= abs(d_quiet.score)
