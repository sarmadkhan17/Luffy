"""Selective cleanup of legacy entry-booked research rows.

Only invalid derived combo rows go; thresholds, the frozen cut, the registered
budget, spent looks, candidates, controls and batch history stay, and the
existing planner resumes under portfolio-exit-realized.v1 without a new look.
"""
import json

import pytest

from trader.core.journal import Journal
from trader.research.ledger import Ledger
from trader.strategy.portfolio_evidence import ACCOUNTING_VERSION

from tests.test_research_control_unavailable import (   # noqa: F401
    CFG, _controls, _drive, _row, _runner, _world, registered_coverage)

NO_REFEREE = {**CFG, "research": {**CFG["research"], "referee": False}}
LEGACY = {"total_pct": 12.0, "max_dd_pct": 9.0, "taken": 40}


def _legacy(h, **kw):
    return {"hash": h, "tf": "4h", "geo": "trail", "k": 1, "verdict": "scored",
            "consistency_p": 0.02, "trades": 50, "scored_symbols": 8,
            "testable": True, "median_pf": 1.2, "portfolio": dict(LEGACY), **kw}


def _stable(j):
    """Every table whose rows the cleanup must leave byte-identical."""
    return {t: [tuple(r) for r in j._conn().execute(
        f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()]
        for t in ("research_gauges", "research_slices", "research_budget",
                  "research_tests", "research_candidates",
                  "research_controls", "research_batches")}


@pytest.fixture
def aged(tmp_path):
    """A ledger whose combos all pre-date the accounting contract."""
    j = Journal(tmp_path / "j.db")
    led = Ledger(j)
    led.ensure()
    led.record_gauges("4h", {"ret(24)": {"p10": -.1, "p25": -.02, "p75": .02,
                                         "p90": .1, "n": 9, "finite_frac": 1,
                                         "usable": True}})
    led.record_slices("4h", 1_741_452_480_000, {"discovery": {"BTC/USDT": 9}})
    led.record_control("4h", "ohlcv", {"consistency_p": 0.002}, True)
    led.register_budget(0.10, 0.05)
    bid = led.start_batch("4h", "trail", "singles", 3)
    led.finish_batch(bid, True, 1.0)
    for h in ("aa", "bb", "cc"):
        led.record_result(_legacy(h), "grow", "", {})
    return led


def test_report_does_not_write(aged):
    before = aged.counts()
    out = aged.discard_legacy_accounting()
    assert out == {"would_discard": 3, "retained_referenced": 0,
                   "applied": False}
    assert aged.counts() == before


def test_discard_removes_only_unreferenced_legacy_rows_and_keeps_history(aged):
    aged.record_test("aa", "4h", "trail", "gate1", 0.5,
                     aged.next_alpha(0.1, 0.05)[1], False, {"cut_ms": 1})
    aged.set_candidate("bb", "4h", "trail", "gate1_fail")
    stable = _stable(aged.journal)
    out = aged.discard_legacy_accounting(apply=True)
    assert out == {"discarded": 1, "retained_referenced": 2, "applied": True}
    assert {r["hash"] for r in aged.journal.query(
        "SELECT hash FROM research_combos")} == {"aa", "bb"}
    assert _stable(aged.journal) == stable
    assert aged.journal.query("SELECT COUNT(*) n FROM research_archive")[0]["n"] == 0


def test_current_accounting_rows_are_never_discarded(tmp_path):
    reply, st = _world(first_ms=[])
    r = _runner(tmp_path, reply)
    _drive(r, steps=6)
    n = r.journal.query("SELECT COUNT(*) n FROM research_combos")[0]["n"]
    assert n
    assert r.ledger.discard_legacy_accounting(apply=True)["discarded"] == 0
    assert r.journal.query("SELECT COUNT(*) n FROM research_combos")[0]["n"] == n


def test_discard_is_what_unblocks_the_planner_and_spends_no_look(aged, tmp_path):
    reply, st = _world(first_ms=[])
    from trader.research.runner import ResearchRunner
    r = ResearchRunner(aged.journal, NO_REFEREE,
                       run=lambda fn, payload, **kw: reply(fn, payload))
    assert r.step()["skipped"] == "portfolio_accounting_incompatible"
    assert st["calls"] == []
    stable = _stable(aged.journal)
    aged.discard_legacy_accounting(apply=True)
    outs = _drive(r, steps=12)
    assert not any(o.get("skipped") == "portfolio_accounting_incompatible"
                   for o in outs)
    assert st["calls"], "planner did not resume"
    j = aged.journal
    for t in ("research_gauges", "research_slices", "research_budget",
              "research_tests", "research_candidates"):
        assert _stable(j)[t] == stable[t]
    for row in j.query("SELECT result FROM research_combos"):
        pf = json.loads(row["result"]).get("portfolio") or {}
        assert pf["accounting"]["version"] == ACCOUNTING_VERSION
    assert j.query("SELECT COUNT(*) n FROM research_tests")[0]["n"] == 0


def test_a_spent_look_survives_recompute_and_cannot_be_respent(aged):
    aged.record_test("aa", "4h", "trail", "gate1", 0.5,
                     aged.next_alpha(0.1, 0.05)[1], False, {"cut_ms": 1})
    aged.discard_legacy_accounting(apply=True)
    assert aged.spent("aa") is not None
    with pytest.raises(ValueError, match="holdout_already_spent"):
        aged.record_test("aa", "4h", "trail", "gate1", 0.01, 0.01, False, {})
    assert aged.next_alpha(0.1, 0.05)[0] == 2        # sequence not reset


def test_rekey_path_discards_legacy_rows_instead_of_archiving_them(aged):
    aged.discard_legacy_accounting("4h", apply=True)     # what --rekey now does
    moved = aged.archive_horizon("4h", "v")
    assert moved["combos"] == 0
    assert {r["table_name"] for r in aged.journal.query(
        "SELECT table_name FROM research_archive")} == {"research_controls"}


def test_cli_reports_by_default_and_only_deletes_with_apply(
        aged, monkeypatch, capsys):
    import sys
    from trader.research import __main__ as cli
    monkeypatch.setattr(cli, "_journal", lambda: aged.journal)
    monkeypatch.setattr(sys, "argv", ["trader.research",
                                      "--discard-legacy-accounting"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["would_discard"] == 3
    assert aged.journal.query("SELECT COUNT(*) n FROM research_combos")[0]["n"] == 3
    monkeypatch.setattr(sys, "argv", ["trader.research",
                                      "--discard-legacy-accounting", "--apply"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["discarded"] == 3
    assert aged.journal.query("SELECT COUNT(*) n FROM research_combos")[0]["n"] == 0
