"""Unreadable strategy-health evidence-routing plans —
strategy-health-unreadable-plan.v1.

One deterministic, context_only plan per persisted, fully re-verified
strategy-health-unreadable-question.v1. The plan ROUTES evidence only: it
names the existing internal health records a reader would inspect for the
unreadable event and binds each by event id and SHA-256 of its stored raw
detail. It reads no field values, diagnoses nothing and does not claim a
data failure; it carries no falsifier, threshold, regime, asset or symbol
attribution, source or vendor choice, credibility, ranking, priority,
salience, usefulness, novelty, suppression, budget or cost.

Routes, from the frozen strategy-health-unreadable-routing.v1 mapping in
this module, always all three and in this order:

- ``source_observation``        — the question's own source observation;
- ``source_sweep``              — the question's own bound sweep record;
- ``spec_observation_history``  — every observation of the question's spec
  written before the source observation (the same-spec rows
  research_question._bounded already admits to verification). ``EMPTY``
  when there are none; never invented.

Every route reads the internal health store described by
research-source-registry.v1 entry ``internal.strategy_health_observations``.
The mapping lives here, pins that registry's SHA-256 and checks the
descriptor against it read-only: the registry is neither modified nor
versioned. Other registry sources (research questions, decisions, the
UNAVAILABLE WorldModel regime entry) are not routed.

Fail-closed. The question is re-verified in full (contract, row projection,
evidence via research_unreadable_question.verify_evidence) before planning.
The source observation and sweep rows must exist under exactly the ids and
hashes the question binds. History must be complete and exact: every sweep
started no later than the source sweep that lists the spec in
``observation_recorded_spec_ids`` has exactly one decodable observation of
the spec, and every history observation belongs to such a sweep. Anything
else refuses the plan.

Isolation: plans live in their own ``research_unreadable_plans`` table.
research-plan.v1, research_plans, research_questions, research_registrations
and the registry are untouched, and research-plan.v1 still refuses the
unreadable question. `build` is pure; `record_from_journal` is an offline
writer nothing calls from the live loop. No evidence, result, run, recall,
registration, network, LLM, Attention, Kernel, Risk, Execution or trading
authority.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_question as rq
from trader.cognition import research_sources as rs
from trader.cognition import research_unreadable_question as uq
from trader.strategy import health_observation as ho

SCHEMA = "strategy-health-unreadable-plan.v1"
PLAN_KIND = uq.QUESTION_KIND                  # strategy_health_unreadable
PLANNER_ID = "strategy-health-unreadable-evidence-routing.v1"
AUTHORITY = "context_only"

ROUTING_SCHEMA = "strategy-health-unreadable-routing.v1"
SOURCE_OBSERVATION = "source_observation"
SOURCE_SWEEP = "source_sweep"
SPEC_HISTORY = "spec_observation_history"
ROUTE_IDS = (SOURCE_OBSERVATION, SOURCE_SWEEP, SPEC_HISTORY)

AVAILABLE = "AVAILABLE"
EMPTY = "EMPTY"
EXACTLY_ONE = "exactly_one"
ZERO_OR_MORE = "zero_or_more"

SEMANTICS = ("context_only evidence-routing plan for one "
             "strategy-health-unreadable-question.v1; names and binds the "
             "existing internal health records to inspect and concludes "
             "nothing; no diagnosis, proven data failure, falsifier, "
             "threshold, regime, asset or symbol attribution, source or "
             "vendor choice, credibility, ranking, priority, salience, "
             "usefulness, novelty, suppression, budget or cost; not an "
             "Attention trigger; no network, LLM, Risk, Execution or trading "
             "authority")

# ── frozen routing mapping ───────────────────────────────────────────────
#: the registry entry every route reads; checked read-only, never changed
_HEALTH_DESCRIPTOR = {"store": "journal.brain_events",
                      "reader": "Journal.strategy_health_rows",
                      "record_schema": ho.SCHEMA}


def _route_def(route_id, record_kind, cardinality, selection) -> dict:
    return {"route_id": route_id, "source_id": rs.HEALTH_SOURCE_ID,
            **_HEALTH_DESCRIPTOR, "record_kind": record_kind,
            "cardinality": cardinality, "selection": selection}


_ROUTING_DEFINITION = {
    "schema": ROUTING_SCHEMA,
    "question_schema": uq.SCHEMA,
    "registry": {"schema": rs.SCHEMA, "sha256": rs.REGISTRY_SHA256,
                 "source_id": rs.HEALTH_SOURCE_ID},
    "routes": [
        _route_def(SOURCE_OBSERVATION, ho.KIND_SPEC, EXACTLY_ONE,
                   "the health row whose id is the question's "
                   "source.event_id; its detail SHA-256 must equal "
                   "source.record_sha256"),
        _route_def(SOURCE_SWEEP, ho.KIND_SWEEP, EXACTLY_ONE,
                   "the health row whose id is the question's "
                   "source.sweep_record.event_id; its detail SHA-256 must "
                   "equal source.sweep_record.record_sha256"),
        _route_def(SPEC_HISTORY, ho.KIND_SPEC, ZERO_OR_MORE,
                   "every observation row whose subject is the question's "
                   "spec_id and whose id is below source.event_id, in id "
                   "order; together with the source observation they must "
                   "be exactly one per sweep started no later than the "
                   "source sweep that lists the spec in "
                   "observation_recorded_spec_ids"),
    ],
}


class UnreadablePlanError(ValueError):
    """A question cannot be planned, or a stored plan failed verification."""


def _fail(code):
    raise UnreadablePlanError(code)


def canonical(payload) -> str:
    return rq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _freeze(definition) -> str:
    try:
        rs.require(rs.HEALTH_SOURCE_ID, _HEALTH_DESCRIPTOR)
    except rs.ResearchSourceError as e:
        raise UnreadablePlanError("routing:registry_descriptor") from e
    if definition["registry"]["sha256"] != rs.REGISTRY_SHA256:
        _fail("routing:registry_pin")
    if [r["route_id"] for r in definition["routes"]] != list(ROUTE_IDS):
        _fail("routing:routes")
    return canonical(definition)


_ROUTING = _freeze(_ROUTING_DEFINITION)
del _ROUTING_DEFINITION
#: pinned identity of the routing mapping; a change without a deliberate
#: new pin (and a new routing schema) fails import
ROUTING_SHA256 = (
    "87cea6d9f44134b8557e853de4ef7fb4cbda7645857282b4d089fb5a7ccf579c")
if _sha(_ROUTING) != ROUTING_SHA256:
    raise UnreadablePlanError("routing:pin_mismatch")


def routing() -> dict:
    """A fresh copy of the frozen routing mapping."""
    if _sha(_ROUTING) != ROUTING_SHA256:
        _fail("routing:pin_mismatch")
    return json.loads(_ROUTING)


def canonical_routing() -> str:
    routing()
    return _ROUTING


# ── identity ─────────────────────────────────────────────────────────────
_RECORD_KEYS = ("schema", "plan_id", "plan_kind", "planner_id", "authority",
                "routing", "source_question", "scope", "routes", "semantics")
_SOURCE_Q_KEYS = ("schema", "question_id", "question_kind",
                  "canonical_sha256")


def _identity(plan: dict) -> dict:
    sq = plan["source_question"]
    return {"schema": plan["schema"], "plan_kind": plan["plan_kind"],
            "planner_id": plan["planner_id"],
            "routing_sha256": plan["routing"]["sha256"],
            "question_id": sq["question_id"],
            "question_canonical_sha256": sq["canonical_sha256"]}


def plan_id(plan: dict) -> str:
    return _sha(canonical(_identity(plan)))


# ── question re-verification ─────────────────────────────────────────────
def _question(question_json, rows) -> dict:
    """The contract- and evidence-verified unreadable question."""
    if not isinstance(question_json, str):
        _fail("question_invalid:not_text")
    try:
        q = uq.from_json(question_json)
    except uq.UnreadableQuestionError as e:
        raise UnreadablePlanError(f"question_invalid:{e}") from e
    if q["question_kind"] != PLAN_KIND or q["schema"] != uq.SCHEMA:
        _fail("question_unsupported")
    try:
        uq.verify_evidence(q, rows)
    except uq.UnreadableQuestionError as e:
        raise UnreadablePlanError(f"question_evidence:{e}") from e
    return q


# ── routing ──────────────────────────────────────────────────────────────
def _binding(row) -> dict:
    return {"event_id": row["id"], "record_sha256": _sha(row["detail"])}


def _exact(rows, kind, event_id, sha, what) -> dict:
    hits = [r for r in rows if isinstance(r, dict) and r.get("kind") == kind
            and r.get("id") == event_id and rq._int(r.get("id"))]
    if len(hits) != 1 or not isinstance(hits[0].get("detail"), str):
        _fail(f"supporting_record_missing:{what}")
    if _sha(hits[0]["detail"]) != sha:
        _fail(f"supporting_record_mismatch:{what}")
    return hits[0]


def _history(q: dict, rows) -> list:
    """Same-spec observation rows before the source, id order, after the
    completeness check against the sweeps' own recorded lists."""
    src, spec_id = q["source"], q["scope"]["spec_id"]
    own = [r for r in rows if isinstance(r, dict)
           and r.get("kind") == ho.KIND_SPEC and r.get("subject") == spec_id]
    if any(not rq._int(r.get("id")) or not isinstance(r.get("detail"), str)
           for r in own):
        _fail("history_malformed")
    history = sorted((r for r in own if r["id"] < src["event_id"]),
                     key=lambda r: r["id"])
    observed = [src["sweep_id"]]
    for r in history:
        rec, bad = ho._decode(r)
        if bad or not rq._nonempty(rec.get("sweep_id")) \
                or rec.get("spec_id") != spec_id:
            _fail("history_malformed")
        observed.append(rec["sweep_id"])
    hi = rq._ms(src["sweep_started_at"])
    expected = []
    for r in rows:
        if not (isinstance(r, dict) and r.get("kind") == ho.KIND_SWEEP):
            continue
        rec, bad = ho._decode(r)
        sw = None if bad else rq._sweep(rec)
        # an unparseable sweep here lies after the source by write order:
        # verify_evidence already refused any within the bounded history
        if sw is not None and sw["started_ms"] <= hi \
                and spec_id in sw["observation_recorded_spec_ids"]:
            expected.append(sw["sweep_id"])
    if sorted(observed) != sorted(expected) or \
            len(set(observed)) != len(observed):
        _fail("history_incomplete")
    return history


def _route(route_id, rows) -> dict:
    d = next(r for r in routing()["routes"] if r["route_id"] == route_id)
    records = [_binding(r) for r in rows]
    if d["cardinality"] == EXACTLY_ONE and len(records) != 1:
        _fail(f"supporting_record_missing:{route_id}")
    return {"route_id": route_id, "source_id": d["source_id"],
            "store": d["store"], "reader": d["reader"],
            "record_schema": d["record_schema"],
            "record_kind": d["record_kind"],
            "status": AVAILABLE if records else EMPTY, "records": records}


def _routes(q: dict, rows) -> list:
    src = q["source"]
    obs = _exact(rows, ho.KIND_SPEC, src["event_id"], src["record_sha256"],
                 SOURCE_OBSERVATION)
    if obs.get("subject") != q["scope"]["spec_id"]:
        _fail(f"supporting_record_mismatch:{SOURCE_OBSERVATION}")
    ref = src["sweep_record"]
    sweep = _exact(rows, ho.KIND_SWEEP, ref["event_id"], ref["record_sha256"],
                   SOURCE_SWEEP)
    if sweep.get("subject") != src["sweep_id"]:
        _fail(f"supporting_record_mismatch:{SOURCE_SWEEP}")
    return [_route(SOURCE_OBSERVATION, [obs]),
            _route(SOURCE_SWEEP, [sweep]),
            _route(SPEC_HISTORY, _history(q, rows))]


def build(question_json: str, health_rows) -> dict:
    """The canonical strategy-health-unreadable-plan.v1 for one stored
    question JSON and the journal's health rows. Pure and deterministic;
    refuses (UnreadablePlanError) a question that is not a contract-valid,
    evidence-verified unreadable question, or whose routed records are
    missing, altered or incomplete."""
    rows = list(health_rows)
    q = _question(question_json, rows)
    plan = {
        "schema": SCHEMA,
        "plan_kind": PLAN_KIND,
        "planner_id": PLANNER_ID,
        "authority": AUTHORITY,
        "routing": {"schema": ROUTING_SCHEMA, "sha256": ROUTING_SHA256},
        "source_question": {"schema": q["schema"],
                            "question_id": q["question_id"],
                            "question_kind": q["question_kind"],
                            "canonical_sha256": _sha(question_json)},
        "scope": dict(q["scope"]),
        "routes": _routes(q, rows),
        "semantics": SEMANTICS,
    }
    plan["plan_id"] = plan_id(plan)
    return json.loads(canonical(plan))


# ── contract verification ────────────────────────────────────────────────
def from_json(text: str, question_json: str, health_rows) -> dict:
    """Parse and verify a stored plan: exact keys, contract constants,
    canonical form, plan_id, question binding, and byte equality with
    `build(question_json, health_rows)` — so every record binding is
    re-checked. Raises UnreadablePlanError."""
    try:
        plan = json.loads(text)
    except (TypeError, ValueError) as e:
        raise UnreadablePlanError("undecodable") from e
    if not isinstance(plan, dict) or set(plan) != set(_RECORD_KEYS):
        _fail("keys")
    if (plan["schema"] != SCHEMA or plan["plan_kind"] != PLAN_KIND
            or plan["planner_id"] != PLANNER_ID
            or plan["authority"] != AUTHORITY
            or plan["semantics"] != SEMANTICS
            or plan["routing"] != {"schema": ROUTING_SCHEMA,
                                   "sha256": ROUTING_SHA256}):
        _fail("contract")
    sq = plan["source_question"]
    if not isinstance(sq, dict) or set(sq) != set(_SOURCE_Q_KEYS):
        _fail("source_question_keys")
    if canonical(plan) != text:
        _fail("not_canonical")
    if plan["plan_id"] != plan_id(plan):
        _fail("plan_id")
    if sq["canonical_sha256"] != _sha(question_json):
        _fail("source_question_mismatch")
    if text != canonical(build(question_json, health_rows)):
        _fail("plan_content")
    return plan


# ── durable record ───────────────────────────────────────────────────────
def row_for(plan: dict) -> dict:
    sq, text = plan["source_question"], canonical(plan)
    [obs] = [r for r in plan["routes"] if r["route_id"] == SOURCE_OBSERVATION]
    return {"plan_id": plan["plan_id"], "schema": plan["schema"],
            "plan_kind": plan["plan_kind"], "planner_id": plan["planner_id"],
            "question_id": sq["question_id"],
            "question_sha256": sq["canonical_sha256"],
            "scope_kind": plan["scope"]["kind"],
            "scope_id": plan["scope"]["spec_id"],
            "source_event_id": obs["records"][0]["event_id"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def _question_text(qrow: dict, rows) -> str:
    """The stored question's JSON after its contract, row-projection and
    evidence checks."""
    text = qrow.get("canonical_json")
    q = _question(text, rows)
    if canonical(uq.row_for(q)) != canonical({k: qrow.get(k)
                                              for k in uq.row_for(q)}):
        _fail("question_invalid:row_projection")
    return text


def record_from_journal(journal, now_ms: int) -> dict:
    """Plan every stored unreadable question that verifies. Returns
    {"inserted"|"duplicate"|"conflict": [plan_id], "refusals":
    [(question_id, reason)]}. Offline; not wired into any live path;
    enqueues and invokes nothing."""
    health = journal.strategy_health_rows()
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for qrow in journal.research_unreadable_questions():
        try:
            plan = build(_question_text(qrow, health), health)
        except UnreadablePlanError as e:
            res["refusals"].append((qrow.get("question_id"), str(e)))
            continue
        status = journal.record_research_unreadable_plan(
            row_for(plan), recorded_at_ms=now_ms)
        res[status].append(plan["plan_id"])
    return res


def load(journal, question_id: str | None = None) -> list:
    """Stored plans, each re-verified: its source question row (present,
    contract-valid, evidence-verified), every record binding, byte equality
    with a fresh build and its row projection. Raises UnreadablePlanError
    on the first plan that fails."""
    health = journal.strategy_health_rows()
    questions = {r["question_id"]: r
                 for r in journal.research_unreadable_questions()}
    out = []
    for r in journal.research_unreadable_plans(question_id=question_id):
        qrow = questions.get(r["question_id"])
        if qrow is None:
            _fail("source_question_missing")
        plan = from_json(r["canonical_json"], _question_text(qrow, health),
                         health)
        if canonical(row_for(plan)) != canonical({k: r[k]
                                                  for k in row_for(plan)}):
            _fail("row_projection")
        out.append(plan)
    return out
