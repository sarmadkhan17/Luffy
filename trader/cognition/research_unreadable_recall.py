"""Unreadable strategy-health prior research recall — context only.

strategy-health-unreadable-prior-research-recall.v1, a sibling of the
strategy-decay prior research recall. It shares no output, schema, reader
or verifier with it: the decay recall is left byte-identical and never sees
this family, and this recall never reads a strategy-decay question or bank
object.

Given one persisted strategy-health-unreadable-question.v1, fully verified
(its own contract, row projection and bound health evidence, as the
unreadable bank verifies it), return the verified
strategy-health-unreadable-bank-object.v1 records filed earlier for the same
strategy, with exact identity facts about each. Nothing is persisted and
nothing is decided: the recall has ``context_only`` authority. It never
suppresses, cools down, prevents, ranks or alters the question, its plan,
evidence, result or any future run, and it never says a prior object
answers the question; a prior INCONCLUSIVE object is historical context
only. There is no no-repeat policy.

Eligibility (all required):

- scope: the object's ``scope`` is exactly ``{"kind": "strategy",
  "spec_id": <question.scope.spec_id>}``;
- known before registration: the bank object's verified first-registration
  time is strictly earlier than the question's verified first-registration
  time (equal is excluded);
- source before source: the object's own verified question has a source
  health observation with a strictly smaller journal event id than the new
  question's source observation. That event id is the only order used: it
  is the question's own source identity, and question derivation already
  refuses a spec whose sweep order disagrees with write order. An object
  known in time whose source is not earlier is listed under ``excluded``
  with ``source_not_before_question``, never recalled.

Registration times. Times come only from research-registration.v1
receipts, never from a row's own ``recorded_at_ms`` (the bank reader does
not even select it) nor from any timestamp inside a record. A receipt is
verified as: envelope_json is exactly the canonical ``{schema, record_type,
record_id, canonical_sha256, recorded_at_ms}`` for this record type and ID,
with a non-negative int time; envelope_sha256 is its SHA-256; its columns
are its projection; and its canonical_sha256 is the SHA-256 of the stored
row's canonical JSON. The question's receipt, and the receipt of every
unreadable bank row of the scope, is verified before any row is selected; a
missing receipt (a legacy row filed before receipts existed; none is ever
backfilled) or one that fails raises UnreadableRecallError: recall fails
closed and never guesses a time or skips the row.

Bound and order. ``max_objects`` (required, 1..MAX_OBJECTS) bounds the bank
objects examined — fully verified and then source-filtered — among those
registered before the question, latest registration first, ties by
``bank_object_id``; ``more_known_before`` says whether further ones exist
beyond the bound. It does not bound database work: every receipt of the
scope is read and verified. Every examined row is verified exactly as
``research_unreadable_bank.verify_row`` verifies it (the whole linked
chain and its live health sources); one that fails raises
UnreadableRecallError (fail closed) and is never context.

Identity facts. For each recalled object, exact equality against the new
question — EQUAL, DIFFERENT, or NOT_EXPOSED when either side has no value —
on question_id, question canonical SHA-256, the source fields both
questions carry (event id, record hash, sweep id, sweep start, spec name,
health fingerprint, spec content hash, sweep record event id and hash) and
the recorded reasons (verdict, verdict_reason, coverage_complete,
coverage_faults, error_stage, error_class); and, within the recall, which
other recalled objects bind the identical result_id or evidence_id.
DIFFERENT states identity only. No field the unreadable family does not
carry (simulation settings, spec fingerprint, regime, asset) is reported.
There is no similarity, novelty, redundancy, usefulness, answer, success or
failure, repeated-hypothesis, relevance, score, probability, rank,
priority, salience, cooldown, suppression or asset attribution here, and
nothing live calls it: no Kernel, Attention, Analyst, Risk, Execution, LLM
or network.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_question as uq

SCHEMA = "strategy-health-unreadable-prior-research-recall.v1"
AUTHORITY = "context_only"
MAX_OBJECTS = 200
ORDER = "bank_registered_at_ms_desc_then_bank_object_id_asc"

EQUAL, DIFFERENT, NOT_EXPOSED = "EQUAL", "DIFFERENT", "NOT_EXPOSED"
SOURCE_NOT_BEFORE_QUESTION = "source_not_before_question"
REGISTRATION_SCHEMA = "research-registration.v1"
_REGISTRATION_KEYS = ("record_type", "record_id", "canonical_sha256",
                      "recorded_at_ms")

SEMANTICS = ("context-only recall of verified "
             "strategy-health-unreadable-bank-object.v1 records filed for the "
             "same strategy before this unreadable question was registered "
             "and sourced; exact identity equality facts only; no "
             "similarity, novelty, redundancy, usefulness, answer, success or "
             "failure, repeated hypothesis, relevance, score, probability, "
             "rank, priority, salience, cooldown, suppression, no-repeat or "
             "asset attribution; never prevents or alters question, plan, "
             "evidence, result or run creation; no network, LLM, Kernel, "
             "Attention, Analyst, Risk, Execution or trading authority")

#: question.source fields both unreadable questions carry
_SOURCE_FACTS = ("event_id", "record_sha256", "sweep_id", "sweep_started_at",
                 "spec_name", "health_fingerprint", "spec_content_sha256")
#: question.source.sweep_record fields
_SWEEP_FACTS = ("event_id", "record_sha256")
#: question.recorded_reasons fields
_REASON_FACTS = uq._REASON_KEYS


class UnreadableRecallError(ValueError):
    """The question or an examined bank object failed verification."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _eq(new, prior) -> str:
    if new is None or prior is None:
        return NOT_EXPOSED
    return EQUAL if uq.canonical(new) == uq.canonical(prior) else DIFFERENT


def _registered_at(reg, record_type: str, record_id: str,
                   canonical_sha256: str) -> int:
    """The verified first-registration time of one record from its
    research-registration.v1 receipt columns ({record_type, record_id,
    canonical_sha256, recorded_at_ms, envelope_sha256, envelope_json}).
    Raises UnreadableRecallError."""
    def bad(why):
        return UnreadableRecallError(f"registration_invalid:{record_id}:{why}")
    if reg is None or reg.get("envelope_json") is None:
        raise UnreadableRecallError(f"registration_missing:{record_id}")
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
    if uq.canonical(want) != reg["envelope_json"]:
        raise bad("envelope")
    if _sha(reg["envelope_json"]) != reg.get("envelope_sha256"):
        raise bad("envelope_sha256")
    if any(reg.get(k) != want[k] or type(reg.get(k)) is not type(want[k])
           for k in _REGISTRATION_KEYS):
        raise bad("projection")
    return ms


def _question(journal, question_id):
    """(question, row, registered_at_ms) for one persisted unreadable
    question, verified as the unreadable bank verifies it. A strategy-decay
    question lives in another table and is never found here. Raises
    UnreadableRecallError."""
    row = (journal.research_unreadable_question_by_id(question_id)
           if isinstance(question_id, str) and question_id else None)
    if row is None:
        raise UnreadableRecallError("question_missing")
    try:
        q = uq.from_json(row["canonical_json"])
        if uq.canonical(uq.row_for(q)) != uq.canonical(
                {k: row[k] for k in uq.row_for(q)}):
            raise UnreadableRecallError("question_invalid:row_projection")
        uq.verify_evidence(q, journal.strategy_health_rows())
    except uq.UnreadableQuestionError as e:
        raise UnreadableRecallError(f"question_invalid:{e}") from e
    ms = _registered_at(journal.research_registration(uq.SCHEMA, question_id),
                        uq.SCHEMA, question_id,
                        _sha(row["canonical_json"]))
    return q, row, ms


def _known_before(journal, scope, before_ms: int) -> list:
    """(registered_at_ms, bank row) for every unreadable bank object of the
    scope whose verified registration is strictly before ``before_ms``,
    latest first, ties by ID. Every receipt of the scope is verified first,
    so a tampered or missing one cannot drop a row unnoticed."""
    out = []
    for r in journal.research_unreadable_bank_registrations(
            ub.SCHEMA, scope["kind"], scope["spec_id"]):
        reg = ({k: r[f"reg_{k}"] for k in _REGISTRATION_KEYS
                + ("envelope_sha256", "envelope_json")})
        ms = _registered_at(reg, ub.SCHEMA, r["bank_object_id"],
                            _sha(r["canonical_json"]))
        if ms < before_ms:
            out.append((ms, r))
    return sorted(out, key=lambda x: (-x[0], x[1]["bank_object_id"]))


def _identity(q, q_sha, rec) -> dict:
    lk, pq = rec["links"], rec["question"]
    ns, ps = q["source"], pq["source"]
    out = {"question_id": _eq(q["question_id"],
                              lk["question"]["question_id"]),
           "question_canonical_sha256": _eq(
               q_sha, lk["question"]["canonical_sha256"])}
    out.update({f"source_{k}": _eq(ns[k], ps[k]) for k in _SOURCE_FACTS})
    out.update({f"source_sweep_record_{k}": _eq(ns["sweep_record"][k],
                                                ps["sweep_record"][k])
                for k in _SWEEP_FACTS})
    out.update({f"recorded_{k}": _eq(q["recorded_reasons"][k],
                                     pq["recorded_reasons"][k])
                for k in _REASON_FACTS})
    return out


def _prior(row, reg_ms, rec, q, q_sha) -> dict:
    lk, pq = rec["links"], rec["question"]
    return {"bank_object_id": rec["bank_object_id"],
            "bank_registered_at_ms": reg_ms,
            "bank_canonical_sha256": row["canonical_sha256"],
            "run_id": lk["run"]["run_id"],
            "question_id": lk["question"]["question_id"],
            "question_canonical_sha256": lk["question"]["canonical_sha256"],
            "plan_id": lk["plan"]["plan_id"],
            "evidence_id": lk["evidence"]["evidence_id"],
            "evidence_canonical_sha256": lk["evidence"]["canonical_sha256"],
            "result_id": lk["result"]["result_id"],
            "result_canonical_sha256": lk["result"]["canonical_sha256"],
            "result_status": rec["result"]["status"],
            "result_assessment": rec["result"]["assessment"],
            "result_reason": rec["result"]["reason"],
            "source": {**{k: pq["source"][k] for k in _SOURCE_FACTS},
                       "sweep_record": {k: pq["source"]["sweep_record"][k]
                                        for k in _SWEEP_FACTS}},
            "recorded_reasons": dict(pq["recorded_reasons"]),
            "identity_vs_question": _identity(q, q_sha, rec)}


def recall(journal, question_id: str, max_objects: int) -> dict:
    """Context-only prior research for one persisted unreadable
    strategy-health question. Read-only. Raises UnreadableRecallError when
    the question or any examined bank object fails verification."""
    if (type(max_objects) is not int
            or not 1 <= max_objects <= MAX_OBJECTS):
        raise UnreadableRecallError("max_objects")
    q, qrow, registered_ms = _question(journal, question_id)
    scope = q["scope"]
    src_event = q["source"]["event_id"]
    q_sha = _sha(qrow["canonical_json"])
    known = _known_before(journal, scope, registered_ms)
    more, known = len(known) > max_objects, known[:max_objects]
    prior, excluded = [], []
    for reg_ms, row in known:
        try:
            rec, chain = ub.verify_row(journal, row)
        except ub.UnreadableBankError as e:
            raise UnreadableRecallError(
                f"bank_object_invalid:{row.get('bank_object_id')}:{e}") from e
        if (uq.canonical(rec["scope"]) != uq.canonical(scope)
                or uq.canonical(chain["question"]["scope"])
                != uq.canonical(scope)):
            raise UnreadableRecallError(f"bank_object_invalid:"
                                        f"{rec['bank_object_id']}:scope")
        if chain["question"]["source"]["event_id"] < src_event:
            prior.append(_prior(row, reg_ms, rec, q, q_sha))
        else:
            excluded.append({"bank_object_id": rec["bank_object_id"],
                             "reason": SOURCE_NOT_BEFORE_QUESTION})
    for p in prior:
        for key in ("result_id", "evidence_id"):
            p[f"same_{key}_as"] = [o["bank_object_id"] for o in prior
                                   if o is not p and o[key] == p[key]]
    return {"schema": SCHEMA, "authority": AUTHORITY,
            "question": {"schema": q["schema"],
                         "question_id": q["question_id"],
                         "canonical_sha256": q_sha, "scope": dict(scope),
                         "registered_at_ms": registered_ms,
                         "source_event_id": src_event,
                         "source_sweep_id": q["source"]["sweep_id"]},
            "cutoff": {"bank_registered_before_ms": registered_ms,
                       "prior_source_event_before": src_event},
            "bound": {"max_objects": max_objects, "order": ORDER,
                      "examined": len(known), "more_known_before": more},
            "prior": prior, "excluded": excluded, "semantics": SEMANTICS}
