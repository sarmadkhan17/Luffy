"""research-bank-object.v1: one immutable Research Bank object per verified
result a completed research-run.v1 reached.

The bank object binds the exact question/plan/evidence/result/run IDs and
canonical hashes, copies their fields verbatim, marks what no contract
provides as NOT_AVAILABLE / NOT_CLASSIFIED with fixed structural reasons,
and copies the run telemetry exactly. It adds no research semantics and
nothing live, Attention, Risk or Execution calls it.
"""
import ast
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_evidence import D, W
from tests.test_strategy_decay_research_run import (
    _clock, _copy, _fresh, _FORBIDDEN, _all_keys)
from trader.cognition import research_bank as rb
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_result as rr
from trader.cognition import research_run as run_
from trader.core.journal import Journal

CHAIN_TABLES = ("research_questions", "research_plans", "research_evidence",
                "research_results", "research_runs")


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _ran(tmp_path, verdicts=(W, D), key="k", ms=1, **kw):
    j = _fresh(tmp_path, verdicts=verdicts)
    run_.run(j, key, ms, clock=_clock(), **kw)
    return j


def _filed(tmp_path, **kw):
    j = _ran(tmp_path, **kw)
    res = rb.record_from_journal(j, now_ms=2)
    assert res["refusals"] == []
    return j, res


def _bank_row(j):
    [r] = j.research_bank_objects()
    return r


def _set_bank(j, **cols):
    sets = ",".join(f"{k}=?" for k in cols)
    with j._tx() as c:
        c.execute(f"UPDATE research_bank_objects SET {sets}",
                  tuple(cols.values()))


def _restore(j, rec):
    """Store a (mutated) record with a recomputed id and matching row."""
    rec["bank_object_id"] = rb.bank_object_id(rec)
    row = rb.row_for(rec)
    _set_bank(j, **row)


# ── 1 completed verified run -> one deterministic bank object ───────────
def test_completed_run_files_one_deterministic_bank_object(tmp_path):
    j = _ran(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    res = rb.record_from_journal(j, now_ms=2)
    assert len(res["inserted"]) == 1 and not res["duplicate"]
    [obj] = rb.load(j)
    assert obj["bank_object_id"] == res["inserted"][0]
    assert _bank_row(j)["canonical_json"] == rb.canonical(obj)
    assert _bank_row(j)["recorded_at_ms"] == 2
    # the same persisted chain filed at another time: byte-identical object
    assert rb.record_from_journal(ref, now_ms=99)["inserted"] == \
        res["inserted"]
    assert _bank_row(ref)["canonical_json"] == _bank_row(j)["canonical_json"]
    [stored] = run_.load(j)
    chain = rb._chain(j, stored["receipt"]["run_id"],
                      obj["links"]["result"]["result_id"])
    assert rb.canonical(rb.build(chain)) == _bank_row(j)["canonical_json"]


def test_each_reached_result_gets_its_own_object(tmp_path):
    j = _ran(tmp_path, verdicts=(W, D, D, W, D))
    res = rb.record_from_journal(j, now_ms=2)
    results = [r["result_id"] for r in j.research_results()]
    assert len(results) == 2 and len(res["inserted"]) == 2
    objs = rb.load(j)
    assert [o["links"]["result"]["result_id"] for o in objs] == results
    assert len({o["links"]["question"]["question_id"] for o in objs}) == 2


def test_a_second_run_reaching_the_same_result_is_filed_separately(tmp_path):
    j = _ran(tmp_path)
    run_.run(j, "k2", 5, clock=_clock())
    res = rb.record_from_journal(j, now_ms=6)
    assert len(res["inserted"]) == 2
    a, b = rb.load(j)
    assert a["links"]["result"] == b["links"]["result"]
    assert a["links"]["run"]["run_id"] != b["links"]["run"]["run_id"]
    assert [s["chain_record_outcome"] for s in b["experiments"]["steps"]] \
        == ["duplicate"] * 4


# ── 2 every linked Q/P/E/R/run ID and hash is exact ─────────────────────
def test_links_bind_every_record_id_and_canonical_hash(tmp_path):
    j, _ = _filed(tmp_path)
    [obj] = rb.load(j)
    [q], [p], [e], [r], [run] = (j.query(f"SELECT * FROM {t}")
                                 for t in CHAIN_TABLES)
    lk = obj["links"]
    assert lk["question"] == {"schema": rq.SCHEMA,
                              "question_id": q["question_id"],
                              "canonical_sha256": _sha(q["canonical_json"])}
    assert lk["plan"] == {"schema": rp.SCHEMA, "plan_id": p["plan_id"],
                          "canonical_sha256": _sha(p["canonical_json"])}
    assert lk["evidence"] == {"schema": re_.SCHEMA,
                              "evidence_id": e["evidence_id"],
                              "canonical_sha256": e["canonical_sha256"]}
    assert lk["result"] == {"schema": rr.SCHEMA, "result_id": r["result_id"],
                            "canonical_sha256": r["canonical_sha256"]}
    assert lk["run"] == {"schema": run_.SCHEMA, "run_id": run["run_id"],
                         "canonical_sha256": run["canonical_sha256"],
                         "telemetry_schema": run_.TELEMETRY_SCHEMA,
                         "telemetry_sha256": run["telemetry_sha256"]}
    # each predecessor hash is the one its successor itself binds
    assert p["question_sha256"] == lk["question"]["canonical_sha256"]
    assert e["plan_sha256"] == lk["plan"]["canonical_sha256"]
    assert r["evidence_sha256"] == lk["evidence"]["canonical_sha256"]
    row = _bank_row(j)
    assert (row["run_id"], row["result_id"], row["evidence_id"],
            row["plan_id"], row["question_id"]) == (
        run["run_id"], r["result_id"], e["evidence_id"], p["plan_id"],
        q["question_id"])
    assert obj["bank_object_id"] == _sha(rb.canonical({
        "schema": rb.SCHEMA, "bank_kind": rb.BANK_KIND,
        "builder_id": rb.BUILDER_ID, "run_id": run["run_id"],
        "run_canonical_sha256": run["canonical_sha256"],
        "result_id": r["result_id"],
        "result_canonical_sha256": r["canonical_sha256"]}))


def test_fields_are_verbatim_copies_of_the_chain(tmp_path):
    j, _ = _filed(tmp_path)
    [obj] = rb.load(j)
    [q] = rq.load(j)
    [e] = re_.load(j)
    [r] = rr.load(j)
    assert obj["question"] == {k: q[k] for k in
                               ("question_id", "trigger", "scope", "question")}
    assert obj["scope"] == q["scope"] == r["scope"]
    assert obj["sources"] == [
        {k: it[k] for k in ("hypothesis", "role", "source", "locator",
                            "plan_binding", "source_identity",
                            "source_sha256", "content_sha256")}
        for s in e["hypotheses"] for it in s["items"]]
    assert all("values" not in s and "source_content" not in s
               for s in obj["sources"])
    assert (obj["result_status"], obj["result_reason"]) == (
        r["status"], r["status_reason"]) == (
        "INCONCLUSIVE", "no_registered_falsifier_predicates")
    assert [x["entry"] for x in obj["limitations"]
            if x["origin_schema"] == rr.SCHEMA] == r["limitations"]
    assert all(x["origin_id"] == r["result_id"]
               and x["origin_field"] == "limitations"
               for x in obj["limitations"] if x["origin_schema"] == rr.SCHEMA)


def _run_with(j, monkeypatch, mod, patch):
    """Run once with ``mod.record_from_journal``'s return edited by
    ``patch(journal, out)``; the store holds what the real step wrote."""
    orig = mod.record_from_journal

    def patched(journal, now_ms):
        out = orig(journal, now_ms=now_ms)
        patch(journal, out)
        return out
    monkeypatch.setattr(mod, "record_from_journal", patched)
    out = run_.run(j, "k", 1)
    monkeypatch.undo()
    return out


def test_only_exactly_attributed_run_refusals_are_copied(
        tmp_path, monkeypatch):
    j = _fresh(tmp_path)

    def refusals(journal, out):
        [q] = rq.load(journal)
        src = q["source"]
        out["refusals"] = [
            # this question's own source observation: attributed
            rq.Refusal("s1", "ambiguous_chronology", src["event_id"],
                       src["sweep_id"]),
            # same spec, another episode: never attributed
            rq.Refusal("s1", "source_fingerprint_missing",
                       src["event_id"] + 4, "sw040"),
            rq.Refusal("s1", "observation_missing", src["event_id"], "swX"),
            rq.Refusal("s1", "malformed_spec_record"),
            rq.Refusal("other", "observation_missing", src["event_id"],
                       src["sweep_id"]),
            rq.Refusal(None, "malformed_sweep_record")]
    _run_with(j, monkeypatch, rq, refusals)
    assert rb.record_from_journal(j, now_ms=2)["refusals"] == []
    [obj] = rb.load(j)
    [receipt] = [x["receipt"] for x in run_.load(j)]
    run_lims = [x for x in obj["limitations"]
                if x["origin_schema"] == run_.SCHEMA]
    assert run_lims == [{"origin_schema": run_.SCHEMA,
                         "origin_id": receipt["run_id"],
                         "origin_field": "steps.question.outcomes.refusals",
                         "entry": receipt["steps"][0]["outcomes"]
                         ["refusals"][0]}]


def test_same_spec_later_episode_refusal_is_not_a_limitation(
        tmp_path, monkeypatch):
    """Regression: a valid question and a later same-spec refusal of a
    different episode (source_fingerprint_missing at a later event and
    sweep) — the earlier chain's object must not copy the refusal."""
    j = _fresh(tmp_path)

    def later(journal, out):
        [q] = rq.load(journal)
        out["refusals"] = [rq.Refusal(
            q["scope"]["spec_id"], "source_fingerprint_missing",
            q["source"]["event_id"] + 4, "sw040")]
    _run_with(j, monkeypatch, rq, later)
    rb.record_from_journal(j, now_ms=2)
    [obj] = rb.load(j)
    assert [x["origin_schema"] for x in obj["limitations"]] == [
        rr.SCHEMA] * len(rr.load(j)[0]["limitations"])


@pytest.mark.parametrize("mod,step", [(rp, "plan"), (re_, "evidence")])
def test_downstream_refusals_match_by_exact_id(tmp_path, monkeypatch,
                                               mod, step):
    j = _fresh(tmp_path)
    src_key = {"plan": "question_id", "evidence": "plan_id"}[step]

    def refusals(journal, out):
        mine = {"plan": lambda: rq.load(journal)[0]["question_id"],
                "evidence": lambda: rp.load(journal)[0]["plan_id"]}[step]()
        out["refusals"] = [(mine, "kept verbatim"), ("f" * 64, "other")]
    _run_with(j, monkeypatch, mod, refusals)
    rb.record_from_journal(j, now_ms=2)
    [obj] = rb.load(j)
    run_lims = [x for x in obj["limitations"]
                if x["origin_schema"] == run_.SCHEMA]
    assert [x["entry"]["reason"] for x in run_lims] == ["kept verbatim"]
    assert run_lims[0]["origin_field"] == f"steps.{step}.outcomes.refusals"
    assert run_lims[0]["entry"][src_key] != "f" * 64


# ── run membership: exactly once, never in conflict ─────────────────────
_STEP_MOD = {"question": rq, "plan": rp, "evidence": re_, "result": rr}


@pytest.mark.parametrize("step", list(_STEP_MOD))
@pytest.mark.parametrize("edit,code", [
    (lambda o: o.update(inserted=o["inserted"] * 2),
     "chain_record_repeated_in_run"),
    (lambda o: o.update(duplicate=list(o["inserted"])),
     "chain_record_repeated_in_run"),
    (lambda o: o.update(conflict=list(o["inserted"])),
     "chain_record_conflict_in_run"),
])
def test_repeated_or_conflicting_run_membership_is_refused(
        tmp_path, monkeypatch, step, edit, code):
    j = _fresh(tmp_path)
    out = _run_with(j, monkeypatch, _STEP_MOD[step], lambda _j, o: edit(o))
    assert len(run_.load(j)) == 1           # the run contract accepts it
    res = rb.record_from_journal(j, now_ms=2)
    assert not (res["inserted"] or res["duplicate"] or res["conflict"])
    assert (out["run_id"], rr.load(j)[0]["result_id"],
            f"{code}:{step}") in res["refusals"]
    assert j.research_bank_objects() == []
    if step == "result" and code.endswith("conflict_in_run"):
        assert (out["run_id"], rr.load(j)[0]["result_id"],
                rb.RESULT_CONFLICT_IN_RUN) in res["refusals"]
    # a repeated result ID is still refused once, never filed twice
    assert len([r for r in res["refusals"] if r[2].startswith(code)]) == 1


@pytest.mark.parametrize("edit,code", [
    (lambda o: o.update(inserted=o["inserted"] * 2),
     "chain_record_repeated_in_run:plan"),
    (lambda o: o.update(conflict=list(o["inserted"])),
     "chain_record_conflict_in_run:plan"),
])
def test_load_refuses_an_object_whose_run_membership_is_not_exact(
        tmp_path, monkeypatch, edit, code):
    j = _fresh(tmp_path)
    _run_with(j, monkeypatch, rp, lambda _j, o: edit(o))
    # an object filed by a lenient builder must still fail load
    monkeypatch.setattr(rb, "run_membership", lambda s, cid: "inserted")
    assert len(rb.record_from_journal(j, now_ms=2)["inserted"]) == 1
    monkeypatch.undo()
    with pytest.raises(rb.ResearchBankError, match=re.escape(code)):
        rb.load(j)


# ── 3 chain mismatch / tampering / deletion fails closed ────────────────
@pytest.mark.parametrize("table,code", [
    ("research_questions", "source_invalid:run:reference_missing:question"),
    ("research_plans", "source_invalid:run:reference_missing:plan"),
    ("research_evidence", "source_invalid:run:reference_missing:evidence"),
    ("research_results", "source_invalid:run:reference_missing:result"),
    ("research_runs", "source_missing:run")])
def test_deleted_link_fails_closed(tmp_path, table, code):
    j, _ = _filed(tmp_path)
    with j._tx() as c:
        c.execute(f"DELETE FROM {table}")
    with pytest.raises(rb.ResearchBankError, match=re.escape(code)):
        rb.load(j)


@pytest.mark.parametrize("table", CHAIN_TABLES[:4])
def test_tampered_link_fails_closed(tmp_path, table):
    j, _ = _filed(tmp_path)
    with j._tx() as c:
        c.execute(f"UPDATE {table} SET canonical_json="
                  "replace(canonical_json, 's1', 's9')")
    with pytest.raises(rb.ResearchBankError, match="source_invalid"):
        rb.load(j)


def test_deleted_health_source_fails_closed_per_source_contract(tmp_path):
    j, _ = _filed(tmp_path)
    [q] = rq.load(j)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?",
                  (q["source"]["event_id"],))
    with pytest.raises(rb.ResearchBankError, match="source_invalid"):
        rb.load(j)


def test_tampered_telemetry_fails_closed(tmp_path):
    j, _ = _filed(tmp_path)
    [run] = j.research_runs()
    tel = json.loads(run["telemetry_json"])
    tel["steps"][0]["elapsed_wall_ns"]["value"] += 1
    text = rb.canonical(tel)
    with j._tx() as c:                  # consistent digest: run load passes
        c.execute("UPDATE research_runs SET telemetry_json=?, "
                  "telemetry_sha256=?", (text, _sha(text)))
    assert len(run_.load(j)) == 1
    with pytest.raises(rb.ResearchBankError, match="rebuild_mismatch"):
        rb.load(j)


@pytest.mark.parametrize("path,value", [
    (("links", "question", "canonical_sha256"), "0" * 64),
    (("links", "plan", "plan_id"), "x"),
    (("links", "evidence", "canonical_sha256"), "0" * 64),
    (("links", "run", "telemetry_sha256"), "0" * 64),
    (("sources", 0, "source_sha256"), "0" * 64),
    (("question", "trigger"), "first_truthful_decayed"),
    (("experiments", "steps", 1, "chain_record_outcome"), "duplicate"),
    (("limitations", 0, "entry", "role"), "other"),
])
def test_forged_object_fails_rebuild(tmp_path, path, value):
    j, _ = _filed(tmp_path)
    rec = json.loads(_bank_row(j)["canonical_json"])
    cur = rec
    for p in path[:-1]:
        cur = cur[p]
    assert cur[path[-1]] != value
    cur[path[-1]] = value
    _restore(j, rec)
    with pytest.raises(rb.ResearchBankError,
                       match="rebuild_mismatch|source_missing"):
        rb.load(j)


def test_chain_mismatch_between_records_is_refused(tmp_path):
    j, _ = _filed(tmp_path)
    [stored] = run_.load(j)
    [xid] = [r["result_id"] for r in j.research_results()]
    chain = rb._chain(j, stored["receipt"]["run_id"], xid)
    bad = json.loads(json.dumps(chain))
    bad["plan"]["source_question"]["canonical_sha256"] = "0" * 64
    with pytest.raises(rb.ResearchBankError,
                       match=r"chain_mismatch:plan->question"):
        rb.build(bad)
    bad = json.loads(json.dumps(chain))
    bad["result"]["source_evidence"]["canonical_sha256"] = "0" * 64
    with pytest.raises(rb.ResearchBankError,
                       match=r"chain_mismatch:result->evidence"):
        rb.build(bad)
    bad = json.loads(json.dumps(chain))
    bad["receipt"]["steps"][2]["outcomes"]["inserted"] = []
    with pytest.raises(rb.ResearchBankError,
                       match="chain_not_in_run:evidence"):
        rb.build(bad)


def test_failed_run_is_refused_and_files_nothing(tmp_path, monkeypatch):
    j = _fresh(tmp_path)

    def boom(journal, now_ms):
        raise RuntimeError("collector unavailable")
    monkeypatch.setattr(re_, "record_from_journal", boom)
    out = run_.run(j, "k", 1)
    monkeypatch.undo()
    res = rb.record_from_journal(j, now_ms=2)
    assert res == {"inserted": [], "duplicate": [], "conflict": [],
                   "refusals": [(out["run_id"], None,
                                 rb.RUN_NOT_COMPLETED)]}
    assert j.research_bank_objects() == []


def test_run_reaching_no_result_is_refused(tmp_path):
    j = Journal(tmp_path / "e.db")
    out = run_.run(j, "k", 1)
    res = rb.record_from_journal(j, now_ms=2)
    assert res["refusals"] == [(out["run_id"], None,
                                rb.RUN_REACHED_NO_RESULT)]
    assert j.research_bank_objects() == []


def test_invalid_run_is_refused_not_filed(tmp_path):
    j = _ran(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM research_results")
    res = rb.record_from_journal(j, now_ms=2)
    [(rid, xid, why)] = res["refusals"]
    assert xid is None and why.startswith("source_invalid:run:")
    assert j.research_bank_objects() == []


# ── 4 NOT_AVAILABLE / NOT_CLASSIFIED use exact structural reasons ───────
def test_unavailable_and_unclassified_fields_are_exact(tmp_path):
    j, _ = _filed(tmp_path)
    [obj] = rb.load(j)
    assert obj["extracted_claims"] == {
        "status": "NOT_AVAILABLE", "reason": "no_claim_extraction_contract"}
    assert obj["supporting_evidence"] == obj["contradictory_evidence"] == {
        "status": "NOT_CLASSIFIED",
        "reason": "no_registered_falsifier_predicates"}
    assert obj["next_questions"] == {
        "status": "NOT_AVAILABLE", "reason": "no_next_question_generator"}
    assert obj["experiments"]["kind"] == "structural_run_step_record"


@pytest.mark.parametrize("field,value", [
    ("extracted_claims", {"status": "NOT_AVAILABLE", "reason": "x"}),
    ("supporting_evidence", {"status": "SUPPORTED",
                             "reason": "no_registered_falsifier_predicates"}),
    ("contradictory_evidence", {"status": "NOT_CLASSIFIED", "reason": "x"}),
    ("next_questions", {"status": "NOT_AVAILABLE",
                        "reason": "no_next_question_generator", "items": []}),
])
def test_forged_unavailable_fields_fail_closed(tmp_path, field, value):
    j, _ = _filed(tmp_path)
    rec = json.loads(_bank_row(j)["canonical_json"])
    rec[field] = value
    _restore(j, rec)
    with pytest.raises(rb.ResearchBankError, match=field):
        rb.load(j)


def test_result_reason_is_structural_not_a_conclusion():
    src = Path(rb.__file__).read_text()
    assert not re.search(r"\bSUPPORTED\b|\bREFUTED\b|\bDECAYED\b", src)
    assert "structural reason" in src
    assert rb.NO_REGISTERED_FALSIFIER_PREDICATES \
        == rr.NO_REGISTERED_FALSIFIER_PREDICATES


# ── 5 run telemetry copied exactly ──────────────────────────────────────
def test_cost_is_the_run_telemetry_verbatim(tmp_path):
    j, _ = _filed(tmp_path)
    [obj] = rb.load(j)
    [stored] = run_.load(j)
    tel = stored["telemetry"]
    assert obj["cost"] == {"scope": "research_run",
                           "telemetry_schema": tel["schema"],
                           "run_id": tel["run_id"],
                           "telemetry_sha256": _sha(rb.canonical(tel)),
                           "telemetry_semantics": tel["semantics"],
                           "steps": tel["steps"]}
    assert [t["elapsed_wall_ns"]["value"] for t in obj["cost"]["steps"]] \
        == [7, 7, 7, 7]


def test_not_measured_telemetry_keeps_its_exact_reason(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    orig = rp.record_from_journal

    def peeking(journal, now_ms):
        journal._conn().execute("SELECT 1").fetchall()
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(rp, "record_from_journal", peeking)
    run_.run(j, "k", 1)
    monkeypatch.undo()
    rb.record_from_journal(j, now_ms=2)
    [obj] = rb.load(j)
    assert obj["cost"]["steps"][1]["rows_read"] == {
        "status": "NOT_MEASURED", "value": None, "unit": "rows",
        "source": None,
        "reason": "unmetered_journal_access:direct_connection_access"}
    assert obj["cost"]["steps"] == run_.load(j)[0]["telemetry"]["steps"]


def test_cost_carries_no_budget_or_cap_interpretation(tmp_path):
    j, _ = _filed(tmp_path)
    [obj] = rb.load(j)
    keys = set(_all_keys(obj["cost"]))
    assert keys == {"scope", "telemetry_schema", "run_id",
                    "telemetry_sha256", "telemetry_semantics", "steps",
                    "step", "elapsed_wall_ns", "rows_read", "status",
                    "value", "unit", "source", "reason"}


# ── 6 retry / restart idempotent ────────────────────────────────────────
def test_retry_and_restart_are_idempotent(tmp_path):
    j, first = _filed(tmp_path)
    before = j.research_bank_objects()
    again = rb.record_from_journal(j, now_ms=50)
    assert again == {"inserted": [], "duplicate": first["inserted"],
                     "conflict": [], "refusals": []}
    restarted = Journal(j.db_path)
    assert rb.record_from_journal(restarted, now_ms=60)["duplicate"] == \
        first["inserted"]
    assert restarted.research_bank_objects() == before
    assert rb.load(restarted) == rb.load(j)


# ── 7 conflict never overwrites ─────────────────────────────────────────
def test_conflict_never_overwrites(tmp_path):
    j, _ = _filed(tmp_path)
    good = _bank_row(j)
    forged = dict(good, canonical_json=good["canonical_json"].replace(
        "no_claim_extraction_contract", "x"))
    forged["canonical_sha256"] = _sha(forged["canonical_json"])
    cols = {k: forged[k] for k in Journal._BANK_COLUMNS}
    assert j.record_research_bank_object(cols, recorded_at_ms=9) == \
        "conflict"
    other_id = dict(cols, bank_object_id="f" * 64)   # same run+result
    assert j.record_research_bank_object(other_id, recorded_at_ms=9) == \
        "conflict"
    assert j.research_bank_objects() == [good]


def test_stored_conflict_is_reported_not_overwritten(tmp_path):
    j, first = _filed(tmp_path)
    rec = json.loads(_bank_row(j)["canonical_json"])
    rec["next_questions"] = {"status": "NOT_AVAILABLE", "reason": "x"}
    text = rb.canonical(rec)
    _set_bank(j, canonical_json=text, canonical_sha256=_sha(text))
    tampered = j.research_bank_objects()
    res = rb.record_from_journal(j, now_ms=3)
    assert res["conflict"] == first["inserted"] and not res["inserted"]
    assert j.research_bank_objects() == tampered
    with pytest.raises(rb.ResearchBankError):
        rb.load(j)


def test_run_result_conflicts_are_refused(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    orig = rr.record_from_journal

    def conflicting(journal, now_ms):
        out = orig(journal, now_ms=now_ms)
        out["conflict"], out["inserted"] = out["inserted"], []
        return out
    monkeypatch.setattr(rr, "record_from_journal", conflicting)
    out = run_.run(j, "k", 1)
    monkeypatch.undo()
    [xid] = out["receipt"]["steps"][3]["outcomes"]["conflict"]
    res = rb.record_from_journal(j, now_ms=2)
    assert res["refusals"] == [(out["run_id"], xid,
                                rb.RESULT_CONFLICT_IN_RUN)]
    assert j.research_bank_objects() == []


# ── 8 canonical nested types / keys enforced ────────────────────────────
@pytest.mark.parametrize("mangle", [
    lambda t: t.replace('"recorded_at_ms":1', '"recorded_at_ms":1.0'),
    lambda t: t.replace('"event_id":', '"event_id":true,"event_id":', 1),
    lambda t: t.replace('"value":7', '"value":NaN', 1),
    lambda t: t + " ",
    lambda t: json.dumps(json.loads(t), sort_keys=True),
    lambda t: t.replace('"reason":null', '"reason":false', 1),
])
def test_non_canonical_or_retyped_text_fails_closed(tmp_path, mangle):
    j, _ = _filed(tmp_path)
    text = mangle(_bank_row(j)["canonical_json"])
    assert text != _bank_row(j)["canonical_json"]
    _set_bank(j, canonical_json=text, canonical_sha256=_sha(text))
    with pytest.raises(rb.ResearchBankError):
        rb.load(j)


@pytest.mark.parametrize("path", [
    (), ("links",), ("links", "run"), ("question",), ("sources", 0),
    ("experiments",), ("experiments", "steps", 0), ("limitations", 0),
    ("cost",)])
def test_extra_key_at_any_level_fails_closed(tmp_path, path):
    j, _ = _filed(tmp_path)
    rec = json.loads(_bank_row(j)["canonical_json"])
    cur = rec
    for p in path:
        cur = cur[p]
    cur["score"] = 1
    _restore(j, rec)
    with pytest.raises(rb.ResearchBankError, match="keys"):
        rb.load(j)


@pytest.mark.parametrize("col,value", [
    ("run_id", "x"), ("scope_id", "s9"), ("canonical_sha256", "0" * 64),
    ("question_id", "x")])
def test_row_projection_is_verified(tmp_path, col, value):
    j, _ = _filed(tmp_path)
    _set_bank(j, **{col: value})
    with pytest.raises(rb.ResearchBankError, match="row_projection"):
        rb.load(j)


def test_bank_object_id_is_verified(tmp_path):
    j, _ = _filed(tmp_path)
    rec = json.loads(_bank_row(j)["canonical_json"])
    rec["bank_object_id"] = "0" * 64
    text = rb.canonical(rec)
    _set_bank(j, canonical_json=text)
    with pytest.raises(rb.ResearchBankError, match="bank_object_id"):
        rb.load(j)


# ── 9 no evaluative / scoring / asset fields ────────────────────────────
_MANDATED = {"supporting_evidence", "limitations"}


def test_object_carries_no_evaluative_or_asset_fields(tmp_path):
    j = _ran(tmp_path, verdicts=(W, D, D, W, D))
    rb.record_from_journal(j, now_ms=2)
    for obj in rb.load(j):
        keys = set(_all_keys(obj)) - _MANDATED
        assert not {k for k in keys if _FORBIDDEN.search(k)}
        assert not {k for k in keys if re.search(
            r"credib|quality|regime|claim_|hypothes.*status|verdict", k)}
        assert "values" not in keys and "source_content" not in keys


# ── 10 no Attention / Kernel / Analyst / Risk / Execution / live wiring ─
def test_module_imports_only_contract_modules():
    src = Path(rb.__file__).read_text()
    mods, names = set(), set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "hashlib", "json", "trader.cognition"}
    assert names == {"annotations", "research_question", "research_plan",
                     "research_evidence", "research_result", "research_run"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(rb.__file__).resolve().parents[1]
    pat = re.compile(r"research_bank\b|record_research_bank_object")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_bank.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive; the offline context-only
    # recall is the only reader
    assert callers == ["cognition/research_recall.py", "core/journal.py"]
    assert "research_bank import" not in (root / "core/journal.py").read_text()


def test_filing_writes_only_the_bank_table(tmp_path):
    j = _ran(tmp_path)
    other = ("brain_events", "decisions", "trades", "strategies") \
        + CHAIN_TABLES
    before = {t: j.query(f"SELECT * FROM {t}") for t in other}
    rb.record_from_journal(j, now_ms=2)
    rb.load(j)
    assert {t: j.query(f"SELECT * FROM {t}") for t in other} == before


def test_legacy_journal_gains_the_table_and_stays_readable(tmp_path):
    p = tmp_path / "legacy.db"
    Journal(p)
    with sqlite3.connect(p) as c:
        c.execute("DROP TABLE research_bank_objects")
    j = Journal(p)
    assert j.research_bank_objects() == []
    assert rb.load(j) == []
