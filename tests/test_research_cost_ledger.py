"""SDD-STAGE-3-RESEARCH-COST-LEDGER-V1: read-only research-cost-ledger.v1 over
investigation-resource-receipt.v1 and research-run-telemetry.v1. Synthetic;
no network, LLM, Attention, Kernel, Risk or Execution."""
import ast
import json
import random
import re
import sqlite3
import time
from pathlib import Path

import pytest

from tests.test_investigation_state_feedback import publish
from tests.test_strategy_decay_research_run import _clock, _fresh
from trader.cognition import investigation as I
from trader.cognition import research_cost as rc
from trader.cognition import research_plan as rp
from trader.cognition import research_run as run_
from trader.observability import investigation as C


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def receipts_db(tmp_path):
    source, dest = tmp_path / "attention.db", tmp_path / "investigation.db"
    now = int(time.time() * 1000) // I.TF * I.TF + 60_000
    publish(source, now, "s1", [0, 1])
    assert C.step(source, dest, now)["registered"] == 2
    return dest


def _family(ledger, name):
    [f] = [f for f in ledger["families"] if f["family"] == name]
    return f


def _group(fam, measurement):
    [g] = [g for g in fam["groups"] if g["measurement"] == measurement]
    return g


def _entries(fam, **match):
    return [e for e in fam["entries"]
            if all(e[k] == v if k != "step" else e["context"]["step"] == v
                   for k, v in match.items())]


def _runs(tmp_path, n=1):
    j = _fresh(tmp_path)
    for i in range(n):
        run_.run(j, f"k{i}", 10 + i, clock=_clock(7))
    return j


def _unknown_receipt_row(row):
    body = json.loads(row["payload"])
    for k in ("llm_calls", "venue_requests"):
        body["measurements"][k] = None
    body["unknown_units"] = {"llm_calls": "unclassified_egress",
                             "venue_requests": "unclassified_egress"}
    body["measurement_status"] = "UNKNOWN"
    body["egress_events"] = {"socket.connect": 1}   # the source's only path
    return dict(row, payload=I.encode(body))


# 1 ── a measured investigation receipt appears exactly ──────────────────
def test_measured_receipt_appears_exactly(receipts_db):
    stored = C.receipts(receipts_db)
    rows = rc.read_receipt_rows(receipts_db)
    fam = _family(rc.from_sources(investigation_path=receipts_db),
                  rc.RECEIPT_FAMILY)
    assert fam["source_state"] == "READ" and fam["refused_sources"] == []
    assert len(fam["entries"]) == len(stored) * len(C.UNITS)
    for r, row in zip(stored, rows):
        es = _entries(fam, source_id=r["receipt_id"])
        assert [e["measurement"] for e in es] == list(C.UNITS)
        for e in es:
            k = e["measurement"]
            assert (e["state"], e["value"], e["reason"]) == (
                "MEASURED", r["measurements"][k], None)
            assert type(e["value"]) is int
            assert e["unit_definition"] == C.UNITS[k]
            assert e["provenance"] == {
                "table": "resource_receipts", "receipt_id": r["receipt_id"],
                "payload_sha256": __import__("hashlib").sha256(
                    row["payload"].encode()).hexdigest()}
            assert e["context"] == {
                "case_id": r["case_id"], "stage": r["stage"],
                "execution_id": r["execution_id"],
                "observed_ms": r["observed_ms"], "outcome": r["outcome"]}
    # the derived elapsed_ms is not a second measurement
    assert all(e["measurement"] != "elapsed_ms" for e in fam["entries"])


# 2, 3 ── run elapsed_wall_ns and rows_read appear exactly ─────────────────
def test_measured_run_telemetry_appears_exactly(tmp_path):
    j = _runs(tmp_path)
    [stored] = run_.load(j)
    [row] = j.research_runs()
    fam = _family(rc.from_sources(journal=j), rc.RUN_FAMILY)
    assert fam["refused_sources"] == []
    for t, s in zip(stored["telemetry"]["steps"], stored["receipt"]["steps"]):
        for name, unit, definition in rc.RUN_MEASURES:
            [e] = _entries(fam, measurement=name, step=t["step"])
            assert (e["state"], e["value"], e["unit"], e["reason"]) == (
                t[name]["status"], t[name]["value"], t[name]["unit"],
                t[name]["reason"])
            assert e["unit_definition"] == t[name]["source"] == definition
            assert e["context"] == {"step": s["step"],
                                    "step_status": s["status"]}
            assert e["provenance"] == {
                "table": "research_runs", "run_id": row["run_id"],
                "canonical_sha256": row["canonical_sha256"],
                "telemetry_sha256": row["telemetry_sha256"],
                "run_recorded_at_ms": 10}
    assert [e["value"] for e in _entries(fam, measurement="elapsed_wall_ns")
            ] == [7, 7, 7, 7]
    rows_read = [t["rows_read"]["value"]
                 for t in stored["telemetry"]["steps"]]
    assert all(isinstance(v, int) and v > 0 for v in rows_read)
    assert [e["value"] for e in _entries(fam, measurement="rows_read")
            ] == rows_read


# 4 ── NOT_MEASURED / UNKNOWN reasons are preserved exactly ───────────────
def test_not_measured_reason_preserved_exactly(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    orig = rp.record_from_journal

    def peeking(journal, now_ms):
        journal._conn().execute("SELECT 1").fetchall()
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(rp, "record_from_journal", peeking)
    run_.run(j, "k", 1)
    fam = _family(rc.from_sources(journal=j), rc.RUN_FAMILY)
    [e] = _entries(fam, measurement="rows_read", step="plan")
    assert (e["state"], e["value"], e["reason"]) == (
        "NOT_MEASURED", None,
        "unmetered_journal_access:direct_connection_access")
    assert _group(fam, "rows_read")["state"] == "NOT_ESTABLISHED"
    assert _group(fam, "rows_read")["reason"] == "unmeasured_entries_present"
    assert _group(fam, "rows_read")["value"] is None


def test_not_run_steps_keep_step_not_run(tmp_path, monkeypatch):
    j = _fresh(tmp_path)

    def boom(journal, now_ms):
        raise RuntimeError("planner unavailable")
    monkeypatch.setattr(rp, "record_from_journal", boom)
    run_.run(j, "k", 1, clock=_clock(3))
    fam = _family(rc.from_sources(journal=j), rc.RUN_FAMILY)
    got = [(e["context"]["step"], e["context"]["step_status"],
            e["measurement"], e["state"], e["value"], e["reason"])
           for e in fam["entries"]]
    assert got[2][:4] == ("plan", "FAILED", "elapsed_wall_ns", "MEASURED")
    for step in ("evidence", "result"):
        for m in ("elapsed_wall_ns", "rows_read"):
            assert (step, "NOT_RUN", m, "NOT_MEASURED", None,
                    "step_not_run") in got
    assert _group(fam, "elapsed_wall_ns")["state"] == "NOT_ESTABLISHED"


def test_receipt_unknown_reason_preserved_exactly(receipts_db):
    rows = [_unknown_receipt_row(r) for r in rc.read_receipt_rows(receipts_db)]
    fam = _family(rc.build(rows), rc.RECEIPT_FAMILY)
    assert fam["refused_sources"] == []
    for k in ("llm_calls", "venue_requests"):
        for e in _entries(fam, measurement=k):
            assert (e["state"], e["value"], e["reason"]) == (
                "UNKNOWN", None, "unclassified_egress")
        g = _group(fam, k)
        assert (g["state"], g["value"], g["unknown"]) == (
            "NOT_ESTABLISHED", None, len(rows))
    assert _group(fam, "wall_ns")["state"] == "SUMMED"


# 5 ── missing / rolled-back / unmeasured cost stays UNKNOWN ────────────────
def test_unrecorded_cost_is_unknown_never_zero(tmp_path):
    ledger = rc.build([], [])
    for fam in ledger["families"]:
        assert fam["source_state"] == "READ" and fam["entries"] == []
        for g in fam["groups"]:
            assert (g["state"], g["value"], g["reason"]) == (
                "NOT_ESTABLISHED", None, "no_entries")
        assert fam["not_covered"] and all(
            n["state"] == "UNKNOWN" and n["reason"] for n in fam["not_covered"])
    rec = {n["name"]: n for n in _family(ledger, rc.RECEIPT_FAMILY)
           ["not_covered"]}
    assert rec["failed_or_rolled_back_attempt_cost"] == {
        "name": "failed_or_rolled_back_attempt_cost", "state": "UNKNOWN",
        "reason": "declared_not_covered_by_investigation-resource-receipt.v1"}
    assert set(C.NOT_COVERED) < set(rec)
    assert "receipts_of_retention_pruned_cases" in rec
    runs = {n["name"] for n in _family(ledger, rc.RUN_FAMILY)["not_covered"]}
    assert {"unstored_run_attempts",
            "research_work_outside_offline_runner"} <= runs
    assert ledger["total_research_cost"] == {
        "state": "NOT_ESTABLISHED", "value": None,
        "reasons": list(rc.TOTAL_REASONS)}


def test_unread_and_unreadable_sources_are_not_empty(tmp_path):
    ledger = rc.from_sources(investigation_path=tmp_path / "absent.db")
    rec, runs = (_family(ledger, rc.RECEIPT_FAMILY),
                 _family(ledger, rc.RUN_FAMILY))
    assert rec["source_state"] == "UNAVAILABLE"
    assert rec["source_reason"] == "source_unreadable:OperationalError"
    assert (runs["source_state"], runs["source_reason"]) == (
        "NOT_READ", "source_not_supplied")
    assert rec["groups"] == runs["groups"] == []
    assert ledger["total_research_cost"]["value"] is None
    assert ledger["total_research_cost"]["reasons"][-2:] == [
        "source_unavailable:investigation-resource-receipt.v1",
        "source_not_read:research-run-telemetry.v1"]
    assert not (tmp_path / "absent.db").exists()


# 6 ── incompatible units are never summed ─────────────────────────────────
def test_families_and_units_are_never_combined(tmp_path, receipts_db):
    j = _runs(tmp_path, n=2)
    ledger = rc.from_sources(journal=j, investigation_path=receipts_db)
    rec, runs = (_family(ledger, rc.RECEIPT_FAMILY),
                 _family(ledger, rc.RUN_FAMILY))
    for fam in (rec, runs):
        for g in fam["groups"]:
            es = [e for e in fam["entries"]
                  if e["measurement"] == g["measurement"]]
            assert {(e["family"], e["unit"], e["unit_definition"])
                    for e in es} == {(fam["family"], g["unit"],
                                      g["unit_definition"])}
            assert g["family"] == fam["family"]
            assert g["state"] == "SUMMED"
            assert g["value"] == sum(e["value"] for e in es)
    # same unit, different clock/scope: separate groups, never one sum
    assert _group(rec, "wall_ns")["unit"] == _group(
        runs, "elapsed_wall_ns")["unit"] == "ns"
    assert _group(rec, "wall_ns")["unit_definition"] != _group(
        runs, "elapsed_wall_ns")["unit_definition"]
    assert _group(rec, "wall_ns")["value"] == sum(
        r["measurements"]["wall_ns"] for r in C.receipts(receipts_db))
    assert _group(runs, "elapsed_wall_ns")["value"] == 2 * 4 * 7
    assert len({(g["family"], g["measurement"]) for f in ledger["families"]
                for g in f["groups"]}) == len(C.UNITS) + len(rc.RUN_MEASURES)
    assert ledger["total_research_cost"]["value"] is None
    assert "incommensurable_source_families" in ledger[
        "total_research_cost"]["reasons"]


# 7 ── deterministic ordering and serialization ───────────────────────────
def test_deterministic_ordering_and_serialization(tmp_path, receipts_db):
    j = _runs(tmp_path, n=3)
    receipts, runs = rc.read_receipt_rows(receipts_db), j.research_runs()
    base = rc.build(receipts, runs)
    text = rc.canonical(base)
    assert json.loads(text) == base and rc.canonical(json.loads(text)) == text
    for seed in range(5):
        a, b = list(receipts), list(runs)
        random.Random(seed).shuffle(a)
        random.Random(seed + 9).shuffle(b)
        again = rc.build(a, b)
        assert rc.canonical(again) == text
        assert rc.ledger_sha256(again) == rc.ledger_sha256(base)
    assert rc.from_sources(journal=j, investigation_path=receipts_db) == base
    ms = [e["provenance"]["run_recorded_at_ms"]
          for e in _family(base, rc.RUN_FAMILY)["entries"]]
    assert ms == sorted(ms)


# 8 ── tampered sources fail closed where the contract can verify ─────────
def test_tampered_run_telemetry_is_refused(tmp_path):
    j = _runs(tmp_path, n=2)
    rows = j.research_runs()
    tel = json.loads(rows[0]["telemetry_json"])
    tel["steps"][0]["elapsed_wall_ns"]["value"] = 1
    rows[0] = dict(rows[0], telemetry_json=run_.canonical(tel))
    fam = _family(rc.build(None, rows), rc.RUN_FAMILY)
    assert fam["refused_sources"] == [
        {"source_id": rows[0]["run_id"], "reason": "run_invalid:row_projection"}]
    assert {e["source_id"] for e in fam["entries"]} == {rows[1]["run_id"]}
    for g in fam["groups"]:
        assert (g["state"], g["value"], g["reason"]) == (
            "NOT_ESTABLISHED", None, "refused_source_records_present")


@pytest.mark.parametrize("tamper, reason", [
    (lambda r, b: (dict(r, payload=r["payload"] + " "), None),
     "payload_not_canonical"),
    (lambda r, b: (r, dict(b, case_id="other")), "receipt_id_mismatch"),
    (lambda r, b: (dict(r, recorded_ms=r["recorded_ms"] + 1), None),
     "row_binding"),
    (lambda r, b: (r, dict(b, measurements=dict(b["measurements"],
                                                wall_ns=b["measurements"]["wall_ns"] + 1))),
     "elapsed_ms_mismatch"),
    (lambda r, b: (r, dict(b, measurements=dict(b["measurements"],
                                                cpu_ns=-1))),
     "measurement_shape"),
    (lambda r, b: (r, dict(b, resource_bound=10)), "compliance_contract"),
    (lambda r, b: (r, dict(b, coverage="everything")), "coverage_contract"),
    (lambda r, b: (dict(r, payload="{"), None), "payload_not_json"),
])
def test_tampered_receipt_is_refused(receipts_db, tamper, reason):
    rows = rc.read_receipt_rows(receipts_db)
    row, body = tamper(rows[0], json.loads(rows[0]["payload"]))
    if body is not None:
        row = dict(row, payload=I.encode(body))
    fam = _family(rc.build([row] + rows[1:]), rc.RECEIPT_FAMILY)
    assert fam["refused_sources"] == [
        {"source_id": rows[0]["id"], "reason": f"receipt_invalid:{reason}"}]
    assert rows[0]["id"] not in {e["source_id"] for e in fam["entries"]}
    assert all(g["value"] is None for g in fam["groups"])


def _rekeyed(body):
    """Recompute receipt_id so only the targeted check can fail."""
    ident = {k: body[k] for k in ("schema_version", "execution_id",
                                  "case_id", "stage", "work")}
    return dict(body, receipt_id=C.digest(ident))


def _without(body, key):
    return {k: v for k, v in body.items() if k != key}


def _measure(body, **kw):
    return dict(body, measurements=dict(body["measurements"], **kw))


@pytest.mark.parametrize("mutate, row_mutate, reason", [
    (lambda b: _without(b, "resource_bound"), None, "receipt_keys"),
    (lambda b: dict(b, extra="x"), None, "receipt_keys"),
    (lambda b: dict(b, egress_events=[]), None, "egress_shape"),
    (lambda b: dict(b, egress_events={"socket.connect": True}), None,
     "egress_shape"),
    (lambda b: dict(b, egress_events={"not.an.audit.event": 1}), None,
     "egress_shape"),
    (lambda b: _rekeyed(dict(b, work=dict(b["work"],
                                          previous_update_id=True))), None,
     "work_shape"),
    (lambda b: _rekeyed(dict(b, work=dict(b["work"], extra=None))), None,
     "work_shape"),
    (lambda b: dict(b, observed_ms=float(b["observed_ms"])), None,
     "field_types"),
    (lambda b: b, lambda r: dict(r, recorded_ms=float(r["recorded_ms"])),
     "row_binding"),
    (lambda b: _rekeyed(dict(b, stage="made_up")),
     lambda r: dict(r, stage="made_up"), "field_types"),
    (lambda b: dict(b, symbol=7), None, "field_types"),
    (lambda b: dict(b, elapsed_ms=True), None, "elapsed_ms_mismatch"),
    (lambda b: dict(b, elapsed_ms=int(b["elapsed_ms"])), None,
     "elapsed_ms_mismatch"),
    (lambda b: _measure(b, llm_calls=4), None, "measurement_semantics"),
    (lambda b: _measure(b, venue_requests=1), None, "measurement_semantics"),
    (lambda b: _measure(b, cpu_ns=True), None, "measurement_shape"),
    (lambda b: dict(_measure(b, llm_calls=None, venue_requests=None),
                    unknown_units={"llm_calls": "unclassified_egress",
                                   "venue_requests": "unclassified_egress"},
                    measurement_status="UNKNOWN"), None,
     "measurement_semantics"),            # UNKNOWN egress without an event
    (lambda b: dict(_measure(b, payload_bytes=None),
                    unknown_units={"payload_bytes": "clock_read_failed"},
                    measurement_status="UNKNOWN"), None,
     "measurement_semantics"),            # ledger counter cannot be unknown
    (lambda b: dict(_measure(b, cpu_ns=None),
                    unknown_units={"cpu_ns": "invented_reason"},
                    measurement_status="UNKNOWN"), None,
     "measurement_semantics"),
    (lambda b: dict(b, compliance_reason="owner_bound"), None,
     "compliance_contract"),
])
def test_malformed_receipt_shape_is_refused(receipts_db, mutate, row_mutate,
                                            reason):
    rows = rc.read_receipt_rows(receipts_db)
    body = mutate(json.loads(rows[0]["payload"]))
    row = dict(rows[0], payload=I.encode(body))
    if body.get("receipt_id") and body["receipt_id"] != rows[0]["id"]:
        row["id"] = body["receipt_id"]
    if row_mutate:
        row = row_mutate(row)
    fam = _family(rc.build([row] + rows[1:]), rc.RECEIPT_FAMILY)
    assert fam["refused_sources"] == [
        {"source_id": row["id"], "reason": f"receipt_invalid:{reason}"}]
    assert row["id"] not in {e["source_id"] for e in fam["entries"]}
    assert all(g["state"] == "NOT_ESTABLISHED" and g["value"] is None
               for g in fam["groups"])


def test_valid_egress_unknown_receipt_is_accepted(receipts_db):
    rows = rc.read_receipt_rows(receipts_db)
    body = dict(json.loads(rows[0]["payload"]),
                egress_events={"socket.connect": 2})
    body = dict(_measure(body, llm_calls=None, venue_requests=None),
                unknown_units={"llm_calls": "unclassified_egress",
                               "venue_requests": "unclassified_egress"},
                measurement_status="UNKNOWN")
    fam = _family(rc.build([dict(rows[0], payload=I.encode(body))]),
                  rc.RECEIPT_FAMILY)
    assert fam["refused_sources"] == []
    assert _group(fam, "llm_calls")["state"] == "NOT_ESTABLISHED"
    assert _group(fam, "wall_ns")["state"] == "SUMMED"


def test_source_semantics_literals_match_the_receipt_writer():
    text = Path(C.__file__).read_text()
    for literal in ("clock_read_failed", "non_monotonic_reading",
                    "unclassified_egress",
                    "no_prospectively_frozen_resource_bound"):
        assert f'"{literal}"' in text


def test_consistent_measurement_rewrite_is_a_documented_limit(receipts_db):
    rows = rc.read_receipt_rows(receipts_db)
    body = _measure(json.loads(rows[0]["payload"]), cpu_ns=1)
    ledger = rc.build([dict(rows[0], payload=I.encode(body))] + rows[1:])
    # identity excludes measurements: accepted, but the fingerprint moves
    fam = _family(ledger, rc.RECEIPT_FAMILY)
    assert fam["refused_sources"] == []
    assert rc.ledger_sha256(ledger) != rc.ledger_sha256(rc.build(rows))
    assert "does not authenticate" in rc.__doc__


def test_unreadable_journal_is_unavailable_and_receipts_stay_usable(
        receipts_db):
    class Broken:
        def research_runs(self, run_id=None):
            raise sqlite3.OperationalError("database is locked")
    ledger = rc.from_sources(journal=Broken(), investigation_path=receipts_db)
    runs, rec = (_family(ledger, rc.RUN_FAMILY),
                 _family(ledger, rc.RECEIPT_FAMILY))
    assert (runs["source_state"], runs["source_reason"]) == (
        "UNAVAILABLE", "source_unreadable:OperationalError")
    assert runs["entries"] == runs["groups"] == []
    assert rec["source_state"] == "READ" and rec["refused_sources"] == []
    assert _group(rec, "wall_ns")["state"] == "SUMMED"
    assert ledger["total_research_cost"]["reasons"][-1] == (
        "source_unavailable:research-run-telemetry.v1")


@pytest.mark.parametrize("payload, reason", [
    (lambda b: I.encode(b).replace('"elapsed_ms":', '"elapsed_ms":1e999,"x":',
                                   1), "payload_not_canonical"),
    (lambda b: I.encode(dict(b, elapsed_ms=None)).replace(
        '"elapsed_ms":null', '"elapsed_ms":1e999'), "payload_not_canonical"),
    (lambda b: I.encode(dict(b, measurements=dict(
        b["measurements"], wall_ns=10 ** 400))), "elapsed_ms_mismatch"),
    (lambda b: "[" * 100_000 + "]" * 100_000, "payload_not_json"),
])
def test_numeric_edge_receipt_refuses_only_its_row(receipts_db, payload,
                                                   reason):
    rows = rc.read_receipt_rows(receipts_db)
    bad = dict(rows[0], payload=payload(json.loads(rows[0]["payload"])))
    fam = _family(rc.build([bad] + rows[1:]), rc.RECEIPT_FAMILY)
    assert fam["refused_sources"] == [
        {"source_id": rows[0]["id"], "reason": f"receipt_invalid:{reason}"}]
    valid = {e["source_id"] for e in fam["entries"]}
    assert valid == {r["id"] for r in rows[1:]} and valid
    for g in fam["groups"]:
        assert (g["state"], g["value"], g["reason"]) == (
            "NOT_ESTABLISHED", None, "refused_source_records_present")


@pytest.mark.parametrize("field, text, reason", [
    ("telemetry_json", '{"a":1e999}', "malformed_source:ValueError"),
    ("canonical_json", '{"a":1e999}', "malformed_source:ValueError"),
    ("canonical_json", "[" * 100_000 + "]" * 100_000,
     "malformed_source:RecursionError"),
])
def test_numeric_edge_run_refuses_only_its_row(tmp_path, field, text, reason):
    rows = _runs(tmp_path, n=2).research_runs()
    bad = dict(rows[0], **{field: text})
    fam = _family(rc.build(None, [bad, rows[1]]), rc.RUN_FAMILY)
    assert fam["refused_sources"] == [
        {"source_id": rows[0]["run_id"], "reason": f"run_invalid:{reason}"}]
    assert {e["source_id"] for e in fam["entries"]} == {rows[1]["run_id"]}
    assert all(g["state"] == "NOT_ESTABLISHED" and g["value"] is None
               for g in fam["groups"])


def test_duplicate_source_ids_are_refused(receipts_db):
    rows = rc.read_receipt_rows(receipts_db)
    fam = _family(rc.build(rows + rows[:1]), rc.RECEIPT_FAMILY)
    assert fam["refused_sources"] == [
        {"source_id": rows[0]["id"],
         "reason": "receipt_invalid:duplicate_source_id"}] * 2


# 9 ── no per-result attribution ──────────────────────────────────────────
def test_no_per_result_attribution(tmp_path):
    j = _runs(tmp_path)
    [stored] = run_.load(j)
    fam = _family(rc.from_sources(journal=j), rc.RUN_FAMILY)
    text = rc.canonical(fam)
    ids = [i for s in stored["receipt"]["steps"]
           for k in ("inserted", "duplicate", "conflict")
           for i in s["outcomes"][k]]
    assert ids and not any(i in text for i in ids)
    for e in fam["entries"]:
        assert set(e) == {"family", "source_id", "measurement", "state",
                          "value", "unit", "unit_definition", "reason",
                          "provenance", "context"}
        assert set(e["context"]) == {"step", "step_status"}
        assert e["source_id"] == stored["receipt"]["run_id"]
    for word in ("result_id", "question_id", "plan_id", "evidence_id",
                 "bank_object_id"):
        assert word not in text


# 10 ── no budget / efficiency / priority / live authority ────────────────
FORBIDDEN = re.compile(r"budget|cap\b|bound|efficien|roi\b|return_|priority"
                       r"|salience|usefulness|score|rank|limit|allow")


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _keys(x)


def test_no_budget_efficiency_priority_or_authority_fields(tmp_path,
                                                           receipts_db):
    ledger = rc.from_sources(journal=_runs(tmp_path),
                             investigation_path=receipts_db)
    assert not [k for k in _keys(ledger) if FORBIDDEN.search(k.lower())]
    assert set(ledger) == {"schema", "builder_id", "semantics",
                           "total_research_cost", "families"}
    assert ledger["schema"] == "research-cost-ledger.v1"


def test_module_imports_nothing_live():
    tree = ast.parse(Path(rc.__file__).read_text())
    mods, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            names |= {a.name for a in node.names}
    assert mods == {"__future__", "hashlib", "json", "sqlite3", "collections",
                    "contextlib", "pathlib", "trader.cognition",
                    "trader.observability"}
    assert names == {"annotations", "Counter", "closing", "Path",
                     "research_run", "investigation"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(rc.__file__).resolve().parents[1]
    pat = re.compile(r"\bresearch_cost\b|research-cost-ledger")
    callers = [str(p.relative_to(root)) for p in root.rglob("*.py")
               if p.name != "research_cost.py"
               and pat.search(p.read_text(errors="ignore"))]
    assert callers == []


def test_ledger_is_read_only(tmp_path, receipts_db):
    j = _runs(tmp_path)
    tables = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    before = {t: j.query(f"SELECT * FROM {t}") for t in tables}
    receipt_bytes = receipts_db.read_bytes()
    rc.from_sources(journal=j, investigation_path=receipts_db)
    assert {t: j.query(f"SELECT * FROM {t}") for t in tables} == before
    assert receipts_db.read_bytes() == receipt_bytes
