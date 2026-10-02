"""Research shadow owner report — research-shadow-report.v1 (read-only).

One owner-facing, context_only view of ONE research shadow invocation
across BOTH research families. The invocation is named explicitly
(``invocation_id``); there is no default "latest", "best" or "most
important" invocation or object, and nothing is scored, ranked, weighted or
called useful.

Read-only by construction: the shadow database is opened ``mode=ro`` and the
source journal is attached ``mode=ro`` behind the store's read-only views
and authorizer (research_shadow_store); the health view is unbounded here,
so every family verification reads the source's current health history.
Nothing is written to either database (SQLite may still touch ``-shm`` /
``-wal`` sidecars).

Contents, each verified by its own existing contract — nothing else:

- ``invocation``: the stored research-shadow-invocation.v1 receipt and its
  SHA-256, verified (digest, canonical form, exact contract);
- ``families``: for strategy_decay then strategy_health_unreadable, in
  registry order:

  - ``sources``: the receipt's sources dispatched to the family, verbatim
    (source event id, question id, run id, bank object ids);
  - ``bank_objects``: every bank object the family's run filed in this
    invocation (the filing's inserted then duplicate IDs, as recorded —
    filing order, not a ranking). Each is re-verified with the family's
    own ``verify_row`` (whole linked Q/P/E/R/Run chain against the live
    stores); a verified object shows its links (Q/P/E/R/Run identity and
    SHA-256 provenance) verbatim, scope, question source event, the
    family's own structural result fields verbatim, and its
    research-registration.v1 receipt state (``VERIFIED`` with the time, or
    ``MISSING``/``INVALID`` with the reason — rows filed before receipts
    existed are never backfilled). An object that fails verification
    shows only its id and the reason (``UNVERIFIED``);
  - ``recall`` (only when ``recall_max_objects`` is given explicitly): the
    family's existing context_only prior-research recall for every ROUTED
    source's question, or ``REFUSED`` with the recall's own reason (for
    example a legacy question without a registration receipt);
- ``cost``: research-cost-ledger.v1 over the shadow database with the
  unreadable family explicitly enabled; families stay separate and
  ``total_research_cost`` stays NOT_ESTABLISHED, exactly as the ledger
  builds it.

No score, rank, salience, usefulness, priority or diagnosis. No network,
LLM, Attention, Kernel, Risk, Execution or trading authority.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from trader.cognition import research_bank as rb
from trader.cognition import research_cost as rc
from trader.cognition import research_families as rf
from trader.cognition import research_recall as rrc
from trader.cognition import research_shadow as rs
from trader.cognition import research_shadow_contract as sc
from trader.cognition import research_shadow_store as st
from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_recall as urc

SCHEMA = "research-shadow-report.v1"
BUILDER_ID = "research-shadow-report-builder.v1"
AUTHORITY = "context_only"
OBJECT_ORDER = "receipt_filing_order_inserted_then_duplicate"
VERIFIED, UNVERIFIED = "VERIFIED", "UNVERIFIED"
MISSING, INVALID = "MISSING", "INVALID"

SEMANTICS = ("read-only context_only owner view of one explicitly named "
             "research shadow invocation across both research families; "
             "every object is shown only as verified by its own contract; "
             "objects appear in the receipt's filing order, which is not a "
             "ranking; no score, rank, salience, usefulness, priority or "
             "diagnosis; cost is research-cost-ledger.v1 with families "
             "separate and total research cost not established; no network, "
             "LLM, Attention, Kernel, Risk, Execution or trading authority")


class ShadowReportError(ValueError):
    """The request is malformed or the invocation receipt cannot be read."""


def _family_api(name):
    if name == rf.STRATEGY_DECAY:
        return {"bank": rb, "bank_error": rb.ResearchBankError,
                "rows": lambda j, rid: j.research_bank_objects(run_id=rid),
                "recall": rrc.recall, "recall_error": rrc.RecallError,
                "reg_at": rrc._registered_at,
                "result": lambda rec: {"result_status": rec["result_status"],
                                       "result_reason": rec["result_reason"]}}
    return {"bank": ub, "bank_error": ub.UnreadableBankError,
            "rows": lambda j, rid: j.research_unreadable_bank_objects(
                run_id=rid),
            "recall": urc.recall, "recall_error": urc.UnreadableRecallError,
            "reg_at": urc._registered_at,
            "result": lambda rec: rec["result"]}


def _registration(j, api, record_type, record_id, sha) -> dict:
    reg = j.research_registration(record_type, record_id)
    if reg is None:
        return {"state": MISSING, "registered_at_ms": None,
                "reason": "registration_missing"}
    try:
        ms = api["reg_at"](reg, record_type, record_id, sha)
    except api["recall_error"] as e:
        return {"state": INVALID, "registered_at_ms": None,
                "reason": str(e)}
    return {"state": VERIFIED, "registered_at_ms": ms, "reason": None}


def _bank_object(j, api, run_id, bank_object_id) -> dict:
    rows = [r for r in api["rows"](j, run_id)
            if r["bank_object_id"] == bank_object_id]
    if len(rows) != 1:
        return {"bank_object_id": bank_object_id, "state": UNVERIFIED,
                "reason": "bank_object_missing"}
    row = rows[0]
    try:
        rec, chain = api["bank"].verify_row(j, row)
    except api["bank_error"] as e:
        return {"bank_object_id": bank_object_id, "state": UNVERIFIED,
                "reason": str(e)}
    lk = rec["links"]
    return {"bank_object_id": bank_object_id, "state": VERIFIED,
            "bank_schema": rec["schema"],
            "bank_canonical_sha256": row["canonical_sha256"],
            "run_id": lk["run"]["run_id"], "links": lk,
            "scope": rec["scope"],
            "question_source_event_id":
                chain["question"]["source"]["event_id"],
            "structural_result": api["result"](rec),
            "bank_registration": _registration(
                j, api, rec["schema"], bank_object_id,
                row["canonical_sha256"]),
            "question_registration": _registration(
                j, api, lk["question"]["schema"],
                lk["question"]["question_id"],
                lk["question"]["canonical_sha256"])}


def _recall(j, api, question_id, max_objects) -> dict:
    try:
        out = api["recall"](j, question_id, max_objects)
    except api["recall_error"] as e:
        return {"question_id": question_id, "state": "REFUSED",
                "reason": str(e), "recall": None}
    return {"question_id": question_id, "state": "RECALLED",
            "reason": None, "recall": out}


def _check_request(invocation_id, recall_max_objects):
    if not sc._hex64(invocation_id):
        raise ShadowReportError("invalid_invocation_id")
    if recall_max_objects is not None and (
            type(recall_max_objects) is not int
            or not 1 <= recall_max_objects <= min(rrc.MAX_OBJECTS,
                                                  urc.MAX_OBJECTS)):
        raise ShadowReportError("invalid_recall_max_objects")


def build(*, shadow_db, source_db, invocation_id: str,
          recall_max_objects: int | None) -> dict:
    """The research-shadow-report.v1 of one invocation. Read-only.
    ``recall_max_objects`` None means recall was not requested."""
    _check_request(invocation_id, recall_max_objects)
    j = st.open_shadow(shadow_db, source_db, readonly=True)
    try:
        j.set_bound(st.MAX_EVENT_ID, st.MAX_EVENT_ID)
        try:
            stored = rs.load_receipt(j._conn(), invocation_id)
        except (ValueError, sc.ContractError) as e:
            raise ShadowReportError(f"receipt_unverifiable:{e}") from e
        if stored is None:
            raise ShadowReportError("invocation_missing")
        receipt, sha = stored
        families = []
        for name in rf.FAMILY_NAMES:
            api = _family_api(name)
            srcs = [{k: s[k] for k in ("source_event_id", "question_id",
                                       "run_id", "bank_object_ids")}
                    for s in receipt["sources"]
                    if s["dispatch"]["family"] == name]
            objs = []
            for r in receipt["family_runs"]:
                if r["family"] != name:
                    continue
                f = r["bank_filing"]
                for bid in dict.fromkeys(f["inserted"] + f["duplicate"]):
                    objs.append(_bank_object(j, api, r["run_id"], bid))
            fam = {"family": name, "sources": srcs,
                   "object_order": OBJECT_ORDER, "bank_objects": objs,
                   "recall": None}
            if recall_max_objects is not None:
                fam["recall"] = {
                    "max_objects": recall_max_objects,
                    "questions": [_recall(j, api, s["question_id"],
                                          recall_max_objects)
                                  for s in srcs]}
            families.append(fam)
        cost = rc.from_sources(journal=j, include_unreadable_runs=True)
        report = {"schema": SCHEMA, "builder_id": BUILDER_ID,
                  "authority": AUTHORITY, "read_only": True,
                  "request": {"invocation_id": invocation_id,
                              "recall_max_objects": recall_max_objects},
                  "invocation": {"receipt": receipt,
                                 "canonical_sha256": sha},
                  "families": families, "cost": cost,
                  "semantics": SEMANTICS}
        return json.loads(sc.canonical(report))
    finally:
        j.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m trader.cognition.research_shadow_report",
        description="Read-only research-shadow-report.v1 of one research "
                    "shadow invocation, as canonical JSON.")
    ap.add_argument("--shadow-db", required=True)
    ap.add_argument("--source-db", required=True)
    ap.add_argument("--invocation-id", required=True)
    ap.add_argument("--recall-max-objects", type=int, default=None,
                    help="request prior-research recall with this explicit "
                         "bound; omitted = recall not requested")
    a = ap.parse_args(argv)
    try:
        rep = build(shadow_db=a.shadow_db, source_db=a.source_db,
                    invocation_id=a.invocation_id,
                    recall_max_objects=a.recall_max_objects)
    except (ShadowReportError, st.ShadowStoreError, sqlite3.Error) as e:
        print(sc.canonical({"schema": SCHEMA, "error": str(e)}),
              file=sys.stderr)
        return 2
    print(sc.canonical(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
