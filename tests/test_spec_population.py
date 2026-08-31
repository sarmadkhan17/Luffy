import json

import pytest

from trader.core.journal import Journal
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(sid="s1", name="Population Probe"):
    return StrategySpec(
        id=sid, name=name,
        thesis="A hypothesis long enough for the validator, naming a plausible "
               "inefficiency and who is on the other side of it when it pays.",
        invalidation="Retire below pooled profit factor 0.85 over 30 days.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="15m", direction="long", entry_long="close > ema(20)",
        entry_short="", filters=[], exit=ExitSpec(),
        regime_filter=["TRENDING_UP"], markets=["futures"])


@pytest.fixture
def journal(tmp_path):
    return Journal(str(tmp_path / "j.db"))


def test_spec_roundtrips_through_the_journal(journal):
    journal.upsert_spec(_spec())
    rows = journal.list_specs(["paper"])
    assert len(rows) == 1
    row, spec = rows[0]
    assert spec.name == "Population Probe"
    assert spec.entry_long == "close > ema(20)"
    assert row["kind"] == "spec" and row["state"] == "paper"


def test_upsert_is_idempotent_and_updates(journal):
    s = _spec()
    journal.upsert_spec(s)
    s.name = "Renamed Probe"
    journal.upsert_spec(s)
    rows = journal.list_specs()
    assert len(rows) == 1 and rows[0][1].name == "Renamed Probe"


def test_state_filter_excludes_retired(journal):
    journal.upsert_spec(_spec("live", "Live One"), state="paper")
    journal.upsert_spec(_spec("dead", "Dead One"), state="retired")
    names = {s.name for _r, s in journal.list_specs(["paper", "active"])}
    assert names == {"Live One"}


def test_legacy_genome_rows_are_not_returned_as_specs(journal):
    from trader.core.types import Strategy, StrategyState
    journal.upsert_strategy(Strategy(
        id="g1", name="Legacy", kind="ema_trend", params={},
        state=StrategyState.PAPER, description="", origin="seed"))
    assert journal.list_specs() == []


def test_measured_regime_filter_survives_the_roundtrip(journal):
    s = _spec()
    s.regime_filter = ["TRENDING_DOWN", "TRENDING_UP"]
    journal.upsert_spec(s)
    _row, back = journal.list_specs()[0]
    assert back.regime_filter == ["TRENDING_DOWN", "TRENDING_UP"]


def test_spec_column_is_added_to_an_existing_table(journal):
    """The migration must be additive — legacy rows keep working."""
    journal._ensure_spec_column()
    journal._ensure_spec_column()          # idempotent
    cols = [r[1] for r in journal._conn().execute("PRAGMA table_info(strategies)")]
    assert "spec_json" in cols
