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
        if "FROM trades" in a[0] or "trade_accounting_bookings" in a[0]:
            return []
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


def test_runtime_vault_bootstrap_preserves_source_and_survives_restart(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'MOC.md').write_text('authored source')
    runtime = tmp_path / 'data' / 'knowledge'
    monkeypatch.setattr(vaultmod, 'SOURCE_VAULT', source)
    monkeypatch.setattr(vaultmod, 'VAULT', runtime)
    v = Vault(StubJournal())
    assert (runtime / 'MOC.md').read_text() == 'authored source'
    v.refresh_strategy_notes()
    v.write_moc()
    learned = (runtime / 'MOC.md').read_text()
    Vault(StubJournal())
    assert (runtime / 'MOC.md').read_text() == learned
    assert (source / 'MOC.md').read_text() == 'authored source'
    assert not (source / '20 Strategies').exists()


def test_strategy_live_record_counts_only_closed_trades(tmp_path, monkeypatch):
    """Open/unresolved exposure cannot inflate closed counts or booked P&L."""
    v = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(v.journal, "trades_for_strategy", lambda sid: [
        {"id": "c1", "status": "closed", "realized_pnl": 12.5},
        {"id": "c2", "status": "closed", "realized_pnl": -2.0},
        {"id": "o1", "status": "open", "realized_pnl": 900.0},
        {"id": "u1", "status": "unresolved", "realized_pnl": 800.0},
    ])
    v.refresh_strategy_notes()
    text = (tmp_path / "20 Strategies" / "Test_Strat.md").read_text()
    assert "closed trades: 2 · wins: 1" in text
    assert "realized P&L: +10.50 USDT" in text


def test_strategy_notes_do_not_depend_on_unused_cached_stats(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    rows = v.journal.list_strategies()
    rows[0]["stats_json"] = "malformed historical cache"
    monkeypatch.setattr(v.journal, "list_strategies", lambda: rows)
    assert v.refresh_strategy_notes() == 1
    assert (tmp_path / "20 Strategies" / "Test_Strat.md").exists()


def test_repeated_incident_preserves_prior_evidence(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    class FixedClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc)

    v = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(vaultmod, "datetime", FixedClock)
    folder = tmp_path / "30 Postmortems"
    original = folder / "20261020-1230 Book health.md"
    original.write_text("original historical evidence")
    v.incident_note("Book health", "second verdict")
    v.incident_note("Book health", "third verdict")
    assert original.read_text() == "original historical evidence"
    assert "second verdict" in (folder / "20261020-1230 Book health (1).md").read_text()
    assert "third verdict" in (folder / "20261020-1230 Book health (2).md").read_text()


def test_vault_monetary_notes_preserve_unknown_retained_booking(tmp_path, monkeypatch):
    """A later numeric close cannot erase an unpriced earlier exit receipt."""
    from trader.core.journal import Journal
    from trader.core.types import Position, Side

    v = _vault(tmp_path, monkeypatch)
    journal = Journal(tmp_path / "journal.db")
    rows = v.journal.list_strategies()
    monkeypatch.setattr(journal, "list_strategies", lambda: rows)
    journal.add_trade(Position(id="t1", symbol="BTC/USDT", side=Side.LONG,
                               amount=2, entry_price=100, notional_usdt=200,
                               strategy_id=1))
    journal.align_trade_amount("t1", 1, 100, accounting={
        "pnl_status": "UNKNOWN", "basis": "unpriced_exit"})
    journal.close_trade("t1", 107, 7, "test")
    v.journal = journal
    v.refresh_strategy_notes()
    text = (tmp_path / "20 Strategies" / "Test_Strat.md").read_text()
    assert "closed trades: 1 · wins: unavailable" in text
    assert "realized P&L: unavailable (UNKNOWN" in text
    assert "realized P&L: +7.00" not in text
    assert "realized P&L: unavailable (UNKNOWN" in v.daily_review().read_text()


def test_vault_labels_estimated_and_missing_money(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    wins, pnl = v._pnl_summary([{"id": "t1", "realized_pnl": 5,
                                "pnl_status": "ESTIMATE"}])
    assert wins == "1" and "DERIVED_ESTIMATE" in pnl
    assert "funding and venue net accounting not established" in pnl
    wins, pnl = v._pnl_summary([{"id": "t1", "realized_pnl": None}])
    assert wins == "unavailable" and "UNKNOWN" in pnl


def test_vault_preserves_executor_estimated_booking_basis(tmp_path, monkeypatch):
    """Executor's retained estimate marker must survive both narrative views."""
    from trader.core.journal import Journal
    from trader.core.types import Position, Side

    v = _vault(tmp_path, monkeypatch)
    journal = Journal(tmp_path / "journal.db")
    rows = v.journal.list_strategies()
    monkeypatch.setattr(journal, "list_strategies", lambda: rows)
    journal.add_trade(Position(id="t1", symbol="BTC/USDT", side=Side.LONG,
                               amount=1, entry_price=100, notional_usdt=100,
                               strategy_id=1))
    journal.close_trade("t1", 105, 5, "test", accounting={
        "basis": "venue_order_fills", "booked_pnl_basis": "estimated"})
    v.journal = journal
    v.refresh_strategy_notes()
    text = (tmp_path / "20 Strategies" / "Test_Strat.md").read_text()
    assert "+5.00 USDT (DERIVED_ESTIMATE;" in text
    assert "+5.00 USDT (DERIVED_ESTIMATE;" in v.daily_review().read_text()
