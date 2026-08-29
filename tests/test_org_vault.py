"""Knowledge-graph integration — employees become vault nodes.

Verifies `Vault.seed_company` writes an org chart + one node per employee, that
generated notes carry an `author:` stamp and a `Filed by [[…]]` backlink, and
that the MOC exposes a Company section. Runs against a temp vault dir so no
live journal or real knowledge/ folder is touched.
"""
from __future__ import annotations

import trader.knowledge.vault as vaultmod
from trader.knowledge.vault import Vault
from trader.org import Org


class StubJournal:
    def list_strategies(self):
        return [{"id": 1, "name": "Test Strat", "state": "ACTIVE",
                 "kind": "ema_trend", "origin": "harvested",
                 "hypothesis": "h", "invalidation": "i",
                 "params": "{}", "stats_json": "{}"}]

    def trades_for_strategy(self, sid):
        return []

    def query(self, *a):
        return [{"n": 0, "s": 0, "equity": 100.0}]

    def agent_accuracy(self):
        return [{"agent": "structure", "n": 10, "accuracy": 0.5}]


def _vault(tmp_path, monkeypatch):
    monkeypatch.setattr(vaultmod, "VAULT", tmp_path)
    return Vault(StubJournal())


def test_seed_company_writes_chart_and_nodes(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    org = Org.load()
    v.seed_company(org)

    chart = tmp_path / "00 Company" / "Company.md"
    assert chart.exists()
    assert "Manager" in chart.read_text()

    for e in org.all():
        note = tmp_path / "00 Company" / f"{e.name}.md"
        assert note.exists(), f"missing node for {e.name}"
        text = note.read_text()
        assert "type: role" in text
        if e.reports_to:
            assert f"[[{e.reports_to}]]" in text


def test_strategy_note_carries_author(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    v.refresh_strategy_notes()
    note = tmp_path / "20 Strategies" / "Test_Strat.md"
    text = note.read_text()
    assert "author:" in text
    # harvested origin → Researcher signs it
    assert "Researcher" in text
    assert "Filed by [[Researcher]]" in text


def test_incident_note_signed_by_theorist(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    v.incident_note("Test Incident", "body")
    notes = list((tmp_path / "30 Postmortems").glob("*Test Incident.md"))
    assert notes
    text = notes[0].read_text()
    assert "author: Theorist" in text
    assert "Filed by [[Theorist]]" in text


def test_daily_review_signed_by_manager(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    p = v.daily_review()
    text = p.read_text()
    assert "author: Manager" in text
    assert "Filed by [[Manager]]" in text


def test_moc_has_company_section(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    v.seed_company(Org.load())
    v.write_moc()
    text = (tmp_path / "MOC.md").read_text()
    assert "## Company" in text
    assert "[[Manager]]" in text
    assert "[[Strategist]]" in text
