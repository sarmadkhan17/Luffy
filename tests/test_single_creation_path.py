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
