"""Where the SG-05 accounting contract and the RP-06 unavailable-control
contract meet. Each test pins one thing neither branch's own tests can see.
"""
import json

import pytest

from trader.core.child import ChildResult
from trader.research import control
from trader.strategy.portfolio_evidence import AccountingMismatch

from tests.test_research_control_unavailable import (   # noqa: F401
    CUT, _controls, _drive, _row, _runner, _world, registered_coverage)

LEGACY = {"total_pct": 12.0, "max_dd_pct": 9.0, "taken": 40}   # no accounting


def _first_control_batch_result(st):
    return next(c for call in st["calls"] for c in call if c[0] == "control")


def test_unavailable_control_under_corrected_accounting_is_not_powered(tmp_path):
    reply, st = _world(first_ms=[])
    r = _runner(tmp_path, reply)
    _drive(r)
    unav = {w: c for w, c in _controls(r).items()
            if c["status"] == "unavailable"}
    assert unav
    for c in unav.values():
        assert c["powered"] == 0 and c["consistency_p"] is None
    # every stored child row is under the corrected accounting version
    for row in r.journal.query("SELECT result FROM research_combos"):
        pf = json.loads(row["result"]).get("portfolio") or {}
        assert pf.get("accounting", {}).get("version") == "portfolio-exit-realized.v1"


def test_a_legacy_shaped_control_result_is_refused_never_recorded(tmp_path):
    """A child that returns a control without accounting provenance must not
    be able to create an `unavailable` (or any) control row."""
    inner, st = _world(first_ms=[])

    def reply(fn, payload):
        out = inner(fn, payload)
        if fn.__name__ == "evaluate_job" and out.value:
            for row in out.value["results"]:
                row["portfolio"] = dict(LEGACY)
        return out
    r = _runner(tmp_path, reply)
    outs = [r.step() for _ in range(4)]
    assert any(o.get("skipped") == "portfolio_accounting_incompatible"
               for o in outs)
    assert _controls(r) == {}
    assert r.journal.query("SELECT COUNT(*) n FROM research_combos")[0]["n"] == 0


def test_legacy_ledger_halts_before_any_child_even_with_unavailable_controls(
        tmp_path):
    reply, st = _world(first_ms=[])
    r = _runner(tmp_path, reply)
    _drive(r)
    # age the ledger: one stored row in the legacy accounting era
    row = r.journal.query("SELECT hash, result FROM research_combos LIMIT 1")[0]
    res = json.loads(row["result"])
    res["portfolio"] = dict(LEGACY)
    with r.journal._tx() as c:
        c.execute("UPDATE research_combos SET result=? WHERE hash=?",
                  (json.dumps(res), row["hash"]))
    calls, controls = len(st["calls"]), _controls(r)
    out = r.step()
    assert out["skipped"] == "portfolio_accounting_incompatible"
    assert len(st["calls"]) == calls and _controls(r) == controls


def test_new_accounting_cannot_overwrite_a_legacy_result_or_a_spent_look(
        tmp_path):
    reply, st = _world(first_ms=[])
    r = _runner(tmp_path, reply)
    _drive(r)
    led = r.ledger
    row = r.journal.query(
        "SELECT hash, tf, geo, result FROM research_combos LIMIT 1")[0]
    h = row["hash"]
    # a legacy-era result and a spent look on the same rule
    legacy = json.loads(row["result"])
    legacy["portfolio"] = dict(LEGACY)
    with r.journal._tx() as c:
        c.execute("UPDATE research_combos SET result=? WHERE hash=?",
                  (json.dumps(legacy), h))
    led.register_budget(0.10, 0.05)
    _t, alpha_t = led.next_alpha(0.10, 0.05)
    led.record_test(h, row["tf"], row["geo"], "gate1", 0.2, alpha_t, False,
                    {"cut_ms": CUT})
    tests = [dict(x) for x in r.journal.query("SELECT * FROM research_tests")]
    stored = r.journal.query(
        "SELECT result FROM research_combos WHERE hash=?", (h,))[0]["result"]
    # a corrected-accounting result for the same hash is refused outright
    corrected = _combo_stub(h, row["tf"], row["geo"])
    with pytest.raises(AccountingMismatch):
        led.record_result(corrected, "grow", "x", {})
    assert r.journal.query(
        "SELECT result FROM research_combos WHERE hash=?",
        (h,))[0]["result"] == stored
    # and the look is still spent, the budget sequence unchanged
    assert led.spent(h) is not None
    with pytest.raises(ValueError):
        led.record_test(h, row["tf"], row["geo"], "gate1", 0.001, alpha_t,
                        False, {})
    assert [dict(x) for x in
            r.journal.query("SELECT * FROM research_tests")] == tests


def _combo_stub(h, tf, geo):
    """A corrected-accounting result row carrying an existing hash."""
    from trader.strategy.portfolio_evidence import (
        RESEARCH_CLOCK, accounting_provenance)
    return {"hash": h, "tf": tf, "geo": geo, "k": 1, "round": "singles",
            "parent": None, "trigger": "x", "parts": [], "window": "ohlcv",
            "entry_long": "", "entry_short": "", "symbols": {},
            "trades": 10, "scored_symbols": 8, "consistency_p": 0.01,
            "median_pf": 1.1, "median_rate": 0.0,
            "portfolio": {"total_pct": 1.0, "max_dd_pct": 1.0, "taken": 1,
                          "accounting": accounting_provenance(
                              2000, .5, 8, RESEARCH_CLOCK)},
            "projection": {"markets_a": 6, "markets_b": 6},
            "testable": True, "verdict": "scored"}


def test_unavailable_identity_is_independent_of_accounting_version():
    ident = control.evidence_identity()
    assert set(ident) == {"schema", "min_symbols", "channel"}
