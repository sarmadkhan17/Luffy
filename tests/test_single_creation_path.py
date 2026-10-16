"""One creation path: Scraper queues -> Strategist writes a spec -> Analyst admits.

On 2026-09-11 the legacy `brain/strategist.py` review answered a DeepSeek
"mutate" verdict on a RETIRED genome (strat_606048ec95, live PF 0.168) by
inserting a child straight into `paper` — trade-eligible, no backtest,
stats={} — and the very next cycle opened 7 correlated shorts on it. The
same review also ran the legacy Proposer and its invention pass: three
population-writing paths on the hourly brain tick, none through the Analyst.
It also showed the LLM every row, specs included, and would have acted on a
"retire" verdict against the book's one validated spec.
"""
import importlib
import inspect

import pytest

from trader.core.journal import Journal


def _states(j):
    return {r["id"]: r["state"]
            for r in j.query("SELECT id, state FROM strategies")}


def test_the_legacy_proposer_is_gone():
    """It had no caller after 2026-09-11; a module that writes strategies
    and is called by nothing is a second creation path waiting for one."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trader.strategy.proposer")


def test_the_legacy_review_module_is_gone():
    """The unused genome reviewer must not become a second lifecycle path."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trader.brain.strategist")


def test_the_kernel_brain_tick_runs_no_legacy_review():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "brain.strategist import" not in src
    assert "Strategist(" not in src


# ── the search's handoff (research phase 3) ──────────────────────────────
def test_no_research_module_writes_the_strategies_table():
    """Research handoff authenticates existing evidence and stops at Factory
    VALIDATED; it cannot write the trading book. Reading `strategies` (seed parts, the
    incumbent's universe, the brake) is fine; writing it is a second path."""
    import pathlib
    import re
    pat = re.compile(r"(INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)"
                     r"\s+strategies\b|upsert_spec", re.I)
    for f in pathlib.Path("trader/research").glob("*.py"):
        assert not pat.search(f.read_text()), f"{f} writes strategies"


def _handoff_kernel(tmp_path, handoff, admit_ok=True):
    from trader.core.child import ChildResult  # noqa: F401
    from trader.kernel import Kernel
    from trader.research import vocab
    from trader.research.combo import Combination, evaluated_record
    from trader.research.ledger import Ledger

    j = Journal(tmp_path / "j.db")
    led = Ledger(j)
    led.ensure()
    led.record_gauges("4h", {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5,
                                 "p90": 1.0, "n": 9000, "finite_frac": 0.99,
                                 "usable": True}
                             for e in vocab.expressions("4h")})
    part = vocab.parts_for("4h", led.gauges("4h"))[0]
    c = Combination((part,), "4h", "fixed")
    led.record_result({"hash": c.hash, "tf": "4h", "geo": "fixed", "k": 1,
                       "parts": list(c.keys), "trades": 300,
                       "portfolio": {"total_pct": 40.0, "max_dd_pct": 20.0},
                       "testable": True, "verdict": "scored"},
                      "survivor", "r", {})
    # TEST-ONLY registered referee evidence; an Analyst result cannot replace it.
    led.record_test(c.hash, "4h", "fixed", "gate1", .001, .0025, False,
                    {"t": 1, "evaluated": evaluated_record(c),
                     "cut_ms": 1_780_000_000_000, "draws": 1999,
                     "a": {"consistency_p_dep": .0005},
                     "b": {"consistency_p_dep": .0005},
                     "rotation": {"p": .001}})
    led.set_candidate(c.hash, "4h", "fixed", "reason_passed", rank=40.0,
                      gate1={"p": .001, "alpha": .0025, "t": 1},
                      gate3={"passed": True, "reason": "TEST-ONLY book"})

    seen = []

    class _Analyst:
        def admit(self, spec, book):
            seen.append(spec)
            return admit_ok, {"reason": "test", "chosen_timeframe": "4h",
                              "recent": {"pooled_pf": 1.3, "trades": 30}}

        def set_measured_regimes(self, spec):
            return []

    k = Kernel.__new__(Kernel)
    k.journal = j
    k.cfg = {"research": {"handoff": handoff}}
    k.notifier = None
    return k, _Analyst(), seen, c, led


def test_a_closed_handoff_admits_nothing(tmp_path):
    k, analyst, seen, c, led = _handoff_kernel(tmp_path, handoff=False)
    assert k._research_handoff(analyst, []) is None
    assert seen == []
    assert led.candidate(c.hash)["state"] == "reason_passed"


def test_an_open_handoff_requires_bank_and_does_not_call_admit(tmp_path):
    k, analyst, seen, c, led = _handoff_kernel(tmp_path, handoff=True)
    result = k._research_handoff(analyst, [])
    assert result["status"] == "DEFERRED"
    assert result["reason_code"] == "research_bank_required"
    assert seen == [] and _states(k.journal) == {}
    assert led.candidate(c.hash)["state"] == "reason_passed"


def test_unavailable_lineage_does_not_permanently_refuse_candidate(tmp_path):
    k, analyst, seen, c, led = _handoff_kernel(tmp_path, handoff=True,
                                               admit_ok=False)
    assert k._research_handoff(analyst, [])["status"] == "DEFERRED"
    assert led.candidate(c.hash)["state"] == "reason_passed"
    assert seen == [] and _states(k.journal) == {}


def test_referee_passed_label_alone_does_not_replace_authentication(tmp_path):
    k, analyst, seen, c, led = _handoff_kernel(tmp_path, handoff=True)
    led.set_candidate(c.hash, "4h", "fixed", "referee_passed")
    assert k._research_handoff(analyst, [])["status"] == "DEFERRED"
    assert led.candidate(c.hash)["state"] == "referee_passed"
    assert seen == [] and _states(k.journal) == {}
