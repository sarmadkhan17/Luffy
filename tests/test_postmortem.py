"""The Theorist without an LLM: is each live strategy still inside the
envelope it was admitted on?

The LLM Theorist spent ~55% of all tokens rewriting prose doctrine off
2-9-trade samples. What the book actually needs every 6h is this check,
which is arithmetic: Donchian Breakout Trail was admitted at a 38.85%
win rate, so 0 wins from 3 is an ordinary Tuesday, and 2 from 60 is broken.
"""
import inspect
import json

from trader.brain import postmortem as PM
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(sid, expected_wr=None):
    prov = {"expected_winrate": expected_wr} if expected_wr is not None else {}
    return StrategySpec(
        id=sid, name=sid, thesis="t", invalidation="i", provenance=prov,
        universe={"include": []}, timeframe="4h", direction="both",
        entry_long="close > ema(50)", entry_short="close < ema(50)",
        filters=[], exit=ExitSpec(), regime_filter=[], markets=["futures"])


class _Journal:
    def __init__(self, specs, trades, last=None, kv=None):
        self._specs, self._trades, self._last = specs, trades, last
        self.kv = kv or {}
        self.events = []

    def list_specs(self, states=None):
        return [({"state": "paper"}, s) for s in self._specs]

    def query(self, sql, args=()):
        if "FROM trades" in sql:
            w, n = self._trades.get(args[0], (0, 0))
            return [{"n": n, "w": w, "pnl": 0.0}]
        if "kind='postmortem'" in sql:
            return [{"detail": json.dumps(self._last)}] if self._last else []
        return []

    def log_brain_event(self, kind, subject, detail):
        self.events.append((kind, subject, detail))

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)


class _Vault:
    def __init__(self):
        self.notes = []

    def incident_note(self, title, body):
        self.notes.append((title, body))


def test_a_spec_with_a_validated_rate_gets_a_health_verdict():
    rows = PM.book_health(_Journal([_spec("s1", 0.3885)], {"s1": (2, 60)}))
    assert rows[0]["health"]["verdict"] == "DIVERGED"
    assert rows[0]["health"]["expected_winrate"] == 0.3885


def test_a_young_strategy_reads_insufficient_not_broken():
    rows = PM.book_health(_Journal([_spec("s1", 0.3885)], {"s1": (0, 3)}))
    assert rows[0]["health"]["verdict"] == "INSUFFICIENT"


def test_a_strategy_performing_as_validated_reads_consistent():
    rows = PM.book_health(_Journal([_spec("s1", 0.3885)], {"s1": (39, 61)}))
    assert rows[0]["health"]["verdict"] == "CONSISTENT"


def test_no_validated_rate_reports_no_health_rather_than_inventing_one():
    rows = PM.book_health(_Journal([_spec("s1")], {"s1": (1, 4)}))
    assert rows[0]["health"] is None


def test_the_first_run_records_every_verdict_as_a_change():
    j = _Journal([_spec("s1", 0.3885)], {"s1": (0, 3)})
    v = _Vault()
    rep = PM.run_postmortem(j, v)
    assert rep["ran"] is True
    assert rep["changed"] == {"s1": "INSUFFICIENT"}
    assert j.events[0][0] == "postmortem"
    assert len(v.notes) == 1


def test_an_unchanged_book_writes_no_note():
    j = _Journal([_spec("s1", 0.3885)], {"s1": (0, 3)},
                 last={"verdicts": {"s1": "INSUFFICIENT"}})
    v = _Vault()
    rep = PM.run_postmortem(j, v)
    assert rep["changed"] == {}
    assert v.notes == []
    assert j.events and j.events[0][0] == "postmortem"


def test_the_summary_names_each_strategy_and_the_rent():
    j = _Journal([_spec("Donchian", 0.3885)], {"Donchian": (0, 3)},
                 kv={"rent_state": json.dumps(
                     {"week_start": "2026-09-07", "net": 11.83, "bar": 50.0,
                      "status": "IN_PROGRESS"})})
    text = PM.summary_text(j)
    assert "Donchian" in text
    assert "11.83" in text and "2026-09-07" in text


def test_the_post_mortem_spends_no_tokens():
    src = inspect.getsource(PM)
    assert "BrainLLM" not in src and ".chat" not in src


def test_doctrine_loads_read_only():
    from trader.brain.doctrine import load_doctrine
    d = load_doctrine()
    assert "beliefs" in d and "version" in d
