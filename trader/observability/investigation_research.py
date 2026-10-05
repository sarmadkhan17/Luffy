"""Investigation-sourced research family — investigation_volume_anomaly (R1).

The one research family whose question source is an EXISTING persisted
investigation.v2 ``volume_anomaly`` case in the investigation ledger. It runs
the Q -> P -> E -> R chain and files a Research Bank object, with the same
record roles, bank field layout, identity and insert/duplicate/conflict
semantics as the strategy-decay chain (research_question/plan/evidence/
result/run/bank). Like strategy_health_unreadable, it persists in its own
append-only table: the decay tables are read unfiltered by the decay
verifiers and key questions by an integer journal event id, which an
investigation does not have.

Source authority is the case's own frozen protocol (catalog_id, threshold
2.0, N=20 baseline, H=5 exact forward target bars, alternatives
same_direction / normalization / opposite_direction). Nothing here adds a
threshold, score, statistical test or hypothesis.

- Question: the case's observable question verbatim ("Does unusual volume
  persist, return toward baseline, or reverse?"), bound to the exact
  investigation, state, catalog, registration context (with its WorldModel
  and Attention scan/allocation ids) and intelligence trace.
- Plan: routes only to evidence the frozen measurement already requires —
  the verified update chain, the terminal OutcomeEvidence and the exact
  target candle versions — per registered path. Participant cause stays
  UNAVAILABLE: the ACQUIRE recommendation is future-only, no acquisition.
- Evidence: every stored update is replayed through investigation.measure
  and investigation.advance from retained inputs and must reproduce byte
  for byte; identities are recomputed; the registration context must
  replay. The case, chain and exact target inputs are frozen verbatim.
- Result: PREDICATES re-derive the path from the frozen measured score and
  must agree with the stored terminal assessment (compatible -> SUPPORTED,
  contradicted -> REFUTED). unresolved / not_testable -> INCONCLUSIVE with
  every path NOT_ASSESSED; absence of evidence is never REFUTED.

SUPPORTED means only that the frozen registered observable path was
supported by its exact forward measurement. It establishes no predictive
edge, causal mechanism, profitability or trading permission. No LLM,
network, Kernel, Risk, Execution, order, allocation or control authority.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from trader.cognition import investigation as I
from trader.cognition.contracts import stable_id

FAMILY = "investigation_volume_anomaly"
SOURCE_FAMILY = "volume_anomaly"
QUESTION_SCHEMA = "investigation-volume-anomaly-question.v1"
PLAN_SCHEMA = "investigation-volume-anomaly-plan.v1"
EVIDENCE_SCHEMA = "investigation-volume-anomaly-evidence.v1"
RESULT_SCHEMA = "investigation-volume-anomaly-result.v1"
RUN_SCHEMA = "investigation-volume-anomaly-run.v1"
BANK_SCHEMA = "investigation-volume-anomaly-bank-object.v1"
RECORD_TYPES = (QUESTION_SCHEMA, PLAN_SCHEMA, EVIDENCE_SCHEMA, RESULT_SCHEMA, RUN_SCHEMA,
                BANK_SCHEMA)
PLANNER_ID = "investigation-volume-anomaly-evidence-routing.v1"
COLLECTOR_ID = "investigation-volume-anomaly-evidence-collection.v1"
RESOLVER_ID = "investigation-volume-anomaly-predicate-result.v1"
RUNNER_ID = "investigation-volume-anomaly-runner.v1"
BUILDER_ID = "investigation-volume-anomaly-research-bank.v1"

PATHS = ("same_direction", "normalization", "opposite_direction")
PARTICIPANT = "participant_cause"
ROUTED, UNAVAILABLE = "ROUTED", "UNAVAILABLE"
SUPPORTED, REFUTED, NOT_ASSESSED, INCONCLUSIVE = "SUPPORTED", "REFUTED", "NOT_ASSESSED", "INCONCLUSIVE"
MEASURED, UNRESOLVED, NOT_TESTABLE = "measured", "unresolved", "not_testable"

SUPPORTED_MEANING = ("SUPPORTED means only that the frozen registered observable path was "
                     "supported by its exact forward measurement; it establishes no predictive "
                     "edge, causal mechanism, profitable strategy or trading permission")
QUESTION_SEMANTICS = ("investigation-sourced research question; the case's own observable "
                      "question verbatim; not a profitability, trade or causal question; no "
                      "direction, score, probability, rank, salience or trading authority")
PARTICIPANT_REASON = "participant_taker_buy_source_not_acquired"
PARTICIPANT_DETAIL = ("the case's ACQUIRE recommendation for taker-buy/total volume is a future "
                      "recommendation only; this family performs no network acquisition")

#: the registered falsifier predicates of THIS family only; frozen before use
PREDICATES = {
    "schema": "investigation-volume-anomaly-predicates.v1",
    "source_family": SOURCE_FAMILY,
    "source_catalog_id": I.CATALOG_ID,
    "threshold": I.CATALOG["thresholds"][SOURCE_FAMILY],
    "signed_score": "terminal OutcomeEvidence.score * Measurement.sign",
    "rules": {
        "same_direction": "measured and signed_score >= threshold -> SUPPORTED; measured otherwise -> REFUTED",
        "normalization": "measured and -threshold < signed_score < threshold -> SUPPORTED; measured otherwise -> REFUTED",
        "opposite_direction": "measured and signed_score <= -threshold -> SUPPORTED; measured otherwise -> REFUTED",
    },
    "absence_rule": "unresolved or not_testable -> every path NOT_ASSESSED, result INCONCLUSIVE; "
                    "absence of evidence is never REFUTED",
    "agreement_rule": "predicate outcomes must equal the stored terminal InvestigationUpdate "
                      "assessment (compatible -> SUPPORTED, contradicted -> REFUTED), else refused",
    "meaning": SUPPORTED_MEANING,
}
PREDICATES_ID = hashlib.sha256(json.dumps(PREDICATES, sort_keys=True, separators=(",", ":"))
                               .encode()).hexdigest()

_STATE_FIELDS = ("scan_id", "symbol", "as_of_ms", "observed_ms", "available_ms", "cohort",
                 "membership_json", "evidence_ids", "input_versions", "dimensions", "transitions",
                 "contradictions", "missing", "code_json", "config_id")


class ResearchRefused(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _refuse(reason):
    raise ResearchRefused(reason)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _with_id(body: dict, key: str) -> dict:
    return dict(body, **{key: sha256(canonical(body))})


# ── source verification ──────────────────────────────────────────────────

def verify_case(inv) -> None:
    """The case must be exactly the frozen investigation.v2 volume_anomaly protocol."""
    m, st = inv.measurement, inv.state
    if inv.primary_trigger != SOURCE_FAMILY or m.family != SOURCE_FAMILY:
        _refuse("unsupported_investigation_family")
    if inv.schema_version != I.SCHEMA or st.schema_version != I.SCHEMA or m.catalog_id != I.CATALOG_ID:
        _refuse("catalog_mismatch")
    if m.threshold != PREDICATES["threshold"]:
        _refuse("threshold_mismatch")
    if stable_id("state", {k: getattr(st, k) for k in _STATE_FIELDS}) != st.state_id:
        _refuse("state_identity_mismatch")
    anchor = st.as_of_ms // I.TF * I.TF - I.TF
    episode = stable_id("episode", I.CATALOG_ID, st.symbol, SOURCE_FAMILY, anchor)
    if (episode != inv.episode_id
            or stable_id("investigation", episode, st.state_id, inv.registered_ms) != inv.investigation_id):
        _refuse("investigation_identity_mismatch")
    start = (inv.registered_ms // I.TF + 1) * I.TF
    if (m.target_keys != tuple((st.symbol, start + i * I.TF) for i in range(I.H))
            or m.deadline_ms != start + I.H * I.TF or m.expires_ms != m.deadline_ms + I.GRACE_MS):
        _refuse("measurement_protocol_mismatch")
    value = next((d.value for d in st.dimensions if d.name == SOURCE_FAMILY and d.status == "ok"), None)
    if m.sign != (0 if not value else (1 if value > 0 else -1)):
        _refuse("measurement_sign_mismatch")
    if tuple(a.name for a in inv.alternatives) != PATHS or inv.question != I.QUESTIONS[SOURCE_FAMILY]:
        _refuse("protocol_mismatch")


def _replay_chain(inv, updates, bars):
    """Every stored update must reproduce from the case and retained inputs."""
    previous = None
    for u in updates:
        if u.investigation_id != inv.investigation_id or u.previous_event_id != (
                previous.event_id if previous else None):
            _refuse("update_chain_broken")
        try:
            evidence = I.measure(inv, bars, u.as_of_ms, u.observed_ms)
            again = I.advance(inv, evidence, previous)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            _refuse("update_replay_failed")
        if evidence != u.evidence:
            _refuse("outcome_evidence_mismatch")
        if again != u:
            _refuse("update_replay_mismatch")
        previous = u


def read_source(ledger_path, investigation_id) -> dict:
    """Verified source facts for one case; fails closed on any mismatch."""
    from trader.observability import investigation as C
    from trader.observability import intelligence_trace as T
    uri = Path(ledger_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=.1)) as db:
        db.execute("BEGIN")
        case = db.execute("SELECT id,episode_id,symbol,created_ms,terminal_ms,payload FROM cases "
                          "WHERE id=?", (investigation_id,)).fetchone()
        if case is None:
            _refuse("investigation_missing")
        updates = [r[0] for r in db.execute("SELECT payload FROM updates WHERE case_id=? "
                                            "ORDER BY observed_ms,rowid", (investigation_id,))]
        inputs = dict(db.execute("SELECT inputs.id,inputs.payload FROM inputs JOIN case_inputs "
                                 "ON inputs.id=case_inputs.input_id WHERE case_id=?",
                                 (investigation_id,)).fetchall())
        has_trace = db.execute("SELECT 1 FROM sqlite_master WHERE name='intelligence_traces'").fetchone()
        trace = db.execute("SELECT id,payload FROM intelligence_traces WHERE investigation_id=?",
                           (investigation_id,)).fetchone() if has_trace else None
    iid, episode, symbol, created, terminal_ms, payload = case
    try:
        inv = I.investigation_from_dict(json.loads(payload))
        chain = [I.update_from_dict(json.loads(p)) for p in updates]
        bars = [I.InputBar(d["version_id"], I.Candle(**d["candle"]))
                for d in (json.loads(p) for p in inputs.values())]
    except (TypeError, KeyError, ValueError) as exc:
        raise ResearchRefused("source_payload_malformed") from exc
    if (inv.investigation_id, inv.episode_id, inv.state.symbol, inv.registered_ms) != (
            iid, episode, symbol, created):
        _refuse("investigation_row_mismatch")
    verify_case(inv)
    if not chain:
        _refuse("update_chain_missing")
    _replay_chain(inv, chain, bars)
    last = chain[-1]
    is_terminal = last.evidence.status != UNRESOLVED
    if is_terminal != (terminal_ms is not None) or (is_terminal and terminal_ms != last.observed_ms):
        _refuse("terminal_state_mismatch")
    targets = []
    for s, t, v in last.evidence.target_versions:
        if v not in inputs:
            _refuse("target_version_missing")
        targets.append({"symbol": s, "open_ms": t, "version_id": v,
                        "payload": inputs[v], "payload_sha256": sha256(inputs[v])})
    try:
        ctx = C.replay_context(ledger_path, investigation_id)
    except C.LedgerRefused as exc:
        raise ResearchRefused("registration_context_unverified:" + exc.reason) from None
    if ctx.to_dict()["investigation"]["latest_update"].get("event_id") != chain[0].event_id:
        _refuse("registration_update_mismatch")
    trace_id = None
    if trace is not None:
        body = T.verify(trace[1])
        if (body["trace_id"] != trace[0] or body["investigation_id"] != iid
                or body["links"]["registration"]["opportunity_context_id"] != ctx.context_id):
            _refuse("intelligence_trace_mismatch")
        trace_id = body["trace_id"]
    return {"inv": inv, "case_payload": payload, "updates": updates, "chain": chain,
            "targets": targets, "context": ctx, "trace_id": trace_id}


# ── Q / P / E / R / Run / Bank (pure) ────────────────────────────────────

def build_question(src) -> dict:
    inv, ctx = src["inv"], src["context"].to_dict()
    att, world = ctx["attention"], ctx["world_model"]
    body = {
        "schema": QUESTION_SCHEMA, "question_kind": FAMILY, "source_family": SOURCE_FAMILY,
        "question": inv.question,
        "scope": {"kind": "investigation", "investigation_id": inv.investigation_id,
                  "symbol": inv.state.symbol},
        "source": {"investigation_id": inv.investigation_id, "episode_id": inv.episode_id,
                   "state_id": inv.state.state_id, "catalog_id": inv.measurement.catalog_id,
                   "registered_ms": inv.registered_ms,
                   "case_payload_sha256": sha256(src["case_payload"]),
                   "registration_update_id": src["chain"][0].event_id,
                   "registration_update_sha256": sha256(src["updates"][0]),
                   "registration_context_id": src["context"].context_id,
                   "registration_context_sha256": sha256(src["context"].canonical_json),
                   "trace_id": src["trace_id"]},
        "context": {"world_model": {k: world.get(k) for k in ("status", "reason", "model_id",
                                                               "record_id")},
                    "attention": {"scan_id": att.get("scan_id"), "scan_sha256": att.get("scan_sha256"),
                                  "allocation_decision_id": att.get("allocation", {}).get("decision_id")}},
        "protocol": {"catalog_id": inv.measurement.catalog_id, "threshold": inv.measurement.threshold,
                     "baseline_bars": I.N, "horizon_bars": I.H, "sign": inv.measurement.sign,
                     "target_keys": [list(k) for k in inv.measurement.target_keys],
                     "alternatives": [{"name": a.name, "prediction": a.prediction,
                                       "invalidator": a.invalidator} for a in inv.alternatives]},
        "semantics": QUESTION_SEMANTICS,
    }
    return _with_id(body, "question_id")


def build_plan(question: dict) -> dict:
    iid = question["scope"]["investigation_id"]
    targets = question["protocol"]["target_keys"]
    routes = [{"store": "investigation.updates", "locator": {"investigation_id": iid,
                                                             "selector": "latest_update_of_verified_chain"},
               "fields": ["evidence.evidence_id", "evidence.status", "evidence.score",
                          "evidence.target_versions", "assessment"]},
              {"store": "investigation.inputs", "locator": {"investigation_id": iid, "target_keys": targets},
               "fields": ["candle.volume", "candle.available_ms", "candle.close_ms"]}]
    sections = [{"hypothesis": a["name"], "status": ROUTED, "prediction": a["prediction"],
                 "invalidator": a["invalidator"], "routes": routes}
                for a in question["protocol"]["alternatives"]]
    sections.append({"hypothesis": PARTICIPANT, "status": UNAVAILABLE, "reason": PARTICIPANT_REASON,
                     "detail": PARTICIPANT_DETAIL, "routes": []})
    body = {"schema": PLAN_SCHEMA, "plan_kind": FAMILY, "planner_id": PLANNER_ID,
            "question_id": question["question_id"], "question_sha256": sha256(canonical(question)),
            "predicates_id": PREDICATES_ID, "sections": sections}
    return _with_id(body, "plan_id")


def build_evidence(plan: dict, question: dict, src) -> dict:
    inv, last = src["inv"], src["chain"][-1]
    if plan["question_id"] != question["question_id"]:
        _refuse("plan_question_mismatch")
    ev = last.evidence
    body = {"schema": EVIDENCE_SCHEMA, "evidence_kind": FAMILY, "collector_id": COLLECTOR_ID,
            "plan_id": plan["plan_id"], "plan_sha256": sha256(canonical(plan)),
            "question_id": question["question_id"], "investigation_id": inv.investigation_id,
            "update_event_id": last.event_id,
            "frozen": {"case_payload": src["case_payload"],
                       "case_payload_sha256": sha256(src["case_payload"]),
                       "update_chain": [{"event_id": u.event_id, "payload": p, "sha256": sha256(p)}
                                        for u, p in zip(src["chain"], src["updates"])],
                       "targets": src["targets"],
                       "registration_context_id": src["context"].context_id},
            "measurement": {"terminal": ev.status != UNRESOLVED, "status": ev.status,
                            "reason": ev.reason, "outcome_evidence_id": ev.evidence_id,
                            "score": ev.score if ev.status == MEASURED else None,
                            "sign": inv.measurement.sign, "threshold": inv.measurement.threshold,
                            "target_versions": [list(v) for v in ev.target_versions],
                            "missing_keys": [list(k) for k in ev.missing_keys]},
            "alternative_assessments": [list(a) for a in last.assessment]}
    return _with_id(body, "evidence_id")


def verify_evidence(evidence: dict):
    """Re-derive the frozen evidence on its own: case identity, chain links,
    and the last update's measurement and assessment from the frozen target
    inputs. Returns the case."""
    fz = evidence["frozen"]
    try:
        inv = I.investigation_from_dict(json.loads(fz["case_payload"]))
        chain = [I.update_from_dict(json.loads(u["payload"])) for u in fz["update_chain"]]
        bars = [I.InputBar(d["version_id"], I.Candle(**d["candle"]))
                for d in (json.loads(t["payload"]) for t in fz["targets"])]
    except (TypeError, KeyError, ValueError) as exc:
        raise ResearchRefused("evidence_frozen_content_malformed") from exc
    if sha256(fz["case_payload"]) != fz["case_payload_sha256"] or any(
            sha256(u["payload"]) != u["sha256"] or c.event_id != u["event_id"]
            for u, c in zip(fz["update_chain"], chain)) or any(
            sha256(t["payload"]) != t["payload_sha256"] for t in fz["targets"]):
        _refuse("evidence_frozen_hash_mismatch")
    verify_case(inv)
    if inv.investigation_id != evidence["investigation_id"] or not chain:
        _refuse("evidence_identity_mismatch")
    previous = None
    for u in chain:
        if u.previous_event_id != (previous.event_id if previous else None):
            _refuse("update_chain_broken")
        previous = u
    last, prior = chain[-1], (chain[-2] if len(chain) > 1 else None)
    if last.event_id != evidence["update_event_id"]:
        _refuse("evidence_update_mismatch")
    try:
        again = I.measure(inv, bars, last.as_of_ms, last.observed_ms)
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        _refuse("outcome_evidence_mismatch")
    if again != last.evidence or I.advance(inv, again, prior) != last:
        _refuse("outcome_evidence_mismatch")
    m = evidence["measurement"]
    if (m["status"], m["outcome_evidence_id"], m["sign"], m["threshold"]) != (
            last.evidence.status, last.evidence.evidence_id, inv.measurement.sign,
            inv.measurement.threshold) or [list(a) for a in last.assessment] != evidence[
            "alternative_assessments"]:
        _refuse("evidence_measurement_mismatch")
    return inv


def build_result(evidence: dict) -> dict:
    """Apply the registered predicates to frozen evidence."""
    m = evidence["measurement"]
    stored = dict((k, v) for k, v in evidence["alternative_assessments"])
    if m["status"] == MEASURED:
        score = m["score"]
        if type(score) not in (int, float) or m["sign"] not in (-1, 1):
            _refuse("measured_score_invalid")
        signed, thr = score * m["sign"], m["threshold"]
        path = ("same_direction" if signed >= thr else
                "opposite_direction" if signed <= -thr else "normalization")
        for p in PATHS:
            if stored.get(p) != ("compatible" if p == path else "contradicted"):
                _refuse("assessment_predicate_disagreement")
        paths = [{"hypothesis": p, "status": SUPPORTED if p == path else REFUTED,
                  "investigation_assessment": stored[p], "rule": PREDICATES["rules"][p]}
                 for p in PATHS]
        status, reason, supported = SUPPORTED, "registered_path_supported_by_exact_forward_measurement", path
    elif m["status"] in (UNRESOLVED, NOT_TESTABLE):
        paths = [{"hypothesis": p, "status": NOT_ASSESSED, "investigation_assessment": stored.get(p),
                  "rule": PREDICATES["absence_rule"]} for p in PATHS]
        status, supported = INCONCLUSIVE, None
        reason = ("investigation_unresolved" if m["status"] == UNRESOLVED
                  else "investigation_not_testable:" + str(m["reason"]))
    else:
        _refuse("unsupported_measurement_status")
    body = {"schema": RESULT_SCHEMA, "result_kind": FAMILY, "resolver_id": RESOLVER_ID,
            "evidence_id": evidence["evidence_id"], "evidence_sha256": sha256(canonical(evidence)),
            "predicates_id": PREDICATES_ID, "status": status, "status_reason": reason,
            "supported_path": supported, "paths": paths,
            "unavailable": [{"hypothesis": PARTICIPANT, "reason": PARTICIPANT_REASON}],
            "meaning": SUPPORTED_MEANING, "predictive_edge_established": False,
            "trading_authority": "NONE"}
    return _with_id(body, "result_id")


def build_run(question, plan, evidence, result) -> dict:
    body = {"schema": RUN_SCHEMA, "run_kind": FAMILY, "runner_id": RUNNER_ID,
            "investigation_id": evidence["investigation_id"],
            "update_event_id": evidence["update_event_id"],
            "steps": [{"step": s, "schema": r["schema"], "record_id": r[k]}
                      for s, r, k in (("question", question, "question_id"), ("plan", plan, "plan_id"),
                                      ("evidence", evidence, "evidence_id"),
                                      ("result", result, "result_id"))],
            "status": "COMPLETED"}
    return _with_id(body, "run_id")


def build_bank(question, plan, evidence, result, run) -> dict:
    """research-bank-object.v1 field layout, filed for this family."""
    links = {name: {"id": rec[key], "sha256": sha256(canonical(rec))}
             for name, rec, key in (("question", question, "question_id"), ("plan", plan, "plan_id"),
                                    ("evidence", evidence, "evidence_id"),
                                    ("result", result, "result_id"), ("run", run, "run_id"))}
    refs = {"evidence_id": evidence["evidence_id"],
            "outcome_evidence_id": evidence["measurement"]["outcome_evidence_id"],
            "update_event_id": evidence["update_event_id"]}
    measured = result["status"] == SUPPORTED

    def classified(wanted):
        if not measured:
            return {"status": "NOT_CLASSIFIED", "reason": result["status_reason"], "items": []}
        return {"status": "CLASSIFIED", "reason": None,
                "items": [dict(refs, hypothesis=p["hypothesis"], status=p["status"])
                          for p in result["paths"] if p["status"] == wanted]}
    body = {"schema": BANK_SCHEMA, "bank_kind": FAMILY, "builder_id": BUILDER_ID, "links": links,
            "trace_id": question["source"]["trace_id"],
            "question": {"question_id": question["question_id"], "scope": question["scope"],
                         "question": question["question"]},
            "sources": [{"hypothesis": s["hypothesis"], "status": s["status"],
                         "stores": [r["store"] for r in s["routes"]]} for s in plan["sections"]],
            "extracted_claims": {"status": "NOT_AVAILABLE", "reason": "no_claim_extraction_contract"},
            "supporting_evidence": classified(SUPPORTED),
            "contradictory_evidence": classified(REFUTED),
            "experiments": {"kind": "structural_run_step_record", "steps": run["steps"]},
            "result_status": result["status"], "result_reason": result["status_reason"],
            "limitations": [{"origin": "result.meaning", "text": SUPPORTED_MEANING},
                            {"origin": "plan." + PARTICIPANT, "text": PARTICIPANT_DETAIL}],
            "next_questions": {"status": "NOT_AVAILABLE", "reason": "no_next_question_generator"},
            "cost": {"status": "NOT_MEASURED", "reason": "no_family_cost_telemetry_contract"},
            "recall": {"authority": "context_only", "suppression": None}}
    return _with_id(body, "bank_object_id")


# ── persistence ──────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS investigation_research_records (
    record_type TEXT NOT NULL, record_id TEXT NOT NULL,
    investigation_id TEXT NOT NULL,
    record_key TEXT NOT NULL,          -- investigation id (Q, P) or update event id (E, R, run, bank)
    canonical_sha256 TEXT NOT NULL, canonical_json TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    PRIMARY KEY (record_type, record_id), UNIQUE (record_type, record_key));
CREATE TRIGGER IF NOT EXISTS investigation_research_records_no_update
BEFORE UPDATE ON investigation_research_records
BEGIN SELECT RAISE(ABORT, 'investigation_research_records is immutable'); END;
CREATE TRIGGER IF NOT EXISTS investigation_research_records_no_delete
BEFORE DELETE ON investigation_research_records
BEGIN SELECT RAISE(ABORT, 'investigation_research_records is immutable'); END;
"""
_ID_KEY = {QUESTION_SCHEMA: "question_id", PLAN_SCHEMA: "plan_id", EVIDENCE_SCHEMA: "evidence_id",
           RESULT_SCHEMA: "result_id", RUN_SCHEMA: "run_id", BANK_SCHEMA: "bank_object_id"}


def schema(db) -> None:
    db.executescript(_DDL)


def run(ledger_path, investigation_id, *, recorded_at_ms: int) -> dict:
    """Q -> P -> E -> R -> run -> bank for the case's latest verified update,
    in one transaction. Refusal writes nothing. Identities exclude recording
    time, so retry/restart returns duplicates."""
    try:
        src = read_source(ledger_path, investigation_id)
        q = build_question(src)
        p = build_plan(q)
        e = build_evidence(p, q, src)
        verify_evidence(e)
        r = build_result(e)
        rn = build_run(q, p, e, r)
        b = build_bank(q, p, e, r, rn)
    except ResearchRefused as exc:
        return {"status": "REFUSED", "reason": exc.reason, "investigation_id": investigation_id}
    iid, uid = investigation_id, e["update_event_id"]
    outcomes = {}
    with closing(sqlite3.connect(ledger_path, timeout=5)) as db:
        schema(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            for name, rec, key in (("question", q, iid), ("plan", p, iid), ("evidence", e, uid),
                                   ("result", r, uid), ("run", rn, uid), ("bank", b, uid)):
                outcomes[name] = _store(db, rec, iid, key, recorded_at_ms)
            if outcomes['bank']=='inserted':
                from trader.learning import capture as lc, capture_runtime as lr
                lc.safely(db, 'research-bank:'+b['bank_object_id'], lr.investigation_research,
                    dict(question=q,plan=p,evidence=e,result=r,receipt=rn),b,recorded_at_ms)
            db.commit()
        except BaseException as exc:
            db.rollback()
            if isinstance(exc, ResearchRefused):
                return {"status": "REFUSED", "reason": exc.reason, "investigation_id": iid}
            raise
    return {"status": "OK", "investigation_id": iid, "update_event_id": uid,
            "question_id": q["question_id"], "plan_id": p["plan_id"],
            "evidence_id": e["evidence_id"], "result_id": r["result_id"], "run_id": rn["run_id"],
            "bank_object_id": b["bank_object_id"], "result_status": r["status"],
            "supported_path": r["supported_path"], "outcomes": outcomes}


def _store(db, record, iid, key, recorded_at_ms) -> str:
    rtype, rid = record["schema"], record[_ID_KEY[record["schema"]]]
    text = canonical(record)
    rows = db.execute("SELECT record_id,canonical_json FROM investigation_research_records "
                      "WHERE record_type=? AND (record_id=? OR record_key=?)",
                      (rtype, rid, key)).fetchall()
    for r in rows:
        if tuple(r) != (rid, text):
            _refuse("research_record_conflict:" + rtype)
    if rows:
        return "duplicate"
    db.execute("INSERT INTO investigation_research_records VALUES (?,?,?,?,?,?,?)",
               (rtype, rid, iid, key, sha256(text), text, recorded_at_ms))
    return "inserted"


def research_pass(ledger_path, *, recorded_at_ms: int, max_cases: int) -> dict:
    """Bounded shadow pass: run each volume_anomaly case whose latest update
    has no run receipt yet, oldest first, at most `max_cases`."""
    if type(max_cases) is not int or max_cases < 1:
        raise ValueError("max_cases must be a positive int")
    with closing(sqlite3.connect(ledger_path, timeout=5)) as db:
        schema(db)
        rows = db.execute(
            "SELECT c.id, (SELECT u.id FROM updates u WHERE u.case_id=c.id "
            "ORDER BY u.observed_ms DESC, u.rowid DESC LIMIT 1) AS latest FROM cases c "
            "WHERE json_extract(c.payload,'$.measurement.family')=? ORDER BY c.created_ms,c.id",
            (SOURCE_FAMILY,)).fetchall()
        done = {k for (k,) in db.execute("SELECT record_key FROM investigation_research_records "
                                         "WHERE record_type=?", (RUN_SCHEMA,))}
    todo = [iid for iid, latest in rows if latest and latest not in done][:max_cases]
    out = {"attempted": len(todo), "ok": 0, "refused": {}}
    for iid in todo:
        r = run(ledger_path, iid, recorded_at_ms=recorded_at_ms)
        if r["status"] == "OK":
            out["ok"] += 1
        else:
            out["refused"][r["reason"]] = out["refused"].get(r["reason"], 0) + 1
    return out


# ── read / verify ────────────────────────────────────────────────────────

def _load(text, schema_name):
    rec = json.loads(text)
    key = _ID_KEY[schema_name]
    claimed = rec.get(key)
    body = {k: v for k, v in rec.items() if k != key}
    if rec.get("schema") != schema_name or claimed != sha256(canonical(body)) \
            or canonical(rec) != text:
        _refuse("research_record_corrupt:" + schema_name)
    return rec


def chain(ledger_path, investigation_id) -> dict | None:
    """Every stored research record of one case, fully re-verified and
    re-derived; None when the family never ran for it."""
    uri = Path(ledger_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=.1)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='investigation_research_records'"
                          ).fetchone():
            return None
        rows = db.execute("SELECT record_type,record_id,record_key,canonical_sha256,canonical_json "
                          "FROM investigation_research_records WHERE investigation_id=? ORDER BY rowid",
                          (investigation_id,)).fetchall()
        live = db.execute("SELECT payload FROM cases WHERE id=?", (investigation_id,)).fetchone()
    if not rows:
        return None
    by = {t: [] for t in RECORD_TYPES}
    for rtype, rid, key, csha, text in rows:
        if rtype not in by or sha256(text) != csha:
            _refuse("research_record_corrupt:" + str(rtype))
        rec = _load(text, rtype)
        if rec[_ID_KEY[rtype]] != rid:
            _refuse("research_record_corrupt:" + rtype)
        by[rtype].append(rec)
    if len(by[QUESTION_SCHEMA]) != 1 or len(by[PLAN_SCHEMA]) != 1:
        _refuse("research_chain_incomplete")
    (q,), (p,) = by[QUESTION_SCHEMA], by[PLAN_SCHEMA]
    if build_plan(q) != p:
        _refuse("plan_rederivation_mismatch")
    runs = []
    for e in by[EVIDENCE_SCHEMA]:
        if e["plan_id"] != p["plan_id"] or e["plan_sha256"] != sha256(canonical(p)):
            _refuse("evidence_plan_mismatch")
        verify_evidence(e)
        if live is not None and sha256(live[0]) != e["frozen"]["case_payload_sha256"]:
            _refuse("live_case_changed")
        r = next((x for x in by[RESULT_SCHEMA] if x["evidence_id"] == e["evidence_id"]), None)
        rn = next((x for x in by[RUN_SCHEMA] if x["update_event_id"] == e["update_event_id"]), None)
        b = next((x for x in by[BANK_SCHEMA] if x["links"]["evidence"]["id"] == e["evidence_id"]), None)
        if r is None or rn is None or b is None:
            _refuse("research_chain_incomplete")
        if build_result(e) != r or build_run(q, p, e, r) != rn or build_bank(q, p, e, r, rn) != b:
            _refuse("research_rederivation_mismatch")
        runs.append({"update_event_id": e["update_event_id"], "evidence": e, "result": r,
                     "run": rn, "bank": b})
    return {"question": q, "plan": p, "runs": runs}
