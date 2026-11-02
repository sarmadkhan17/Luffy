"""Startup of the legacy research thread spends zero held-out looks and zero LORD++ alpha.

Simulates what the kernel's `_research_loop` does at startup (`ResearchRunner.step` in a loop) against a ledger of
legacy-style survivors and a small SYNTHETIC candle store, both temporary. Child jobs are stubbed: a dispatch is
counted and never executed, and the runner reserves a look BEFORE dispatching the referee, so `research_tests` is the
budget actually spent. Nothing here reads a production store.

Three layers are exercised and each is shown to matter:
  1. the legacy-accounting fence (every legacy row lacks current accounting, so `step` stops before the referee);
  2. `research.referee_forward_only`: with the fence lifted, every candidate is deferred before any held-out read;
  3. the same flag fails closed when the candle store cannot be read.
The controls without the flag spend a look, so a passing result is not an accident of the fixture.
"""
import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.research import runner as R
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner

STEP = 14_400_000
BARS = 4000                      # held-out B of ~1,200 bars: long enough that an unprotected look is resolvable
DISC = ["D0/USDT", "D1/USDT", "D2/USDT", "D3/USDT"]
HELD = ["H0/USDT", "H1/USDT", "H2/USDT"]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    """Synthetic 4h candles for 7 symbols, with the retained receipts that make them replay-eligible."""
    import time
    from tests.retained_candle_fixtures import qualify
    path = tmp_path_factory.mktemp("startup-store") / "candles.db"
    now = int(time.time() * 1000) // STEP * STEP
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL, open REAL, high REAL, "
                "low REAL, close REAL, volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
    for k, sym in enumerate(DISC + HELD):
        rng, px, rows = np.random.default_rng(40 + k), 100.0, []
        for b in range(BARS):
            px *= 1.0 + rng.normal(0, 0.008)
            rows.append((sym, "4h", now - (BARS - b) * STEP, px, px * 1.004, px * 0.996, px, 10.0, 5.0))
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    qualify(str(path))
    return path


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _world(tmp_path, monkeypatch, store, *, flag, lift_fence, store_readable=True, selected="late", n=6, queued=True):
    """A ledger of legacy survivors (no current accounting metadata), n of them already queued for the referee."""
    import time
    now = int(time.time() * 1000)
    cfg = load_config()
    cfg["research"] = {**cfg["research"], "enabled": True, "referee": True, "referee_forward_only": flag,
                       "paths": {"candles": str(store) if store_readable else str(tmp_path / "missing.db")}}
    cfg["research"].pop("predictive_split", None)
    j = Journal(tmp_path / "j.db")
    led = Ledger(j)
    led.ensure()
    from trader.research import vocab
    from trader.research.combo import Combination
    led.record_gauges("4h", {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5, "p90": 1.0, "n": 9000, "finite_frac": 0.99,
                                 "usable": True} for e in vocab.expressions("4h")})
    parts = vocab.parts_for("4h", led.gauges("4h"))
    assert len(parts) >= n, "the vocabulary must supply one distinct single-part combination per survivor"
    combos = [Combination((p,), "4h", "trail") for p in parts[:n]]
    hashes = [c.hash for c in combos]
    by_hash = {c.hash: c for c in combos}
    # 'late': selected after the discovery cut (the overlap case); 'early': selected long before it
    created = _iso(now - 5 * STEP) if selected == "late" else _iso(now - 3000 * STEP)
    legacy_result = json.dumps({"portfolio": {"total_pct": 40.0, "max_dd_pct": 10.0}})     # no 'accounting' key
    with j._tx() as c:
        for i in range(n):
            c.execute("INSERT INTO research_combos (hash,tf,geo,k,status,label,verdict,consistency_p,median_pf,total_pct,"
                      "trades,scored_symbols,testable,result,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (hashes[i], "4h", "trail", 1, "scored", "scored", "survivor", 1e-6 * (i + 1), 1.5, 40.0, 300, 8, 1,
                       legacy_result, created))
    led.record_slices("4h", now - 1300 * STEP, {"discovery": 2000, "heldout_a": 1000, "heldout_b": 1300})
    if queued:
        for i in range(n):
            led.set_candidate(hashes[i], "4h", "trail", "queued", rank=40.0)
    if lift_fence:
        monkeypatch.setattr(R, "require_current_accounting", lambda portfolio: None)
    monkeypatch.setattr(ResearchRunner, "_combo", lambda self, row, tf, geo: by_hash.get(row["hash"]))
    monkeypatch.setattr(ResearchRunner, "_symbols", lambda self, tf: (list(DISC), list(HELD)))
    dispatched = {}

    def run(fn, *args, **kw):
        dispatched[fn.__name__] = dispatched.get(fn.__name__, 0) + 1
        return SimpleNamespace(ok=False, error="startup test: child never executed", value=None, terminated=False)

    return ResearchRunner(j, cfg, run=run), led, dispatched


def _startup(runner, steps=8):
    """What the kernel thread does at startup: step repeatedly."""
    outcomes = {}
    for _ in range(steps):
        out = runner.step(cycle_seconds=1.0)
        key = out.get("skipped") or f"ran:{out.get('kind')}:{out.get('state')}"
        outcomes[key] = outcomes.get(key, 0) + 1
    return outcomes


def _spent(led):
    tests = led.tests()
    return len(tests), sum(t["alpha_t"] for t in tests)


def test_legacy_accounting_fence_stops_startup_before_the_referee(tmp_path, monkeypatch, store):
    runner, led, dispatched = _world(tmp_path, monkeypatch, store, flag=True, lift_fence=False)
    assert _startup(runner) == {"portfolio_accounting_incompatible": 8}
    assert dispatched == {} and _spent(led) == (0, 0)


def test_forward_only_defers_every_queued_candidate_and_spends_nothing_when_the_fence_is_lifted(tmp_path, monkeypatch, store):
    runner, led, dispatched = _world(tmp_path, monkeypatch, store, flag=True, lift_fence=True)
    outcomes = _startup(runner)
    assert outcomes.get("ran:referee:deferred") == 6, outcomes
    assert "referee_job" not in dispatched, "no held-out look may be dispatched"
    assert _spent(led) == (0, 0), "no look and no alpha may be spent"
    states = {c["state"] for c in led.candidates("4h", "trail")}
    reasons = {c["reason"].split(":")[0] for c in led.candidates("4h", "trail")}
    assert states == {"deferred"} and reasons == {"selection_overlap"}


def test_control_without_the_flag_spends_a_look_so_the_protection_is_what_stops_it(tmp_path, monkeypatch, store):
    runner, led, dispatched = _world(tmp_path, monkeypatch, store, flag=False, lift_fence=True)
    _startup(runner)
    looks, alpha = _spent(led)
    assert dispatched.get("referee_job", 0) >= 1 and looks >= 1 and alpha > 0


def test_unreadable_store_defers_as_capacity_unavailable_instead_of_looking_blind(tmp_path, monkeypatch, store):
    # selected long before the cut, so the overlap check passes and the bar-count check is what decides
    runner, led, dispatched = _world(tmp_path, monkeypatch, store, flag=True, lift_fence=True, store_readable=False,
                                     selected="early", n=4)
    outcomes = _startup(runner, steps=6)
    assert outcomes.get("ran:referee:deferred") == 4, outcomes
    assert "referee_job" not in dispatched and _spent(led) == (0, 0)
    reasons = {c["reason"].split(":")[0] for c in led.candidates("4h", "trail")}
    assert reasons == {"capacity_unavailable"}


def test_control_unreadable_store_without_the_flag_looks_blind(tmp_path, monkeypatch, store):
    runner, led, dispatched = _world(tmp_path, monkeypatch, store, flag=False, lift_fence=True, store_readable=False,
                                     selected="early", n=4)
    _startup(runner, steps=6)
    assert dispatched.get("referee_job", 0) >= 1 and _spent(led)[0] >= 1


def test_the_first_batch_is_capped_and_still_spends_nothing(tmp_path, monkeypatch, store):
    """Natural startup (nothing pre-queued): at most referee_first_batch survivors are even sent for selection."""
    runner, led, dispatched = _world(tmp_path, monkeypatch, store, flag=True, lift_fence=True, n=25, queued=False)
    sent = []
    inner = runner.run
    runner.run = lambda fn, payload, **kw: (sent.append(len(payload.get("combos", ()))) if fn.__name__ == "select_job" else None,
                                            inner(fn, payload, **kw))[1]
    _startup(runner, steps=5)
    assert sent and max(sent) <= 10, sent
    assert "referee_job" not in dispatched and _spent(led) == (0, 0)
