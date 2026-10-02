"""Unreadable strategy-health research evidence —
strategy-health-unreadable-evidence.v1.

One deterministic, immutable, context_only evidence record per persisted,
fully re-verified strategy-health-unreadable-plan.v1. Collection FREEZES
exactly the records the plan already routes and nothing else: for each of
its three routes (``source_observation``, ``source_sweep``,
``spec_observation_history``), in plan order, every record the plan binds
by ``event_id`` + ``record_sha256`` is looked up by that event id alone and
frozen verbatim as its raw stored detail (``source_content``), with the
kind and subject the plan and its question bind. No other row is read into
the record: no further history is discovered, and earlier sweep records
the plan consulted only for membership stay unrouted and unbound exactly as
in the plan.

Each route's ``route_id``, ``source_id``, ``store``, ``reader``,
``record_schema``, ``record_kind`` and ``status`` are copied from the plan
unchanged, and must equal the plan's frozen routing mapping (which pins the
internal source registry's SHA-256; the registry itself is not read or
changed here). ``AVAILABLE`` / ``EMPTY`` keep the plan's meaning: routed
records present or absent — never data quality, capability or cause. An
``EMPTY`` route freezes no items.

The record weighs nothing. It classifies no record as support or
contradiction, concludes nothing (no data failure, no diagnosis), and
carries no falsifier predicate, threshold, regime, asset or symbol
attribution, source or vendor authority, score, rank, salience,
usefulness, priority, novelty, suppression, no-repeat or budget semantics.
Frozen content is the health writer's verbatim; any field it carries (a
verdict, an error class) is the source's, not a judgement made here.

Fail-closed. Before collection the plan is re-verified in full by
research_unreadable_plan.from_json (its source question re-verified, every
routed binding re-checked, byte equality with a fresh build). Every routed
record must then exist exactly once under its bound event id, with the
route's kind, the bound subject, the bound SHA-256 and a detail that
decodes under the health writer's own contract. Anything else — a
tampered, missing or rerouted plan, question or source record, or a
strategy-decay research-plan.v1 — refuses collection.

Isolation: records live in their own ``research_unreadable_evidence``
table. research-evidence.v1 (and its table), research_questions,
research_plans, research_registrations and the source registry are
untouched, and research-evidence.v1 still refuses unreadable plans.
`collect` is pure; `record_from_journal` is an offline writer nothing calls
from the live loop. No result, run, Research Bank, recall, registration,
network, LLM, Attention, Kernel, Risk, Execution or trading authority.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_question as rq
from trader.cognition import research_unreadable_plan as up
from trader.strategy import health_observation as ho

SCHEMA = "strategy-health-unreadable-evidence.v1"
EVIDENCE_KIND = up.PLAN_KIND                  # strategy_health_unreadable
COLLECTOR_ID = "strategy-health-unreadable-evidence-collection.v1"
AUTHORITY = "context_only"

SEMANTICS = ("context_only frozen evidence for one "
             "strategy-health-unreadable-plan.v1: the verbatim stored "
             "content of exactly the internal health records the plan "
             "routes, bound by event id and content hash; AVAILABLE / EMPTY "
             "state routed-record presence only; classifies nothing as "
             "support or contradiction and concludes nothing; no diagnosis, "
             "proven data failure, falsifier, threshold, regime, asset or "
             "symbol attribution, source or vendor authority, score, rank, "
             "salience, usefulness, priority, novelty, suppression or "
             "budget; not an Attention trigger; no network, LLM, Risk, "
             "Execution or trading authority")

_RECORD_KEYS = ("schema", "evidence_id", "evidence_kind", "collector_id",
                "authority", "source_plan", "scope", "routes", "semantics")
_SOURCE_PLAN_KEYS = ("schema", "plan_id", "plan_kind", "planner_id",
                     "question_id", "canonical_sha256")
#: route fields copied from the plan unchanged
_ROUTE_COPY = ("route_id", "source_id", "store", "reader", "record_schema",
               "record_kind", "status")
_ROUTE_KEYS = _ROUTE_COPY + ("items",)
_ITEM_KEYS = ("event_id", "record_sha256", "kind", "subject",
              "source_content")


class UnreadableEvidenceError(ValueError):
    """A plan cannot be collected, or a stored record failed verification."""


def _fail(code):
    raise UnreadableEvidenceError(code)


def canonical(payload) -> str:
    return rq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _same(a, b) -> bool:
    """Exact equality of JSON values (1, 1.0 and true differ)."""
    return canonical(a) == canonical(b)


def _keys_exact(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


# ── identity ─────────────────────────────────────────────────────────────
def _identity(rec: dict) -> dict:
    sp = rec["source_plan"]
    return {"schema": rec["schema"], "evidence_kind": rec["evidence_kind"],
            "collector_id": rec["collector_id"], "plan_id": sp["plan_id"],
            "plan_canonical_sha256": sp["canonical_sha256"]}


def evidence_id(rec: dict) -> str:
    return _sha(canonical(_identity(rec)))


# ── plan and question binding ────────────────────────────────────────────
def _verified_plan(plan_text, question_json, health_rows) -> dict:
    """The plan, re-verified in full against its question and the rows."""
    if not isinstance(plan_text, str):
        _fail("plan_invalid:not_text")
    try:
        return up.from_json(plan_text, question_json, health_rows)
    except up.UnreadablePlanError as e:
        raise UnreadableEvidenceError(f"plan_invalid:{e}") from e


def _subjects(plan: dict, question_json) -> dict:
    """record_kind -> the subject the plan and its question bind: the
    plan's spec for observations, the question's sweep for the sweep."""
    sq = plan["source_question"]
    if not isinstance(question_json, str) \
            or _sha(question_json) != sq["canonical_sha256"]:
        _fail("source_question_mismatch")
    try:
        sweep_id = json.loads(question_json)["source"]["sweep_id"]
    except (TypeError, ValueError, KeyError) as e:
        raise UnreadableEvidenceError("source_question_malformed") from e
    if not rq._nonempty(sweep_id):
        _fail("source_question_malformed")
    return {ho.KIND_SPEC: plan["scope"]["spec_id"], ho.KIND_SWEEP: sweep_id}


def _skeleton(plan: dict) -> list:
    """[(route fields, [record binding])] exactly as the plan routes them,
    checked against the frozen routing mapping."""
    if plan.get("schema") != up.SCHEMA or plan.get("plan_kind") != up.PLAN_KIND:
        _fail("plan_unsupported")
    mapping = {r["route_id"]: r for r in up.routing()["routes"]}
    routes = plan["routes"]
    if [r.get("route_id") for r in routes] != list(up.ROUTE_IDS):
        _fail("plan_routes")
    out = []
    for r in routes:
        d = mapping[r["route_id"]]
        if not all(_same(r[k], d[k]) for k in _ROUTE_COPY if k in d):
            _fail(f"plan_route_mapping:{r['route_id']}")
        records = r["records"]
        if r["status"] != (up.AVAILABLE if records else up.EMPTY):
            _fail(f"plan_route_status:{r['route_id']}")
        if d["cardinality"] == up.EXACTLY_ONE and len(records) != 1:
            _fail(f"plan_route_cardinality:{r['route_id']}")
        for b in records:
            if not (isinstance(b, dict) and set(b) == {"event_id",
                                                        "record_sha256"}
                    and rq._int(b["event_id"])
                    and isinstance(b["record_sha256"], str)):
                _fail(f"plan_binding_malformed:{r['route_id']}")
        out.append(({k: r[k] for k in _ROUTE_COPY}, records))
    return out


# ── one frozen item from its content alone ───────────────────────────────
def _check_item(route: dict, binding: dict, subject: str, content) -> None:
    what = f"{route['route_id']}:{binding['event_id']}"
    if not isinstance(content, str) \
            or _sha(content) != binding["record_sha256"]:
        _fail(f"source_binding_mismatch:{what}")
    # the health writer's own decode: schema, required fields, subject
    _, bad = ho._decode({"kind": route["record_kind"], "subject": subject,
                         "detail": content})
    if bad:
        _fail(f"source_malformed:{what}:{bad}")


def _item(route: dict, binding: dict, subject: str, content) -> dict:
    _check_item(route, binding, subject, content)
    return {"event_id": binding["event_id"],
            "record_sha256": binding["record_sha256"],
            "kind": route["record_kind"], "subject": subject,
            "source_content": content}


def _live_content(route: dict, binding: dict, subject: str, rows) -> str:
    """The raw detail of the one row under the bound event id, with the
    route's kind and the bound subject; nothing else is looked at."""
    eid, what = binding["event_id"], f"{route['route_id']}:{binding['event_id']}"
    hits = [r for r in rows if isinstance(r, dict) and r.get("id") == eid
            and rq._int(r.get("id"))]
    if len(hits) != 1:
        _fail(f"source_missing:{what}")
    row = hits[0]
    if (row.get("kind"), row.get("subject")) != (route["record_kind"],
                                                  subject):
        _fail(f"source_identity_mismatch:{what}")
    return row.get("detail")


# ── collection ───────────────────────────────────────────────────────────
def collect(plan_text: str, question_json: str, health_rows) -> dict:
    """The canonical strategy-health-unreadable-evidence.v1 for one stored
    plan text, its stored question JSON and the journal's health rows.
    Pure and deterministic; raises UnreadableEvidenceError when the plan
    does not re-verify or any routed record is missing, altered or breaks
    the health writer's contract."""
    rows = list(health_rows)
    plan = _verified_plan(plan_text, question_json, rows)
    subjects = _subjects(plan, question_json)
    routes = []
    for route, records in _skeleton(plan):
        subject = subjects[route["record_kind"]]
        routes.append({**route, "items": [
            _item(route, b, subject, _live_content(route, b, subject, rows))
            for b in records]})
    sq = plan["source_question"]
    rec = {"schema": SCHEMA, "evidence_kind": EVIDENCE_KIND,
           "collector_id": COLLECTOR_ID, "authority": AUTHORITY,
           "source_plan": {"schema": plan["schema"],
                           "plan_id": plan["plan_id"],
                           "plan_kind": plan["plan_kind"],
                           "planner_id": plan["planner_id"],
                           "question_id": sq["question_id"],
                           "canonical_sha256": _sha(plan_text)},
           "scope": dict(plan["scope"]), "routes": routes,
           "semantics": SEMANTICS}
    rec["evidence_id"] = evidence_id(rec)
    return json.loads(canonical(rec))


# ── contract verification ────────────────────────────────────────────────
def from_json(text: str, plan_text: str, question_json: str) -> dict:
    """Parse and verify a stored record on its own and against the exact
    stored plan text and question JSON it was collected from: exact keys at
    every level, contract constants, canonical form, evidence_id, plan and
    question binding, every route copied unchanged from the plan, and every
    item — from its frozen content alone — bound to the plan's event id and
    hash, the route's kind, the bound subject and the health writer's
    decode. Independent of whether the sources still exist; `load` checks
    those. Raises UnreadableEvidenceError."""
    try:
        rec = json.loads(text)
    except (TypeError, ValueError) as e:
        raise UnreadableEvidenceError("undecodable") from e
    _keys_exact(rec, _RECORD_KEYS, "keys")
    if (rec["schema"] != SCHEMA or rec["evidence_kind"] != EVIDENCE_KIND
            or rec["collector_id"] != COLLECTOR_ID
            or rec["authority"] != AUTHORITY
            or rec["semantics"] != SEMANTICS):
        _fail("contract")
    _keys_exact(rec["source_plan"], _SOURCE_PLAN_KEYS, "source_plan_keys")
    if canonical(rec) != text:
        _fail("not_canonical")
    if rec["evidence_id"] != evidence_id(rec):
        _fail("evidence_id")
    if not isinstance(plan_text, str) \
            or rec["source_plan"]["canonical_sha256"] != _sha(plan_text):
        _fail("source_plan_mismatch")
    try:
        plan = json.loads(plan_text)
        sq = plan["source_question"]
        expect_sp = {"schema": plan["schema"], "plan_id": plan["plan_id"],
                     "plan_kind": plan["plan_kind"],
                     "planner_id": plan["planner_id"],
                     "question_id": sq["question_id"],
                     "canonical_sha256": _sha(plan_text)}
        skel = _skeleton(plan)
    except (TypeError, ValueError, KeyError, AttributeError) as e:
        raise UnreadableEvidenceError("source_plan_malformed") from e
    if not (_same(rec["source_plan"], expect_sp)
            and _same(rec["scope"], plan["scope"])):
        _fail("source_plan_mismatch")
    subjects = _subjects(plan, question_json)
    if not isinstance(rec["routes"], list) or len(rec["routes"]) != len(skel):
        _fail("routes")
    for r, (route, records) in zip(rec["routes"], skel):
        _keys_exact(r, _ROUTE_KEYS, "route_keys")
        if not _same({k: r[k] for k in _ROUTE_COPY}, route):
            _fail(f"route_contract:{route['route_id']}")
        if not isinstance(r["items"], list) or len(r["items"]) != len(records):
            _fail(f"route_items:{route['route_id']}")
        subject = subjects[route["record_kind"]]
        for it, b in zip(r["items"], records):
            _keys_exact(it, _ITEM_KEYS, "item_keys")
            if not _same(it, _item(route, b, subject, it["source_content"])):
                _fail(f"item_contract:{route['route_id']}:{b['event_id']}")
    return rec


# ── durable record ───────────────────────────────────────────────────────
def row_for(rec: dict) -> dict:
    sp, text = rec["source_plan"], canonical(rec)
    [obs] = [r for r in rec["routes"]
             if r["route_id"] == up.SOURCE_OBSERVATION]
    return {"evidence_id": rec["evidence_id"], "schema": rec["schema"],
            "evidence_kind": rec["evidence_kind"],
            "collector_id": rec["collector_id"], "plan_id": sp["plan_id"],
            "plan_sha256": sp["canonical_sha256"],
            "question_id": sp["question_id"],
            "scope_kind": rec["scope"]["kind"],
            "scope_id": rec["scope"]["spec_id"],
            "source_event_id": obs["items"][0]["event_id"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def _plan_inputs(prow: dict, questions: dict, health) -> tuple:
    """(plan text, question JSON) of one stored plan row, both fully
    re-verified as research_unreadable_plan.load does."""
    qrow = questions.get(prow.get("question_id"))
    if qrow is None:
        _fail("plan_invalid:source_question_missing")
    try:
        qtext = up._question_text(qrow, health)
        plan = up.from_json(prow.get("canonical_json"), qtext, health)
    except up.UnreadablePlanError as e:
        raise UnreadableEvidenceError(f"plan_invalid:{e}") from e
    if canonical(up.row_for(plan)) != canonical(
            {k: prow.get(k) for k in up.row_for(plan)}):
        _fail("plan_invalid:row_projection")
    return prow["canonical_json"], qtext


def record_from_journal(journal, now_ms: int) -> dict:
    """Collect evidence for every stored unreadable plan that fully
    verifies. Returns {"inserted"|"duplicate"|"conflict": [evidence_id],
    "refusals": [(plan_id, reason)]}. Offline; not wired into any live
    path; enqueues and invokes nothing."""
    health = journal.strategy_health_rows()
    questions = {r["question_id"]: r
                 for r in journal.research_unreadable_questions()}
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for prow in journal.research_unreadable_plans():
        try:
            ptext, qtext = _plan_inputs(prow, questions, health)
            rec = collect(ptext, qtext, health)
        except UnreadableEvidenceError as e:
            res["refusals"].append((prow.get("plan_id"), str(e)))
            continue
        status = journal.record_research_unreadable_evidence(
            row_for(rec), recorded_at_ms=now_ms)
        res[status].append(rec["evidence_id"])
    return res


def load(journal, plan_id: str | None = None) -> list:
    """Stored records, each re-verified: its plan row (present and fully
    re-verified with its question against the current health rows), the
    record's contract and row projection, and every frozen item against
    the current row under its event id, which must still be byte-equal.
    Raises UnreadableEvidenceError on the first record that fails."""
    health = journal.strategy_health_rows()
    questions = {r["question_id"]: r
                 for r in journal.research_unreadable_questions()}
    plans = {r["plan_id"]: r for r in journal.research_unreadable_plans()}
    out = []
    for r in journal.research_unreadable_evidence(plan_id=plan_id):
        prow = plans.get(r["plan_id"])
        if prow is None:
            _fail("source_plan_missing")
        ptext, qtext = _plan_inputs(prow, questions, health)
        rec = from_json(r["canonical_json"], ptext, qtext)
        if canonical(row_for(rec)) != canonical({k: r[k]
                                                 for k in row_for(rec)}):
            _fail("row_projection")
        for route in rec["routes"]:
            for it in route["items"]:
                live = _live_content(route, it, it["subject"], health)
                if live != it["source_content"]:
                    _fail(f"source_changed:{route['route_id']}:"
                          f"{it['event_id']}")
        out.append(rec)
    return out
