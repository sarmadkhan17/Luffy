"""Strategy-decay research evidence — research-evidence.v1.

One deterministic, immutable evidence record per persisted, fully verified
strategy_decay research-plan.v1. Collection RESOLVES every ROUTED plan
locator against the existing internal store it names and FREEZES the exact
values of the routed fields, with the source identity and SHA-256 of the
raw stored content. It does not weigh, classify or compare the values: no
support/contradiction, no conclusion on any hypothesis, no score,
probability, ranking, priority, salience, usefulness, threshold or asset
attribution. Frozen values are source content verbatim; any field they
carry (a signal's symbol, a health record's verdict) is the source's, not
a judgement made here.

Every item freezes the exact raw content of its bound source
(``source_content``) and derives everything else from it and the plan:

- ``journal.brain_events`` (strategy-health-observation.v1) — content is
  the stored detail of the row the plan binds by event id. Its SHA-256
  must equal the plan binding; it must decode under the health writer's
  own contract for the plan's kind and subject (spec: the plan's spec id;
  sweep: the locator's sweep id) and match the plan's record variant;
  values are the routed field paths. Identity is event id, kind and
  subject — all bound by the plan, none taken from the source row.
- ``journal.research_questions`` (research-question.v1) — content is the
  stored question JSON; its SHA-256 must equal the plan's question binding
  and it must pass research_question.from_json; identity is its question
  id and source event id; values are the routed change flags.
- ``journal.decisions`` — the plan's declared
  decision-signal-occurrence-selection.v1. Content is the raw routed
  columns of every row the rule selected or reported, ordered by id.
  Values are the rule re-run over that content: selected rows (by exact
  decision instant, then id) with their row hash, routed columns, entry
  indexes and selected signals_json entries, and reports with reasons.
  Decision instants are timezone-aware and compared at full parsed
  precision; the signal-bar cutoff stays an integer millisecond.

``temporary_regime_absence`` (and any other UNAVAILABLE plan section) stays
UNAVAILABLE with its plan reason and no items.

`collect` depends only on the verified plan and the journal rows it reads.
`record_from_journal` is the only writer (insert / duplicate / conflict).
`load` re-verifies each stored record canonically and against its stored
plan row, re-derives every item from its frozen content (so a record
verifies on its own, whether or not its sources still exist), and then
checks every source that still exists: a present source whose content
differs from what was frozen, or a decision newly entering the selection,
fails closed; a deleted source is tolerated and reported absent. Nothing
calls any of this from the live loop; there is no network, LLM,
Attention, Risk, Execution or trading authority here.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.strategy import health_observation as ho
from trader.strategy import signal_occurrence_observation as soo

SCHEMA = "research-evidence.v1"
EVIDENCE_KIND = "strategy_decay"
COLLECTOR_ID = "strategy-decay-evidence-collection.v1"

SEMANTICS = ("frozen evidence for one strategy-decay research plan: the "
             "exact values of every routed field, resolved from existing "
             "internal records with their source identity and content "
             "hash; classifies nothing as support or contradiction and "
             "concludes nothing; no score, probability, ranking, priority, "
             "salience, usefulness, threshold or asset attribution; not an "
             "Attention trigger; no network, LLM, Risk, Execution or "
             "trading authority")

# decisions selection report reasons (in addition to soo's decode reasons)
DECISION_TS_UNREADABLE = "decision_ts_unreadable"
SIGNAL_ENTRY_WITHOUT_OCCURRENCE = "signal_entry_without_occurrence_key"

_RECORD_KEYS = ("schema", "evidence_id", "evidence_kind", "collector_id",
                "source_plan", "scope", "hypotheses", "semantics")
_SOURCE_PLAN_KEYS = ("schema", "plan_id", "plan_kind", "planner_id",
                     "question_id", "canonical_sha256")
_SECTION_KEYS = ("hypothesis", "status", "unavailable_reason",
                 "unavailable_detail", "items")
_ITEM_KEYS = ("hypothesis", "role", "locator", "source", "fields",
              "plan_binding", "source_identity", "source_sha256",
              "source_content", "values", "content_sha256")
_SOURCE_KEYS = ("store", "reader", "record_schema", "record_kind",
                "record_variant")
_DECISION_VALUE_KEYS = ("selected_rows", "reported")
#: routed decision columns frozen per selected row (signals_json is frozen
#: as the selected entries only, per the plan's selection rule)
_DECISION_COLUMNS = tuple(f for f in rp._DECISION_FIELDS
                          if f != "signals_json")


class ResearchEvidenceError(ValueError):
    """A plan cannot be collected, or a stored record failed verification."""


def _fail(code):
    raise ResearchEvidenceError(code)


def canonical(payload) -> str:
    return rq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _identity(rec: dict) -> dict:
    sp = rec["source_plan"]
    return {"schema": rec["schema"], "evidence_kind": rec["evidence_kind"],
            "collector_id": rec["collector_id"], "plan_id": sp["plan_id"],
            "plan_canonical_sha256": sp["canonical_sha256"]}


def evidence_id(rec: dict) -> str:
    return _sha(canonical(_identity(rec)))


def _frozen(values, what):
    """`values` as their canonical JSON form; non-finite numbers (which
    canonical JSON cannot carry) fail closed instead of being altered."""
    try:
        return json.loads(canonical(values))
    except ValueError as e:
        raise ResearchEvidenceError(f"value_not_canonical:{what}") from e


def _get(rec: dict, path: str):
    cur = rec
    for part in path.split("."):
        cur = cur[part]
    return cur


# ── skeleton: everything an item takes from the plan ─────────────────────
def _source_of(e: dict) -> dict:
    return {k: e[k] for k in ("store", "reader", "record_schema",
                              "record_kind", "record_variant")}


def _skeleton(plan: dict) -> list:
    """[(section, [item skeleton])] exactly as the plan routes them."""
    out = []
    for s in plan["hypotheses"]:
        sec = {k: s[k] for k in ("hypothesis", "status", "unavailable_reason",
                                 "unavailable_detail")}
        items = [{"hypothesis": s["hypothesis"], "role": e["role"],
                  "locator": e["locator"], "source": _source_of(e),
                  "fields": e["fields"], "plan_binding": e["binding"]}
                 for e in s["evidence"]]
        if s["status"] != rp.ROUTED and items:
            _fail("plan_unavailable_section_has_evidence")
        out.append((sec, items))
    return out


# ── derivation: identity, hash and values from frozen source content ─────
# Every item freezes the exact raw content of its bound source. `_derive`
# recomputes the item's identity, source hash and values from that content
# and the plan alone, so a stored record verifies independently of whether
# its sources still exist; live sources are an additional check.
def _values(rec: dict, fields, what) -> dict:
    for f in fields:
        if not rp._has(rec, f):
            _fail(f"routed_field_missing:{what}:{f}")
    return _frozen({f: _get(rec, f) for f in fields}, what)


def _health_identity(sk: dict, plan: dict) -> dict:
    """event id, kind and subject bound by the plan, never by the source."""
    b, kind = sk["plan_binding"], sk["source"]["record_kind"]
    if not (isinstance(b, dict) and rq._int(b.get("event_id"))
            and isinstance(b.get("record_sha256"), str)):
        _fail(f"plan_binding_missing:{sk['role']}")
    if kind == ho.KIND_SPEC:
        subject = plan["scope"]["spec_id"]
    elif kind == ho.KIND_SWEEP:
        subject = sk["locator"].get("sweep_id")
    else:
        _fail(f"record_kind_unsupported:{sk['role']}")
    return {"event_id": b["event_id"], "kind": kind, "subject": subject}


def _derive_health(sk: dict, plan: dict, content) -> tuple:
    ident = _health_identity(sk, plan)
    what = f"{sk['role']}:{ident['event_id']}"
    if not isinstance(content, str) \
            or _sha(content) != sk["plan_binding"]["record_sha256"]:
        _fail(f"source_binding_mismatch:{what}")
    # the health writer's own decode: schema, required fields, subject
    rec, bad = ho._decode({"kind": ident["kind"], "subject": ident["subject"],
                           "detail": content})
    if bad:
        _fail(f"source_malformed:{what}:{bad}")
    try:
        if ident["kind"] == ho.KIND_SPEC:
            variant = rp._observation_variant(rec, what)
        else:
            variant = rp.SWEEP_RECORD if rp._sweep_ok(rec) else None
    except rp.ResearchPlanError as e:
        raise ResearchEvidenceError(f"source_contract:{what}") from e
    if variant != sk["source"]["record_variant"]:
        _fail(f"source_contract:{what}")
    return ident, _sha(content), _values(rec, sk["fields"], what)


def _derive_question(sk: dict, plan: dict, content) -> tuple:
    sq, what = plan["source_question"], sk["role"]
    if sk["locator"] != {"question_id": sq["question_id"]}:
        _fail(f"locator_unsupported:{what}")
    if not isinstance(content, str) or _sha(content) != sq["canonical_sha256"]:
        _fail(f"source_binding_mismatch:{what}")
    try:
        q = rq.from_json(content)
    except rq.ResearchQuestionError as e:
        raise ResearchEvidenceError(f"source_contract:{what}") from e
    if q["question_id"] != sq["question_id"]:
        _fail(f"source_identity_mismatch:{what}")
    ident = {"question_id": q["question_id"],
             "source_event_id": q["source"]["event_id"]}
    return ident, _sha(content), _values(q, sk["fields"], what)


def _instant(iso):
    """A timezone-aware datetime at its full parsed precision, or None."""
    if not isinstance(iso, str):
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt if dt.utcoffset() is not None else None


def _raw_decision(r: dict) -> dict:
    return {f: r.get(f) for f in rp._DECISION_FIELDS}


def _row_sha(raw: dict) -> str:
    return _sha(canonical(raw))


def select_decisions(locator: dict, rows) -> dict:
    """decision-signal-occurrence-selection.v1 over raw decision rows.
    {"selected_rows": [...], "reported": [...]}, deterministically ordered:
    selected by exact decision instant then id; reported by id then entry.
    Instants compare at full parsed precision across offsets."""
    if (not isinstance(locator, dict)
            or locator.get("selection") != rp.DECISION_SELECTION
            or locator.get("rule") != rp.DECISION_SELECTION_RULE):
        _fail("decision_selection_unsupported")
    spec_id = locator.get("spec_id")
    ts_hi = _instant(locator.get("decision_ts_at_or_before"))
    close_hi = locator.get("signal_bar_close_ms_at_or_before")
    if ts_hi is None or not rq._int(close_hi) \
            or not (isinstance(spec_id, str) and spec_id):
        _fail("decision_selection_locator_invalid")
    selected, reported = [], []
    for r in rows:
        did = r.get("id")
        if not (isinstance(did, str) and did):
            _fail("decision_row_id_invalid")
        ts = _instant(r.get("ts"))
        if ts is None:
            reported.append({"decision_id": did, "entry_index": None,
                             "reason": DECISION_TS_UNREADABLE})
            continue
        if ts > ts_hi:
            continue
        sigs, why = soo._decode_signals(r.get("signals_json"))
        if sigs is None:
            reported.append({"decision_id": did, "entry_index": None,
                             "reason": why})
            continue
        idx, picked = [], []
        for i, entry in enumerate(sigs):
            key, why = soo._occurrence(entry)
            if key is None:
                reported.append({"decision_id": did, "entry_index": i,
                                 "reason": why if why in (
                                     soo.SIGNAL_ENTRY_NOT_OBJECT,
                                     soo.SIGNAL_ENTRY_UNREADABLE)
                                 else SIGNAL_ENTRY_WITHOUT_OCCURRENCE})
            elif key[0] == spec_id and key[5] <= close_hi:
                idx.append(i)
                picked.append(entry)
        if idx:
            raw = _raw_decision(r)
            selected.append((ts, did, {
                "decision_id": did, "row_sha256": _row_sha(raw),
                "columns": {f: raw[f] for f in _DECISION_COLUMNS},
                "selected_entry_indexes": idx, "selected_signals": picked}))
    selected.sort(key=lambda x: x[:2])
    reported.sort(key=lambda x: (x["decision_id"],
                                 -1 if x["entry_index"] is None
                                 else x["entry_index"]))
    ids = [x[1] for x in selected]
    if len(set(ids)) != len(ids):
        _fail("decision_row_id_duplicate")
    # canonical round trip: frozen values are exactly their JSON form
    return _frozen({"selected_rows": [x[2] for x in selected],
                    "reported": reported}, "decisions")


def _decision_content(values: dict, rows) -> list:
    """The raw routed columns of every row the selection selected or
    reported, ordered by id: enough to recompute every row hash, entry
    index, report and the selection itself."""
    ids = {r["decision_id"] for k in _DECISION_VALUE_KEYS for r in values[k]}
    return _frozen(sorted((_raw_decision(r) for r in rows if r["id"] in ids),
                          key=lambda r: r["id"]), "decisions")


_SCALARS = (str, int, float, type(None))


def _derive_decisions(sk: dict, plan: dict, content) -> tuple:
    if sk["fields"] != list(rp._DECISION_FIELDS):
        _fail("decision_fields_unsupported")
    if sk["plan_binding"] is not None:
        _fail("decision_binding_unsupported")
    if not isinstance(content, list) or not all(
            isinstance(r, dict) and list(r) == sorted(rp._DECISION_FIELDS)
            and all(isinstance(v, _SCALARS) and not isinstance(v, bool)
                    for v in r.values())
            for r in content):
        _fail("decision_content_contract")
    ids = [r["id"] for r in content]
    if ids != sorted(set(ids)):
        _fail("decision_content_order")
    values = select_decisions(sk["locator"], content)
    used = {r["decision_id"] for k in _DECISION_VALUE_KEYS for r in values[k]}
    if used != set(ids):                 # nothing frozen that is not used
        _fail("decision_content_extra")
    return dict(sk["locator"]), _sha(canonical(content)), values


_DERIVE = {rp._HEALTH["store"]: _derive_health,
           rp._QUESTION["store"]: _derive_question,
           rp._DECISIONS["store"]: _derive_decisions}


def _derive(sk: dict, plan: dict, content) -> tuple:
    fn = _DERIVE.get(sk["source"]["store"])
    if fn is None:
        _fail(f"store_unsupported:{sk['source']['store']}")
    return fn(sk, plan, content)


# ── collection: resolve live sources, then freeze ────────────────────────
def _live_content(sk: dict, plan: dict, ctx: dict):
    """The bound source's current raw content, or ResearchEvidenceError."""
    store, role = sk["source"]["store"], sk["role"]
    if store == rp._HEALTH["store"]:
        ident = _health_identity(sk, plan)
        row = ctx["health"].get(ident["event_id"])
        if row is None:
            _fail(f"source_missing:{role}:{ident['event_id']}")
        if (row.get("kind"), row.get("subject")) != (ident["kind"],
                                                      ident["subject"]):
            _fail(f"source_identity_mismatch:{role}:{ident['event_id']}")
        return row.get("detail")
    if store == rp._QUESTION["store"]:
        row = ctx["question"]
        if row is None:
            _fail(f"source_missing:{role}")
        return row.get("canonical_json")
    if store == rp._DECISIONS["store"]:
        values = select_decisions(sk["locator"], ctx["decisions"])
        return _decision_content(values, ctx["decisions"])
    _fail(f"store_unsupported:{store}")


def _item(sk: dict, plan: dict, ctx: dict) -> dict:
    content = _live_content(sk, plan, ctx)
    ident, src_sha, values = _derive(sk, plan, content)
    return {**sk, "source_identity": ident, "source_sha256": src_sha,
            "source_content": content, "values": values,
            "content_sha256": _sha(canonical(values))}


def _ctx(journal, question_id) -> dict:
    return {"health": {r["id"]: r for r in journal.strategy_health_rows()},
            "question": next((r for r in journal.research_questions()
                              if r["question_id"] == question_id), None),
            "decisions": journal.decision_observation_rows()}


def collect(plan_text: str, plan: dict, ctx: dict) -> dict:
    """The canonical research-evidence.v1 for one fully verified plan (its
    stored canonical text and parsed form) and the resolved journal rows
    (`ctx`: health rows by id, the source question row, raw decision
    rows). Raises ResearchEvidenceError when any routed evidence is
    missing, tampered or breaks its contract."""
    if rp.canonical(plan) != plan_text:
        _fail("plan_not_canonical")
    sections = []
    for sec, items in _skeleton(plan):
        sections.append({**sec, "items": [_item(sk, plan, ctx)
                                          for sk in items]})
    sq = plan["source_question"]
    rec = {"schema": SCHEMA, "evidence_kind": EVIDENCE_KIND,
           "collector_id": COLLECTOR_ID,
           "source_plan": {"schema": plan["schema"],
                           "plan_id": plan["plan_id"],
                           "plan_kind": plan["plan_kind"],
                           "planner_id": plan["planner_id"],
                           "question_id": sq["question_id"],
                           "canonical_sha256": _sha(plan_text)},
           "scope": dict(plan["scope"]), "hypotheses": sections,
           "semantics": SEMANTICS}
    rec["evidence_id"] = evidence_id(rec)
    return _frozen(rec, "record")


# ── contract verification ────────────────────────────────────────────────
def _same(a, b) -> bool:
    """Exact equality of JSON values: canonical text, so 1, 1.0 and true
    are different (plain == would treat them as equal)."""
    return canonical(a) == canonical(b)


def _keys_exact(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


def from_json(text: str, plan_text: str) -> dict:
    """Parse and verify a stored record on its own and against the exact
    stored plan text it was collected from: exact keys at every structural
    level, canonical form, evidence_id, plan binding, every item's plan
    skeleton, and — from each item's frozen source content alone —
    its identity, source hash (bound to the plan), routed values (nested
    content included) and content hash, with the decision selection,
    indexes, reports, row hashes and ordering recomputed. Independent of
    whether the sources still exist; `verify_sources` checks those.
    Raises ResearchEvidenceError."""
    try:
        rec = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ResearchEvidenceError("undecodable") from e
    _keys_exact(rec, _RECORD_KEYS, "keys")
    if (rec["schema"] != SCHEMA or rec["evidence_kind"] != EVIDENCE_KIND
            or rec["collector_id"] != COLLECTOR_ID
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
    except (TypeError, ValueError, KeyError) as e:
        raise ResearchEvidenceError("source_plan_malformed") from e
    if not (_same(rec["source_plan"], expect_sp)
            and _same(rec["scope"], plan["scope"])):
        _fail("source_plan_mismatch")
    skel = _skeleton(plan)
    if not isinstance(rec["hypotheses"], list) \
            or len(rec["hypotheses"]) != len(skel):
        _fail("hypotheses")
    for s, (sec, items) in zip(rec["hypotheses"], skel):
        _keys_exact(s, _SECTION_KEYS, "section_keys")
        if not _same({k: s[k] for k in sec}, sec):
            _fail("section_contract")
        if not isinstance(s["items"], list) or len(s["items"]) != len(items):
            _fail("section_items")
        for it, sk in zip(s["items"], items):
            _keys_exact(it, _ITEM_KEYS, "item_keys")
            if not _same({k: it[k] for k in sk}, sk):
                _fail(f"item_contract:{sk['role']}")
            ident, src_sha, values = _derive(sk, plan, it["source_content"])
            if not (_same(it["source_identity"], ident)
                    and _same(it["source_sha256"], src_sha)):
                _fail(f"item_source:{sk['role']}")
            if not _same(it["values"], values):
                _fail(f"item_values:{sk['role']}")
            if it["content_sha256"] != _sha(canonical(values)):
                _fail(f"content_sha256:{sk['role']}")
    return rec


def verify_sources(rec: dict, ctx: dict) -> dict:
    """Additionally check each (already self-verified) item against its
    live source where that source still exists. {"matched": n, "absent":
    [role:id]}; raises ResearchEvidenceError ("source_changed:...") when a
    present source differs from what was frozen, or when a decision row
    now enters the selection that was not frozen."""
    matched, absent = 0, []
    for s in rec["hypotheses"]:
        for it in s["items"]:
            store, ident, role = (it["source"]["store"],
                                  it["source_identity"], it["role"])
            if store == rp._HEALTH["store"]:
                row = ctx["health"].get(ident["event_id"])
                if row is None:
                    absent.append(f"{role}:{ident['event_id']}")
                    continue
                if (row.get("kind"), row.get("subject"), row.get("detail")) \
                        != (ident["kind"], ident["subject"],
                            it["source_content"]):
                    _fail(f"source_changed:{role}:{ident['event_id']}")
                matched += 1
            elif store == rp._QUESTION["store"]:
                row = ctx["question"]
                if row is None:
                    absent.append(f"{role}:{ident['question_id']}")
                    continue
                if (row.get("canonical_json") != it["source_content"]
                        or row.get("source_event_id")
                        != ident["source_event_id"]):
                    _fail(f"source_changed:{role}")
                matched += 1
            else:
                matched += _verify_decisions(it, ctx["decisions"], absent)
    return {"matched": matched, "absent": absent}


def _verify_decisions(it: dict, rows, absent: list) -> int:
    """Every frozen decision row that still exists must be byte-equal in
    its routed columns; re-running the selection over the current rows
    must give exactly the selection over the frozen rows still present
    (so nothing new enters). Frozen rows since deleted are listed absent;
    their frozen content was already verified by `from_json`."""
    live = {r.get("id"): _raw_decision(r) for r in rows}
    frozen = it["source_content"]
    present = [r for r in frozen if r["id"] in live]
    absent += [f"{it['role']}:{r['id']}" for r in frozen
               if r["id"] not in live]
    if any(canonical(live[r["id"]]) != canonical(r) for r in present):
        _fail(f"source_changed:{it['role']}")
    if canonical(select_decisions(it["locator"], rows)) != canonical(
            select_decisions(it["locator"], present)):
        _fail(f"source_changed:{it['role']}")
    return len(present)

# ── durable record ───────────────────────────────────────────────────────
def row_for(rec: dict) -> dict:
    sp = rec["source_plan"]
    text = canonical(rec)
    return {"evidence_id": rec["evidence_id"], "schema": rec["schema"],
            "evidence_kind": rec["evidence_kind"],
            "collector_id": rec["collector_id"], "plan_id": sp["plan_id"],
            "plan_sha256": sp["canonical_sha256"],
            "question_id": sp["question_id"],
            "scope_kind": rec["scope"]["kind"],
            "scope_id": rec["scope"]["spec_id"],
            "canonical_sha256": _sha(text), "canonical_json": text}


def _verified_plan(prow: dict, questions: dict, health) -> dict:
    """One stored plan row, fully verified as research_plan.load does."""
    qrow = questions.get(prow.get("question_id"))
    if qrow is None:
        raise rp.ResearchPlanError("source_question_missing")
    plan = rp.from_json(prow["canonical_json"], rp._question_text(qrow),
                        health)
    if canonical(rp.row_for(plan)) != canonical(
            {k: prow.get(k) for k in rp.row_for(plan)}):
        raise rp.ResearchPlanError("row_projection")
    return plan


def record_from_journal(journal, now_ms: int) -> dict:
    """Collect evidence for every stored plan that fully verifies. Returns
    {"inserted"|"duplicate"|"conflict": [evidence_id], "refusals":
    [(plan_id, reason)]}. A plan that fails verification, or whose routed
    evidence is missing, tampered or breaks contract, is refused, never
    collected. Not wired into any live path."""
    health = journal.strategy_health_rows()
    questions = {r["question_id"]: r for r in journal.research_questions()}
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for prow in journal.research_plans():
        try:
            plan = _verified_plan(prow, questions, health)
            rec = collect(prow["canonical_json"], plan,
                          _ctx(journal, plan["source_question"]["question_id"]))
        except (rp.ResearchPlanError, ResearchEvidenceError) as e:
            res["refusals"].append((prow.get("plan_id"), str(e)))
            continue
        status = journal.record_research_evidence(row_for(rec),
                                                  recorded_at_ms=now_ms)
        res[status].append(rec["evidence_id"])
    return res


def load(journal, plan_id: str | None = None) -> list:
    """Stored records, each re-verified: canonical form and contract
    against its stored plan row (which must exist and hash to the bound
    plan), row projection, and every source that still exists
    (`verify_sources`). Raises ResearchEvidenceError on the first record
    that fails."""
    plans = {r["plan_id"]: r for r in journal.research_plans()}
    out = []
    for r in journal.research_evidence(plan_id=plan_id):
        prow = plans.get(r["plan_id"])
        if prow is None:
            _fail("source_plan_missing")
        rec = from_json(r["canonical_json"], prow["canonical_json"])
        if canonical(row_for(rec)) != canonical({k: r[k]
                                                 for k in row_for(rec)}):
            _fail("row_projection")
        verify_sources(rec, _ctx(journal,
                                 rec["source_plan"]["question_id"]))
        out.append(rec)
    return out
