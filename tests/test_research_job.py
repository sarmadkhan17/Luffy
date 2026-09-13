"""The search runs in a spawned, niced, time-boxed child.

The kernel's pandas work holds the GIL, so a CPU-heavy search thread would
slow the trade loop directly, and a blow-up in it would be the kernel's. The
child reads the stores READ-ONLY and returns results over a pipe; the thread
writes them. It never writes a database, so the kernel stays the only writer
of truth.

A child that runs out of time returns what it finished rather than losing the
batch: the combinations it did not reach are simply absent from the ledger,
and the planner offers them again.
"""
import sqlite3

import numpy as np
import pytest

from trader.core.child import run_child
from trader.research import job
from trader.research.combo import Combination
from trader.research.vocab import Part

TF_MS = 14_400_000
DONCH = Part("ev:donch20", "event", "ev:donch20",
             "close > donchian_hi(20)", "close < donchian_lo(20)")
EMA = Part("st:above_ema50", "state", "st:above_ema50",
           "close > ema(50)", "close < ema(50)")


@pytest.fixture
def store(tmp_path):
    """A candle store with five synthetic markets."""
    db = tmp_path / "candles.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, "
        "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
    rng = np.random.default_rng(7)
    for i in range(5):
        px, rows = 100.0, []
        for b in range(900):
            drift = 0.006 if (b // 60) % 2 == 0 else 0.0
            px *= 1.0 + rng.normal(drift, 0.008)
            rows.append((f"S{i}/USDT", "4h", b * TF_MS, px, px * 1.004,
                         px * 0.996, px, 10.0, 5.0))
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                        rows)
    con.commit()
    con.close()
    return db


def _payload(store, combos=(), **kw):
    base = {"tf": "4h",
            "symbols": [f"S{i}/USDT" for i in range(5)],
            "heldout_symbols": [],
            "requires": ["ohlcv"],
            "cfg": {"risk": {"risk_per_trade_pct": 0.5,
                             "taker_fee_pct": 0.04,
                             "slippage_atr_frac": 0.015,
                             "funding_rate_8h": 0.0001,
                             "real_funding": False,
                             "max_open_trades": 8},
                    # a 900-bar synthetic store yields ~3.1k pooled samples,
                    # under the production floor of 5000 — this test is about
                    # the measurement path, not about the floor
                    "research": {"min_threshold_samples": 100}},
            "paths": {"candles": str(store), "derivs": str(store)},
            "combos": [c.as_dict() for c in combos],
            "draws": 20, "seed": 3, "soft_deadline_s": 300.0}
    base.update(kw)
    return base


def test_measure_returns_percentiles_and_the_cut(store):
    p = _payload(store)
    p["exprs"] = ["ret(24)", "rsi(14)"]
    out = job.measure_job(p)
    assert out["tf"] == "4h" and out["cut_ms"] > 0
    assert out["gauges"]["ret(24)"]["usable"] is True
    assert out["counts"]["discovery"]["S0/USDT"] > 0


def test_measure_reports_a_gauge_with_no_data_as_unusable(store):
    p = _payload(store)
    p["exprs"] = ["funding_z(360)"]
    out = job.measure_job(p)
    assert out["gauges"]["funding_z(360)"]["usable"] is False


def test_evaluate_scores_every_combination_it_is_given(store):
    combos = [Combination((DONCH,), "4h", "trail"),
              Combination((DONCH, EMA), "4h", "trail")]
    out = job.evaluate_job(_payload(store, combos))
    assert out["done"] == 2
    assert {r["hash"] for r in out["results"]} == {c.hash for c in combos}
    assert all("verdict" in r for r in out["results"])


def test_evaluate_reports_how_many_symbols_the_bundle_actually_loaded(store):
    """The `min_discovery_symbols` guard in `runner._evaluate` reads THIS
    number, not the coverage count the planner used to size the request —
    a bundle that silently drops symbols must be visible to it."""
    out = job.evaluate_job(
        _payload(store, [Combination((DONCH,), "4h", "trail")]))
    assert out["loaded_symbols"] == 5


def test_the_result_is_json_able_so_it_can_cross_the_pipe(store):
    import json
    out = job.evaluate_job(
        _payload(store, [Combination((DONCH,), "4h", "trail")]))
    json.dumps(out)


FUNDZ = Part("g:fundz360>p90", "gauge", "fundz360",
             "funding_z(360) > 1.5", "0 - funding_z(360) < -1.5")


def test_a_poisonous_combination_does_not_cost_the_batch(store, monkeypatch):
    """One rule raising must not discard the results already scored, and
    must not be routed to `skipped` — that would have the planner re-offer
    it forever, re-burning the whole child timeout on a rule that can never
    succeed. The errored row must also carry the metadata `growth.decide`
    and `ledger.record_result` read: `error` (not some other key) and its
    own `window` rather than a silently-defaulted `"ohlcv"`."""
    good = Combination((DONCH,), "4h", "trail")
    bad = Combination((DONCH, FUNDZ), "4h", "trail")
    assert bad.window == "funding"    # genuinely not ohlcv, not faked
    real_evaluate = job.ev.evaluate

    def _boom(c, b, **kw):
        if c.hash == bad.hash:
            raise RuntimeError("poisoned rule")
        return real_evaluate(c, b, **kw)

    monkeypatch.setattr(job.ev, "evaluate", _boom)
    out = job.evaluate_job(_payload(store, [good, bad]))

    assert out["done"] == 2
    assert out["skipped"] == 0
    by_hash = {r["hash"]: r for r in out["results"]}
    assert by_hash[good.hash]["verdict"] != "error"
    bad_row = by_hash[bad.hash]
    assert bad_row["verdict"] == "error"
    assert "poisoned rule" in bad_row["error"]
    assert bad_row["window"] == "funding"


def test_a_soft_deadline_returns_what_was_finished(store):
    combos = [Combination((DONCH,), "4h", "trail"),
              Combination((DONCH, EMA), "4h", "trail")]
    out = job.evaluate_job(_payload(store, combos, soft_deadline_s=0.0))
    assert out["done"] == 0 and out["skipped"] == 2
    assert out["results"] == []


def test_the_job_really_runs_in_a_spawned_niced_child(store):
    r = run_child(job.evaluate_job,
                  _payload(store, [Combination((DONCH,), "4h", "trail")]),
                  timeout_s=180, nice=19)
    assert r.ok is True, r.error
    assert r.value["done"] == 1


def test_a_child_past_its_hard_deadline_is_a_failure_not_a_hang(store):
    combos = [Combination((DONCH,), "4h", "trail")] * 1
    r = run_child(job.evaluate_job, _payload(store, combos), timeout_s=0.2)
    assert r.ok is False and (r.timed_out or r.error)


def _row_counts(db_path) -> dict:
    con = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in tables}
    finally:
        con.close()


def test_the_child_writes_nothing(store):
    # `DataFeed.db` runs `CREATE TABLE IF NOT EXISTS` on open, which is a
    # no-op on an existing table but still touches the file's mtime — so the
    # real assertion is that no table gains a row, not that the file is
    # untouched.
    before = _row_counts(store)
    job.evaluate_job(_payload(store, [Combination((DONCH,), "4h", "trail")]))
    after = _row_counts(store)
    for table, n in before.items():
        assert after.get(table, 0) == n, f"{table} gained rows"


def test_the_job_module_opens_no_write_path():
    import inspect
    src = inspect.getsource(job)
    for forbidden in ("INSERT", "UPDATE ", "_tx(", "upsert"):
        assert forbidden not in src
