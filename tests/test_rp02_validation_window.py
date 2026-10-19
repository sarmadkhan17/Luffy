"""RP-02: the predictive validation window against the referee's own null.

All data is synthetic. The registered requirement chain is 210 warmup bars +
210 trading bars = 420 per slice, a 70/30 calendar split (so 420 protected-B
bars need 1,400 bars = 233.3 days of 4h), and a referee whose rotation null
needs distinct offsets: 2 x (210 + 1) margin + the common rotation's 199-draw
floor = 621 protected-B bars = 2,070 bars = 345 days. No protected price is
read; the only numerical work is on random-walk fixtures.
"""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from trader.research import portfolio_null as pn
from trader.research import referee as rf
from trader.research import slices
from trader.strategy.geometries import GEOS
from trader.strategy.vector_backtest import WARMUP

TF_MS = 14_400_000
RISK = {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
        "slippage_atr_frac": 0.015, "bar_minutes": 240, "real_funding": False}


def _frame(close):
    ts = pd.date_range("2022-01-01", periods=len(close), freq="4h", tz="UTC")
    prev = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"ts": ts, "open": prev,
                         "high": np.maximum(prev, close) * 1.004,
                         "low": np.minimum(prev, close) * 0.996,
                         "close": close, "volume": 1.0})


def _legs(seed, n_b, k=6):
    """No-edge legs: random entries on a shared-factor walk, B after context."""
    ctx = rf.B_CONTEXT_BARS
    rng = np.random.default_rng(seed)
    n = ctx + n_b
    m = rng.normal(0, .01, n)
    out = []
    for i in range(k):
        c = 100 * np.exp(np.cumsum(m + rng.normal(0, .006, n)))
        fire = np.zeros(n, bool)
        fire[::12] = True
        up = rng.random(n) > .5
        out.append(pn.Leg(f"S{i}", fire & up, fire & ~up, _frame(c), None,
                          first_bar=ctx))
    return out


# ── the requirement chain ───────────────────────────────────────────────
def test_requirement_chain_is_traced_to_one_set_of_constants():
    assert WARMUP == 210 and slices.MIN_SLICE_BARS == WARMUP + 210 == 420
    assert slices.DISCOVERY_FRAC == 0.7
    total = int(np.ceil(slices.MIN_SLICE_BARS / (1 - slices.DISCOVERY_FRAC)))
    assert total == 1400
    assert round(total * 4 / 24, 1) == 233.3          # days of 4h bars
    # the rotation floor is derived from existing locked constants only
    assert rf.ROTATION_FLOOR_BARS == 2 * (WARMUP + 1) + pn.MIN_DRAWS == 621
    assert int(np.ceil(rf.ROTATION_FLOOR_BARS / 0.3)) == 2070


def test_a_420_bar_protected_slice_has_no_rotation_at_all():
    assert pn.rotation_offsets(420) == 0
    assert pn.rotation_offsets(422) == 0
    assert pn.rotation_offsets(423) == 1
    assert pn.rotation_offsets(rf.ROTATION_FLOOR_BARS) == pn.MIN_DRAWS


# ── D1: a narrow null can no longer read as a confident one ─────────────
def test_common_rotation_refuses_fewer_offsets_than_draws():
    legs = _legs(7, 423)                    # one distinct offset
    out = pn.common_rotation(legs, GEOS["trail"], RISK, "4h", 2000.0, 0.5, 8,
                             draws=199, seed=7)
    assert out["p"] is None and out["draws"] == 0
    assert "too narrow" in out["reason"]


def test_common_rotation_with_enough_offsets_is_unchanged():
    legs = _legs(7, rf.ROTATION_FLOOR_BARS)
    out = pn.common_rotation(legs, GEOS["trail"], RISK, "4h", 2000.0, 0.5, 8,
                             draws=pn.MIN_DRAWS, seed=7)
    assert out["p"] is not None and out["draws"] == pn.MIN_DRAWS
    assert out["p"] >= 1 / (pn.MIN_DRAWS + 1)


def test_consistency_leaves_a_symbol_without_a_distinct_null_unscored():
    narrow = rf.consistency(_legs(3, 480), GEOS["trail"], RISK, "4h",
                            draws=rf.CONSISTENCY_DRAWS, seed=3,
                            dependence_draws=20)
    assert narrow["scored_symbols"] == 0 and narrow["consistency_p"] is None
    wide = rf.consistency(_legs(3, 700), GEOS["trail"], RISK, "4h",
                          draws=rf.CONSISTENCY_DRAWS, seed=3,
                          dependence_draws=20)
    assert wide["scored_symbols"] >= 4 and wide["consistency_p"] is not None


# ── D2: unresolvable looks are deferred from counts, never charged ──────
@pytest.fixture
def store(tmp_path):
    path = tmp_path / "candles.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,"
                   "high REAL,low REAL,close REAL,volume REAL,taker_buy REAL,"
                   "PRIMARY KEY(symbol,tf,ts))")
        for sym, first in (("A", 0), ("B", 0), ("LATE", 1300)):
            db.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                           [(sym, "4h", i * TF_MS, 1, 1, 1, 1, 1, 1)
                            for i in range(first, 1000 + 500)])
    from tests.retained_candle_fixtures import qualify
    qualify(path)
    return str(path)


def test_capacity_counts_bars_and_mirrors_loader_inclusion(store):
    cut = 1000 * TF_MS
    cap = rf.rotation_capacity("4h", cut, ["A", "B"], ["A", "B", "LATE"], {}, {"candles": store})
    assert cap["a"] == {"A": 1000, "B": 1000}
    assert cap["b"] == {"A": 500, "B": 500}      # LATE: no pre-cut context
    assert rf.rotation_capacity("4h", cut, ["A"], ["A"], {}, {"candles": "/nonexistent.db"}) is None


def test_capacity_honours_the_registered_split(store):
    cut = 1000 * TF_MS
    cfg = {"research": {"predictive_split": {"start_ms": 400 * TF_MS, "cut_ms": cut,
                                             "end_ms": 1299 * TF_MS}}}
    cap = rf.rotation_capacity("4h", cut, ["A"], ["A"], cfg, {"candles": store})
    assert cap == {"a": {"A": 600}, "b": {"A": 300}}


@pytest.mark.parametrize("b_bars,draws,deferred", [
    (420, 199, True), (600, 199, True), (620, 199, True),
    (621, 199, False), (700, 199, False), (700, 400, True)])
def test_deferral_is_decided_from_counts(b_bars, draws, deferred):
    cap = {"a": {"A": 1000}, "b": {"A": b_bars, "B": b_bars}}
    assert bool(rf.rotation_deferral(cap, draws)) is deferred


def test_examine_defers_before_any_price_is_read(store, monkeypatch):
    from trader.research.combo import Combination
    from trader.research.vocab import Part
    monkeypatch.setattr(rf, "load_heldout", lambda *a, **k: pytest.fail("price read"))
    donch = Part("ev:donch20", "event", "ev:donch20", "close > donchian_hi(20)",
                 "close < donchian_lo(20)")
    c = Combination((donch,), "4h", "trail")
    out = rf.examine(c, {"cfg": {"research": {}, "risk": RISK}, "paths": {"candles": store},
                         "cut_ms": 1000 * TF_MS, "spent_cut_ms": 1000 * TF_MS,
                         "discovery_symbols": ["A"], "heldout_symbols": ["B"],
                         "draws": 199, "alpha": 0.0025, "max_draws": 19999, "seed": 1})
    assert out["looked"] is False and "deferred" in out and "gate1" not in out


def test_examine_defers_when_alpha_exceeds_rotation_resolution(store, monkeypatch):
    # B = 500 bars -> 78 distinct offsets: below the 199-draw floor.
    cut = 1000 * TF_MS
    cap = rf.rotation_capacity("4h", cut, ["B"], ["A", "B"], {}, {"candles": store})
    assert pn.rotation_offsets(min(cap["b"].values())) == 78
    assert pn.draws_for(0.0025, 78) is None            # needs 399 distinct offsets


# ── runner: a child deferral is a state, not a charged look ─────────────
def test_runner_preflights_metadata_deferral_without_spending(tmp_path, monkeypatch):
    from trader.research import universe
    monkeypatch.setattr(universe, "coverage", lambda tfs=None, feed=None: {
        tf: {s:11000 for s in universe.DISCOVERY+universe.HELDOUT}
        for tf in (tfs or ["4h"])})
    from tests.test_research_runner import _setup
    monkeypatch.setattr(rf, "rotation_capacity", lambda *a,**k:
                        {"a":{"A":700},"b":{"B":210}})
    r,calls,combos = _setup(tmp_path)
    r.step()
    out=r.step()
    assert out["state"]=="deferred" and out["ok"]
    assert r.ledger.tests()==[] and r.ledger.spent(combos[0].hash) is None
    assert not [c for c in calls if c["fn"]=="referee_job"]


# ── registration: a frozen window the referee could never test ──────────
def test_translation_waits_for_the_referees_rotation_floor(population, tmp_path):
    from trader.cognition import predictive as P
    from trader.research import predictive_experiment as E
    from tests.test_predictive_research_bridge import CFG
    _, _, candles, created, later, s = population
    h = P.propose(s, "volume_breakout_context.v1", created)
    assert E.translate(h, candles, CFG, later)["status"] == "READY"

    cut_rows = tmp_path / "short.db"
    import shutil
    shutil.copy(candles, cut_rows)
    with sqlite3.connect(cut_rows) as db:
        first = db.execute("SELECT MIN(ts) FROM candles").fetchone()[0]
        db.execute("DELETE FROM candles WHERE ts>=?", (first + 1800 * TF_MS,))
        db.execute("DELETE FROM market_revisions WHERE event_ms>=?", (first + 1800 * TF_MS,))
    short = E.translate(h, cut_rows, CFG, later)
    assert short["status"] == "DATA_INSUFFICIENT"
    assert short["reason"] == "referee_rotation_floor"
    assert short["required_protected_bars"] == rf.ROTATION_FLOOR_BARS
    assert min(v for v in short["counts"]["protected_b"].values()) < rf.ROTATION_FLOOR_BARS


from tests.test_predictive_research_bridge import population  # noqa: E402,F401
from tests.test_investigation_state_feedback import paths  # noqa: E402,F401
