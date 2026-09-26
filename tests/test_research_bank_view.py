"""research-bank-view.v1: a read-only, context_only owner view of one filed
Research Bank object.

The view assembles existing verified records only — the bank object as
stored, its verified chain, separately linked next-question gap records,
the requested run's telemetry entries and context-only prior recall — binds
them by exact IDs and hashes, fails closed on any unverifiable source, and
writes nothing. Nothing live calls it.
"""
import ast
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_bank import _filed
from tests.test_strategy_decay_research_recall import (
    _episode, _qid, _set, _timeline)
from tests.test_strategy_decay_research_run import (
    _FORBIDDEN, _all_keys, _clock, _copy)
from trader.cognition import research_bank as rb
from trader.cognition import research_bank_view as bv
from trader.cognition import research_cost as rcost
from trader.cognition import research_next_question as nq
from trader.cognition import research_recall as rc
from trader.cognition import research_run as run_

NOT_GEN = {"status": "NOT_AVAILABLE", "reason": "no_next_question_generator"}


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _three(tmp_path):
    """Three s1 decay episodes, each run and banked; the third run (k3)
    files exactly one bank object whose question has two prior objects."""
    j = _timeline(tmp_path)
    _episode(j, start=200)
    run_.run(j, "k3", 9, clock=_clock())
    assert rb.record_from_journal(j, now_ms=10)["refusals"] == []
    q3 = _qid(j, ms=9)
    [row] = [r for r in j.research_bank_objects() if r["question_id"] == q3]
    return j, row


def _filed_nq(tmp_path):
    j, row = _three(tmp_path)
    res = nq.record_for_bank_object(j, row["bank_object_id"], now_ms=11)
    assert res["refusals"] == [] and len(res["inserted"]) == 4
    return j, row


def _view(j, row, n=5):
    return bv.build(j, bank_object_id=row["bank_object_id"],
                    max_prior_objects=n)


def _dump(j):
    tables = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: j.query(f"SELECT * FROM {t} ORDER BY rowid") for t in tables}


# ── 1 one bank object produces a complete verified view ─────────────────
def test_one_bank_object_produces_a_complete_view(tmp_path):
    j, row = _filed_nq(tmp_path)
    v = _view(j, row)
    assert set(v) == {"schema", "builder_id", "authority", "read_only",
                      "request", "bank_object",
                      "bank_object_canonical_sha256", "chain",
                      "linked_next_questions", "cost", "prior_research",
                      "semantics", "view_id", "view_sha256"}
    assert v["schema"] == "research-bank-view.v1"
    assert v["authority"] == "context_only" and v["read_only"] is True
    assert v["request"] == {"by": "bank_object_id",
                            "id": row["bank_object_id"],
                            "max_prior_objects": 5}
    [stored] = [b for b in rb.load(j, run_id=row["run_id"])
                if b["bank_object_id"] == row["bank_object_id"]]
    assert v["bank_object"] == stored
    assert set(v["chain"]) == {"links", "question", "plan", "result",
                               "evidence"}
    assert v["linked_next_questions"]["stored_count"] == 4
    assert v["cost"]["run_id"] == row["run_id"]
    assert v["prior_research"] == rc.recall(j, row["question_id"],
                                            max_objects=5)
    assert len(v["prior_research"]["prior"]) >= 2


def _single_run(tmp_path):
    """One run that filed exactly one bank object."""
    j, _ = _filed(tmp_path)
    [row] = j.research_bank_objects()
    return j, row


def test_run_id_request_resolves_its_one_bank_object(tmp_path):
    j, row = _single_run(tmp_path)
    by_run = bv.build(j, run_id=row["run_id"], max_prior_objects=5)
    by_bank = _view(j, row)
    assert by_run["request"] == {"by": "run_id", "id": row["run_id"],
                                 "max_prior_objects": 5}
    strip = ("request", "view_id", "view_sha256")
    assert {k: x for k, x in by_run.items() if k not in strip} == \
        {k: x for k, x in by_bank.items() if k not in strip}


def test_run_filing_several_bank_objects_is_refused(tmp_path):
    j = _timeline(tmp_path)                 # run k1 files s1 and s2 objects
    rid = run_.run_id("k1", 1)
    assert len(j.research_bank_objects(run_id=rid)) > 1
    with pytest.raises(bv.ResearchBankViewError,
                       match="run_bank_objects_not_exactly_one"):
        bv.build(j, run_id=rid, max_prior_objects=5)


@pytest.mark.parametrize("kw", [
    {}, {"bank_object_id": "x", "run_id": "y"}, {"bank_object_id": ""},
    {"bank_object_id": 3}, {"run_id": None, "bank_object_id": None}])
def test_request_must_name_exactly_one_identifier(tmp_path, kw):
    j, _ = _three(tmp_path)
    with pytest.raises(bv.ResearchBankViewError, match="request"):
        bv.build(j, max_prior_objects=5, **kw)


def test_unknown_identifier_is_refused(tmp_path):
    j, _ = _three(tmp_path)
    with pytest.raises(bv.ResearchBankViewError, match="source_missing"):
        bv.build(j, bank_object_id="0" * 64, max_prior_objects=5)
    with pytest.raises(bv.ResearchBankViewError, match="not_exactly_one:0"):
        bv.build(j, run_id="0" * 64, max_prior_objects=5)


# ── 2 exact Q/P/E/R/run IDs and hashes are preserved ────────────────────
def test_chain_ids_and_hashes_are_exact(tmp_path):
    j, row = _filed_nq(tmp_path)
    v = _view(j, row)
    links = v["chain"]["links"]
    assert links == v["bank_object"]["links"]
    stored = {
        "question": j.research_question_by_id(row["question_id"]),
        "plan": j.research_plan_by_id(row["plan_id"]),
        "evidence": j.research_evidence_by_id(row["evidence_id"]),
        "result": j.research_result_by_id(row["result_id"])}
    for step, srow in stored.items():
        key = f"{step}_id"
        assert links[step][key] == row[key] == srow[key]
        assert links[step]["canonical_sha256"] == \
            _sha(srow["canonical_json"])
    [rrow] = j.research_runs(run_id=row["run_id"])
    assert links["run"]["run_id"] == rrow["run_id"]
    assert links["run"]["canonical_sha256"] == rrow["canonical_sha256"]
    assert links["run"]["telemetry_sha256"] == rrow["telemetry_sha256"]
    for step in ("question", "plan", "result"):
        assert bv.canonical(v["chain"][step]) == \
            stored[step]["canonical_json"]
    assert v["chain"]["evidence"]["values"] == "NOT_ASSERTED"
    assert v["bank_object_canonical_sha256"] == row["canonical_sha256"]


# ── 3 the persisted bank next_questions field is shown exactly ──────────
def test_persisted_next_questions_field_is_unchanged(tmp_path):
    j, row = _filed_nq(tmp_path)
    v = _view(j, row)
    assert v["bank_object"]["next_questions"] == NOT_GEN
    assert bv.canonical(v["bank_object"]) == row["canonical_json"]
    assert j.research_bank_object(row["bank_object_id"]) == row


# ── 4 linked next questions are separate external context ───────────────
def test_linked_next_questions_are_separate(tmp_path):
    j, row = _filed_nq(tmp_path)
    v = _view(j, row)
    ln = v["linked_next_questions"]
    assert ln["relation"] == \
        "external_linked_context_not_part_of_bank_object"
    assert ln["bank_object_id"] == row["bank_object_id"]
    want = sorted(nq.load(j, bank_object_id=row["bank_object_id"]),
                  key=lambda r: (r["source"]["index"], r["next_question_id"]))
    assert [x["record"] for x in ln["records"]] == want
    for x in ln["records"]:
        assert x["canonical_sha256"] == _sha(nq.canonical(x["record"]))
        assert x["record"]["authority"] == "context_only"
    # nothing linked is merged into the bank object
    ids = {x["next_question_id"] for x in ln["records"]}
    assert not ids & set(re.findall(r"[0-9a-f]{64}",
                                    bv.canonical(v["bank_object"])))


def test_other_bank_objects_next_questions_are_not_linked(tmp_path):
    j, row = _filed_nq(tmp_path)
    other = [r for r in j.research_bank_objects()
             if r["bank_object_id"] != row["bank_object_id"]][0]
    assert nq.record_for_bank_object(j, other["bank_object_id"],
                                     now_ms=12)["refusals"] == []
    v = _view(j, row)
    assert {x["record"]["source"]["bank_object"]["bank_object_id"]
            for x in v["linked_next_questions"]["records"]} == \
        {row["bank_object_id"]}


# ── 5 partial next-question filing is never a completeness claim ────────
@pytest.mark.parametrize("keep", [0, 1, 3])
def test_partial_next_question_filing_asserts_no_completeness(tmp_path,
                                                              keep):
    j, row = _filed_nq(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM research_next_questions WHERE rowid NOT IN "
                  "(SELECT rowid FROM research_next_questions "
                  "ORDER BY rowid LIMIT ?)", (keep,))
    ln = _view(j, row)["linked_next_questions"]
    assert ln["stored_count"] == len(ln["records"]) == keep
    assert ln["completeness"] == "NOT_ASSERTED"
    assert ln["completeness_reason"] == \
        "next_question_rows_commit_individually"
    assert not {k for k in _all_keys(ln)
                if re.search(r"missing|expected|complete$|total", k)}


def test_view_before_any_next_question_filing(tmp_path):
    j, row = _three(tmp_path)
    ln = _view(j, row)["linked_next_questions"]
    assert ln["records"] == [] and ln["completeness"] == "NOT_ASSERTED"


# ── 6 cost is the requested run's telemetry entries only ────────────────
def test_cost_holds_requested_run_entries_only(tmp_path):
    j, row = _filed_nq(tmp_path)
    assert len(j.research_runs()) == 3
    cost = _view(j, row)["cost"]
    assert cost["scope"] == "requested_run_only"
    assert cost["family"] == "research-run-telemetry.v1"
    assert {e["source_id"] for e in cost["entries"]} == {row["run_id"]}
    ledger = rcost.from_sources(journal=j)
    [fam] = [f for f in ledger["families"]
             if f["family"] == rcost.RUN_FAMILY]
    assert cost["entries"] == [e for e in fam["entries"]
                               if e["source_id"] == row["run_id"]]
    assert len(cost["entries"]) == 4 * len(rcost.RUN_MEASURES)
    assert all(e["state"] in ("MEASURED", "NOT_MEASURED")
               for e in cost["entries"])
    assert "groups" not in cost


# ── 7 total research cost stays NOT_ESTABLISHED ─────────────────────────
def test_total_research_cost_is_not_established(tmp_path):
    j, row = _filed_nq(tmp_path)
    total = _view(j, row)["cost"]["total_research_cost"]
    assert total["state"] == "NOT_ESTABLISHED" and total["value"] is None
    assert total["reasons"] == list(rcost.TOTAL_REASONS) + [
        "view_scope_is_one_run"]
    assert not any(k in ("sum", "value") and x is not None
                   for k, x in _cost_scalars(_view(j, row)["cost"]))


def _cost_scalars(cost):
    for k, x in cost.items():
        if k != "entries" and not isinstance(x, (dict, list)):
            yield k, x


# ── 8 prior recall obeys an explicit max_prior_objects ──────────────────
def test_prior_recall_obeys_max_prior_objects(tmp_path):
    j, row = _filed_nq(tmp_path)
    full = _view(j, row, n=rc.MAX_OBJECTS)["prior_research"]
    one = _view(j, row, n=1)["prior_research"]
    assert len(full["prior"]) >= 2 and full["bound"]["more_known_before"] \
        is False
    assert full["bound"]["examined"] == len(full["prior"]) + len(
        full["excluded"])
    assert len(one["prior"]) == 1 and one["bound"]["more_known_before"] \
        is True
    assert one["prior"][0]["bank_object_id"] == \
        full["prior"][0]["bank_object_id"]
    assert one == rc.recall(j, row["question_id"], max_objects=1)
    assert one["authority"] == "context_only"


def test_max_prior_objects_is_required(tmp_path):
    j, row = _three(tmp_path)
    with pytest.raises(TypeError):
        bv.build(j, bank_object_id=row["bank_object_id"])


@pytest.mark.parametrize("n", [0, -1, rc.MAX_OBJECTS + 1, True, 2.0, "3",
                               None])
def test_max_prior_objects_must_be_a_small_positive_int(tmp_path, n):
    j, row = _three(tmp_path)
    with pytest.raises(bv.ResearchBankViewError, match="max_prior_objects"):
        _view(j, row, n=n)


# ── 9 a tampered linked source fails closed ─────────────────────────────
def _tamper_json(j, table, key, value):
    [r] = j.query(f"SELECT canonical_json FROM {table} WHERE {key}=?",
                  (value,))[:1]
    _set(j, table, "canonical_json",
         r["canonical_json"].replace('"', "'", 1), key, value)


@pytest.mark.parametrize("target", [
    "next_question", "result", "plan", "question", "bank", "telemetry",
    "run"])
def test_tampered_linked_source_fails_closed(tmp_path, target):
    j, row = _filed_nq(tmp_path)
    if target == "next_question":
        nid = j.research_next_questions(
            bank_object_id=row["bank_object_id"])[0]["next_question_id"]
        _tamper_json(j, "research_next_questions", "next_question_id", nid)
    elif target == "telemetry":
        _set(j, "research_runs", "telemetry_json", "{}", "run_id",
             row["run_id"])
    else:
        table, key = {"result": ("research_results", "result_id"),
                      "plan": ("research_plans", "plan_id"),
                      "question": ("research_questions", "question_id"),
                      "bank": ("research_bank_objects", "bank_object_id"),
                      "run": ("research_runs", "run_id")}[target]
        _tamper_json(j, table, key, row[key])
    with pytest.raises(bv.ResearchBankViewError, match="source_invalid"):
        _view(j, row)


def test_tampered_prior_object_fails_closed(tmp_path):
    j, row = _filed_nq(tmp_path)
    prior = _view(j, row)["prior_research"]["prior"][0]["bank_object_id"]
    _tamper_json(j, "research_bank_objects", "bank_object_id", prior)
    with pytest.raises(bv.ResearchBankViewError,
                       match="source_invalid:prior_research"):
        _view(j, row)


def test_deleted_run_telemetry_row_fails_closed(tmp_path):
    j, row = _filed_nq(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM research_runs WHERE run_id=?",
                  (row["run_id"],))
    with pytest.raises(bv.ResearchBankViewError, match="source_invalid"):
        _view(j, row)


# ── 10 deterministic JSON, hash and order ───────────────────────────────
def test_view_is_deterministic(tmp_path):
    j, row = _filed_nq(tmp_path)
    a, b = _view(j, row), _view(j, row)
    assert bv.canonical(a) == bv.canonical(b)
    assert a["view_sha256"] == bv.view_sha256(a)
    assert a["view_id"] == bv.view_id(a)
    assert json.loads(bv.canonical(a)) == a
    c = _copy(tmp_path, j, "copy.db")
    assert bv.canonical(_view(c, row)) == bv.canonical(a)


def test_next_question_order_does_not_depend_on_insertion(tmp_path):
    j, row = _filed_nq(tmp_path)
    a = _view(j, row)
    rows = j.research_next_questions(bank_object_id=row["bank_object_id"])
    with j._tx() as c:
        c.execute("DELETE FROM research_next_questions")
    for r in reversed(rows):
        assert j.record_research_next_question(
            {k: r[k] for k in j._NEXT_QUESTION_COLUMNS},
            recorded_at_ms=r["recorded_at_ms"]) == "inserted"
    assert bv.canonical(_view(j, row)) == bv.canonical(a)
    assert [x["record"]["source"]["index"]
            for x in a["linked_next_questions"]["records"]] == [0, 1, 2, 3]


def test_identity_binds_request_bound_and_sources(tmp_path):
    j, row = _filed_nq(tmp_path)
    a, b = _view(j, row, n=5), _view(j, row, n=4)
    assert a["view_id"] != b["view_id"]
    ident = bv._identity(a)
    assert ident["request"]["max_prior_objects"] == 5
    assert ident["links"] == a["bank_object"]["links"]
    assert ident["bank_object"]["canonical_sha256"] == row["canonical_sha256"]
    with j._tx() as c:
        c.execute("DELETE FROM research_next_questions WHERE rowid IN "
                  "(SELECT rowid FROM research_next_questions LIMIT 1)")
    assert _view(j, row)["view_id"] != a["view_id"]


# ── 11 zero writes to all stores ────────────────────────────────────────
def test_build_writes_nothing(tmp_path):
    j, row = _filed_nq(tmp_path)
    before, changes = _dump(j), j._conn().total_changes
    _view(j, row)
    assert _dump(j) == before
    j, row = _single_run(tmp_path / "single")
    before, changes = _dump(j), j._conn().total_changes
    bv.build(j, run_id=row["run_id"], max_prior_objects=1)
    assert _dump(j) == before
    assert j._conn().total_changes == changes


def _files(p):
    return {s: hashlib.sha256(Path(str(p) + s).read_bytes()).hexdigest()
            for s in ("", "-wal") if Path(str(p) + s).exists()}


def test_readonly_journal_and_cli_write_nothing(tmp_path, capsys):
    j, row = _filed_nq(tmp_path)
    before, files = _dump(j), _files(j.db_path)
    ro = bv.open_readonly(j.db_path)
    v = _view(ro, row)
    assert bv.canonical(v) == bv.canonical(_view(j, row))
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.log_brain_event("x", "y", {})
    assert bv.main(["--db", str(j.db_path), "--bank-object-id",
                    row["bank_object_id"], "--max-prior-objects", "5"]) == 0
    assert json.loads(capsys.readouterr().out) == v
    assert _dump(j) == before and _files(j.db_path) == files


def test_cli_requires_explicit_inputs_and_refuses_missing_db(tmp_path,
                                                             capsys):
    missing = tmp_path / "none.db"
    assert bv.main(["--db", str(missing), "--run-id", "r",
                    "--max-prior-objects", "1"]) == 2
    assert not missing.exists()
    assert "db_missing" in capsys.readouterr().err
    for argv in (["--run-id", "r", "--max-prior-objects", "1"],
                 ["--db", str(missing), "--max-prior-objects", "1"],
                 ["--db", str(missing), "--run-id", "r"]):
        with pytest.raises(SystemExit):
            bv.main(argv)


def test_cli_refusal_is_reported_not_raised(tmp_path, capsys):
    j = _timeline(tmp_path)
    assert bv.main(["--db", str(j.db_path), "--run-id", run_.run_id("k1", 1),
                    "--max-prior-objects", "1"]) == 2
    assert "run_bank_objects_not_exactly_one" in capsys.readouterr().err


# ── 12 no authority, live, priority or suppression semantics ────────────
def _own_keys(v):
    """Keys the view itself adds, excluding records copied verbatim from
    their own contracts (bank object, chain records, next-question records,
    ledger entries, recall output)."""
    out = set(v)
    out |= set(v["request"]) | set(v["chain"])
    out |= set(_all_keys(v["chain"]["links"])) | set(v["chain"]["evidence"])
    ln = v["linked_next_questions"]
    out |= set(ln) | {k for x in ln["records"] for k in x}
    out |= set(_all_keys({k: x for k, x in v["cost"].items()
                          if k != "entries"}))
    return out


def test_view_carries_no_evaluative_or_authority_fields(tmp_path):
    j, row = _filed_nq(tmp_path)
    v = _view(j, row)
    keys = _own_keys(v)
    assert not {k for k in keys if _FORBIDDEN.search(k)}
    assert not {k for k in keys if re.search(
        r"relevan|similar|novel|redundan|cooldown|suppress|repeat|success"
        r"|credib|trigger|enqueue|deploy|live|risk|execut", k)}
    assert v["authority"] == v["prior_research"]["authority"] == \
        "context_only"
    for phrase in ("no writes", "suppression", "no-repeat", "priority",
                   "total cost", "live authority"):
        assert phrase in v["semantics"]


def test_module_imports_only_contract_modules():
    src = Path(bv.__file__).read_text()
    mods, names = set(), set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "argparse", "hashlib", "json", "sqlite3",
                    "sys", "threading", "pathlib", "trader.cognition",
                    "trader.core.journal"}
    assert names == {"annotations", "Path", "research_bank",
                     "research_cost", "research_next_question",
                     "research_recall", "Journal"}


def test_nothing_calls_the_view():
    root = Path(bv.__file__).resolve().parents[1]
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_bank_view.py"
                     and "research_bank_view" in p.read_text(errors="ignore"))
    assert callers == []


# ── 13 existing bank/result/next-question/cost objects unchanged ────────
def test_existing_objects_and_ledger_are_unchanged(tmp_path):
    j, row = _filed_nq(tmp_path)
    banks, nqs = rb.load(j), nq.load(j)
    ledger = rcost.from_sources(journal=j)
    _view(j, row)
    assert rb.load(j) == banks and nq.load(j) == nqs
    after = rcost.from_sources(journal=j)
    assert after == ledger
    assert rcost.ledger_sha256(after) == rcost.ledger_sha256(ledger)
    assert set(after) == {"schema", "builder_id", "semantics",
                          "total_research_cost", "families"}


def test_run_entries_reads_only_the_requested_run(tmp_path):
    j, row = _filed_nq(tmp_path)
    seen = []
    real = j.research_runs

    def spy(run_id=None):
        seen.append(run_id)
        return real(run_id=run_id)
    j.research_runs = spy
    entries, refused = rcost.run_entries(j, row["run_id"])
    assert seen == [row["run_id"]] and refused == []
    assert {e["source_id"] for e in entries} == {row["run_id"]}
