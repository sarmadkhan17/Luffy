"""Strategy-decay prior research recall — context only.

Given one persisted research-question.v1, fully verified (its own
contract, row projection and bound health evidence), return the verified
research-bank-object.v1 records filed earlier for the same strategy, with
exact identity facts about each. Nothing is persisted and nothing is
decided: the recall has ``context_only`` authority. It never suppresses,
delays, ranks or alters the question, its plan, evidence, result or any
future run; a prior INCONCLUSIVE object is historical context only.

Eligibility (all required):

- scope: the object's ``scope`` is exactly ``{"kind": "strategy",
  "spec_id": <question.scope.spec_id>}``;
- known before registration: the bank object's verified first-registration
  time is strictly earlier than the question's verified first-registration
  time;
- source before source: the object's own verified question has a health
  source event strictly earlier (journal event id) than the new question's
  source event. An object known in time whose source is not earlier is
  listed under ``excluded`` with ``source_not_before_question``, never
  recalled.

Registration times. Times come only from research-registration.v1
receipts (``Journal.research_registrations``), never from a record row's
own ``recorded_at_ms`` column. A receipt is verified as: envelope_json is
exactly the canonical ``{schema, record_type, record_id, canonical_sha256,
recorded_at_ms}`` for this record type and ID, with an int time;
envelope_sha256 is its SHA-256; its columns are its projection; and its
canonical_sha256 is the SHA-256 of the record's canonical JSON. The
question's receipt, and the receipt of every bank row of the scope, is
verified before any row is selected; a missing receipt (a legacy row) or one
that fails raises RecallError: recall fails closed and never guesses a time.

Bound and order. At most ``max_objects`` bank objects registered before the
question are examined, latest registration first, ties by
``bank_object_id``; ``more_known_before`` says whether further ones exist
beyond the bound. Every examined row is verified exactly as
``research_bank.load`` verifies it; one that fails raises RecallError (fail
closed) and is never context.

Identity facts. For each recalled object, exact equality against the new
question — EQUAL, DIFFERENT, or NOT_EXPOSED when either side has no value —
on question_id, question canonical SHA-256, source event/sweep/record hash
and the spec/health/simulation-settings fingerprints; and, within the
recall, which other recalled objects bind the identical result_id or
evidence_id (and so the identical canonical hash). DIFFERENT states
identity only. There is no similarity, novelty, redundancy, usefulness,
success/failure, repeated-hypothesis, relevance, score, probability, rank,
priority, salience, cooldown, suppression or asset attribution here, and
nothing live calls it: no Kernel, Attention, Analyst, Risk, Execution, LLM
or network.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_bank as rb
from trader.cognition import research_question as rq

SCHEMA = "strategy-decay-prior-research-recall.v1"
AUTHORITY = "context_only"
DEFAULT_MAX_OBJECTS = 20
MAX_OBJECTS = 200
ORDER = "bank_registered_at_ms_desc_then_bank_object_id_asc"

EQUAL, DIFFERENT, NOT_EXPOSED = "EQUAL", "DIFFERENT", "NOT_EXPOSED"
SOURCE_NOT_BEFORE_QUESTION = "source_not_before_question"
REGISTRATION_SCHEMA = "research-registration.v1"
_REGISTRATION_KEYS = ("record_type", "record_id", "canonical_sha256",
                      "recorded_at_ms")

SEMANTICS = ("context-only recall of verified research-bank-object.v1 "
             "records filed for the same strategy before this question was "
             "registered and sourced; exact identity equality facts only; "
             "no similarity, novelty, redundancy, usefulness, success or "
             "failure, repeated hypothesis, relevance, score, probability, "
             "rank, priority, salience, cooldown, suppression or asset "
             "attribution; never prevents or alters question, plan, "
             "evidence, result or run creation; no network, LLM, Kernel, "
             "Attention, Analyst, Risk, Execution or trading authority")

#: source fields compared by exact equality (question.source key)
_SOURCE_FACTS = ("event_id", "sweep_id", "record_sha256",
                 "health_fingerprint", "spec_fingerprint",
                 "spec_content_sha256", "simulation_settings_sha256")


class RecallError(ValueError):
    """The question or an examined bank object failed verification."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _eq(new, prior) -> str:
    if new is None or prior is None:
        return NOT_EXPOSED
    return EQUAL if rq.canonical(new) == rq.canonical(prior) else DIFFERENT


def _registered_at(reg, record_type: str, record_id: str,
                   canonical_sha256: str) -> int:
    """The verified first-registration time of one record from its
    research-registration.v1 receipt. Raises RecallError."""
    def bad(why):
        return RecallError(f"registration_invalid:{record_id}:{why}")
    if reg is None or reg.get("envelope_json") is None:
        raise RecallError(f"registration_missing:{record_id}")
    try:
        env = json.loads(reg["envelope_json"])
    except (TypeError, ValueError) as e:
        raise bad("envelope_json") from e
    if not isinstance(env, dict):
        raise bad("envelope_json")
    ms = env.get("recorded_at_ms")
    if type(ms) is not int or ms < 0:
        raise bad("recorded_at_ms")
    want = {"schema": REGISTRATION_SCHEMA, "record_type": record_type,
            "record_id": record_id, "canonical_sha256": canonical_sha256,
            "recorded_at_ms": ms}
    if rq.canonical(want) != reg["envelope_json"]:
        raise bad("envelope")
    if _sha(reg["envelope_json"]) != reg.get("envelope_sha256"):
        raise bad("envelope_sha256")
    if any(reg.get(k) != want[k] or type(reg.get(k)) is not type(want[k])
           for k in _REGISTRATION_KEYS):
        raise bad("projection")
    return ms


def _question(journal, question_id):
    """(question, row) for one persisted question, verified as `rq.load`
    verifies it. Raises RecallError."""
    rows = [r for r in journal.research_questions()
            if r["question_id"] == question_id]
    if len(rows) != 1:
        raise RecallError("question_missing")
    try:
        q = rq.from_json(rows[0]["canonical_json"])
        if rq.canonical(rq.row_for(q)) != rq.canonical(
                {k: rows[0][k] for k in rq.row_for(q)}):
            raise RecallError("question_invalid:row_projection")
        rq.verify_evidence(q, journal.strategy_health_rows())
    except rq.ResearchQuestionError as e:
        raise RecallError(f"question_invalid:{e}") from e
    ms = _registered_at(journal.research_registration(rq.SCHEMA, question_id),
                        rq.SCHEMA, question_id,
                        _sha(rows[0]["canonical_json"]))
    return q, rows[0], ms


def _known_before(journal, scope, before_ms: int) -> list:
    """(registered_at_ms, bank_object_id) for every bank object of the
    scope whose verified registration is strictly before ``before_ms``,
    latest first, ties by ID. Every receipt of the scope is verified first,
    so a tampered or missing one cannot drop a row unnoticed."""
    out = []
    for r in journal.research_bank_registrations(rb.SCHEMA, scope["kind"],
                                                 scope["spec_id"]):
        ms = _registered_at(r, rb.SCHEMA, r["bank_object_id"],
                            r["bank_canonical_sha256"])
        if ms < before_ms:
            out.append((ms, r["bank_object_id"]))
    return sorted(out, key=lambda x: (-x[0], x[1]))


def _prior(row, reg_ms, rec, chain, q, q_sha) -> dict:
    lk, pq = rec["links"], chain["question"]
    identity = {"question_id": _eq(q["question_id"],
                                   lk["question"]["question_id"]),
                "question_canonical_sha256": _eq(
                    q_sha, lk["question"]["canonical_sha256"])}
    identity.update({f"source_{k}": _eq(q["source"][k], pq["source"][k])
                     for k in _SOURCE_FACTS})
    return {"bank_object_id": rec["bank_object_id"],
            "bank_registered_at_ms": reg_ms,
            "bank_canonical_sha256": row["canonical_sha256"],
            "run_id": lk["run"]["run_id"],
            "question_id": lk["question"]["question_id"],
            "question_canonical_sha256": lk["question"]["canonical_sha256"],
            "question_trigger": rec["question"]["trigger"],
            "plan_id": lk["plan"]["plan_id"],
            "evidence_id": lk["evidence"]["evidence_id"],
            "evidence_canonical_sha256": lk["evidence"]["canonical_sha256"],
            "result_id": lk["result"]["result_id"],
            "result_canonical_sha256": lk["result"]["canonical_sha256"],
            "result_status": rec["result_status"],
            "result_reason": rec["result_reason"],
            "source": {k: pq["source"][k] for k in _SOURCE_FACTS},
            "identity_vs_question": identity}


def recall(journal, question_id: str,
           max_objects: int = DEFAULT_MAX_OBJECTS) -> dict:
    """Context-only prior research for one persisted strategy-decay
    question. Read-only. Raises RecallError when the question or any
    examined bank object fails verification."""
    if (type(max_objects) is not int
            or not 1 <= max_objects <= MAX_OBJECTS):
        raise RecallError("max_objects")
    q, qrow, registered_ms = _question(journal, question_id)
    scope = q["scope"]
    src_event = q["source"]["event_id"]
    q_sha = _sha(qrow["canonical_json"])
    known = _known_before(journal, scope, registered_ms)
    more, known = len(known) > max_objects, known[:max_objects]
    prior, excluded = [], []
    for reg_ms, bank_object_id in known:
        row = journal.research_bank_object(bank_object_id)
        if row is None:
            raise RecallError(f"bank_object_invalid:{bank_object_id}:missing")
        try:
            rec, chain = rb.verify_row(journal, row)
        except rb.ResearchBankError as e:
            raise RecallError(
                f"bank_object_invalid:{row.get('bank_object_id')}:{e}") from e
        if rq.canonical(rec["scope"]) != rq.canonical(scope):
            raise RecallError(f"bank_object_invalid:"
                              f"{rec['bank_object_id']}:scope")
        if chain["question"]["source"]["event_id"] < src_event:
            prior.append(_prior(row, reg_ms, rec, chain, q, q_sha))
        else:
            excluded.append({"bank_object_id": rec["bank_object_id"],
                             "reason": SOURCE_NOT_BEFORE_QUESTION})
    for p in prior:
        for key in ("result_id", "evidence_id"):
            p[f"same_{key}_as"] = [o["bank_object_id"] for o in prior
                                   if o is not p and o[key] == p[key]]
    return {"schema": SCHEMA, "authority": AUTHORITY,
            "question": {"question_id": q["question_id"],
                         "canonical_sha256": q_sha, "scope": dict(scope),
                         "registered_at_ms": registered_ms,
                         "source_event_id": src_event,
                         "source_sweep_id": q["source"]["sweep_id"]},
            "cutoff": {"bank_registered_before_ms": registered_ms,
                       "prior_source_event_before": src_event},
            "bound": {"max_objects": max_objects, "order": ORDER,
                      "examined": len(known), "more_known_before": more},
            "prior": prior, "excluded": excluded, "semantics": SEMANTICS}
