"""Strategy-decay research plans — research-plan.v1.

One deterministic plan per persisted, verified strategy_decay
research-question.v1. The plan ROUTES evidence: for each SDD 14.5 decay
hypothesis it names which existing internal LUFFY records, and which fields
of them, bear on the question. It does not read those fields' values, weigh
them or conclude which hypothesis holds. It carries no score, probability,
ranking, priority, salience, usefulness, threshold, mapping or asset
attribution.

Hypothesis sections, always all four and in this order:

- ``genuine_deterioration``           — ROUTED to the health observation's
  backtest metrics (source, prior) and the question's change flags;
- ``insufficient_recent_opportunities`` — ROUTED to the observation's trade
  counts and scoring window (source, prior) and to raw ``journal.decisions``
  rows selected by decision time and by the spec's signal-occurrence keys
  inside ``signals_json``;
- ``data_failure``                    — ROUTED to the observation's coverage
  and closed-bar facts, the source sweep record, the prior observation,
  every unreadable observation between prior and source, and the history
  sweeps between prior and source;
- ``temporary_regime_absence``        — UNAVAILABLE: no truthful WorldModel
  regime source exists for decay sweeps.

Binding. research-question.v1 binds only its source and prior observation
hashes. The plan additionally binds, at plan registration, the exact raw
content (event id + SHA-256 of the stored detail) of EVERY health record it
routes to: source, prior, source sweep, unreadable observations and every
history sweep the question's derivation depends on (prior sweep through
source sweep; with no prior, every sweep up to the source). Before
binding, each supporting record must satisfy the health writer's own
record contract for its variant, and every routed field must exist in it.
`load` re-checks every binding, so a supporting record changed after
registration fails the plan, and re-registration after such a change is a
conflict, never a duplicate. A change made before registration to content
the question does not bind is only caught by those contract checks; closing
that fully needs a new research-question version, which is not introduced
here. The decision route is a declared selection, not bound content.

`build` depends only on the question text and the health rows.
`record_from_journal` is the only writer (insert / duplicate / conflict);
`load` re-verifies each plan byte-for-byte against a fresh build. Nothing
calls either from the live loop; there is no network, LLM, Attention, Risk,
Execution or trading authority here.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_question as rq
from trader.cognition import research_sources as rs
from trader.strategy import health_observation as ho

SCHEMA = "research-plan.v1"
PLAN_KIND = "strategy_decay"
PLANNER_ID = "strategy-decay-evidence-routing.v1"

GENUINE_DETERIORATION = "genuine_deterioration"
INSUFFICIENT_RECENT_OPPORTUNITIES = "insufficient_recent_opportunities"
DATA_FAILURE = "data_failure"
TEMPORARY_REGIME_ABSENCE = "temporary_regime_absence"
HYPOTHESES = (GENUINE_DETERIORATION, INSUFFICIENT_RECENT_OPPORTUNITIES,
              DATA_FAILURE, TEMPORARY_REGIME_ABSENCE)

ROUTED = "ROUTED"
UNAVAILABLE = "UNAVAILABLE"
#: the registry's UNAVAILABLE WorldModel/regime entry states the reason
_REGIME = rs.unavailable(rs.WORLDMODEL_REGIME_SOURCE_ID)
NO_TRUTHFUL_WORLDMODEL_SOURCE = _REGIME["reason"]
REGIME_UNAVAILABLE_DETAIL = _REGIME["detail"]

SEMANTICS = ("evidence-routing plan for one strategy-decay research question; "
             "names which existing internal records bear on each SDD 14.5 "
             "hypothesis and concludes nothing; no score, probability, "
             "ranking, priority, salience, usefulness, threshold or asset "
             "attribution; not an Attention trigger; no network, LLM, Risk, "
             "Execution or trading authority")

# ── health-record variants (as health_observation._build writes them) ────
EVALUATED = "evaluated"                  # has_decayed returned a branch
COMPILE_FAILED_RECORD = "compile_failed"
EVALUATION_EXCEPTION_RECORD = "evaluation_exception"
SWEEP_RECORD = "sweep"

#: plan-facing source descriptors, read from research-source-registry.v1
_HEALTH = rs.descriptor(rs.HEALTH_SOURCE_ID)
_QUESTION = rs.descriptor(rs.QUESTION_SOURCE_ID)
#: raw decision columns; the selection below runs over them. No record
#: schema: these are journal rows, not a derived summary.
_DECISIONS = rs.descriptor(rs.DECISIONS_SOURCE_ID)
DECISION_SELECTION = "decision-signal-occurrence-selection.v1"
DECISION_SELECTION_RULE = (
    "a decisions row is selected when its ts, parsed as a timezone-aware "
    "ISO-8601 instant, is at or before decision_ts_at_or_before and its "
    "signals_json decodes to a list containing at least one entry whose "
    "trader.strategy.signal_occurrence.signal_occurrence key has spec_id "
    "equal to spec_id and signal_bar_close_ms at or before "
    "signal_bar_close_ms_at_or_before; only those entries are selected "
    "from signals_json; a row whose ts or signals_json cannot be read, or "
    "an entry without an occurrence key, is reported, never selected")

_DETERIORATION_FIELDS = (
    "lifecycle_branch", "lifecycle_verdict_text", "metrics.trades",
    "metrics.wins", "metrics.gross_win", "metrics.gross_loss",
    "metrics.pooled_pf", "metrics.pooled_pf_kind", "metrics.winrate",
    "metrics.pnl", "metrics.per_symbol", "thresholds")
_OPPORTUNITY_FIELDS = (
    "metrics.trades", "metrics.per_symbol", "thresholds", "recent_days",
    "window_days", "window_widened", "window.window_bars",
    "window.window_start_bar_open_ts", "window.window_end_bar_open_ts",
    "window.per_symbol")
_DATA_FIELDS = (
    "coverage", "verdict_reason", "window.last_bar_close_ms",
    "window.last_bar_closed_at_sweep", "window.per_symbol",
    "simulation_settings", "error")
#: unreadable observations: only fields the variant actually writes
_UNREADABLE_FIELDS = {
    EVALUATED: ("verdict", "verdict_reason", "lifecycle_branch", "coverage",
                "error"),
    COMPILE_FAILED_RECORD: ("verdict", "verdict_reason", "lifecycle_branch",
                            "error"),
    EVALUATION_EXCEPTION_RECORD: ("verdict", "verdict_reason",
                                  "lifecycle_branch", "error"),
}
_SWEEP_FIELDS = ("status", "intended_spec_ids", "evaluation_attempted_spec_ids",
                 "evaluation_completed_spec_ids", "compile_failed_spec_ids",
                 "not_evaluated_spec_ids", "observation_failed_spec_ids",
                 "observation_failures", "aborted_at_spec_id", "abort_error")
_HISTORY_SWEEP_FIELDS = _SWEEP_FIELDS + ("observation_recorded_spec_ids",)
_SWEEP_LISTS = ("intended_spec_ids", "evaluation_attempted_spec_ids",
                "evaluation_completed_spec_ids", "compile_failed_spec_ids",
                "not_evaluated_spec_ids", "observation_recorded_spec_ids",
                "observation_failed_spec_ids")
_CHANGE_FIELDS = ("changes_since_prior.spec_version_changed",
                  "changes_since_prior.simulation_settings_changed",
                  "changes_since_prior.thresholds_changed")
_DECISION_FIELDS = ("id", "ts", "scan_id", "executed", "reason_codes",
                    "reason_codes_version", "signals_json")
_ERROR_KEYS = {"stage", "error_class", "message"}

_RECORD_KEYS = ("schema", "plan_id", "plan_kind", "planner_id",
                "source_question", "scope", "hypotheses", "semantics")
_SOURCE_Q_KEYS = ("schema", "question_id", "question_kind", "trigger",
                  "canonical_sha256")


class ResearchPlanError(ValueError):
    """A question cannot be planned, or a stored plan failed verification."""


def _fail(code):
    raise ResearchPlanError(code)


def canonical(payload) -> str:
    return rq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _identity(plan: dict) -> dict:
    sq = plan["source_question"]
    return {"schema": plan["schema"], "plan_kind": plan["plan_kind"],
            "planner_id": plan["planner_id"],
            "question_id": sq["question_id"],
            "question_canonical_sha256": sq["canonical_sha256"]}


def plan_id(plan: dict) -> str:
    return _sha(canonical(_identity(plan)))


# ── supporting-record contracts ──────────────────────────────────────────
def _has(rec: dict, path: str) -> bool:
    cur = rec
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def _error_ok(v) -> bool:
    return (isinstance(v, dict) and set(v) == _ERROR_KEYS
            and all(isinstance(v[k], str) for k in _ERROR_KEYS))


def _decoded(row, what):
    rec, bad = ho._decode(row)
    if bad:
        _fail(f"supporting_record_malformed:{what}:{bad}")
    return rec


def _observation_variant(rec: dict, what: str) -> str:
    """The writer variant of a spec health record, or ResearchPlanError when
    the record does not match any variant the health writer produces."""
    if rec.get("record") != "spec":
        _fail(f"supporting_record_contract:{what}")
    v, reason = rec["verdict"], rec.get("verdict_reason")
    if v == ho.COMPILE_FAILED:
        ok = (reason is None and rec.get("lifecycle_branch") is None
              and rec.get("retirement_action_selected") is False
              and _error_ok(rec.get("error")) and "coverage" not in rec)
        variant = COMPILE_FAILED_RECORD
    elif v == ho.EVALUATION_FAILED and reason == ho.EVALUATION_EXCEPTION:
        ok = (rec.get("lifecycle_branch") is None
              and rec.get("retirement_action_selected") is False
              and _error_ok(rec.get("error")) and "coverage" not in rec)
        variant = EVALUATION_EXCEPTION_RECORD
    else:
        cov = rec.get("coverage")
        ok = (isinstance(cov, dict) and rec.get("error") is None
              and isinstance(rec.get("retirement_action_selected"), bool)
              and (reason is None if v in ho.BRANCHES else
                   v == ho.EVALUATION_FAILED and reason in (
                       ho.COVERAGE_UNAVAILABLE, ho.NO_SYMBOL_SCORED,
                       *ho.COVERAGE_FAULTS)))
        variant = EVALUATED
    if not ok:
        _fail(f"supporting_record_contract:{what}")
    return variant


def _failure_ok(v) -> bool:
    """One observation_failures entry exactly as Sweep._flush writes it."""
    if not isinstance(v, dict) or set(v) != {"step", *_ERROR_KEYS}:
        return False
    if v["step"] == "build":
        return (v["stage"] == "build" and isinstance(v["error_class"], str)
                and isinstance(v["message"], str))
    return (v["step"] == "write" and v["stage"] == "write"
            and v["error_class"] is None and v["message"] is None)


def _sweep_ok(rec: dict) -> bool:
    """The sweep record satisfies the relationships Sweep._flush writes."""
    aborted = rec.get("aborted_at_spec_id")
    if not (all(isinstance(rec.get(k), list)
                and all(isinstance(x, str) for x in rec[k])
                for k in _SWEEP_LISTS)
            and isinstance(rec.get("observation_failures"), dict)):
        return False
    attempted = set(rec["evaluation_attempted_spec_ids"])
    failures = rec["observation_failures"]
    return (rec.get("record") == "sweep"
            and rec.get("status") in (ho.SWEEP_COMPLETED, ho.SWEEP_ABORTED)
            # failure ids are sorted(failures) and each entry is well formed
            and rec["observation_failed_spec_ids"] == sorted(failures)
            and all(_failure_ok(v) for v in failures.values())
            # never reached = intended minus attempted, in intended order
            and rec["not_evaluated_spec_ids"] == [
                x for x in rec["intended_spec_ids"] if x not in attempted]
            and (aborted is None or (isinstance(aborted, str) and aborted))
            # set together by the writer; an abort is never "completed"
            and (aborted is None) == (rec.get("abort_error") is None)
            and (aborted is None or _error_ok(rec["abort_error"]))
            and (aborted is None or rec["status"] == ho.SWEEP_ABORTED))


def _history_sweeps(q: dict, rows) -> list:
    """[(sweep_id, row)] for every sweep record, other than the source
    sweep, that the question's derivation depends on: those started in
    [prior sweep start, source sweep start] — the prior is always the
    baseline of an emitted transition (a gap at its own sweep would have
    refused the question) — or, with no prior, every sweep started at or
    before the source sweep. Ordered by start. Each must satisfy the sweep
    contract and carry the routed fields. `verify_evidence` has already
    proved every sweep record in that range decodes."""
    src, prior = q["source"], q["prior"]
    hi = rq._ms(src["sweep_started_at"])
    lo = rq._ms(prior["sweep_started_at"]) if prior is not None else None
    out = []
    for r in rows:
        if not (isinstance(r, dict) and r.get("kind") == ho.KIND_SWEEP):
            continue
        rec, bad = ho._decode(r)
        sw = None if bad else rq._sweep(rec)
        if sw is None or sw["sweep_id"] == src["sweep_id"]:
            continue                    # outside the range: derive refused
        if sw["started_ms"] > hi or (lo is not None and sw["started_ms"] < lo):
            continue
        what = f"history_sweep:{sw['sweep_id']}"
        if not rq._int(r.get("id")) or not _sweep_ok(rec):
            _fail(f"supporting_record_contract:{what}")
        _require_fields(rec, _HISTORY_SWEEP_FIELDS, what)
        out.append((sw["started_ms"], sw["sweep_id"], r))
    return [(sid, r) for _, sid, r in sorted(out, key=lambda x: x[:2])]


def _require_fields(rec, fields, what):
    for f in fields:
        if not _has(rec, f):
            _fail(f"routed_field_missing:{what}:{f}")


def _binding(row) -> dict:
    return {"event_id": row["id"], "record_sha256": _sha(row["detail"])}


# ── routing ──────────────────────────────────────────────────────────────
def _item(role, source_id, source, record_kind, variant, locator, fields,
          binding):
    try:
        # each route is bound to its own registry entry, not mere membership
        rs.require(source_id, source)
    except rs.ResearchSourceError as e:
        raise ResearchPlanError(f"source_unregistered:{role}") from e
    return {"role": role, **source, "record_kind": record_kind,
            "record_variant": variant, "locator": locator,
            "fields": list(fields), "binding": binding}


def _routes(q: dict, rows) -> list:
    src, prior = q["source"], q["prior"]
    sid = src["spec_id"]
    spec_rows = {r["id"]: r for r in rows
                 if isinstance(r, dict) and r.get("kind") == ho.KIND_SPEC
                 and r.get("subject") == sid and rq._int(r.get("id"))}

    def observation(role, event_id, fields_for):
        row = spec_rows.get(event_id)
        if row is None or not isinstance(row.get("detail"), str):
            _fail(f"supporting_record_missing:{role}")
        rec = _decoded(row, role)
        variant = _observation_variant(rec, role)
        fields = fields_for(variant)
        _require_fields(rec, fields, role)
        return _item(role, rs.HEALTH_SOURCE_ID, _HEALTH, ho.KIND_SPEC,
                     variant, {"event_id": event_id}, fields, _binding(row))

    def truthful(fields):
        # verify_evidence already proved source/prior truthful, so their
        # variant is EVALUATED; anything else fails the contract below
        def pick(variant):
            if variant != EVALUATED:
                _fail("supporting_record_contract:truthful")
            return fields
        out = [observation("source_observation", src["event_id"], pick)]
        if prior is not None:
            out.append(observation("prior_observation", prior["event_id"],
                                   pick))
        return out

    sweep_rows = [r for r in rows
                  if isinstance(r, dict) and r.get("kind") == ho.KIND_SWEEP
                  and r.get("subject") == src["sweep_id"]]
    if len(sweep_rows) != 1 or not rq._int(sweep_rows[0].get("id")):
        _fail("supporting_record_missing:source_sweep")
    [sweep_row] = sweep_rows
    sweep = _decoded(sweep_row, "source_sweep")
    if not _sweep_ok(sweep):
        _fail("supporting_record_contract:source_sweep")
    _require_fields(sweep, _SWEEP_FIELDS, "source_sweep")
    history = _history_sweeps(q, rows)

    deterioration = truthful(_DETERIORATION_FIELDS)
    if prior is not None:
        deterioration.append(_item(
            "question_change_flags", rs.QUESTION_SOURCE_ID, _QUESTION,
            rq.QUESTION_KIND, None, {"question_id": q["question_id"]},
            _CHANGE_FIELDS, None))
    opportunities = truthful(_OPPORTUNITY_FIELDS) + [_item(
        "strategy_signal_decisions", rs.DECISIONS_SOURCE_ID, _DECISIONS,
        "decision_row", None,
        {"selection": DECISION_SELECTION,
         "rule": DECISION_SELECTION_RULE,
         "spec_id": sid,
         "decision_ts_at_or_before": src["sweep_started_at"],
         "signal_bar_close_ms_at_or_before": rq._ms(src["sweep_started_at"])},
        _DECISION_FIELDS, None)]
    data = truthful(_DATA_FIELDS)
    data.append(_item("source_sweep", rs.HEALTH_SOURCE_ID, _HEALTH,
                      ho.KIND_SWEEP, SWEEP_RECORD,
                      {"sweep_id": src["sweep_id"]}, _SWEEP_FIELDS,
                      _binding(sweep_row)))
    data += [observation("unreadable_observation", e,
                         lambda v: _UNREADABLE_FIELDS[v])
             for e in q["unreadable_since_prior_event_ids"]]
    data += [_item("history_sweep", rs.HEALTH_SOURCE_ID, _HEALTH,
                   ho.KIND_SWEEP, SWEEP_RECORD, {"sweep_id": sw_id},
                   _HISTORY_SWEEP_FIELDS, _binding(row))
             for sw_id, row in history]

    def section(h, evidence):
        return {"hypothesis": h, "status": ROUTED, "unavailable_reason": None,
                "unavailable_detail": None, "evidence": evidence}

    return [section(GENUINE_DETERIORATION, deterioration),
            section(INSUFFICIENT_RECENT_OPPORTUNITIES, opportunities),
            section(DATA_FAILURE, data),
            {"hypothesis": TEMPORARY_REGIME_ABSENCE, "status": UNAVAILABLE,
             "unavailable_reason": NO_TRUTHFUL_WORLDMODEL_SOURCE,
             "unavailable_detail": REGIME_UNAVAILABLE_DETAIL, "evidence": []}]


def _question(question_json) -> dict:
    """The contract-valid strategy_decay question, or ResearchPlanError."""
    if not isinstance(question_json, str):
        _fail("question_invalid:not_text")
    try:
        q = rq.from_json(question_json)
    except rq.ResearchQuestionError as e:
        raise ResearchPlanError(f"question_invalid:{e}") from e
    if q["question_kind"] != PLAN_KIND or q["schema"] != rq.SCHEMA:
        _fail("question_unsupported")
    return q


def build(question_json: str, health_rows) -> dict:
    """The canonical research-plan.v1 for one stored question JSON and the
    journal's health rows. Deterministic; refuses (ResearchPlanError) a
    question that is not a contract-valid strategy_decay
    research-question.v1, whose bound evidence does not verify, or whose
    supporting records break the health writer's contracts."""
    q = _question(question_json)
    rows = list(health_rows)
    try:
        rq.verify_evidence(q, rows)
    except rq.ResearchQuestionError as e:
        raise ResearchPlanError(f"question_evidence:{e}") from e
    plan = {
        "schema": SCHEMA,
        "plan_kind": PLAN_KIND,
        "planner_id": PLANNER_ID,
        "source_question": {"schema": q["schema"],
                            "question_id": q["question_id"],
                            "question_kind": q["question_kind"],
                            "trigger": q["trigger"],
                            "canonical_sha256": _sha(question_json)},
        "scope": dict(q["scope"]),
        "hypotheses": _routes(q, rows),
        "semantics": SEMANTICS,
    }
    plan["plan_id"] = plan_id(plan)
    return json.loads(canonical(plan))


# ── contract verification ────────────────────────────────────────────────
def from_json(text: str, question_json: str, health_rows) -> dict:
    """Parse and verify a stored plan: exact top-level keys, plan_id, the
    question binding, and the stored text byte-identical to the canonical
    serialization of `build(question_json, health_rows)` — so nested types
    (1 vs 1.0 vs true) and every supporting-record binding are checked.
    Raises ResearchPlanError."""
    try:
        plan = json.loads(text)
    except (TypeError, ValueError) as e:
        raise ResearchPlanError("undecodable") from e
    if not isinstance(plan, dict) or set(plan) != set(_RECORD_KEYS):
        _fail("keys")
    if (plan["schema"] != SCHEMA or plan["plan_kind"] != PLAN_KIND
            or plan["planner_id"] != PLANNER_ID
            or plan["semantics"] != SEMANTICS):
        _fail("contract")
    sq = plan["source_question"]
    if not isinstance(sq, dict) or set(sq) != set(_SOURCE_Q_KEYS):
        _fail("source_question_keys")
    if canonical(plan) != text:
        _fail("not_canonical")
    if plan["plan_id"] != plan_id(plan):
        _fail("plan_id")
    if sq["canonical_sha256"] != _sha(question_json):
        _fail("source_question_mismatch")
    if text != canonical(build(question_json, health_rows)):
        _fail("plan_content")
    return plan


# ── durable record ───────────────────────────────────────────────────────
def row_for(plan: dict) -> dict:
    sq = plan["source_question"]
    return {"plan_id": plan["plan_id"], "schema": plan["schema"],
            "plan_kind": plan["plan_kind"], "planner_id": plan["planner_id"],
            "question_id": sq["question_id"],
            "question_sha256": sq["canonical_sha256"],
            "scope_kind": plan["scope"]["kind"],
            "scope_id": plan["scope"]["spec_id"],
            "canonical_json": canonical(plan)}


def _question_text(qrow: dict) -> str:
    """The stored question's JSON after its contract and row-projection
    checks; `build` verifies its evidence."""
    text = qrow.get("canonical_json")
    q = _question(text)
    if canonical(rq.row_for(q)) != canonical({k: qrow.get(k)
                                              for k in rq.row_for(q)}):
        _fail("question_invalid:row_projection")
    return text


def record_from_journal(journal, now_ms: int) -> dict:
    """Plan every stored research question that verifies. Returns
    {"inserted"|"duplicate"|"conflict": [plan_id], "refusals":
    [(question_id, reason)]}. A question that fails verification, is not a
    strategy_decay question, or whose supporting records break contract is
    refused, never planned. Not wired into any live path."""
    health = journal.strategy_health_rows()
    res = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}
    for qrow in journal.research_questions():
        try:
            plan = build(_question_text(qrow), health)
        except ResearchPlanError as e:
            res["refusals"].append((qrow.get("question_id"), str(e)))
            continue
        status = journal.record_research_plan(row_for(plan),
                                              recorded_at_ms=now_ms)
        res[status].append(plan["plan_id"])
    return res


def load(journal, question_id: str | None = None) -> list:
    """Stored plans, each re-verified: its source question row (present,
    contract-valid, evidence-verified against the current health rows),
    every supporting-record binding, byte equality with a fresh build and
    its row projection. Raises ResearchPlanError on the first plan that
    fails."""
    health = journal.strategy_health_rows()
    questions = {r["question_id"]: r for r in journal.research_questions()}
    out = []
    for r in journal.research_plans(question_id=question_id):
        qrow = questions.get(r["question_id"])
        if qrow is None:
            _fail("source_question_missing")
        plan = from_json(r["canonical_json"], _question_text(qrow), health)
        if canonical(row_for(plan)) != canonical({k: r[k] for k in row_for(plan)}):
            _fail("row_projection")
        out.append(plan)
    return out
