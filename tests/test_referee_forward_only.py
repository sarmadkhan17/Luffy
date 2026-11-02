"""Opt-in forward-only referee (Task 3.1): held-out B must start after a candidate's selection time.

Default OFF: with the flag absent nothing below changes existing behaviour. Everything here runs on temporary
stores; no held-out price is read by any refusal (the refusal precedes every held-out read and spends nothing).
"""
import json
from types import SimpleNamespace

import pytest

from trader.core.journal import Journal
from trader.core.types import TF_MS
from trader.research import referee
from trader.research.ledger import Ledger
from trader.research import runner as R
from trader.research.runner import ResearchRunner, forward_first_batch, forward_rank_key
from trader.strategy.portfolio_evidence import RESEARCH_CLOCK, accounting_provenance

STEP = TF_MS["4h"]
SELECTED_AT = "2026-09-13T16:49:01.875456+00:00"
SELECTED_MS = referee.selection_ms(SELECTED_AT)


# ── pure helpers ─────────────────────────────────────────────────────────
def test_default_is_off_and_first_batch_is_ten():
    assert R.DEFAULTS["referee_forward_only"] is False and R.DEFAULTS["referee_first_batch"] == 10


def test_selection_ms_parses_ledger_timestamps_and_rejects_garbage():
    assert SELECTED_MS == 1_789_318_141_875
    assert referee.selection_ms("2026-09-13T16:49:01+00:00") == 1_789_318_141_000
    assert referee.selection_ms(None) is None and referee.selection_ms("not a time") is None


def test_b_must_start_at_least_ten_bars_after_selection():
    earliest = SELECTED_MS + referee.FORWARD_EMBARGO_BARS * STEP
    assert referee.selection_overlap(earliest, SELECTED_MS, "4h") is None
    refusal = referee.selection_overlap(earliest - 1, SELECTED_MS, "4h")
    assert refusal.startswith("selection_overlap") and str(earliest) in refusal
    assert referee.selection_overlap(1_741_452_480_000, SELECTED_MS, "4h"), "the 2025 discovery cut precedes selection"


def test_an_unreadable_selection_time_fails_closed():
    assert referee.selection_overlap(10 ** 15, None, "4h").startswith("selection_time_unknown")


def _row(h, p, pf, total):
    return dict(hash=h, consistency_p=p, median_pf=pf, total_pct=total)


def test_ranking_uses_discovery_evidence_only_and_is_deterministic():
    rows = [_row("c", 1e-9, 1.2, 900.0), _row("a", 1e-12, 1.1, 1.0), _row("b", 1e-12, 1.9, 5.0), _row("n", None, 9.0, 99.0)]
    ranked = [r["hash"] for r in sorted(rows, key=forward_rank_key)]
    assert ranked == ["b", "a", "c", "n"]                  # p asc, then pf desc, None last; total_pct never consulted
    assert ranked == [r["hash"] for r in sorted(reversed(rows), key=forward_rank_key)]
    assert forward_rank_key({"hash": "x", "consistency_p": 0.1, "median_pf": 1.0}) == (0.1, -1.0, "x")


def test_first_batch_is_capped_across_calls():
    rows = [_row(f"h{i:02d}", i * 1e-6, 1.0, 0.0) for i in range(30)]
    assert [r["hash"] for r in forward_first_batch(rows, 0, 10)] == [f"h{i:02d}" for i in range(10)]
    assert len(forward_first_batch(rows, 4, 10)) == 6
    assert forward_first_batch(rows, 10, 10) == [] and forward_first_batch(rows, 25, 10) == []


# ── wiring: _referee_step ────────────────────────────────────────────────
def _step_runner(cfg_research, already=0):
    picked = {}
    fresh = [_row("low_p_low_total", 1e-12, 1.5, 1.0), _row("high_p_high_total", 1e-3, 1.5, 500.0),
             _row("mid", 1e-8, 1.5, 50.0)]
    fresh = [dict(r, testable=1) for r in fresh]
    led = SimpleNamespace(slices=lambda tf: {"cut_ms": 1}, rows=lambda tf, geo, verdict=None: list(fresh),
                          candidate=lambda h: None, candidates=lambda tf, geo, state=None: [object()] * already if state is None else [])
    fake = SimpleNamespace(cfg={"research": cfg_research}, ledger=led,
                           _r=lambda k: {"horizons": ["4h"], "geometries": ["trail"], "referee_select_batch": 20}[k],
                           _select=lambda tf, geo, rows: picked.setdefault("rows", [r["hash"] for r in rows]) or {})
    return fake, picked


def test_flag_off_keeps_the_existing_total_pct_order():
    fake, picked = _step_runner({})
    ResearchRunner._referee_step(fake)
    assert picked["rows"] == ["high_p_high_total", "mid", "low_p_low_total"]


def test_flag_on_uses_the_fixed_ranking_and_the_cap():
    fake, picked = _step_runner({"referee_forward_only": True, "referee_first_batch": 2})
    ResearchRunner._referee_step(fake)
    assert picked["rows"] == ["low_p_low_total", "mid"]                    # fixed rank, capped at 2
    fake, picked = _step_runner({"referee_forward_only": True, "referee_first_batch": 2}, already=2)
    ResearchRunner._referee_step(fake)
    assert "rows" not in picked, "nothing more is selected once the first batch is full"


# ── wiring: _examine defers an overlapping candidate before any held-out read and spends nothing ──
def _candidate_runner(tmp_path, monkeypatch, *, flag, cut_ms):
    led = Ledger(Journal(str(tmp_path / "j.db")))
    led.ensure()
    led.set_candidate("late", "4h", "fixed", "queued")
    with led.journal._tx() as c:
        c.execute("INSERT INTO research_combos (hash, tf, geo, k, result, created_at) VALUES ('late','4h','fixed',1,?,?)",
                  (json.dumps({"portfolio": {"accounting": accounting_provenance(2000, .5, 8, RESEARCH_CLOCK)}}), SELECTED_AT))
    monkeypatch.setattr(referee, "current_cut", lambda tf, disc, paths, recorded: cut_ms)
    monkeypatch.setattr(referee, "rotation_capacity", lambda *a, **k: None)
    monkeypatch.setattr(referee, "rotation_deferral", lambda capacity, draws: "reached_rotation_check")
    fake = SimpleNamespace(ledger=led, journal=led.journal, cfg={"research": {"referee_forward_only": True}} if flag else {},
                           _r=lambda k: {"fdr_target": 0.10, "lord_w0": 0.05, "referee_max_draws": 19999}[k],
                           _brake=lambda: 1.0, _combo=lambda row, tf, geo: object(),
                           _symbols=lambda tf: ([], []), _paths=lambda: {})
    return led, fake


def test_an_overlapping_candidate_is_deferred_with_the_reason_and_nothing_is_spent(tmp_path, monkeypatch):
    led, fake = _candidate_runner(tmp_path, monkeypatch, flag=True, cut_ms=1_741_452_480_000)
    out = ResearchRunner._examine(fake, "4h", "fixed", {"hash": "late"}, 1)
    assert out["state"] == "deferred"
    cand = led.candidate("late")
    assert cand["state"] == "deferred" and cand["reason"].startswith("selection_overlap")
    assert led.tests() == [] and led.spent("late") is None


def test_a_candidate_with_enough_forward_distance_passes_the_new_check(tmp_path, monkeypatch):
    led, fake = _candidate_runner(tmp_path, monkeypatch, flag=True, cut_ms=SELECTED_MS + 11 * STEP)
    out = ResearchRunner._examine(fake, "4h", "fixed", {"hash": "late"}, 1)
    assert led.candidate("late")["reason"] == "reached_rotation_check", "it went on to the existing checks"
    assert out["state"] == "deferred" and led.tests() == []


def test_with_the_flag_off_the_new_check_never_runs(tmp_path, monkeypatch):
    led, fake = _candidate_runner(tmp_path, monkeypatch, flag=False, cut_ms=1_741_452_480_000)
    ResearchRunner._examine(fake, "4h", "fixed", {"hash": "late"}, 1)
    assert led.candidate("late")["reason"] == "reached_rotation_check"
