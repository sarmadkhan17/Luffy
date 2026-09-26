"""Unreadable strategy-health Research Bank objects —
strategy-health-unreadable-bank-object.v1.

A sibling of the strategy-decay research-bank-object.v1 family. One
deterministic, immutable, context_only bank object per verified result that
a completed strategy-health-unreadable-run.v1 reached. It FILES what the
already-persisted unreadable chain recorded:

    strategy-health-unreadable-question.v1 -> -plan.v1 -> -evidence.v1
    -> -result.v1, reached by strategy-health-unreadable-run.v1

and adds no research semantics. Every record is consumed only through its
own module's verification (`research_unreadable_run.load`,
`research_unreadable_result.load`, `research_unreadable_evidence.load`,
`research_unreadable_plan.load`, and research_unreadable_question's
`from_json` + row projection + `verify_evidence`), and the object binds the
exact ID and SHA-256 of the canonical JSON of each (``links``).

Fields:

- ``question``: the stored question's identity and content verbatim (id,
  kind, scope, template text, bound source observation and sweep record,
  recorded reasons);
- ``plan``: the stored plan's identity, frozen routing reference, source
  question binding and routes (every routed record binding) verbatim;
- ``sources``: the evidence record's routes with each frozen item's
  identity only (event id, record SHA-256, kind, subject) — never the
  frozen ``source_content`` itself, which stays in the evidence record;
- ``result``: the structural result verbatim — INCONCLUSIVE /
  NOT_ASSESSED / no_registered_falsifier_predicates;
- ``extracted_claims`` / ``next_questions``: NOT_AVAILABLE;
- ``supporting_evidence`` / ``contradictory_evidence``: NOT_CLASSIFIED,
  ``no_registered_falsifier_predicates`` — a structural reason, not a
  finding about any market, strategy, data source or capability;
- ``experiments``: the run's step record (step, schema, status, reason,
  error) and this chain's record ID and outcome in each step, as structure
  only. Each chain ID must occur exactly once across the step's
  inserted/duplicate refs and never in its conflict list;
- ``limitations``: verbatim copies, each tagged with its origin record and
  field — the result's ``assessment`` and ``reason`` and every run refusal
  keyed by exact ID to this chain (a question refusal naming this
  question's own spec, event and sweep; plan/evidence/result refusals
  naming this chain's question, plan or evidence ID);
- ``source_liveness``: INHERITED — the object is loadable only while every
  record it binds, and every health row those records re-derive from,
  still re-verifies; the retained records are not independently
  authenticated upstream;
- ``cost``: the run's strategy-health-unreadable-run-telemetry.v1 steps
  verbatim (MEASURED with unit and source, NOT_MEASURED with its exact
  reason). Run-wide, not attributed to this result; total research cost is
  NOT_ESTABLISHED; no budget, cap or efficiency is read into it.

There is no claim, diagnosis, support/refute inference, falsifier
predicate, threshold, regime, asset attribution, credibility, score, rank,
salience, usefulness, priority, suppression, no-repeat, recall or
registration here.

Identity. ``bank_object_id`` hashes the run and result IDs and their
canonical SHA-256s. `record_from_journal` / `record_from_run` are the only
writers (insert / duplicate / conflict; a conflict never overwrites).
`load` re-verifies the whole linked chain through each source contract and
rebuilds the object byte-for-byte, so an unchanged stored object becomes
unloadable once any bound record or source row can no longer be verified.

Isolation: objects live in their own ``research_unreadable_bank_objects``
table with no registration receipt. research-bank-object.v1, its table,
research-bank-view.v1, recall, next questions, the cost ledger, the source
registry and every chain table are only read. A strategy-decay run, result
or bank object is refused here, and research-bank-object.v1 refuses these
objects. Nothing live calls any of this: no Kernel, Attention, Analyst,
Risk, Execution, LLM or network.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_unreadable_evidence as ue
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.cognition import research_unreadable_result as ur
from trader.cognition import research_unreadable_run as ru

SCHEMA = "strategy-health-unreadable-bank-object.v1"
BANK_KIND = ru.RUN_KIND                       # strategy_health_unreadable
BUILDER_ID = "strategy-health-unreadable-research-bank.v1"
AUTHORITY = "context_only"

NOT_AVAILABLE = "NOT_AVAILABLE"
NOT_CLASSIFIED = "NOT_CLASSIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
INHERITED = "INHERITED"
NO_CLAIM_EXTRACTION_CONTRACT = "no_claim_extraction_contract"
NO_REGISTERED_FALSIFIER_PREDICATES = ur.NO_REGISTERED_FALSIFIER_PREDICATES
NO_NEXT_QUESTION_GENERATOR = "no_next_question_generator"
EXPERIMENTS_KIND = "structural_run_step_record"
COST_SCOPE = "strategy_health_unreadable_run"
COST_ATTRIBUTION = "run_wide_not_attributed_to_this_result"
SOURCE_LIVENESS_REASON = ("loadable_only_while_every_bound_record_and_source_"
                          "row_reverifies;retained_records_not_independently_"
                          "authenticated_upstream")

# refusal reasons
RUN_NOT_COMPLETED = "run_not_completed"
RUN_REACHED_NO_RESULT = "run_reached_no_result"
RESULT_CONFLICT_IN_RUN = "result_conflict_in_run"

SEMANTICS = ("context_only Research Bank filing of one verified "
             "strategy-health-unreadable result reached by one completed "
             "unreadable run; binds the exact question, plan, evidence, "
             "result and run records by ID and canonical hash and copies "
             "their fields verbatim; INCONCLUSIVE / NOT_ASSESSED is "
             "structural; no claim, diagnosis, support/refute "
             "classification, falsifier predicate, threshold, regime, asset "
             "attribution, next question, credibility, score, rank, "
             "salience, usefulness, priority, suppression, no-repeat, recall "
             "or registration; cost is the run-wide telemetry copied "
             "verbatim, total research cost not established, no budget or "
             "cap; not an Attention trigger; no network, LLM, Kernel, "
             "Analyst, Risk, Execution or trading authority")

_RECORD_KEYS = ("schema", "bank_object_id", "bank_kind", "builder_id",
                "authority", "links", "scope", "question", "plan", "sources",
                "result", "extracted_claims", "supporting_evidence",
                "contradictory_evidence", "experiments", "limitations",
                "source_liveness", "next_questions", "cost", "semantics")
_LINK_KEYS = {"question": ("schema", "question_id", "canonical_sha256"),
              "plan": ("schema", "plan_id", "canonical_sha256"),
              "evidence": ("schema", "evidence_id", "canonical_sha256"),
              "result": ("schema", "result_id", "canonical_sha256"),
              "run": ("schema", "run_id", "canonical_sha256",
                      "telemetry_schema", "telemetry_sha256")}
_QUESTION_KEYS = ("question_id", "question_kind", "scope", "question",
                  "source", "recorded_reasons")
_PLAN_KEYS = ("plan_id", "plan_kind", "planner_id", "routing",
              "source_question", "routes")
_SOURCE_ROUTE_KEYS = ue._ROUTE_COPY + ("items",)
_SOURCE_ITEM_KEYS = ("event_id", "record_sha256", "kind", "subject")
_RESULT_KEYS = ("result_id", "result_kind", "resolver_id", "status",
                "assessment", "reason")
_STATUS_KEYS = ("status", "reason")
_EXPERIMENT_KEYS = ("kind", "run_id", "inputs", "steps")
_EXPERIMENT_STEP_KEYS = ("step", "record_schema", "status", "reason",
                         "error", "chain_record_id", "chain_record_outcome")
_LIMITATION_KEYS = ("origin_schema", "origin_id", "origin_field", "entry")
_COST_KEYS = ("scope", "attribution", "total_research_cost",
              "telemetry_schema", "run_id", "telemetry_sha256",
              "telemetry_semantics", "steps")

#: run step -> the refusal keys that must equal this chain's exact IDs
_REFUSAL_KEYS = {"question": ("spec_id", "event_id", "sweep_id"),
                 "plan": ("question_id",), "evidence": ("plan_id",),
                 "result": ("evidence_id",)}
_CHAIN_SCHEMAS = {"question": uq.SCHEMA, "plan": up.SCHEMA,
                  "evidence": ue.SCHEMA, "result": ur.SCHEMA}


class UnreadableBankError(ValueError):
    """A bank object cannot be built, or a stored one failed verification."""


def _fail(code):
    raise UnreadableBankError(code)


def canonical(payload) -> str:
    return uq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _identity(rec: dict) -> dict:
    lk = rec["links"]
    return {"schema": rec["schema"], "bank_kind": rec["bank_kind"],
            "builder_id": rec["builder_id"],
            "run_id": lk["run"]["run_id"],
            "run_canonical_sha256": lk["run"]["canonical_sha256"],
            "result_id": lk["result"]["result_id"],
            "result_canonical_sha256": lk["result"]["canonical_sha256"]}


def bank_object_id(rec: dict) -> str:
    return _sha(canonical(_identity(rec)))


# ── chain resolution: every record through its own contract ─────────────
_SOURCE_ERRORS = (uq.UnreadableQuestionError, up.UnreadablePlanError,
                  ue.UnreadableEvidenceError, ur.UnreadableResultError,
                  ru.UnreadableRunError)


def _src(step, fn, *a, **k):
    try:
        return fn(*a, **k)
    except _SOURCE_ERRORS as e:
        raise UnreadableBankError(f"source_invalid:{step}:{e}") from e


def _one(recs, key, value, step):
    match = [r for r in recs if r[key] == value]
    if len(match) != 1:
        _fail(f"source_missing:{step}")
    return match[0]


def _verified_run(journal, rid) -> dict:
    """The unreadable run, fully re-verified by its own `load` (which also
    re-verifies every object it references). A strategy-decay run lives in
    research_runs and is never found here."""
    if not isinstance(rid, str) or not rid:
        _fail("source_missing:run")
    stored = _src("run", ru.load, journal, run_id=rid)
    if len(stored) != 1:
        _fail("source_missing:run")
    return stored[0]


def _question(journal, qid) -> dict:
    """The stored question, verified as the unreadable runner verifies it:
    its own contract, row projection and bound health evidence."""
    row = (journal.research_unreadable_question_by_id(qid)
           if isinstance(qid, str) else None)
    if row is None:
        _fail("source_missing:question")
    q = _src("question", uq.from_json, row["canonical_json"])
    if canonical(uq.row_for(q)) != canonical(
            {k: row[k] for k in uq.row_for(q)}):
        _fail("source_invalid:question:row_projection")
    _src("question", uq.verify_evidence, q, journal.strategy_health_rows())
    return q


def _chain(journal, rid, result_id, stored=None) -> dict:
    """The verified run and the verified Q/P/E/R chain behind one result,
    each record read by its key. A strategy-decay result is never found
    in the unreadable result table."""
    stored = stored or _verified_run(journal, rid)
    rrow = (journal.research_unreadable_result_by_id(result_id)
            if isinstance(result_id, str) else None)
    if rrow is None:
        _fail("source_missing:result")
    res = _one(_src("result", ur.load, journal,
                    evidence_id=rrow["evidence_id"]),
               "result_id", result_id, "result")
    se = res["source_evidence"]
    ev = _one(_src("evidence", ue.load, journal, plan_id=se["plan_id"]),
              "evidence_id", se["evidence_id"], "evidence")
    plan = _one(_src("plan", up.load, journal,
                     question_id=se["question_id"]),
                "plan_id", se["plan_id"], "plan")
    return {"receipt": stored["receipt"], "telemetry": stored["telemetry"],
            "result": res, "evidence": ev, "plan": plan,
            "question": _question(journal, se["question_id"])}


# ── build: structure only, from a verified chain ─────────────────────────
def _check_links(q, plan, ev, res):
    """Each record binds its predecessor's exact ID and canonical hash."""
    for name, rec in (("question", q), ("plan", plan), ("evidence", ev),
                      ("result", res)):
        if rec.get("schema") != _CHAIN_SCHEMAS[name]:
            _fail(f"chain_family:{name}")
    sq, sp, se = (plan["source_question"], ev["source_plan"],
                  res["source_evidence"])
    if (sq["question_id"] != q["question_id"]
            or sq["canonical_sha256"] != _sha(canonical(q))):
        _fail("chain_mismatch:plan->question")
    if (sp["plan_id"] != plan["plan_id"]
            or sp["question_id"] != q["question_id"]
            or sp["canonical_sha256"] != _sha(canonical(plan))):
        _fail("chain_mismatch:evidence->plan")
    if (se["evidence_id"] != ev["evidence_id"]
            or se["plan_id"] != plan["plan_id"]
            or se["plan_sha256"] != _sha(canonical(plan))
            or se["question_id"] != q["question_id"]
            or se["canonical_sha256"] != _sha(canonical(ev))):
        _fail("chain_mismatch:result->evidence")
    if not (canonical(q["scope"]) == canonical(plan["scope"])
            == canonical(ev["scope"]) == canonical(res["scope"])):
        _fail("chain_mismatch:scope")


def completed(receipt: dict) -> bool:
    return all(s["status"] == ru.COMPLETED for s in receipt["steps"])


def run_membership(step: dict, obj: dict, id_key: str) -> str:
    """The one outcome ("inserted"|"duplicate") under which a run step
    returned chain record ``obj``: exactly one ref across inserted and
    duplicate, bound to the record's canonical hash and scope, and none in
    conflict, or UnreadableBankError."""
    out, name, cid = step["outcomes"], step["step"], obj[id_key]
    if cid in out["conflict"]:
        _fail(f"chain_record_conflict_in_run:{name}")
    hits = [(k, r) for k in ("inserted", "duplicate") for r in out[k]
            if r["object_id"] == cid]
    if not hits:
        _fail(f"chain_not_in_run:{name}")
    if len(hits) != 1:
        _fail(f"chain_record_repeated_in_run:{name}")
    kind, ref = hits[0]
    if (ref["canonical_sha256"] != _sha(canonical(obj))
            or canonical(ref["scope"]) != canonical(obj["scope"])):
        _fail(f"chain_ref_mismatch_in_run:{name}")
    return kind


_ID_KEYS = {"question": "question_id", "plan": "plan_id",
            "evidence": "evidence_id", "result": "result_id"}


def _experiments(receipt, objs) -> dict:
    steps = []
    for s in receipt["steps"]:
        id_key = _ID_KEYS[s["step"]]
        obj = objs[s["step"]]
        steps.append({"step": s["step"], "record_schema": s["record_schema"],
                      "status": s["status"], "reason": s["reason"],
                      "error": s["error"], "chain_record_id": obj[id_key],
                      "chain_record_outcome": run_membership(s, obj, id_key)})
    return {"kind": EXPERIMENTS_KIND, "run_id": receipt["run_id"],
            "inputs": dict(receipt["inputs"]), "steps": steps}


def _limitations(receipt, res, keys) -> list:
    out = [{"origin_schema": res["schema"], "origin_id": res["result_id"],
            "origin_field": f, "entry": res[f]}
           for f in ("assessment", "reason")]
    for s in receipt["steps"]:
        ks = _REFUSAL_KEYS[s["step"]]
        out += [{"origin_schema": receipt["schema"],
                 "origin_id": receipt["run_id"],
                 "origin_field": f"steps.{s['step']}.outcomes.refusals",
                 "entry": r}
                for r in s["outcomes"]["refusals"]
                if all(r[k] == keys[k] for k in ks)]
    return out


def _sources(ev) -> list:
    return [{**{k: r[k] for k in ue._ROUTE_COPY},
             "items": [{k: it[k] for k in _SOURCE_ITEM_KEYS}
                       for it in r["items"]]}
            for r in ev["routes"]]


def build(chain: dict) -> dict:
    """The canonical strategy-health-unreadable-bank-object.v1 for one
    verified chain (see `_chain`). Raises UnreadableBankError when the run
    is not a completed unreadable run, the chain is not the unreadable
    family or does not bind exactly, or the chain is not in the run."""
    receipt, tel = chain["receipt"], chain["telemetry"]
    q, plan, ev, res = (chain["question"], chain["plan"], chain["evidence"],
                        chain["result"])
    if (receipt.get("schema") != ru.SCHEMA
            or tel.get("schema") != ru.TELEMETRY_SCHEMA):
        _fail("chain_family:run")
    if not completed(receipt):
        _fail(RUN_NOT_COMPLETED)
    _check_links(q, plan, ev, res)
    if (res["status"], res["assessment"], res["reason"]) != (
            ur.INCONCLUSIVE, ur.NOT_ASSESSED,
            ur.NO_REGISTERED_FALSIFIER_PREDICATES):
        _fail("result_contract")
    objs = {"question": q, "plan": plan, "evidence": ev, "result": res}
    src = q["source"]
    keys = {"spec_id": src["spec_id"], "event_id": src["event_id"],
            "sweep_id": src["sweep_id"], "question_id": q["question_id"],
            "plan_id": plan["plan_id"], "evidence_id": ev["evidence_id"]}

    def link(rec, id_key):
        return {"schema": rec["schema"], id_key: rec[id_key],
                "canonical_sha256": _sha(canonical(rec))}

    rec = {"schema": SCHEMA, "bank_kind": BANK_KIND, "builder_id": BUILDER_ID,
           "authority": AUTHORITY,
           "links": {"question": link(q, "question_id"),
                     "plan": link(plan, "plan_id"),
                     "evidence": link(ev, "evidence_id"),
                     "result": link(res, "result_id"),
                     "run": {**link(receipt, "run_id"),
                             "telemetry_schema": tel["schema"],
                             "telemetry_sha256": _sha(canonical(tel))}},
           "scope": dict(res["scope"]),
           "question": {k: q[k] for k in _QUESTION_KEYS},
           "plan": {k: plan[k] for k in _PLAN_KEYS},
           "sources": _sources(ev),
           "result": {k: res[k] for k in _RESULT_KEYS},
           "extracted_claims": {"status": NOT_AVAILABLE,
                                "reason": NO_CLAIM_EXTRACTION_CONTRACT},
           "supporting_evidence": {"status": NOT_CLASSIFIED,
                                   "reason": NO_REGISTERED_FALSIFIER_PREDICATES},
           "contradictory_evidence": {
               "status": NOT_CLASSIFIED,
               "reason": NO_REGISTERED_FALSIFIER_PREDICATES},
           "experiments": _experiments(receipt, objs),
           "limitations": _limitations(receipt, res, keys),
           "source_liveness": {"status": INHERITED,
                               "reason": SOURCE_LIVENESS_REASON},
           "next_questions": {"status": NOT_AVAILABLE,
                              "reason": NO_NEXT_QUESTION_GENERATOR},
           "cost": {"scope": COST_SCOPE, "attribution": COST_ATTRIBUTION,
                    "total_research_cost": NOT_ESTABLISHED,
                    "telemetry_schema": tel["schema"],
                    "run_id": tel["run_id"],
                    "telemetry_sha256": _sha(canonical(tel)),
                    "telemetry_semantics": tel["semantics"],
                    "steps": tel["steps"]},
           "semantics": SEMANTICS}
    rec["bank_object_id"] = bank_object_id(rec)
    return json.loads(canonical(rec))


# ── contract verification ────────────────────────────────────────────────
def _keys(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


def _list_of(v, code):
    if not isinstance(v, list):
        _fail(code)
    return v


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            _fail("duplicate_json_key")
        out[k] = v
    return out


def _non_finite(_c):
    _fail("non_finite")


_FIXED = (("extracted_claims", NOT_AVAILABLE, NO_CLAIM_EXTRACTION_CONTRACT),
          ("supporting_evidence", NOT_CLASSIFIED,
           NO_REGISTERED_FALSIFIER_PREDICATES),
          ("contradictory_evidence", NOT_CLASSIFIED,
           NO_REGISTERED_FALSIFIER_PREDICATES),
          ("next_questions", NOT_AVAILABLE, NO_NEXT_QUESTION_GENERATOR),
          ("source_liveness", INHERITED, SOURCE_LIVENESS_REASON))


def from_json(text: str) -> dict:
    """Parse and verify a stored object on its own: strict JSON (no
    duplicate keys or non-finite numbers), exact keys at every structural
    level, the fixed NOT_AVAILABLE/NOT_CLASSIFIED/INHERITED fields, the
    structural result, contract constants, canonical form and
    bank_object_id. `load` additionally rebuilds it from the verified
    chain. Raises UnreadableBankError."""
    if not isinstance(text, str):
        _fail("not_text")
    try:
        rec = json.loads(text, object_pairs_hook=_no_dupes,
                         parse_constant=_non_finite)
    except UnreadableBankError:
        raise
    except ValueError as e:
        raise UnreadableBankError("undecodable") from e
    _keys(rec, _RECORD_KEYS, "keys")
    _keys(rec["links"], _LINK_KEYS, "links_keys")
    for step, keys in _LINK_KEYS.items():
        _keys(rec["links"][step], keys, f"link_keys:{step}")
    _keys(rec["question"], _QUESTION_KEYS, "question_keys")
    _keys(rec["plan"], _PLAN_KEYS, "plan_keys")
    for r in _list_of(rec["sources"], "sources"):
        _keys(r, _SOURCE_ROUTE_KEYS, "source_route_keys")
        for it in _list_of(r["items"], "source_items"):
            _keys(it, _SOURCE_ITEM_KEYS, "source_item_keys")
    _keys(rec["result"], _RESULT_KEYS, "result_keys")
    for f, status, reason in _FIXED:
        _keys(rec[f], _STATUS_KEYS, f"{f}_keys")
        if rec[f] != {"status": status, "reason": reason}:
            _fail(f"{f}_contract")
    _keys(rec["experiments"], _EXPERIMENT_KEYS, "experiments_keys")
    for s in _list_of(rec["experiments"]["steps"], "experiments_steps"):
        _keys(s, _EXPERIMENT_STEP_KEYS, "experiment_step_keys")
    for lim in _list_of(rec["limitations"], "limitations"):
        _keys(lim, _LIMITATION_KEYS, "limitation_keys")
    _keys(rec["cost"], _COST_KEYS, "cost_keys")
    res = rec["result"]
    if (rec["schema"] != SCHEMA or rec["bank_kind"] != BANK_KIND
            or rec["builder_id"] != BUILDER_ID
            or rec["authority"] != AUTHORITY
            or rec["semantics"] != SEMANTICS
            or res["status"] not in ur.STATUSES
            or res["assessment"] not in ur.ASSESSMENTS
            or res["reason"] not in ur.REASONS
            or rec["experiments"]["kind"] != EXPERIMENTS_KIND
            or rec["cost"]["scope"] != COST_SCOPE
            or rec["cost"]["attribution"] != COST_ATTRIBUTION
            or rec["cost"]["total_research_cost"] != NOT_ESTABLISHED):
        _fail("contract")
    if canonical(rec) != text:
        _fail("not_canonical")
    if rec["bank_object_id"] != bank_object_id(rec):
        _fail("bank_object_id")
    return rec


# ── durable record ───────────────────────────────────────────────────────
def row_for(rec: dict) -> dict:
    lk = rec["links"]
    text = canonical(rec)
    return {"bank_object_id": rec["bank_object_id"], "schema": rec["schema"],
            "bank_kind": rec["bank_kind"], "builder_id": rec["builder_id"],
            "run_id": lk["run"]["run_id"],
            "result_id": lk["result"]["result_id"],
            "evidence_id": lk["evidence"]["evidence_id"],
            "plan_id": lk["plan"]["plan_id"],
            "question_id": lk["question"]["question_id"],
            "scope_kind": rec["scope"]["kind"],
            "scope_id": rec["scope"]["spec_id"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def reached_results(receipt: dict) -> tuple:
    """(distinct result IDs the run's result step inserted or found
    duplicate, in that order; distinct conflict result IDs). Filing still
    requires `run_membership` for every chain record. The run must be
    completed and its result step must have reached at least one result."""
    if not completed(receipt):
        _fail(RUN_NOT_COMPLETED)
    out = receipt["steps"][-1]["outcomes"]
    ids = list(dict.fromkeys(r["object_id"]
                             for r in out["inserted"] + out["duplicate"]))
    conflicts = list(dict.fromkeys(out["conflict"]))
    if not (ids or conflicts):
        _fail(RUN_REACHED_NO_RESULT)
    return ids, conflicts


def _file_run(journal, rid, now_ms: int, res: dict) -> None:
    """File one bank object per verified result the stored unreadable run
    ``rid`` reached, appending to ``res``."""
    try:
        stored = _verified_run(journal, rid)
        ids, conflicts = reached_results(stored["receipt"])
    except UnreadableBankError as e:
        res["refusals"].append((rid, None, str(e)))
        return
    res["refusals"] += [(rid, x, RESULT_CONFLICT_IN_RUN)
                        for x in conflicts]
    for xid in ids:
        try:
            rec = build(_chain(journal, rid, xid, stored))
        except UnreadableBankError as e:
            res["refusals"].append((rid, xid, str(e)))
            continue
        status = journal.record_research_unreadable_bank_object(
            row_for(rec), recorded_at_ms=now_ms)
        res[status].append(rec["bank_object_id"])


def record_from_journal(journal, now_ms: int) -> dict:
    """File one bank object per verified result each completed stored
    unreadable run reached. Returns {"inserted"|"duplicate"|"conflict":
    [bank_object_id], "refusals": [(run_id, result_id|None, reason)]}. A
    run or chain that fails verification is refused, never filed; a
    conflict is never overwritten. Offline; not wired into any live path."""
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for row in journal.research_unreadable_runs():
        _file_run(journal, row.get("run_id"), now_ms, res)
    return res


def record_from_run(journal, run_id: str, now_ms: int) -> dict:
    """``record_from_journal`` scoped to one stored unreadable run: the
    same filing, verification and result shape, without reading any other
    run's receipt. Not a bound on verification work. Offline; not wired
    into any live path."""
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    _file_run(journal, run_id, now_ms, res)
    return res


def load(journal, run_id: str | None = None) -> list:
    """Stored bank objects, each re-verified: its own contract
    (`from_json`), the complete linked run/result/evidence/plan/question
    chain through each source's own verification (including live health
    sources), a byte-equal rebuild and the row projection. Raises
    UnreadableBankError on the first object that fails."""
    return [verify_row(journal, r)[0]
            for r in journal.research_unreadable_bank_objects(run_id=run_id)]


def verify_row(journal, r: dict) -> tuple:
    """(object, verified chain) for one stored bank-object row, verified
    exactly as `load` verifies it. Raises UnreadableBankError."""
    rec = from_json(r["canonical_json"])
    lk = rec["links"]
    chain = _chain(journal, lk["run"]["run_id"], lk["result"]["result_id"])
    if canonical(build(chain)) != r["canonical_json"]:
        _fail("rebuild_mismatch")
    if canonical(row_for(rec)) != canonical({k: r[k] for k in row_for(rec)}):
        _fail("row_projection")
    return rec, chain
