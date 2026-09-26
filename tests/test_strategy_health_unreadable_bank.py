"""strategy-health-unreadable-bank-object.v1: one immutable context_only
Research Bank object per verified (unreadable run, unreadable result) pair,
in its own table apart from research-bank-object.v1, whose table, fixtures,
IDs, hashes and view stay byte-identical. The object binds the exact
Q/P/E/R/run IDs and hashes, copies the structural result verbatim, keeps
claims / classification / next questions unavailable and copies run
telemetry without any total-cost inference.
"""
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_research_evidence_source_binding import (FIXTURE, build_all,
                                                         fixture_text)
from tests.test_strategy_decay_research_plan import CF, D, EF, W
from tests.test_strategy_health_unreadable_question import (NO_FRAME,
                                                            _journal, pinned)
from tests.test_strategy_health_unreadable_run import (_clock, _copy,
                                                       _decay_journal,
                                                       _spec_rows, _tamper_id,
                                                       _two)
from trader.cognition import research_bank as rb
from trader.cognition import research_bank_view as rbv
from trader.cognition import research_run as run_
from trader.cognition import research_sources as rs
from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_evidence as ue
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.cognition import research_unreadable_result as ur
from trader.cognition import research_unreadable_run as ru
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

ROOT = Path(__file__).resolve().parents[1]
BASE = "37f9473"                         # HEAD this package was cut from
TABLE = "research_unreadable_bank_objects"
OBJ_TABLES = ("research_unreadable_questions", "research_unreadable_plans",
              "research_unreadable_evidence", "research_unreadable_results")
NAMES = ("question", "plan", "evidence", "result")
ID_KEYS = ("question_id", "plan_id", "evidence_id", "result_id")
UNCHANGED = ("research_bank_objects", "research_runs", "research_questions",
             "research_plans", "research_evidence", "research_results",
             "research_registrations", "research_next_questions",
             "research_unreadable_runs", "brain_events") + OBJ_TABLES


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _tables(j, only=None, skip=(TABLE,)):
    names = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: j.query(f'SELECT * FROM "{t}" ORDER BY rowid') for t in names
            if (only is None or t in only) and t not in skip}


def _filed(tmp_path, j=None, key="k", ms=5):
    j = j if j is not None else _two(tmp_path)
    ru.run(j, key, ms)
    res = ub.record_from_journal(j, now_ms=9)
    assert res["refusals"] == [] and res["conflict"] == []
    return j, res


def _rows(j):
    return j.research_unreadable_bank_objects()


def _set_row(j, _where, **cols):
    sets = ",".join(f"{k}=?" for k in cols)
    with j._tx() as c:
        c.execute(f"UPDATE {TABLE} SET {sets} WHERE bank_object_id=?",
                  tuple(cols.values()) + (_where,))


def _restore(j, old_id, rec):
    """Store a (mutated) object with a recomputed id and matching row."""
    rec["bank_object_id"] = ub.bank_object_id(rec)
    _set_row(j, old_id, **ub.row_for(rec))


def _walk_keys(v, out):
    if isinstance(v, dict):
        out.update(v)
        for x in v.values():
            _walk_keys(x, out)
    elif isinstance(v, list):
        for x in v:
            _walk_keys(x, out)
    return out


# ── 1-2 compile_failed / evaluation_failed paths file deterministically ─
@pytest.mark.parametrize("outcome,verdict", [("compile", CF), ("raise", EF),
                                             (NO_FRAME, EF)])
def test_single_unreadable_path_files_one_deterministic_object(
        tmp_path, outcome, verdict):
    a = _journal(tmp_path, {"s1": outcome})
    b = _copy(tmp_path, a, "b.db")
    ra, rb_ = (ru.run(a, "k", 3, clock=_clock()),
               ru.run(b, "k", 3, clock=_clock()))
    oa = ub.record_from_journal(a, now_ms=4)
    ob = ub.record_from_journal(b, now_ms=99)     # recorded_at differs
    assert len(oa["inserted"]) == 1 and oa["inserted"] == ob["inserted"]
    [row_a], [row_b] = _rows(a), _rows(b)
    assert row_a["canonical_json"] == row_b["canonical_json"]
    assert row_a["canonical_sha256"] == _sha(row_a["canonical_json"])
    [obj] = ub.load(a)
    assert obj["bank_object_id"] == oa["inserted"][0]
    assert obj["links"]["run"]["run_id"] == ra["run_id"] == rb_["run_id"]
    assert obj["question"]["recorded_reasons"]["verdict"] == verdict
    assert obj["schema"] == "strategy-health-unreadable-bank-object.v1"
    assert (obj["bank_kind"], obj["authority"]) == (
        "strategy_health_unreadable", "context_only")


def test_two_results_file_two_objects_in_result_order(tmp_path):
    j, res = _filed(tmp_path)
    assert len(res["inserted"]) == 2
    out = ru.load(j)[0]["receipt"]["steps"][3]["outcomes"]["inserted"]
    assert [o["links"]["result"]["result_id"] for o in ub.load(j)] == \
        [r["object_id"] for r in out]
    verdicts = [o["question"]["recorded_reasons"]["verdict"]
                for o in ub.load(j)]
    assert sorted(verdicts) == sorted([CF, EF])


# ── 3 exact Q/P/E/R/run IDs and hashes ──────────────────────────────────
def test_links_bind_every_record_id_and_canonical_hash(tmp_path):
    j, _ = _filed(tmp_path)
    [stored] = ru.load(j)
    [run_row] = j.research_unreadable_runs()
    for obj in ub.load(j):
        lk = obj["links"]
        res = j.research_unreadable_result_by_id(lk["result"]["result_id"])
        ev = j.research_unreadable_evidence_by_id(res["evidence_id"])
        plan = j.research_unreadable_plan_by_id(res["plan_id"])
        q = j.research_unreadable_question_by_id(res["question_id"])
        for name, row, idk in zip(NAMES, (q, plan, ev, res), ID_KEYS):
            assert lk[name] == {"schema": row["schema"], idk: row[idk],
                                "canonical_sha256": row["canonical_sha256"]}
            assert row["canonical_sha256"] == _sha(row["canonical_json"])
        assert lk["run"] == {
            "schema": ru.SCHEMA, "run_id": run_row["run_id"],
            "canonical_sha256": run_row["canonical_sha256"],
            "telemetry_schema": ru.TELEMETRY_SCHEMA,
            "telemetry_sha256": run_row["telemetry_sha256"]}
        assert obj["question"] == {k: json.loads(q["canonical_json"])[k]
                                   for k in ub._QUESTION_KEYS}
        assert obj["plan"] == {k: json.loads(plan["canonical_json"])[k]
                               for k in ub._PLAN_KEYS}
        evrec = json.loads(ev["canonical_json"])
        assert [s["route_id"] for s in obj["sources"]] == list(up.ROUTE_IDS)
        for s, r in zip(obj["sources"], evrec["routes"]):
            assert {k: s[k] for k in ue._ROUTE_COPY} == \
                {k: r[k] for k in ue._ROUTE_COPY}
            assert s["items"] == [{k: it[k] for k in ("event_id",
                                                      "record_sha256",
                                                      "kind", "subject")}
                                  for it in r["items"]]
        # frozen content stays in the evidence record, never in the object
        assert "source_content" not in _walk_keys(obj, set())
        exp = obj["experiments"]
        assert exp["run_id"] == stored["receipt"]["run_id"]
        assert exp["inputs"] == stored["receipt"]["inputs"]
        assert [(s["step"], s["chain_record_id"], s["chain_record_outcome"])
                for s in exp["steps"]] == [
            (n, lk[n][k], "inserted") for n, k in zip(NAMES, ID_KEYS)]


def test_duplicate_outcomes_in_a_second_run_are_filed_as_duplicate(
        tmp_path):
    j, _ = _filed(tmp_path)
    ru.run(j, "k2", 6)
    res = ub.record_from_journal(j, now_ms=10)
    assert len(res["inserted"]) == 2 and len(res["duplicate"]) == 2
    second = [o for o in ub.load(j)
              if o["experiments"]["inputs"]["run_key"] == "k2"]
    assert len(second) == 2
    for o in second:
        assert {s["chain_record_outcome"]
                for s in o["experiments"]["steps"]} == {"duplicate"}


# ── 4 structural result verbatim ────────────────────────────────────────
def test_structural_result_is_copied_verbatim(tmp_path):
    j, _ = _filed(tmp_path)
    for obj in ub.load(j):
        [res] = [r for r in ur.load(j)
                 if r["result_id"] == obj["links"]["result"]["result_id"]]
        assert obj["result"] == {k: res[k] for k in ub._RESULT_KEYS}
        assert (obj["result"]["status"], obj["result"]["assessment"],
                obj["result"]["reason"]) == (
            "INCONCLUSIVE", "NOT_ASSESSED",
            "no_registered_falsifier_predicates")
        assert obj["limitations"][:2] == [
            {"origin_schema": ur.SCHEMA, "origin_id": res["result_id"],
             "origin_field": f, "entry": res[f]}
            for f in ("assessment", "reason")]


def test_only_exactly_attributed_run_refusals_are_limitations(
        tmp_path, monkeypatch):
    j = _two(tmp_path)
    orig = up.record_from_journal

    def with_refusals(journal, now_ms):
        out = orig(journal, now_ms=now_ms)
        first = journal.research_unreadable_questions()[0]["question_id"]
        out["refusals"] += [(first, "extra_reason"), ("0" * 64, "unrelated")]
        return out
    monkeypatch.setattr(up, "record_from_journal", with_refusals)
    ru.run(j, "k", 5)
    monkeypatch.undo()
    ub.record_from_journal(j, now_ms=9)
    first = j.research_unreadable_questions()[0]["question_id"]
    objs = ub.load(j)
    assert len(objs) == 2
    for o in objs:
        extra = o["limitations"][2:]
        if o["links"]["question"]["question_id"] == first:
            assert extra == [{
                "origin_schema": ru.SCHEMA,
                "origin_id": o["links"]["run"]["run_id"],
                "origin_field": "steps.plan.outcomes.refusals",
                "entry": {"question_id": first, "reason": "extra_reason"}}]
        else:
            assert extra == []


# ── 5-7 claims / classification / next questions unavailable ────────────
def test_unavailable_and_unclassified_fields_are_exact(tmp_path):
    j, _ = _filed(tmp_path)
    for obj in ub.load(j):
        assert obj["extracted_claims"] == {
            "status": "NOT_AVAILABLE",
            "reason": "no_claim_extraction_contract"}
        for f in ("supporting_evidence", "contradictory_evidence"):
            assert obj[f] == {"status": "NOT_CLASSIFIED",
                              "reason": "no_registered_falsifier_predicates"}
        assert obj["next_questions"] == {
            "status": "NOT_AVAILABLE", "reason": "no_next_question_generator"}
        assert obj["source_liveness"] == {
            "status": "INHERITED", "reason": ub.SOURCE_LIVENESS_REASON}


@pytest.mark.parametrize("field,value", [
    ("extracted_claims", {"status": "AVAILABLE", "reason": "x"}),
    ("supporting_evidence", {"status": "SUPPORTED",
                             "reason": "no_registered_falsifier_predicates"}),
    ("contradictory_evidence", {"status": "REFUTED",
                                "reason": "no_registered_falsifier_predicates"}),
    ("next_questions", {"status": "AVAILABLE",
                        "reason": "no_next_question_generator"}),
    ("source_liveness", {"status": "INDEPENDENT", "reason": "x"})])
def test_forged_unavailable_fields_fail_closed(tmp_path, field, value):
    j, _ = _filed(tmp_path)
    row = _rows(j)[0]
    rec = json.loads(row["canonical_json"])
    rec[field] = value
    _restore(j, row["bank_object_id"], rec)
    with pytest.raises(ub.UnreadableBankError, match=f"^{field}_contract$"):
        ub.load(j)


@pytest.mark.parametrize("path,value", [
    (("result", "status"), "SUPPORTED"),
    (("result", "assessment"), "ASSESSED"),
    (("result", "reason"), "diagnosed"),
    (("cost", "total_research_cost"), 123),
    (("cost", "attribution"), "per_result")])
def test_forged_result_or_cost_fails_contract(tmp_path, path, value):
    j, _ = _filed(tmp_path)
    row = _rows(j)[0]
    rec = json.loads(row["canonical_json"])
    rec[path[0]][path[1]] = value
    _restore(j, row["bank_object_id"], rec)
    with pytest.raises(ub.UnreadableBankError, match="^contract$"):
        ub.load(j)


def test_object_carries_no_evaluative_or_asset_fields(tmp_path):
    j, _ = _filed(tmp_path)
    for obj in ub.load(j):
        # extracted_claims is the mandated NOT_AVAILABLE field
        keys = _walk_keys(obj, set()) - {"extracted_claims"}
        assert not {k for k in keys if re.search(
            r"score|rank|salien|useful|priority|suppress|repeat|novel|"
            r"budget|cap$|efficien|falsifier|threshold|regime|asset|symbol|"
            r"diagnos|credib|claims$|recall|registration", k)}, keys
        text = ub.canonical({k: v for k, v in obj.items()
                             if k != "semantics"})
        assert "SUPPORTED" not in text and "REFUTED" not in text


# ── 8 telemetry copied exactly, no total-cost inference ─────────────────
def test_cost_is_the_run_telemetry_verbatim(tmp_path):
    j, _ = _filed(tmp_path)
    [stored] = ru.load(j)
    [row] = j.research_unreadable_runs()
    tel = stored["telemetry"]
    for obj in ub.load(j):
        assert obj["cost"] == {
            "scope": "strategy_health_unreadable_run",
            "attribution": "run_wide_not_attributed_to_this_result",
            "total_research_cost": "NOT_ESTABLISHED",
            "telemetry_schema": tel["schema"], "run_id": tel["run_id"],
            "telemetry_sha256": row["telemetry_sha256"],
            "telemetry_semantics": tel["semantics"], "steps": tel["steps"]}
        assert _sha(ub.canonical(tel)) == row["telemetry_sha256"]
        # no sum, total or per-result value was formed
        assert set(_walk_keys(obj["cost"], set())) == {
            "scope", "attribution", "total_research_cost",
            "telemetry_schema", "run_id", "telemetry_sha256",
            "telemetry_semantics", "steps", "step", "elapsed_wall_ns",
            "rows_read", "status", "value", "unit", "source", "reason"}


def test_not_measured_telemetry_keeps_its_exact_reason(tmp_path,
                                                       monkeypatch):
    j = _two(tmp_path)
    orig = uq.record_from_journal

    def sneaky(journal, now_ms):
        journal._conn().execute("SELECT 1").fetchall()
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(uq, "record_from_journal", sneaky)
    ru.run(j, "k", 5)
    monkeypatch.undo()
    ub.record_from_journal(j, now_ms=9)
    for obj in ub.load(j):
        rows = obj["cost"]["steps"][0]["rows_read"]
        assert rows == {"status": "NOT_MEASURED", "value": None,
                        "unit": "rows", "source": None,
                        "reason": "unmetered_journal_access:"
                                  "direct_connection_access"}
        assert obj["cost"]["steps"][1]["rows_read"]["status"] == "MEASURED"


def test_telemetry_is_outside_the_identity(tmp_path):
    a = _two(tmp_path)
    b = _copy(tmp_path, a, "b.db")
    ru.run(a, "k", 5, clock=_clock(1))
    ru.run(b, "k", 5, clock=_clock(1000))
    ia = ub.record_from_journal(a, now_ms=9)["inserted"]
    ib = ub.record_from_journal(b, now_ms=9)["inserted"]
    # the receipt (hence the run hash and the ids) are identical; the
    # copied telemetry differs, so the object text differs truthfully
    assert ia == ib
    assert [r["canonical_json"] for r in _rows(a)] != \
        [r["canonical_json"] for r in _rows(b)]


# ── 9 tampered / missing / substituted chain fails closed ───────────────
@pytest.mark.parametrize("i", range(4))
def test_tampered_chain_record_fails_closed(tmp_path, i):
    j, _ = _filed(tmp_path)
    ub.load(j)
    with j._tx() as c:
        c.execute(f"UPDATE {OBJ_TABLES[i]} SET canonical_json="
                  "canonical_json || ' '")
    with pytest.raises(ub.UnreadableBankError,
                       match=f"^source_invalid:run:reference_invalid:"
                             f"{NAMES[i]}:"):
        ub.load(j)


@pytest.mark.parametrize("i", range(4))
def test_deleted_chain_record_fails_closed(tmp_path, i):
    j, _ = _filed(tmp_path)
    with j._tx() as c:
        c.execute(f"DELETE FROM {OBJ_TABLES[i]}")
    with pytest.raises(ub.UnreadableBankError,
                       match="^source_invalid:run:reference_"):
        ub.load(j)


def test_tampered_or_deleted_run_fails_closed(tmp_path):
    j, _ = _filed(tmp_path)
    k = _copy(tmp_path, j, "k.db")
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_runs SET "
                  "canonical_json=canonical_json || ' '")
    with pytest.raises(ub.UnreadableBankError,
                       match="^source_invalid:run:receipt_not_canonical$"):
        ub.load(j)
    with k._tx() as c:
        c.execute("DELETE FROM research_unreadable_runs")
    with pytest.raises(ub.UnreadableBankError, match="^source_missing:run$"):
        ub.load(k)


def test_substituted_result_link_fails_rebuild(tmp_path):
    """An object re-pointed at the run's other result, with a consistent
    id and row, does not rebuild to its stored text."""
    j, _ = _filed(tmp_path)
    [a, b] = _rows(j)
    rec = json.loads(a["canonical_json"])
    other = json.loads(b["canonical_json"])
    rec["links"]["result"] = other["links"]["result"]
    # storage alone already refuses two objects for one (run, result)
    with pytest.raises(Exception, match="UNIQUE"):
        _restore(j, a["bank_object_id"], dict(rec))
    with j._tx() as c:
        c.execute(f"DELETE FROM {TABLE} WHERE bank_object_id=?",
                  (b["bank_object_id"],))
    _restore(j, a["bank_object_id"], rec)
    with pytest.raises(ub.UnreadableBankError, match="^rebuild_mismatch$"):
        ub.load(j)


@pytest.mark.parametrize("path,value", [
    (("question", "question"), {"template_id": "x", "text": "y"}),
    (("plan", "routes"), []),
    (("links", "evidence", "canonical_sha256"), "0" * 64),
    (("experiments", "steps"), []),
    (("limitations",), []),
    (("cost", "steps"), [])])
def test_forged_object_fails_rebuild(tmp_path, path, value):
    j, _ = _filed(tmp_path)
    row = _rows(j)[0]
    rec = json.loads(row["canonical_json"])
    tgt = rec
    for p in path[:-1]:
        tgt = tgt[p]
    tgt[path[-1]] = value
    _restore(j, row["bank_object_id"], rec)
    with pytest.raises(ub.UnreadableBankError, match="^rebuild_mismatch$"):
        ub.load(j)


def test_chain_record_missing_from_run_is_refused(tmp_path):
    j, _ = _filed(tmp_path)
    [stored] = ru.load(j)
    [obj] = ub.load(j)[:1]
    chain = ub._chain(j, obj["links"]["run"]["run_id"],
                      obj["links"]["result"]["result_id"])
    rec = json.loads(json.dumps(stored["receipt"]))
    rec["steps"][2]["outcomes"]["inserted"] = [
        r for r in rec["steps"][2]["outcomes"]["inserted"]
        if r["object_id"] != obj["links"]["evidence"]["evidence_id"]]
    with pytest.raises(ub.UnreadableBankError,
                       match="^chain_not_in_run:evidence$"):
        ub.build({**chain, "receipt": rec})
    rec["steps"][2]["outcomes"]["conflict"] = [
        obj["links"]["evidence"]["evidence_id"]]
    with pytest.raises(ub.UnreadableBankError,
                       match="^chain_record_conflict_in_run:evidence$"):
        ub.build({**chain, "receipt": rec})


def test_failed_run_is_refused_and_files_nothing(tmp_path, monkeypatch):
    j = _two(tmp_path)

    def boom(journal, now_ms):
        raise RuntimeError("x")
    monkeypatch.setattr(ue, "record_from_journal", boom)
    out = ru.run(j, "k", 5)
    monkeypatch.undo()
    res = ub.record_from_journal(j, now_ms=9)
    assert res == {"inserted": [], "duplicate": [], "conflict": [],
                   "refusals": [(out["run_id"], None, "run_not_completed")]}
    assert _rows(j) == []


def test_run_reaching_no_result_is_refused(tmp_path):
    from tests.test_strategy_health_unreadable_question import COMPLETE
    j = _journal(tmp_path, {"s1": COMPLETE})
    out = ru.run(j, "k", 1)
    assert ub.record_from_run(j, out["run_id"], now_ms=2)["refusals"] == [
        (out["run_id"], None, "run_reached_no_result")]


def test_record_run_is_scoped_to_one_run(tmp_path):
    j = _two(tmp_path)
    a, b = ru.run(j, "a", 5), ru.run(j, "b", 6)
    res = ub.record_from_run(j, a["run_id"], now_ms=9)
    assert len(res["inserted"]) == 2
    assert {r["run_id"] for r in _rows(j)} == {a["run_id"]}
    assert ub.load(j, run_id=b["run_id"]) == []
    assert ub.record_from_run(j, "f" * 64, now_ms=9)["refusals"] == [
        ("f" * 64, None, "source_missing:run")]


# ── 10 source deletion/change inherits source-liveness refusal ──────────
def test_changed_live_source_fails_closed_without_touching_stored_rows(
        tmp_path):
    j, _ = _filed(tmp_path)
    before = _tables(j, only=(TABLE,) + OBJ_TABLES
                     + ("research_unreadable_runs",), skip=())
    [_, src] = _spec_rows(j)
    _tamper_id(j, src["id"], lambda d: d.replace('"no_frame"',
                                                 '"no_frame" '))
    with pytest.raises(ub.UnreadableBankError,
                       match="^source_invalid:run:reference_invalid:"):
        ub.load(j)
    # the stored object is unchanged; only its provenance no longer verifies
    assert _tables(j, only=(TABLE,) + OBJ_TABLES
                   + ("research_unreadable_runs",), skip=()) == before


@pytest.mark.parametrize("target", ["history", "source", "sweep"])
def test_deleted_live_source_fails_closed(tmp_path, target):
    j, _ = _filed(tmp_path)
    [hist, src] = _spec_rows(j)
    sweeps = [r for r in j.strategy_health_rows()
              if r["kind"] == ho.KIND_SWEEP]
    rid = {"history": hist["id"], "source": src["id"],
           "sweep": sweeps[-1]["id"]}[target]
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (rid,))
    with pytest.raises(ub.UnreadableBankError, match="^source_invalid:"):
        ub.load(j)


# ── 11 deterministic ID / JSON / hash ───────────────────────────────────
def test_identity_is_deterministic_and_verified(tmp_path):
    j, _ = _filed(tmp_path)
    for row in _rows(j):
        rec = ub.from_json(row["canonical_json"])
        assert rec["bank_object_id"] == ub.bank_object_id(rec) == _sha(
            ub.canonical({
                "schema": ub.SCHEMA, "bank_kind": ub.BANK_KIND,
                "builder_id": ub.BUILDER_ID,
                "run_id": rec["links"]["run"]["run_id"],
                "run_canonical_sha256":
                    rec["links"]["run"]["canonical_sha256"],
                "result_id": rec["links"]["result"]["result_id"],
                "result_canonical_sha256":
                    rec["links"]["result"]["canonical_sha256"]}))
        assert ub.canonical(rec) == row["canonical_json"]
        assert ub.row_for(rec) == {k: row[k] for k in ub.row_for(rec)}
    rec = json.loads(_rows(j)[0]["canonical_json"])
    rec["bank_object_id"] = "0" * 64
    with pytest.raises(ub.UnreadableBankError, match="^bank_object_id$"):
        ub.from_json(ub.canonical(rec))


@pytest.mark.parametrize("mangle", [
    lambda t: t + " ", lambda t: json.dumps(json.loads(t), indent=1),
    lambda t: t.replace('"event_id":1,', '"event_id":1.0,', 1),
    lambda t: t[:-1] + ',"schema":"x"}'])
def test_non_canonical_or_retyped_text_fails_closed(tmp_path, mangle):
    j, _ = _filed(tmp_path)
    row = _rows(j)[0]
    _set_row(j, row["bank_object_id"],
             canonical_json=mangle(row["canonical_json"]))
    with pytest.raises(ub.UnreadableBankError):
        ub.load(j)


@pytest.mark.parametrize("path", [(), ("links",), ("links", "run"),
                                  ("question",), ("plan",), ("result",),
                                  ("experiments",), ("cost",)])
def test_extra_key_at_any_level_fails_closed(tmp_path, path):
    j, _ = _filed(tmp_path)
    rec = json.loads(_rows(j)[0]["canonical_json"])
    tgt = rec
    for p in path:
        tgt = tgt[p]
    tgt["extra"] = 1
    with pytest.raises(ub.UnreadableBankError, match="keys"):
        ub.from_json(ub.canonical(rec))


@pytest.mark.parametrize("col,value", [("scope_id", "s2"),
                                       ("question_id", "0" * 64)])
def test_row_projection_is_verified(tmp_path, col, value):
    j, _ = _filed(tmp_path)
    _set_row(j, _rows(j)[0]["bank_object_id"], **{col: value})
    with pytest.raises(ub.UnreadableBankError, match="^row_projection$"):
        ub.load(j)


# ── 12-13 idempotent duplicate; conflict never overwrites ───────────────
def test_duplicate_write_is_idempotent(tmp_path):
    j, first = _filed(tmp_path)
    before = _tables(j, only=(TABLE,), skip=())
    again = ub.record_from_journal(j, now_ms=77)
    assert again == {"inserted": [], "duplicate": first["inserted"],
                     "conflict": [], "refusals": []}
    assert _tables(j, only=(TABLE,), skip=()) == before


def test_conflict_never_overwrites(tmp_path):
    j, _ = _filed(tmp_path)
    before = _tables(j, only=(TABLE,), skip=())
    row = dict(_rows(j)[0])
    other = dict(row, canonical_json=row["canonical_json"] + " ",
                 canonical_sha256="0" * 64)
    assert j.record_research_unreadable_bank_object(
        other, recorded_at_ms=1) == "conflict"
    # same (run, result) under another id is also a conflict
    other = dict(row, bank_object_id="e" * 64)
    assert j.record_research_unreadable_bank_object(
        other, recorded_at_ms=1) == "conflict"
    assert _tables(j, only=(TABLE,), skip=()) == before


def test_stored_conflict_is_reported_not_overwritten(tmp_path):
    j, first = _filed(tmp_path)
    row = _rows(j)[0]
    _set_row(j, row["bank_object_id"],
             canonical_json=row["canonical_json"] + " ")
    before = _tables(j, only=(TABLE,), skip=())
    res = ub.record_from_journal(j, now_ms=10)
    assert res["conflict"] == [row["bank_object_id"]]
    assert _tables(j, only=(TABLE,), skip=()) == before


# ── 14-15 the two bank families refuse each other ───────────────────────
def test_decay_bank_rejects_unreadable_chain(tmp_path):
    j, _ = _filed(tmp_path)
    [urun] = j.research_unreadable_runs()
    row = _rows(j)[0]
    with pytest.raises(rb.ResearchBankError, match="^keys$"):
        rb.from_json(row["canonical_json"])
    # the decay filer never reads the unreadable run table
    assert rb.record_run(j, urun["run_id"], now_ms=9)["refusals"] == [
        (urun["run_id"], None, "source_missing:run")]
    assert rb.record_from_journal(j, now_ms=9) == {
        "inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    with pytest.raises(rb.ResearchBankError, match="^source_missing:result$"):
        rb._chain(j, urun["run_id"], row["result_id"],
                  stored={"receipt": {}, "telemetry": {}})
    # an unreadable object placed in research_bank_objects is refused
    k = _copy(tmp_path, j, "forged.db")
    with k._tx() as c:
        c.execute("INSERT INTO research_bank_objects SELECT * FROM "
                  f"{TABLE}")
    with pytest.raises(rb.ResearchBankError, match="^keys$"):
        rb.load(k)


def test_unreadable_bank_rejects_decay_chain(tmp_path):
    j = _decay_journal(tmp_path)
    assert rb.record_from_journal(j, now_ms=3)["inserted"]
    [drun] = j.research_runs()
    [dbank] = j.research_bank_objects()
    with pytest.raises(ub.UnreadableBankError, match="^keys$"):
        ub.from_json(dbank["canonical_json"])
    assert ub.record_from_run(j, drun["run_id"], now_ms=9)["refusals"] == [
        (drun["run_id"], None, "source_missing:run")]
    # a decay result is never found through an unreadable run
    ru.run(j, "k", 7)
    [urun] = j.research_unreadable_runs()
    with pytest.raises(ub.UnreadableBankError,
                       match="^source_missing:result$"):
        ub._chain(j, urun["run_id"], dbank["result_id"])
    # a verified decay chain handed to the builder is refused by family
    dchain = rb._chain(j, drun["run_id"], dbank["result_id"])
    with pytest.raises(ub.UnreadableBankError, match="^chain_family:run$"):
        ub.build(dchain)
    [uobj] = [ub.build(ub._chain(j, urun["run_id"], x))
              for x in ub.reached_results(
                  ru.load(j, run_id=urun["run_id"])[0]["receipt"])[0]]
    uchain = ub._chain(j, urun["run_id"], uobj["links"]["result"]["result_id"])
    with pytest.raises(ub.UnreadableBankError,
                       match="^chain_family:question$"):
        ub.build({**uchain, "question": dchain["question"]})
    # a decay object placed in the unreadable table is refused on load
    k = _copy(tmp_path, j, "forged.db")
    with k._tx() as c:
        c.execute(f"INSERT INTO {TABLE} SELECT * FROM research_bank_objects")
    with pytest.raises(ub.UnreadableBankError, match="^keys$"):
        ub.load(k)


# ── 16-18 isolation and byte-compatibility ──────────────────────────────
@pytest.mark.parametrize("verdicts", [[W, CF, D], [W, EF, D], [CF, EF, D]])
def test_filing_leaves_every_other_table_unchanged(tmp_path, verdicts):
    j = _decay_journal(tmp_path, verdicts)
    rb.record_from_journal(j, now_ms=3)
    ru.run(j, "k", 7)
    assert j.research_bank_objects()
    regs = j.query("SELECT * FROM research_registrations ORDER BY rowid")
    assert regs
    before = _tables(j, skip=(TABLE, "research_registrations"))
    decay_bank = rb.load(j)
    res = ub.record_from_journal(j, now_ms=9)
    assert len(res["inserted"]) == verdicts.count(CF) + verdicts.count(EF)
    ub.load(j)
    ub.record_from_journal(j, now_ms=10)
    assert _tables(j, skip=(TABLE, "research_registrations")) == before
    assert set(before) >= set(UNCHANGED) - {"research_registrations"}
    # decay receipts unchanged; only this family's first-insert receipts added
    after = j.query("SELECT * FROM research_registrations ORDER BY rowid")
    assert after[:len(regs)] == regs
    assert sorted(r["record_id"] for r in after[len(regs):]) == \
        sorted(res["inserted"])
    assert {r["record_type"] for r in after[len(regs):]} == {ub.SCHEMA}
    assert rb.load(j) == decay_bank
    assert rs.REGISTRY_SHA256 == _sha(rs.canonical_registry())


def test_decay_bank_ids_and_hashes_unchanged_by_unreadable_filing(tmp_path):
    a = _decay_journal(tmp_path, [W, CF, EF, D], name="a.db")
    b = _copy(tmp_path, a, "b.db")
    ru.run(b, "k", 7)
    ub.record_from_journal(b, now_ms=8)        # unreadable filing first on b
    da, db = rb.record_from_journal(a, now_ms=9), \
        rb.record_from_journal(b, now_ms=9)
    assert da["inserted"] and da == db
    cols = ("bank_object_id", "canonical_sha256", "canonical_json")
    assert [tuple(r[c] for c in cols) for r in a.research_bank_objects()] == \
        [tuple(r[c] for c in cols) for r in b.research_bank_objects()]
    decay = ("SELECT * FROM research_registrations WHERE record_type "
             "NOT IN (?,?) ORDER BY rowid")
    assert a.query(decay, (uq.SCHEMA, ub.SCHEMA)) == \
        b.query(decay, (uq.SCHEMA, ub.SCHEMA))


def test_existing_decay_fixtures_are_byte_identical():
    assert fixture_text(build_all()) == FIXTURE.read_text()


def test_source_registry_bytes_and_hash_unchanged(tmp_path):
    pinned = "17b68ba56d2e8d925d1448910527a7d7a7b9f5460601743fe88eb549e25e928e"
    before = rs.canonical_registry()
    j, _ = _filed(tmp_path)
    ub.load(j)
    assert rs.canonical_registry() == before and _sha(before) == pinned \
        == rs.REGISTRY_SHA256


def _at_base(path):
    blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:{path}"],
                          capture_output=True, text=True)
    if blob.returncode != 0:
        pytest.skip("baseline commit not available")
    return blob.stdout


@pytest.mark.parametrize("path", [
    "trader/cognition/research_sources.py",
    "trader/cognition/research_question.py",
    "trader/cognition/research_plan.py",
    "trader/cognition/research_evidence.py",
    "trader/cognition/research_result.py",
    "trader/cognition/research_run.py",
    "trader/cognition/research_bank.py",
    "trader/cognition/research_bank_view.py",
    "trader/cognition/research_recall.py",
    # research_cost.py gained opt-in unreadable coverage; its default output
    # is pinned byte-identical in test_research_cost_unreadable_coverage.py
    "trader/cognition/research_next_question.py",
    "trader/cognition/research_unreadable_question.py",
    "trader/cognition/research_unreadable_plan.py",
    "trader/cognition/research_unreadable_evidence.py",
    "trader/cognition/research_unreadable_result.py",
    "trader/cognition/research_unreadable_run.py",
    "tests/fixtures/research_evidence_v1_pre_source_registry.json",
    "tests/fixtures/research_plan_v1_pre_source_registry.json"])
def test_upstream_modules_and_fixtures_are_byte_identical(path):
    assert pinned(path, (ROOT / path).read_text()) == \
        pinned(path, _at_base(path))


# ── 17 the bank view never accepts a sibling object ─────────────────────
def test_bank_view_does_not_accept_unreadable_objects(tmp_path):
    j, _ = _filed(tmp_path)
    row = _rows(j)[0]
    with pytest.raises(rbv.ResearchBankViewError,
                       match="^source_missing:bank_object$"):
        rbv.build(j, bank_object_id=row["bank_object_id"],
                  max_prior_objects=1)
    with pytest.raises(rbv.ResearchBankViewError,
                       match="^run_bank_objects_not_exactly_one:0$"):
        rbv.build(j, run_id=row["run_id"], max_prior_objects=1)
    k = _copy(tmp_path, j, "forged.db")
    with k._tx() as c:
        c.execute("INSERT INTO research_bank_objects SELECT * FROM "
                  f"{TABLE} LIMIT 1")
    with pytest.raises(rbv.ResearchBankViewError,
                       match="^source_invalid:bank_object:"
                             "ResearchBankError:keys$"):
        rbv.build(k, bank_object_id=row["bank_object_id"],
                  max_prior_objects=1)


def test_bank_view_of_a_decay_object_is_unchanged(tmp_path):
    a = _decay_journal(tmp_path, [W, CF, D], name="a.db")
    rb.record_from_journal(a, now_ms=3)
    b = _copy(tmp_path, a, "b.db")
    ru.run(b, "k", 7)
    ub.record_from_journal(b, now_ms=8)
    [dbank] = a.research_bank_objects()
    va = rbv.build(a, bank_object_id=dbank["bank_object_id"],
                   max_prior_objects=2)
    vb = rbv.build(b, bank_object_id=dbank["bank_object_id"],
                   max_prior_objects=2)
    assert rbv.canonical(va) == rbv.canonical(vb)


# ── 19 no recall / registration / no-repeat / live wiring ───────────────
def test_module_imports_only_contract_modules():
    src = Path(ub.__file__).read_text()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imports.add(f"{node.module}:{','.join(a.name for a in node.names)}")
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
    assert imports == {"__future__:annotations", "hashlib", "json",
                       "trader.cognition:research_unreadable_evidence",
                       "trader.cognition:research_unreadable_plan",
                       "trader.cognition:research_unreadable_question",
                       "trader.cognition:research_unreadable_result",
                       "trader.cognition:research_unreadable_run"}
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for n in names:
        for word in ("recall", "registration",
                     "research_bank", "research_cost", "research_sources",
                     "attention", "kernel", "risk", "executor",
                     "orchestrator", "requests", "urllib", "socket", "llm",
                     "budget", "suppress", "repeat", "score", "rank"):
            assert word not in n.lower() or n.startswith(
                ("research_unreadable_", "record_research_unreadable_")), n


def test_nothing_calls_the_new_module():
    root = ROOT / "trader"
    pat = re.compile(r"research_unreadable_bank\b|"
                     r"record_research_unreadable_bank_object")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_unreadable_bank.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive (and names the module only in
    # its schema comment); only the offline context-only unreadable recall,
    # guarded by its own tests, imports the contract
    assert callers == ["cognition/research_unreadable_recall.py",
                       "core/journal.py"]
    assert "research_unreadable_bank import" not in \
        (root / "core/journal.py").read_text()


def test_journal_helpers_register_only_through_first_insert_writer(tmp_path):
    src = (ROOT / "trader/core/journal.py").read_text()
    body = src.split("def record_research_unreadable_bank_object(", 1)[1] \
        .split("\n    def ", 1)[0]
    assert "_record_registered(" in body and "research_registrations" not in body
    reader = src.split("def research_unreadable_bank_objects(", 1)[1] \
        .split("\n    def ", 1)[0]
    assert "SELECT" in reader and not re.search(
        r"INSERT|UPDATE|DELETE|_tx\(", reader)


def test_legacy_journal_without_bank_table_gets_it_additively(tmp_path):
    # a legacy journal never filed bank objects, so it holds no bank
    # receipts: drop the table on a copy taken before filing
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    first = ub.record_from_journal(_copy(tmp_path, j, "ref.db"), now_ms=9)
    assert first["inserted"]
    with j._tx() as c:
        c.execute(f"DROP TABLE {TABLE}")
    j2 = Journal(j.db_path)
    assert j2.research_unreadable_bank_objects() == [] and ub.load(j2) == []
    assert ub.record_from_journal(j2, now_ms=9)["inserted"] == \
        first["inserted"]
    assert run_.load(j2) == []
