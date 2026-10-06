"""QNT-04: discovery / held-out separation and spent-holdout permanence.

Negative controls: future-label leakage, overlapping windows, reused
held-out, revision after the cut, missing held-out evidence."""
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trader.core.journal import Journal
from trader.research import evaluate as ev
from trader.research import referee as rf
from trader.research import slices
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner

TF_MS = 14_400_000
CFG = {"risk": {"risk_per_trade_pct": 0.5, "taker_fee_pct": 0.04,
                "slippage_atr_frac": 0.015, "bar_minutes": 240,
                "real_funding": False}}
SYMS = ["A1/USDT", "A2/USDT"]


def _store(path, bars=1600, tamper_from=None, extra=0):
    """Same seeded walk every time; optionally rewrite bars from
    `tamper_from` on (a later revision) or append `extra` newer bars."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE candles (symbol TEXT NOT NULL, tf TEXT NOT NULL, "
        "ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, taker_buy REAL, PRIMARY KEY (symbol, tf, ts))")
    for k, sym in enumerate(SYMS):
        rng = np.random.default_rng(10 + k)
        px, rows = 100.0, []
        for b in range(bars + extra):
            px *= 1.0 + rng.normal(0, 0.008)
            p = px * 3.0 if tamper_from is not None and b >= tamper_from \
                else px
            rows.append((sym, "4h", b * TF_MS, p, p * 1.004, p * 0.996,
                         p, 10.0, 5.0))
        con.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                        rows)
    con.commit()
    con.close()
    from tests.retained_candle_fixtures import qualify
    qualify(path)
    return str(path)


def _bundle(path, cap=None):
    return ev.load_bundle("4h", SYMS, CFG, paths={"candles": path},
                          cap_ms=cap)


def _same(a, b):
    assert set(a.frames) == set(b.frames) and a.cut == b.cut
    for s in a.frames:
        pd.testing.assert_frame_equal(a.frames[s], b.frames[s])


@pytest.fixture
def led(tmp_path):
    lg = Ledger(Journal(tmp_path / "j.db"))
    lg.ensure()
    return lg


# ── separation, point-in-time, purge ─────────────────────────────────────
def test_discovery_and_heldout_b_are_disjoint_and_cover(tmp_path):
    b = _bundle(_store(tmp_path / "c.db"))
    full = {s: pd.DataFrame({"ts": pd.to_datetime(
        np.arange(1600) * TF_MS, unit="ms", utc=True)}) for s in SYMS}
    for s, df in b.frames.items():
        assert slices._ms(df).max() < b.cut
    hb = slices.heldout_b(full, b.cut, min_bars=1)
    for df in hb.values():
        assert slices._ms(df).min() >= b.cut


def test_future_label_leakage_control(tmp_path):
    """Rewriting every price after the cut leaves discovery untouched."""
    base = _bundle(_store(tmp_path / "a.db"))
    alt = _bundle(_store(tmp_path / "b.db", tamper_from=base_cut_bar(base)))
    _same(base, alt)


def base_cut_bar(b):
    return int(b.cut // TF_MS) + 1       # first bar at/after the cut


def test_revision_or_growth_after_the_cut_cannot_reach_discovery(tmp_path):
    base = _bundle(_store(tmp_path / "a.db"))
    grown = _store(tmp_path / "g.db", extra=400)
    drift = _bundle(grown)
    assert drift.cut > base.cut                 # the demonstrated drift
    capped = _bundle(grown, cap=base.cut)       # the fix
    _same(base, capped)


def test_load_heldout_b_context_never_trades_before_cut(tmp_path):
    p = _store(tmp_path / "c.db")
    cut = 1000 * TF_MS
    b = rf.load_heldout("4h", "b", SYMS, cut, CFG, paths={"candles": p})
    for s, df in b.frames.items():
        assert slices._ms(df)[b.first_bars[s]] == cut


def test_missing_heldout_evidence_yields_no_frames(tmp_path):
    p = _store(tmp_path / "c.db", bars=700)
    b = rf.load_heldout("4h", "b", SYMS, 690 * TF_MS, CFG,
                        paths={"candles": p})
    assert b.frames == {}                       # too short: not a pass


# ── spent held-out is permanent ──────────────────────────────────────────
def _spend(led, h="h1", cut=1000, tf="4h"):
    _t, a = led.next_alpha(0.10, 0.05)
    return led.record_test(h, tf, "fixed", "gate1", 0.5, a, False,
                           {"cut_ms": cut})


def test_second_look_at_a_spent_holdout_is_refused(led):
    _spend(led)
    with pytest.raises(ValueError, match="holdout_already_spent"):
        _spend(led)
    assert len(led.tests()) == 1


def test_reset_cannot_requeue_a_spent_candidate(led):
    led.set_candidate("h1", "4h", "fixed", "queued")
    _spend(led)
    led.set_candidate("h1", "4h", "fixed", "gate1_fail")
    led.set_candidate("h1", "4h", "fixed", "queued")      # the reset
    assert led.candidate("h1")["state"] == "gate1_fail"


def test_examine_refuses_a_spent_rule_without_a_look(led):
    led.set_candidate("h1", "4h", "fixed", "queued")
    _spend(led)
    fake = SimpleNamespace(ledger=led)
    out = ResearchRunner._examine(fake, "4h", "fixed", {"hash": "h1"}, 1000)
    assert out["state"] == "refused" and len(led.tests()) == 1


def test_cut_frozen_at_first_spent_look_and_survives_restart(led, tmp_path):
    assert led.spent_cut("4h") is None
    _spend(led, "h1", cut=1000)
    _spend(led, "h2", cut=1200)                 # a later, drifted look
    assert led.spent_cut("4h") == 1000
    led.record_slices("4h", 5000, {})           # drifted measure
    assert led.slices("4h")["cut_ms"] == 1000
    again = Ledger(Journal(tmp_path / "j.db"))  # restart
    assert again.spent_cut("4h") == 1000


def test_replay_gives_identical_split_and_bundle(tmp_path):
    p = _store(tmp_path / "c.db")
    _same(_bundle(p), _bundle(p))
