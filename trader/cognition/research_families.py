"""Research family dispatch — research-family-dispatch.v1.

A frozen, deterministic router from one persisted strategy-health source
observation (a ``strategy_health_observed`` row of
strategy-health-observation.v1, identified by its journal event id) to the
TESTED research family that accepts it as the source of a question.

Only two families are routable, in this fixed registry order:

- ``strategy_decay``             — research_question.derive
- ``strategy_health_unreadable`` — research_unreadable_question.derive

For one health history (a list of raw ``strategy_health_rows``) each
family's own ``derive`` is asked exactly once, unchanged. A family ACCEPTS
a source event when its derivation returns a question whose
``source.event_id`` is that event id. Outcomes:

- ``ROUTED``     — exactly one family accepts; it is ``family``;
- ``NOT_ROUTED`` — no family accepts;
- ``AMBIGUOUS``  — more than one accepts. Fail closed: no family is chosen.

For every family the record carries whether it accepted, the accepted
question's id, and the family's own refusals that name this source,
verbatim (the four research_question.Refusal fields, in the order the
family returned them). A refusal names the source when it refuses the whole
history (``spec_id`` None), or names the source's own spec with this event
id or with no event id (every candidate of the spec). Nothing else is
inferred: a family that neither accepts nor refuses the source (for
example the decay family on an ``idle`` observation) reports no refusal,
and no reason is invented for it.

There is no score, rank, salience, priority, threshold, preference,
diagnosis or verdict mapping. The families' verdict sets are disjoint today
(decay questions need ``decayed``; unreadable questions need
``compile_failed``/``evaluation_failed``), so AMBIGUOUS is a guard, not an
expected outcome. Pure: no I/O, no network, LLM, Attention, Kernel, Risk,
Execution or trading authority.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from trader.cognition import research_question as rq
from trader.cognition import research_unreadable_question as uq

SCHEMA = "research-family-dispatch.v1"
DISPATCH_ID = "research-family-dispatch.v1"

ROUTED, NOT_ROUTED, AMBIGUOUS = "ROUTED", "NOT_ROUTED", "AMBIGUOUS"
OUTCOMES = (ROUTED, NOT_ROUTED, AMBIGUOUS)

STRATEGY_DECAY = rq.QUESTION_KIND                  # "strategy_decay"
STRATEGY_HEALTH_UNREADABLE = uq.QUESTION_KIND      # "strategy_health_unreadable"

REFUSAL_KEYS = ("spec_id", "reason", "event_id", "sweep_id")

SEMANTICS = ("deterministic routing of one strategy-health-observation.v1 "
             "source event to the one TESTED research family whose unchanged "
             "derivation accepts it as a question source; refusals copied "
             "verbatim; AMBIGUOUS fails closed; no score, rank, salience, "
             "priority, threshold, preference, diagnosis or verdict mapping; "
             "no network, LLM, Attention, Kernel, Risk, Execution or trading "
             "authority")


@dataclass(frozen=True)
class Family:
    name: str
    question_schema: str
    derive: object            # rows -> Derivation(questions, refusals)


#: frozen registry, in fixed order; nothing else is routable
FAMILIES = (Family(STRATEGY_DECAY, rq.SCHEMA, rq.derive),
            Family(STRATEGY_HEALTH_UNREADABLE, uq.SCHEMA, uq.derive))
FAMILY_NAMES = tuple(f.name for f in FAMILIES)


class DispatchError(ValueError):
    """A dispatch request or stored dispatch record is malformed."""


def canonical(payload) -> str:
    return rq.canonical(payload)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _refusal(r) -> dict:
    if not isinstance(r, rq.Refusal):
        raise DispatchError("unrecognized_refusal")
    return {k: getattr(r, k) for k in REFUSAL_KEYS}


def names_source(r: dict, event_id: int, subject) -> bool:
    """Whether one family refusal names this source event: the whole
    history, or the source's own spec at this event or at every event."""
    if r["spec_id"] is None:
        return True
    return (isinstance(subject, str) and r["spec_id"] == subject
            and r["event_id"] in (None, event_id))


@dataclass(frozen=True)
class FamilyReading:
    """One family's derivation over one history, indexed by source event."""
    name: str
    question_schema: str
    by_event: dict            # source event id -> canonical question dict
    refusals: tuple           # verbatim refusal dicts, family order


def read_families(rows, families=FAMILIES) -> tuple:
    """Ask every family's derive exactly once over the same rows."""
    rows = list(rows)
    out = []
    for f in families:
        d = f.derive(rows)
        by_event = {}
        for q in d.questions:
            eid = q["source"]["event_id"]
            if eid in by_event:
                raise DispatchError(f"family_duplicate_source:{f.name}")
            by_event[eid] = q
        out.append(FamilyReading(f.name, f.question_schema, by_event,
                                 tuple(_refusal(r) for r in d.refusals)))
    return tuple(out)


def dispatch_one(readings, event_id: int, subject) -> dict:
    """The research-family-dispatch.v1 record of one source event."""
    if not _int(event_id) or event_id <= 0:
        raise DispatchError("invalid_source_event_id")
    fams, accepted = [], []
    for r in readings:
        q = r.by_event.get(event_id)
        if q is not None:
            accepted.append(r.name)
        fams.append({
            "family": r.name,
            "question_schema": r.question_schema,
            "accepted": q is not None,
            "question_id": None if q is None else q["question_id"],
            "refusals": [x for x in r.refusals
                         if names_source(x, event_id, subject)]})
    outcome = (ROUTED if len(accepted) == 1 else
               NOT_ROUTED if not accepted else AMBIGUOUS)
    rec = {"schema": SCHEMA, "dispatch_id": DISPATCH_ID,
           "source_event_id": event_id,
           "source_subject": subject if isinstance(subject, str) else None,
           "outcome": outcome,
           "family": accepted[0] if outcome == ROUTED else None,
           "families": fams, "semantics": SEMANTICS}
    return json.loads(canonical(rec))


def dispatch(rows, sources, families=FAMILIES) -> list:
    """Dispatch records for ``sources`` — [(event_id, subject)] in the given
    (event id) order — against one health history. Pure."""
    readings = read_families(rows, families)
    return [dispatch_one(readings, eid, subj) for eid, subj in sources]


# ── contract verification ────────────────────────────────────────────────
_KEYS = ("schema", "dispatch_id", "source_event_id", "source_subject",
         "outcome", "family", "families", "semantics")
_FAMILY_KEYS = ("family", "question_schema", "accepted", "question_id",
                "refusals")


def check(rec) -> None:
    """Exact-shape verification of one stored dispatch record."""
    if not isinstance(rec, dict) or set(rec) != set(_KEYS):
        raise DispatchError("keys")
    if (rec["schema"] != SCHEMA or rec["dispatch_id"] != DISPATCH_ID
            or rec["semantics"] != SEMANTICS
            or rec["outcome"] not in OUTCOMES
            or not _int(rec["source_event_id"])
            or rec["source_event_id"] <= 0
            or not (rec["source_subject"] is None
                    or isinstance(rec["source_subject"], str))):
        raise DispatchError("contract")
    fams = rec["families"]
    if (not isinstance(fams, list) or len(fams) != len(FAMILIES)
            or [f.get("family") if isinstance(f, dict) else None
                for f in fams] != list(FAMILY_NAMES)):
        raise DispatchError("families_layout")
    accepted = []
    for f, reg in zip(fams, FAMILIES):
        if set(f) != set(_FAMILY_KEYS) or f["question_schema"] != \
                reg.question_schema or type(f["accepted"]) is not bool:
            raise DispatchError("family_shape")
        if f["accepted"] != (isinstance(f["question_id"], str)
                             and len(f["question_id"]) == 64):
            raise DispatchError("family_question_id")
        if f["accepted"] is False and f["question_id"] is not None:
            raise DispatchError("family_question_id")
        if not isinstance(f["refusals"], list):
            raise DispatchError("family_refusals")
        for x in f["refusals"]:
            if (not isinstance(x, dict) or set(x) != set(REFUSAL_KEYS)
                    or not isinstance(x["reason"], str)):
                raise DispatchError("family_refusals")
        if f["accepted"]:
            accepted.append(f["family"])
    want = (ROUTED if len(accepted) == 1 else
            NOT_ROUTED if not accepted else AMBIGUOUS)
    if rec["outcome"] != want or rec["family"] != (
            accepted[0] if want == ROUTED else None):
        raise DispatchError("outcome")
