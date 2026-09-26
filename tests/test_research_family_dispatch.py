"""research-family-dispatch.v1: deterministic routing across the two TESTED
research families, with verbatim refusals and fail-closed ambiguity."""
import ast
import json
import random
from pathlib import Path

import pytest

from tests.research_shadow_fixtures import I
from tests.test_strategy_decay_research_plan import CF, D, EF, W, _FullHist
from trader.cognition import research_families as rf
from trader.cognition import research_question as rq
from trader.cognition import research_unreadable_question as uq
from trader.strategy import health_observation as ho

ROOT = Path(__file__).resolve().parents[1]


def _hist(*sweeps, **kw):
    h = _FullHist()
    for verdicts, minute in sweeps:
        h.sweep(dict(verdicts), minute, **kw)
    return h


def _spec_sources(rows):
    return [(r["id"], r["subject"]) for r in rows
            if r["kind"] == ho.KIND_SPEC]


def _by_event(rows):
    return {d["source_event_id"]: d
            for d in rf.dispatch(rows, _spec_sources(rows))}


# ── registry ─────────────────────────────────────────────────────────────
def test_registry_is_frozen_to_the_two_tested_families():
    assert rf.FAMILY_NAMES == ("strategy_decay", "strategy_health_unreadable")
    assert rf.FAMILIES[0].derive is rq.derive
    assert rf.FAMILIES[1].derive is uq.derive
    assert [f.question_schema for f in rf.FAMILIES] == [rq.SCHEMA, uq.SCHEMA]
    with pytest.raises(Exception):
        rf.FAMILIES[0].name = "other"


# ── outcomes ─────────────────────────────────────────────────────────────
def test_decayed_source_routes_to_strategy_decay_with_its_question():
    rows = _hist(({"s1": W}, 10), ({"s1": D}, 20)).rows
    got = _by_event(rows)
    [q] = rq.derive(rows).questions
    d = got[q["source"]["event_id"]]
    assert d["outcome"] == rf.ROUTED and d["family"] == rf.STRATEGY_DECAY
    fam = {f["family"]: f for f in d["families"]}
    assert fam[rf.STRATEGY_DECAY]["question_id"] == q["question_id"]
    assert fam[rf.STRATEGY_HEALTH_UNREADABLE]["accepted"] is False
    rf.check(d)


@pytest.mark.parametrize("verdict", [EF, CF])
def test_unreadable_source_routes_to_strategy_health_unreadable(verdict):
    rows = _hist(({"s1": W}, 10), ({"s1": verdict}, 20)).rows
    [q] = uq.derive(rows).questions
    d = _by_event(rows)[q["source"]["event_id"]]
    assert d["outcome"] == rf.ROUTED
    assert d["family"] == rf.STRATEGY_HEALTH_UNREADABLE
    assert d["families"][1]["question_id"] == q["question_id"]


@pytest.mark.parametrize("verdict", [W, I])
def test_truthful_non_decayed_source_is_not_routed_and_no_reason_invented(
        verdict):
    rows = _hist(({"s1": verdict}, 10)).rows
    [d] = _by_event(rows).values()
    assert d["outcome"] == rf.NOT_ROUTED and d["family"] is None
    assert all(f["accepted"] is False and f["refusals"] == []
               and f["question_id"] is None for f in d["families"])


def test_family_refusals_are_preserved_verbatim():
    # a decayed observation whose sweep record was never written
    h = _FullHist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": D}, 20, write_sweep=False)
    rows = h.rows
    eid = rows[-1]["id"]
    d = _by_event(rows)[eid]
    assert d["outcome"] == rf.NOT_ROUTED
    want = [{k: getattr(r, k) for k in rf.REFUSAL_KEYS}
            for r in rq.derive(rows).refusals if r.event_id == eid]
    assert want and d["families"][0]["refusals"] == want
    assert want[0]["reason"] == rq.SWEEP_RECORD_MISSING


def test_unreadable_family_refusal_is_preserved_verbatim():
    h = _FullHist()
    h.sweep({"s1": EF}, 10, write_sweep=False)
    rows = h.rows
    d = _by_event(rows)[rows[0]["id"]]
    assert d["outcome"] == rf.NOT_ROUTED
    [r] = uq.derive(rows).refusals
    assert d["families"][1]["refusals"] == [
        {k: getattr(r, k) for k in rf.REFUSAL_KEYS}]


def test_whole_history_refusal_names_every_source():
    h = _hist(({"s1": D}, 10), ({"s2": EF}, 20))
    h._add(ho.KIND_SWEEP, "bad", "not json")
    rows = h.rows
    got = _by_event(rows)
    assert len(got) == 2
    for d in got.values():
        assert d["outcome"] == rf.NOT_ROUTED
        for f in d["families"]:
            assert [x["reason"] for x in f["refusals"]] == [
                rq.MALFORMED_SWEEP_RECORD]


def test_other_specs_refusals_do_not_name_this_source():
    h = _FullHist()
    h.sweep({"s1": D, "s2": W}, 10)
    h.sweep({"s2": D}, 20, write_sweep=False)
    rows = h.rows
    s1 = next(r["id"] for r in rows if r["subject"] == "s1")
    d = _by_event(rows)[s1]
    assert d["outcome"] == rf.ROUTED
    assert all(f["refusals"] == [] for f in d["families"])


def test_ambiguous_dispatch_fails_closed_without_choosing():
    rows = _hist(({"s1": D}, 10)).rows
    fake = (rf.Family("a", rq.SCHEMA, rq.derive),
            rf.Family("b", uq.SCHEMA, rq.derive))
    [d] = rf.dispatch(rows, _spec_sources(rows), families=fake)
    assert d["outcome"] == rf.AMBIGUOUS and d["family"] is None
    assert [f["accepted"] for f in d["families"]] == [True, True]


def test_malformed_source_row_is_refused_not_routed():
    h = _hist(({"s1": W}, 10))
    h._add(ho.KIND_SPEC, "s1", "{not json")
    rows = h.rows
    d = _by_event(rows)[rows[-1]["id"]]
    assert d["outcome"] == rf.NOT_ROUTED
    assert d["families"][0]["refusals"] == [
        {"spec_id": "s1", "reason": rq.MALFORMED_SPEC_RECORD,
         "event_id": None, "sweep_id": None}]


# ── determinism and disjointness ─────────────────────────────────────────
def test_dispatch_is_deterministic_and_row_order_independent():
    rows = _hist(({"s1": W, "s2": W}, 10), ({"s1": D, "s2": EF}, 20),
                 ({"s1": EF, "s2": CF}, 30), ({"s1": W, "s2": D}, 40)).rows
    src = _spec_sources(rows)
    a = rf.canonical(rf.dispatch(rows, src))
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    assert rf.canonical(rf.dispatch(shuffled, src)) == a
    assert rf.canonical(rf.dispatch(rows, src)) == a


def test_the_two_families_never_both_accept_one_source():
    rng = random.Random(3)
    for _ in range(40):
        seq = [{s: rng.choice([W, I, D, EF, CF]) for s in ("s1", "s2")}
               for _ in range(5)]
        rows = _hist(*[(v, 10 * (i + 1)) for i, v in enumerate(seq)]).rows
        for d in rf.dispatch(rows, _spec_sources(rows)):
            assert d["outcome"] != rf.AMBIGUOUS
            rf.check(d)


def test_check_rejects_a_tampered_record():
    rows = _hist(({"s1": D}, 10)).rows
    [d] = rf.dispatch(rows, _spec_sources(rows))
    for bad in (dict(d, outcome=rf.NOT_ROUTED), dict(d, family=None),
                dict(d, extra=1), dict(d, families=d["families"][:1])):
        with pytest.raises(rf.DispatchError):
            rf.check(json.loads(json.dumps(bad)))


def test_dispatch_module_imports_only_the_family_derivations():
    tree = ast.parse((ROOT / "trader/cognition/research_families.py")
                     .read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module)
    assert mods == {"__future__", "json", "dataclasses", "trader.cognition"}
    text = (ROOT / "trader/cognition/research_families.py").read_text()
    assert "research_question as rq" in text
    assert "research_unreadable_question as uq" in text
