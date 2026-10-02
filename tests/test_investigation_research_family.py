"""LUFFY-INVESTIGATION-RESEARCH-FAMILY-R1: investigation_volume_anomaly.

source candles -> WorldModel -> Attention -> investigation(volume_anomaly)
-> Q -> P -> E -> R -> Research Bank, with exact stored ids. Synthetic; no
network, LLM or trading.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from trader.cognition import investigation as I
from trader.observability import investigation as C
from trader.observability import intelligence_trace as T
from trader.observability import investigation_research as R
from tests.test_attention_telemetry import frames
from tests.test_intelligence_spine import publish, scan_of, strip_world, table, LEGACY, WORLD
from tests.test_investigation_state_feedback import paths  # noqa: F401  (fixture)

TARGET_VOLUME = {"same_direction": 10_000, "normalization": 103, "opposite_direction": 1}


def registered(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    assert C.step(src, dest, now)["registered"] == 1
    (iid, payload), = table(dest, "SELECT id,payload FROM cases")
    return iid, I.investigation_from_dict(json.loads(payload))


def measured(paths, path):  # noqa: F811
    """Register S0's volume anomaly, then observe its exact target window."""
    src, dest, _now = paths
    iid, case = registered(paths)
    end = case.measurement.deadline_ms
    data = frames(6, end + 1)
    data["S0/USDT"]["4h"].loc[25:29, "volume"] = TARGET_VOLUME[path]   # the 5 target bars
    publish(src, end + 1, sid="s2", spikes=(), data=data)
    C.step(src, dest, end + 1)
    return iid, end


def last_update(dest, iid):
    (payload,), = table(dest, "SELECT payload FROM updates WHERE case_id=? "
                              "ORDER BY observed_ms DESC,rowid DESC LIMIT 1", iid)
    return I.update_from_dict(json.loads(payload))


def records(dest):
    return table(dest, "SELECT record_type,record_id,record_key,canonical_json "
                       "FROM investigation_research_records ORDER BY rowid")


# ── measured paths: exactly one registered path supported ───────────────────

@pytest.mark.parametrize("path", ["same_direction", "normalization", "opposite_direction"])
def test_measured_case_supports_exactly_its_registered_path(paths, path):  # noqa: F811
    _src, dest, _ = paths
    iid, end = measured(paths, path)
    u = last_update(dest, iid)
    assert u.evidence.status == "measured" and dict(u.assessment)[path] == "compatible"
    out = R.run(dest, iid, recorded_at_ms=end + 2)
    assert out["status"] == "OK" and out["result_status"] == "SUPPORTED"
    assert out["supported_path"] == path
    ch = R.chain(dest, iid)
    result = ch["runs"][-1]["result"]
    assert [(p["hypothesis"], p["status"]) for p in result["paths"]] == [
        (p, "SUPPORTED" if p == path else "REFUTED") for p in R.PATHS]
    assert result["predictive_edge_established"] is False and result["trading_authority"] == "NONE"
    assert result["meaning"] == R.SUPPORTED_MEANING
    ev = ch["runs"][-1]["evidence"]["measurement"]
    assert ev["score"] == u.evidence.score and ev["outcome_evidence_id"] == u.evidence.evidence_id
    assert ev["threshold"] == 2.0 == I.CATALOG["thresholds"]["volume_anomaly"]
    bank = ch["runs"][-1]["bank"]
    assert [i["hypothesis"] for i in bank["supporting_evidence"]["items"]] == [path]
    assert {i["hypothesis"] for i in bank["contradictory_evidence"]["items"]} == set(R.PATHS) - {path}


def test_question_is_the_cases_observable_question_bound_to_exact_ids(paths):  # noqa: F811
    src, dest, now = paths
    iid, case = registered(paths)
    R.run(dest, iid, recorded_at_ms=now)
    q = R.chain(dest, iid)["question"]
    assert q["question"] == I.QUESTIONS["volume_anomaly"] == case.question
    assert "profit" not in q["question"].lower() and "trade" not in q["question"].lower()
    s = q["source"]
    assert (s["investigation_id"], s["state_id"], s["catalog_id"]) == (
        iid, case.state.state_id, I.CATALOG_ID)
    ctx = C.context_for(dest, iid)
    assert s["registration_context_id"] == ctx.context_id
    (trace_id,), = table(dest, "SELECT id FROM intelligence_traces WHERE investigation_id=?", iid)
    assert s["trace_id"] == trace_id
    scan = scan_of(src)
    assert q["context"]["world_model"]["model_id"] == scan["world_model"]["model_id"]
    assert q["context"]["attention"]["scan_id"] == scan["scan_id"]
    assert [a["name"] for a in q["protocol"]["alternatives"]] == list(R.PATHS)
    assert (q["protocol"]["baseline_bars"], q["protocol"]["horizon_bars"]) == (20, 5)
    plan = R.chain(dest, iid)["plan"]
    assert [x["hypothesis"] for x in plan["sections"]] == [*R.PATHS, R.PARTICIPANT]
    assert plan["sections"][-1]["status"] == "UNAVAILABLE" and plan["sections"][-1]["routes"] == []
    assert {r["store"] for x in plan["sections"][:3] for r in x["routes"]} == {
        "investigation.updates", "investigation.inputs"}


# ── absence of evidence is never REFUTED ────────────────────────────────────

def test_unresolved_case_is_inconclusive_not_refuted(paths):  # noqa: F811
    _src, dest, now = paths
    iid, _ = registered(paths)
    out = R.run(dest, iid, recorded_at_ms=now)
    assert out["result_status"] == "INCONCLUSIVE" and out["supported_path"] is None
    r = R.chain(dest, iid)["runs"][-1]["result"]
    assert r["status_reason"] == "investigation_unresolved"
    assert {p["status"] for p in r["paths"]} == {"NOT_ASSESSED"}
    bank = R.chain(dest, iid)["runs"][-1]["bank"]
    assert bank["supporting_evidence"]["status"] == bank["contradictory_evidence"]["status"] == "NOT_CLASSIFIED"


def test_not_testable_case_is_inconclusive_not_refuted(paths):  # noqa: F811
    _src, dest, _ = paths
    iid, case = registered(paths)
    late = case.measurement.expires_ms + 1        # no target data ever arrived
    C.step(_src, dest, late)
    assert last_update(dest, iid).evidence.status == "not_testable"
    out = R.run(dest, iid, recorded_at_ms=late)
    assert out["result_status"] == "INCONCLUSIVE"
    r = R.chain(dest, iid)["runs"][-1]["result"]
    assert r["status_reason"] == "investigation_not_testable:missing_data_expired"
    assert {p["status"] for p in r["paths"]} == {"NOT_ASSESSED"}


# ── fail closed ─────────────────────────────────────────────────────────────

def _edit(dest, sql, *args):
    with sqlite3.connect(dest) as db:
        db.execute(sql, args)


def test_tampered_investigation_is_refused(paths):  # noqa: F811
    _src, dest, now = paths
    iid, _ = registered(paths)
    (payload,), = table(dest, "SELECT payload FROM cases WHERE id=?", iid)
    d = json.loads(payload)
    d["state"]["transitions"] = ["volume_anomaly:negative"]
    _edit(dest, "UPDATE cases SET payload=? WHERE id=?", I.encode(d), iid)
    out = R.run(dest, iid, recorded_at_ms=now)
    assert out == {"status": "REFUSED", "reason": "state_identity_mismatch", "investigation_id": iid}
    assert records(dest) == []


def test_other_investigation_family_is_refused():
    inv = type("X", (), {})()
    inv.primary_trigger = "correlation_change"
    inv.measurement = type("M", (), {"family": "correlation_change"})()
    inv.state = None
    with pytest.raises(R.ResearchRefused, match="unsupported_investigation_family"):
        R.verify_case(inv)


def test_tampered_terminal_update_is_refused(paths):  # noqa: F811
    _src, dest, _ = paths
    iid, end = measured(paths, "same_direction")
    u = last_update(dest, iid)
    d = json.loads(table(dest, "SELECT payload FROM updates WHERE id=?", u.event_id)[0][0])
    d["evidence"]["score"] = -50.0
    _edit(dest, "UPDATE updates SET payload=? WHERE id=?", I.encode(d), u.event_id)
    assert R.run(dest, iid, recorded_at_ms=end + 2)["reason"] == "outcome_evidence_mismatch"
    assert records(dest) == []


def test_wrong_target_version_is_refused(paths):  # noqa: F811
    _src, dest, _ = paths
    iid, end = measured(paths, "same_direction")
    vid = last_update(dest, iid).evidence.target_versions[0][2]
    d = json.loads(table(dest, "SELECT payload FROM inputs WHERE id=?", vid)[0][0])
    d["candle"]["volume"] = 7.0
    _edit(dest, "UPDATE inputs SET payload=? WHERE id=?", I.encode(d), vid)
    assert R.run(dest, iid, recorded_at_ms=end + 2)["reason"] == "outcome_evidence_mismatch"
    assert records(dest) == []


def test_stored_research_records_are_immutable_and_verified(paths):  # noqa: F811
    _src, dest, _ = paths
    iid, end = measured(paths, "opposite_direction")
    R.run(dest, iid, recorded_at_ms=end + 2)
    with pytest.raises(sqlite3.IntegrityError):
        _edit(dest, "UPDATE investigation_research_records SET canonical_json='{}'")
    with pytest.raises(sqlite3.IntegrityError):
        _edit(dest, "DELETE FROM investigation_research_records")
    # A forged record whose content does not re-derive is refused on read.
    e = R.chain(dest, iid)["runs"][-1]["evidence"]
    forged = R._with_id({k: v for k, v in dict(e, measurement=dict(
        e["measurement"], score=0.0)).items() if k != "evidence_id"}, "evidence_id")
    with sqlite3.connect(dest) as db:
        db.execute("INSERT INTO investigation_research_records VALUES (?,?,?,?,?,?,?)",
                   (R.EVIDENCE_SCHEMA, forged["evidence_id"], iid, "forged",
                    R.sha256(R.canonical(forged)), R.canonical(forged), end))
    with pytest.raises(R.ResearchRefused):
        R.chain(dest, iid)


# ── idempotency, bank linkage and the full trace ───────────────────────────

def test_retry_restart_and_research_pass_are_idempotent(paths):  # noqa: F811
    _src, dest, now = paths
    iid, end = measured(paths, "normalization")
    first = R.research_pass(dest, recorded_at_ms=end + 2, max_cases=8)
    assert first == {"attempted": 1, "ok": 1, "refused": {}}
    before = records(dest)
    assert R.research_pass(dest, recorded_at_ms=end + 3, max_cases=8)["attempted"] == 0
    again = R.run(dest, iid, recorded_at_ms=end + 99)       # a restart re-run
    assert set(again["outcomes"].values()) == {"duplicate"}
    assert records(dest) == before


def test_research_bank_links_exact_stored_records(paths):  # noqa: F811
    _src, dest, _ = paths
    iid, end = measured(paths, "same_direction")
    out = R.run(dest, iid, recorded_at_ms=end + 2)
    stored = {(t, i): json.loads(j) for t, i, _k, j in records(dest)}
    bank = stored[(R.BANK_SCHEMA, out["bank_object_id"])]
    for name, schema in (("question", R.QUESTION_SCHEMA), ("plan", R.PLAN_SCHEMA),
                         ("evidence", R.EVIDENCE_SCHEMA), ("result", R.RESULT_SCHEMA),
                         ("run", R.RUN_SCHEMA)):
        link = bank["links"][name]
        assert link["id"] == out[f"{name}_id"]
        assert link["sha256"] == R.sha256(R.canonical(stored[(schema, link["id"])]))
    assert bank["result_status"] == "SUPPORTED" and bank["recall"]["authority"] == "context_only"
    assert bank["experiments"]["steps"] == stored[(R.RUN_SCHEMA, out["run_id"])]["steps"]
    assert stored[(R.EVIDENCE_SCHEMA, out["evidence_id"])]["update_event_id"] == last_update(dest, iid).event_id


def test_full_trace_source_to_research_bank_uses_actual_stored_ids(paths):  # noqa: F811
    src, dest, _ = paths
    iid, end = measured(paths, "same_direction")
    out = R.run(dest, iid, recorded_at_ms=end + 2)
    t = T.view(dest, iid)
    L = t["links"]
    assert all(L[s]["status"] == "AVAILABLE" for s in T.STAGES), {s: L[s]["status"] for s in T.STAGES}
    scan = scan_of(src)
    (trace_id,), = table(dest, "SELECT id FROM intelligence_traces WHERE investigation_id=?", iid)
    (ctx_id,), = table(dest, "SELECT id FROM opportunity_contexts WHERE investigation_id=?", iid)
    (decision_id,), = table(dest, "SELECT id FROM allocation_decisions WHERE scan_id='s1'")
    assert L["world_model"]["model_id"] == scan["world_model"]["model_id"]
    assert L["attention"]["scan_id"] == "s1" and L["allocation"]["decision_id"] == decision_id
    assert L["investigation"]["investigation_id"] == iid
    assert L["registration"]["opportunity_context_id"] == ctx_id
    assert L["research_question"]["question_id"] == out["question_id"]
    assert L["research_plan"]["plan_id"] == out["plan_id"]
    assert L["research_evidence"]["evidence_id"] == out["evidence_id"]
    assert L["research_evidence"]["update_event_id"] == t["investigation_outcome"]["updates"][-1]["event_id"]
    assert L["research_result"]["result_id"] == out["result_id"]
    assert L["research_result"]["result_status"] == "SUPPORTED"
    assert L["research_bank_object"]["bank_object_id"] == out["bank_object_id"]
    assert L["research_bank_object"]["trace_id"] == t["trace_id"] == trace_id
    assert [r["result_status"] for r in t["research_runs"]] == ["SUPPORTED"]


# ── safety boundary ─────────────────────────────────────────────────────────

def test_trading_and_attention_output_are_unchanged(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    legacy = src.with_name("legacy.db")
    publish(legacy, now, cfg=LEGACY)
    assert strip_world(scan_of(src)["rows"]) == scan_of(legacy)["rows"]
    code = ("import sys; import trader.observability.investigation_research; "
            "print('\\n'.join(sorted(sys.modules)))")
    loaded = set(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                check=True, cwd=Path(__file__).resolve().parents[1]).stdout.split())
    assert not loaded & {"trader.kernel", "trader.engine.executor", "trader.engine.risk",
                         "trader.engine.orchestrator", "trader.engine.exits", "trader.brain",
                         "trader.brain.llm", "trader.data.feed", "ccxt", "openai", "anthropic",
                         "requests", "httpx"}
    root = Path(__file__).resolve().parents[1] / "trader"
    for name in ("kernel.py", "engine/orchestrator.py", "engine/risk.py", "engine/executor.py",
                 "engine/exits.py", "strategy/promotion.py", "strategy/rolling.py"):
        assert "investigation_research" not in (root / name).read_text(), name
