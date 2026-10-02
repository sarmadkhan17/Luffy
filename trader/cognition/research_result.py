"""Strategy-decay research results — research-result.v1.

One deterministic, immutable result per persisted, fully verified
strategy_decay research-evidence.v1 record. The result CLOSES the evidence
record structurally; it does not read, weigh or compare any frozen value.

Why every result is INCONCLUSIVE. No falsifier predicate is registered for
any strategy-decay hypothesis: nothing defines, before the evidence is
seen, what frozen values would count for or against a hypothesis. Without
one, no hypothesis can be assessed, so:

- ``status`` is ``INCONCLUSIVE`` with reason
  ``no_registered_falsifier_predicates``;
- each ROUTED evidence section (``genuine_deterioration``,
  ``insufficient_recent_opportunities``, ``data_failure``) is
  ``NOT_ASSESSED`` for the same reason;
- ``temporary_regime_absence`` stays ``UNAVAILABLE`` with the evidence
  section's own reason, ``no_truthful_worldmodel_source``, and is also
  listed in ``unavailable_hypotheses``.

There is no other outcome: this module has no status meaning a hypothesis
holds or fails, and names no explanation as true. An evidence record whose
sections do not match that fixed layout is refused, not reinterpreted.

``limitations`` carries forward, verbatim, what the evidence record itself
states it could not provide: each UNAVAILABLE section's reason and detail,
and each decision-selection report (a decision candidate the selection rule
reported instead of selecting). Nothing is added, dropped or reworded.

Evidence is consumed only through `research_evidence.load`, the existing
verification contract (canonical form, plan binding, frozen-content
re-derivation, live-source checks). The result binds the exact evidence_id
and the SHA-256 of the evidence canonical JSON; `load` re-verifies that
binding and rebuilds the result byte-for-byte.

`record_from_journal` is the only writer (insert / duplicate / conflict).
Nothing calls any of this from the live loop; there is no network, LLM,
Attention, Kernel, Analyst, Risk, Execution or trading authority here, and
no score, probability, ranking, priority, salience, usefulness, threshold
or asset attribution.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_evidence as re_

rp = re_.rp          # hypothesis/section vocabulary, as the evidence carries it

SCHEMA = "research-result.v1"
RESULT_KIND = "strategy_decay"
RESOLVER_ID = "strategy-decay-structural-result.v1"

INCONCLUSIVE = "INCONCLUSIVE"
NOT_ASSESSED = "NOT_ASSESSED"
UNAVAILABLE = rp.UNAVAILABLE
#: the only result statuses this module can produce
STATUSES = (INCONCLUSIVE,)
HYPOTHESIS_STATUSES = (NOT_ASSESSED, UNAVAILABLE)

NO_REGISTERED_FALSIFIER_PREDICATES = "no_registered_falsifier_predicates"

# limitation kinds: each names where in the evidence record it was copied from
LIMIT_HYPOTHESIS_UNAVAILABLE = "hypothesis_unavailable"
LIMIT_DECISION_SELECTION_REPORTS = "decision_selection_reports"

#: (hypothesis, evidence section status, result status, reason), fixed
_LAYOUT = (
    (rp.GENUINE_DETERIORATION, rp.ROUTED, NOT_ASSESSED,
     NO_REGISTERED_FALSIFIER_PREDICATES),
    (rp.INSUFFICIENT_RECENT_OPPORTUNITIES, rp.ROUTED, NOT_ASSESSED,
     NO_REGISTERED_FALSIFIER_PREDICATES),
    (rp.DATA_FAILURE, rp.ROUTED, NOT_ASSESSED,
     NO_REGISTERED_FALSIFIER_PREDICATES),
    (rp.TEMPORARY_REGIME_ABSENCE, rp.UNAVAILABLE, UNAVAILABLE,
     rp.NO_TRUTHFUL_WORLDMODEL_SOURCE),
)

SEMANTICS = ("structural result for one strategy-decay research-evidence "
             "record: INCONCLUSIVE because no falsifier predicate is "
             "registered; no hypothesis is assessed and none is named "
             "true or false; evidence values are not read; no score, "
             "probability, ranking, priority, salience, usefulness, "
             "threshold or asset attribution; not an Attention trigger; no "
             "network, LLM, Risk, Execution or trading authority")

_RECORD_KEYS = ("schema", "result_id", "result_kind", "resolver_id",
                "source_evidence", "scope", "status", "status_reason",
                "hypotheses", "unavailable_hypotheses", "limitations",
                "semantics")
_SOURCE_EVIDENCE_KEYS = ("schema", "evidence_id", "evidence_kind",
                         "collector_id", "plan_id", "question_id",
                         "canonical_sha256")
_HYPOTHESIS_KEYS = ("hypothesis", "evidence_status", "status", "reason")
_UNAVAILABLE_KEYS = ("hypothesis", "reason")
_LIMITATION_KEYS = {
    LIMIT_HYPOTHESIS_UNAVAILABLE: ("kind", "hypothesis", "unavailable_reason",
                                   "unavailable_detail"),
    LIMIT_DECISION_SELECTION_REPORTS: ("kind", "hypothesis", "role",
                                       "reported"),
}


class ResearchResultError(ValueError):
    """Evidence cannot be closed, or a stored result failed verification."""


def _fail(code):
    raise ResearchResultError(code)


def canonical(payload) -> str:
    return re_.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _identity(rec: dict) -> dict:
    se = rec["source_evidence"]
    return {"schema": rec["schema"], "result_kind": rec["result_kind"],
            "resolver_id": rec["resolver_id"],
            "evidence_id": se["evidence_id"],
            "evidence_canonical_sha256": se["canonical_sha256"]}


def result_id(rec: dict) -> str:
    return _sha(canonical(_identity(rec)))


# ── build: structure only, from a verified evidence record ───────────────
def _limitations(evidence: dict) -> list:
    out = []
    for s in evidence["hypotheses"]:
        h = s["hypothesis"]
        if s["status"] == rp.UNAVAILABLE:
            out.append({"kind": LIMIT_HYPOTHESIS_UNAVAILABLE, "hypothesis": h,
                        "unavailable_reason": s["unavailable_reason"],
                        "unavailable_detail": s["unavailable_detail"]})
        for it in s["items"]:
            if it["source"]["store"] == rp._DECISIONS["store"] \
                    and it["values"]["reported"]:
                out.append({"kind": LIMIT_DECISION_SELECTION_REPORTS,
                            "hypothesis": h, "role": it["role"],
                            "reported": it["values"]["reported"]})
    return out


def build(evidence: dict) -> dict:
    """The canonical research-result.v1 for one evidence record that
    `research_evidence.load` has already fully verified. Raises
    ResearchResultError when its sections do not have the fixed layout."""
    if (evidence.get("schema") != re_.SCHEMA
            or evidence.get("evidence_kind") != re_.EVIDENCE_KIND
            or evidence.get("collector_id") != re_.COLLECTOR_ID):
        _fail("evidence_contract")
    sections = evidence["hypotheses"]
    if [s["hypothesis"] for s in sections] != [x[0] for x in _LAYOUT]:
        _fail("evidence_hypotheses")
    hyps, unavailable = [], []
    for s, (h, ev_status, status, reason) in zip(sections, _LAYOUT):
        if s["status"] != ev_status:
            _fail(f"evidence_section_status:{h}")
        if status == UNAVAILABLE:
            if s["unavailable_reason"] != reason or s["items"]:
                _fail(f"evidence_section_contract:{h}")
            unavailable.append({"hypothesis": h, "reason": reason})
        elif s["unavailable_reason"] is not None:
            _fail(f"evidence_section_contract:{h}")
        hyps.append({"hypothesis": h, "evidence_status": s["status"],
                     "status": status, "reason": reason})
    sp = evidence["source_plan"]
    rec = {"schema": SCHEMA, "result_kind": RESULT_KIND,
           "resolver_id": RESOLVER_ID,
           "source_evidence": {
               "schema": evidence["schema"],
               "evidence_id": evidence["evidence_id"],
               "evidence_kind": evidence["evidence_kind"],
               "collector_id": evidence["collector_id"],
               "plan_id": sp["plan_id"], "question_id": sp["question_id"],
               "canonical_sha256": _sha(canonical(evidence))},
           "scope": dict(evidence["scope"]),
           "status": INCONCLUSIVE,
           "status_reason": NO_REGISTERED_FALSIFIER_PREDICATES,
           "hypotheses": hyps, "unavailable_hypotheses": unavailable,
           "limitations": _limitations(evidence), "semantics": SEMANTICS}
    rec["result_id"] = result_id(rec)
    return json.loads(canonical(rec))


# ── contract verification ────────────────────────────────────────────────
def _keys_exact(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


def _list_of(v, code):
    if not isinstance(v, list):
        _fail(code)
    return v


def from_json(text: str, evidence: dict) -> dict:
    """Parse and verify a stored result against the verified evidence
    record it binds: exact keys at every level, only the statuses this
    module defines, canonical form, result_id, the evidence id and hash
    binding, and byte-equality with a fresh `build`. Raises
    ResearchResultError."""
    try:
        rec = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ResearchResultError("undecodable") from e
    _keys_exact(rec, _RECORD_KEYS, "keys")
    _keys_exact(rec["source_evidence"], _SOURCE_EVIDENCE_KEYS,
                "source_evidence_keys")
    _keys_exact(rec["scope"], evidence["scope"], "scope_keys")
    for h in _list_of(rec["hypotheses"], "hypotheses"):
        _keys_exact(h, _HYPOTHESIS_KEYS, "hypothesis_keys")
        if h["status"] not in HYPOTHESIS_STATUSES:
            _fail("hypothesis_status")
    for u in _list_of(rec["unavailable_hypotheses"], "unavailable_hypotheses"):
        _keys_exact(u, _UNAVAILABLE_KEYS, "unavailable_keys")
    for lim in _list_of(rec["limitations"], "limitations"):
        if not isinstance(lim, dict) or lim.get("kind") not in _LIMITATION_KEYS:
            _fail("limitation_kind")
        _keys_exact(lim, _LIMITATION_KEYS[lim["kind"]], "limitation_keys")
    if rec["status"] not in STATUSES:
        _fail("status")
    if (rec["schema"] != SCHEMA or rec["result_kind"] != RESULT_KIND
            or rec["resolver_id"] != RESOLVER_ID
            or rec["semantics"] != SEMANTICS):
        _fail("contract")
    if canonical(rec) != text:
        _fail("not_canonical")
    if rec["result_id"] != result_id(rec):
        _fail("result_id")
    se = rec["source_evidence"]
    if (se["evidence_id"] != evidence["evidence_id"]
            or se["canonical_sha256"] != _sha(canonical(evidence))):
        _fail("source_evidence_mismatch")
    if canonical(build(evidence)) != text:
        _fail("rebuild_mismatch")
    return rec


# ── durable record ───────────────────────────────────────────────────────
def row_for(rec: dict) -> dict:
    se = rec["source_evidence"]
    text = canonical(rec)
    return {"result_id": rec["result_id"], "schema": rec["schema"],
            "result_kind": rec["result_kind"],
            "resolver_id": rec["resolver_id"],
            "evidence_id": se["evidence_id"],
            "evidence_sha256": se["canonical_sha256"],
            "plan_id": se["plan_id"], "scope_kind": rec["scope"]["kind"],
            "scope_id": rec["scope"]["spec_id"], "status": rec["status"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def _verified_evidence(journal, plan_id, evidence_id) -> dict:
    """The evidence record, verified by the existing evidence contract."""
    try:
        recs = re_.load(journal, plan_id=plan_id)
    except (re_.ResearchEvidenceError, rp.ResearchPlanError) as e:
        raise ResearchResultError(f"source_evidence:{e}") from e
    match = [r for r in recs if r["evidence_id"] == evidence_id]
    if len(match) != 1:
        _fail("source_evidence_missing")
    return match[0]


def record_from_journal(journal, now_ms: int) -> dict:
    """Close every stored evidence record that fully verifies. Returns
    {"inserted"|"duplicate"|"conflict": [result_id], "refusals":
    [(evidence_id, reason)]}. Evidence that fails verification is refused,
    never closed. Not wired into any live path."""
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for erow in journal.research_evidence():
        try:
            ev = _verified_evidence(journal, erow.get("plan_id"),
                                    erow.get("evidence_id"))
            rec = build(ev)
        except ResearchResultError as e:
            res["refusals"].append((erow.get("evidence_id"), str(e)))
            continue
        status = journal.record_research_result(row_for(rec),
                                                recorded_at_ms=now_ms)
        res[status].append(rec["result_id"])
    return res


def load(journal, evidence_id: str | None = None) -> list:
    """Stored results, each re-verified: its source evidence re-verified by
    the evidence contract, the exact evidence id/hash binding, canonical
    form and contract, a byte-equal rebuild, and the row projection.
    Raises ResearchResultError on the first result that fails."""
    out = []
    for r in journal.research_results(evidence_id=evidence_id):
        ev = _verified_evidence(journal, r["plan_id"], r["evidence_id"])
        rec = from_json(r["canonical_json"], ev)
        if canonical(row_for(rec)) != canonical({k: r[k]
                                                 for k in row_for(rec)}):
            _fail("row_projection")
        out.append(rec)
    return out
