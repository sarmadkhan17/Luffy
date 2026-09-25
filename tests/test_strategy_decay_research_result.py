"""research-result.v1: structural results for strategy-decay research evidence.

One fully verified research-evidence.v1 record yields one deterministic,
immutable result that is always INCONCLUSIVE (no registered falsifier
predicates): three hypotheses NOT_ASSESSED, temporary_regime_absence
UNAVAILABLE. It reads no evidence value, names no explanation true or
false, and nothing live, Attention, Risk or Execution calls it.
"""
import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_evidence import (
    D, EF, W, _decisions, _health_id, _journal as _plan_journal)
from tests.test_strategy_decay_research_plan import _NO_CONCLUSION
from tests.test_strategy_decay_research_question import _keys
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_result as rr
from trader.core.journal import Journal

def _journal(tmp_path, verdicts=(W, EF, D), name="j.db", decisions=True):
    j = _plan_journal(tmp_path, list(verdicts), name=name)
    if decisions:
        _decisions(j)
    re_.record_from_journal(j, now_ms=1)
    return j


def _stored(j):
    [r] = j.research_results()
    return r


def _row(j):
    r = _stored(j)
    return {k: r[k] for k in j._RESULT_COLUMNS}


def _evidence(j):
    [ev] = re_.load(j)
    return ev


def _store_text(j, text):
    rec = json.loads(text)
    with j._tx() as c:
        c.execute("UPDATE research_results SET canonical_json=?, "
                  "canonical_sha256=?, result_id=?",
                  (text, rr._sha(text), rec.get("result_id")))


def _reid(rec):
    rec["result_id"] = rr.result_id(rec)
    return rr.canonical(rec)


# ── 1 verified evidence -> exactly one deterministic INCONCLUSIVE result ─
def test_verified_evidence_yields_one_inconclusive_result(tmp_path):
    j = _journal(tmp_path)
    res = rr.record_from_journal(j, now_ms=10)
    assert len(res["inserted"]) == 1 and not res["refusals"]
    [rec] = rr.load(j)
    ev, [erow] = _evidence(j), j.research_evidence()
    assert rec["result_id"] == res["inserted"][0]
    assert rec["status"] == rr.INCONCLUSIVE == "INCONCLUSIVE"
    assert rec["status_reason"] == "no_registered_falsifier_predicates"
    assert rec["source_evidence"] == {
        "schema": re_.SCHEMA, "evidence_id": ev["evidence_id"],
        "evidence_kind": re_.EVIDENCE_KIND, "collector_id": re_.COLLECTOR_ID,
        "plan_id": erow["plan_id"], "question_id": erow["question_id"],
        "canonical_sha256": erow["canonical_sha256"]}
    assert rec["scope"] == ev["scope"] == {"kind": "strategy",
                                           "spec_id": "s1"}
    row = _stored(j)
    assert row["canonical_json"] == rr.canonical(rec)
    assert row["canonical_sha256"] == rr._sha(row["canonical_json"])
    assert row["evidence_sha256"] == erow["canonical_sha256"]
    assert row["status"] == "INCONCLUSIVE"


def test_every_evidence_record_gets_its_own_result(tmp_path):
    j = _journal(tmp_path, [W, D, D, W, D])
    res = rr.record_from_journal(j, 1)
    assert len(res["inserted"]) == 2 == len(j.research_evidence())
    assert [r["evidence_id"] for r in j.research_results()] == [
        r["evidence_id"] for r in j.research_evidence()]


def test_result_id_and_serialization_are_deterministic(tmp_path):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    text = _stored(j)["canonical_json"]
    src, dst = (sqlite3.connect(tmp_path / "j.db"),
                sqlite3.connect(tmp_path / "b.db"))
    src.backup(dst)
    src.close(), dst.close()
    b = Journal(tmp_path / "b.db")
    with b._tx() as c:
        c.execute("DELETE FROM research_results")
    rr.record_from_journal(b, now_ms=99)
    assert _stored(b)["canonical_json"] == text
    assert rr.canonical(rr.build(_evidence(j))) == text


# ── 2 all four hypotheses with exact statuses ───────────────────────────
@pytest.mark.parametrize("verdicts", [(D,), (W, EF, D), (W, D, D, W, D)])
def test_all_four_hypotheses_with_exact_statuses(tmp_path, verdicts):
    j = _journal(tmp_path, verdicts)
    rr.record_from_journal(j, 1)
    for rec in rr.load(j):
        assert rec["hypotheses"] == [
            {"hypothesis": rp.GENUINE_DETERIORATION, "evidence_status": "ROUTED",
             "status": "NOT_ASSESSED",
             "reason": "no_registered_falsifier_predicates"},
            {"hypothesis": rp.INSUFFICIENT_RECENT_OPPORTUNITIES,
             "evidence_status": "ROUTED", "status": "NOT_ASSESSED",
             "reason": "no_registered_falsifier_predicates"},
            {"hypothesis": rp.DATA_FAILURE, "evidence_status": "ROUTED",
             "status": "NOT_ASSESSED",
             "reason": "no_registered_falsifier_predicates"},
            {"hypothesis": rp.TEMPORARY_REGIME_ABSENCE,
             "evidence_status": "UNAVAILABLE", "status": "UNAVAILABLE",
             "reason": "no_truthful_worldmodel_source"}]
        assert rec["unavailable_hypotheses"] == [
            {"hypothesis": "temporary_regime_absence",
             "reason": "no_truthful_worldmodel_source"}]


def test_limitations_are_carried_forward_verbatim(tmp_path):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    [rec], ev = rr.load(j), _evidence(j)
    [regime] = [s for s in ev["hypotheses"]
                if s["hypothesis"] == rp.TEMPORARY_REGIME_ABSENCE]
    [dec] = [it for s in ev["hypotheses"] for it in s["items"]
             if it["role"] == "strategy_signal_decisions"]
    assert dec["values"]["reported"]                 # fixture reports rows
    assert rec["limitations"] == [
        {"kind": "decision_selection_reports",
         "hypothesis": rp.INSUFFICIENT_RECENT_OPPORTUNITIES,
         "role": "strategy_signal_decisions",
         "reported": dec["values"]["reported"]},
        {"kind": "hypothesis_unavailable",
         "hypothesis": rp.TEMPORARY_REGIME_ABSENCE,
         "unavailable_reason": regime["unavailable_reason"],
         "unavailable_detail": regime["unavailable_detail"]}]
    assert regime["unavailable_detail"] == rp.REGIME_UNAVAILABLE_DETAIL


def test_empty_decision_reports_add_no_report_limitation(tmp_path):
    j = _journal(tmp_path, decisions=False)
    rr.record_from_journal(j, 1)
    [rec] = rr.load(j)
    assert rec["limitations"] == [
        {"kind": "hypothesis_unavailable",
         "hypothesis": rp.TEMPORARY_REGIME_ABSENCE,
         "unavailable_reason": rp.NO_TRUTHFUL_WORLDMODEL_SOURCE,
         "unavailable_detail": rp.REGIME_UNAVAILABLE_DETAIL}]


# ── 3 no SUPPORTED / REFUTED path exists ────────────────────────────────
def test_module_defines_no_support_or_refute_outcome():
    assert rr.STATUSES == ("INCONCLUSIVE",)
    assert rr.HYPOTHESIS_STATUSES == ("NOT_ASSESSED", "UNAVAILABLE")
    assert {x[2] for x in rr._LAYOUT} == set(rr.HYPOTHESIS_STATUSES)
    code = "\n".join(l for l in Path(rr.__file__).read_text().splitlines()
                     if not l.lstrip().startswith("#"))
    tree = ast.parse(code)
    strings = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and n.value not in (ast.get_docstring(tree),)}
    for s in strings:
        assert not re.search(r"SUPPORTED|REFUTED|CONFIRMED|REJECTED|"
                             r"ESTABLISHED", s), s


@pytest.mark.parametrize("path,value", [
    (("status",), "SUPPORTED"), (("status",), "REFUTED"),
    (("status",), "CONCLUSIVE"),
    (("hypotheses", 0, "status"), "SUPPORTED"),
    (("hypotheses", 2, "status"), "REFUTED"),
    (("hypotheses", 3, "status"), "NOT_ASSESSED"),
    (("hypotheses", 0, "reason"), "pooled_pf_below_floor"),
    (("status_reason",), "genuine_deterioration")])
def test_rehashed_outcome_substitution_is_rejected(tmp_path, path, value):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    cur = rec
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value
    with pytest.raises(rr.ResearchResultError):
        rr.from_json(_reid(rec), _evidence(j))
    _store_text(j, _reid(rec))
    with pytest.raises(rr.ResearchResultError):
        rr.load(j)


def test_evidence_with_other_section_layout_is_refused(tmp_path):
    j = _journal(tmp_path)
    ev = _evidence(j)
    swapped = json.loads(rr.canonical(ev))
    swapped["hypotheses"].reverse()
    routed_regime = json.loads(rr.canonical(ev))
    routed_regime["hypotheses"][3]["status"] = rp.ROUTED
    unavail_det = json.loads(rr.canonical(ev))
    unavail_det["hypotheses"][0]["status"] = rp.UNAVAILABLE
    other_reason = json.loads(rr.canonical(ev))
    other_reason["hypotheses"][3]["unavailable_reason"] = "regime_is_absent"
    for bad in (swapped, routed_regime, unavail_det, other_reason):
        with pytest.raises(rr.ResearchResultError):
            rr.build(bad)


# ── 4 retry / restart idempotent ────────────────────────────────────────
def test_retry_and_restart_are_idempotent(tmp_path):
    j = _journal(tmp_path, [W, D, D, W, D])
    first = rr.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2
    again = rr.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and again["duplicate"] == first["inserted"]
    j2 = Journal(tmp_path / "j.db")
    third = rr.record_from_journal(j2, now_ms=3)
    assert third["duplicate"] == first["inserted"]
    assert [r["result_id"] for r in rr.load(j2)] == first["inserted"]
    assert {r["recorded_at_ms"] for r in j2.research_results()} == {1}


# ── 5 conflict never overwrites ─────────────────────────────────────────
def test_conflicting_duplicate_is_refused_not_overwritten(tmp_path):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    before = _stored(j)
    row = _row(j)
    rec = json.loads(row["canonical_json"])
    rec["limitations"] = []
    for bad in (dict(row, canonical_json=rr.canonical(rec)),
                dict(row, result_id="0" * 64),       # same evidence, other id
                dict(row, status="SUPPORTED")):
        assert j.record_research_result(bad, recorded_at_ms=5) == "conflict"
    assert j.research_results() == [before]
    with pytest.raises(sqlite3.IntegrityError):      # schema: one per evidence
        with j._tx() as c:
            c.execute("INSERT INTO research_results (result_id,schema,"
                      "result_kind,resolver_id,evidence_id,evidence_sha256,"
                      "plan_id,scope_kind,scope_id,status,canonical_sha256,"
                      "canonical_json,recorded_at_ms) VALUES "
                      "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("1" * 64,) + tuple(row[k] for k in
                                          j._RESULT_COLUMNS[1:]) + (9,))
    assert j.research_results() == [before]


# ── 6 tampered / missing evidence fails closed ──────────────────────────
def test_tampered_evidence_is_refused_at_recording(tmp_path):
    j = _journal(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_evidence SET canonical_json="
                  "canonical_json||' '")
    res = rr.record_from_journal(j, 1)
    assert not res["inserted"] and len(res["refusals"]) == 1
    assert res["refusals"][0][1].startswith("source_evidence:")
    assert j.research_results() == []


def test_changed_live_source_is_refused_at_recording(tmp_path):
    j = _journal(tmp_path)
    j.update_decision_outcome("d-sel", True)
    res = rr.record_from_journal(j, 1)
    assert not res["inserted"]
    assert "source_changed" in res["refusals"][0][1]


def test_missing_plan_row_is_refused_at_recording(tmp_path):
    j = _journal(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM research_plans")
    res = rr.record_from_journal(j, 1)
    assert not res["inserted"] and "source_plan_missing" in res["refusals"][0][1]


@pytest.mark.parametrize("fault", ["evidence_deleted", "evidence_edited",
                                   "plan_deleted", "decision_changed",
                                   "health_edited"])
def test_load_fails_closed_when_source_evidence_breaks(tmp_path, fault):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    with j._tx() as c:
        if fault == "evidence_deleted":
            c.execute("DELETE FROM research_evidence")
        elif fault == "evidence_edited":
            c.execute("UPDATE research_evidence SET canonical_json="
                      "canonical_json||' '")
        elif fault == "plan_deleted":
            c.execute("DELETE FROM research_plans")
        elif fault == "health_edited":
            c.execute("UPDATE brain_events SET detail=detail||' ' WHERE id=?",
                      (_health_id(j, "source_observation"),))
    if fault == "decision_changed":
        j.update_decision_outcome("d-sel", True)
    with pytest.raises(rr.ResearchResultError):
        rr.load(j)


def test_deleted_sources_follow_the_evidence_contract(tmp_path):
    # the evidence contract tolerates deleted sources (reported absent);
    # the result, bound to the frozen record, still loads unchanged
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    before = _stored(j)["canonical_json"]
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?",
                  (_health_id(j, "prior_observation"),))
        c.execute("DELETE FROM decisions WHERE id='d-sel'")
    [rec] = rr.load(j)
    assert rr.canonical(rec) == before


# ── 7 result / evidence hashes verified ─────────────────────────────────
def test_load_rejects_tampered_stored_result(tmp_path):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    text = _stored(j)["canonical_json"]
    for bad in (text + " ", text.replace('"INCONCLUSIVE"', '"inconclusive"')):
        with j._tx() as c:
            c.execute("UPDATE research_results SET canonical_json=?", (bad,))
        with pytest.raises(rr.ResearchResultError):
            rr.load(j)


def test_result_id_and_evidence_binding_are_verified(tmp_path):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    ev = _evidence(j)
    rec = json.loads(_stored(j)["canonical_json"])
    bad_id = dict(rec, result_id="0" * 64)
    with pytest.raises(rr.ResearchResultError, match="result_id"):
        rr.from_json(rr.canonical(bad_id), ev)
    for field, value in (("canonical_sha256", "0" * 64),
                         ("evidence_id", "0" * 64)):
        bad = json.loads(rr.canonical(rec))
        bad["source_evidence"][field] = value
        with pytest.raises(rr.ResearchResultError,
                           match="source_evidence_mismatch"):
            rr.from_json(_reid(bad), ev)
    # a result bound to one evidence record never verifies against another
    other = json.loads(rr.canonical(ev))
    other["evidence_id"] = "f" * 64
    with pytest.raises(rr.ResearchResultError):
        rr.from_json(_stored(j)["canonical_json"], other)


def test_row_projection_is_verified(tmp_path):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    with j._tx() as c:
        c.execute("UPDATE research_results SET scope_id='s2'")
    with pytest.raises(rr.ResearchResultError, match="row_projection"):
        rr.load(j)


def test_evidence_row_pointing_elsewhere_fails_closed(tmp_path):
    j = _journal(tmp_path, [W, D, D, W, D])
    rr.record_from_journal(j, 1)
    a, b = j.research_results()
    with j._tx() as c:
        c.execute("UPDATE research_results SET plan_id=? WHERE result_id=?",
                  (b["plan_id"], a["result_id"]))
    with pytest.raises(rr.ResearchResultError):
        rr.load(j)


# ── 8 extra evaluative fields rejected ──────────────────────────────────
_ADDED = ("score", "conclusion", "supported", "refuted", "support",
          "contradiction", "asset", "symbol", "probability", "rank",
          "salience", "usefulness", "verdict", "confidence")


def _where(rec, level):
    return {"record": rec, "source_evidence": rec["source_evidence"],
            "scope": rec["scope"], "hypothesis": rec["hypotheses"][0],
            "unavailable": rec["unavailable_hypotheses"][0],
            "limitation": rec["limitations"][0]}[level]


@pytest.mark.parametrize("level", ["record", "source_evidence", "scope",
                                   "hypothesis", "unavailable", "limitation"])
@pytest.mark.parametrize("key", _ADDED)
def test_added_evaluative_fields_are_rejected(tmp_path, level, key):
    j = _journal(tmp_path, name=f"{level}-{key}.db")
    rr.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    _where(rec, level)[key] = 1
    with pytest.raises(rr.ResearchResultError):
        rr.from_json(_reid(rec), _evidence(j))


@pytest.mark.parametrize("edit", ["type_list", "type_str", "bad_kind",
                                  "limitation_dropped", "limitation_reworded",
                                  "status_int"])
def test_exact_nested_schema_and_types(tmp_path, edit):
    j = _journal(tmp_path, name=f"{edit}.db")
    rr.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    if edit == "type_list":
        rec["hypotheses"] = {"0": rec["hypotheses"][0]}
    elif edit == "type_str":
        rec["limitations"] = "none"
    elif edit == "bad_kind":
        rec["limitations"][0]["kind"] = "evidence_weak"
    elif edit == "limitation_dropped":
        rec["limitations"].pop()
    elif edit == "limitation_reworded":
        rec["limitations"][-1]["unavailable_detail"] = "regime unknown"
    elif edit == "status_int":
        rec["hypotheses"][0]["status"] = 0
    with pytest.raises(rr.ResearchResultError):
        rr.from_json(_reid(rec), _evidence(j))


def test_record_carries_no_conclusion_or_asset_fields(tmp_path):
    j = _journal(tmp_path)
    rr.record_from_journal(j, 1)
    [rec] = rr.load(j)
    shell = json.loads(rr.canonical(rec))
    for lim in shell["limitations"]:
        lim.pop("reported", None)       # the evidence's own reports, verbatim
    assert not (set(_keys(shell)) & (_NO_CONCLUSION | set(_ADDED)))
    assert set(rec) == set(rr._RECORD_KEYS)


# ── 9 deterministic under reload ────────────────────────────────────────
def test_deterministic_under_reload(tmp_path):
    j = _journal(tmp_path, [W, D, D, W, D])
    rr.record_from_journal(j, 1)
    first = [rr.canonical(r) for r in rr.load(j)]
    second = [rr.canonical(r) for r in rr.load(Journal(tmp_path / "j.db"))]
    assert first == second == [r["canonical_json"]
                               for r in j.research_results()]
    assert [rr.canonical(rr.build(e)) for e in re_.load(j)] == first
    one = j.research_results()[1]["evidence_id"]
    assert [r["source_evidence"]["evidence_id"]
            for r in rr.load(j, evidence_id=one)] == [one]


# ── legacy journals ─────────────────────────────────────────────────────
def test_legacy_journal_without_result_table_remains_readable(tmp_path):
    j = _journal(tmp_path, name="legacy.db")
    with j._tx() as c:
        c.execute("DROP TABLE research_results")
    j2 = Journal(tmp_path / "legacy.db")
    assert j2.research_results() == [] and rr.load(j2) == []
    assert len(re_.load(j2)) == 1
    assert len(rr.record_from_journal(j2, 1)["inserted"]) == 1


def test_journal_without_evidence_records_nothing(tmp_path):
    j = Journal(tmp_path / "e.db")
    assert rr.record_from_journal(j, 1) == {"inserted": [], "duplicate": [],
                                            "conflict": [], "refusals": []}


# ── 10 no live-loop / Risk / Execution / Attention wiring ───────────────
def test_module_imports_only_contract_modules():
    src = Path(rr.__file__).read_text()
    mods, names = set(), set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "hashlib", "json", "trader.cognition"}
    assert names == {"annotations", "research_evidence"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(rr.__file__).resolve().parents[1]
    pat = re.compile(r"research_result\b|record_research_result")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_result.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive; research_run.py is the
    # offline runner, guarded by its own test
    assert callers == ["cognition/research_run.py", "core/journal.py"]
    assert "research_result import" not in (root / "core/journal.py").read_text()


def test_recording_is_read_only_on_evidence(tmp_path):
    j = _journal(tmp_path)
    tables = ("brain_events", "research_questions", "research_plans",
              "research_evidence", "decisions", "trades")
    before = {t: j.query(f"SELECT * FROM {t}") for t in tables}
    rr.record_from_journal(j, 1)
    rr.load(j)
    assert {t: j.query(f"SELECT * FROM {t}") for t in tables} == before
