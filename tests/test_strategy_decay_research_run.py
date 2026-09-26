"""research-run.v1: one explicit offline run of the strategy-decay chain.

The runner executes question -> plan -> evidence -> result through each
step's unchanged record_from_journal and persists one immutable receipt of
exactly what every step returned. Telemetry is MEASURED or NOT_MEASURED,
outside the receipt identity. It adds no research semantics and nothing
live, Attention, Risk or Execution calls it.
"""
import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_evidence import (
    D, EF, W, _decisions, _journal as _plan_journal)
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_result as rr
from trader.cognition import research_run as run_
from trader.core.journal import Journal

TABLES = ("research_questions", "research_plans", "research_evidence",
          "research_results")


def _copy(tmp_path, src: Journal, name) -> Journal:
    a = sqlite3.connect(src.db_path)
    b = sqlite3.connect(tmp_path / name)
    a.backup(b)
    a.close(), b.close()
    return Journal(tmp_path / name)


def _fresh(tmp_path, verdicts=(W, EF, D), name="fresh.db"):
    """Health history and decisions only: no stored research records."""
    seed = _plan_journal(tmp_path, list(verdicts), name="seed_" + name)
    _decisions(seed)
    j = _copy(tmp_path, seed, name)
    with j._tx() as c:
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
        # the seed's first-registration receipts go too; the immutability
        # triggers are dropped for this fixture only and recreated below
        c.execute("DROP TRIGGER research_registrations_no_delete")
        c.execute("DELETE FROM research_registrations")
    return Journal(j.db_path)


def _direct(j, now_ms=5):
    return [m.record_from_journal(j, now_ms=now_ms)
            for m in (rq, rp, re_, rr)]


def _rows(j):
    return {t: j.query(f"SELECT * FROM {t} ORDER BY rowid") for t in TABLES}


def _clock(step=7):
    t = {"n": 0}

    def clock():
        t["n"] += step
        return t["n"]
    return clock


def _serialized(key, res):
    ref = ([{k: getattr(r, k) for k in run_._QUESTION_REFUSAL_KEYS}
            for r in res["refusals"]] if key is None else
           [{key: a, "reason": b} for a, b in res["refusals"]])
    return {"inserted": res["inserted"], "duplicate": res["duplicate"],
            "conflict": res["conflict"], "refusals": ref}


def _stored_row(j):
    [r] = j.research_runs()
    return r


def _store(j, **cols):
    sets = ",".join(f"{k}=?" for k in cols)
    with j._tx() as c:
        c.execute(f"UPDATE research_runs SET {sets}", tuple(cols.values()))


# ── 1 one explicit run executes Q -> P -> E -> R in order ────────────────
def test_one_run_executes_the_chain_in_order(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    calls = []
    for name, mod in (("question", rq), ("plan", rp), ("evidence", re_),
                      ("result", rr)):
        orig = mod.record_from_journal

        def wrapped(journal, now_ms, _o=orig, _n=name):
            calls.append((_n, now_ms))
            return _o(journal, now_ms=now_ms)
        monkeypatch.setattr(mod, "record_from_journal", wrapped)
    out = run_.run(j, "run-1", 42, clock=_clock())
    assert calls == [("question", 42), ("plan", 42), ("evidence", 42),
                     ("result", 42)]
    assert out["status"] == "inserted" and out["executed"] is True
    rec = out["receipt"]
    assert [s["step"] for s in rec["steps"]] == [
        "question", "plan", "evidence", "result"]
    assert [s["record_schema"] for s in rec["steps"]] == [
        rq.SCHEMA, rp.SCHEMA, re_.SCHEMA, rr.SCHEMA]
    assert all(s["status"] == "COMPLETED" for s in rec["steps"])
    for s, t, idc in zip(rec["steps"], TABLES, ("question_id", "plan_id",
                                                "evidence_id", "result_id")):
        assert s["outcomes"]["inserted"] == [r[idc] for r in j.query(
            f"SELECT {idc} FROM {t} ORDER BY rowid")]
        assert len(s["outcomes"]["inserted"]) == 1
    assert all(r["recorded_at_ms"] == 42 for t in TABLES
               for r in j.query(f"SELECT recorded_at_ms FROM {t}"))


def test_runner_preserves_step_behavior_exactly(tmp_path):
    j = _fresh(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    direct = _direct(ref, now_ms=9)
    rec = run_.run(j, "k", 9)["receipt"]
    assert _rows(j) == _rows(ref)
    for s, res, (_n, _m, _s, key) in zip(rec["steps"], direct, run_.STEPS):
        assert s["outcomes"] == _serialized(key, res)


# ── 2 inserted / duplicate / conflict / refusal captured exactly ────────
def test_every_outcome_kind_is_captured_verbatim(tmp_path, monkeypatch):
    j = Journal(tmp_path / "e.db")
    fake = {
        rq: {"inserted": ["q1"], "duplicate": ["q2"], "conflict": ["q3"],
             "refusals": [rq.Refusal("s1", "ambiguous_chronology", 4, "sw"),
                          rq.Refusal(None, "malformed_sweep_record")]},
        rp: {"inserted": [], "duplicate": ["p1"], "conflict": [],
             "refusals": [("q9", "question_invalid:row_projection"),
                          (None, "odd reason: kept; verbatim")]},
        re_: {"inserted": [], "duplicate": [], "conflict": ["e1"],
              "refusals": [("p9", "source_plan_missing")]},
        rr: {"inserted": ["r1", "r2"], "duplicate": [], "conflict": [],
             "refusals": []},
    }
    for mod, res in fake.items():
        monkeypatch.setattr(mod, "record_from_journal",
                            lambda journal, now_ms, _r=res: _r)
    rec = run_.run(j, "k", 1)["receipt"]
    assert [s["outcomes"] for s in rec["steps"]] == [
        {"inserted": ["q1"], "duplicate": ["q2"], "conflict": ["q3"],
         "refusals": [{"spec_id": "s1", "reason": "ambiguous_chronology",
                       "event_id": 4, "sweep_id": "sw"},
                      {"spec_id": None, "reason": "malformed_sweep_record",
                       "event_id": None, "sweep_id": None}]},
        {"inserted": [], "duplicate": ["p1"], "conflict": [],
         "refusals": [{"question_id": "q9",
                       "reason": "question_invalid:row_projection"},
                      {"question_id": None,
                       "reason": "odd reason: kept; verbatim"}]},
        {"inserted": [], "duplicate": [], "conflict": ["e1"],
         "refusals": [{"plan_id": "p9", "reason": "source_plan_missing"}]},
        {"inserted": ["r1", "r2"], "duplicate": [], "conflict": [],
         "refusals": []}]
    # the stored receipt is the returned one, byte for byte
    assert _stored_row(j)["canonical_json"] == run_.canonical(rec)


def test_real_refusals_match_the_steps_own_return(tmp_path):
    j = _fresh(tmp_path)
    run_.run(j, "first", 1)
    with j._tx() as c:          # break the stored plan: evidence refuses it
        c.execute("UPDATE research_plans SET canonical_json="
                  "canonical_json || ' '")
    ref = _copy(tmp_path, j, "ref.db")
    direct = _direct(ref, now_ms=2)
    out = run_.run(j, "second", 2)
    for s, res, (_n, _m, _s, key) in zip(out["receipt"]["steps"], direct,
                                         run_.STEPS):
        assert s["outcomes"] == _serialized(key, res)
    ev = out["receipt"]["steps"][2]["outcomes"]
    assert ev["refusals"] and ev["refusals"][0]["plan_id"] == \
        j.research_plans()[0]["plan_id"]
    assert ev["refusals"][0]["reason"] == direct[2]["refusals"][0][1]


def test_second_run_records_duplicates(tmp_path):
    j = _fresh(tmp_path)
    first = run_.run(j, "a", 1)["receipt"]
    second = run_.run(j, "b", 1)["receipt"]
    for s1, s2 in zip(first["steps"], second["steps"]):
        assert s2["outcomes"]["inserted"] == []
        assert s2["outcomes"]["duplicate"] == s1["outcomes"]["inserted"]
    assert len(j.research_runs()) == 2
    assert len(run_.load(j)) == 2


# ── 3 retry with the same run identity is idempotent ────────────────────
def test_retry_same_identity_executes_nothing(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    first = run_.run(j, "k", 3, clock=_clock(3))
    before = _rows(j)
    for mod in (rq, rp, re_, rr):
        monkeypatch.setattr(mod, "record_from_journal",
                            lambda *a, **k: pytest.fail("step re-executed"))
    again = run_.run(j, "k", 3, clock=_clock(11))
    assert again["status"] == "duplicate" and again["executed"] is False
    assert again["run_id"] == first["run_id"]
    assert again["receipt"] == first["receipt"]
    assert again["telemetry"] == first["telemetry"]
    assert _rows(j) == before and len(j.research_runs()) == 1


def test_restart_after_unrecorded_run_is_idempotent(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    orig = Journal.record_research_run

    def crash(self, row, *, recorded_at_ms):
        raise RuntimeError("crash before receipt")
    monkeypatch.setattr(Journal, "record_research_run", crash)
    with pytest.raises(RuntimeError):
        run_.run(j, "k", 3)
    monkeypatch.setattr(Journal, "record_research_run", orig)
    out = run_.run(j, "k", 3)
    assert out["status"] == "inserted"
    assert all(s["outcomes"]["inserted"] == [] and
               len(s["outcomes"]["duplicate"]) == 1
               for s in out["receipt"]["steps"])
    assert run_.run(j, "k", 3)["status"] == "duplicate"


def test_run_id_is_deterministic_from_explicit_inputs_only(tmp_path):
    a = _fresh(tmp_path, name="a.db")
    b = _copy(tmp_path, a, "b.db")          # identical store
    ra = run_.run(a, "k", 5, clock=_clock(1))
    rb = run_.run(b, "k", 5, clock=_clock(1000))
    assert ra["run_id"] == rb["run_id"] == run_.run_id("k", 5)
    assert run_.run_id("k", 6) != ra["run_id"] != run_.run_id("k2", 5)
    assert _stored_row(a)["canonical_json"] == _stored_row(b)["canonical_json"]


@pytest.mark.parametrize("key,ms", [("", 1), (None, 1), ("k", -1),
                                    ("k", True), ("k", 1.0)])
def test_invalid_run_inputs_are_refused_before_any_step(tmp_path, key, ms):
    j = _fresh(tmp_path)
    with pytest.raises(run_.ResearchRunError):
        run_.run(j, key, ms)
    assert all(not v for v in _rows(j).values())


# ── 4 wall-time differences cause no semantic conflict ──────────────────
def test_wall_time_difference_is_duplicate_not_conflict(tmp_path):
    a = _fresh(tmp_path, name="a.db")
    b = _copy(tmp_path, a, "b.db")          # identical store
    ra = run_.run(a, "k", 5, clock=_clock(2))
    rb = run_.run(b, "k", 5, clock=_clock(900))
    assert ra["telemetry"] != rb["telemetry"]
    assert ra["receipt"] == rb["receipt"]
    tel_a = _stored_row(a)["telemetry_json"]
    assert a.record_research_run(run_.row_for(rb["receipt"], rb["telemetry"]),
                                 recorded_at_ms=99) == "duplicate"
    assert _stored_row(a)["telemetry_json"] == tel_a    # never overwritten


def test_semantically_different_receipt_conflicts_and_never_overwrites(
        tmp_path):
    j = _fresh(tmp_path)
    out = run_.run(j, "k", 5)
    before = _stored_row(j)
    rec = json.loads(json.dumps(out["receipt"]))
    rec["steps"][0]["outcomes"]["refusals"].append(
        {"spec_id": None, "reason": "x", "event_id": None, "sweep_id": None})
    assert j.record_research_run(run_.row_for(rec, out["telemetry"]),
                                 recorded_at_ms=5) == "conflict"
    assert _stored_row(j) == before


def test_concurrent_writer_with_other_outcomes_is_conflict(tmp_path,
                                                           monkeypatch):
    j = _fresh(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    other = run_.run(ref, "k", 5)          # same run_id, other store
    real_lookup = Journal.research_runs
    state = {"n": 0}

    def racing(self, run_id=None):
        state["n"] += 1
        if state["n"] == 1:              # our existence check: none yet
            self.record_research_run(
                run_.row_for(other["receipt"], other["telemetry"]),
                recorded_at_ms=5)
            return []
        return real_lookup(self, run_id)
    monkeypatch.setattr(Journal, "research_runs", racing)
    # the racing writer's receipt reports inserts; ours will report them
    # as duplicates of the rows it can see only after it runs
    with j._tx() as c:
        for t in TABLES:
            c.execute(f"DELETE FROM {t}")
    rq.record_from_journal(j, now_ms=5)
    out = run_.run(j, "k", 5)
    monkeypatch.setattr(Journal, "research_runs", real_lookup)
    assert out["status"] == "conflict"
    assert _stored_row(j)["canonical_json"] == run_.canonical(
        other["receipt"])


# ── 5 truthful counters ─────────────────────────────────────────────────
class _Counting(Journal):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rows = 0

    def query(self, sql, params=()):
        out = super().query(sql, params)
        self.rows += len(out)
        return out


def test_measured_counters_are_exact(tmp_path):
    j = _fresh(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    counting = _Counting(ref.db_path)
    expected = []
    for mod in (rq, rp, re_, rr):
        counting.rows = 0
        mod.record_from_journal(counting, now_ms=1)
        expected.append(counting.rows)
    tel = run_.run(j, "k", 1, clock=_clock(5))["telemetry"]
    assert [t["rows_read"] for t in tel["steps"]] == [
        {"status": "MEASURED", "value": n, "unit": "rows",
         "source": run_.ROWS_DEFINITION, "reason": None} for n in expected]
    assert expected[0] == len(j.strategy_health_rows())
    assert expected[1] == len(j.strategy_health_rows()) + 1
    assert [t["elapsed_wall_ns"] for t in tel["steps"]] == [
        {"status": "MEASURED", "value": 5, "unit": "ns",
         "source": "time.perf_counter_ns", "reason": None}] * 4


def test_direct_connection_access_is_not_measured(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    orig = rp.record_from_journal

    def peeking(journal, now_ms):
        journal._conn().execute("SELECT 1").fetchall()
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(rp, "record_from_journal", peeking)
    tel = run_.run(j, "k", 1)["telemetry"]
    assert tel["steps"][1]["rows_read"] == {
        "status": "NOT_MEASURED", "value": None, "unit": "rows",
        "source": None,
        "reason": "unmetered_journal_access:direct_connection_access"}
    assert tel["steps"][1]["elapsed_wall_ns"]["status"] == "MEASURED"
    assert tel["steps"][0]["rows_read"]["status"] == "MEASURED"
    assert len(run_.load(j)) == 1


def test_indirect_journal_method_connection_is_not_measured(tmp_path,
                                                            monkeypatch):
    j = _fresh(tmp_path)
    orig = Journal.research_questions

    def sneaky(self, scope_id=None):     # a reader bypassing query()
        self._conn().execute("SELECT 1").fetchall()
        return orig(self, scope_id)
    monkeypatch.setattr(Journal, "research_questions", sneaky)
    tel = run_.run(j, "k", 1)["telemetry"]
    assert tel["steps"][0]["rows_read"]["status"] == "MEASURED"
    assert all(t["rows_read"]["reason"] ==
               "unmetered_journal_access:direct_connection_access"
               for t in tel["steps"][1:])


def test_indirect_query_inside_a_writer_is_counted(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    ref = _copy(tmp_path, j, "ref.db")
    base = run_.run(ref, "k", 1)["telemetry"]
    orig = Journal.record_research_plan

    def extra(self, row, *, recorded_at_ms):
        self.query("SELECT 1 UNION ALL SELECT 2")
        return orig(self, row, recorded_at_ms=recorded_at_ms)
    monkeypatch.setattr(Journal, "record_research_plan", extra)
    tel = run_.run(j, "k", 1)["telemetry"]
    got = [t["rows_read"]["value"] for t in tel["steps"]]
    want = [t["rows_read"]["value"] for t in base["steps"]]
    assert got == [want[0], want[1] + 2, want[2], want[3]]
    assert all(t["rows_read"]["status"] == "MEASURED" for t in tel["steps"])


def test_other_thread_journal_access_is_not_measured(tmp_path, monkeypatch):
    import threading
    j = _fresh(tmp_path)
    orig = rq.record_from_journal

    def threaded(journal, now_ms):
        t = threading.Thread(target=lambda: journal.research_plans())
        t.start(), t.join()
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(rq, "record_from_journal", threaded)
    tel = run_.run(j, "k", 1)["telemetry"]
    assert tel["steps"][0]["rows_read"]["reason"] == \
        "unmetered_journal_access:concurrent_journal_access"


class _WithProperty(Journal):
    marker = "class-attr"

    @property
    def rows(self):
        return self.query("SELECT id FROM brain_events")


def test_step_receives_the_real_journal_unchanged(tmp_path, monkeypatch):
    src = _fresh(tmp_path)
    j = _WithProperty(src.db_path)
    j.instance_attr = "kept"
    expected = j.rows
    seen = {}
    orig = rq.record_from_journal

    def looking(journal, now_ms):
        seen.update(same=journal is j, rows=journal.rows,
                    marker=journal.marker, attr=journal.instance_attr,
                    cls=type(journal))
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(rq, "record_from_journal", looking)
    tel = run_.run(j, "k", 1)["telemetry"]
    assert seen == {"same": True, "rows": expected, "marker": "class-attr",
                    "attr": "kept", "cls": _WithProperty}
    # the property's query is an ordinary counted read
    assert tel["steps"][0]["rows_read"]["value"] == (
        len(expected) + len(j.strategy_health_rows()))
    assert not set(vars(j)) & set(run_._Meter._NAMES)


def test_instrumentation_is_removed_even_when_a_step_raises(tmp_path,
                                                            monkeypatch):
    j = _fresh(tmp_path)
    j.query_marker = 1
    before = dict(vars(j))
    monkeypatch.setattr(rp, "record_from_journal",
                        lambda journal, now_ms: 1 / 0)
    run_.run(j, "k", 1)
    assert vars(j) == before
    assert type(j).query is Journal.query and "query" not in vars(j)


# ── 6 partial / refused upstream work fabricates nothing downstream ─────
def test_failed_step_stops_the_chain_without_downstream_records(
        tmp_path, monkeypatch):
    j = _fresh(tmp_path)

    def boom(journal, now_ms):
        raise RuntimeError("planner unavailable")
    monkeypatch.setattr(rp, "record_from_journal", boom)
    out = run_.run(j, "k", 1)
    steps, tel = out["receipt"]["steps"], out["telemetry"]["steps"]
    assert [s["status"] for s in steps] == [
        "COMPLETED", "FAILED", "NOT_RUN", "NOT_RUN"]
    assert steps[1]["error"] == {"type": "RuntimeError",
                                 "message": "planner unavailable"}
    assert steps[1]["reason"] == "step_raised"
    assert steps[1]["outcomes"] is None
    assert all(s["outcomes"] is None and s["reason"] == "upstream_step_failed"
               for s in steps[2:])
    assert all(t[m] == {"status": "NOT_MEASURED", "value": None,
                        "unit": u, "source": None, "reason": "step_not_run"}
               for t in tel[2:]
               for m, u in (("elapsed_wall_ns", "ns"), ("rows_read", "rows")))
    assert tel[1]["elapsed_wall_ns"]["status"] == "MEASURED"
    assert len(j.research_questions()) == 1
    assert not (j.research_plans() or j.research_evidence()
                or j.research_results())
    monkeypatch.undo()
    assert run_.load(j)[0]["receipt"] == out["receipt"]


def test_refused_upstream_question_yields_no_downstream(tmp_path):
    j = _fresh(tmp_path)
    rq.record_from_journal(j, now_ms=1)
    [q] = rq.load(j)
    with j._tx() as c:                  # the question's source disappears
        c.execute("DELETE FROM brain_events WHERE id=?",
                  (q["source"]["event_id"],))
    ref = _copy(tmp_path, j, "ref.db")
    direct = _direct(ref, now_ms=2)
    out = run_.run(j, "k", 2)
    steps = out["receipt"]["steps"]
    for s, res, (_n, _m, _s, key) in zip(steps, direct, run_.STEPS):
        assert s["status"] == "COMPLETED"
        assert s["outcomes"] == _serialized(key, res)
    assert [r["question_id"] for r in steps[1]["outcomes"]["refusals"]] == [
        q["question_id"]]
    assert steps[1]["outcomes"]["inserted"] == []
    assert steps[2]["outcomes"] == steps[3]["outcomes"] == {
        "inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    assert not (j.research_plans() or j.research_evidence()
                or j.research_results())
    assert len(run_.load(j)) == 1


def test_empty_journal_run_records_empty_steps(tmp_path):
    j = Journal(tmp_path / "e.db")
    out = run_.run(j, "k", 1)
    assert all(s["outcomes"] == {"inserted": [], "duplicate": [],
                                 "conflict": [], "refusals": []}
               for s in out["receipt"]["steps"])
    assert [t["rows_read"]["value"] for t in out["telemetry"]["steps"]] == [
        0, 0, 0, 0]
    assert len(run_.load(j)) == 1


# ── 7 referenced stored records re-verify on load ───────────────────────
def test_load_reverifies_every_referenced_record(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    seen = []
    for mod, fn in ((rp, "load"), (re_, "load"), (rr, "load"),
                    (rq, "verify_evidence")):
        orig = getattr(mod, fn)

        def spy(*a, _o=orig, _m=mod.__name__, **k):
            seen.append(_m.rsplit(".", 1)[1])
            return _o(*a, **k)
        monkeypatch.setattr(mod, fn, spy)
    [stored] = run_.load(j)
    assert {"research_question", "research_plan", "research_evidence",
            "research_result"} <= set(seen)
    assert stored["receipt"]["run_id"] == run_.run_id("k", 1)


@pytest.mark.parametrize("table", TABLES)
def test_deleted_reference_fails_closed(tmp_path, table):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    with j._tx() as c:
        c.execute(f"DELETE FROM {table}")
    with pytest.raises(run_.ResearchRunError, match="reference_"):
        run_.load(j)


@pytest.mark.parametrize("table", TABLES)
def test_tampered_reference_fails_closed(tmp_path, table):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    with j._tx() as c:
        c.execute(f"UPDATE {table} SET canonical_json="
                  "replace(canonical_json, 's1', 's9')")
    with pytest.raises(run_.ResearchRunError, match="reference_invalid"):
        run_.load(j)


def test_existing_invalid_receipt_blocks_retry(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    with j._tx() as c:
        c.execute("DELETE FROM research_results")
    for mod in (rq, rp, re_, rr):
        monkeypatch.setattr(mod, "record_from_journal",
                            lambda *a, **k: pytest.fail("re-executed"))
    with pytest.raises(run_.ResearchRunError):
        run_.run(j, "k", 1)
    assert not j.research_results()


# ── 8 tampered receipt fails closed ─────────────────────────────────────
def _retext(j, edit, col="canonical_json"):
    r = _stored_row(j)
    v = json.loads(r[col])
    edit(v)
    text = run_.canonical(v)
    digest = ("canonical_sha256" if col == "canonical_json"
              else "telemetry_sha256")
    _store(j, **{col: text, digest: run_._sha(text)})


@pytest.mark.parametrize("edit", [
    lambda v: v.update(extra=1),
    lambda v: v.update(schema="research-run.v2"),
    lambda v: v.update(semantics="x"),
    lambda v: v["inputs"].update(run_key="other"),
    lambda v: v["inputs"].update(recorded_at_ms=True),
    lambda v: v.update(run_id="0" * 64),
    lambda v: v["steps"].pop(),
    lambda v: v["steps"].reverse(),
    lambda v: v["steps"][0].update(status="SUPPORTED"),
    lambda v: v["steps"][0].update(score=1),
    lambda v: v["steps"][0]["outcomes"].update(salience=[]),
    lambda v: v["steps"][0]["outcomes"]["inserted"].append(1),
    lambda v: v["steps"][1]["outcomes"]["inserted"].append("f" * 64),
    lambda v: v["steps"][0]["outcomes"]["refusals"].append({"reason": "x"}),
    lambda v: v["steps"][1].update(status="FAILED", reason="step_raised",
                                   error={"type": "E", "message": "m"},
                                   outcomes=None),
    lambda v: v["steps"][2].update(status="NOT_RUN",
                                   reason="upstream_step_failed",
                                   outcomes=None),
])
def test_tampered_receipt_fails_closed(tmp_path, edit):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    _retext(j, edit)
    with pytest.raises(run_.ResearchRunError):
        run_.load(j)


@pytest.mark.parametrize("edit", [
    lambda v: v.update(extra=1),
    lambda v: v.update(run_id="0" * 64),
    lambda v: v.update(semantics="budget: 10"),
    lambda v: v["steps"][0]["rows_read"].update(value=-1),
    lambda v: v["steps"][0]["rows_read"].update(value=1.5),
    lambda v: v["steps"][0]["rows_read"].update(unit="bytes"),
    lambda v: v["steps"][0]["elapsed_wall_ns"].update(source="estimate"),
    lambda v: v["steps"][0]["elapsed_wall_ns"].update(
        status="NOT_MEASURED", value=None, source=None, reason="x"),
    lambda v: v["steps"][0]["rows_read"].update(status="ESTIMATED"),
    lambda v: v["steps"][0].update(budget=1),
    lambda v: v["steps"].pop(),
])
def test_tampered_telemetry_fails_closed(tmp_path, edit):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    _retext(j, edit, col="telemetry_json")
    with pytest.raises(run_.ResearchRunError):
        run_.load(j)


@pytest.mark.parametrize("cols", [
    {"run_key": "other"},
    {"run_recorded_at_ms": 2},
    {"canonical_sha256": "0" * 64},
    {"schema": "x"},
])
def test_row_projection_is_verified(tmp_path, cols):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    _store(j, **cols)
    with pytest.raises(run_.ResearchRunError, match="row_projection"):
        run_.load(j)


@pytest.mark.parametrize("mangle", [
    lambda t: t + " ",
    lambda t: t.replace('{"inputs"', '{"inputs":{},"inputs"', 1),
    lambda t: "not json",
])
def test_non_canonical_receipt_text_fails_closed(tmp_path, mangle):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    text = mangle(_stored_row(j)["canonical_json"])
    _store(j, canonical_json=text, canonical_sha256=run_._sha(text))
    with pytest.raises(run_.ResearchRunError):
        run_.load(j)


def test_not_run_telemetry_must_say_not_measured(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    monkeypatch.setattr(rq, "record_from_journal",
                        lambda journal, now_ms: 1 / 0)
    run_.run(j, "k", 1)
    monkeypatch.undo()

    def edit(v):
        v["steps"][3]["rows_read"] = {"status": "MEASURED", "value": 0,
                                      "unit": "rows",
                                      "source": run_.ROWS_DEFINITION,
                                      "reason": None}
    _retext(j, edit, col="telemetry_json")
    with pytest.raises(run_.ResearchRunError):
        run_.load(j)


@pytest.mark.parametrize("edit", [
    lambda v: v["steps"][0]["elapsed_wall_ns"].update(value=987654321),
    lambda v: v["steps"][0]["rows_read"].update(value=999999),
])
def test_valid_telemetry_change_without_digest_fails_closed(tmp_path, edit):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    v = json.loads(_stored_row(j)["telemetry_json"])
    edit(v)
    _store(j, telemetry_json=run_.canonical(v))     # digest left unchanged
    with pytest.raises(run_.ResearchRunError, match="row_projection"):
        run_.load(j)


def test_telemetry_digest_is_outside_identity_and_duplicates(tmp_path):
    j = _fresh(tmp_path)
    out = run_.run(j, "k", 1, clock=_clock(3))
    row = run_.row_for(out["receipt"], out["telemetry"])
    assert row["telemetry_sha256"] == run_._sha(row["telemetry_json"])
    other = json.loads(row["telemetry_json"])
    other["steps"][0]["elapsed_wall_ns"]["value"] = 12345
    row2 = run_.row_for(out["receipt"], other)
    assert row2["telemetry_sha256"] != row["telemetry_sha256"]
    assert row2["canonical_sha256"] == row["canonical_sha256"]
    assert row2["run_id"] == row["run_id"] == run_.run_id("k", 1)
    assert j.record_research_run(row2, recorded_at_ms=1) == "duplicate"
    assert _stored_row(j)["telemetry_sha256"] == row["telemetry_sha256"]


@pytest.mark.parametrize("value", ["0" * 64, "", None])
def test_tampered_telemetry_digest_fails_closed(tmp_path, value):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    if value is None:
        with pytest.raises(sqlite3.IntegrityError):
            _store(j, telemetry_sha256=value)
        return
    _store(j, telemetry_sha256=value)
    with pytest.raises(run_.ResearchRunError, match="row_projection"):
        run_.load(j)


# ── 9 no scoring, salience or evaluative behaviour ──────────────────────
_FORBIDDEN = re.compile(
    r"score|salien|rank|priorit|probab|useful|label|threshold|confiden"
    r"|asset|symbol|conclu|support|refut|budget|cap\b|limit|weight|urgen")


def _all_keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _all_keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _all_keys(x)


def test_receipt_carries_no_evaluative_fields(tmp_path):
    j = _fresh(tmp_path)
    out = run_.run(j, "k", 1)
    keys = set(_all_keys(out["receipt"])) | set(_all_keys(out["telemetry"]))
    assert not {k for k in keys if _FORBIDDEN.search(k)}
    assert all(s["status"] in ("COMPLETED", "FAILED", "NOT_RUN")
               for s in out["receipt"]["steps"])


def test_results_stay_inconclusive_and_unchanged_by_the_runner(tmp_path):
    j = _fresh(tmp_path)
    run_.run(j, "k", 1)
    assert [r["status"] for r in rr.load(j)] == ["INCONCLUSIVE"]
    src = Path(run_.__file__).read_text()
    assert not re.search(r"\bSUPPORTED\b|\bREFUTED\b", src)


def test_runner_never_suppresses_questions(tmp_path):
    j = _fresh(tmp_path, verdicts=(W, D, D, W, D))
    ref = _copy(tmp_path, j, "ref.db")
    direct = rq.record_from_journal(ref, now_ms=1)
    out = run_.run(j, "k", 1)
    assert out["receipt"]["steps"][0]["outcomes"]["inserted"] == \
        direct["inserted"]
    assert len(direct["inserted"]) == 2


# ── 10 no live / Attention / Risk / Execution wiring ────────────────────
def test_module_imports_only_contract_modules():
    src = Path(run_.__file__).read_text()
    mods, names = set(), set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "hashlib", "json", "threading", "time",
                    "trader.cognition"}
    assert names == {"annotations", "research_question", "research_plan",
                     "research_evidence", "research_result"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(run_.__file__).resolve().parents[1]
    pat = re.compile(r"research_run\b|record_research_run")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_run.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive; research_bank.py is the
    # offline bank filer, guarded by its own test
    assert callers == ["cognition/research_bank.py", "core/journal.py"]
    assert "research_run import" not in (root / "core/journal.py").read_text()


def test_run_writes_only_research_tables(tmp_path):
    j = _fresh(tmp_path)
    other = ("brain_events", "decisions", "trades", "strategies")
    before = {t: j.query(f"SELECT * FROM {t}") for t in other}
    run_.run(j, "k", 1)
    run_.load(j)
    assert {t: j.query(f"SELECT * FROM {t}") for t in other} == before


def test_legacy_journal_gains_the_table_and_stays_readable(tmp_path):
    p = tmp_path / "legacy.db"
    Journal(p)
    with sqlite3.connect(p) as c:
        c.execute("DROP TABLE research_runs")
    j = Journal(p)
    assert j.research_runs() == [] and run_.load(j) == []
