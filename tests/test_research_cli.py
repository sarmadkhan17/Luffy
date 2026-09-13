"""The CLI's exit code and its behaviour on an empty ledger.

Task 15 scripts `--once`/`--measure` and reads the process exit code to tell
"ran and failed" from "ran and succeeded" or "stood down" — it does not
parse the JSON for that. And `--top` on a ledger with nothing in it yet must
say so on stdout rather than exit silently, which is indistinguishable from
a crash or a `--tf`/`horizons` misconfiguration that skipped every loop.

These tests never touch `data/luffy.db`: `_journal` is monkeypatched to a
`tmp_path` database, and `ResearchRunner.step` is monkeypatched so no real
search work runs.
"""
import sys

from trader.core.journal import Journal
from trader.research import __main__ as cli
from trader.research.runner import ResearchRunner


def _use_tmp_journal(monkeypatch, tmp_path):
    db = tmp_path / "luffy.db"
    monkeypatch.setattr(cli, "_journal", lambda: Journal(str(db)))


def test_once_exits_1_when_the_step_failed(monkeypatch, tmp_path, capsys):
    _use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ResearchRunner, "step",
        lambda self, cycle_seconds=None: {"skipped": None, "ok": False,
                                          "error": "child crashed"})
    monkeypatch.setattr(sys, "argv", ["trader.research", "--once"])
    assert cli.main() == 1


def test_once_exits_0_when_the_step_stands_down(monkeypatch, tmp_path,
                                                capsys):
    """A stand-down report carries no `ok` key at all and is not a
    failure."""
    _use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ResearchRunner, "step",
        lambda self, cycle_seconds=None: {"skipped": "busy"})
    monkeypatch.setattr(sys, "argv", ["trader.research", "--once"])
    assert cli.main() == 0


def test_once_exits_0_when_the_step_ran_and_succeeded(monkeypatch, tmp_path,
                                                       capsys):
    _use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ResearchRunner, "step",
        lambda self, cycle_seconds=None: {"skipped": None, "ok": True,
                                          "gauges": 3})
    monkeypatch.setattr(sys, "argv", ["trader.research", "--once"])
    assert cli.main() == 0


def test_measure_refuses_a_horizon_that_already_holds_scored_combos(
        monkeypatch, tmp_path, capsys):
    """Thresholds are frozen once measured, deliberately: a stored hash
    carries a percentile RANK, never its value, so a silent re-measure
    would re-point every existing hash at different numbers while
    `Ledger.known()` still treats it as already evaluated under the old
    ones."""
    _use_tmp_journal(monkeypatch, tmp_path)
    j = cli._journal()
    from trader.research.ledger import Ledger
    led = Ledger(j)
    led.ensure()
    led.record_result(
        {"hash": "a", "tf": "4h", "geo": "trail", "k": 1,
         "consistency_p": 0.01, "portfolio": {"total_pct": 10.0}},
        "grow", "carries information", {})
    stepped = []
    monkeypatch.setattr(ResearchRunner, "step",
                        lambda self, cycle_seconds=None: stepped.append(1)
                        or {"skipped": None, "ok": True})
    monkeypatch.setattr(sys, "argv",
                        ["trader.research", "--measure", "--tf", "4h"])
    assert cli.main() == 1
    assert stepped == []                    # never ran — refused first
    out = capsys.readouterr().out
    assert "refusing" in out
    assert led.combos_exist("4h") == 1      # nothing was archived either


def test_measure_rekey_archives_then_proceeds(monkeypatch, tmp_path, capsys):
    _use_tmp_journal(monkeypatch, tmp_path)
    j = cli._journal()
    from trader.research.ledger import Ledger
    led = Ledger(j)
    led.ensure()
    led.record_result(
        {"hash": "a", "tf": "4h", "geo": "trail", "k": 1,
         "consistency_p": 0.01, "portfolio": {"total_pct": 10.0}},
        "grow", "carries information", {})
    monkeypatch.setattr(
        ResearchRunner, "step",
        lambda self, cycle_seconds=None: {"skipped": None, "ok": True,
                                          "gauges": 3})
    monkeypatch.setattr(
        sys, "argv",
        ["trader.research", "--measure", "--tf", "4h", "--rekey"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "archived 1 combo" in out
    assert led.combos_exist("4h") == 0
    archived = led.journal.query(
        "SELECT * FROM research_archive WHERE tf='4h'")
    assert archived and archived[0]["table_name"] == "research_combos"


def test_top_on_an_empty_ledger_says_so_rather_than_staying_silent(
        monkeypatch, tmp_path, capsys):
    _use_tmp_journal(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["trader.research", "--top", "5", "--tf", "4h"])
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert out.strip() != "", "an empty ledger printed nothing"
    assert "4h" in out
