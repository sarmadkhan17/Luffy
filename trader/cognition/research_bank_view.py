"""Research Bank read model — research-bank-view.v1.

A deterministic, read-only, ``context_only`` owner view of ONE filed
research-bank-object.v1, requested by its exact ``bank_object_id`` or by the
exact ``run_id`` of a run that filed exactly one bank object. It assembles
existing verified records only and writes nothing:

- ``bank_object``: the stored object verbatim, verified by
  `research_bank.verify_row` (its own contract, the whole linked
  run/result/evidence/plan/question chain, byte-equal rebuild and row
  projection). Its persisted ``next_questions`` field
  (NOT_AVAILABLE / no_next_question_generator) is shown exactly as stored;
- ``chain``: the verified question, plan and result records in full, the
  evidence record's identity only (its routed source references are the
  bank object's ``sources``; values are not copied), and the run receipt
  identity with its telemetry hash. Every ID and canonical SHA-256 is
  recomputed from the verified record and must equal the bank link;
- ``linked_next_questions``: research-next-question.v1 records stored for
  this exact bank object, each verified by `research_next_question.load`
  (re-derived from the re-verified bank object). They are external linked
  context filed after the bank object, not part of it. Rows commit
  individually, so no completeness is asserted;
- ``cost``: the requested run's research-run-telemetry.v1 entries only
  (MEASURED / NOT_MEASURED with exact reasons), through the cost ledger's
  own per-run contract (`research_cost.run_entries`). No sum is formed and
  total research cost stays NOT_ESTABLISHED;
- ``prior_research``: `research_recall.recall` for the bank object's own
  question with the caller's explicit ``max_prior_objects``, verbatim and
  context_only.

Any source that fails its contract, or any binding between sections that
is not exact, fails the whole view closed (ResearchBankViewError); nothing
is partially shown.

There is no claim, support/refute classification, falsifier predicate,
WorldModel evidence, novelty, similarity, suppression, cooldown, no-repeat,
score, rank, priority, salience, usefulness, budget or asset attribution
here, and nothing live calls it: no Kernel, Attention, Analyst, Risk,
Execution, LLM or network. Bounds apply to returned prior objects only; the
underlying verification reads are not a bound on database work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import threading
from pathlib import Path

from trader.cognition import research_bank as rb
from trader.cognition import research_cost as rcost
from trader.cognition import research_next_question as nq
from trader.cognition import research_recall as rc
from trader.core.journal import Journal

SCHEMA = "research-bank-view.v1"
BUILDER_ID = "research-bank-view-builder.v1"
AUTHORITY = "context_only"

BY_BANK_OBJECT = "bank_object_id"
BY_RUN = "run_id"

LINKED_RELATION = "external_linked_context_not_part_of_bank_object"
NOT_ASSERTED = "NOT_ASSERTED"
NON_ATOMIC_FILING = "next_question_rows_commit_individually"
NEXT_QUESTION_ORDER = "source_index_asc_then_next_question_id_asc"
EVIDENCE_VALUES_NOT_COPIED = "routed_source_references_are_bank_sources"
COST_SCOPE = "requested_run_only"
VIEW_SCOPE_ONE_RUN = "view_scope_is_one_run"

SEMANTICS = ("read-only context_only owner view of one verified Research "
             "Bank object, its verified chain, separately linked "
             "next-question gap records, the requested run's measured "
             "telemetry and context-only prior-research recall; no writes, "
             "claim, support/refute classification, falsifier predicate, "
             "WorldModel evidence, novelty, suppression, cooldown, "
             "no-repeat, score, rank, priority, salience, usefulness, "
             "budget, total cost or live authority")


class ResearchBankViewError(ValueError):
    """The request is malformed, or a source failed verification."""


def _fail(code):
    raise ResearchBankViewError(code)


def canonical(payload) -> str:
    return rb.canonical(payload)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _src(step, fn, *a, **k):
    """Call a source contract; any failure fails the view closed."""
    try:
        return fn(*a, **k)
    except ResearchBankViewError:
        raise
    except (ValueError, TypeError, KeyError, sqlite3.Error) as e:
        raise ResearchBankViewError(
            f"source_invalid:{step}:{type(e).__name__}:{e}") from e


# ── request ──────────────────────────────────────────────────────────────
def _request(bank_object_id, run_id, max_prior_objects) -> dict:
    if (bank_object_id is None) == (run_id is None):
        _fail("request:exactly_one_of_bank_object_id_or_run_id")
    key = bank_object_id if run_id is None else run_id
    if not isinstance(key, str) or not key:
        _fail("request:identifier")
    if (type(max_prior_objects) is not int
            or not 1 <= max_prior_objects <= rc.MAX_OBJECTS):
        _fail("request:max_prior_objects")
    return {"by": BY_BANK_OBJECT if run_id is None else BY_RUN, "id": key,
            "max_prior_objects": max_prior_objects}


def _bank_row(journal, req) -> dict:
    if req["by"] == BY_BANK_OBJECT:
        row = _src("bank_object", journal.research_bank_object, req["id"])
        if row is None:
            _fail("source_missing:bank_object")
        return row
    rows = _src("bank_object", journal.research_bank_objects,
                run_id=req["id"])
    if len(rows) != 1:
        _fail(f"run_bank_objects_not_exactly_one:{len(rows)}")
    return rows[0]


# ── sections ─────────────────────────────────────────────────────────────
def _link(rec, id_key) -> dict:
    return {"schema": rec["schema"], id_key: rec[id_key],
            "canonical_sha256": _sha(canonical(rec))}


def _chain(bank, chain) -> dict:
    """Verified chain records, each re-bound to the bank link by exact ID
    and recomputed canonical SHA-256."""
    lk = bank["links"]
    q, plan, ev, res = (chain["question"], chain["plan"], chain["evidence"],
                        chain["result"])
    receipt, tel = chain["receipt"], chain["telemetry"]
    got = {"question": _link(q, "question_id"),
           "plan": _link(plan, "plan_id"),
           "evidence": _link(ev, "evidence_id"),
           "result": _link(res, "result_id"),
           "run": {**_link(receipt, "run_id"),
                   "telemetry_schema": tel["schema"],
                   "telemetry_sha256": _sha(canonical(tel))}}
    for step, link in got.items():
        if canonical(link) != canonical(lk[step]):
            _fail(f"binding_mismatch:chain:{step}")
    return {"links": got, "question": q, "plan": plan, "result": res,
            "evidence": {**got["evidence"], "values": NOT_ASSERTED,
                         "values_reason": EVIDENCE_VALUES_NOT_COPIED}}


def _next_questions(journal, bank, bank_sha) -> dict:
    bid = bank["bank_object_id"]
    recs = _src("next_questions", nq.load, journal, bank_object_id=bid)
    out = []
    for rec in recs:
        src = rec["source"]
        if (src["bank_object"]["bank_object_id"] != bid
                or src["bank_object"]["canonical_sha256"] != bank_sha
                or src["result"]["result_id"]
                != bank["links"]["result"]["result_id"]
                or src["result"]["canonical_sha256"]
                != bank["links"]["result"]["canonical_sha256"]):
            _fail("binding_mismatch:next_question")
        out.append({"next_question_id": rec["next_question_id"],
                    "canonical_sha256": _sha(canonical(rec)),
                    "record": rec})
    out.sort(key=lambda x: (x["record"]["source"]["index"],
                            x["next_question_id"]))
    return {"relation": LINKED_RELATION, "bank_object_id": bid,
            "order": NEXT_QUESTION_ORDER, "stored_count": len(out),
            "completeness": NOT_ASSERTED,
            "completeness_reason": NON_ATOMIC_FILING, "records": out}


def _cost(journal, bank) -> dict:
    run = bank["links"]["run"]
    entries, refused = _src("cost", rcost.run_entries, journal,
                            run["run_id"])
    if refused or not entries:
        _fail("source_invalid:cost:run_telemetry")
    for e in entries:
        p = e["provenance"]
        if (e["family"] != rcost.RUN_FAMILY
                or e["source_id"] != run["run_id"]
                or p["run_id"] != run["run_id"]
                or p["canonical_sha256"] != run["canonical_sha256"]
                or p["telemetry_sha256"] != run["telemetry_sha256"]):
            _fail("binding_mismatch:cost")
    return {"family": rcost.RUN_FAMILY, "scope": COST_SCOPE,
            "run_id": run["run_id"],
            "telemetry_sha256": run["telemetry_sha256"],
            "entries": entries,
            "not_covered": [{"name": n, "state": rcost.UNKNOWN, "reason": r}
                            for n, r in rcost.RUN_NOT_COVERED],
            "total_research_cost": {
                "state": rcost.NOT_ESTABLISHED, "value": None,
                "reasons": list(rcost.TOTAL_REASONS) + [VIEW_SCOPE_ONE_RUN]}}


def _prior(journal, bank, max_prior_objects) -> dict:
    ql = bank["links"]["question"]
    out = _src("prior_research", rc.recall, journal, ql["question_id"],
               max_objects=max_prior_objects)
    q = out["question"]
    if (out["authority"] != rc.AUTHORITY
            or q["question_id"] != ql["question_id"]
            or q["canonical_sha256"] != ql["canonical_sha256"]
            or out["bound"]["max_objects"] != max_prior_objects
            or len(out["prior"]) > max_prior_objects
            or any(p["bank_object_id"] == bank["bank_object_id"]
                   for p in out["prior"])):
        _fail("binding_mismatch:prior_research")
    return out


# ── assembly ─────────────────────────────────────────────────────────────
def _identity(view) -> dict:
    return {"schema": view["schema"], "builder_id": view["builder_id"],
            "request": view["request"],
            "bank_object": {"bank_object_id":
                            view["bank_object"]["bank_object_id"],
                            "canonical_sha256":
                            view["bank_object_canonical_sha256"]},
            "links": view["chain"]["links"],
            "linked_next_questions": [
                [x["next_question_id"], x["canonical_sha256"]]
                for x in view["linked_next_questions"]["records"]],
            "cost_sha256": _sha(canonical(view["cost"])),
            "prior_research_sha256": _sha(canonical(view["prior_research"]))}


def view_id(view: dict) -> str:
    return _sha(canonical(_identity(view)))


def view_sha256(view: dict) -> str:
    """SHA-256 of the canonical view without its own ``view_sha256``."""
    return _sha(canonical({k: v for k, v in view.items()
                           if k != "view_sha256"}))


def build(journal, *, bank_object_id: str | None = None,
          run_id: str | None = None, max_prior_objects: int) -> dict:
    """The research-bank-view.v1 of one filed bank object. Read-only;
    raises ResearchBankViewError on a malformed request or any source that
    fails verification."""
    req = _request(bank_object_id, run_id, max_prior_objects)
    row = _bank_row(journal, req)
    bank, chain = _src("bank_object", rb.verify_row, journal, row)
    if req["by"] == BY_RUN and bank["links"]["run"]["run_id"] != req["id"]:
        _fail("binding_mismatch:run")
    bank_sha = _sha(canonical(bank))
    if bank_sha != row["canonical_sha256"]:
        _fail("binding_mismatch:bank_object")
    view = {"schema": SCHEMA, "builder_id": BUILDER_ID,
            "authority": AUTHORITY, "read_only": True, "request": req,
            "bank_object": bank, "bank_object_canonical_sha256": bank_sha,
            "chain": _chain(bank, chain),
            "linked_next_questions": _next_questions(journal, bank,
                                                     bank_sha),
            "cost": _cost(journal, bank),
            "prior_research": _prior(journal, bank, max_prior_objects),
            "semantics": SEMANTICS}
    view["view_id"] = view_id(view)
    view = json.loads(canonical(view))
    view["view_sha256"] = view_sha256(view)
    return view


# ── offline owner access ─────────────────────────────────────────────────
def open_readonly(db_path) -> Journal:
    """A Journal over an existing database through a read-only SQLite
    connection (``mode=ro``).

    Journal initialization (``Journal.__init__``) is bypassed because it
    runs schema DDL and a backfill UPDATE, so this performs no schema
    creation, no backfill and no mutation of database or WAL content; any
    write attempt through this instance fails with
    sqlite3.OperationalError. SQLite may still create or touch the
    ``-shm`` sidecar and may create an empty ``-wal`` sidecar, so zero
    filesystem activity is NOT guaranteed. Single-thread only: the one
    read-only connection is bound to the calling thread."""
    path = Path(db_path)
    if not path.is_file():
        _fail("db_missing")
    j = Journal.__new__(Journal)
    j.db_path = path
    j._local = threading.local()
    j._write_lock = threading.Lock()
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                           timeout=30)
    conn.row_factory = sqlite3.Row
    j._local.conn = conn
    return j


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m trader.cognition.research_bank_view",
        description="Read-only research-bank-view.v1 of one filed Research "
                    "Bank object, as canonical JSON.")
    ap.add_argument("--db", required=True,
                    help="explicit path to an existing journal database")
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--bank-object-id")
    who.add_argument("--run-id")
    ap.add_argument("--max-prior-objects", type=int, required=True)
    a = ap.parse_args(argv)
    try:
        j = open_readonly(a.db)
        view = build(j, bank_object_id=a.bank_object_id, run_id=a.run_id,
                     max_prior_objects=a.max_prior_objects)
    except (ResearchBankViewError, sqlite3.Error) as e:
        print(canonical({"schema": SCHEMA, "error": str(e)}),
              file=sys.stderr)
        return 2
    print(canonical(view))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
