"""Strategy-decay research runs — research-run.v1.

One explicit, offline run executes the existing strategy-decay chain in
order, each step through its own unchanged ``record_from_journal``:

    question (research-question.v1) -> plan (research-plan.v1)
    -> evidence (research-evidence.v1) -> result (research-result.v1)

and persists one immutable receipt recording exactly what each step
returned: inserted, duplicate and conflict IDs, and every refusal with its
existing reason string, verbatim and in the order the step returned it.
The runner adds no research semantics: no predicate, conclusion, label,
score, threshold, priority, salience, asset attribution or suppression.
Results stay whatever research_result.py makes them (INCONCLUSIVE).

Identity. ``run_id`` hashes only the explicit run inputs — the caller's
``run_key`` and the ``recorded_at_ms`` handed to every step — never a clock
reading. A run whose receipt already exists is verified and returned without
executing any step again, so retry and restart are idempotent. Two receipts
for one ``run_id`` that differ semantically are a conflict; nothing is ever
overwritten.

A step that raises is recorded ``FAILED`` with the exception type and
message; its outcomes are unknown (``null``, never guessed) and later steps
are ``NOT_RUN``. Refused or failed upstream work fabricates no downstream
record: downstream steps only ever see what the store holds.

Telemetry (research-run-telemetry.v1) is stored beside the receipt with
its own SHA-256, outside the receipt hash, the run identity and duplicate
comparison. ``elapsed_wall_ns`` is MEASURED around each executed step with
``time.perf_counter_ns``. ``rows_read`` is MEASURED by scoped
instrumentation of the actual Journal instance the step receives (no
proxy): every row returned by ``Journal.query`` on the runner's thread is
counted, including calls made inside Journal methods. The declared storage
writers (``_WRITERS``) read through their own transaction connection; those
rows are excluded by definition. Any other connection access, any Journal
access from another thread, or a non-list ``query`` result makes
``rows_read`` NOT_MEASURED with the reason, because coverage is then not
complete. A step that did not run has both NOT_MEASURED. Nothing is
estimated. Telemetry is not evidence, ranking or quality, and no budget,
cap or cost limit is declared.

`run` is the only writer; `load` re-verifies. Nothing live calls either:
no Kernel, Attention, Analyst, Risk, Execution, LLM or network.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_result as rr

SCHEMA = "research-run.v1"
TELEMETRY_SCHEMA = "research-run-telemetry.v1"
RUN_KIND = "strategy_decay"
RUNNER_ID = "strategy-decay-research-runner.v1"

COMPLETED, FAILED, NOT_RUN = "COMPLETED", "FAILED", "NOT_RUN"
STEP_RAISED = "step_raised"
UPSTREAM_STEP_FAILED = "upstream_step_failed"
MEASURED, NOT_MEASURED = "MEASURED", "NOT_MEASURED"
STEP_NOT_RUN = "step_not_run"
UNMETERED_ACCESS = "unmetered_journal_access"

OUTCOME_KEYS = ("inserted", "duplicate", "conflict", "refusals")

# (step, module, record schema, refusal source-id key; None = rq.Refusal)
STEPS = (("question", rq, rq.SCHEMA, None),
         ("plan", rp, rp.SCHEMA, "question_id"),
         ("evidence", re_, re_.SCHEMA, "plan_id"),
         ("result", rr, rr.SCHEMA, "evidence_id"))
_QUESTION_REFUSAL_KEYS = ("spec_id", "reason", "event_id", "sweep_id")

_WRITERS = ("record_research_question", "record_research_plan",
            "record_research_evidence", "record_research_result")
DIRECT_CONNECTION = "direct_connection_access"
CONCURRENT_ACCESS = "concurrent_journal_access"
NON_LIST_QUERY = "non_list_query_result"

ROWS_DEFINITION = ("rows returned by Journal.query on the runner thread "
                   "during the step, including calls inside Journal "
                   "methods; excludes SQLite page reads and rows read by "
                   "the declared storage writers " + ", ".join(_WRITERS)
                   + " through their own transaction connection")
CLOCK = "time.perf_counter_ns"

SEMANTICS = ("orchestration/audit receipt of one explicit offline run of "
             "the existing strategy-decay research chain; records exactly "
             "what each step returned; no predicate, conclusion, label, "
             "score, threshold, priority, salience, asset attribution or "
             "suppression; no Attention, Kernel, Analyst, Risk, Execution, "
             "LLM, network or trading authority")
TELEMETRY_SEMANTICS = ("MEASURED telemetry only; not evidence, ranking or "
                       "quality; no budget, cap or cost limit is declared")

_RECEIPT_KEYS = ("schema", "run_kind", "runner_id", "run_id", "inputs",
                 "steps", "semantics")
_INPUT_KEYS = ("run_key", "recorded_at_ms")
_STEP_KEYS = ("step", "record_schema", "status", "reason", "error",
              "outcomes")
_ERROR_KEYS = ("type", "message")
_TELEMETRY_KEYS = ("schema", "run_id", "steps", "semantics")
_TSTEP_KEYS = ("step", "elapsed_wall_ns", "rows_read")
_MEASURE_KEYS = ("status", "value", "unit", "source", "reason")


class ResearchRunError(ValueError):
    """A run could not be recorded, or a stored receipt failed verification."""


def _fail(code):
    raise ResearchRunError(code)


def canonical(payload) -> str:
    return rq.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _opt(v, kind) -> bool:
    return v is None or (_int(v) if kind is int else isinstance(v, kind))


def run_id(run_key: str, recorded_at_ms: int) -> str:
    return _sha(canonical({"schema": SCHEMA, "run_kind": RUN_KIND,
                           "runner_id": RUNNER_ID, "run_key": run_key,
                           "recorded_at_ms": recorded_at_ms}))


def _check_inputs(run_key, recorded_at_ms):
    if not isinstance(run_key, str) or not run_key:
        _fail("invalid_run_key")
    if not _int(recorded_at_ms) or recorded_at_ms < 0:
        _fail("invalid_recorded_at_ms")


# ── metering: scoped instrumentation of the actual Journal ──────────────
class _Meter:
    """Context manager instrumenting one Journal instance for one step.
    Instance attributes shadow ``query``, ``_conn`` and the declared
    writers only for the step's duration and delegate to the originals, so
    the step receives the real Journal and every other attribute behaves
    unchanged. Instance state is restored on exit, even when the step
    raises."""

    _NAMES = ("query", "_conn") + _WRITERS

    def __init__(self, journal):
        self._j = journal
        self._tid = threading.get_ident()
        self._depth = 0          # inside query or a declared writer
        self.rows = 0
        self.gaps = set()

    def _own(self):
        if threading.get_ident() != self._tid:
            self.gaps.add(CONCURRENT_ACCESS)
            return False
        return True

    def _scoped(self, orig, count):
        def call(*a, **k):
            if not self._own():
                return orig(*a, **k)
            self._depth += 1
            try:
                out = orig(*a, **k)
            finally:
                self._depth -= 1
            if count:
                if isinstance(out, list):
                    self.rows += len(out)
                else:
                    self.gaps.add(NON_LIST_QUERY)
            return out
        return call

    def _conn_guard(self, orig):
        def call(*a, **k):
            if self._own() and self._depth == 0:
                self.gaps.add(DIRECT_CONNECTION)
            return orig(*a, **k)
        return call

    def __enter__(self):
        d = vars(self._j)
        self._saved = {n: d[n] for n in self._NAMES if n in d}
        orig = {n: getattr(self._j, n) for n in self._NAMES}
        d["query"] = self._scoped(orig["query"], count=True)
        d["_conn"] = self._conn_guard(orig["_conn"])
        for n in _WRITERS:
            d[n] = self._scoped(orig[n], count=False)
        return self

    def __exit__(self, *exc):
        d = vars(self._j)
        for n in self._NAMES:
            d.pop(n, None)
        d.update(self._saved)
        return False


def _measured(value, unit, source):
    return {"status": MEASURED, "value": value, "unit": unit,
            "source": source, "reason": None}


def _not_measured(unit, reason):
    return {"status": NOT_MEASURED, "value": None, "unit": unit,
            "source": None, "reason": reason}


# ── outcome capture: exactly what the step returned ──────────────────────
def _refusal(step_key, r):
    if step_key is None:
        if not isinstance(r, rq.Refusal):
            _fail("unrecognized_refusal:question")
        return {k: getattr(r, k) for k in _QUESTION_REFUSAL_KEYS}
    if not isinstance(r, tuple) or len(r) != 2:
        _fail(f"unrecognized_refusal:{step_key}")
    return {step_key: r[0], "reason": r[1]}


def _outcomes(step_key, res):
    if not isinstance(res, dict) or tuple(sorted(res)) != tuple(
            sorted(OUTCOME_KEYS)):
        _fail("unrecognized_step_result")
    out = {k: list(res[k]) for k in OUTCOME_KEYS[:3]}
    out["refusals"] = [_refusal(step_key, r) for r in res["refusals"]]
    _check_outcomes(step_key, out)
    return out


def run(journal, run_key: str, recorded_at_ms: int, *,
        clock=time.perf_counter_ns) -> dict:
    """Execute one explicit run and persist its receipt. Returns
    {"status": "inserted"|"duplicate"|"conflict", "executed": bool,
    "run_id", "receipt", "telemetry"}. An already-recorded run is verified
    (``load``) and returned with executed False; no step runs again. A
    conflicting receipt is returned but never stored. Not wired into any
    live path."""
    _check_inputs(run_key, recorded_at_ms)
    rid = run_id(run_key, recorded_at_ms)
    if journal.research_runs(run_id=rid):
        [stored] = load(journal, run_id=rid)
        return {"status": "duplicate", "executed": False, "run_id": rid,
                **stored}
    steps, tsteps, failed = [], [], False
    for name, mod, schema, key in STEPS:
        if failed:
            steps.append({"step": name, "record_schema": schema,
                          "status": NOT_RUN, "reason": UPSTREAM_STEP_FAILED,
                          "error": None, "outcomes": None})
            tsteps.append({"step": name,
                           "elapsed_wall_ns": _not_measured("ns", STEP_NOT_RUN),
                           "rows_read": _not_measured("rows", STEP_NOT_RUN)})
            continue
        with _Meter(journal) as meter:
            t0 = clock()
            try:
                res = mod.record_from_journal(journal, now_ms=recorded_at_ms)
                err = None
            except Exception as e:       # recorded, never retried or guessed
                res, err = None, {"type": type(e).__name__, "message": str(e)}
            elapsed = clock() - t0
        if err is None:
            steps.append({"step": name, "record_schema": schema,
                          "status": COMPLETED, "reason": None, "error": None,
                          "outcomes": _outcomes(key, res)})
        else:
            failed = True
            steps.append({"step": name, "record_schema": schema,
                          "status": FAILED, "reason": STEP_RAISED,
                          "error": err, "outcomes": None})
        rows = (_measured(meter.rows, "rows", ROWS_DEFINITION)
                if not meter.gaps else _not_measured(
                    "rows", f"{UNMETERED_ACCESS}:"
                    + ",".join(sorted(meter.gaps))))
        tsteps.append({"step": name,
                       "elapsed_wall_ns": _measured(elapsed, "ns", CLOCK),
                       "rows_read": rows})
    receipt = {"schema": SCHEMA, "run_kind": RUN_KIND, "runner_id": RUNNER_ID,
               "run_id": rid,
               "inputs": {"run_key": run_key,
                          "recorded_at_ms": recorded_at_ms},
               "steps": steps, "semantics": SEMANTICS}
    telemetry = {"schema": TELEMETRY_SCHEMA, "run_id": rid, "steps": tsteps,
                 "semantics": TELEMETRY_SEMANTICS}
    _check_receipt(receipt)
    _check_telemetry(telemetry, receipt)
    status = journal.record_research_run(row_for(receipt, telemetry),
                                         recorded_at_ms=recorded_at_ms)
    return {"status": status, "executed": True, "run_id": rid,
            "receipt": receipt, "telemetry": telemetry}


# ── exact nested validation ──────────────────────────────────────────────
def _keys(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


def _check_outcomes(step_key, out):
    _keys(out, OUTCOME_KEYS, "outcomes_keys")
    for k in OUTCOME_KEYS[:3]:
        if (not isinstance(out[k], list)
                or not all(isinstance(x, str) for x in out[k])):
            _fail(f"outcome_ids:{k}")
    if not isinstance(out["refusals"], list):
        _fail("refusals_type")
    for r in out["refusals"]:
        if step_key is None:
            _keys(r, _QUESTION_REFUSAL_KEYS, "refusal_keys")
            if not (_opt(r["spec_id"], str) and isinstance(r["reason"], str)
                    and _opt(r["event_id"], int)
                    and _opt(r["sweep_id"], str)):
                _fail("refusal_types")
        else:
            _keys(r, (step_key, "reason"), "refusal_keys")
            if not (_opt(r[step_key], str) and isinstance(r["reason"], str)):
                _fail("refusal_types")


def _check_receipt(rec):
    _keys(rec, _RECEIPT_KEYS, "receipt_keys")
    if (rec["schema"] != SCHEMA or rec["run_kind"] != RUN_KIND
            or rec["runner_id"] != RUNNER_ID or rec["semantics"] != SEMANTICS):
        _fail("receipt_contract")
    _keys(rec["inputs"], _INPUT_KEYS, "inputs_keys")
    _check_inputs(rec["inputs"]["run_key"], rec["inputs"]["recorded_at_ms"])
    if rec["run_id"] != run_id(rec["inputs"]["run_key"],
                               rec["inputs"]["recorded_at_ms"]):
        _fail("run_id_mismatch")
    steps = rec["steps"]
    if not isinstance(steps, list) or len(steps) != len(STEPS):
        _fail("steps_layout")
    seen_failure = False
    for s, (name, _mod, schema, key) in zip(steps, STEPS):
        _keys(s, _STEP_KEYS, "step_keys")
        if s["step"] != name or s["record_schema"] != schema:
            _fail("step_order")
        st = s["status"]
        if seen_failure:
            if (st != NOT_RUN or s["reason"] != UPSTREAM_STEP_FAILED
                    or s["error"] is not None or s["outcomes"] is not None):
                _fail("step_after_failure")
        elif st == COMPLETED:
            if s["reason"] is not None or s["error"] is not None:
                _fail("completed_step_shape")
            _check_outcomes(key, s["outcomes"])
        elif st == FAILED:
            seen_failure = True
            _keys(s["error"], _ERROR_KEYS, "error_keys")
            if (s["reason"] != STEP_RAISED or s["outcomes"] is not None
                    or not all(isinstance(s["error"][k], str)
                               for k in _ERROR_KEYS)):
                _fail("failed_step_shape")
        else:
            _fail("step_status")


def _check_measure(m, unit, source, not_run):
    _keys(m, _MEASURE_KEYS, "measure_keys")
    if m["unit"] != unit:
        _fail("measure_unit")
    if m["status"] == MEASURED and not not_run:
        if (not _int(m["value"]) or m["value"] < 0 or m["source"] != source
                or m["reason"] is not None):
            _fail("measured_shape")
    elif m["status"] == NOT_MEASURED:
        if (m["value"] is not None or m["source"] is not None
                or not isinstance(m["reason"], str) or not m["reason"]):
            _fail("not_measured_shape")
        if not_run and m["reason"] != STEP_NOT_RUN:
            _fail("not_run_reason")
        if (not not_run and unit == "ns") or (
                not not_run and not m["reason"].startswith(
                    UNMETERED_ACCESS + ":")):
            _fail("not_measured_reason")
    else:
        _fail("measure_status")


def _check_telemetry(tel, rec):
    _keys(tel, _TELEMETRY_KEYS, "telemetry_keys")
    if (tel["schema"] != TELEMETRY_SCHEMA or tel["run_id"] != rec["run_id"]
            or tel["semantics"] != TELEMETRY_SEMANTICS):
        _fail("telemetry_contract")
    if not isinstance(tel["steps"], list) or len(tel["steps"]) != len(STEPS):
        _fail("telemetry_layout")
    for t, s in zip(tel["steps"], rec["steps"]):
        _keys(t, _TSTEP_KEYS, "telemetry_step_keys")
        if t["step"] != s["step"]:
            _fail("telemetry_step_order")
        not_run = s["status"] == NOT_RUN
        _check_measure(t["elapsed_wall_ns"], "ns", CLOCK, not_run)
        _check_measure(t["rows_read"], "rows", ROWS_DEFINITION, not_run)


def _no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            _fail("duplicate_json_key")
        out[k] = v
    return out


def _parse(text, what):
    if not isinstance(text, str):
        _fail(f"{what}_not_text")
    try:
        v = json.loads(text, object_pairs_hook=_no_dupes,
                       parse_constant=lambda c: _fail("non_finite"))
    except ValueError as e:
        if isinstance(e, ResearchRunError):
            raise
        _fail(f"{what}_not_json")
    if canonical(v) != text:
        _fail(f"{what}_not_canonical")
    return v


def from_json(text: str, telemetry_text: str) -> tuple:
    rec = _parse(text, "receipt")
    _check_receipt(rec)
    tel = _parse(telemetry_text, "telemetry")
    _check_telemetry(tel, rec)
    return rec, tel


# ── durable record ───────────────────────────────────────────────────────
def row_for(rec: dict, telemetry: dict) -> dict:
    text, tel = canonical(rec), canonical(telemetry)
    return {"run_id": rec["run_id"], "schema": rec["schema"],
            "run_kind": rec["run_kind"], "runner_id": rec["runner_id"],
            "run_key": rec["inputs"]["run_key"],
            "run_recorded_at_ms": rec["inputs"]["recorded_at_ms"],
            "canonical_sha256": _sha(text), "canonical_json": text,
            "telemetry_sha256": _sha(tel), "telemetry_json": tel}


def _verify_question(journal, rows_by_id, qid, health):
    q = rq.from_json(rows_by_id[qid]["canonical_json"])
    if rq.row_for(q) != {k: rows_by_id[qid][k] for k in rq.row_for(q)}:
        _fail("reference_invalid:question:row_projection")
    rq.verify_evidence(q, health)


def _verify_references(journal, rec):
    """Every inserted/duplicate ID must be stored and verify under its own
    contract; a conflict ID is verified where it is stored."""
    health = journal.strategy_health_rows()
    stores = {"question": ({r["question_id"]: r
                            for r in journal.research_questions()}),
              "plan": {r["plan_id"]: r for r in journal.research_plans()},
              "evidence": {r["evidence_id"]: r
                           for r in journal.research_evidence()},
              "result": {r["result_id"]: r
                         for r in journal.research_results()}}
    for s in rec["steps"]:
        if s["status"] != COMPLETED:
            continue
        name, rows = s["step"], stores[s["step"]]
        out = s["outcomes"]
        required = out["inserted"] + out["duplicate"]
        for rid in required + [c for c in out["conflict"] if c in rows]:
            if rid not in rows:
                _fail(f"reference_missing:{name}")
            try:
                if name == "question":
                    _verify_question(journal, rows, rid, health)
                    continue
                if name == "plan":
                    got = rp.load(journal, question_id=rows[rid]["question_id"])
                    ids = [p["plan_id"] for p in got]
                elif name == "evidence":
                    got = re_.load(journal, plan_id=rows[rid]["plan_id"])
                    ids = [e["evidence_id"] for e in got]
                else:
                    got = rr.load(journal,
                                  evidence_id=rows[rid]["evidence_id"])
                    ids = [r["result_id"] for r in got]
            except (rq.ResearchQuestionError, rp.ResearchPlanError,
                    re_.ResearchEvidenceError, rr.ResearchResultError) as e:
                raise ResearchRunError(
                    f"reference_invalid:{name}:{e}") from e
            if rid not in ids:
                _fail(f"reference_invalid:{name}:id_mismatch")


def load(journal, run_id: str | None = None) -> list:
    """Stored runs as {"receipt", "telemetry"}, each re-verified: canonical
    form and exact nested contract of both, run_id recomputed from the
    inputs, row projection (including the stored receipt and telemetry
    SHA-256 digests), and every referenced question/plan/evidence/
    result under its own module's verification. Raises ResearchRunError on
    the first run that fails."""
    out = []
    for r in journal.research_runs(run_id=run_id):
        rec, tel = from_json(r["canonical_json"], r["telemetry_json"])
        if canonical(row_for(rec, tel)) != canonical(
                {k: r[k] for k in row_for(rec, tel)}):
            _fail("row_projection")
        _verify_references(journal, rec)
        out.append({"receipt": rec, "telemetry": tel})
    return out
