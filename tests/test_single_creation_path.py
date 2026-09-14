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
import json

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal


def _row(j, sid, kind, state):
    with j._tx() as c:
        c.execute(
            "INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json) VALUES (?,?,?,?,?,'d','seed',"
            "'h','i','[]','[\"futures\"]',0,'','2026-08-01T00:00:00+00:00','{}')",
            (sid, sid, kind,
             json.dumps({"adx_min": 22.0, "pullback_atr": 0.8}), state))


class _NoLLM:
    available = False

    def budget_left(self):
        return 0


def _strategist(tmp_path):
    from trader.brain.strategist import Strategist
    j = Journal(tmp_path / "j.db")
    cfg = load_config()
    cfg["strategies"]["gauntlet_time_budget_s"] = 0
    s = Strategist(j, cfg)
    s.llm = _NoLLM()               # never reach the network from a test
    return j, s


def _states(j):
    return {r["id"]: r["state"]
            for r in j.query("SELECT id, state FROM strategies")}


def test_a_mutate_verdict_creates_no_strategy(tmp_path):
    j, s = _strategist(tmp_path)
    _row(j, "dead", "ema_trend", "retired")
    _row(j, "live", "ema_trend", "paper")
    before = _states(j)
    s._apply({
        "dead": {"id": "dead", "action": "mutate",
                 "param": "adx_min", "new_value": 27},
        "live": {"id": "live", "action": "mutate",
                 "param": "adx_min", "new_value": 27},
    })
    assert _states(j) == before


def test_a_verdict_never_touches_a_spec(tmp_path):
    j, s = _strategist(tmp_path)
    _row(j, "the_spec", "spec", "paper")
    s._apply({"the_spec": {"id": "the_spec", "action": "retire",
                           "rationale": "zero trades, retire it"}})
    assert _states(j)["the_spec"] == "paper"


def test_the_llm_is_shown_only_live_legacy_genomes(tmp_path):
    j, s = _strategist(tmp_path)
    _row(j, "the_spec", "spec", "paper")
    _row(j, "dead", "ema_trend", "retired")
    _row(j, "live", "ema_trend", "paper")
    assert {p["id"] for p in s._population_report()} == {"live"}


def test_the_legacy_proposer_is_gone():
    """It had no caller after 2026-09-11; a module that writes strategies
    and is called by nothing is a second creation path waiting for one."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("trader.strategy.proposer")


def test_the_mutation_path_is_gone():
    from trader.brain.strategist import Strategist
    for gone in ("_mutate_row", "_mutations_today"):
        assert not hasattr(Strategist, gone), (
            f"Strategist.{gone} still exists — that is the population-writing "
            f"path that resurrected a retired genome into paper")


def test_the_kernel_brain_tick_runs_no_legacy_review():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "brain.strategist import" not in src
    assert "Strategist(" not in src


# ── the search's handoff (research phase 3) ──────────────────────────────
def test_no_research_module_writes_the_strategies_table():
    """The search reaches the book only through kernel._research_handoff,
    which calls analyst.admit. Reading `strategies` (seed parts, the
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
    from trader.research.combo import Combination
    from trader.research.ledger import Ledger

    j = Journal(tmp_path / "j.db")
    led = Ledger(j)
    led.ensure()
    led.record_gauges("4h", {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5,
                                 "p90": 1.0, "n": 9000, "finite_frac": 0.99,
                                 "usable": True}
                             for e in vocab.expressions("4h")})
    part = vocab.parts_for("4h", led.gauges("4h"))[0]
    c = Combination((part,), "4h", "trail")
    led.record_result({"hash": c.hash, "tf": "4h", "geo": "trail", "k": 1,
                       "parts": list(c.keys), "trades": 300,
                       "portfolio": {"total_pct": 40.0, "max_dd_pct": 20.0},
                       "testable": True, "verdict": "scored"},
                      "survivor", "r", {})
    led.set_candidate(c.hash, "4h", "trail", "reason_passed", rank=40.0)

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


def test_an_open_handoff_goes_through_admit_and_nothing_else(tmp_path):
    k, analyst, seen, c, led = _handoff_kernel(tmp_path, handoff=True)
    spec = k._research_handoff(analyst, [])
    assert spec is not None and len(seen) == 1
    assert seen[0].provenance["research_hash"] == c.hash
    assert len(seen[0].universe["include"]) == 36
    assert led.candidate(c.hash)["state"] == "admitted"
    assert _states(k.journal)[spec.id] == "paper"


def test_a_refused_candidate_is_recorded_and_not_installed(tmp_path):
    k, analyst, seen, c, led = _handoff_kernel(tmp_path, handoff=True,
                                               admit_ok=False)
    assert k._research_handoff(analyst, []) is None
    assert led.candidate(c.hash)["state"] == "refused"
    assert _states(k.journal) == {}


def test_only_reason_passed_candidates_are_handed_off(tmp_path):
    k, analyst, seen, c, led = _handoff_kernel(tmp_path, handoff=True)
    led.set_candidate(c.hash, "4h", "trail", "referee_passed")
    assert k._research_handoff(analyst, []) is None
    assert seen == []
