"""Durable end-to-end intelligence trace — intelligence-trace.v1.

One immutable record per registered investigation, written in the
registering transaction beside its opportunity-context.v1, linking the
actual stored ids of every stage:

    source observation -> WorldModel -> Attention -> allocation
    -> investigation -> registration -> research question -> plan -> evidence
    -> result -> bank

Every id is copied from a persisted record the registration already holds
(the Attention scan payload and its world-model-live receipt, the frozen
allocation decision, the case, its initial update and the opportunity
context), and cross-record agreement is verified before anything is written;
a disagreement refuses the trace rather than recording an inferred edge.

Research stages are reported as they truly are. At registration a
volume_anomaly case's research stages are PENDING for the one registered
investigation-sourced family (``investigation_research``); any other
investigation family has no research family, so its question is UNAVAILABLE
and later stages NOT_RUN. The stored trace is immutable; ``view`` resolves
the research stages from the family's own stored, re-verified records,
which bind back to this trace by trace_id. Nothing here creates a research
record or a verdict.

Shadow/read-only: no score, salience copy, probability, direction, Risk,
Execution, order, allocation, control, LLM or network authority.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from trader.cognition import research_families as rf
from . import investigation_research as IR
from . import world_producer

SCHEMA = "intelligence-trace.v1"
AUTHORITY = "NONE"
MODE = "shadow_read_only"
AVAILABLE, UNKNOWN, UNAVAILABLE, NOT_RUN = "AVAILABLE", "UNKNOWN", "UNAVAILABLE", "NOT_RUN"
PENDING = "PENDING"
NO_RESEARCH_FAMILY = "no_registered_research_family_accepts_investigation_family"
UPSTREAM_UNAVAILABLE = "upstream_research_question_unavailable"
AWAITING_RUN = "awaiting_research_pass"
RESEARCH_STAGES = ("research_plan", "research_evidence", "research_result", "research_bank_object")
STAGES = ("source_observation", "world_model", "attention", "allocation", "investigation",
          "registration", "research_question", *RESEARCH_STAGES)
OUTCOME_SEMANTICS = ("investigation frozen-catalog updates as stored; an investigation "
                     "outcome, not a research result, verdict or trading signal")


class TraceRefused(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _require(ok, reason):
    if not ok:
        raise TraceRefused(reason)


def _world(scan, row, symbol):
    """(source_observation link fields, world_model link) from the stored receipt."""
    rec = scan.get("world_model")
    if rec is None:
        return {"observation_id": None, "observation_source_ref": None}, {
            "status": UNKNOWN, "reason": "world_model_not_supplied"}
    if rec.get("status") != world_producer.OK:
        return {"observation_id": None, "observation_source_ref": None}, {
            "status": UNAVAILABLE, "reason": f"{rec.get('status')}:{rec.get('reason')}",
            "model_id": rec.get("model_id")}
    record = world_producer.record_of(scan)
    model = record.reconstruct()
    _require((record.model_id, record.record_id) == (rec["model_id"], rec["record_id"]),
             "world_model_receipt_mismatch")
    state = next((s for s in model.states if s.instrument == symbol), None)
    _require(state is not None, "world_model_state_missing")
    window = state.get_observations(world_producer.WINDOW_KIND)
    derived = state.get_observations(world_producer.OUTPUT_KIND)
    _require(len(derived) == 1 and len(window) <= 1, "world_model_state_ambiguous")
    contribution = row.get("world_model")
    _require(isinstance(contribution, dict), "attention_world_contribution_missing")
    _require(contribution.get("model_id") == model.model_id, "attention_world_model_mismatch")
    obs = derived[0]
    _require(contribution.get("observation_id") == obs.observation_id,
             "attention_world_observation_mismatch")
    source = {"observation_id": window[0].observation_id if window else None,
              "observation_source_ref": window[0].source_ref if window else None}
    return source, {
        "status": AVAILABLE, "reason": None, "as_of_ms": model.as_of_ms,
        "model_id": model.model_id, "record_id": record.record_id,
        "producer_id": rec["producer_id"],
        "observation_id": obs.observation_id, "observation_quality": obs.quality.value,
        "attention_contribution": {"status": contribution["status"],
                                   "reason": contribution["reason"],
                                   "observation_id": contribution["observation_id"],
                                   "record_sha256": contribution["record_sha256"]}}


def build(scan, allocation, inv, initial, ctx) -> dict:
    """The trace of one registration from the records it holds; pure."""
    symbol = inv.state.symbol
    body = ctx.to_dict()
    att, cinv = body["attention"], body["investigation"]
    alloc = {k: v for k, v in allocation.items() if k != "reused"}
    _require(scan["scan_id"] == inv.state.scan_id == att.get("scan_id")
             == alloc["source_scan_id"], "scan_id_mismatch")
    _require(att.get("scan_sha256") == _sha(scan), "scan_sha256_mismatch")
    _require(att.get("allocation", {}).get("decision_id") == alloc["decision_id"],
             "allocation_mismatch")
    _require(cinv.get("investigation_id") == inv.investigation_id
             and initial.investigation_id == inv.investigation_id
             and cinv.get("latest_update", {}).get("event_id") == initial.event_id,
             "investigation_mismatch")
    rows = [r for r in scan["rows"] if r["symbol"] == symbol]
    _require(len(rows) == 1, "attention_row_missing")
    row = rows[0]
    source, world = _world(scan, row, symbol)
    ctx_world = body["world_model"]
    if world["status"] == AVAILABLE:
        _require(ctx_world["status"] == AVAILABLE
                 and (ctx_world["model_id"], ctx_world["record_id"])
                 == (world["model_id"], world["record_id"]), "context_world_model_mismatch")
    else:
        _require(ctx_world["status"] != AVAILABLE, "context_world_model_mismatch")
    versions = sorted(v["version_id"] for v in scan["input_versions"] if v["symbol"] == symbol)
    _require(bool(versions) and set(versions) <= set(inv.state.input_versions),
             "input_version_mismatch")
    links = {
        "source_observation": dict(status=AVAILABLE, reason=None, scan_id=scan["scan_id"],
                                   input_hash=scan["input_hash"], input_version_ids=versions,
                                   **source),
        "world_model": world,
        "attention": {"status": AVAILABLE, "reason": None, "scan_id": scan["scan_id"],
                      "scan_sha256": att["scan_sha256"], "config_id": scan["config_id"],
                      "rank": row.get("rank"), "selection_reason": row.get("reason"),
                      "selected": row.get("selected"), "dominant": row.get("dominant")},
        "allocation": {"status": AVAILABLE, "reason": None, "decision_id": alloc["decision_id"],
                       "mode": alloc["mode"],
                       "candidate_allocated": att["allocation"]["candidate_allocated"]},
        "investigation": {"status": AVAILABLE, "reason": None,
                          "investigation_id": inv.investigation_id, "episode_id": inv.episode_id,
                          "family": inv.measurement.family, "catalog_id": inv.measurement.catalog_id,
                          "state_id": inv.state.state_id, "registered_ms": inv.registered_ms},
        "registration": {"status": AVAILABLE, "reason": None,
                         "initial_update_id": initial.event_id,
                         "opportunity_context_id": ctx.context_id},
        **_research_at_registration(inv.measurement.family),
    }
    out = {"schema": SCHEMA, "authority": AUTHORITY, "mode": MODE, "symbol": symbol,
           "investigation_id": inv.investigation_id, "as_of_ms": inv.registered_ms,
           "stages": list(STAGES), "links": links}
    return dict(out, trace_id=f"{SCHEMA}:{_sha(out)}")


def _research_at_registration(family) -> dict:
    if family == IR.SOURCE_FAMILY:
        pending = {"status": PENDING, "reason": AWAITING_RUN, "family": IR.FAMILY}
        return {"research_question": dict(pending, schema=IR.QUESTION_SCHEMA),
                **{stage: dict(pending) for stage in RESEARCH_STAGES}}
    return {"research_question": {"status": UNAVAILABLE, "reason": NO_RESEARCH_FAMILY,
                                  "investigation_family": family,
                                  "investigation_research_families": [IR.SOURCE_FAMILY],
                                  "health_research_families": list(rf.FAMILY_NAMES)},
            **{stage: {"status": NOT_RUN, "reason": UPSTREAM_UNAVAILABLE}
               for stage in RESEARCH_STAGES}}


def _research_now(trace, path) -> dict:
    """Research stages as stored now, from the family's verified records."""
    if trace["links"]["research_question"]["status"] != PENDING:
        return {}
    ch = IR.chain(path, trace["investigation_id"])
    if ch is None:
        return {}
    q, p = ch["question"], ch["plan"]
    src = q["source"]
    if (src["trace_id"], src["registration_context_id"]) != (
            trace["trace_id"], trace["links"]["registration"]["opportunity_context_id"]):
        raise TraceRefused("research_question_trace_mismatch")
    out = {"research_question": {"status": AVAILABLE, "reason": None, "family": IR.FAMILY,
                                 "question_id": q["question_id"], "schema": q["schema"]},
           "research_plan": {"status": AVAILABLE, "reason": None, "plan_id": p["plan_id"],
                             "question_id": p["question_id"]}}
    if ch["runs"]:
        latest = ch["runs"][-1]
        e, r, b = latest["evidence"], latest["result"], latest["bank"]
        out.update(
            research_evidence={"status": AVAILABLE, "reason": None, "evidence_id": e["evidence_id"],
                               "plan_id": e["plan_id"], "update_event_id": e["update_event_id"],
                               "outcome_evidence_id": e["measurement"]["outcome_evidence_id"]},
            research_result={"status": AVAILABLE, "reason": None, "result_id": r["result_id"],
                             "evidence_id": r["evidence_id"], "result_status": r["status"],
                             "status_reason": r["status_reason"],
                             "supported_path": r["supported_path"]},
            research_bank_object={"status": AVAILABLE, "reason": None,
                                  "bank_object_id": b["bank_object_id"],
                                  "run_id": latest["run"]["run_id"],
                                  "result_id": b["links"]["result"]["id"],
                                  "trace_id": b["trace_id"]})
    runs = [{"update_event_id": x["update_event_id"], "run_id": x["run"]["run_id"],
             "result_status": x["result"]["status"], "bank_object_id": x["bank"]["bank_object_id"]}
            for x in ch["runs"]]
    return dict(out, research_runs=runs)


def schema(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS intelligence_traces(id TEXT PRIMARY KEY, "
               "investigation_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)")


def persist(db, trace) -> bool:
    """Insert (True), identical duplicate (False) or conflict (TraceRefused)."""
    text = canonical(trace)
    rows = db.execute("SELECT id,investigation_id,payload FROM intelligence_traces "
                      "WHERE id=? OR investigation_id=?",
                      (trace["trace_id"], trace["investigation_id"])).fetchall()
    for r in rows:
        if tuple(r) != (trace["trace_id"], trace["investigation_id"], text):
            raise TraceRefused("intelligence_trace_conflict")
    if rows:
        return False
    db.execute("INSERT INTO intelligence_traces VALUES (?,?,?)",
               (trace["trace_id"], trace["investigation_id"], text))
    return True


def verify(text) -> dict:
    trace = json.loads(text)
    claimed = trace.pop("trace_id", None)
    if claimed != f"{SCHEMA}:{_sha(trace)}" or canonical(dict(trace, trace_id=claimed)) != text:
        raise TraceRefused("intelligence_trace_corrupt")
    return dict(trace, trace_id=claimed)


def view(path, investigation_id) -> dict | None:
    """Read-only: the verified stored trace plus the case's stored updates in
    chain order. None when no trace was recorded for the case."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True,
                                 timeout=.1)) as db:
        db.execute("BEGIN")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='intelligence_traces'").fetchone():
            return None
        row = db.execute("SELECT id,payload FROM intelligence_traces WHERE investigation_id=?",
                         (investigation_id,)).fetchone()
        if row is None:
            return None
        trace = verify(row[1])
        if trace["trace_id"] != row[0]:
            raise TraceRefused("intelligence_trace_corrupt")
        ctx = db.execute("SELECT id FROM opportunity_contexts WHERE investigation_id=?",
                         (investigation_id,)).fetchone()
        if ctx is None or ctx[0] != trace["links"]["registration"]["opportunity_context_id"]:
            raise TraceRefused("intelligence_trace_context_mismatch")
        updates = [json.loads(p) for (p,) in db.execute(
            "SELECT payload FROM updates WHERE case_id=? ORDER BY observed_ms,rowid",
            (investigation_id,))]
        terminal = db.execute("SELECT terminal_ms FROM cases WHERE id=?",
                              (investigation_id,)).fetchone()
    chain, previous = [], None
    for u in updates:
        if u["previous_event_id"] != previous:
            raise TraceRefused("investigation_update_chain_broken")
        chain.append({"event_id": u["event_id"], "previous_event_id": u["previous_event_id"],
                      "evidence_id": u["evidence"]["evidence_id"],
                      "evidence_status": u["evidence"]["status"], "as_of_ms": u["as_of_ms"]})
        previous = u["event_id"]
    if not chain or chain[0]["event_id"] != trace["links"]["registration"]["initial_update_id"]:
        raise TraceRefused("investigation_update_chain_broken")
    research = _research_now(trace, path)
    runs = research.pop("research_runs", [])
    links = dict(trace["links"], **research)
    return dict(trace, links=links, research_runs=runs, investigation_outcome={
        "semantics": OUTCOME_SEMANTICS,
        "terminal": bool(terminal and terminal[0] is not None),
        "latest_evidence_status": chain[-1]["evidence_status"], "updates": chain})
