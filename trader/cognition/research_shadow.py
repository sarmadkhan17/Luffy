"""Research shadow harness — one bounded, isolated, shadow-only invocation.

``invoke`` (or ``python -m trader.cognition.research_shadow``) runs the two
TESTED research families (strategy_decay, strategy_health_unreadable)
over new strategy-health source events of the production journal and files
every artifact in a SEPARATE shadow database. It is TESTED / NOT_DEPLOYED:
nothing schedules it, no cadence, production ``max_sources`` or deadline is
chosen here, and no service, cron, systemd or supervisor entry exists.

Required, with no defaults (missing or invalid refuses startup):

- ``max_sources`` — 1..MAX_SOURCES_LIMIT: at most this many source events
  are selected by this invocation. It does NOT bound total database work:
  the existing family runners re-derive and re-verify their whole stored
  history on every run;
- ``wall_clock_deadline_s`` — 1..MAX_DEADLINE_S: the parent SIGKILLs the
  child's whole process group when it elapses;
- ``invocation_key`` and ``recorded_at_ms`` — the explicit identity inputs
  (``invocation_id`` hashes only these, never a clock reading);
  ``recorded_at_ms`` is also the stamp every family step receives.

Process isolation. The parent never opens the source journal. It
initializes the shadow store, takes an exclusive ``flock`` on
``<shadow>.lock`` (held by the child too, so an orphaned child still holds
it), writes the research-shadow-start.v1 record with ``cursor_before``, and
starts ``_research_shadow_child`` in a new session with an allow-list
environment (research_shadow_contract.CHILD_ENV_KEYS — no BINANCE_*, API
key, ``.env`` or deployment variable is inherited) and a parent-death
signal. The child opens the source only through a read-only attach.

Before any writable initialization, a shadow path that is the same
filesystem object as the source (resolved alias, symlink or hard link) is
refused, and ``init_shadow`` refuses any existing database that is not a
shadow store. The first start binds the store to one source path
(``research_shadow_source_binding``, immutable); another path is refused
(``source_binding_mismatch``). The cursor also stores the raw-record
SHA-256 of the source event it stands on, and the child refuses a source
where that record is absent or different (``source_lineage_mismatch``), so
a journal replaced at the bound path is never read past. A new source
needs a new shadow store; no database version is invented.

Interruption. If the wait for the child ends by ANY exception (deadline,
KeyboardInterrupt, SystemExit, error), the parent SIGKILLs and reaps the
child's process group before re-raising or recording anything, so the
child never outlives the wait even when a caller catches the interruption.

Receipt. Only the parent writes research-shadow-invocation.v1, and only
after the child's outcome is known: from the durable shadow state
(snapshot, completion and cursor rows), with how the child ended recorded
beside it. Outcomes are defined in research_shadow_contract.SEMANTICS; there
is no PARTIAL: the work is one transaction, so a TIMEOUT or FAILED
invocation committed no research artifact and did not move the cursor.

Idempotency. An existing receipt for the same ``invocation_id`` and the
same inputs, bounds and database paths is returned (``duplicate``) without
starting anything; different ones are a ``conflict``: nothing runs and
nothing is overwritten. A start record without a receipt (the parent died)
is finalized from the durable state, without starting a child, before
any other invocation does new work (``_finalize_pending``), so later work
never runs on top of an unreceipted invocation. A completion is verified
against the durable progression chain: the cursor (value and last writer)
must be its own result, or be reached from it through other invocations'
committed completions that each fully verify (start, snapshot, completion
digests and contracts, and their run/bank artifacts), the last of which
wrote the cursor row. Unverified or missing evidence fails closed; later
progress is never attributed to it.

No network, LLM, Attention, Kernel, Orchestrator, Risk, Execution, order or
trading authority. Receipts are ``shadow_context_only``.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

from trader.cognition import research_bank as rb
from trader.cognition import research_families as rf
from trader.cognition import research_run as run_
from trader.cognition import research_shadow_contract as sc
from trader.cognition import research_shadow_store as st
from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_run as ru

REPO_ROOT = Path(__file__).resolve().parents[2]
CHILD_MODULE = "trader.cognition._research_shadow_child"

RECORDED, DUPLICATE, CONFLICT, REFUSED_START = (
    "recorded", "duplicate", "conflict", "refused")

TIMEOUT_REASON = "wall_clock_deadline_exceeded"
INTERRUPTED = "invocation_interrupted"


def child_env() -> dict:
    """The complete child environment. Built from constants only: no value
    of the parent environment is copied."""
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(REPO_ROOT)}
    assert tuple(env) == sc.CHILD_ENV_KEYS
    return env


def _pdeathsig():               # runs in the child between fork and exec
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)
    except Exception:
        pass


def _refused(reason, inv=None) -> dict:
    return {"status": REFUSED_START, "invocation_id": inv, "receipt": None,
            "canonical_sha256": None, "reason": reason}


def _cursor(conn) -> int:
    row = conn.execute("SELECT source_event_id FROM research_shadow_cursor "
                       "WHERE cursor_name=?", (sc.CURSOR_NAME,)).fetchone()
    return 0 if row is None else row[0]


def _row(conn, table, inv):
    return conn.execute(f"SELECT canonical_sha256, canonical_json FROM "
                        f"{table} WHERE invocation_id=?", (inv,)).fetchone()


def _parsed(row, what):
    text = row["canonical_json"]
    if sc.sha256(text) != row["canonical_sha256"]:
        raise sc.ContractError(f"{what}_sha256")
    rec = json.loads(text)
    if sc.canonical(rec) != text:
        raise sc.ContractError(f"{what}_not_canonical")
    return rec


#: family -> (run module, run table, bank module, bank table). Only the
#: modules' pure contract functions (from_json / row_for) are used here:
#: the parent still never opens the source journal.
_FAMILY_STORES = {
    rf.STRATEGY_DECAY: (run_, "research_runs", rb, "research_bank_objects"),
    rf.STRATEGY_HEALTH_UNREADABLE: (ru, "research_unreadable_runs", ub,
                                    "research_unreadable_bank_objects"),
}
_ARTIFACT_ERRORS = (run_.ResearchRunError, ru.UnreadableRunError,
                    rb.ResearchBankError, ub.UnreadableBankError)


def _one_row(conn, table, key, value, what):
    rows = conn.execute(f"SELECT * FROM {table} WHERE {key}=?",
                        (value,)).fetchall()
    if len(rows) != 1:
        raise sc.ContractError(f"{what}_missing")
    return dict(rows[0])


def _projection_ok(expected: dict, row: dict) -> bool:
    return sc.canonical(expected) == sc.canonical(
        {k: row.get(k) for k in expected})


def _reached(step, key_is_ref):
    o = step["outcomes"] or {}
    items = o.get("inserted", []) + o.get("duplicate", [])
    return {x["object_id"] if key_is_ref else x for x in items}


def _check_committed(conn, comp, start) -> None:
    """Every run and bank object the completion names is stored in the
    shadow database and verifies under its own family contract, from its
    actual stored JSON: exact contract, canonical form, recomputed identity
    (run_id / bank_object_id), recomputed SHA-256 digests, row projection,
    and the bindings run <-> completion (id, receipt digest, run_key,
    recorded_at_ms, schema), bank object <-> run (links.run id and receipt
    and telemetry digests, a result the run's result step reached) and bank
    object <-> source (its question is that source's routed question, which
    the run's question step reached). Shadow-only: no source access, so
    the families' live-evidence re-derivation is the report's job."""
    ms = start["inputs"]["recorded_at_ms"]
    for r in comp["family_runs"]:
        run_mod, run_table, bank_mod, bank_table = _FAMILY_STORES[
            r["family"]]
        fam = r["family"]
        row = _one_row(conn, run_table, "run_id", r["run_id"], f"run:{fam}")
        try:
            rec, tel = run_mod.from_json(row["canonical_json"],
                                         row["telemetry_json"])
            if not _projection_ok(run_mod.row_for(rec, tel), row):
                raise sc.ContractError(f"run_projection:{fam}")
        except _ARTIFACT_ERRORS as e:
            raise sc.ContractError(f"run_invalid:{fam}:{e}") from e
        if (rec["run_id"] != r["run_id"]
                or sc.sha256(row["canonical_json"])
                != r["run_canonical_sha256"]
                or rec["schema"] != r["run_schema"]
                or rec["inputs"] != {"run_key": r["run_key"],
                                     "recorded_at_ms": ms}):
            raise sc.ContractError(f"run_binding:{fam}")
        steps = rec["steps"]
        if any(st_["status"] != "COMPLETED" for st_ in steps):
            raise sc.ContractError(f"run_not_completed:{fam}")
        ref = fam == rf.STRATEGY_HEALTH_UNREADABLE
        questions, results = _reached(steps[0], ref), _reached(steps[-1], ref)
        routed = [s for s in comp["sources"]
                  if s["dispatch"]["family"] == fam]
        if any(s["question_id"] not in questions for s in routed):
            raise sc.ContractError(f"run_question_not_reached:{fam}")
        f = r["bank_filing"]
        banks = {}
        for bid in dict.fromkeys(f["inserted"] + f["duplicate"]):
            brow = _one_row(conn, bank_table, "bank_object_id", bid,
                            f"bank_object:{fam}")
            try:
                obj = bank_mod.from_json(brow["canonical_json"])
                if not _projection_ok(bank_mod.row_for(obj), brow):
                    raise sc.ContractError(f"bank_projection:{fam}")
            except _ARTIFACT_ERRORS as e:
                raise sc.ContractError(
                    f"bank_object_invalid:{fam}:{e}") from e
            lk = obj["links"]["run"]
            if (obj["bank_object_id"] != bid or obj["schema"] !=
                    r["bank_schema"] or lk["run_id"] != r["run_id"]
                    or lk["canonical_sha256"] != r["run_canonical_sha256"]
                    or lk["telemetry_sha256"] != row["telemetry_sha256"]
                    or obj["links"]["result"]["result_id"] not in results):
                raise sc.ContractError(f"bank_object_binding:{fam}")
            banks[bid] = obj
        for s in routed:
            if any(banks[b]["links"]["question"]["question_id"]
                   != s["question_id"] for b in s["bank_object_ids"]):
                raise sc.ContractError(f"bank_object_source_binding:{fam}")


def _cursor_writer(conn):
    row = conn.execute("SELECT invocation_id FROM research_shadow_cursor "
                       "WHERE cursor_name=?", (sc.CURSOR_NAME,)).fetchone()
    return None if row is None else row[0]


def _verified_completion(conn, inv) -> dict:
    """The completion of ``inv`` only when the whole durable state it rests
    on verifies: its own start, snapshot and completion rows (digest,
    canonical form, exact contracts, mutual binding) and every run and bank
    object it names. Raises ContractError / ValueError otherwise."""
    rows = {}
    for what, table in (("start", "research_shadow_starts"),
                        ("snapshot", "research_shadow_snapshots"),
                        ("completion", "research_shadow_completions")):
        row = _row(conn, table, inv)
        if row is None:
            raise sc.ContractError(f"{what}_missing")
        rows[what] = _parsed(row, what)
    start, snap, comp = rows["start"], rows["snapshot"], rows["completion"]
    sc.check_start(start)
    if start["invocation_id"] != inv:
        raise sc.ContractError("start_binding")
    sc.check_snapshot(snap, start)
    sc.check_completion(comp, start, snap)
    _check_committed(conn, comp, start)
    return comp


def _progression_edges(conn, inv) -> dict:
    """cursor_before -> {(cursor_after, invocation_id)} from every OTHER
    invocation's committed OK completion that fully verifies. A completion
    that does not verify is not evidence of anything."""
    edges = {}
    for r in conn.execute("SELECT invocation_id FROM "
                          "research_shadow_completions").fetchall():
        other = r[0]
        if other == inv:
            continue
        try:
            c = _verified_completion(conn, other)
        except (ValueError, sc.ContractError):
            continue
        if c["outcome"] == sc.OK:
            edges.setdefault(c["cursor_before"], set()).add(
                (c["cursor_after"], other))
    return edges


def _cursor_reaches(conn, inv, frm: int, frm_writer) -> bool:
    """Whether the durable cursor (value and the invocation that last wrote
    it) is ``(frm, frm_writer)`` itself, or is reached from ``frm`` through
    a chain of other invocations' VERIFIED committed completions whose last
    link is the invocation the cursor row names. ``frm_writer`` None means
    any writer is acceptable for an unchanged value. Later progress is
    attributed to those invocations, never to ``inv``."""
    now, writer = _cursor(conn), _cursor_writer(conn)
    if now == frm and (frm_writer is None or writer == frm_writer):
        return True
    edges = _progression_edges(conn, inv)
    seen, frontier = set(), [(frm, frm_writer)]
    while frontier:
        state = frontier.pop()
        if state in seen:
            continue
        seen.add(state)
        for nxt in edges.get(state[0], ()):
            if nxt == (now, writer):
                return True
            frontier.append(nxt)
    return False


def _finalize(conn, start, child) -> dict:
    """The receipt of ``start`` from the durable shadow state plus how the
    child ended (``child``: termination/returncode/result/parsed)."""
    inv = start["invocation_id"]
    snapshot = completion = None
    reason = None
    srow = _row(conn, "research_shadow_snapshots", inv)
    if srow is not None:
        try:
            snapshot = _parsed(srow, "snapshot")
            sc.check_snapshot(snapshot, start)
        except (ValueError, sc.ContractError) as e:
            snapshot, reason = None, f"snapshot_unverifiable:{e}"
    crow = _row(conn, "research_shadow_completions", inv)
    cursor_now = _cursor(conn)
    parsed = child.pop("parsed", None)
    if crow is not None:
        try:
            completion = _verified_completion(conn, inv)
        except (ValueError, sc.ContractError) as e:
            # its own committed state does not verify: its effect is unknown
            completion = None
            outcome = sc.FAILED
            reason = f"{sc.COMMITTED_UNVERIFIABLE}:{e}"
            cursor_after = cursor_now
        else:
            cursor_after = completion["cursor_after"]
            # an OK completion wrote the cursor row; a REFUSED one left it
            writer = inv if completion["outcome"] == sc.OK else None
            if _cursor_reaches(conn, inv, cursor_after, writer):
                outcome = completion["outcome"]
                reason = completion["outcome_reason"]
            else:
                # its own effect verifies, but the cursor's later state is
                # not supported by verified evidence: fail closed
                completion = None
                outcome = sc.FAILED
                reason = (f"{sc.COMMITTED_UNVERIFIABLE}:"
                          "cursor_progression_unverified")
    else:
        # nothing of this invocation committed: its own effect on the cursor
        # is none, whatever later invocations did
        cursor_after = start["cursor_before"]
        outcome = sc.FAILED
        if not _cursor_reaches(conn, inv, start["cursor_before"], None):
            cursor_after = cursor_now
            reason = (f"{sc.COMMITTED_UNVERIFIABLE}:"
                      "cursor_moved_without_verified_completion")
        elif child["termination"] == "killed_at_deadline":
            outcome, reason = sc.TIMEOUT, TIMEOUT_REASON
        elif child["termination"] == "not_observed":
            reason = INTERRUPTED
        elif parsed is not None and parsed["status"] == sc.CHILD_FAILED:
            reason = parsed["reason"]
        elif child["returncode"] != 0:
            reason = f"child_exit_nonzero:{child['returncode']}"
        elif child["result"] != "wellformed":
            reason = "malformed_child_result"
        else:
            reason = "child_result_without_completion"
    return sc.receipt(start, snapshot=snapshot, completion=completion,
                      outcome=outcome, outcome_reason=reason,
                      cursor_after=cursor_after, child=child)


def _store_receipt(conn, rec) -> str:
    text = sc.canonical(rec)
    sha = sc.sha256(text)
    with conn:
        old = conn.execute("SELECT canonical_json FROM "
                           "research_shadow_invocations WHERE "
                           "invocation_id=?", (rec["invocation_id"],)
                           ).fetchone()
        if old is not None:
            return DUPLICATE if old[0] == text else CONFLICT
        conn.execute("INSERT INTO research_shadow_invocations(invocation_id,"
                     "schema, outcome, canonical_sha256, canonical_json) "
                     "VALUES (?,?,?,?,?)", (rec["invocation_id"],
                                           rec["schema"], rec["outcome"],
                                           sha, text))
    return RECORDED


def _same_request(stored: dict, start: dict) -> bool:
    keys = ("invocation_id", "inputs", "bounds", "source_db", "shadow_db")
    return all(sc.canonical(stored[k]) == sc.canonical(start[k])
               for k in keys)


def load_receipt(conn, inv: str):
    """(receipt, canonical_sha256) of one stored invocation, verified
    (digest, canonical form, exact contract), or None."""
    row = conn.execute("SELECT schema, outcome, canonical_sha256, "
                       "canonical_json FROM research_shadow_invocations "
                       "WHERE invocation_id=?", (inv,)).fetchone()
    if row is None:
        return None
    rec = _parsed(row, "receipt")
    sc.check_receipt(rec)
    if (row["schema"] != rec["schema"] or row["outcome"] != rec["outcome"]
            or rec["invocation_id"] != inv):
        raise sc.ContractError("receipt_row_projection")
    return rec, row["canonical_sha256"]


def _kill_group(p) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_child(start, lock_fd, fault, python) -> dict:
    req = sc.canonical(sc.child_request(start, fault))
    deadline = start["bounds"]["wall_clock_deadline_s"]
    p = subprocess.Popen(
        [python, "-s", "-m", CHILD_MODULE], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=child_env(),
        cwd=str(REPO_ROOT), start_new_session=True, close_fds=True,
        pass_fds=(lock_fd,), preexec_fn=_pdeathsig)
    killed = False
    try:
        out, err = p.communicate(req.encode(), timeout=deadline)
    except subprocess.TimeoutExpired:
        killed = True
        _kill_group(p)
        out, err = p.communicate()
    except BaseException:
        # interrupted parent (KeyboardInterrupt, SystemExit, any error):
        # the child must not outlive the wait, even if a caller catches
        # the interruption and keeps this process alive. The start record
        # stays without a receipt, so the next request for this invocation
        # finalizes it truthfully as invocation_interrupted.
        _kill_group(p)
        p.wait()
        raise
    text = out.decode("utf-8", "replace")
    parsed = sc.parse_child_result(text, start["invocation_id"])
    return {"termination": "killed_at_deadline" if killed else "exited",
            "returncode": p.returncode,
            "result": ("absent" if not text.strip() else
                       "wellformed" if parsed is not None else "malformed"),
            "parsed": parsed,
            "_stderr_tail": err.decode("utf-8", "replace")[-2000:]}


def invoke(*, source_db, shadow_db, max_sources, wall_clock_deadline_s,
           invocation_key, recorded_at_ms, _fault=None,
           _python=None) -> dict:
    """One bounded shadow invocation. Returns {"status":
    "recorded"|"duplicate"|"conflict"|"refused", "invocation_id",
    "receipt", "canonical_sha256", "reason"}. ``_fault`` is a test-only
    fault-injection hook (research_shadow_contract.FAULTS)."""
    try:
        sc.check_inputs(invocation_key, recorded_at_ms, max_sources,
                        wall_clock_deadline_s)
        if _fault not in sc.FAULTS:
            raise sc.ContractError("invalid_fault")
    except sc.ContractError as e:
        return _refused(str(e))
    if source_db is None or shadow_db is None:
        return _refused("database_paths_required")
    source, shadow = Path(source_db).resolve(), Path(shadow_db).resolve()
    # before ANY writable initialization: one filesystem object under two
    # names (resolved alias, symlink or hard link) is refused
    if source == shadow or st.same_file(source, shadow):
        return _refused("source_is_shadow")
    inv = sc.invocation_id(invocation_key, recorded_at_ms)
    try:
        st.init_shadow(shadow)
    except st.ShadowStoreError as e:
        return _refused(f"shadow_db_unusable:{e}", inv)
    except sqlite3.Error as e:
        locked = isinstance(e, sqlite3.OperationalError) and \
            "locked" in str(e)
        return _refused(("shadow_db_locked" if locked else
                         f"shadow_db_unusable:{type(e).__name__}"), inv)
    lock_fd = os.open(str(shadow) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _refused("shadow_busy", inv)
        conn = st.open_shadow_only(shadow, readonly=False)
        try:
            return _invoke_locked(conn, lock_fd, inv, source, shadow,
                                  max_sources, wall_clock_deadline_s,
                                  invocation_key, recorded_at_ms, _fault,
                                  _python or sys.executable)
        except sqlite3.OperationalError as e:
            if "locked" in str(e):
                return _refused("shadow_db_locked", inv)
            raise
        except (ValueError, sc.ContractError) as e:
            # a stored start/receipt of this invocation fails verification:
            # nothing runs and nothing is overwritten
            return _refused(f"shadow_state_unverifiable:{e}", inv)
        finally:
            conn.close()
    finally:
        os.close(lock_fd)


def _binding(conn):
    row = conn.execute("SELECT source_path FROM "
                       "research_shadow_source_binding WHERE binding_name=?",
                       (st.BINDING_NAME,)).fetchone()
    return None if row is None else row[0]


def _finalize_pending(conn, except_inv) -> list:
    """Receipts for every started invocation that has none (its parent died
    before writing one), oldest first, from durable state only; no child is
    running because the shadow lock is held. Done before any new work, so
    later work never runs on top of an unreceipted invocation."""
    done = []
    rows = conn.execute(
        "SELECT s.invocation_id AS inv, s.canonical_sha256, "
        "s.canonical_json FROM research_shadow_starts s "
        "LEFT JOIN research_shadow_invocations r USING (invocation_id) "
        "WHERE r.invocation_id IS NULL ORDER BY s.rowid").fetchall()
    for row in rows:
        if row["inv"] == except_inv:
            continue
        start = _parsed(row, "start")
        sc.check_start(start)
        rec = _finalize(conn, start, {"termination": "not_observed",
                                      "returncode": None,
                                      "result": "not_observed"})
        _store_receipt(conn, rec)
        done.append(row["inv"])
    return done


def _invoke_locked(conn, lock_fd, inv, source, shadow, max_sources,
                   deadline, key, ms, fault, python) -> dict:
    bound = _binding(conn)
    if bound is not None and bound != str(source):
        # the cursor and every stored artifact belong to one source
        # journal; another source needs its own shadow store
        return _refused("source_binding_mismatch", inv)
    stored = load_receipt(conn, inv)
    probe = sc.start_record(
        invocation_key=key, recorded_at_ms=ms, max_sources=max_sources,
        wall_clock_deadline_s=deadline,
        source_db={"path": str(source), "access": st.SOURCE_ACCESS},
        shadow_db={"path": str(shadow)}, cursor_before=_cursor(conn))
    if stored is not None:
        rec, sha = stored
        same = _same_request(rec, probe)
        return {"status": DUPLICATE if same else CONFLICT,
                "invocation_id": inv, "receipt": rec,
                "canonical_sha256": sha,
                "reason": None if same else "invocation_identity_conflict"}
    pending = _finalize_pending(conn, inv)
    srow = _row(conn, "research_shadow_starts", inv)
    if srow is not None:
        start = _parsed(srow, "start")
        sc.check_start(start)
        if not _same_request(start, probe):
            return {"status": CONFLICT, "invocation_id": inv,
                    "receipt": None, "canonical_sha256": None,
                    "reason": "invocation_identity_conflict"}
        child = {"termination": "not_observed", "returncode": None,
                 "result": "not_observed"}
    else:
        start = probe
        text = sc.canonical(start)
        with conn:
            if bound is None:
                conn.execute("INSERT INTO research_shadow_source_binding("
                             "binding_name, source_path, "
                             "bound_by_invocation_id) VALUES (?,?,?)",
                             (st.BINDING_NAME, str(source), inv))
            conn.execute("INSERT INTO research_shadow_starts(invocation_id,"
                         "canonical_sha256, canonical_json) VALUES (?,?,?)",
                         (inv, sc.sha256(text), text))
        child = _run_child(start, lock_fd, fault, python)
    stderr_tail = child.pop("_stderr_tail", None)
    rec = _finalize(conn, start, child)
    status = _store_receipt(conn, rec)
    return {"status": status, "invocation_id": inv, "receipt": rec,
            "canonical_sha256": sc.sha256(sc.canonical(rec)),
            "reason": None, "finalized_pending": pending,
            "child_stderr_tail": stderr_tail}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m trader.cognition.research_shadow",
        description="One bounded research shadow invocation (shadow DB "
                    "only; source journal read-only; no trading authority).")
    ap.add_argument("--source-db", required=True)
    ap.add_argument("--shadow-db", required=True)
    ap.add_argument("--max-sources", type=int, required=True)
    ap.add_argument("--wall-clock-deadline-s", type=int, required=True)
    ap.add_argument("--invocation-key", required=True)
    ap.add_argument("--recorded-at-ms", type=int, required=True)
    a = ap.parse_args(argv)
    res = invoke(source_db=a.source_db, shadow_db=a.shadow_db,
                 max_sources=a.max_sources,
                 wall_clock_deadline_s=a.wall_clock_deadline_s,
                 invocation_key=a.invocation_key,
                 recorded_at_ms=a.recorded_at_ms)
    res.pop("child_stderr_tail", None)
    print(sc.canonical(res))
    if res["status"] in (REFUSED_START, CONFLICT):
        return 2
    return 0 if res["receipt"]["outcome"] == sc.OK else 3


if __name__ == "__main__":
    raise SystemExit(main())
