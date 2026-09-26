"""Strategy-decay Research Bank objects — research-bank-object.v1.

One deterministic, immutable Research Bank object per verified result that
a completed research-run.v1 reached. It FILES what the already-persisted
chain recorded:

    research-question.v1 -> research-plan.v1 -> research-evidence.v1
    -> research-result.v1, reached by research-run.v1

and adds no research semantics. Every record is consumed only through its
own module's verification (`research_run.load`, `research_result.load`,
`research_evidence.load`, `research_plan.load`, and research_question's
`from_json` + row projection + `verify_evidence`), and the object binds the
exact ID and SHA-256 of the canonical JSON of each (``links``).

Fields:

- ``question``: the stored question's id, trigger, scope and text, verbatim;
- ``sources``: every evidence item's routed/frozen source reference
  (hypothesis, role, source, locator, plan binding, source identity,
  source hash, content hash) in evidence order — never the values;
- ``extracted_claims``: NOT_AVAILABLE, ``no_claim_extraction_contract``;
- ``supporting_evidence`` / ``contradictory_evidence``: NOT_CLASSIFIED,
  ``no_registered_falsifier_predicates`` — no falsifier predicate is
  registered, so nothing can be classified; this is a structural reason,
  not a finding about any market or strategy;
- ``experiments``: the run's step record (step, schema, status, reason,
  error) and the ID of this chain's record in each step's outcome, as
  structure only — not an experiment result. Each chain ID must occur
  exactly once across the step's inserted/duplicate and never in its
  conflict list, or the chain is refused;
- ``result_status`` / ``result_reason``: the result's own status and
  status_reason, verbatim (INCONCLUSIVE / no_registered_falsifier_predicates);
- ``limitations``: verbatim copies, each tagged with its origin record and
  field — the result's ``limitations`` (themselves verbatim evidence
  copies) and every run refusal keyed by exact ID to this chain: a
  question refusal naming this question's own source observation (spec,
  event and sweep — a same-spec refusal of another episode is omitted),
  and plan/evidence/result refusals naming this chain's question, plan or
  evidence ID;
- ``next_questions``: NOT_AVAILABLE, ``no_next_question_generator``;
- ``cost``: the run's research-run-telemetry.v1 steps verbatim (MEASURED
  values with unit and source, NOT_MEASURED with its exact reason). It is
  run-wide telemetry: not attributed to this object, and no budget, cap or
  cost limit is read into it.

There are no claims, support/refute classifications, falsifier
predicates, next questions, credibility, source quality, score,
probability, rank, priority, salience, usefulness, asset attribution or
regime evidence here.

Identity. ``bank_object_id`` hashes the run and result IDs and their
canonical SHA-256s. `record_from_journal` is the only writer (insert /
duplicate / conflict; a conflict never overwrites). `load` re-verifies the
whole linked chain through each source contract and rebuilds the object
byte-for-byte. Nothing live calls any of this: no Kernel, Attention,
Analyst, Risk, Execution, LLM or network.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_result as rr
from trader.cognition import research_run as run_

SCHEMA = "research-bank-object.v1"
BANK_KIND = "strategy_decay"
BUILDER_ID = "strategy-decay-research-bank.v1"

NOT_AVAILABLE = "NOT_AVAILABLE"
NOT_CLASSIFIED = "NOT_CLASSIFIED"
NO_CLAIM_EXTRACTION_CONTRACT = "no_claim_extraction_contract"
NO_REGISTERED_FALSIFIER_PREDICATES = rr.NO_REGISTERED_FALSIFIER_PREDICATES
NO_NEXT_QUESTION_GENERATOR = "no_next_question_generator"
EXPERIMENTS_KIND = "structural_run_step_record"
COST_SCOPE = "research_run"

# refusal reasons
RUN_NOT_COMPLETED = "run_not_completed"
RUN_REACHED_NO_RESULT = "run_reached_no_result"
RESULT_CONFLICT_IN_RUN = "result_conflict_in_run"

SEMANTICS = ("Research Bank filing of one verified strategy-decay result "
             "reached by one completed research run; binds the exact "
             "question, plan, evidence, result and run records by ID and "
             "canonical hash and copies their fields verbatim; no claim, "
             "support/refute classification, falsifier predicate, next "
             "question, credibility, source quality, score, probability, "
             "rank, priority, salience, usefulness, asset attribution or "
             "regime evidence; cost is the run-wide telemetry copied "
             "verbatim with no budget or cap; not an Attention trigger; no "
             "network, LLM, Kernel, Analyst, Risk, Execution or trading "
             "authority")

_RECORD_KEYS = ("schema", "bank_object_id", "bank_kind", "builder_id",
                "links", "scope", "question", "sources", "extracted_claims",
                "supporting_evidence", "contradictory_evidence",
                "experiments", "result_status", "result_reason",
                "limitations", "next_questions", "cost", "semantics")
_LINK_KEYS = {"question": ("schema", "question_id", "canonical_sha256"),
              "plan": ("schema", "plan_id", "canonical_sha256"),
              "evidence": ("schema", "evidence_id", "canonical_sha256"),
              "result": ("schema", "result_id", "canonical_sha256"),
              "run": ("schema", "run_id", "canonical_sha256",
                      "telemetry_schema", "telemetry_sha256")}
_QUESTION_KEYS = ("question_id", "trigger", "scope", "question")
_SOURCE_KEYS = ("hypothesis", "role", "source", "locator", "plan_binding",
                "source_identity", "source_sha256", "content_sha256")
_STATUS_KEYS = ("status", "reason")
_EXPERIMENT_KEYS = ("kind", "run_id", "inputs", "steps")
_EXPERIMENT_STEP_KEYS = ("step", "record_schema", "status", "reason",
                         "error", "chain_record_id", "chain_record_outcome")
_LIMITATION_KEYS = ("origin_schema", "origin_id", "origin_field", "entry")
_COST_KEYS = ("scope", "telemetry_schema", "run_id", "telemetry_sha256",
              "telemetry_semantics", "steps")

#: run step -> the refusal keys that must equal this chain's exact IDs.
#: A question refusal is attributed only when it names this question's own
#: source observation (spec, event and sweep); a spec match alone is
#: another episode and is omitted.
_REFUSAL_KEYS = {"question": ("spec_id", "event_id", "sweep_id"),
                 "plan": ("question_id",), "evidence": ("plan_id",),
                 "result": ("evidence_id",)}


class ResearchBankError(ValueError):
    """A bank object cannot be built, or a stored one failed verification."""


def _fail(code):
    raise ResearchBankError(code)


def canonical(payload) -> str:
    return rq.canonical(payload)


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
def _src(step, fn, *a, **k):
    try:
        return fn(*a, **k)
    except (rq.ResearchQuestionError, rp.ResearchPlanError,
            re_.ResearchEvidenceError, rr.ResearchResultError,
            run_.ResearchRunError) as e:
        raise ResearchBankError(f"source_invalid:{step}:{e}") from e


def _one(recs, key, value, step):
    match = [r for r in recs if r[key] == value]
    if len(match) != 1:
        _fail(f"source_missing:{step}")
    return match[0]


def _verified_run(journal, rid) -> dict:
    if not isinstance(rid, str) or not rid:
        _fail("source_missing:run")
    stored = _src("run", run_.load, journal, run_id=rid)
    if len(stored) != 1:
        _fail("source_missing:run")
    return stored[0]


def _question(journal, qid) -> dict:
    """The stored question, verified as research_run verifies it: its own
    contract, row projection and bound evidence."""
    rows = [r for r in journal.research_questions()
            if r["question_id"] == qid]
    if len(rows) != 1:
        _fail("source_missing:question")
    q = _src("question", rq.from_json, rows[0]["canonical_json"])
    if canonical(rq.row_for(q)) != canonical(
            {k: rows[0][k] for k in rq.row_for(q)}):
        _fail("source_invalid:question:row_projection")
    _src("question", rq.verify_evidence, q, journal.strategy_health_rows())
    return q


def _chain(journal, rid, result_id, stored=None) -> dict:
    """The verified run and the verified Q/P/E/R chain behind one result."""
    stored = stored or _verified_run(journal, rid)
    rrows = [r for r in journal.research_results()
             if r["result_id"] == result_id]
    if len(rrows) != 1:
        _fail("source_missing:result")
    res = _one(_src("result", rr.load, journal,
                    evidence_id=rrows[0]["evidence_id"]),
               "result_id", result_id, "result")
    se = res["source_evidence"]
    ev = _one(_src("evidence", re_.load, journal, plan_id=se["plan_id"]),
              "evidence_id", se["evidence_id"], "evidence")
    plan = _one(_src("plan", rp.load, journal,
                     question_id=se["question_id"]),
                "plan_id", se["plan_id"], "plan")
    return {"receipt": stored["receipt"], "telemetry": stored["telemetry"],
            "result": res, "evidence": ev, "plan": plan,
            "question": _question(journal, se["question_id"])}


# ── build: structure only, from a verified chain ─────────────────────────
def _check_links(q, plan, ev, res):
    """Each record binds its predecessor's exact ID and canonical hash."""
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
            or se["question_id"] != q["question_id"]
            or se["canonical_sha256"] != _sha(canonical(ev))):
        _fail("chain_mismatch:result->evidence")
    if not (canonical(q["scope"]) == canonical(plan["scope"])
            == canonical(ev["scope"]) == canonical(res["scope"])):
        _fail("chain_mismatch:scope")


def completed(receipt: dict) -> bool:
    return all(s["status"] == run_.COMPLETED for s in receipt["steps"])


def run_membership(step: dict, cid: str) -> str:
    """The one outcome ("inserted"|"duplicate") under which a run step
    returned chain record ``cid``: exactly one occurrence across inserted
    and duplicate, and none in conflict, or ResearchBankError."""
    out, name = step["outcomes"], step["step"]
    if cid in out["conflict"]:
        _fail(f"chain_record_conflict_in_run:{name}")
    hits = [k for k in ("inserted", "duplicate") for x in out[k] if x == cid]
    if not hits:
        _fail(f"chain_not_in_run:{name}")
    if len(hits) != 1:
        _fail(f"chain_record_repeated_in_run:{name}")
    return hits[0]


def _experiments(receipt, ids) -> dict:
    steps = []
    for s in receipt["steps"]:
        cid = ids[s["step"]]
        steps.append({"step": s["step"], "record_schema": s["record_schema"],
                      "status": s["status"], "reason": s["reason"],
                      "error": s["error"], "chain_record_id": cid,
                      "chain_record_outcome": run_membership(s, cid)})
    return {"kind": EXPERIMENTS_KIND, "run_id": receipt["run_id"],
            "inputs": dict(receipt["inputs"]), "steps": steps}


def _limitations(receipt, res, keys) -> list:
    out = [{"origin_schema": res["schema"], "origin_id": res["result_id"],
            "origin_field": "limitations", "entry": lim}
           for lim in res["limitations"]]
    for s in receipt["steps"]:
        ks = _REFUSAL_KEYS[s["step"]]
        out += [{"origin_schema": receipt["schema"],
                 "origin_id": receipt["run_id"],
                 "origin_field": f"steps.{s['step']}.outcomes.refusals",
                 "entry": r}
                for r in s["outcomes"]["refusals"]
                if all(r[k] == keys[k] for k in ks)]
    return out


def build(chain: dict) -> dict:
    """The canonical research-bank-object.v1 for one verified chain (see
    `_chain`). Raises ResearchBankError when the run is not completed,
    the chain does not bind exactly, or the chain is not in the run."""
    receipt, tel = chain["receipt"], chain["telemetry"]
    q, plan, ev, res = (chain["question"], chain["plan"], chain["evidence"],
                        chain["result"])
    if not completed(receipt):
        _fail(RUN_NOT_COMPLETED)
    _check_links(q, plan, ev, res)
    if (res["status"], res["status_reason"]) != (
            rr.INCONCLUSIVE, rr.NO_REGISTERED_FALSIFIER_PREDICATES):
        _fail("result_contract")
    ids = {"question": q["question_id"], "plan": plan["plan_id"],
           "evidence": ev["evidence_id"], "result": res["result_id"]}
    src = q["source"]
    keys = {"spec_id": src["spec_id"], "event_id": src["event_id"],
            "sweep_id": src["sweep_id"], "question_id": ids["question"],
            "plan_id": ids["plan"], "evidence_id": ids["evidence"]}

    def link(rec, id_key):
        return {"schema": rec["schema"], id_key: rec[id_key],
                "canonical_sha256": _sha(canonical(rec))}

    rec = {"schema": SCHEMA, "bank_kind": BANK_KIND, "builder_id": BUILDER_ID,
           "links": {"question": link(q, "question_id"),
                     "plan": link(plan, "plan_id"),
                     "evidence": link(ev, "evidence_id"),
                     "result": link(res, "result_id"),
                     "run": {**link(receipt, "run_id"),
                             "telemetry_schema": tel["schema"],
                             "telemetry_sha256": _sha(canonical(tel))}},
           "scope": dict(res["scope"]),
           "question": {k: q[k] for k in _QUESTION_KEYS},
           "sources": [{k: it[k] for k in _SOURCE_KEYS}
                       for s in ev["hypotheses"] for it in s["items"]],
           "extracted_claims": {"status": NOT_AVAILABLE,
                                "reason": NO_CLAIM_EXTRACTION_CONTRACT},
           "supporting_evidence": {"status": NOT_CLASSIFIED,
                                   "reason": NO_REGISTERED_FALSIFIER_PREDICATES},
           "contradictory_evidence": {
               "status": NOT_CLASSIFIED,
               "reason": NO_REGISTERED_FALSIFIER_PREDICATES},
           "experiments": _experiments(receipt, ids),
           "result_status": res["status"],
           "result_reason": res["status_reason"],
           "limitations": _limitations(receipt, res, keys),
           "next_questions": {"status": NOT_AVAILABLE,
                              "reason": NO_NEXT_QUESTION_GENERATOR},
           "cost": {"scope": COST_SCOPE, "telemetry_schema": tel["schema"],
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


def from_json(text: str) -> dict:
    """Parse and verify a stored object on its own: strict JSON (no
    duplicate keys or non-finite numbers), exact keys at every structural
    level, the fixed NOT_AVAILABLE/NOT_CLASSIFIED fields, contract
    constants, canonical form and bank_object_id. `load` additionally
    rebuilds it from the verified chain. Raises ResearchBankError."""
    if not isinstance(text, str):
        _fail("not_text")
    try:
        rec = json.loads(text, object_pairs_hook=_no_dupes,
                         parse_constant=_non_finite)
    except ResearchBankError:
        raise
    except ValueError as e:
        raise ResearchBankError("undecodable") from e
    _keys(rec, _RECORD_KEYS, "keys")
    _keys(rec["links"], _LINK_KEYS, "links_keys")
    for step, keys in _LINK_KEYS.items():
        _keys(rec["links"][step], keys, f"link_keys:{step}")
    _keys(rec["question"], _QUESTION_KEYS, "question_keys")
    for s in _list_of(rec["sources"], "sources"):
        _keys(s, _SOURCE_KEYS, "source_keys")
    for f, status, reason in (
            ("extracted_claims", NOT_AVAILABLE, NO_CLAIM_EXTRACTION_CONTRACT),
            ("supporting_evidence", NOT_CLASSIFIED,
             NO_REGISTERED_FALSIFIER_PREDICATES),
            ("contradictory_evidence", NOT_CLASSIFIED,
             NO_REGISTERED_FALSIFIER_PREDICATES),
            ("next_questions", NOT_AVAILABLE, NO_NEXT_QUESTION_GENERATOR)):
        _keys(rec[f], _STATUS_KEYS, f"{f}_keys")
        if rec[f] != {"status": status, "reason": reason}:
            _fail(f"{f}_contract")
    _keys(rec["experiments"], _EXPERIMENT_KEYS, "experiments_keys")
    for s in _list_of(rec["experiments"]["steps"], "experiments_steps"):
        _keys(s, _EXPERIMENT_STEP_KEYS, "experiment_step_keys")
    for lim in _list_of(rec["limitations"], "limitations"):
        _keys(lim, _LIMITATION_KEYS, "limitation_keys")
    _keys(rec["cost"], _COST_KEYS, "cost_keys")
    if (rec["schema"] != SCHEMA or rec["bank_kind"] != BANK_KIND
            or rec["builder_id"] != BUILDER_ID
            or rec["semantics"] != SEMANTICS
            or rec["result_status"] not in rr.STATUSES
            or rec["result_reason"] != NO_REGISTERED_FALSIFIER_PREDICATES
            or rec["experiments"]["kind"] != EXPERIMENTS_KIND
            or rec["cost"]["scope"] != COST_SCOPE):
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
    requires `run_membership` for every chain record, so a repeated or
    conflicting ID is refused, never filed. The run must be completed and its
    result step must have reached at least one result."""
    if not completed(receipt):
        _fail(RUN_NOT_COMPLETED)
    out = receipt["steps"][-1]["outcomes"]
    ids = list(dict.fromkeys(out["inserted"] + out["duplicate"]))
    conflicts = list(dict.fromkeys(out["conflict"]))
    if not (ids or conflicts):
        _fail(RUN_REACHED_NO_RESULT)
    return ids, conflicts


def record_from_journal(journal, now_ms: int) -> dict:
    """File one bank object per verified result each completed stored run
    reached. Returns {"inserted"|"duplicate"|"conflict": [bank_object_id],
    "refusals": [(run_id, result_id|None, reason)]}. A run or chain that
    fails verification is refused, never filed; a conflict is never
    overwritten. Not wired into any live path."""
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for row in journal.research_runs():
        rid = row.get("run_id")
        try:
            stored = _verified_run(journal, rid)
            ids, conflicts = reached_results(stored["receipt"])
        except ResearchBankError as e:
            res["refusals"].append((rid, None, str(e)))
            continue
        res["refusals"] += [(rid, x, RESULT_CONFLICT_IN_RUN)
                            for x in conflicts]
        for xid in ids:
            try:
                rec = build(_chain(journal, rid, xid, stored))
            except ResearchBankError as e:
                res["refusals"].append((rid, xid, str(e)))
                continue
            status = journal.record_research_bank_object(
                row_for(rec), recorded_at_ms=now_ms)
            res[status].append(rec["bank_object_id"])
    return res


def load(journal, run_id: str | None = None) -> list:
    """Stored bank objects, each re-verified: its own contract
    (`from_json`), the complete linked run/result/evidence/plan/question
    chain through each source's own verification, a byte-equal rebuild
    (so every bound ID, hash, copied field and nested type is exact), and
    the row projection. Raises ResearchBankError on the first object that
    fails."""
    return [verify_row(journal, r)[0]
            for r in journal.research_bank_objects(run_id=run_id)]


def verify_row(journal, r: dict) -> tuple:
    """(object, verified chain) for one stored bank-object row, verified
    exactly as `load` verifies it. Raises ResearchBankError."""
    rec = from_json(r["canonical_json"])
    lk = rec["links"]
    chain = _chain(journal, lk["run"]["run_id"], lk["result"]["result_id"])
    if canonical(build(chain)) != r["canonical_json"]:
        _fail("rebuild_mismatch")
    if canonical(row_for(rec)) != canonical({k: r[k] for k in row_for(rec)}):
        _fail("row_projection")
    return rec, chain
