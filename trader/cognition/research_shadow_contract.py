"""Research shadow harness contracts — pure record shapes and identities.

Shared by the parent launcher (``research_shadow.py``), the isolated child
(``_research_shadow_child.py``) and the owner report
(``research_shadow_report.py``). Stdlib and the dispatch module only: it
imports no process, network, LLM, Kernel, Risk or Execution code.

Records (all canonical JSON, stored immutable in the shadow database):

- ``research-shadow-start.v1``     — written by the parent before the child
  starts: identity inputs, bounds, database paths, ``cursor_before``;
- ``research-shadow-source-snapshot.v1`` — written by the child in its own
  small transaction before any research work: the exact source facts it
  observed and the source events it selected;
- ``research-shadow-completion.v1`` — written by the child inside the one
  work transaction (with the cursor move and every research artifact), or
  alone for a REFUSED invocation: per-source dispatch and what each family
  run and bank filing returned;
- ``research-shadow-invocation.v1`` — the receipt, written only by the
  parent after the child's outcome is known.

No score, rank, salience, priority, threshold, diagnosis or trading
authority is expressed by any of them.
"""
from __future__ import annotations

import hashlib
import json

from trader.cognition import research_families as rf

HARNESS_ID = "research-shadow-harness.v1"
AUTHORITY = "shadow_context_only"
CURSOR_NAME = "strategy_health_observation_events"

INVOCATION_SCHEMA = "research-shadow-invocation.v1"
START_SCHEMA = "research-shadow-start.v1"
SNAPSHOT_SCHEMA = "research-shadow-source-snapshot.v1"
COMPLETION_SCHEMA = "research-shadow-completion.v1"
CHILD_REQUEST_SCHEMA = "research-shadow-child-request.v1"
CHILD_RESULT_SCHEMA = "research-shadow-child-result.v1"

#: contract ceilings for operator-supplied bounds; NOT production choices
MAX_SOURCES_LIMIT = 10_000
MAX_DEADLINE_S = 86_400

OK, REFUSED, TIMEOUT, FAILED = "OK", "REFUSED", "TIMEOUT", "FAILED"
OUTCOMES = (OK, REFUSED, TIMEOUT, FAILED)
AMBIGUOUS_DISPATCH = "ambiguous_dispatch"
#: FAILED because a committed completion did not verify; only then may the
#: durable cursor differ from cursor_before on a non-committed outcome
COMMITTED_UNVERIFIABLE = "committed_state_unverifiable"

CHILD_COMPLETED, CHILD_FAILED = "COMPLETED", "FAILED"

DECISIONS_ACCESS = "read_live_not_snapshot_bounded"

#: the complete child environment: nothing else is inherited or allowed
CHILD_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "PYTHONHASHSEED",
                  "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONPATH")

SEMANTICS = (
    "shadow_context_only receipt of one bounded research shadow invocation: "
    "OK = the child's one work transaction committed every selected source's "
    "dispatch, family run and bank filing together with the cursor move; "
    "REFUSED = dispatch was AMBIGUOUS for a selected source, so no family ran "
    "and the cursor did not move; TIMEOUT = the parent killed the child at the "
    "wall-clock deadline and no work transaction committed; FAILED = the "
    "child ended any other way without a committed work transaction (or its "
    "committed state failed verification); sources lists only committed or "
    "refused dispatch records; research artifacts live in the shadow "
    "database only; the source journal is read through a read-only attach; "
    "no score, rank, salience, priority, threshold or diagnosis; no Attention, "
    "Kernel, Orchestrator, Risk, Execution, order, LLM, network or trading "
    "authority")

SNAPSHOT_SEMANTICS = (
    "exact source facts observed by the research shadow child through the "
    "read-only attach: the highest strategy-health sweep record id, the "
    "source events selected after the cursor in event-id order, and the "
    "bounded health view the families derived from; decisions are read live "
    "and are not snapshot-bounded")

COMPLETION_SEMANTICS = (
    "what the research shadow child committed for one invocation: every "
    "selected source's research-family-dispatch.v1 record, and exactly what "
    "each family run and bank filing returned; context only")


class ContractError(ValueError):
    """An input or stored shadow record violates its contract."""


def _fail(code):
    raise ContractError(code)


def canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _hex64(v) -> bool:
    return (isinstance(v, str) and len(v) == 64
            and all(c in "0123456789abcdef" for c in v))


def _keys(v, keys, code):
    if not isinstance(v, dict) or set(v) != set(keys):
        _fail(code)


# ── operator inputs ───────────────────────────────────────────────────────
def check_inputs(invocation_key, recorded_at_ms, max_sources,
                 wall_clock_deadline_s) -> None:
    """Every invocation needs all four explicit values; none has a
    default. Raises ContractError naming the first missing/invalid one."""
    if max_sources is None:
        _fail("max_sources_required")
    if wall_clock_deadline_s is None:
        _fail("wall_clock_deadline_required")
    if not _int(max_sources) or not 1 <= max_sources <= MAX_SOURCES_LIMIT:
        _fail("invalid_max_sources")
    if (not _int(wall_clock_deadline_s)
            or not 1 <= wall_clock_deadline_s <= MAX_DEADLINE_S):
        _fail("invalid_wall_clock_deadline")
    if not isinstance(invocation_key, str) or not invocation_key:
        _fail("invalid_invocation_key")
    if not _int(recorded_at_ms) or recorded_at_ms < 0:
        _fail("invalid_recorded_at_ms")


def invocation_id(invocation_key: str, recorded_at_ms: int) -> str:
    return sha256(canonical({"schema": INVOCATION_SCHEMA,
                             "harness_id": HARNESS_ID,
                             "invocation_key": invocation_key,
                             "recorded_at_ms": recorded_at_ms}))


def run_key(inv_id: str, family: str) -> str:
    """The explicit run_key the harness hands a family runner."""
    return f"{HARNESS_ID}:{inv_id}:{family}"


# ── start ─────────────────────────────────────────────────────────────────
_START_KEYS = ("schema", "invocation_id", "harness_id", "inputs", "bounds",
               "source_db", "shadow_db", "cursor_name", "cursor_before")


def start_record(*, invocation_key, recorded_at_ms, max_sources,
                 wall_clock_deadline_s, source_db: dict, shadow_db: dict,
                 cursor_before: int) -> dict:
    rec = {"schema": START_SCHEMA,
           "invocation_id": invocation_id(invocation_key, recorded_at_ms),
           "harness_id": HARNESS_ID,
           "inputs": {"invocation_key": invocation_key,
                      "recorded_at_ms": recorded_at_ms},
           "bounds": {"max_sources": max_sources,
                      "wall_clock_deadline_s": wall_clock_deadline_s},
           "source_db": source_db, "shadow_db": shadow_db,
           "cursor_name": CURSOR_NAME, "cursor_before": cursor_before}
    check_start(rec)
    return json.loads(canonical(rec))


def check_start(rec) -> None:
    _keys(rec, _START_KEYS, "start_keys")
    _keys(rec["inputs"], ("invocation_key", "recorded_at_ms"), "inputs_keys")
    _keys(rec["bounds"], ("max_sources", "wall_clock_deadline_s"),
          "bounds_keys")
    check_inputs(rec["inputs"]["invocation_key"],
                 rec["inputs"]["recorded_at_ms"],
                 rec["bounds"]["max_sources"],
                 rec["bounds"]["wall_clock_deadline_s"])
    if (rec["schema"] != START_SCHEMA or rec["harness_id"] != HARNESS_ID
            or rec["cursor_name"] != CURSOR_NAME
            or rec["invocation_id"] != invocation_id(
                rec["inputs"]["invocation_key"],
                rec["inputs"]["recorded_at_ms"])
            or not _int(rec["cursor_before"]) or rec["cursor_before"] < 0):
        _fail("start_contract")
    _keys(rec["source_db"], ("path", "access"), "source_db_keys")
    _keys(rec["shadow_db"], ("path",), "shadow_db_keys")


# ── snapshot ──────────────────────────────────────────────────────────────
_SNAPSHOT_KEYS = ("schema", "invocation_id", "cursor_before",
                  "sweep_high_water_event_id", "selected_source_event_ids",
                  "health_view", "decisions", "semantics")
_VIEW_KEYS = ("spec_max_event_id", "sweep_max_event_id", "row_count",
              "rows_sha256")


def check_snapshot(rec, start: dict) -> None:
    _keys(rec, _SNAPSHOT_KEYS, "snapshot_keys")
    v = rec["health_view"]
    _keys(v, _VIEW_KEYS, "snapshot_view_keys")
    ids = rec["selected_source_event_ids"]
    if (rec["schema"] != SNAPSHOT_SCHEMA
            or rec["invocation_id"] != start["invocation_id"]
            or rec["cursor_before"] != start["cursor_before"]
            or rec["decisions"] != DECISIONS_ACCESS
            or rec["semantics"] != SNAPSHOT_SEMANTICS
            or not _int(rec["sweep_high_water_event_id"])
            or rec["sweep_high_water_event_id"] < 0
            or not isinstance(ids, list) or not all(_int(x) for x in ids)
            or ids != sorted(set(ids))
            or len(ids) > start["bounds"]["max_sources"]
            or any(not start["cursor_before"] < x
                   <= rec["sweep_high_water_event_id"] for x in ids)
            or v["spec_max_event_id"] != (ids[-1] if ids
                                          else start["cursor_before"])
            or v["sweep_max_event_id"] != rec["sweep_high_water_event_id"]
            or not _int(v["row_count"]) or v["row_count"] < 0
            or not _hex64(v["rows_sha256"])):
        _fail("snapshot_contract")


def health_rows_sha256(rows) -> str:
    """Digest of the exact raw health rows the families derived from."""
    return sha256(canonical([{k: r[k] for k in ("id", "ts", "kind",
                                                "subject", "detail")}
                             for r in sorted(rows, key=lambda r: r["id"])]))


# ── completion ────────────────────────────────────────────────────────────
_COMPLETION_KEYS = ("schema", "invocation_id", "outcome", "outcome_reason",
                    "cursor_before", "cursor_after", "sources",
                    "family_runs", "semantics")
_SOURCE_KEYS = ("source_event_id", "dispatch", "question_id", "run_id",
                "bank_object_ids")
_RUN_KEYS = ("family", "run_schema", "run_key", "run_id", "run_status",
             "run_canonical_sha256", "bank_schema", "bank_filing")
_FILING_KEYS = ("inserted", "duplicate", "conflict", "refusals")


def _check_filing(f):
    _keys(f, _FILING_KEYS, "bank_filing_keys")
    for k in ("inserted", "duplicate", "conflict"):
        if not isinstance(f[k], list) or not all(_hex64(x) for x in f[k]):
            _fail("bank_filing_ids")
    if not isinstance(f["refusals"], list):
        _fail("bank_filing_refusals")
    for r in f["refusals"]:
        if (not isinstance(r, list) or len(r) != 3
                or not (r[0] is None or isinstance(r[0], str))
                or not (r[1] is None or isinstance(r[1], str))
                or not isinstance(r[2], str)):
            _fail("bank_filing_refusals")


def check_completion(rec, start: dict, snapshot: dict) -> None:
    _keys(rec, _COMPLETION_KEYS, "completion_keys")
    if (rec["schema"] != COMPLETION_SCHEMA
            or rec["invocation_id"] != start["invocation_id"]
            or rec["semantics"] != COMPLETION_SEMANTICS
            or rec["outcome"] not in (OK, REFUSED)
            or rec["cursor_before"] != start["cursor_before"]):
        _fail("completion_contract")
    ids = snapshot["selected_source_event_ids"]
    srcs, runs = rec["sources"], rec["family_runs"]
    if not isinstance(srcs, list) or not isinstance(runs, list):
        _fail("completion_layout")
    if [s.get("source_event_id") if isinstance(s, dict) else None
            for s in srcs] != ids:
        _fail("completion_sources_not_selected")
    outcomes = []
    for s in srcs:
        _keys(s, _SOURCE_KEYS, "completion_source_keys")
        try:
            rf.check(s["dispatch"])
        except rf.DispatchError as e:
            _fail(f"completion_dispatch:{e}")
        d = s["dispatch"]
        if d["source_event_id"] != s["source_event_id"]:
            _fail("completion_dispatch_source")
        outcomes.append(d["outcome"])
        routed_q = next((f["question_id"] for f in d["families"]
                         if f["family"] == d["family"]), None)
        if s["question_id"] != routed_q:
            _fail("completion_question_id")
        if not isinstance(s["bank_object_ids"], list) or not all(
                _hex64(x) for x in s["bank_object_ids"]):
            _fail("completion_bank_ids")
    families = []
    for r in runs:
        _keys(r, _RUN_KEYS, "completion_run_keys")
        if (r["family"] not in rf.FAMILY_NAMES or r["family"] in families
                or r["run_key"] != run_key(start["invocation_id"],
                                           r["family"])
                or not _hex64(r["run_id"]) or r["run_status"] != "inserted"
                or not _hex64(r["run_canonical_sha256"])
                or not isinstance(r["run_schema"], str)
                or not isinstance(r["bank_schema"], str)):
            _fail("completion_run")
        families.append(r["family"])
        _check_filing(r["bank_filing"])
    if [f for f in rf.FAMILY_NAMES if f in families] != families:
        _fail("completion_run_order")
    if rec["outcome"] == REFUSED:
        if (rf.AMBIGUOUS not in outcomes
                or rec["outcome_reason"] != AMBIGUOUS_DISPATCH or runs
                or rec["cursor_after"] != start["cursor_before"]
                or any(s["run_id"] is not None or s["bank_object_ids"]
                       for s in srcs)):
            _fail("completion_refused_shape")
        return
    run_of = {r["family"]: r for r in runs}
    for s in srcs:
        fam = s["dispatch"]["family"]
        if fam is None:
            if s["run_id"] is not None or s["bank_object_ids"]:
                _fail("completion_unrouted_source_has_run")
            continue
        if fam not in run_of or s["run_id"] != run_of[fam]["run_id"]:
            _fail("completion_routed_source_run")
        filed = set(run_of[fam]["bank_filing"]["inserted"]
                    + run_of[fam]["bank_filing"]["duplicate"])
        if not set(s["bank_object_ids"]) <= filed:
            _fail("completion_bank_ids_not_filed")
    routed = {s["dispatch"]["family"] for s in srcs} - {None}
    if (rf.AMBIGUOUS in outcomes or rec["outcome_reason"] is not None
            or set(families) != routed
            or rec["cursor_after"] != (ids[-1] if ids
                                       else start["cursor_before"])):
        _fail("completion_ok_shape")


# ── child request / result ────────────────────────────────────────────────
_REQUEST_KEYS = ("schema", "start", "fault")
FAULTS = (None, "hang_in_transaction", "crash_in_transaction",
          "malformed_result_before_work", "malformed_result_after_commit",
          "import_forbidden_module", "fixed_clock")
_RESULT_KEYS = ("schema", "invocation_id", "status", "reason")


def child_request(start: dict, fault=None) -> dict:
    if fault not in FAULTS:
        _fail("invalid_fault")
    return {"schema": CHILD_REQUEST_SCHEMA, "start": start, "fault": fault}


def check_child_request(req) -> None:
    _keys(req, _REQUEST_KEYS, "request_keys")
    if req["schema"] != CHILD_REQUEST_SCHEMA or req["fault"] not in FAULTS:
        _fail("request_contract")
    check_start(req["start"])


def child_result(inv_id: str, status: str, reason) -> dict:
    return {"schema": CHILD_RESULT_SCHEMA, "invocation_id": inv_id,
            "status": status, "reason": reason}


def parse_child_result(text, inv_id: str):
    """The child's one-line result, or None when it is malformed."""
    if not isinstance(text, str) or len(text) > 65_536:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) != 1:
        return None
    try:
        res = json.loads(lines[0])
    except ValueError:
        return None
    if (not isinstance(res, dict) or set(res) != set(_RESULT_KEYS)
            or res["schema"] != CHILD_RESULT_SCHEMA
            or res["invocation_id"] != inv_id
            or res["status"] not in (CHILD_COMPLETED, CHILD_FAILED)
            or not (res["reason"] is None or isinstance(res["reason"], str))
            or (res["status"] == CHILD_COMPLETED) != (res["reason"] is None)):
        return None
    return res


# ── invocation receipt ────────────────────────────────────────────────────
_RECEIPT_KEYS = ("schema", "invocation_id", "harness_id", "dispatch_id",
                 "authority", "inputs", "bounds", "source_db", "shadow_db",
                 "cursor_name", "cursor_before", "cursor_after",
                 "source_snapshot", "sources", "family_runs", "outcome",
                 "outcome_reason", "child", "semantics")
_CHILD_KEYS = ("termination", "returncode", "result")
TERMINATIONS = ("exited", "killed_at_deadline", "not_started",
                "not_observed")
RESULTS = ("wellformed", "malformed", "absent", "not_observed")


def receipt(start: dict, *, snapshot, completion, outcome: str,
            outcome_reason, cursor_after: int, child: dict) -> dict:
    rec = {"schema": INVOCATION_SCHEMA,
           "invocation_id": start["invocation_id"],
           "harness_id": HARNESS_ID, "dispatch_id": rf.DISPATCH_ID,
           "authority": AUTHORITY, "inputs": start["inputs"],
           "bounds": start["bounds"], "source_db": start["source_db"],
           "shadow_db": start["shadow_db"], "cursor_name": CURSOR_NAME,
           "cursor_before": start["cursor_before"],
           "cursor_after": cursor_after, "source_snapshot": snapshot,
           "sources": completion["sources"] if completion else [],
           "family_runs": completion["family_runs"] if completion else [],
           "outcome": outcome, "outcome_reason": outcome_reason,
           "child": child, "semantics": SEMANTICS}
    rec = json.loads(canonical(rec))
    check_receipt(rec)
    return rec


def check_receipt(rec) -> None:
    """Exact-shape verification of one stored invocation receipt."""
    _keys(rec, _RECEIPT_KEYS, "receipt_keys")
    start = {k: rec[k] for k in ("invocation_id", "harness_id", "inputs",
                                 "bounds", "source_db", "shadow_db",
                                 "cursor_name", "cursor_before")}
    start["schema"] = START_SCHEMA
    check_start(start)
    if (rec["schema"] != INVOCATION_SCHEMA
            or rec["dispatch_id"] != rf.DISPATCH_ID
            or rec["authority"] != AUTHORITY
            or rec["semantics"] != SEMANTICS
            or rec["outcome"] not in OUTCOMES
            or not _int(rec["cursor_after"])):
        _fail("receipt_contract")
    _keys(rec["child"], _CHILD_KEYS, "receipt_child_keys")
    if (rec["child"]["termination"] not in TERMINATIONS
            or rec["child"]["result"] not in RESULTS
            or not (rec["child"]["returncode"] is None
                    or _int(rec["child"]["returncode"]))):
        _fail("receipt_child")
    snap = rec["source_snapshot"]
    if snap is not None:
        check_snapshot(snap, start)
    committed = rec["outcome"] in (OK, REFUSED)
    if committed:
        if snap is None:
            _fail("receipt_committed_without_snapshot")
        check_completion({"schema": COMPLETION_SCHEMA,
                          "invocation_id": rec["invocation_id"],
                          "outcome": rec["outcome"],
                          "outcome_reason": rec["outcome_reason"],
                          "cursor_before": rec["cursor_before"],
                          "cursor_after": rec["cursor_after"],
                          "sources": rec["sources"],
                          "family_runs": rec["family_runs"],
                          "semantics": COMPLETION_SEMANTICS}, start, snap)
    else:
        if (rec["sources"] or rec["family_runs"]
                or not isinstance(rec["outcome_reason"], str)
                or (rec["cursor_after"] != rec["cursor_before"]
                    and not rec["outcome_reason"].startswith(
                        COMMITTED_UNVERIFIABLE))):
            _fail("receipt_uncommitted_shape")
