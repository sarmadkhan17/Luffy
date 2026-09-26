"""strategy-health-unreadable-run.v1: one explicit offline run of the
unreadable strategy-health chain (question -> plan -> evidence -> result)
through each step's unchanged record_from_journal, persisted as one
immutable context_only receipt in its own table, apart from research-run.v1,
whose table, fixtures, IDs and hashes stay byte-identical. Telemetry is
MEASURED or NOT_MEASURED, outside the receipt identity.
"""
import ast
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.test_research_evidence_source_binding import (FIXTURE, build_all,
                                                         fixture_text)
from tests.test_strategy_decay_research_plan import CF, D, EF, W, _FullHist
from tests.test_strategy_health_unreadable_question import (COMPLETE,
                                                            NO_FRAME,
                                                            _journal)
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_result as rr
from trader.cognition import research_run as run_
from trader.cognition import research_sources as rs
from trader.cognition import research_unreadable_evidence as ue
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.cognition import research_unreadable_result as ur
from trader.cognition import research_unreadable_run as ru
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

ROOT = Path(__file__).resolve().parents[1]
BASE = "3a7fd70"                         # HEAD this package was cut from
MODS = (uq, up, ue, ur)
NAMES = ("question", "plan", "evidence", "result")
OBJ_TABLES = ("research_unreadable_questions", "research_unreadable_plans",
              "research_unreadable_evidence", "research_unreadable_results")
DECAY_TABLES = ("research_runs", "research_questions", "research_plans",
                "research_evidence", "research_results",
                "research_registrations")
ID_KEYS = ("question_id", "plan_id", "evidence_id", "result_id")


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _copy(tmp_path, src: Journal, name) -> Journal:
    a = sqlite3.connect(src.db_path)
    b = sqlite3.connect(tmp_path / name)
    a.backup(b)
    a.close(), b.close()
    return Journal(tmp_path / name)


def _clock(step=7):
    t = {"n": 0}

    def clock():
        t["n"] += step
        return t["n"]
    return clock


def _tables(j, only=None, skip=("research_unreadable_runs",)):
    names = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: j.query(f'SELECT * FROM "{t}" ORDER BY rowid') for t in names
            if (only is None or t in only) and t not in skip}


def _stored_row(j):
    [r] = j.research_unreadable_runs()
    return r


def _tamper_id(j, rid, fn):
    with j._tx() as c:
        [detail] = c.execute("SELECT detail FROM brain_events WHERE id=?",
                             (rid,)).fetchone()
        c.execute("UPDATE brain_events SET detail=? WHERE id=?",
                  (fn(detail), rid))


def _spec_rows(j, spec="s1"):
    return [r for r in j.strategy_health_rows()
            if r["kind"] == ho.KIND_SPEC and r["subject"] == spec]


def _two(tmp_path):
    """compile_failed then evaluation_failed of one spec."""
    return _journal(tmp_path, {"s1": "compile"}, {"s1": NO_FRAME})


def _decay_journal(tmp_path, verdicts=(W, CF, D), name="d.db"):
    """Decay history with its full decay chain recorded, plus one decay
    run receipt; the same health rows also carry unreadable verdicts."""
    j = Journal(tmp_path / name)
    for r in _FullHist().seq("s1", list(verdicts)).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    for m in (rq, rp, re_, rr):
        m.record_from_journal(j, now_ms=1)
    run_.run(j, "decay-k", 2)
    return j


def _serialized(key, res):
    ref = ([{k: getattr(r, k) for k in ru._QUESTION_REFUSAL_KEYS}
            for r in res["refusals"]] if key is None else
           [{key: a, "reason": b} for a, b in res["refusals"]])
    return ref


def _refs(j, table, idk, ids):
    rows = {r[idk]: r for r in j.query(f"SELECT * FROM {table}")}
    return [{"object_id": i, "canonical_sha256": rows[i]["canonical_sha256"],
             "scope": {"kind": rows[i]["scope_kind"],
                       "spec_id": rows[i]["scope_id"]}} for i in ids]


# ── 1 successful Q -> P -> E -> R run gives a deterministic receipt ─────
def test_successful_run_produces_deterministic_receipt(tmp_path):
    a = _two(tmp_path)
    b = _copy(tmp_path, a, "b.db")
    direct = _copy(tmp_path, a, "direct.db")
    ra = ru.run(a, "k", 5, clock=_clock(1))
    rb = ru.run(b, "k", 5, clock=_clock(1000))
    assert ra["status"] == rb["status"] == "inserted" and ra["executed"]
    assert ra["run_id"] == rb["run_id"] == ru.run_id("k", 5)
    assert ru.run_id("k", 6) != ra["run_id"] != ru.run_id("k2", 5)
    assert _stored_row(a)["canonical_json"] == \
        _stored_row(b)["canonical_json"]
    rec = ra["receipt"]
    assert {k: rec[k] for k in ("schema", "run_kind", "runner_id",
                                "authority", "inputs")} == {
        "schema": "strategy-health-unreadable-run.v1",
        "run_kind": "strategy_health_unreadable",
        "runner_id": ru.RUNNER_ID, "authority": "context_only",
        "inputs": {"run_key": "k", "recorded_at_ms": 5}}
    # the steps wrote exactly what direct calls write, and every ref is the
    # stored row's own id / hash / scope
    res = [m.record_from_journal(direct, now_ms=5) for m in MODS]
    assert _tables(a, OBJ_TABLES) == _tables(direct, OBJ_TABLES)
    for s, r, t, idk in zip(rec["steps"], res, OBJ_TABLES, ID_KEYS):
        assert s["status"] == "COMPLETED" and len(r["inserted"]) == 2
        assert s["outcomes"] == {"inserted": _refs(a, t, idk, r["inserted"]),
                                 "duplicate": [], "conflict": [],
                                 "refusals": []}
    assert ru.load(a) == [{"receipt": rec, "telemetry": ra["telemetry"]}]
    assert {r["status"] for r in ur.load(a)} == {"INCONCLUSIVE"}


# ── 2-3 compile_failed / evaluation_failed paths succeed structurally ───
@pytest.mark.parametrize("outcome,verdict", [("compile", CF), ("raise", EF),
                                             (NO_FRAME, EF)])
def test_single_unreadable_source_path_succeeds(tmp_path, outcome, verdict):
    j = _journal(tmp_path, {"s1": outcome})
    out = ru.run(j, "k", 3)
    assert [s["status"] for s in out["receipt"]["steps"]] == ["COMPLETED"] * 4
    for s in out["receipt"]["steps"]:
        assert len(s["outcomes"]["inserted"]) == 1
        assert s["outcomes"]["inserted"][0]["scope"] == {
            "kind": "strategy", "spec_id": "s1"}
        assert not (s["outcomes"]["duplicate"] or s["outcomes"]["conflict"]
                    or s["outcomes"]["refusals"])
    [ev] = ue.load(j)
    src = json.loads(ev["routes"][0]["items"][0]["source_content"])
    assert src["verdict"] == verdict
    [res] = ur.load(j)
    assert (res["status"], res["assessment"]) == ("INCONCLUSIVE",
                                                  "NOT_ASSESSED")
    ru.load(j)


def test_readable_history_runs_empty(tmp_path):
    j = _journal(tmp_path, {"s1": COMPLETE})
    out = ru.run(j, "k", 1)
    for s in out["receipt"]["steps"]:
        assert s["outcomes"] == {"inserted": [], "duplicate": [],
                                 "conflict": [], "refusals": []}
    assert all(not v for v in _tables(j, OBJ_TABLES).values())
    ru.load(j)


# ── 4 strict step order ─────────────────────────────────────────────────
def test_steps_run_in_strict_order(tmp_path, monkeypatch):
    j = _two(tmp_path)
    calls = []
    for name, m in zip(NAMES, MODS):
        orig = m.record_from_journal

        def wrapped(journal, now_ms, _n=name, _o=orig):
            calls.append((_n, now_ms, journal is j))
            return _o(journal, now_ms=now_ms)
        monkeypatch.setattr(m, "record_from_journal", wrapped)
    out = ru.run(j, "k", 4)
    assert calls == [(n, 4, True) for n in NAMES]
    assert [s["step"] for s in out["receipt"]["steps"]] == list(NAMES)
    assert [s["record_schema"] for s in out["receipt"]["steps"]] == [
        m.SCHEMA for m in MODS]
    assert [t["step"] for t in out["telemetry"]["steps"]] == list(NAMES)


# ── 5 first FAILED step: later steps NOT_RUN, no retry ──────────────────
@pytest.mark.parametrize("i", range(4))
def test_first_failed_step_stops_the_chain(tmp_path, monkeypatch, i):
    j = _two(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    for m in MODS[:i]:
        m.record_from_journal(ref, now_ms=5)
    calls = {"n": 0}

    class Boom(LookupError):
        pass

    def boom(journal, now_ms):
        calls["n"] += 1
        raise Boom(f"step {i} broke")
    monkeypatch.setattr(MODS[i], "record_from_journal", boom)
    out = ru.run(j, "k", 5)
    steps = out["receipt"]["steps"]
    assert [s["status"] for s in steps] == \
        ["COMPLETED"] * i + ["FAILED"] + ["NOT_RUN"] * (3 - i)
    assert steps[i]["reason"] == "step_raised" and \
        steps[i]["outcomes"] is None
    assert steps[i]["error"] == {"type": "Boom", "message": f"step {i} broke"}
    for s in steps[i + 1:]:
        assert (s["reason"], s["error"], s["outcomes"]) == (
            "upstream_step_failed", None, None)
    for t in out["telemetry"]["steps"][i + 1:]:
        assert t["elapsed_wall_ns"]["status"] == \
            t["rows_read"]["status"] == "NOT_MEASURED"
        assert t["rows_read"]["reason"] == "step_not_run"
    assert out["telemetry"]["steps"][i]["elapsed_wall_ns"]["status"] == \
        "MEASURED"
    assert calls["n"] == 1                       # no retry
    # nothing downstream of the failure was written
    assert _tables(j, OBJ_TABLES) == _tables(ref, OBJ_TABLES)
    ru.load(j)
    assert ru.run(j, "k", 5)["executed"] is False and calls["n"] == 1


def test_unrecognized_step_result_aborts_without_receipt(tmp_path,
                                                         monkeypatch):
    j = _two(tmp_path)
    monkeypatch.setattr(up, "record_from_journal",
                        lambda journal, now_ms: {"inserted": []})
    with pytest.raises(ru.UnreadableRunError,
                       match="^unrecognized_step_result$"):
        ru.run(j, "k", 5)
    assert j.research_unreadable_runs() == []


@pytest.mark.parametrize("i", range(4))
@pytest.mark.parametrize("refusals", [
    {}, "", (), None,
    "nonempty_mapping"])
def test_non_list_refusal_container_aborts_without_receipt(
        tmp_path, monkeypatch, i, refusals):
    j = _two(tmp_path)
    if refusals == "nonempty_mapping":      # valid refusals as mapping keys
        refusals = ({rq.Refusal("s1", "why", 1, "sw"): "dropped?"} if i == 0
                    else {("x", "why"): "dropped?"})
    for m in MODS[:i]:
        m.record_from_journal(j, now_ms=5)
    before = _tables(j, skip=())
    ret = {"inserted": [], "duplicate": [], "conflict": [],
           "refusals": refusals}
    monkeypatch.setattr(MODS[i], "record_from_journal",
                        lambda journal, now_ms: ret)
    for m in MODS[:i]:
        monkeypatch.setattr(m, "record_from_journal",
                            lambda journal, now_ms: {
                                "inserted": [], "duplicate": [],
                                "conflict": [], "refusals": []})
    with pytest.raises(ru.UnreadableRunError, match="^refusals_type$"):
        ru.run(j, "k", 5)
    assert j.research_unreadable_runs() == []
    assert _tables(j, skip=()) == before


@pytest.mark.parametrize("key,ms", [("", 1), (None, 1), ("k", -1),
                                    ("k", True), ("k", 1.0)])
def test_invalid_run_inputs_are_refused_before_any_step(tmp_path, key, ms):
    j = _two(tmp_path)
    with pytest.raises(ru.UnreadableRunError):
        ru.run(j, key, ms)
    assert all(not v for v in _tables(j, OBJ_TABLES).values())
    assert j.research_unreadable_runs() == []


# ── 6 refusals are preserved verbatim ───────────────────────────────────
def test_real_question_refusals_match_the_steps_own_return(tmp_path):
    j = _two(tmp_path)
    j.log_brain_event(ho.KIND_SPEC, "", "{}")    # unattributable: refuses
    direct = _copy(tmp_path, j, "direct.db")
    res = uq.record_from_journal(direct, now_ms=5)
    assert res["refusals"]
    out = ru.run(j, "k", 5)
    q = out["receipt"]["steps"][0]["outcomes"]
    assert q["refusals"] == _serialized(None, res) == [
        {"spec_id": r.spec_id, "reason": r.reason, "event_id": r.event_id,
         "sweep_id": r.sweep_id} for r in res["refusals"]]
    ru.load(j)


def test_real_plan_refusals_and_conflicts_match_the_steps_own_return(
        tmp_path):
    j = _two(tmp_path)
    uq.record_from_journal(j, now_ms=1)
    [qrow, _] = j.research_unreadable_questions()
    with j._tx() as c:                            # a tampered stored question
        c.execute("UPDATE research_unreadable_questions SET canonical_json="
                  "canonical_json || ' ' WHERE question_id=?",
                  (qrow["question_id"],))
    direct = _copy(tmp_path, j, "direct.db")
    res = [m.record_from_journal(direct, now_ms=5) for m in MODS]
    out = ru.run(j, "k", 5)
    steps = out["receipt"]["steps"]
    assert steps[0]["outcomes"]["conflict"] == res[0]["conflict"] == [
        qrow["question_id"]]
    for s, r, key in zip(steps[1:], res[1:], ID_KEYS[:3]):
        assert s["outcomes"]["refusals"] == _serialized(key, r)
        assert s["outcomes"]["conflict"] == r["conflict"]
    assert steps[1]["outcomes"]["refusals"][0]["question_id"] == \
        qrow["question_id"]
    assert steps[1]["outcomes"]["refusals"][0]["reason"] == \
        res[1]["refusals"][0][1]
    # a conflicted stored object that fails verification fails the load
    with pytest.raises(ru.UnreadableRunError,
                       match="^reference_invalid:question:"):
        ru.load(j)


def test_every_outcome_kind_and_refusal_shape_is_captured(tmp_path,
                                                          monkeypatch):
    j = _two(tmp_path)
    ret = {
        uq: {"inserted": [], "duplicate": [], "conflict": ["c" * 64],
             "refusals": [rq.Refusal("s9", "why:q", 7, "sw"),
                          rq.Refusal(None, "global", None, None)]},
        up: {"inserted": [], "duplicate": [], "conflict": [],
             "refusals": [("q1", "why:p"), (None, "why:none")]},
        ue: {"inserted": [], "duplicate": [], "conflict": [],
             "refusals": [("p1", "why:e")]},
        ur: {"inserted": [], "duplicate": [], "conflict": ["x"],
             "refusals": [("e1", "why:r")]}}
    for m, r in ret.items():
        monkeypatch.setattr(m, "record_from_journal",
                            lambda journal, now_ms, _r=r: _r)
    steps = ru.run(j, "k", 5)["receipt"]["steps"]
    assert steps[0]["outcomes"]["refusals"] == [
        {"spec_id": "s9", "reason": "why:q", "event_id": 7, "sweep_id": "sw"},
        {"spec_id": None, "reason": "global", "event_id": None,
         "sweep_id": None}]
    assert steps[0]["outcomes"]["conflict"] == ["c" * 64]
    assert steps[1]["outcomes"]["refusals"] == [
        {"question_id": "q1", "reason": "why:p"},
        {"question_id": None, "reason": "why:none"}]
    assert steps[2]["outcomes"]["refusals"] == [
        {"plan_id": "p1", "reason": "why:e"}]
    assert steps[3]["outcomes"] == {
        "inserted": [], "duplicate": [], "conflict": ["x"],
        "refusals": [{"evidence_id": "e1", "reason": "why:r"}]}


# ── 7-8 semantic identity excludes timing telemetry ─────────────────────
def test_telemetry_differs_while_receipt_is_identical(tmp_path):
    a = _two(tmp_path)
    b = _copy(tmp_path, a, "b.db")
    ra = ru.run(a, "k", 5, clock=_clock(2))
    rb = ru.run(b, "k", 5, clock=_clock(900))
    assert ra["telemetry"] != rb["telemetry"]
    assert ra["receipt"] == rb["receipt"]
    row_a, row_b = _stored_row(a), _stored_row(b)
    assert row_a["canonical_sha256"] == row_b["canonical_sha256"]
    assert row_a["telemetry_sha256"] != row_b["telemetry_sha256"]
    assert "elapsed" not in row_a["canonical_json"] and \
        "rows_read" not in row_a["canonical_json"]
    tel_a = row_a["telemetry_json"]
    assert a.record_research_unreadable_run(
        ru.row_for(rb["receipt"], rb["telemetry"]),
        recorded_at_ms=99) == "duplicate"
    assert _stored_row(a)["telemetry_json"] == tel_a    # never overwritten


class _Counting(Journal):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rows = 0

    def query(self, sql, params=()):
        out = super().query(sql, params)
        self.rows += len(out)
        return out


def test_measured_counters_are_exact(tmp_path):
    j = _two(tmp_path)
    c = _Counting(j.db_path)
    expected = []
    ref = _Counting(_copy(tmp_path, j, "ref.db").db_path)
    for m in MODS:
        before = ref.rows
        m.record_from_journal(ref, now_ms=5)
        expected.append(ref.rows - before)
    out = ru.run(c, "k", 5, clock=_clock(3))
    for t, n in zip(out["telemetry"]["steps"], expected):
        assert t["rows_read"] == {"status": "MEASURED", "value": n,
                                  "unit": "rows",
                                  "source": ru.ROWS_DEFINITION,
                                  "reason": None}
        assert t["elapsed_wall_ns"] == {"status": "MEASURED", "value": 3,
                                        "unit": "ns", "source": ru.CLOCK,
                                        "reason": None}
    assert "query" not in vars(c)            # instrumentation removed


def test_direct_connection_access_is_not_measured(tmp_path, monkeypatch):
    j = _two(tmp_path)
    orig = uq.record_from_journal

    def sneaky(journal, now_ms):
        journal._conn().execute("SELECT 1").fetchall()
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(uq, "record_from_journal", sneaky)
    out = ru.run(j, "k", 5)
    rows = out["telemetry"]["steps"][0]["rows_read"]
    assert rows["status"] == "NOT_MEASURED" and \
        rows["reason"] == "unmetered_journal_access:direct_connection_access"
    assert out["telemetry"]["steps"][1]["rows_read"]["status"] == "MEASURED"
    ru.load(j)


def test_telemetry_declares_no_cost_budget_or_efficiency(tmp_path):
    j = _two(tmp_path)
    out = ru.run(j, "k", 5)
    for t in out["telemetry"]["steps"]:
        assert set(t) == {"step", "elapsed_wall_ns", "rows_read"}
    keys = set()

    def walk(v):
        if isinstance(v, dict):
            keys.update(v)
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(out["receipt"]), walk(out["telemetry"])
    assert keys == {
        "schema", "run_kind", "runner_id", "authority", "run_id", "inputs",
        "run_key", "recorded_at_ms", "steps", "semantics", "step",
        "record_schema", "status", "reason", "error", "outcomes",
        "inserted", "duplicate", "conflict", "refusals", "object_id",
        "canonical_sha256", "scope", "kind", "spec_id", "elapsed_wall_ns",
        "rows_read", "value", "unit", "source"}


# ── 9 tampered objects / receipts fail closed ───────────────────────────
@pytest.mark.parametrize("i", range(4))
def test_tampered_referenced_object_fails_closed(tmp_path, i):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    ru.load(j)
    with j._tx() as c:
        c.execute(f"UPDATE {OBJ_TABLES[i]} SET canonical_json="
                  "canonical_json || ' '")
    with pytest.raises(ru.UnreadableRunError,
                       match=f"^reference_invalid:{NAMES[i]}:"):
        ru.load(j)


@pytest.mark.parametrize("i", range(4))
def test_deleted_referenced_object_fails_closed(tmp_path, i):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    with j._tx() as c:
        c.execute(f"DELETE FROM {OBJ_TABLES[i]}")
    with pytest.raises(ru.UnreadableRunError, match="^reference_"):
        ru.load(j)


def test_rehashed_object_substitution_fails_closed(tmp_path):
    """A stored result rewritten consistently (ID and hash) is refused by
    the result contract; the receipt never accepts another object."""
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    [row, _] = j.research_unreadable_results()
    rec = json.loads(row["canonical_json"])
    rec["status"] = "SUPPORTED"
    text = ur.canonical(rec)
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_results SET canonical_json=?, "
                  "canonical_sha256=?, status=? WHERE result_id=?",
                  (text, _sha(text), "SUPPORTED", row["result_id"]))
    with pytest.raises(ru.UnreadableRunError,
                       match="^reference_invalid:result:"):
        ru.load(j)


def _store(j, **cols):
    sets = ",".join(f"{k}=?" for k in cols)
    with j._tx() as c:
        c.execute(f"UPDATE research_unreadable_runs SET {sets}",
                  tuple(cols.values()))


def _rehash(j, rec, tel=None):
    row = _stored_row(j)
    tel = tel if tel is not None else json.loads(row["telemetry_json"])
    new = ru.row_for(rec, tel)
    _store(j, **{k: new[k] for k in ("canonical_json", "canonical_sha256",
                                     "telemetry_json", "telemetry_sha256")})


@pytest.mark.parametrize("edit,code", [
    (lambda r: r["steps"][0]["outcomes"]["inserted"][0].update(
        canonical_sha256="0" * 64), "receipt_rebuild_mismatch"),
    (lambda r: r["steps"][1]["outcomes"]["inserted"][0]["scope"].update(
        spec_id="s2"), "receipt_rebuild_mismatch"),
    (lambda r: r["steps"][2]["outcomes"]["inserted"][0].update(
        object_id="f" * 64), "reference_missing:evidence"),
    (lambda r: r["steps"][3]["outcomes"]["refusals"].append(
        {"evidence_id": 5, "reason": "x"}), "refusal_types"),
    (lambda r: r.update(authority="trading"), "receipt_contract"),
    (lambda r: r["inputs"].update(run_key="other"), "run_id_mismatch"),
    (lambda r: r["steps"].reverse(), "step_order"),
    (lambda r: r["steps"][0].update(status="NOT_RUN"), "step_status"),
    (lambda r: r.update(score=1), "receipt_keys")])
def test_tampered_receipt_fails_closed(tmp_path, edit, code):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    rec = json.loads(_stored_row(j)["canonical_json"])
    edit(rec)
    _rehash(j, rec)
    with pytest.raises(ru.UnreadableRunError, match=f"^{code}$"):
        ru.load(j)


@pytest.mark.parametrize("cols", [
    {"canonical_sha256": "0" * 64}, {"telemetry_sha256": "0" * 64},
    {"run_key": "other"}, {"run_recorded_at_ms": 6},
    {"schema": run_.SCHEMA}, {"run_kind": run_.RUN_KIND}])
def test_row_projection_is_verified(tmp_path, cols):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    _store(j, **cols)
    with pytest.raises(ru.UnreadableRunError, match="^row_projection$"):
        ru.load(j)


def test_tampered_telemetry_fails_closed(tmp_path):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    row = _stored_row(j)
    tel = json.loads(row["telemetry_json"])
    tel["steps"][0]["elapsed_wall_ns"]["value"] = -1
    _rehash(j, json.loads(row["canonical_json"]), tel)
    with pytest.raises(ru.UnreadableRunError, match="^measured_shape$"):
        ru.load(j)
    _store(j, telemetry_json=row["telemetry_json"] + " ")
    with pytest.raises(ru.UnreadableRunError,
                       match="^telemetry_not_canonical$"):
        ru.load(j)


def test_existing_invalid_receipt_blocks_rerun(tmp_path, monkeypatch):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    _store(j, canonical_json=_stored_row(j)["canonical_json"] + " ")
    for m in MODS:
        monkeypatch.setattr(m, "record_from_journal",
                            lambda *a, **k: pytest.fail("step re-executed"))
    with pytest.raises(ru.UnreadableRunError):
        ru.run(j, "k", 5)


# ── 10 live provenance: inherited source-liveness refusal ───────────────
def test_changed_live_source_fails_closed(tmp_path):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    before = _tables(j, skip=())
    [_, src] = _spec_rows(j)
    _tamper_id(j, src["id"], lambda d: d.replace('"no_frame"',
                                                 '"no_frame" '))
    with pytest.raises(ue.UnreadableEvidenceError):
        ue.load(j)
    with pytest.raises(ru.UnreadableRunError, match="^reference_invalid:"):
        ru.load(j)
    # nothing the runner stored changed
    after = _tables(j, skip=("brain_events",))
    assert after == {k: v for k, v in before.items() if k != "brain_events"}


@pytest.mark.parametrize("target", ["history", "source", "sweep"])
def test_deleted_live_source_fails_closed(tmp_path, target):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    [hist, src] = _spec_rows(j)
    sweeps = [r for r in j.strategy_health_rows()
              if r["kind"] == ho.KIND_SWEEP]
    rid = {"history": hist["id"], "source": src["id"],
           "sweep": sweeps[-1]["id"]}[target]
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (rid,))
    with pytest.raises(ru.UnreadableRunError, match="^reference_invalid:"):
        ru.load(j)


# ── 11 duplicate identical run is idempotent ────────────────────────────
def test_duplicate_run_is_idempotent_and_executes_nothing(tmp_path,
                                                          monkeypatch):
    j = _two(tmp_path)
    first = ru.run(j, "k", 3, clock=_clock(3))
    before = _tables(j, skip=())
    for m in MODS:
        monkeypatch.setattr(m, "record_from_journal",
                            lambda *a, **k: pytest.fail("step re-executed"))
    for jj in (j, Journal(j.db_path)):
        again = ru.run(jj, "k", 3, clock=_clock(11))
        assert again["status"] == "duplicate" and again["executed"] is False
        assert again["receipt"] == first["receipt"]
        assert again["telemetry"] == first["telemetry"]
    assert _tables(j, skip=()) == before


def test_second_run_key_records_duplicates_without_touching_objects(
        tmp_path):
    j = _two(tmp_path)
    ru.run(j, "k1", 3)
    objs = _tables(j, OBJ_TABLES)
    out = ru.run(j, "k2", 4)
    assert out["status"] == "inserted"
    for s in out["receipt"]["steps"]:
        assert s["outcomes"]["inserted"] == [] and \
            len(s["outcomes"]["duplicate"]) == 2
    assert _tables(j, OBJ_TABLES) == objs            # byte-identical
    assert len(ru.load(j)) == 2


def test_restart_after_unrecorded_run_is_idempotent(tmp_path, monkeypatch):
    j = _two(tmp_path)
    orig = Journal.record_research_unreadable_run

    def crash(self, row, *, recorded_at_ms):
        raise RuntimeError("crash before receipt")
    monkeypatch.setattr(Journal, "record_research_unreadable_run", crash)
    with pytest.raises(RuntimeError):
        ru.run(j, "k", 3)
    monkeypatch.setattr(Journal, "record_research_unreadable_run", orig)
    out = ru.run(j, "k", 3)
    assert out["status"] == "inserted"
    assert all(s["outcomes"]["inserted"] == [] and
               len(s["outcomes"]["duplicate"]) == 2
               for s in out["receipt"]["steps"])
    assert ru.run(j, "k", 3)["status"] == "duplicate"


# ── 12 conflicting run identity never overwrites ────────────────────────
def test_conflicting_receipt_never_overwrites(tmp_path):
    j = _two(tmp_path)
    out = ru.run(j, "k", 5)
    before = _stored_row(j)
    rec = json.loads(json.dumps(out["receipt"]))
    rec["steps"][0]["outcomes"]["refusals"].append(
        {"spec_id": None, "reason": "x", "event_id": None, "sweep_id": None})
    assert j.record_research_unreadable_run(
        ru.row_for(rec, out["telemetry"]), recorded_at_ms=5) == "conflict"
    assert _stored_row(j) == before
    ru.load(j)


def test_concurrent_writer_with_other_outcomes_is_conflict(tmp_path,
                                                           monkeypatch):
    j = _two(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    other = ru.run(ref, "k", 5)          # same run_id, reports inserts
    uq.record_from_journal(j, now_ms=5)  # ours will see duplicates
    real = Journal.research_unreadable_runs
    state = {"n": 0}

    def racing(self, run_id=None):
        state["n"] += 1
        if state["n"] == 1:
            self.record_research_unreadable_run(
                ru.row_for(other["receipt"], other["telemetry"]),
                recorded_at_ms=5)
            return []
        return real(self, run_id)
    monkeypatch.setattr(Journal, "research_unreadable_runs", racing)
    out = ru.run(j, "k", 5)
    monkeypatch.setattr(Journal, "research_unreadable_runs", real)
    assert out["status"] == "conflict"
    assert _stored_row(j)["canonical_json"] == ru.canonical(other["receipt"])


# ── 13-14 the two run families refuse each other ────────────────────────
def test_decay_runner_rejects_unreadable_family(tmp_path):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    row = _stored_row(j)
    with pytest.raises(run_.ResearchRunError, match="^receipt_keys$"):
        run_.from_json(row["canonical_json"], row["telemetry_json"])
    rec = json.loads(row["canonical_json"])
    del rec["authority"]
    with pytest.raises(run_.ResearchRunError, match="^receipt_contract$"):
        run_.from_json(run_.canonical(rec), row["telemetry_json"])
    # the decay runner never reads the unreadable table
    assert j.research_runs() == [] and run_.load(j) == []
    # an unreadable receipt placed in research_runs is refused on load
    k = _copy(tmp_path, j, "forged.db")
    with k._tx() as c:
        c.execute("INSERT INTO research_runs SELECT * FROM "
                  "research_unreadable_runs")
    with pytest.raises(run_.ResearchRunError, match="^receipt_keys$"):
        run_.load(k)
    # a decay run over unreadable-only objects cites none of them
    objs = {r[c] for t, c in zip(OBJ_TABLES, ID_KEYS)
            for r in j.query(f"SELECT {c} FROM {t}")}
    before = _tables(j, OBJ_TABLES)
    drec = run_.run(j, "d", 6)["receipt"]
    assert not objs & set(re.findall(r"[0-9a-f]{64}", run_.canonical(drec)))
    assert _tables(j, OBJ_TABLES) == before


def test_unreadable_runner_rejects_decay_family(tmp_path):
    j = _decay_journal(tmp_path)
    [drow] = j.research_runs()
    with pytest.raises(ru.UnreadableRunError, match="^receipt_keys$"):
        ru.from_json(drow["canonical_json"], drow["telemetry_json"])
    dec = json.loads(drow["canonical_json"])
    dec["authority"] = "context_only"
    with pytest.raises(ru.UnreadableRunError, match="^receipt_contract$"):
        ru.from_json(ru.canonical(dec), drow["telemetry_json"])
    # a decay receipt placed in the unreadable table is refused on load
    k = _copy(tmp_path, j, "forged.db")
    with k._tx() as c:
        c.execute("INSERT INTO research_unreadable_runs SELECT * FROM "
                  "research_runs")
    with pytest.raises(ru.UnreadableRunError, match="^receipt_keys$"):
        ru.load(k)
    # a well-formed unreadable receipt citing a decay question is refused
    out = ru.run(j, "k", 5)
    [dq] = j.research_questions()
    rec = json.loads(json.dumps(out["receipt"]))
    rec["steps"][0]["outcomes"]["inserted"][0]["object_id"] = \
        dq["question_id"]
    _rehash(j, rec, out["telemetry"])
    with pytest.raises(ru.UnreadableRunError,
                       match="^reference_missing:question$"):
        ru.load(j)
    # nothing decay-family is ever cited by an unreadable run
    decay_ids = {r[c] for t, c in (("research_questions", "question_id"),
                                   ("research_plans", "plan_id"),
                                   ("research_evidence", "evidence_id"),
                                   ("research_results", "result_id"))
                 for r in j.query(f"SELECT {c} FROM {t}")}
    assert not decay_ids & set(re.findall(r"[0-9a-f]{64}",
                                          ru.canonical(out["receipt"])))


# ── 15-17 isolation and byte-compatibility ──────────────────────────────
@pytest.mark.parametrize("verdicts", [[W, CF, D], [W, EF, D], [CF, EF, D]])
def test_recording_leaves_decay_tables_byte_identical(tmp_path, verdicts):
    j = _decay_journal(tmp_path, verdicts)
    assert all(_tables(j, DECAY_TABLES[:5]).values())
    decay = _tables(j, DECAY_TABLES)
    run_before = run_.load(j)
    ids_before = [(r["result_id"], r["canonical_sha256"])
                  for r in j.research_results()]
    out = ru.run(j, "k", 7)
    assert len(out["receipt"]["steps"][3]["outcomes"]["inserted"]) == \
        verdicts.count(CF) + verdicts.count(EF)
    objs = _tables(j, OBJ_TABLES)
    ru.load(j)
    assert ru.run(j, "k2", 8)["status"] == "inserted"
    assert _tables(j, DECAY_TABLES) == decay
    assert _tables(j, OBJ_TABLES) == objs
    assert run_.load(j) == run_before
    assert [(r["result_id"], r["canonical_sha256"])
            for r in j.research_results()] == ids_before


def test_decay_run_ids_and_hashes_unchanged_by_unreadable_runs(tmp_path):
    seed = Journal(tmp_path / "seed.db")
    for r in _FullHist().seq("s1", [W, CF, EF, D]).rows:
        seed.log_brain_event(r["kind"], r["subject"], r["detail"])
    a = _copy(tmp_path, seed, "a.db")
    b = _copy(tmp_path, seed, "b.db")
    ru.run(b, "k", 5)                         # unreadable run first on b
    da, db = run_.run(a, "dk", 9), run_.run(b, "dk", 9)
    assert da["run_id"] == db["run_id"] == run_.run_id("dk", 9)
    assert run_.canonical(da["receipt"]) == run_.canonical(db["receipt"])
    [ra], [rb] = a.research_runs(), b.research_runs()
    assert (ra["run_id"], ra["canonical_sha256"], ra["canonical_json"]) == \
        (rb["run_id"], rb["canonical_sha256"], rb["canonical_json"])
    assert _tables(a, DECAY_TABLES[1:]) == _tables(b, DECAY_TABLES[1:])


def test_existing_decay_fixtures_are_byte_identical():
    assert fixture_text(build_all()) == FIXTURE.read_text()


def test_source_registry_bytes_and_hash_unchanged(tmp_path):
    pinned = "17b68ba56d2e8d925d1448910527a7d7a7b9f5460601743fe88eb549e25e928e"
    before = rs.canonical_registry()
    assert rs.REGISTRY_SHA256 == pinned == _sha(before)
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    ru.load(j)
    assert rs.canonical_registry() == before and _sha(before) == pinned


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
    "trader/cognition/research_recall.py",
    "trader/cognition/research_cost.py",
    "trader/cognition/research_unreadable_question.py",
    "trader/cognition/research_unreadable_plan.py",
    "trader/cognition/research_unreadable_evidence.py",
    "trader/cognition/research_unreadable_result.py",
    "tests/fixtures/research_evidence_v1_pre_source_registry.json",
    "tests/fixtures/research_plan_v1_pre_source_registry.json"])
def test_upstream_modules_and_fixtures_are_byte_identical(path):
    assert (ROOT / path).read_text() == _at_base(path)


def test_run_writes_only_its_table_beyond_the_steps(tmp_path):
    j = _two(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    for m in MODS:
        m.record_from_journal(ref, now_ms=5)
    ru.run(j, "k", 5)
    ru.load(j)
    assert _tables(j) == _tables(ref)         # all but research_unreadable_runs
    assert len(j.research_unreadable_runs()) == 1
    for t in ("research_runs", "research_registrations",
              "research_bank_objects", "research_next_questions",
              "research_questions", "research_results"):
        assert j.query(f'SELECT * FROM "{t}"') == [], t


# ── 18 no Bank / recall / registration / cost ledger / live wiring ──────
def test_module_imports_only_contract_modules():
    src = Path(ru.__file__).read_text()
    imports = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imports.add(f"{node.module}:{','.join(a.name for a in node.names)}")
        elif isinstance(node, ast.Import):
            imports.update(a.name for a in node.names)
    assert imports == {"__future__:annotations", "hashlib", "json",
                       "threading", "time",
                       "trader.cognition:research_question",
                       "trader.cognition:research_unreadable_evidence",
                       "trader.cognition:research_unreadable_plan",
                       "trader.cognition:research_unreadable_question",
                       "trader.cognition:research_unreadable_result"}
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith(("research_", "record_")):
            names.add(node.value)
    for n in names:
        for word in ("research_run", "recall", "bank", "cost",
                     "registration", "research_sources", "attention",
                     "kernel", "risk", "executor", "orchestrator",
                     "requests", "urllib", "socket", "llm", "budget"):
            assert word not in n.lower() or n.startswith(
                ("research_unreadable_", "record_research_unreadable_")), n


def test_nothing_calls_the_new_module():
    root = ROOT / "trader"
    pat = re.compile(r"research_unreadable_run\b|record_research_unreadable_run")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_unreadable_run.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive (and names the module only in
    # its schema comment); nothing imports the contract
    assert callers == ["core/journal.py"]
    assert "research_unreadable_run import" not in \
        (root / "core/journal.py").read_text()


def test_legacy_journal_without_run_table_gets_it_additively(tmp_path):
    j = _two(tmp_path)
    ru.run(j, "k", 5)
    with j._tx() as c:
        c.execute("DROP TABLE research_unreadable_runs")
    j2 = Journal(j.db_path)
    assert j2.research_unreadable_runs() == [] and ru.load(j2) == []
    out = ru.run(j2, "k", 5)
    assert out["status"] == "inserted" and all(
        len(s["outcomes"]["duplicate"]) == 2 for s in out["receipt"]["steps"])
