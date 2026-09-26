"""research-shadow-invocation.v1: required bounds, cursor semantics, one
work transaction, exact outcomes, idempotent receipts and byte-identity with
the existing direct family APIs."""
import fcntl
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from tests.research_shadow_fixtures import (RESEARCH_TABLES, checkpoint,
                                            det_clock, dump, file_sha,
                                            make_source, research_counts,
                                            spec_event_ids, table_rows)
from tests.test_strategy_decay_research_plan import CF, D, EF, W, _FullHist
from trader.cognition import _research_shadow_child as child
from trader.cognition import research_bank as rb
from trader.cognition import research_families as rf
from trader.cognition import research_run as run_
from trader.cognition import research_shadow as rs
from trader.cognition import research_shadow_contract as sc
from trader.cognition import research_shadow_store as st
from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_run as ru
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

BOGUS_PYTHON = "/nonexistent/python-must-not-start"


def _invoke(src, shadow, key="k", ms=1000, **kw):
    kw.setdefault("max_sources", 50)
    kw.setdefault("wall_clock_deadline_s", 120)
    return rs.invoke(source_db=src, shadow_db=shadow, invocation_key=key,
                     recorded_at_ms=ms, **kw)


@pytest.fixture
def stores(tmp_path):
    return make_source(tmp_path / "luffy.db"), tmp_path / "research_shadow.db"


def _cursor(shadow):
    rows = table_rows(shadow, "research_shadow_cursor")
    return rows[0]["source_event_id"] if rows else 0


def _receipts(shadow):
    return table_rows(shadow, "research_shadow_invocations")


# ── required bounds, no defaults ─────────────────────────────────────────
@pytest.mark.parametrize("over,reason", [
    ({"max_sources": None}, "max_sources_required"),
    ({"wall_clock_deadline_s": None}, "wall_clock_deadline_required"),
    ({"max_sources": 0}, "invalid_max_sources"),
    ({"max_sources": -1}, "invalid_max_sources"),
    ({"max_sources": True}, "invalid_max_sources"),
    ({"max_sources": 2.0}, "invalid_max_sources"),
    ({"max_sources": sc.MAX_SOURCES_LIMIT + 1}, "invalid_max_sources"),
    ({"wall_clock_deadline_s": 0}, "invalid_wall_clock_deadline"),
    ({"wall_clock_deadline_s": 1.5}, "invalid_wall_clock_deadline"),
    ({"wall_clock_deadline_s": sc.MAX_DEADLINE_S + 1},
     "invalid_wall_clock_deadline"),
])
def test_missing_or_invalid_bounds_refuse_startup(stores, over, reason):
    src, shadow = stores
    kw = {"max_sources": 5, "wall_clock_deadline_s": 30, **over}
    res = rs.invoke(source_db=src, shadow_db=shadow, invocation_key="k",
                    recorded_at_ms=1, _python=BOGUS_PYTHON, **kw)
    assert res["status"] == rs.REFUSED_START and res["reason"] == reason
    assert not shadow.exists()


def test_bounds_have_no_defaults_in_api_or_cli(stores):
    src, shadow = stores
    with pytest.raises(TypeError):
        rs.invoke(source_db=src, shadow_db=shadow, invocation_key="k",
                  recorded_at_ms=1, wall_clock_deadline_s=5)
    with pytest.raises(TypeError):
        rs.invoke(source_db=src, shadow_db=shadow, invocation_key="k",
                  recorded_at_ms=1, max_sources=5)
    base = ["--source-db", str(src), "--shadow-db", str(shadow),
            "--invocation-key", "k", "--recorded-at-ms", "1"]
    for missing in (["--wall-clock-deadline-s", "5"],
                    ["--max-sources", "5"]):
        with pytest.raises(SystemExit) as e:
            rs.main(base + missing)
        assert e.value.code == 2
    assert not shadow.exists()


# ── OK: dispatch, runs, cursor and receipt ───────────────────────────────
def test_ok_invocation_receipt(stores):
    src, shadow = stores
    res = _invoke(src, shadow)
    assert res["status"] == rs.RECORDED
    r = res["receipt"]
    sc.check_receipt(r)
    ids = spec_event_ids(src)
    assert r["outcome"] == sc.OK and r["outcome_reason"] is None
    assert r["authority"] == "shadow_context_only"
    assert r["cursor_before"] == 0 and r["cursor_after"] == ids[-1]
    assert [s["source_event_id"] for s in r["sources"]] == ids
    assert r["source_snapshot"]["selected_source_event_ids"] == ids
    assert r["source_snapshot"]["decisions"] == sc.DECISIONS_ACCESS
    assert r["bounds"] == {"max_sources": 50, "wall_clock_deadline_s": 120}
    assert r["source_db"]["path"] == str(src.resolve())
    assert r["child"] == {"termination": "exited", "returncode": 0,
                          "result": "wellformed"}
    fams = {s["dispatch"]["family"] for s in r["sources"]}
    assert fams == {None, rf.STRATEGY_DECAY, rf.STRATEGY_HEALTH_UNREADABLE}
    assert [f["family"] for f in r["family_runs"]] == list(rf.FAMILY_NAMES)
    for s in r["sources"]:
        if s["dispatch"]["family"]:
            assert s["run_id"] and len(s["bank_object_ids"]) == 1
    assert _cursor(shadow) == ids[-1]
    [row] = _receipts(shadow)
    assert row["canonical_sha256"] == res["canonical_sha256"]
    assert row["canonical_json"] == sc.canonical(r)


def test_receipt_rows_are_immutable(stores):
    src, shadow = stores
    _invoke(src, shadow)
    c = sqlite3.connect(shadow)
    for t in st.IMMUTABLE_TABLES:
        col = "source_path" if t.endswith("binding") else "canonical_json"
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(f"UPDATE {t} SET {col}='x'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(f"DELETE FROM {t}")
    c.close()


@pytest.mark.parametrize("verb", [
    "INSERT OR REPLACE INTO", "REPLACE INTO", "INSERT OR IGNORE INTO",
    "INSERT INTO"])
def test_immutable_rows_cannot_be_replaced(stores, verb):
    src, shadow = stores
    res = _invoke(src, shadow)
    c = sqlite3.connect(shadow)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA recursive_triggers=0")
    before = {t: table_rows(shadow, t) for t in st.IMMUTABLE_TABLES}
    for t, key in st.IMMUTABLE_TABLES.items():
        row = dict(table_rows(shadow, t)[0])
        if t == "research_shadow_invocations":
            # a VALID replacement receipt: contract-shaped, hash recomputed
            fake = dict(res["receipt"], outcome_reason=None,
                        child=dict(res["receipt"]["child"], returncode=0,
                                   result="malformed"))
            sc.check_receipt(fake)
            row["canonical_json"] = sc.canonical(fake)
            row["canonical_sha256"] = sc.sha256(row["canonical_json"])
        else:
            for k in row:
                if k != key:
                    row[k] = "x"
        cols = ",".join(row)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute(f"{verb} {t}({cols}) VALUES "
                      f"({','.join('?' * len(row))})", tuple(row.values()))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute(f"INSERT INTO {t}({cols}) VALUES "
                      f"({','.join('?' * len(row))}) ON CONFLICT({key}) "
                      f"DO UPDATE SET {key}=excluded.{key}",
                      tuple(row.values()))
    c.commit()
    assert {t: table_rows(shadow, t) for t in st.IMMUTABLE_TABLES} == before
    assert rs.load_receipt(c, res["invocation_id"])[0] == res["receipt"]
    c.close()


def test_no_family_routed_is_ok_and_advances_the_cursor(tmp_path):
    src = make_source(tmp_path / "luffy.db",
                      sweeps=(({"s1": W, "s2": "idle"}, 10),))
    shadow = tmp_path / "research_shadow.db"
    r = _invoke(src, shadow)["receipt"]
    assert r["outcome"] == sc.OK and r["family_runs"] == []
    assert {s["dispatch"]["outcome"] for s in r["sources"]} == {
        rf.NOT_ROUTED}
    assert r["cursor_after"] == spec_event_ids(src)[-1]
    assert research_counts(shadow)["research_runs"] == 0


# ── cursor: bounded, ordered, never skipping ─────────────────────────────
def test_max_sources_bounds_selection_and_cursor_never_skips(stores):
    src, shadow = stores
    ids = spec_event_ids(src)
    seen, k = [], 0
    while True:
        r = _invoke(src, shadow, key=f"k{k}", max_sources=3)["receipt"]
        k += 1
        assert r["outcome"] == sc.OK
        got = [s["source_event_id"] for s in r["sources"]]
        assert len(got) <= 3 and got == sorted(got)
        assert r["cursor_before"] == (seen[-1] if seen else 0)
        if not got:
            assert r["cursor_after"] == r["cursor_before"]
            break
        assert r["cursor_after"] == got[-1]
        seen += got
    assert seen == ids


def test_spec_rows_of_an_unfinished_sweep_are_deferred_not_skipped(tmp_path):
    h = _FullHist()
    h.sweep({"s1": W}, 10)
    h.sweep({"s1": D}, 20, write_sweep=False)     # sweep record not yet written
    src = tmp_path / "luffy.db"
    j = Journal(src)
    for r in h.rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    shadow = tmp_path / "research_shadow.db"
    r1 = _invoke(src, shadow, key="a")["receipt"]
    assert [s["source_event_id"] for s in r1["sources"]] == [1]
    # the sweep finishes: its record is written after its spec rows
    sw = {"id": "sw020", "at": "2026-09-01T00:20:00+00:00"}
    j.log_brain_event(ho.KIND_SWEEP, "sw020", json.dumps({
        "schema": ho.SCHEMA, "record": "sweep", "sweep_id": "sw020",
        "sweep_started_at": sw["at"], "status": ho.SWEEP_COMPLETED,
        "intended_spec_ids": ["s1"], "evaluation_attempted_spec_ids": ["s1"],
        "evaluation_completed_spec_ids": ["s1"], "compile_failed_spec_ids": [],
        "not_evaluated_spec_ids": [], "observation_recorded_spec_ids": ["s1"],
        "observation_failed_spec_ids": [], "aborted_at_spec_id": None,
        "observation_failures": {}, "abort_error": None}))
    r2 = _invoke(src, shadow, key="b")["receipt"]
    [s] = r2["sources"]
    assert s["source_event_id"] == 3
    assert s["dispatch"]["outcome"] == rf.ROUTED
    assert s["dispatch"]["family"] == rf.STRATEGY_DECAY


# ── idempotency and conflict ─────────────────────────────────────────────
def test_identical_rerun_is_idempotent_without_starting_a_child(stores):
    src, shadow = stores
    first = _invoke(src, shadow)
    before = dump(shadow)
    again = _invoke(src, shadow, _python=BOGUS_PYTHON)
    assert again["status"] == rs.DUPLICATE
    assert again["receipt"] == first["receipt"]
    assert again["canonical_sha256"] == first["canonical_sha256"]
    assert dump(shadow) == before


def test_conflicting_rerun_fails_closed_and_overwrites_nothing(stores):
    src, shadow = stores
    _invoke(src, shadow, max_sources=2)
    before = dump(shadow)
    res = _invoke(src, shadow, max_sources=3, _python=BOGUS_PYTHON)
    assert res["status"] == rs.CONFLICT
    assert res["reason"] == "invocation_identity_conflict"
    assert dump(shadow) == before


# ── timeout / crash / malformed ──────────────────────────────────────────
def _nothing_committed(shadow, cursor_before=0):
    assert all(n == 0 for t, n in research_counts(shadow).items())
    assert _cursor(shadow) == cursor_before
    assert table_rows(shadow, "research_shadow_completions") == []


def test_hard_timeout_kills_the_child_and_commits_nothing(stores):
    src, shadow = stores
    before = dump(src)
    res = _invoke(src, shadow, wall_clock_deadline_s=3,
                  _fault="hang_in_transaction")
    r = res["receipt"]
    assert r["outcome"] == sc.TIMEOUT
    assert r["outcome_reason"] == rs.TIMEOUT_REASON
    assert r["child"]["termination"] == "killed_at_deadline"
    assert r["cursor_after"] == r["cursor_before"] == 0
    assert r["sources"] == [] and r["family_runs"] == []
    assert r["source_snapshot"]["selected_source_event_ids"] == \
        spec_event_ids(src)
    _nothing_committed(shadow)
    assert dump(src) == before
    c = sqlite3.connect(shadow)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()
    # the same sources are processed by the next invocation: none skipped
    r2 = _invoke(src, shadow, key="next")["receipt"]
    assert r2["outcome"] == sc.OK
    assert [s["source_event_id"] for s in r2["sources"]] == \
        spec_event_ids(src)


def test_child_crash_inside_the_transaction_commits_nothing(stores):
    src, shadow = stores
    r = _invoke(src, shadow, _fault="crash_in_transaction")["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == "child_exit_nonzero:70"
    assert r["child"]["result"] == "absent"
    _nothing_committed(shadow)


def test_malformed_child_result_fails_closed(stores):
    src, shadow = stores
    r = _invoke(src, shadow,
                _fault="malformed_result_before_work")["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == "malformed_child_result"
    assert r["child"]["result"] == "malformed"
    _nothing_committed(shadow)


def test_committed_work_is_the_outcome_even_if_the_result_is_malformed(
        stores):
    src, shadow = stores
    r = _invoke(src, shadow,
                _fault="malformed_result_after_commit")["receipt"]
    assert r["outcome"] == sc.OK
    assert r["child"]["result"] == "malformed"
    assert _cursor(shadow) == r["cursor_after"] == spec_event_ids(src)[-1]


# ── source / shadow failures ─────────────────────────────────────────────
def test_source_unavailable_fails_without_creating_it(tmp_path):
    shadow = tmp_path / "research_shadow.db"
    missing = tmp_path / "absent" / "luffy.db"
    r = _invoke(missing, shadow)["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == "store:source_missing"
    assert not missing.exists() and not missing.parent.exists()
    _nothing_committed(shadow)


def test_source_locked_fails_without_mutation(tmp_path):
    src = tmp_path / "luffy.db"
    make_source(src)
    c = sqlite3.connect(src)
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("BEGIN EXCLUSIVE")
    try:
        r = _invoke(src, tmp_path / "research_shadow.db")["receipt"]
    finally:
        c.rollback()
        c.close()
    assert r["outcome"] == sc.FAILED
    assert "locked" in r["outcome_reason"]
    _nothing_committed(tmp_path / "research_shadow.db")


def test_corrupt_shadow_is_refused_untouched(stores):
    src, shadow = stores
    shadow.write_bytes(b"this is not a sqlite database" * 100)
    before = file_sha(shadow)
    res = _invoke(src, shadow, _python=BOGUS_PYTHON)
    assert res["status"] == rs.REFUSED_START
    assert res["reason"] == "shadow_db_unusable:DatabaseError"
    assert file_sha(shadow) == before


def test_locked_shadow_is_refused(stores):
    src, shadow = stores
    st.init_shadow(shadow)
    c = sqlite3.connect(shadow)
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("BEGIN EXCLUSIVE")
    try:
        res = _invoke(src, shadow, _python=BOGUS_PYTHON)
    finally:
        c.rollback()
        c.close()
    assert res["status"] == rs.REFUSED_START
    assert res["reason"] == "shadow_db_locked"


def test_concurrent_invocation_is_refused_by_the_shadow_lock(stores):
    src, shadow = stores
    st.init_shadow(shadow)
    fd = os.open(str(shadow) + ".lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        res = _invoke(src, shadow, _python=BOGUS_PYTHON)
    finally:
        os.close(fd)
    assert res["status"] == rs.REFUSED_START
    assert res["reason"] == "shadow_busy"


# ── existing partial shadow state ────────────────────────────────────────
def _start(src, shadow, key="k", ms=1000, cursor=0, **kw):
    st.init_shadow(shadow)
    start = sc.start_record(
        invocation_key=key, recorded_at_ms=ms,
        max_sources=kw.get("max_sources", 50),
        wall_clock_deadline_s=kw.get("wall_clock_deadline_s", 120),
        source_db={"path": str(Path(src).resolve()),
                   "access": st.SOURCE_ACCESS},
        shadow_db={"path": str(Path(shadow).resolve())},
        cursor_before=cursor)
    text = sc.canonical(start)
    c = sqlite3.connect(shadow)
    with c:
        c.execute("INSERT INTO research_shadow_starts VALUES (?,?,?)",
                  (start["invocation_id"], sc.sha256(text), text))
    c.close()
    return start


def test_start_without_receipt_is_finalized_interrupted(stores):
    src, shadow = stores
    _start(src, shadow)
    r = _invoke(src, shadow, _python=BOGUS_PYTHON)["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == rs.INTERRUPTED
    assert r["child"]["termination"] == "not_observed"
    _nothing_committed(shadow)


def test_committed_work_without_receipt_is_finalized_ok(stores, monkeypatch):
    src, shadow = stores

    def die(*a, **k):
        raise RuntimeError("parent died before the receipt")
    real = rs._store_receipt
    monkeypatch.setattr(rs, "_store_receipt", die)
    with pytest.raises(RuntimeError):
        _invoke(src, shadow)
    assert _receipts(shadow) == []
    monkeypatch.setattr(rs, "_store_receipt", real)
    r = _invoke(src, shadow, _python=BOGUS_PYTHON)["receipt"]
    assert r["outcome"] == sc.OK
    assert r["child"]["termination"] == "not_observed"
    assert r["cursor_after"] == _cursor(shadow) == spec_event_ids(src)[-1]


def test_conflicting_existing_research_row_aborts_without_moving_cursor(
        stores):
    src, shadow = stores
    st.init_shadow(shadow)
    # a foreign row already claims the decay question slot of a source
    from trader.cognition import research_question as rq
    rows = Journal(src).strategy_health_rows()
    q = rq.derive(rows).questions[0]
    bad = dict(rq.row_for(q), question_id="f" * 64)
    c = sqlite3.connect(shadow)
    with c:
        cols = ",".join(bad)
        c.execute(f"INSERT INTO research_questions({cols},recorded_at_ms) "
                  f"VALUES ({','.join('?' * (len(bad) + 1))})",
                  (*bad.values(), 1))
    c.close()
    r = _invoke(src, shadow)["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == \
        "routed_question_not_reached:strategy_decay"
    assert _cursor(shadow) == 0
    assert research_counts(shadow)["research_questions"] == 1
    assert research_counts(shadow)["research_unreadable_questions"] == 0


def test_ambiguous_dispatch_is_refused_and_leaves_the_cursor(
        stores, monkeypatch):
    src, shadow = stores
    start = _start(src, shadow)
    fake = (rf.Family(rf.STRATEGY_DECAY, rf.FAMILIES[0].question_schema,
                      rf.FAMILIES[0].derive),
            rf.Family(rf.STRATEGY_HEALTH_UNREADABLE,
                      rf.FAMILIES[1].question_schema,
                      rf.FAMILIES[0].derive))
    real = rf.dispatch
    monkeypatch.setattr(rf, "dispatch",
                        lambda rows, sources: real(rows, sources, fake))
    child.work(sc.child_request(start))
    conn = st.open_shadow_only(shadow, readonly=False)
    rec = rs._finalize(conn, start, {"termination": "exited",
                                     "returncode": 0,
                                     "result": "wellformed"})
    conn.close()
    assert rec["outcome"] == sc.REFUSED
    assert rec["outcome_reason"] == sc.AMBIGUOUS_DISPATCH
    assert rec["cursor_after"] == 0 and rec["family_runs"] == []
    amb = [s for s in rec["sources"]
           if s["dispatch"]["outcome"] == rf.AMBIGUOUS]
    assert amb and all(s["dispatch"]["family"] is None for s in amb)
    assert all(n == 0 for n in research_counts(shadow).values())


def test_tampered_stored_start_is_refused_not_rerun(stores):
    src, shadow = stores
    start = _start(src, shadow)
    c = sqlite3.connect(shadow)
    c.execute("DROP TRIGGER research_shadow_starts_no_update")
    c.execute("UPDATE research_shadow_starts SET canonical_json='{}'")
    c.commit()
    c.close()
    tables = ("research_shadow_starts", "research_shadow_invocations",
              "research_shadow_cursor", "research_shadow_snapshots")
    before = [table_rows(shadow, t) for t in tables]
    res = _invoke(src, shadow, _python=BOGUS_PYTHON)
    assert res["status"] == rs.REFUSED_START
    assert res["reason"].startswith("shadow_state_unverifiable:")
    assert res["invocation_id"] == start["invocation_id"]
    assert [table_rows(shadow, t) for t in tables] == before


def test_tampered_stored_receipt_fails_verification(stores):
    src, shadow = stores
    res = _invoke(src, shadow)
    c = sqlite3.connect(shadow)
    c.execute("DROP TRIGGER research_shadow_invocations_no_update")
    rec = dict(res["receipt"], outcome="TIMEOUT")
    text = sc.canonical(rec)
    c.execute("UPDATE research_shadow_invocations SET canonical_json=?, "
              "canonical_sha256=?", (text, sc.sha256(text)))
    c.commit()
    c.row_factory = sqlite3.Row
    with pytest.raises(sc.ContractError):
        rs.load_receipt(c, res["invocation_id"])
    c.close()


# ── byte-identity with the existing direct APIs ─────────────────────────
DIRECT = ((rf.STRATEGY_DECAY, run_.run, rb.record_run),
          (rf.STRATEGY_HEALTH_UNREADABLE, ru.run, ub.record_from_run))


def _with_decisions(src):
    """Decision rows around the decayed sweeps, as the decay evidence
    collector reads them (tests/test_strategy_decay_research_evidence)."""
    from tests.test_strategy_decay_research_evidence import _decision, _sig
    j = Journal(src)
    for i, (spec, at) in enumerate((("s1", "2026-09-01T00:15:00+00:00"),
                                    ("s2", "2026-09-01T00:35:00+00:00"),
                                    ("s1", "2026-09-01T00:45:00+00:00"))):
        close = int(__import__("datetime").datetime.fromisoformat(
            at).timestamp() * 1000) - 1
        _decision(j, f"shadowdec{i}", at, [_sig(spec, close_ms=close)])
    j._conn().close()
    checkpoint(src)


@pytest.mark.parametrize("decisions", [False, True])
def test_shadow_objects_are_byte_identical_to_direct_api_objects(
        tmp_path, decisions):
    src = make_source(tmp_path / "luffy.db")
    if decisions:
        _with_decisions(src)
    direct_db = tmp_path / "direct.db"
    shutil.copyfile(src, direct_db)
    shadow = tmp_path / "research_shadow.db"
    res = _invoke(src, shadow, key="identity", ms=4242,
                  _fault="fixed_clock")
    assert res["receipt"]["outcome"] == sc.OK
    inv = res["invocation_id"]
    j = Journal(direct_db)
    for fam, run, file_ in DIRECT:
        out = run(j, sc.run_key(inv, fam), 4242, clock=det_clock())
        file_(j, out["run_id"], 4242)
    checkpoint(direct_db)
    for t in RESEARCH_TABLES:
        assert table_rows(shadow, t) == table_rows(direct_db, t), t
    ev = [json.loads(r["canonical_json"])
          for r in table_rows(shadow, "research_evidence")]
    assert ev and ("shadowdec" in json.dumps(ev)) == decisions
    got = {r["family"]: r["run_id"] for r in res["receipt"]["family_runs"]}
    assert got == {fam: run_.run_id(sc.run_key(inv, fam), 4242)
                   if fam == rf.STRATEGY_DECAY else
                   ru.run_id(sc.run_key(inv, fam), 4242)
                   for fam, _r, _f in DIRECT}


def test_shadow_objects_verify_against_the_real_source(stores):
    src, shadow = stores
    _invoke(src, shadow)
    j = st.open_shadow(shadow, src, readonly=True)
    try:
        j.set_bound(st.MAX_EVENT_ID, st.MAX_EVENT_ID)
        assert run_.load(j) and ru.load(j)
        assert rb.load(j) and ub.load(j)
    finally:
        j.close()


# ── review findings: interruption, source binding, recovery order ───────
def test_interrupted_parent_kills_the_child_before_releasing(stores,
                                                             monkeypatch):
    src, shadow = stores
    seen = {}
    real = rs.subprocess.Popen

    class Popen(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen["p"] = self

        def communicate(self, input=None, timeout=None):
            if "interrupted" not in seen:
                seen["interrupted"] = True
                try:
                    super().communicate(input, timeout=1.5)
                except rs.subprocess.TimeoutExpired:
                    pass
                raise KeyboardInterrupt
            return super().communicate(input, timeout=timeout)
    monkeypatch.setattr(rs.subprocess, "Popen", Popen)
    with pytest.raises(KeyboardInterrupt):
        _invoke(src, shadow, wall_clock_deadline_s=60,
                _fault="hang_in_transaction")
    p = seen["p"]
    assert p.returncode is not None               # killed and reaped
    with pytest.raises(ProcessLookupError):
        os.killpg(p.pid, 0)                       # whole group gone
    _nothing_committed(shadow)
    monkeypatch.setattr(rs.subprocess, "Popen", real)
    r = _invoke(src, shadow, wall_clock_deadline_s=60,
                _fault="hang_in_transaction",
                _python=BOGUS_PYTHON)["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == rs.INTERRUPTED


def test_store_is_bound_to_one_source_path(tmp_path):
    a = make_source(tmp_path / "a.db")
    b = make_source(tmp_path / "b.db")
    shadow = tmp_path / "research_shadow.db"
    r = _invoke(a, shadow, key="1", max_sources=3)["receipt"]
    assert r["cursor_after"] > 0
    before = dump(shadow)
    res = _invoke(b, shadow, key="2", _python=BOGUS_PYTHON)
    assert res["status"] == rs.REFUSED_START
    assert res["reason"] == "source_binding_mismatch"
    assert dump(shadow) == before


def test_replaced_source_at_the_bound_path_is_refused(tmp_path):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    r1 = _invoke(src, shadow, key="1", max_sources=3)["receipt"]
    cur = r1["cursor_after"]
    # a different journal now sits at the bound path: same ids, other rows
    src.unlink()
    make_source(src, sweeps=(({"s9": W, "s8": W}, 10),
                             ({"s9": D, "s8": EF}, 20),
                             ({"s9": EF, "s8": CF}, 30)))
    r2 = _invoke(src, shadow, key="2")["receipt"]
    assert r2["outcome"] == sc.FAILED
    assert r2["outcome_reason"] == "source_lineage_mismatch"
    assert r2["cursor_after"] == r2["cursor_before"] == cur
    assert _cursor(shadow) == cur


def _lose_receipt(monkeypatch, src, shadow, **kw):
    real = rs._store_receipt

    def die(*a, **k):
        raise RuntimeError("parent died before the receipt")
    monkeypatch.setattr(rs, "_store_receipt", die)
    with pytest.raises(RuntimeError):
        _invoke(src, shadow, **kw)
    monkeypatch.setattr(rs, "_store_receipt", real)


def test_pending_invocation_is_finalized_before_later_work(stores,
                                                           monkeypatch):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A", max_sources=3)
    a_cursor = _cursor(shadow)
    res = _invoke(src, shadow, key="B")
    inv_a = sc.invocation_id("A", 1000)
    assert res["finalized_pending"] == [inv_a]
    ra = rs.load_receipt(st.open_shadow_only(shadow, readonly=True),
                         inv_a)[0]
    assert ra["outcome"] == sc.OK and ra["cursor_after"] == a_cursor
    assert len(ra["sources"]) == 3 and ra["family_runs"]
    rb_ = res["receipt"]
    assert rb_["outcome"] == sc.OK and rb_["cursor_before"] == a_cursor


def test_recovery_attributes_later_progress_to_the_later_invocation(
        stores, monkeypatch):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A", max_sources=3)
    a_cursor = _cursor(shadow)
    # the historical state the reviewer reproduced: B committed on top of
    # an unreceipted A (pending finalization disabled to recreate it)
    monkeypatch.setattr(rs, "_finalize_pending", lambda conn, inv: [])
    rb_ = _invoke(src, shadow, key="B")["receipt"]
    assert rb_["outcome"] == sc.OK and rb_["cursor_after"] > a_cursor
    ra = _invoke(src, shadow, key="A", max_sources=3,
                 _python=BOGUS_PYTHON)["receipt"]
    assert ra["outcome"] == sc.OK
    assert ra["cursor_after"] == a_cursor
    assert len(ra["sources"]) == 3 and ra["family_runs"]


def test_interrupted_invocation_is_not_charged_later_cursor_progress(
        stores, monkeypatch):
    src, shadow = stores
    _start(src, shadow, key="A")
    monkeypatch.setattr(rs, "_finalize_pending", lambda conn, inv: [])
    rb_ = _invoke(src, shadow, key="B")["receipt"]
    assert rb_["cursor_after"] > 0
    ra = _invoke(src, shadow, key="A", _python=BOGUS_PYTHON)["receipt"]
    assert ra["outcome"] == sc.FAILED
    assert ra["outcome_reason"] == rs.INTERRUPTED
    assert ra["cursor_after"] == ra["cursor_before"] == 0


# ── review round 2: progression evidence must verify ────────────────────
FAKE = "f" * 64


def _forge(shadow, a_cursor, comp_json, sha=None):
    """Insert a completion claimed by FAKE and point the cursor at 99."""
    c = sqlite3.connect(shadow)
    with c:
        c.execute("INSERT INTO research_shadow_completions VALUES (?,?,?)",
                  (FAKE, sha or sc.sha256(comp_json), comp_json))
        c.execute("UPDATE research_shadow_cursor SET source_event_id=99, "
                  "invocation_id=?", (FAKE,))
    c.close()


def _a_completion(shadow):
    [row] = table_rows(shadow, "research_shadow_completions")
    return json.loads(row["canonical_json"])


def _recover_a(src, shadow):
    return _invoke(src, shadow, key="A", max_sources=3,
                   _python=BOGUS_PYTHON)["receipt"]


def _plausible(a):
    return sc.canonical(dict(a, invocation_id=FAKE,
                             cursor_before=a["cursor_after"],
                             cursor_after=99))


@pytest.mark.parametrize("forgery", [
    "wrong_hash", "malformed_contract", "missing_start_and_snapshot"])
def test_fabricated_progression_is_not_accepted(stores, monkeypatch,
                                                forgery):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A", max_sources=3)
    a = _a_completion(shadow)
    if forgery == "wrong_hash":
        _forge(shadow, a["cursor_after"], _plausible(a), sha="WRONG_HASH")
    elif forgery == "malformed_contract":
        _forge(shadow, a["cursor_after"], sc.canonical(
            {"invocation_id": FAKE, "outcome": "OK",
             "cursor_before": a["cursor_after"], "cursor_after": 99}))
    else:
        _forge(shadow, a["cursor_after"], _plausible(a))
    monkeypatch.setattr(rs, "_finalize_pending", lambda conn, inv: [])
    r = _recover_a(src, shadow)
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == \
        f"{sc.COMMITTED_UNVERIFIABLE}:cursor_progression_unverified"
    assert r["cursor_after"] == a["cursor_after"]       # A's own effect
    assert r["sources"] == [] and r["family_runs"] == []


def test_progression_with_a_missing_run_artifact_is_not_accepted(
        stores, monkeypatch):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A", max_sources=3)
    a_cursor = _cursor(shadow)
    monkeypatch.setattr(rs, "_finalize_pending", lambda conn, inv: [])
    rb_ = _invoke(src, shadow, key="B")["receipt"]
    assert rb_["outcome"] == sc.OK and rb_["cursor_after"] > a_cursor
    [run] = [r for r in rb_["family_runs"]
             if r["family"] == rf.STRATEGY_HEALTH_UNREADABLE]
    c = sqlite3.connect(shadow)
    with c:
        c.execute("DELETE FROM research_unreadable_runs WHERE run_id=?",
                  (run["run_id"],))
    c.close()
    r = _recover_a(src, shadow)
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == \
        f"{sc.COMMITTED_UNVERIFIABLE}:cursor_progression_unverified"
    assert r["cursor_after"] == a_cursor


def test_forged_progression_after_an_interrupted_start_fails_closed(
        stores, monkeypatch):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A", max_sources=3)
    a = _a_completion(shadow)
    _start(src, shadow, key="C", cursor=a["cursor_after"])
    _forge(shadow, a["cursor_after"], _plausible(a))
    monkeypatch.setattr(rs, "_finalize_pending", lambda conn, inv: [])
    r = _invoke(src, shadow, key="C", _python=BOGUS_PYTHON)["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == (f"{sc.COMMITTED_UNVERIFIABLE}:"
                                   "cursor_moved_without_verified_completion")


# ── review round 3: committed artifacts verify by content ───────────────
_TABLES = {(rf.STRATEGY_DECAY, "run"): ("research_runs", "run_id"),
           (rf.STRATEGY_DECAY, "bank"): ("research_bank_objects",
                                         "bank_object_id"),
           (rf.STRATEGY_HEALTH_UNREADABLE, "run"): (
               "research_unreadable_runs", "run_id"),
           (rf.STRATEGY_HEALTH_UNREADABLE, "bank"): (
               "research_unreadable_bank_objects", "bank_object_id")}


def _corrupt(shadow, family, target, run_id, how):
    table, key = _TABLES[(family, target)]
    [row] = [r for r in table_rows(shadow, table)
             if r["run_id"] == run_id][:1]
    if how == "content_keep_hash":
        sets = {"canonical_json": "{}"}
    elif how == "content_recompute_hash":
        rec = json.loads(row["canonical_json"])
        rec["semantics"] = rec["semantics"] + " (altered)"
        text = sc.canonical(rec)
        sets = {"canonical_json": text, "canonical_sha256": sc.sha256(text)}
    else:                                      # wrong_hash
        sets = {"canonical_sha256": "0" * 64}
    c = sqlite3.connect(shadow)
    with c:
        c.execute(f"UPDATE {table} SET "
                  + ",".join(f"{k}=?" for k in sets)
                  + f" WHERE {key}=?", (*sets.values(), row[key]))
    c.close()


def _runs_of(comp):
    return {r["family"]: r["run_id"] for r in comp["family_runs"]}


CORRUPTIONS = [(fam, target, how)
               for fam in (rf.STRATEGY_DECAY, rf.STRATEGY_HEALTH_UNREADABLE)
               for target in ("run", "bank")
               for how in ("content_keep_hash", "content_recompute_hash",
                           "wrong_hash")]


@pytest.mark.parametrize("fam,target,how", CORRUPTIONS)
def test_own_corrupted_artifact_fails_recovery_closed(stores, monkeypatch,
                                                      fam, target, how):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A")
    a = _a_completion(shadow)
    _corrupt(shadow, fam, target, _runs_of(a)[fam], how)
    r = _invoke(src, shadow, key="A", _python=BOGUS_PYTHON)["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"].startswith(sc.COMMITTED_UNVERIFIABLE + ":")
    assert "progression" not in r["outcome_reason"]
    assert r["sources"] == [] and r["family_runs"] == []


@pytest.mark.parametrize("fam,target,how", CORRUPTIONS)
def test_corrupted_later_artifact_is_not_progression_evidence(
        stores, monkeypatch, fam, target, how):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A", max_sources=3)
    a_cursor = _cursor(shadow)
    monkeypatch.setattr(rs, "_finalize_pending", lambda conn, inv: [])
    rb_ = _invoke(src, shadow, key="B")["receipt"]
    assert rb_["outcome"] == sc.OK
    assert set(_runs_of(rb_)) == set(rf.FAMILY_NAMES)
    _corrupt(shadow, fam, target, _runs_of(rb_)[fam], how)
    r = _recover_a(src, shadow)
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == \
        f"{sc.COMMITTED_UNVERIFIABLE}:cursor_progression_unverified"
    assert r["cursor_after"] == a_cursor


def test_bank_object_bound_to_another_source_question_is_refused(
        stores, monkeypatch):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A")
    a = _a_completion(shadow)
    # swap two routed sources' bank ids inside a re-hashed completion
    routed = [s for s in a["sources"]
              if s["dispatch"]["family"] == rf.STRATEGY_HEALTH_UNREADABLE]
    assert len(routed) >= 2
    routed[0]["bank_object_ids"], routed[1]["bank_object_ids"] = (
        routed[1]["bank_object_ids"], routed[0]["bank_object_ids"])
    text = sc.canonical(a)
    c = sqlite3.connect(shadow)
    with c:
        c.execute("DROP TRIGGER research_shadow_completions_no_update")
        c.execute("UPDATE research_shadow_completions SET canonical_json=?,"
                  " canonical_sha256=?", (text, sc.sha256(text)))
    c.close()
    r = _invoke(src, shadow, key="A", _python=BOGUS_PYTHON)["receipt"]
    assert r["outcome"] == sc.FAILED
    assert r["outcome_reason"] == (f"{sc.COMMITTED_UNVERIFIABLE}:"
                                   "bank_object_source_binding:"
                                   f"{rf.STRATEGY_HEALTH_UNREADABLE}")


def test_parent_recovery_still_never_opens_the_source(stores, monkeypatch):
    src, shadow = stores
    _lose_receipt(monkeypatch, src, shadow, key="A")
    real = sqlite3.connect
    opened = []

    def connect(target, *a, **k):
        opened.append(str(target))
        return real(target, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", connect)
    r = _invoke(src, shadow, key="A", _python=BOGUS_PYTHON)["receipt"]
    monkeypatch.undo()
    assert r["outcome"] == sc.OK
    assert opened and not [t for t in opened if "luffy.db" in t]
