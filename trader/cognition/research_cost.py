"""Research cost ledger — research-cost-ledger.v1.

A read-only, deterministic view of what LUFFY has actually measured about
the cost of its research, from two existing, separately defined sources:

- ``investigation-resource-receipt.v1`` (observability/investigation.py):
  one receipt per committed execution of case-local investigation work;
- ``research-run-telemetry.v1`` (cognition/research_run.py): per-step
  wall time and Journal rows of stored offline strategy-decay runs.

Every measurement is copied exactly, one entry per (source record,
measurement): ``MEASURED`` with its integer value and unit, or the
source's own ``NOT_MEASURED`` (run telemetry) / ``UNKNOWN`` (receipts)
state with its exact reason. Nothing is estimated, converted or filled in.

Aggregation. The two families measure different scopes with different
clocks and definitions, so they are never combined. Inside one family a
group sums one measurement over the listed entries only when its unit and
definition are identical and every listed entry is MEASURED and no record
of that family was refused; otherwise the group is ``NOT_ESTABLISHED``
with a reason and no value. A group sum covers retained, listed records
only. Run telemetry is run-wide per step: no entry names a question, plan,
evidence or result, and nothing is apportioned to a result.

Unknown cost. Work that left no record is UNKNOWN, never zero: rolled-back
or failed investigation attempts, receipts deleted with retention-pruned
cases, run attempts whose receipt was never stored, and research-chain work
done outside the offline runner. Neither source measures money. The total
research cost is therefore always ``NOT_ESTABLISHED``.

Integrity. Receipts are checked against their stored row and the exact
source shape: canonical payload, exact key set and nested types (no bool
or float substitution), identity-derived ``receipt_id``, row columns,
coverage and compliance contract, and the source's own measurement
semantics. Runs go through research_run's own ``from_json`` and row
projection (receipt and telemetry SHA-256). A record that fails is listed
as refused with its reason and contributes no measurement. Question/plan/
evidence/result references of a run are not re-verified here: the ledger
reads telemetry only.

Receipt authenticity limit. ``receipt_id`` hashes the receipt identity
only, not its measurements, and receipts carry no content hash. A
well-formed, internally consistent rewrite of a measurement therefore
cannot be detected. The ledger's ``payload_sha256`` fingerprints the
content as read now; it does not authenticate that content's history.
Run telemetry, by contrast, is bound by its stored SHA-256.

This is observation only: no budget, cap, bound, efficiency, return,
priority, salience or usefulness, no paid-source or owner-policy inference,
and no writer. Nothing live calls it: no Kernel, Attention, Analyst, Risk,
Execution, LLM or network.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from trader.cognition import research_run as run_
from trader.observability import investigation as inv

SCHEMA = "research-cost-ledger.v1"
BUILDER_ID = "research-cost-ledger-builder.v1"

MEASURED, NOT_MEASURED, UNKNOWN = "MEASURED", "NOT_MEASURED", "UNKNOWN"
SUMMED, NOT_ESTABLISHED = "SUMMED", "NOT_ESTABLISHED"
READ, NOT_READ, UNAVAILABLE = "READ", "NOT_READ", "UNAVAILABLE"

RECEIPT_FAMILY = inv.RECEIPT_SCHEMA
RUN_FAMILY = run_.TELEMETRY_SCHEMA

# Receipt measurement -> unit. The definition of each is the receipt
# schema's own pinned text (observability/investigation.py UNITS).
RECEIPT_UNITS = {"wall_ns": "ns", "cpu_ns": "ns", "evidence_rows_read": "rows",
                 "ledger_rows_written": "rows", "payload_bytes": "bytes",
                 "llm_calls": "calls", "venue_requests": "requests"}
RUN_MEASURES = (("elapsed_wall_ns", "ns", run_.CLOCK),
                ("rows_read", "rows", run_.ROWS_DEFINITION))

NO_ENTRIES = "no_entries"
UNMEASURED_ENTRIES = "unmeasured_entries_present"
REFUSED_RECORDS = "refused_source_records_present"
GROUP_SCOPE = ("sum over the listed retained entries of one family and "
               "measurement only; not a total research cost")

RECEIPT_NOT_COVERED = tuple(
    (name, "declared_not_covered_by_" + RECEIPT_FAMILY)
    for name in inv.NOT_COVERED) + (
    ("receipts_of_retention_pruned_cases",
     "receipts_deleted_with_pruned_cases"),)
RUN_NOT_COVERED = (
    ("unstored_run_attempts",
     "no_telemetry_for_runs_whose_receipt_was_not_stored"),
    ("research_work_outside_offline_runner",
     "direct chain-step calls, bank filing and prior-research recall "
     "are not metered"),
    ("sqlite_page_and_writer_reads", "excluded_by_rows_read_definition"))

TOTAL_REASONS = ("incommensurable_source_families",
                 "unrecorded_work_cost_unknown",
                 "no_monetary_cost_measured")

SEMANTICS = ("read-only copy of measured research-cost evidence; MEASURED/"
             "NOT_MEASURED/UNKNOWN preserved with exact reasons; families "
             "never combined; total research cost NOT_ESTABLISHED; no "
             "budget, cap, bound, efficiency, return, priority, salience, "
             "usefulness, per-result attribution or live authority")

_RECEIPT_IDENTITY = ("schema_version", "execution_id", "case_id", "stage",
                     "work")
# The exact receipt shape written by observability/investigation.py
# _receipt_body; any missing or extra key is refused.
_RECEIPT_KEYS = frozenset(_RECEIPT_IDENTITY + (
    "receipt_id", "episode_id", "symbol", "family", "protocol_id",
    "source_scan_id", "observed_ms", "outcome", "accounting_scope",
    "coverage", "not_covered", "measurements", "unknown_units",
    "elapsed_ms", "measurement_status", "egress_events", "resource_bound",
    "resource_compliance", "compliance_reason"))
_RECEIPT_STRINGS = ("schema_version", "execution_id", "case_id", "stage",
                    "receipt_id", "episode_id", "symbol", "family",
                    "protocol_id", "source_scan_id", "outcome",
                    "measurement_status")
_WORK_KEYS = frozenset(("source_scan_id", "previous_update_id",
                        "resulting_update_id"))
_STAGES = frozenset(("registration", "assessment", "update"))
# Source-defined measurement semantics (observability/investigation.py
# _Meter.stop): only the two clocks can fail, with these reasons; llm_calls
# and venue_requests are 0 without an egress audit event, else UNKNOWN
# "unclassified_egress"; the ledger-backed counters are always recorded.
_CLOCK_UNITS = frozenset(("wall_ns", "cpu_ns"))
_CLOCK_REASONS = frozenset(("clock_read_failed", "non_monotonic_reading"))
_EGRESS_UNITS = ("llm_calls", "venue_requests")
_EGRESS_REASON = "unclassified_egress"
_COMPLIANCE_REASON = "no_prospectively_frozen_resource_bound"
_RECEIPT_COLUMNS = ("id", "case_id", "stage", "execution_id", "recorded_ms",
                    "payload")


class _Refused(ValueError):
    pass


def canonical(payload) -> str:
    return run_.canonical(payload)


def ledger_sha256(ledger: dict) -> str:
    return hashlib.sha256(canonical(ledger).encode()).hexdigest()


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _str(v) -> bool:
    return isinstance(v, str) and bool(v)


def _entry(family, source_id, measurement, state, value, unit, definition,
           reason, provenance, context):
    return {"family": family, "source_id": source_id,
            "measurement": measurement, "state": state, "value": value,
            "unit": unit, "unit_definition": definition, "reason": reason,
            "provenance": provenance, "context": context}


# ── investigation-resource-receipt.v1 ────────────────────────────────────
def _receipt(row) -> tuple:
    """(payload, provenance) of one stored receipt row, or _Refused.
    Enforces the exact source shape, types (no bool/float substitution)
    and source-defined measurement semantics before anything is used."""
    text = row.get("payload")
    try:
        body = json.loads(text, parse_constant=lambda c: _no_constant())
    except (TypeError, ValueError, RecursionError):
        raise _Refused("payload_not_json")
    try:                  # a non-finite float literal (1e999) is not JSON
        text_ok = isinstance(body, dict) and canonical(body) == text
    except (ValueError, RecursionError):
        raise _Refused("payload_not_canonical")
    if not text_ok:
        raise _Refused("payload_not_canonical")
    if set(body) != _RECEIPT_KEYS:
        raise _Refused("receipt_keys")
    if body["schema_version"] != RECEIPT_FAMILY:
        raise _Refused("schema")
    if (not all(_str(body[k]) for k in _RECEIPT_STRINGS)
            or not _int(body["observed_ms"]) or body["observed_ms"] < 0
            or body["stage"] not in _STAGES):
        raise _Refused("field_types")
    work = body["work"]
    if (not isinstance(work, dict) or set(work) != _WORK_KEYS
            or work["source_scan_id"] != body["source_scan_id"]
            or not _str(work["source_scan_id"])
            or not all(work[k] is None or _str(work[k])
                       for k in ("previous_update_id",
                                 "resulting_update_id"))):
        raise _Refused("work_shape")
    if body["receipt_id"] != inv.digest(
            {k: body[k] for k in _RECEIPT_IDENTITY}):
        raise _Refused("receipt_id_mismatch")
    if (not all(_str(row.get(k)) for k in ("id", "case_id", "stage",
                                            "execution_id"))
            or not _int(row.get("recorded_ms"))
            or row["id"] != body["receipt_id"]
            or row["case_id"] != body["case_id"]
            or row["stage"] != body["stage"]
            or row["execution_id"] != body["execution_id"]
            or row["recorded_ms"] != body["observed_ms"]):
        raise _Refused("row_binding")
    if (body["accounting_scope"] != inv.ACCOUNTING_SCOPE
            or body["coverage"] != inv.COVERAGE
            or body["not_covered"] != list(inv.NOT_COVERED)):
        raise _Refused("coverage_contract")
    if (body["resource_bound"] is not None
            or body["resource_compliance"] != UNKNOWN
            or body["compliance_reason"] != _COMPLIANCE_REASON):
        raise _Refused("compliance_contract")
    values, unknown = body["measurements"], body["unknown_units"]
    if not isinstance(values, dict) or set(values) != set(inv.UNITS):
        raise _Refused("measurement_keys")
    if (not isinstance(unknown, dict) or not set(unknown) <= set(values)
            or not all(_str(r) for r in unknown.values())):
        raise _Refused("unknown_units")
    for k, v in values.items():
        if not ((_int(v) and v >= 0 and k not in unknown)
                or (v is None and k in unknown)):
            raise _Refused("measurement_shape")
    egress = body["egress_events"]
    if (not isinstance(egress, dict)
            or not all(e in inv._EGRESS and _int(n) and n > 0
                       for e, n in egress.items())):
        raise _Refused("egress_shape")
    expected_egress = ({k: _EGRESS_REASON for k in _EGRESS_UNITS}
                       if egress else {})
    if ({k: r for k, r in unknown.items() if k in _EGRESS_UNITS}
            != expected_egress
            or any(values[k] not in (0, None) for k in _EGRESS_UNITS)
            or not all(k in _CLOCK_UNITS and r in _CLOCK_REASONS
                       for k, r in unknown.items()
                       if k not in _EGRESS_UNITS)):
        raise _Refused("measurement_semantics")
    if body["measurement_status"] != (UNKNOWN if unknown else MEASURED):
        raise _Refused("measurement_status")
    wall, elapsed = values["wall_ns"], body["elapsed_ms"]
    try:
        derived = None if wall is None else wall / 1e6
    except OverflowError:
        raise _Refused("elapsed_ms_mismatch")
    if not ((wall is None and elapsed is None)
            or (wall is not None and type(elapsed) is float
                and elapsed == derived)):
        raise _Refused("elapsed_ms_mismatch")
    provenance = {"table": "resource_receipts", "receipt_id": row["id"],
                  "payload_sha256": hashlib.sha256(text.encode()).hexdigest()}
    return body, provenance


def _no_constant():
    raise ValueError("non_finite")


def _receipt_entries(body, provenance):
    context = {"case_id": body["case_id"], "stage": body["stage"],
               "execution_id": body["execution_id"],
               "observed_ms": body["observed_ms"],
               "outcome": body.get("outcome")}
    out = []
    for k in inv.UNITS:                       # pinned schema order
        v = body["measurements"][k]
        state, reason = ((MEASURED, None) if v is not None
                         else (UNKNOWN, body["unknown_units"][k]))
        out.append(_entry(RECEIPT_FAMILY, body["receipt_id"], k, state, v,
                          RECEIPT_UNITS[k], inv.UNITS[k], reason,
                          provenance, context))
    return out


def _receipt_family(rows):
    entries, refused, keyed = [], [], []
    ids = Counter(r.get("id") for r in rows)
    for r in rows:
        if ids[r.get("id")] > 1:
            refused.append({"source_id": r.get("id"),
                            "reason": "receipt_invalid:duplicate_source_id"})
            continue
        try:
            body, prov = _receipt(r)
        except _Refused as e:
            refused.append({"source_id": r.get("id"),
                            "reason": f"receipt_invalid:{e}"})
            continue
        keyed.append(((body["observed_ms"], body["receipt_id"]),
                      _receipt_entries(body, prov)))
    for _, es in sorted(keyed, key=lambda x: x[0]):
        entries.extend(es)
    return entries, refused


# ── research-run-telemetry.v1 ────────────────────────────────────────────
def _run(row) -> tuple:
    """(receipt, telemetry) through research_run's own contract, or
    _Refused. References are not re-verified (telemetry only)."""
    try:
        rec, tel = run_.from_json(row.get("canonical_json"),
                                  row.get("telemetry_json"))
        proj = run_.row_for(rec, tel)
        if canonical(proj) != canonical({k: row.get(k) for k in proj}):
            raise run_.ResearchRunError("row_projection")
    except run_.ResearchRunError as e:
        raise _Refused(str(e))
    except (ValueError, OverflowError, RecursionError, TypeError) as e:
        # research_run's parser lets these escape (e.g. a 1e999 literal or
        # deep nesting); here they refuse the one row, not the ledger
        raise _Refused("malformed_source:" + type(e).__name__)
    return rec, tel


def _run_entries(row, rec, tel):
    provenance = {"table": "research_runs", "run_id": rec["run_id"],
                  "canonical_sha256": row["canonical_sha256"],
                  "telemetry_sha256": row["telemetry_sha256"],
                  "run_recorded_at_ms": rec["inputs"]["recorded_at_ms"]}
    out = []
    for t, s in zip(tel["steps"], rec["steps"]):
        context = {"step": s["step"], "step_status": s["status"]}
        for name, unit, definition in RUN_MEASURES:
            m = t[name]
            out.append(_entry(RUN_FAMILY, rec["run_id"], name, m["status"],
                              m["value"], unit, definition, m["reason"],
                              provenance, context))
    return out


def _run_family(rows):
    entries, refused, keyed = [], [], []
    ids = Counter(r.get("run_id") for r in rows)
    for r in rows:
        if ids[r.get("run_id")] > 1:
            refused.append({"source_id": r.get("run_id"),
                            "reason": "run_invalid:duplicate_source_id"})
            continue
        try:
            rec, tel = _run(r)
        except _Refused as e:
            refused.append({"source_id": r.get("run_id"),
                            "reason": f"run_invalid:{e}"})
            continue
        keyed.append(((rec["inputs"]["recorded_at_ms"], rec["run_id"]),
                      _run_entries(r, rec, tel)))
    for _, es in sorted(keyed, key=lambda x: x[0]):
        entries.extend(es)
    return entries, refused


# ── assembly ─────────────────────────────────────────────────────────────
def _groups(family, entries, refused, measures):
    out = []
    for name, unit, definition in measures:
        es = [e for e in entries if e["measurement"] == name]
        counts = {s: sum(e["state"] == s for e in es)
                  for s in (MEASURED, NOT_MEASURED, UNKNOWN)}
        if not es:
            reason = NO_ENTRIES
        elif refused:
            reason = REFUSED_RECORDS
        elif counts[MEASURED] != len(es):
            reason = UNMEASURED_ENTRIES
        else:
            reason = None
        out.append({"family": family, "measurement": name, "unit": unit,
                    "unit_definition": definition, "entries": len(es),
                    "measured": counts[MEASURED],
                    "not_measured": counts[NOT_MEASURED],
                    "unknown": counts[UNKNOWN],
                    "state": SUMMED if reason is None else NOT_ESTABLISHED,
                    "value": (sum(e["value"] for e in es)
                              if reason is None else None),
                    "reason": reason, "scope": GROUP_SCOPE})
    return out


def _family(family, rows, reader, measures, not_covered, source_semantics,
            source_state=None, source_reason=None):
    if rows is None:
        state = source_state or NOT_READ
        entries, refused = [], []
        reason = source_reason or "source_not_supplied"
    else:
        state, reason = READ, None
        entries, refused = reader(rows)
    refused = sorted(refused, key=lambda r: (str(r["source_id"]),
                                             r["reason"]))
    return {"family": family, "source_state": state, "source_reason": reason,
            "source_semantics": source_semantics,
            "not_covered": [{"name": n, "state": UNKNOWN, "reason": r}
                            for n, r in not_covered],
            "entries": entries, "refused_sources": refused,
            "groups": (_groups(family, entries, refused, measures)
                       if state == READ else [])}


def build(receipt_rows=None, run_rows=None, *, receipt_source=None,
          run_source=None) -> dict:
    """Pure. ``receipt_rows``: stored ``resource_receipts`` rows (dicts with
    id, case_id, stage, execution_id, recorded_ms, payload); ``run_rows``:
    ``Journal.research_runs()`` rows. ``None`` means the source was not
    read; ``*_source`` optionally gives (state, reason) for that case."""
    receipts = _family(
        RECEIPT_FAMILY, receipt_rows, _receipt_family,
        [(k, RECEIPT_UNITS[k], inv.UNITS[k]) for k in inv.UNITS],
        RECEIPT_NOT_COVERED, inv.ACCOUNTING_SCOPE + ":" + inv.COVERAGE,
        *(receipt_source or (None, None)))
    runs = _family(RUN_FAMILY, run_rows, _run_family, RUN_MEASURES,
                   RUN_NOT_COVERED, run_.TELEMETRY_SEMANTICS,
                   *(run_source or (None, None)))
    reasons = list(TOTAL_REASONS) + [
        f"source_{f['source_state'].lower()}:{f['family']}"
        for f in (receipts, runs) if f["source_state"] != READ]
    return {"schema": SCHEMA, "builder_id": BUILDER_ID,
            "semantics": SEMANTICS,
            "total_research_cost": {"state": NOT_ESTABLISHED, "value": None,
                                    "reasons": reasons},
            "families": [receipts, runs]}


def run_entries(journal, run_id: str) -> tuple:
    """(entries, refused sources) of the research-run-telemetry.v1 family
    for one stored run only: the same per-row contract and entry shape as
    the ledger's run family, reading only that run's research_runs rows.
    No group, sum or total is formed here. Read-only."""
    entries, refused = _run_family(journal.research_runs(run_id=run_id))
    return entries, sorted(refused, key=lambda r: (str(r["source_id"]),
                                                   r["reason"]))


def read_receipt_rows(path) -> list:
    """Stored receipt rows, read-only, in append order."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=.1)) as db:
        cur = db.execute(f"SELECT {','.join(_RECEIPT_COLUMNS)} "
                         "FROM resource_receipts ORDER BY rowid")
        return [dict(zip(_RECEIPT_COLUMNS, r)) for r in cur]


def from_sources(*, journal=None, investigation_path=None) -> dict:
    """Read-only ledger from the stores. An unreadable receipt store or
    journal is UNAVAILABLE with the exception type, never treated as
    empty; the other family is still built from its own evidence."""
    receipt_rows, receipt_source = None, None
    if investigation_path is not None:
        try:
            receipt_rows = read_receipt_rows(investigation_path)
        except sqlite3.Error as e:
            receipt_source = (UNAVAILABLE, "source_unreadable:"
                              + type(e).__name__)
    run_rows, run_source = None, None
    if journal is not None:
        try:
            run_rows = journal.research_runs()
        except sqlite3.Error as e:
            run_source = (UNAVAILABLE, "source_unreadable:"
                          + type(e).__name__)
    return build(receipt_rows, run_rows, receipt_source=receipt_source,
                 run_source=run_source)
