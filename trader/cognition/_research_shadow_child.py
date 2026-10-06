"""Research shadow child — the isolated worker process of the harness.

Started only by ``research_shadow.py`` as
``python -s -m trader.cognition._research_shadow_child`` with a sanitized
allow-list environment (``research_shadow_contract.CHILD_ENV_KEYS``) and one
research-shadow-child-request.v1 JSON on stdin. It prints exactly one
research-shadow-child-result.v1 line on stdout.

Boundary, enforced here and not only by the parent:

- environment: any variable outside the allow-list (credentials, API keys,
  ``.env`` values, deployment secrets) refuses the run before any database
  is opened; only variable NAMES are ever reported, never values;
- imports: before anything else an import guard is installed at the front
  of ``sys.meta_path``. Repository modules are allow-listed exactly
  (``TRADER_ALLOWED``: the families' research closure and this harness);
  every other ``trader`` module — Kernel, execution, Risk, Orchestrator,
  Attention, brain/LLM, venue feeds — is refused, as are the stdlib and
  third-party network and process modules (``FORBIDDEN``). The one allowed
  ``trader.engine`` member is ``trader.engine.protective``:
  ``strategy.signal_occurrence`` (imported by the health-observation
  contract the families read) takes its pure ``venue_key`` symbol
  normaliser from it; it opens no connection and holds no client. Before
  the work transaction commits, ``sys.modules`` is checked again and any
  forbidden module aborts the transaction;
- databases: only through ``research_shadow_store.open_shadow`` — the
  shadow store read-write, the source journal attached ``mode=ro`` behind
  read-only views and an authorizer.

Work (``work``):

1. Read the shadow cursor; it must equal the start record's
   ``cursor_before``, and (lineage) the source event it stands on must
   still exist in this source with the raw-record SHA-256 stored beside
   the cursor. The parent separately binds the store to one source path.
2. Observe the source through the read-only attach: the highest
   strategy-health sweep record id ``H``, and the first ``max_sources``
   strategy-health spec observations with ``cursor < id <= H`` in event-id
   order. Bound the health view to those (spec rows <= the last selected id,
   sweep records and unattributable rows <= ``H``) and commit the
   research-shadow-source-snapshot.v1 in its own small transaction.
   Spec observations after ``H`` belong to a sweep whose record is not yet
   written; they are left for a later invocation, never skipped.
3. Dispatch every selected source (research_families). Any AMBIGUOUS
   source: commit a REFUSED completion, run no family, leave the cursor.
4. Otherwise, in ONE transaction: for each family with a ROUTED source, in
   registry order, run the family's existing runner
   (``research_run.run`` / ``research_unreadable_run.run``) with
   ``run_key = <harness>:<invocation>:<family>`` and the invocation's
   ``recorded_at_ms``, then its existing bank filing for exactly that run
   (``research_bank.record_run`` /
   ``research_unreadable_bank.record_from_run``); write the completion
   record and move the cursor to the last selected source. A family run
   that is not newly inserted, any step that is not COMPLETED, or a ROUTED
   question the run's question step did not reach aborts the transaction.

A kill or crash anywhere in step 4 leaves no committed research artifact,
completion or cursor move from this invocation (SQLite rollback). No
network, LLM, Attention, Kernel, Orchestrator, Risk, Execution, order or
account authority.
"""
from __future__ import annotations

import json
import os
import sys
import time

#: the ONLY repository modules the child may import: exactly the research
#: closure of the two families, their stores and this harness. Every other
#: ``trader`` module — Kernel, engine execution/risk/orchestration, Attention
#: (``trader.cognition.attention``, ``trader.observability.*``), brain/LLM,
#: data/venue feeds, dashboard, api — is refused.
TRADER_ALLOWED = frozenset({
    "trader", "trader.cognition", "trader.core", "trader.core.journal",
    "trader.core.reason_codes", "trader.core.types", "trader.core.journal_evidence", "trader.strategy",
    # Bounded retained-evidence codec required by journal_evidence; no I/O.
    "trader.core.evidence_zlib",
    "trader.strategy.health_observation", "trader.strategy.signal_occurrence",
    "trader.strategy.signal_occurrence_observation",
    # pure ``venue_key`` symbol normaliser, imported by signal_occurrence
    "trader.engine", "trader.engine.protective",
    "trader.cognition._research_shadow_child",
    "trader.cognition.research_families",
    "trader.cognition.research_shadow_contract",
    "trader.cognition.research_shadow_store",
    "trader.cognition.research_question", "trader.cognition.research_plan",
    "trader.cognition.research_sources", "trader.cognition.research_evidence",
    "trader.cognition.research_result", "trader.cognition.research_run",
    "trader.cognition.research_bank",
    "trader.cognition.research_unreadable_question",
    "trader.cognition.research_unreadable_plan",
    "trader.cognition.research_unreadable_evidence",
    "trader.cognition.research_unreadable_result",
    "trader.cognition.research_unreadable_run",
    "trader.cognition.research_unreadable_bank",
})

#: repository authority modules named explicitly (all are outside
#: TRADER_ALLOWED; listed so tests import each one for real)
AUTHORITY_MODULES = (
    "trader.kernel", "trader.engine.executor", "trader.engine.risk",
    "trader.engine.orchestrator", "trader.engine.exits",
    "trader.engine.supervisor", "trader.engine.control_fence",
    "trader.engine.recovery", "trader.engine.state", "trader.engine.booking",
    "trader.cognition.attention", "trader.observability.attention",
    "trader.observability.selection_persistence",
    "trader.cognition.opportunity_context", "trader.brain.llm",
    "trader.data.feed", "trader.data.derivatives", "trader.dashboard.server",
)

#: refused third-party and stdlib modules (the name and every submodule)
FORBIDDEN = (
    "ccxt", "binance", "requests", "httpx", "aiohttp", "urllib3",
    "websocket", "websockets", "anthropic", "openai", "dotenv",
    "socket", "_socket", "ssl", "_ssl", "http", "urllib.request",
    "smtplib", "ftplib", "subprocess", "multiprocessing", "asyncio",
)


def forbidden(name: str) -> bool:
    if name == "trader" or name.startswith("trader."):
        return name not in TRADER_ALLOWED
    return any(name == p or name.startswith(p + ".") for p in FORBIDDEN)


class ImportGuard:
    """A sys.meta_path finder that refuses forbidden imports."""

    def find_spec(self, fullname, path=None, target=None):
        if forbidden(fullname):
            raise ImportError(f"research shadow child: import of "
                              f"{fullname} is forbidden")
        return None


def install_import_guard() -> list:
    """Install the guard first on sys.meta_path. Returns forbidden modules
    that were already loaded (must be none)."""
    if not any(isinstance(f, ImportGuard) for f in sys.meta_path):
        sys.meta_path.insert(0, ImportGuard())
    return loaded_forbidden()


def loaded_forbidden() -> list:
    return sorted(m for m in list(sys.modules) if forbidden(m))


class HarnessAbort(RuntimeError):
    """A harness-level fault: the work transaction must not commit."""


def _det_clock(step=7):
    t = {"n": 0}

    def clock():
        t["n"] += step
        return t["n"]
    return clock


# ── worker ────────────────────────────────────────────────────────────────
def _runners():
    from trader.cognition import research_bank as rb
    from trader.cognition import research_families as rf
    from trader.cognition import research_run as run_
    from trader.cognition import research_unreadable_bank as ub
    from trader.cognition import research_unreadable_run as ru

    def decay_reached(step):
        o = step["outcomes"]
        return set(o["inserted"] + o["duplicate"])

    def unreadable_reached(step):
        o = step["outcomes"]
        return {r["object_id"] for r in o["inserted"] + o["duplicate"]}

    return {
        rf.STRATEGY_DECAY: {
            "run": run_.run, "run_schema": run_.SCHEMA,
            "file": rb.record_run, "bank_schema": rb.SCHEMA,
            "bank_rows": lambda j, rid: j.research_bank_objects(run_id=rid),
            "reached": decay_reached},
        rf.STRATEGY_HEALTH_UNREADABLE: {
            "run": ru.run, "run_schema": ru.SCHEMA,
            "file": ub.record_from_run, "bank_schema": ub.SCHEMA,
            "bank_rows": lambda j, rid: j.research_unreadable_bank_objects(
                run_id=rid),
            "reached": unreadable_reached},
    }


def _cursor(conn, name) -> int:
    return _cursor_row(conn, name)[0]


def _cursor_row(conn, name) -> tuple:
    """(source_event_id, anchor_record_sha256); (0, None) before any."""
    row = conn.execute("SELECT source_event_id, anchor_record_sha256 FROM "
                       "research_shadow_cursor WHERE cursor_name=?",
                       (name,)).fetchone()
    return (0, None) if row is None else (row[0], row[1])


def _record_sha(j, st, sc, event_id):
    """SHA-256 of the raw detail of one source spec observation, or None."""
    rows = j.source_query(f"SELECT detail FROM {st.SRC}.brain_events "
                          "WHERE id=? AND kind=?", (event_id, st.SPEC_KIND))
    if len(rows) != 1 or not isinstance(rows[0]["detail"], str):
        return None
    return sc.sha256(rows[0]["detail"])


def _insert(c, table, inv, rec, contract):
    text = contract.canonical(rec)
    c.execute(f"INSERT INTO {table}(invocation_id, canonical_sha256, "
              "canonical_json) VALUES (?,?,?)",
              (inv, contract.sha256(text), text))


def work(req: dict, *, clock_factory=None) -> None:
    """Execute one checked child request against the stores it names.
    Raises HarnessAbort / ShadowStoreError / sqlite3.Error on failure; any
    open unit is rolled back."""
    from trader.cognition import research_families as rf
    from trader.cognition import research_shadow_contract as sc
    from trader.cognition import research_shadow_store as st

    start, fault = req["start"], req["fault"]
    inv = start["invocation_id"]
    if clock_factory is None:
        clock_factory = (_det_clock if fault == "fixed_clock"
                         else lambda: time.perf_counter_ns)
    j = st.open_shadow(start["shadow_db"]["path"], start["source_db"]["path"],
                       readonly=False)
    try:
        c = j._conn()
        cur_id, anchor = _cursor_row(c, sc.CURSOR_NAME)
        if cur_id != start["cursor_before"]:
            raise HarnessAbort("cursor_moved_since_start")
        # lineage: the event the cursor stands on must still be the same
        # raw record in THIS source; a different or replaced journal (even
        # at the bound path) is refused, never read past
        if cur_id and (anchor is None
                       or _record_sha(j, st, sc, cur_id) != anchor):
            raise HarnessAbort("source_lineage_mismatch")
        for t in ("research_shadow_snapshots", "research_shadow_completions"):
            if c.execute(f"SELECT 1 FROM {t} WHERE invocation_id=?",
                         (inv,)).fetchone():
                raise HarnessAbort("invocation_state_present")
        cur = start["cursor_before"]
        [hw] = j.source_query(
            f"SELECT max(id) AS h FROM {st.SRC}.brain_events WHERE kind=?",
            (st.SWEEP_KIND,))
        high = hw["h"] or 0
        sel = j.source_query(
            f"SELECT id, subject FROM {st.SRC}.brain_events WHERE kind=? "
            "AND id>? AND id<=? ORDER BY id LIMIT ?",
            (st.SPEC_KIND, cur, high, start["bounds"]["max_sources"]))
        ids = [r["id"] for r in sel]
        spec_max = ids[-1] if ids else cur
        j.set_bound(spec_max, high)
        rows = j.strategy_health_rows()
        snapshot = {"schema": sc.SNAPSHOT_SCHEMA, "invocation_id": inv,
                    "cursor_before": cur, "sweep_high_water_event_id": high,
                    "selected_source_event_ids": ids,
                    "health_view": {"spec_max_event_id": spec_max,
                                    "sweep_max_event_id": high,
                                    "row_count": len(rows),
                                    "rows_sha256": sc.health_rows_sha256(
                                        rows)},
                    "decisions": sc.DECISIONS_ACCESS,
                    "semantics": sc.SNAPSHOT_SEMANTICS}
        sc.check_snapshot(snapshot, start)
        with j.unit() as u:
            _insert(u, "research_shadow_snapshots", inv, snapshot, sc)

        dispatch = rf.dispatch(rows, [(r["id"], r["subject"]) for r in sel])
        sources = [{"source_event_id": d["source_event_id"], "dispatch": d,
                    "question_id": next((f["question_id"]
                                         for f in d["families"]
                                         if f["family"] == d["family"]),
                                        None),
                    "run_id": None, "bank_object_ids": []}
                   for d in dispatch]
        base = {"schema": sc.COMPLETION_SCHEMA, "invocation_id": inv,
                "cursor_before": cur, "sources": sources,
                "semantics": sc.COMPLETION_SEMANTICS}
        if any(d["outcome"] == rf.AMBIGUOUS for d in dispatch):
            comp = dict(base, outcome=sc.REFUSED,
                        outcome_reason=sc.AMBIGUOUS_DISPATCH,
                        cursor_after=cur, family_runs=[])
            comp = json.loads(sc.canonical(comp))
            sc.check_completion(comp, start, snapshot)
            with j.unit() as u:
                _insert(u, "research_shadow_completions", inv, comp, sc)
            return

        runners = _runners()
        with j.unit() as u:
            runs = []
            for fam in rf.FAMILY_NAMES:
                routed = [s for s in sources
                          if s["dispatch"]["family"] == fam]
                if not routed:
                    continue
                r = runners[fam]
                rk = sc.run_key(inv, fam)
                out = r["run"](j, rk, start["inputs"]["recorded_at_ms"],
                               clock=clock_factory())
                if out["status"] != "inserted":
                    raise HarnessAbort(f"family_run_{out['status']}:{fam}")
                steps = out["receipt"]["steps"]
                bad = [s["step"] for s in steps if s["status"] != "COMPLETED"]
                if bad:
                    raise HarnessAbort(f"family_run_step_not_completed:"
                                       f"{fam}:{bad[0]}")
                reached = r["reached"](steps[0])
                if any(s["question_id"] not in reached for s in routed):
                    raise HarnessAbort(f"routed_question_not_reached:{fam}")
                if fault == "hang_in_transaction":
                    while True:
                        time.sleep(3600)
                if fault == "crash_in_transaction":
                    os._exit(70)
                filing = r["file"](j, out["run_id"],
                                   start["inputs"]["recorded_at_ms"])
                filing = {"inserted": list(filing["inserted"]),
                          "duplicate": list(filing["duplicate"]),
                          "conflict": list(filing["conflict"]),
                          "refusals": [list(x) for x in filing["refusals"]]}
                filed = set(filing["inserted"] + filing["duplicate"])
                by_q = {}
                for row in r["bank_rows"](j, out["run_id"]):
                    if row["bank_object_id"] in filed:
                        qid = json.loads(row["canonical_json"])[
                            "links"]["question"]["question_id"]
                        by_q.setdefault(qid, []).append(row["bank_object_id"])
                for s in routed:
                    s["run_id"] = out["run_id"]
                    s["bank_object_ids"] = by_q.get(s["question_id"], [])
                row = (j.research_runs(run_id=out["run_id"])
                       if fam == rf.STRATEGY_DECAY else
                       j.research_unreadable_runs(run_id=out["run_id"]))
                runs.append({"family": fam, "run_schema": r["run_schema"],
                             "run_key": rk, "run_id": out["run_id"],
                             "run_status": out["status"],
                             "run_canonical_sha256":
                                 row[0]["canonical_sha256"],
                             "bank_schema": r["bank_schema"],
                             "bank_filing": filing})
            comp = dict(base, outcome=sc.OK, outcome_reason=None,
                        cursor_after=spec_max, family_runs=runs)
            comp = json.loads(sc.canonical(comp))
            sc.check_completion(comp, start, snapshot)
            _insert(u, "research_shadow_completions", inv, comp, sc)
            new_anchor = (_record_sha(j, st, sc, spec_max) if ids
                          else anchor)
            if ids and new_anchor is None:
                raise HarnessAbort("cursor_anchor_unreadable")
            u.execute("INSERT INTO research_shadow_cursor(cursor_name, "
                      "source_event_id, anchor_record_sha256, invocation_id)"
                      " VALUES (?,?,?,?) "
                      "ON CONFLICT(cursor_name) DO UPDATE SET "
                      "source_event_id=excluded.source_event_id, "
                      "anchor_record_sha256=excluded.anchor_record_sha256, "
                      "invocation_id=excluded.invocation_id "
                      "WHERE research_shadow_cursor.source_event_id=?",
                      (sc.CURSOR_NAME, spec_max, new_anchor, inv, cur))
            if _cursor(u, sc.CURSOR_NAME) != spec_max:
                raise HarnessAbort("cursor_compare_and_set_failed")
            leaked = loaded_forbidden()
            if leaked:
                raise HarnessAbort("forbidden_module_loaded:"
                                   + ",".join(leaked))
    finally:
        j.close()


def _classify(e) -> str:
    import sqlite3
    from trader.cognition import research_shadow_contract as sc
    from trader.cognition import research_shadow_store as st
    if isinstance(e, HarnessAbort):
        return str(e)
    if isinstance(e, st.ShadowStoreError):
        return f"store:{e}"
    if isinstance(e, sc.ContractError):
        return f"contract:{e}"
    if isinstance(e, sqlite3.Error):
        return f"sqlite:{type(e).__name__}:{str(e)[:200]}"
    return f"worker_exception:{type(e).__name__}"


def main() -> int:
    leaked = install_import_guard()
    raw = sys.stdin.read(1 << 20)
    inv = None
    try:
        inv = json.loads(raw)["start"]["invocation_id"]
    except Exception:
        pass

    def emit(status, reason):
        print(json.dumps({"schema": "research-shadow-child-result.v1",
                          "invocation_id": inv, "status": status,
                          "reason": reason}, sort_keys=True,
                         separators=(",", ":")), flush=True)

    if leaked:
        emit("FAILED", "forbidden_module_preloaded:" + ",".join(leaked))
        return 1
    from trader.cognition import research_shadow_contract as sc
    extra = sorted(set(os.environ) - set(sc.CHILD_ENV_KEYS))
    if extra:
        emit("FAILED", "environment_not_sanitized:" + ",".join(extra))
        return 1
    try:
        req = json.loads(raw)
        sc.check_child_request(req)
    except (ValueError, sc.ContractError) as e:
        emit("FAILED", f"invalid_request:{e}")
        return 1
    fault = req["fault"]
    if fault == "import_forbidden_module":
        try:
            __import__("trader.engine.executor")
        except ImportError:
            emit("FAILED", "forbidden_import_blocked:trader.engine.executor")
            return 1
        emit("FAILED", "forbidden_import_not_blocked")
        return 1
    if fault == "malformed_result_before_work":
        print("not a result", flush=True)
        return 0
    try:
        work(req)
    except Exception as e:
        emit("FAILED", _classify(e))
        return 1
    if fault == "malformed_result_after_commit":
        print("{\"schema\": \"garbage\"}", flush=True)
        return 0
    emit("COMPLETED", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
