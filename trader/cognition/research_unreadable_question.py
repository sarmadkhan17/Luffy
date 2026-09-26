"""Unreadable strategy-health research questions —
strategy-health-unreadable-question.v1.

A sibling of the strategy-decay research-question.v1 family for the SDD
12.2 "capability/data gaps" question source. One deterministic question is
registered per strategy-health-observation.v1 spec record whose verdict is

- ``compile_failed``    — the spec did not compile; or
- ``evaluation_failed`` — the health branch could not be read truthfully.

The question carries the record's own stored reasons verbatim — verdict,
verdict_reason, coverage_complete, coverage_faults and, where the writer
stored an error, its stage and error_class — and asks only whether they
describe a capability or data-evaluation gap. It diagnoses nothing beyond
them: the free-text error message, per-symbol coverage detail, lifecycle
branch and metrics are not copied (the record stays bound by its raw
hash). No idle/still_working/decayed observation produces a question.

Grouping: none is invented. Each qualifying observation is its own
question, identified by its source event and raw-record hash; there is no
episode, run, multi-sweep, novelty or no-repeat rule, and repeated
unreadable sweeps of one spec are separate questions.

Fail-closed reading reuses the strategy-decay question module's existing
history rules unchanged: an undecodable sweep record or an unattributable
spec record refuses the whole history; a malformed spec record, two
observations of one spec in one sweep, a shared sweep start or sweep order
disagreeing with write order refuses every candidate of that spec; a
candidate whose sweep record is missing, disagrees with it, contradicts
itself for the spec, or whose outcome lists contradict the verdict, is
refused. Stored reasons outside the health writer's vocabulary are refused.

Isolation: questions live in their own ``research_unreadable_questions``
table, so the research-question.v1 table, its readers, research-plan.v1,
evidence, result, run, bank and recall never see them.

Registration: the journal writes a research-registration.v1 receipt
(record_type = SCHEMA, the question's ID, canonical SHA-256 and insert-time
recorded_at_ms) in the same transaction as a first insert only; a
duplicate, a conflict or a receipt already present for an absent row
never registers. Rows filed before receipts existed are never backfilled
and have no verifiable registration provenance. The receipt is
provenance/order metadata only; this module neither writes nor reads it. research-plan.v1 refuses this JSON if handed it directly.
`derive` is pure; `record_from_journal` is an offline writer that nothing
calls from the live loop. No network, LLM, Attention, Kernel, Risk,
Execution or trading authority.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from trader.cognition import research_question as rq
from trader.strategy import health_observation as ho

SCHEMA = "strategy-health-unreadable-question.v1"
QUESTION_KIND = "strategy_health_unreadable"
TEMPLATE_ID = "strategy-health-unreadable-question-text.v1"
AUTHORITY = "context_only"
SOURCE_SCHEMA = ho.SCHEMA
SOURCE_KIND = ho.KIND_SPEC
SWEEP_KIND = ho.KIND_SWEEP

UNREADABLE = rq.UNREADABLE                    # compile_failed, evaluation_failed

SEMANTICS = ("context_only strategy-scoped research question registered from "
             "one unreadable (compile_failed or evaluation_failed) "
             "strategy-health-observation.v1 record; carries that record's "
             "stored verdict and reason codes verbatim and asks only whether "
             "they describe a capability or data-evaluation gap; no diagnosis "
             "beyond them; no asset or symbol attribution, falsifier "
             "predicate, threshold, source or vendor choice, regime, "
             "priority, salience, urgency, usefulness or score; not consumed "
             "by research-plan.v1; not an Attention trigger; no network, LLM, "
             "Risk, Execution or trading authority")

#: evaluation_failed reasons the health writer can store with coverage
_COVERAGE_REASONS = (ho.COVERAGE_UNAVAILABLE, ho.NO_SYMBOL_SCORED,
                     *ho.COVERAGE_FAULTS)

# ── refusal reasons (beyond the reused research_question ones) ───────────
SOURCE_REASONS_MALFORMED = "source_reasons_malformed"

_RECORD_KEYS = ("schema", "question_id", "question_kind", "authority",
                "scope", "question", "source", "recorded_reasons",
                "semantics")
_SOURCE_KEYS = ("schema", "kind", "event_id", "event_ts", "record_sha256",
                "sweep_id", "sweep_started_at", "spec_id", "spec_name",
                "health_fingerprint", "spec_content_sha256",
                "sweep_record")
_SWEEP_REF_KEYS = ("kind", "event_id", "record_sha256")
_REASON_KEYS = ("verdict", "verdict_reason", "coverage_complete",
                "coverage_faults", "error_stage", "error_class")


class UnreadableQuestionError(ValueError):
    """A stored strategy-health-unreadable-question.v1 failed verification."""


def canonical(payload) -> str:
    return rq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def question_id(identity: dict) -> str:
    return _sha(canonical(identity))


def _identity(rec: dict) -> dict:
    src = rec["source"]
    return {"schema": rec["schema"], "question_kind": rec["question_kind"],
            "spec_id": rec["scope"]["spec_id"],
            "source_schema": src["schema"], "source_kind": src["kind"],
            "source_event_id": src["event_id"],
            "source_record_sha256": src["record_sha256"]}


def _fmt(v) -> str:
    if v is None:
        return "not_recorded"
    if isinstance(v, list):
        return ",".join(v) if v else "none"
    return str(v)


def question_text(spec_id, sweep_id, reasons) -> str:
    return (f"Does the unreadable strategy-health observation of strategy "
            f"{spec_id} in decay sweep {sweep_id} (verdict "
            f"{reasons['verdict']}; verdict_reason "
            f"{_fmt(reasons['verdict_reason'])}; coverage_faults "
            f"{_fmt(reasons['coverage_faults'])}) represent a capability or "
            f"data-evaluation gap described by those recorded reasons?")


@dataclass
class Derivation:
    questions: list = field(default_factory=list)   # canonical dicts
    refusals: list = field(default_factory=list)    # rq.Refusal


# ── stored reasons, verbatim ─────────────────────────────────────────────
def _str(v) -> bool:
    return isinstance(v, str) and bool(v)


def _reasons(rec: dict):
    """The record's stored reasons, copied verbatim, or None when they are
    outside the health writer's vocabulary for the verdict."""
    verdict, reason = rec.get("verdict"), rec.get("verdict_reason")
    err, cov = rec.get("error"), rec.get("coverage")
    if verdict not in UNREADABLE:
        return None
    raised = verdict == ho.COMPILE_FAILED or reason == ho.EVALUATION_EXCEPTION
    if verdict == ho.COMPILE_FAILED and reason is not None:
        return None
    if raised:
        # the writer stores an error and no coverage for these
        if (not isinstance(err, dict) or not _str(err.get("stage"))
                or not _str(err.get("error_class")) or cov is not None):
            return None
        return {"verdict": verdict, "verdict_reason": reason,
                "coverage_complete": None, "coverage_faults": None,
                "error_stage": err["stage"], "error_class": err["error_class"]}
    if (reason not in _COVERAGE_REASONS or err is not None
            or not isinstance(cov, dict)
            or type(cov.get("coverage_complete")) is not bool
            or not isinstance(cov.get("coverage_faults"), list)
            or any(f not in ho.COVERAGE_FAULTS for f in cov["coverage_faults"])):
        return None
    return {"verdict": verdict, "verdict_reason": reason,
            "coverage_complete": cov["coverage_complete"],
            "coverage_faults": list(cov["coverage_faults"]),
            "error_stage": None, "error_class": None}


def build(obs: dict, rec: dict, reasons: dict, sweep_row: dict) -> dict:
    """The canonical strategy-health-unreadable-question.v1. Pure."""
    spec_id = obs["spec_id"]
    q = {
        "schema": SCHEMA,
        "question_kind": QUESTION_KIND,
        "authority": AUTHORITY,
        "scope": {"kind": "strategy", "spec_id": spec_id},
        "question": {"template_id": TEMPLATE_ID,
                     "text": question_text(spec_id, obs["sweep_id"], reasons)},
        "source": {
            "schema": SOURCE_SCHEMA, "kind": SOURCE_KIND,
            "event_id": obs["event_id"], "event_ts": obs["event_ts"],
            "record_sha256": obs["record_sha256"],
            "sweep_id": obs["sweep_id"],
            "sweep_started_at": obs["sweep_started_at"],
            "spec_id": spec_id, "spec_name": rec.get("spec_name"),
            "health_fingerprint": rec.get("health_fingerprint"),
            "spec_content_sha256": rec.get("spec_content_sha256"),
            "sweep_record": {"kind": SWEEP_KIND,
                             "event_id": sweep_row["id"],
                             "record_sha256": _sha(sweep_row["detail"])}},
        "recorded_reasons": reasons,
        "semantics": SEMANTICS,
    }
    q["question_id"] = question_id(_identity(q))
    return json.loads(canonical(q))


# ── derivation ───────────────────────────────────────────────────────────
def derive(rows) -> Derivation:
    """Unreadable-health questions implied by raw health rows. Pure; the
    result is independent of row order. See the module docstring for the
    fail-closed rules (those of research_question.derive, reused)."""
    out = Derivation()
    rows = [r for r in rows if isinstance(r, dict)
            and r.get("kind") in (ho.KIND_SPEC, ho.KIND_SWEEP)]
    sweeps, sweep_rows, spec_rows = {}, {}, []
    global_bad, spec_bad = [], {}
    for row in rows:
        rec, bad = ho._decode(row)
        if row["kind"] == ho.KIND_SWEEP:
            sw = rq._sweep(rec) if rec else None
            if sw is None or sw["sweep_id"] in sweeps:
                global_bad.append((row.get("id"), rq.MALFORMED_SWEEP_RECORD))
                continue
            if not rq._int(row.get("id")):
                global_bad.append((row.get("id"), rq.MALFORMED_SWEEP_RECORD))
                continue
            sweeps[sw["sweep_id"]] = sw
            sweep_rows[sw["sweep_id"]] = row
            continue
        subject = row.get("subject")
        if not rq._nonempty(subject):
            global_bad.append((row.get("id"), rq.UNATTRIBUTABLE_SPEC_RECORD))
            continue
        if bad:
            spec_bad.setdefault(subject, rq.MALFORMED_SPEC_RECORD)
            continue
        spec_rows.append((row, rec))
    if global_bad:
        eid, reason = min(global_bad, key=lambda x: (str(x[0]), x[1]))
        out.refusals.append(rq.Refusal(None, reason,
                                       eid if rq._int(eid) else None))
        return out

    by_spec: dict[str, list] = {}
    for row, rec in spec_rows:
        o, bad = rq._observation(row, rec)
        if bad:
            spec_bad.setdefault(rec["spec_id"], bad)
            continue
        by_spec.setdefault(o["spec_id"], []).append((o, rec))

    starts = {}
    for sw in sweeps.values():
        starts.setdefault(sw["started_ms"], set()).add(sw["sweep_id"])
    shared_start = {sid for ids in starts.values() if len(ids) > 1 for sid in ids}

    for spec_id in sorted(set(by_spec) | set(spec_bad)):
        if spec_id in spec_bad:
            out.refusals.append(rq.Refusal(spec_id, spec_bad[spec_id]))
            continue
        pairs = sorted(by_spec[spec_id], key=lambda p: p[0]["event_id"])
        refusal = rq._chronology(spec_id, [o for o, _ in pairs], shared_start)
        if refusal:
            out.refusals.append(refusal)
            continue
        for o, rec in pairs:
            if o["verdict"] not in UNREADABLE:
                continue
            why = _source_gap(spec_id, o, sweeps)
            if why is None and _reasons(rec) is None:
                why = SOURCE_REASONS_MALFORMED
            if why:
                out.refusals.append(rq.Refusal(spec_id, why, o["event_id"],
                                               o["sweep_id"]))
                continue
            out.questions.append(build(o, rec, _reasons(rec),
                                       sweep_rows[o["sweep_id"]]))
    out.questions.sort(key=lambda q: (q["scope"]["spec_id"],
                                      q["source"]["event_id"]))
    return out


def _source_gap(spec_id, o, sweeps):
    """The research_question gap reason of the candidate's own sweep, or
    None when its sweep record exists and agrees with it."""
    sw = sweeps.get(o["sweep_id"])
    if sw is None:
        return rq.SWEEP_RECORD_MISSING
    if (sw["started_ms"] != o["started_ms"]
            or spec_id not in sw["observation_recorded_spec_ids"]):
        return rq.SWEEP_RECORD_MISMATCH
    if rq._sweep_conflict(sw, spec_id):
        return rq.SWEEP_RECORD_INCONSISTENT
    if not rq._outcome_ok(sw, o):
        return rq.VERDICT_OUTCOME_MISMATCH
    return None


# ── contract verification ────────────────────────────────────────────────
def _fail(code):
    raise UnreadableQuestionError(code)


def _opt_str(v) -> bool:
    return v is None or isinstance(v, str)


def _check_source(src):
    if not isinstance(src, dict) or set(src) != set(_SOURCE_KEYS):
        _fail("source_keys")
    ref = src["sweep_record"]
    if not isinstance(ref, dict) or set(ref) != set(_SWEEP_REF_KEYS):
        _fail("sweep_record_keys")
    if not (src["schema"] == SOURCE_SCHEMA and src["kind"] == SOURCE_KIND
            and rq._int(src["event_id"]) and src["event_id"] > 0
            and isinstance(src["event_ts"], str)
            and rq._hex64(src["record_sha256"])
            and rq._nonempty(src["sweep_id"])
            and rq._ms(src["sweep_started_at"]) is not None
            and rq._nonempty(src["spec_id"])
            and all(_opt_str(src[k]) for k in (
                "spec_name", "health_fingerprint", "spec_content_sha256"))
            and ref["kind"] == SWEEP_KIND
            and rq._int(ref["event_id"]) and ref["event_id"] > 0
            and rq._hex64(ref["record_sha256"])):
        _fail("source")


def _check_reasons(r):
    if not isinstance(r, dict) or set(r) != set(_REASON_KEYS):
        _fail("recorded_reasons_keys")
    # the same vocabulary check derivation applies, on the stored shape
    if r["verdict"] == ho.COMPILE_FAILED or \
            r["verdict_reason"] == ho.EVALUATION_EXCEPTION:
        probe = {"verdict": r["verdict"], "verdict_reason": r["verdict_reason"],
                 "error": {"stage": r["error_stage"],
                           "error_class": r["error_class"]},
                 "coverage": None}
    else:
        probe = {"verdict": r["verdict"], "verdict_reason": r["verdict_reason"],
                 "error": (None if r["error_stage"] is None
                           and r["error_class"] is None else {}),
                 "coverage": {"coverage_complete": r["coverage_complete"],
                              "coverage_faults": r["coverage_faults"]}}
    if _reasons(probe) != r:
        _fail("recorded_reasons")


def from_json(text: str) -> dict:
    """Parse and verify a stored question on its own: exact keys and types
    at every level, canonical form, question_id and text recomputed. It
    does NOT prove the bound evidence exists; `verify_evidence` does.
    Raises UnreadableQuestionError."""
    try:
        q = json.loads(text)
    except (TypeError, ValueError) as e:
        raise UnreadableQuestionError("undecodable") from e
    if not isinstance(q, dict) or set(q) != set(_RECORD_KEYS):
        _fail("keys")
    if (q["schema"] != SCHEMA or q["question_kind"] != QUESTION_KIND
            or q["authority"] != AUTHORITY or q["semantics"] != SEMANTICS):
        _fail("contract")
    _check_source(q["source"])
    _check_reasons(q["recorded_reasons"])
    if q["scope"] != {"kind": "strategy", "spec_id": q["source"]["spec_id"]}:
        _fail("scope")
    if canonical(q) != text:
        _fail("not_canonical")
    if q["question_id"] != question_id(_identity(q)):
        _fail("question_id")
    txt = question_text(q["source"]["spec_id"], q["source"]["sweep_id"],
                        q["recorded_reasons"])
    if q["question"] != {"template_id": TEMPLATE_ID, "text": txt}:
        _fail("question_text")
    return q


def verify_evidence(q: dict, rows) -> None:
    """Check a from_json-verified question against the journal health rows:
    `derive` over the history bounded through the source sweep
    (research_question._bounded, reused) must produce exactly this
    question, so a missing, altered or contradicted source observation or
    sweep record fails closed while later unrelated history is not
    consulted. Raises UnreadableQuestionError."""
    spec_id = q["scope"]["spec_id"]
    try:
        bounded = rq._bounded(spec_id, q["source"], list(rows))
    except rq.ResearchQuestionError as e:
        raise UnreadableQuestionError(f"evidence_missing:{e}") from e
    d = derive(bounded)
    if canonical(q) not in {canonical(x) for x in d.questions}:
        why = next((r.reason for r in d.refusals
                    if r.spec_id in (None, spec_id)
                    and r.event_id in (None, q["source"]["event_id"])),
                   "not_derived")
        _fail(f"derivation_mismatch:{why}")


# ── durable record ───────────────────────────────────────────────────────
def row_for(q: dict) -> dict:
    text = canonical(q)
    return {"question_id": q["question_id"], "schema": q["schema"],
            "question_kind": q["question_kind"],
            "scope_kind": q["scope"]["kind"], "scope_id": q["scope"]["spec_id"],
            "source_kind": q["source"]["kind"],
            "source_event_id": q["source"]["event_id"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def record_from_journal(journal, now_ms: int) -> dict:
    """Derive from the journal's health rows and persist each question in
    its own table. Returns {"inserted"|"duplicate"|"conflict":
    [question_id], "refusals": [Refusal]}. Offline; not wired into any
    live path; enqueues and invokes nothing."""
    d = derive(journal.strategy_health_rows())
    res = {"inserted": [], "duplicate": [], "conflict": [],
           "refusals": list(d.refusals)}
    for q in d.questions:
        status = journal.record_research_unreadable_question(
            row_for(q), recorded_at_ms=now_ms)
        res[status].append(q["question_id"])
    return res


def load(journal, spec_id: str | None = None) -> list:
    """Stored questions, each verified by from_json, its row projection and
    verify_evidence against the spec's current health rows. Raises
    UnreadableQuestionError on the first question that fails."""
    out = []
    for r in journal.research_unreadable_questions(scope_id=spec_id):
        q = from_json(r["canonical_json"])
        if row_for(q) != {k: r[k] for k in row_for(q)}:
            _fail("row_projection")
        verify_evidence(q, journal.strategy_health_rows_for_spec(
            q["scope"]["spec_id"]))
        out.append(q)
    return out
