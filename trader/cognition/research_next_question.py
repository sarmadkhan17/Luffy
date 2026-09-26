"""Research next questions — research-next-question.v1.

A deterministic, immutable record that makes one unresolved structural gap
of a verified Research Bank object durable and queryable. It is derived
only from the bank object's verified result (research-result.v1, reached
through `research_bank.verify_row`, which re-verifies the whole linked
chain), one record per result hypothesis entry, by a fixed one-for-one
mapping of registered (status, reason) pairs:

- (``NOT_ASSESSED``, ``no_registered_falsifier_predicates``)
  -> ``REGISTERED_FALSIFIER_PREDICATES_MISSING``
- (``UNAVAILABLE``, ``no_truthful_worldmodel_source``)
  -> ``TRUTHFUL_WORLDMODEL_SOURCE_MISSING``

Any other pair is unregistered: the bank object is refused and no record is
derived from it (fail closed, never partial). An UNAVAILABLE entry must
also appear verbatim in the result's ``unavailable_hypotheses``.

A record RECORDS a gap. It is not a proposed falsifier, predicate,
threshold, regime rule, WorldModel source, data vendor, research method,
plan or recommendation, and it does not satisfy or close the gap it names.
Its authority is ``context_only``: it is not enqueued or run, and carries
no score, rank, priority, novelty, salience, usefulness, suppression,
cooldown or no-repeat meaning.

Identity. ``next_question_id`` hashes the contract constants, the question
kind, the hypothesis and the exact bank object and result IDs and canonical
SHA-256s. The source entry is copied verbatim with its field index, so the
mapping is reproducible from the bound records. research-bank-object.v1 and
research-result.v1 are read, never modified.

`record_for_bank_object` is the only writer (insert / duplicate / conflict;
a conflict never overwrites) and reads one stored bank object by key. `load`
re-verifies each stored record against a fresh derivation from its
re-verified bank object. Nothing live calls any of this: no Kernel,
Attention, Analyst, Risk, Execution, LLM or network.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_bank as rb

SCHEMA = "research-next-question.v1"
GENERATOR_ID = "research-next-question-structural-mapping.v1"
AUTHORITY = "context_only"
SOURCE_FIELD = "hypotheses"

REGISTERED_FALSIFIER_PREDICATES_MISSING = \
    "REGISTERED_FALSIFIER_PREDICATES_MISSING"
TRUTHFUL_WORLDMODEL_SOURCE_MISSING = "TRUTHFUL_WORLDMODEL_SOURCE_MISSING"

_rr, _rp = rb.rr, rb.rp

#: (result hypothesis status, result hypothesis reason) -> question kind.
#: The only registered mappings; anything else fails closed.
MAPPINGS = {
    (_rr.NOT_ASSESSED, _rr.NO_REGISTERED_FALSIFIER_PREDICATES):
        REGISTERED_FALSIFIER_PREDICATES_MISSING,
    (_rr.UNAVAILABLE, _rp.NO_TRUTHFUL_WORLDMODEL_SOURCE):
        TRUTHFUL_WORLDMODEL_SOURCE_MISSING,
}
QUESTION_KINDS = tuple(MAPPINGS.values())

#: fixed gap statements; they name the gap and nothing else
STATEMENTS = {
    REGISTERED_FALSIFIER_PREDICATES_MISSING:
        "No registered falsifier predicate exists for hypothesis {h}; "
        "this records the gap only.",
    TRUTHFUL_WORLDMODEL_SOURCE_MISSING:
        "No truthful WorldModel/regime evidence source is available for "
        "hypothesis {h}; this records the gap only.",
}

SEMANTICS = ("context_only record of one unresolved structural gap named "
             "by a verified research-result.v1 hypothesis entry, mapped "
             "one-for-one from a registered status/reason pair; records the "
             "gap only and does not satisfy it; proposes no falsifier, "
             "predicate, threshold, regime rule, WorldModel source, data "
             "source, research method or plan; not enqueued or run; no "
             "score, rank, priority, novelty, salience, usefulness, "
             "suppression, cooldown or no-repeat authority; not an "
             "Attention trigger; no network, LLM, Kernel, Analyst, Risk, "
             "Execution or trading authority")

_RECORD_KEYS = ("schema", "next_question_id", "generator_id", "authority",
                "question_kind", "statement", "scope", "source", "semantics")
_SOURCE_KEYS = ("bank_object", "result", "field", "index", "entry")
_BANK_LINK_KEYS = ("schema", "bank_object_id", "canonical_sha256")
_RESULT_LINK_KEYS = ("schema", "result_id", "canonical_sha256")
_ENTRY_KEYS = ("hypothesis", "evidence_status", "status", "reason")


class ResearchNextQuestionError(ValueError):
    """A next question cannot be derived, or a stored one failed
    verification."""


def _fail(code):
    raise ResearchNextQuestionError(code)


def canonical(payload) -> str:
    return rb.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _identity(rec: dict) -> dict:
    src = rec["source"]
    return {"schema": rec["schema"], "generator_id": rec["generator_id"],
            "question_kind": rec["question_kind"],
            "hypothesis": src["entry"]["hypothesis"],
            "bank_object_id": src["bank_object"]["bank_object_id"],
            "bank_canonical_sha256": src["bank_object"]["canonical_sha256"],
            "result_id": src["result"]["result_id"],
            "result_canonical_sha256": src["result"]["canonical_sha256"]}


def next_question_id(rec: dict) -> str:
    return _sha(canonical(_identity(rec)))


# ── derive: a fixed mapping over a verified bank object and its result ───
def derive(bank: dict, result: dict) -> list:
    """The canonical research-next-question.v1 records, in result order,
    for one bank object and the result it binds, both already verified
    (`research_bank.verify_row`). Raises ResearchNextQuestionError, and
    derives nothing, when the bank object does not bind this exact result
    or any hypothesis entry carries an unregistered status/reason."""
    link = bank["links"]["result"]
    if (link["result_id"] != result["result_id"]
            or link["canonical_sha256"] != _sha(canonical(result))
            or link["schema"] != result["schema"]):
        _fail("source_mismatch:bank->result")
    if canonical(bank["scope"]) != canonical(result["scope"]):
        _fail("source_mismatch:scope")
    unavailable = [canonical(u) for u in result["unavailable_hypotheses"]]
    bank_ref = {"schema": bank["schema"],
                "bank_object_id": bank["bank_object_id"],
                "canonical_sha256": _sha(canonical(bank))}
    result_ref = {"schema": result["schema"],
                  "result_id": result["result_id"],
                  "canonical_sha256": _sha(canonical(result))}
    out, seen = [], set()
    for i, h in enumerate(result[SOURCE_FIELD]):
        entry = {k: h[k] for k in _ENTRY_KEYS}
        kind = MAPPINGS.get((entry["status"], entry["reason"]))
        if kind is None:
            _fail(f"unregistered_reason:{entry['status']}:{entry['reason']}")
        if entry["hypothesis"] in seen:
            _fail("duplicate_hypothesis")
        seen.add(entry["hypothesis"])
        if entry["status"] == _rr.UNAVAILABLE and canonical(
                {"hypothesis": entry["hypothesis"],
                 "reason": entry["reason"]}) not in unavailable:
            _fail("source_mismatch:unavailable_hypotheses")
        rec = {"schema": SCHEMA, "generator_id": GENERATOR_ID,
               "authority": AUTHORITY, "question_kind": kind,
               "statement": STATEMENTS[kind].format(h=entry["hypothesis"]),
               "scope": dict(result["scope"]),
               "source": {"bank_object": dict(bank_ref),
                          "result": dict(result_ref), "field": SOURCE_FIELD,
                          "index": i, "entry": entry},
               "semantics": SEMANTICS}
        rec["next_question_id"] = next_question_id(rec)
        out.append(json.loads(canonical(rec)))
    return out


def _verified_bank(journal, bank_object_id) -> tuple:
    """(bank object, its verified result) for one stored bank object, read
    by key and verified by the bank contract."""
    row = (journal.research_bank_object(bank_object_id)
           if isinstance(bank_object_id, str) and bank_object_id else None)
    if row is None:
        _fail("source_missing:bank_object")
    try:
        bank, chain = rb.verify_row(journal, row)
    except rb.ResearchBankError as e:
        raise ResearchNextQuestionError(f"source_invalid:bank:{e}") from e
    return bank, chain["result"]


# ── contract verification ────────────────────────────────────────────────
def _keys(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


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
    """Parse and verify a stored record on its own: strict JSON, exact keys
    at every level, contract constants, a registered mapping, the fixed
    statement, canonical form and next_question_id. `load` additionally
    re-derives it from its verified bank object. Raises
    ResearchNextQuestionError."""
    if not isinstance(text, str):
        _fail("not_text")
    try:
        rec = json.loads(text, object_pairs_hook=_no_dupes,
                         parse_constant=_non_finite)
    except ResearchNextQuestionError:
        raise
    except ValueError as e:
        raise ResearchNextQuestionError("undecodable") from e
    _keys(rec, _RECORD_KEYS, "keys")
    src = rec["source"]
    _keys(src, _SOURCE_KEYS, "source_keys")
    _keys(src["bank_object"], _BANK_LINK_KEYS, "bank_object_keys")
    _keys(src["result"], _RESULT_LINK_KEYS, "result_keys")
    _keys(src["entry"], _ENTRY_KEYS, "entry_keys")
    if (rec["schema"] != SCHEMA or rec["generator_id"] != GENERATOR_ID
            or rec["authority"] != AUTHORITY
            or rec["semantics"] != SEMANTICS
            or src["field"] != SOURCE_FIELD
            or src["bank_object"]["schema"] != rb.SCHEMA
            or src["result"]["schema"] != _rr.SCHEMA):
        _fail("contract")
    entry = src["entry"]
    if not isinstance(entry["hypothesis"], str) or MAPPINGS.get(
            (entry["status"], entry["reason"])) != rec["question_kind"]:
        _fail("mapping")
    if rec["statement"] != STATEMENTS[rec["question_kind"]].format(
            h=entry["hypothesis"]):
        _fail("statement")
    if canonical(rec) != text:
        _fail("not_canonical")
    if rec["next_question_id"] != next_question_id(rec):
        _fail("next_question_id")
    return rec


# ── durable record ───────────────────────────────────────────────────────
def row_for(rec: dict) -> dict:
    src = rec["source"]
    text = canonical(rec)
    return {"next_question_id": rec["next_question_id"],
            "schema": rec["schema"], "question_kind": rec["question_kind"],
            "generator_id": rec["generator_id"],
            "bank_object_id": src["bank_object"]["bank_object_id"],
            "result_id": src["result"]["result_id"],
            "hypothesis": src["entry"]["hypothesis"],
            "scope_kind": rec["scope"]["kind"],
            "scope_id": rec["scope"]["spec_id"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def record_for_bank_object(journal, bank_object_id: str,
                           now_ms: int) -> dict:
    """Derive and store the next questions of one stored bank object.
    Returns {"inserted"|"duplicate"|"conflict": [next_question_id],
    "refusals": [(bank_object_id, reason)]}. A bank object that fails
    verification, or carries any unregistered reason, is refused and
    nothing is written for it; a conflict never overwrites. Reads that one
    bank object by key; enqueues and runs nothing; not wired into any live
    path."""
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    try:
        recs = derive(*_verified_bank(journal, bank_object_id))
    except ResearchNextQuestionError as e:
        res["refusals"].append((bank_object_id, str(e)))
        return res
    for rec in recs:
        status = journal.record_research_next_question(
            row_for(rec), recorded_at_ms=now_ms)
        res[status].append(rec["next_question_id"])
    return res


def load(journal, bank_object_id: str | None = None) -> list:
    """Stored next questions, each re-verified: its own contract
    (`from_json`), its bank object re-verified through the bank contract
    (the whole linked chain), byte-equality with a fresh derivation, and
    the row projection. Raises ResearchNextQuestionError on the first
    record that fails."""
    out, derived = [], {}
    for r in journal.research_next_questions(bank_object_id=bank_object_id):
        rec = from_json(r["canonical_json"])
        bid = rec["source"]["bank_object"]["bank_object_id"]
        if bid not in derived:
            derived[bid] = {canonical(x) for x in
                            derive(*_verified_bank(journal, bid))}
        if r["canonical_json"] not in derived[bid]:
            _fail("rederive_mismatch")
        if canonical(row_for(rec)) != canonical(
                {k: r[k] for k in row_for(rec)}):
            _fail("row_projection")
        out.append(rec)
    return out
