"""Unreadable strategy-health research results —
strategy-health-unreadable-result.v1.

One deterministic, immutable, context_only structural result per persisted,
fully re-verified strategy-health-unreadable-evidence.v1 record. The result
CLOSES the evidence record structurally; it does not read, weigh or compare
any frozen item.

Why every result is INCONCLUSIVE. No falsifier predicate is registered for
unreadable strategy-health evidence: nothing defines, before the evidence is
seen, what frozen content would count for or against any explanation. So:

- ``status`` is ``INCONCLUSIVE``;
- ``assessment`` is ``NOT_ASSESSED``;
- ``reason`` is ``no_registered_falsifier_predicates``.

There is no other outcome: this module has no status meaning anything is
supported or refuted, and names no cause. A ``compile_failed`` or
``evaluation_failed`` verdict in the frozen content is the health writer's
fact, not evidence of a data or capability failure, and is not read here.

Evidence is consumed only through `research_unreadable_evidence.load`, the
existing verification contract (the plan and its question re-verified
against the current health rows, canonical form, evidence_id, every frozen
item re-checked, every routed row still byte-equal). The result binds the
exact evidence_id and the SHA-256 of the evidence canonical JSON, and
carries the evidence's plan id / plan hash / question id and scope so the
binding can be followed; `load` re-verifies the evidence and rebuilds the
result byte-for-byte. A strategy-decay research-evidence.v1 record is
refused; research-result.v1 in turn still refuses unreadable evidence.

Source liveness is inherited, not added: a stored result is only as
loadable as its evidence. Deleting or altering any routed health row, the
plan or question rows, or earlier sweep membership the plan re-derives, makes
`load` fail closed even though the stored result and evidence bytes are
unchanged. Verification requires the retained plan/question text and does
not independently authenticate original upstream provenance.

Isolation: results live in their own ``research_unreadable_results`` table;
research-result.v1 and every other research table and the source registry
are untouched. `build` is pure; `record_from_journal` is an offline writer
nothing calls from the live loop. No run, Research Bank, recall,
registration, network, LLM, Attention, Kernel, Risk, Execution or trading
authority; no falsifier, threshold, diagnosis, regime, asset attribution,
label, score, rank, salience, priority, usefulness, source authority,
budget or suppression semantics.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_unreadable_evidence as ue

SCHEMA = "strategy-health-unreadable-result.v1"
RESULT_KIND = ue.EVIDENCE_KIND               # strategy_health_unreadable
RESOLVER_ID = "strategy-health-unreadable-structural-result.v1"
AUTHORITY = "context_only"

INCONCLUSIVE = "INCONCLUSIVE"
NOT_ASSESSED = "NOT_ASSESSED"
NO_REGISTERED_FALSIFIER_PREDICATES = "no_registered_falsifier_predicates"
#: the only values this module can produce
STATUSES = (INCONCLUSIVE,)
ASSESSMENTS = (NOT_ASSESSED,)
REASONS = (NO_REGISTERED_FALSIFIER_PREDICATES,)

SEMANTICS = ("context_only structural result for one "
             "strategy-health-unreadable-evidence.v1 record: INCONCLUSIVE "
             "and NOT_ASSESSED because no falsifier predicate is registered; "
             "frozen evidence is not read or weighed; nothing is supported "
             "or refuted and no cause, data failure or capability failure "
             "is named; no threshold, regime, asset attribution, label, "
             "score, rank, salience, priority, usefulness, source authority, "
             "budget or suppression; not an Attention trigger; no network, "
             "LLM, Risk, Execution or trading authority")

_RECORD_KEYS = ("schema", "result_id", "result_kind", "resolver_id",
                "authority", "source_evidence", "scope", "status",
                "assessment", "reason", "semantics")
_SOURCE_EVIDENCE_KEYS = ("schema", "evidence_id", "evidence_kind",
                         "collector_id", "plan_id", "plan_sha256",
                         "question_id", "canonical_sha256")


class UnreadableResultError(ValueError):
    """Evidence cannot be closed, or a stored result failed verification."""


def _fail(code):
    raise UnreadableResultError(code)


def canonical(payload) -> str:
    return ue.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _keys_exact(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


# ── identity ─────────────────────────────────────────────────────────────
def _identity(rec: dict) -> dict:
    se = rec["source_evidence"]
    return {"schema": rec["schema"], "result_kind": rec["result_kind"],
            "resolver_id": rec["resolver_id"],
            "evidence_id": se["evidence_id"],
            "evidence_canonical_sha256": se["canonical_sha256"]}


def result_id(rec: dict) -> str:
    return _sha(canonical(_identity(rec)))


# ── build: structure only, from a verified evidence record ───────────────
def build(evidence: dict) -> dict:
    """The canonical strategy-health-unreadable-result.v1 for one evidence
    record that `research_unreadable_evidence.load` has already fully
    verified. Reads only the evidence's identity, provenance and scope.
    Raises UnreadableResultError for any other evidence family."""
    if not isinstance(evidence, dict) or (
            evidence.get("schema") != ue.SCHEMA
            or evidence.get("evidence_kind") != ue.EVIDENCE_KIND
            or evidence.get("collector_id") != ue.COLLECTOR_ID
            or evidence.get("authority") != ue.AUTHORITY):
        _fail("evidence_contract")
    sp = evidence["source_plan"]
    rec = {"schema": SCHEMA, "result_kind": RESULT_KIND,
           "resolver_id": RESOLVER_ID, "authority": AUTHORITY,
           "source_evidence": {
               "schema": evidence["schema"],
               "evidence_id": evidence["evidence_id"],
               "evidence_kind": evidence["evidence_kind"],
               "collector_id": evidence["collector_id"],
               "plan_id": sp["plan_id"],
               "plan_sha256": sp["canonical_sha256"],
               "question_id": sp["question_id"],
               "canonical_sha256": _sha(canonical(evidence))},
           "scope": dict(evidence["scope"]),
           "status": INCONCLUSIVE, "assessment": NOT_ASSESSED,
           "reason": NO_REGISTERED_FALSIFIER_PREDICATES,
           "semantics": SEMANTICS}
    rec["result_id"] = result_id(rec)
    return json.loads(canonical(rec))


# ── contract verification ────────────────────────────────────────────────
def from_json(text: str, evidence: dict) -> dict:
    """Parse and verify a stored result against the verified evidence
    record it binds: exact keys, only the values this module defines,
    canonical form, result_id, the evidence id and hash binding, and
    byte-equality with a fresh `build`. Raises UnreadableResultError."""
    try:
        rec = json.loads(text)
    except (TypeError, ValueError) as e:
        raise UnreadableResultError("undecodable") from e
    _keys_exact(rec, _RECORD_KEYS, "keys")
    _keys_exact(rec["source_evidence"], _SOURCE_EVIDENCE_KEYS,
                "source_evidence_keys")
    if (rec["status"] not in STATUSES or rec["assessment"] not in ASSESSMENTS
            or rec["reason"] not in REASONS):
        _fail("status")
    if (rec["schema"] != SCHEMA or rec["result_kind"] != RESULT_KIND
            or rec["resolver_id"] != RESOLVER_ID
            or rec["authority"] != AUTHORITY
            or rec["semantics"] != SEMANTICS):
        _fail("contract")
    if canonical(rec) != text:
        _fail("not_canonical")
    if rec["result_id"] != result_id(rec):
        _fail("result_id")
    se = rec["source_evidence"]
    if (not isinstance(evidence, dict)
            or se["evidence_id"] != evidence.get("evidence_id")
            or se["canonical_sha256"] != _sha(canonical(evidence))):
        _fail("source_evidence_mismatch")
    if canonical(build(evidence)) != text:
        _fail("rebuild_mismatch")
    return rec


# ── durable record ───────────────────────────────────────────────────────
def row_for(rec: dict) -> dict:
    se, text = rec["source_evidence"], canonical(rec)
    return {"result_id": rec["result_id"], "schema": rec["schema"],
            "result_kind": rec["result_kind"],
            "resolver_id": rec["resolver_id"],
            "evidence_id": se["evidence_id"],
            "evidence_sha256": se["canonical_sha256"],
            "plan_id": se["plan_id"], "question_id": se["question_id"],
            "scope_kind": rec["scope"]["kind"],
            "scope_id": rec["scope"]["spec_id"], "status": rec["status"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def _verified_evidence(journal, plan_id, evidence_id) -> dict:
    """The evidence record, fully re-verified by the evidence contract."""
    try:
        recs = ue.load(journal, plan_id=plan_id)
    except ue.UnreadableEvidenceError as e:
        raise UnreadableResultError(f"source_evidence:{e}") from e
    match = [r for r in recs if r["evidence_id"] == evidence_id]
    if len(match) != 1:
        _fail("source_evidence_missing")
    return match[0]


def record_from_journal(journal, now_ms: int) -> dict:
    """Close every stored unreadable evidence record that fully verifies.
    Returns {"inserted"|"duplicate"|"conflict": [result_id], "refusals":
    [(evidence_id, reason)]}. Evidence that fails verification is refused,
    never closed. Offline; not wired into any live path."""
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for erow in journal.research_unreadable_evidence():
        try:
            ev = _verified_evidence(journal, erow.get("plan_id"),
                                    erow.get("evidence_id"))
            rec = build(ev)
        except UnreadableResultError as e:
            res["refusals"].append((erow.get("evidence_id"), str(e)))
            continue
        status = journal.record_research_unreadable_result(
            row_for(rec), recorded_at_ms=now_ms)
        res[status].append(rec["result_id"])
    return res


def load(journal, evidence_id: str | None = None) -> list:
    """Stored results, each re-verified: its source evidence re-verified by
    the evidence contract (including live sources), the exact evidence
    id/hash binding, canonical form and contract, a byte-equal rebuild, and
    the row projection. Raises UnreadableResultError on the first failure."""
    out = []
    for r in journal.research_unreadable_results(evidence_id=evidence_id):
        ev = _verified_evidence(journal, r["plan_id"], r["evidence_id"])
        rec = from_json(r["canonical_json"], ev)
        if canonical(row_for(rec)) != canonical({k: r[k]
                                                 for k in row_for(rec)}):
            _fail("row_projection")
        out.append(rec)
    return out
