"""SDD-STAGE-3-RESEARCH-CHAIN-SCOPED-VERIFICATION-V1.

research-run.v1 and research-bank-object.v1 verification read every chain
record by its primary key and each question's evidence against only that
spec's health rows (plus every sweep record and every unattributable
observation row, which the question/plan contracts consult). Results,
refusals, IDs, hashes and canonical records are exactly those of the former
full-table reads.

Equivalence harness: `_legacy` re-implements each new keyed reader as the
former full-table scan it replaced (health readers return every health
row, as the old code passed), so every verification path runs once with
full-table reads and once scoped over the same database, and every output
or exception must be identical. Nothing live calls any of it.
"""
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_bank import _ran
from tests.test_strategy_decay_research_evidence import D, W
from tests.test_strategy_decay_research_run import _clock, _copy, _fresh
from trader.cognition import research_bank as rb
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_result as rr
from trader.cognition import research_run as run_
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

CHAIN = ("research_questions", "research_plans", "research_evidence",
         "research_results")
KEYED = ("research_question_by_id", "research_plan_by_id",
         "research_evidence_by_id", "research_result_by_id",
         "strategy_health_rows_for_spec", "strategy_health_rows_by_id")


def _legacy(j):
    """The same Journal with every new keyed reader replaced by the former
    full-table read it stands for."""
    def scan(table_reader, key):
        return lambda rid: next((r for r in table_reader()
                                 if r[key] == rid), None)
    d = vars(j)
    d["research_question_by_id"] = scan(j.research_questions, "question_id")
    d["research_plan_by_id"] = scan(j.research_plans, "plan_id")
    d["research_evidence_by_id"] = scan(j.research_evidence, "evidence_id")
    d["research_result_by_id"] = scan(j.research_results, "result_id")
    d["strategy_health_rows_for_spec"] = lambda spec_id: \
        j.strategy_health_rows()
    d["strategy_health_rows_by_id"] = lambda ids: j.strategy_health_rows()
    return j


def _outcome(fn, *a, **k):
    try:
        return ("ok", fn(*a, **k))
    except Exception as e:                       # compared, never swallowed
        return ("raised", type(e).__name__, str(e))


def _paths(j):
    """Every verification entry point this package touches, keyed by the
    stored chain; each outcome is a value or the exact exception."""
    runs = [r["run_id"] for r in j.research_runs()]
    q = [r["question_id"] for r in j.research_questions()]
    p = [r["plan_id"] for r in j.research_plans()]
    e = [r["evidence_id"] for r in j.research_evidence()]
    out = {}
    for rid in runs:
        out[f"run.load:{rid}"] = _outcome(run_.load, j, run_id=rid)
    for x in q:
        out[f"plan.load:{x}"] = _outcome(rp.load, j, question_id=x)
    for x in p:
        out[f"evidence.load:{x}"] = _outcome(re_.load, j, plan_id=x)
    for x in e:
        out[f"result.load:{x}"] = _outcome(rr.load, j, evidence_id=x)
    for rid in runs:
        out[f"bank.record_run:{rid}"] = _outcome(rb.record_run, j, rid,
                                                 now_ms=9)
    out["bank.load"] = _outcome(rb.load, j)
    out["bank_rows"] = j.research_bank_objects()
    return out


def _both(tmp_path, j):
    """(scoped, legacy) outcomes of every path on two copies of j."""
    a = _copy(tmp_path, j, "scoped.db")
    b = _legacy(_copy(tmp_path, j, "legacy.db"))
    return _paths(a), _paths(b)


# ── fixtures: unrelated noise and requested-chain faults ─────────────────
def _insert(j, table, **cols):
    info = j.query(f"PRAGMA table_info({table})")
    row = {c["name"]: (0 if "INT" in (c["type"] or "").upper() else "x")
           for c in info if c["notnull"] and not c["pk"]}
    row.update(cols)
    names = ",".join(row)
    with j._tx() as c:
        c.execute(f"INSERT INTO {table}({names}) VALUES "
                  f"({','.join('?' * len(row))})", tuple(row.values()))


def _health(j, kind, subject, detail, event_id=None):
    cols = "ts,kind,subject,detail"
    vals = ("2026-09-25T00:00:00+00:00", kind, subject, detail)
    if event_id is not None:
        cols, vals = "id," + cols, (event_id,) + vals
    with j._tx() as c:
        c.execute(f"INSERT INTO brain_events({cols}) VALUES "
                  f"({','.join('?' * len(vals))})", vals)


def _noise(j, n=3):
    """Unrelated rows: corrupt Q/P/E/R rows under other IDs and another
    spec's corrupt and valid-looking health observations."""
    for i in range(n):
        _insert(j, "research_questions", question_id=f"zz-q{i}",
                scope_id="s9", source_event_id=10_000 + i,
                canonical_json="{corrupt")
        _insert(j, "research_plans", plan_id=f"zz-p{i}",
                question_id=f"zz-q{i}", canonical_json="{corrupt")
        _insert(j, "research_evidence", evidence_id=f"zz-e{i}",
                plan_id=f"zz-p{i}", canonical_json="{corrupt")
        _insert(j, "research_results", result_id=f"zz-r{i}",
                evidence_id=f"zz-e{i}", canonical_json="{corrupt")
        _health(j, ho.KIND_SPEC, "s9", "{corrupt")
        _health(j, ho.KIND_SPEC, "s9", '{"schema": "x"}')
    return j


def _set(j, table, col, value, where=""):
    with j._tx() as c:
        c.execute(f"UPDATE {table} SET {col}=? {where}", (value,))


def _source_event(j):
    [q] = [r for r in j.research_questions()
           if not r["question_id"].startswith("zz-")]
    return q["source_event_id"]


def _good(tmp_path):
    return _ran(tmp_path)


def _noisy(tmp_path):
    return _noise(_ran(tmp_path))


def _question_tampered(tmp_path):
    j = _noise(_ran(tmp_path))
    _set(j, "research_questions", "canonical_json", "{}",
         "WHERE question_id NOT LIKE 'zz-%'")
    return j


def _plan_tampered(tmp_path):
    j = _noise(_ran(tmp_path))
    _set(j, "research_plans", "canonical_json", "{}",
         "WHERE plan_id NOT LIKE 'zz-%'")
    return j


def _evidence_tampered(tmp_path):
    j = _noise(_ran(tmp_path))
    _set(j, "research_evidence", "canonical_json", "{}",
         "WHERE evidence_id NOT LIKE 'zz-%'")
    return j


def _result_tampered(tmp_path):
    j = _noise(_ran(tmp_path))
    _set(j, "research_results", "canonical_json", "{}",
         "WHERE result_id NOT LIKE 'zz-%'")
    return j


def _missing(table, key):
    def build(tmp_path):
        j = _noise(_ran(tmp_path))
        with j._tx() as c:
            c.execute(f"DELETE FROM {table} WHERE {key} NOT LIKE 'zz-%'")
        return j
    return build


def _source_health_tampered(tmp_path):
    j = _noise(_ran(tmp_path))
    _set(j, "brain_events", "detail", "{}", f"WHERE id={_source_event(j)}")
    return j


def _source_health_deleted(tmp_path):
    j = _noise(_ran(tmp_path))
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (_source_event(j),))
    return j


def _source_health_moved_to_other_spec(tmp_path):
    j = _noise(_ran(tmp_path))
    _set(j, "brain_events", "subject", "s9", f"WHERE id={_source_event(j)}")
    return j


def _unattributable(subject):
    """An observation row with no usable subject written before the
    history: the question/plan contracts must still see and refuse it."""
    def build(tmp_path):
        j = _noise(_ran(tmp_path))
        _health(j, ho.KIND_SPEC, subject, "{}", event_id=0)
        return j
    return build


def _sweep_tampered(tmp_path):
    j = _noise(_ran(tmp_path))
    [sw] = j.query("SELECT id FROM brain_events WHERE kind=? "
                   "ORDER BY id LIMIT 1", (ho.KIND_SWEEP,))
    _set(j, "brain_events", "detail", "{}", f"WHERE id={sw['id']}")
    return j


def _decision_changed(tmp_path):
    j = _noise(_ran(tmp_path))
    with j._tx() as c:
        c.execute("DELETE FROM decisions WHERE id='d-sel'")
    return j


def _filed_twice(tmp_path):
    j = _noise(_ran(tmp_path))
    rb.record_from_journal(j, now_ms=2)
    return j


def _second_run(tmp_path):
    j = _ran(tmp_path)
    run_.run(j, "k2", 5, clock=_clock())
    return _noise(j)


def _multi_result(tmp_path):
    return _noise(_ran(tmp_path, verdicts=(W, D, D, W, D)))


CASES = {
    "good": _good, "noisy": _noisy,
    "question_tampered": _question_tampered,
    "plan_tampered": _plan_tampered,
    "evidence_tampered": _evidence_tampered,
    "result_tampered": _result_tampered,
    "question_missing": _missing("research_questions", "question_id"),
    "plan_missing": _missing("research_plans", "plan_id"),
    "evidence_missing": _missing("research_evidence", "evidence_id"),
    "result_missing": _missing("research_results", "result_id"),
    "source_health_tampered": _source_health_tampered,
    "source_health_deleted": _source_health_deleted,
    "source_health_other_spec": _source_health_moved_to_other_spec,
    "unattributable_null": _unattributable(None),
    "unattributable_empty": _unattributable(""),
    "unattributable_blob": _unattributable(b"s1"),
    "sweep_tampered": _sweep_tampered,
    "decision_changed": _decision_changed,
    "filed_twice": _filed_twice,
    "second_run": _second_run,
    "multi_result": _multi_result,
}
VALID = ("good", "noisy", "filed_twice", "second_run", "multi_result",
         "decision_changed")          # a deleted frozen decision is absent
FAULTS = sorted(set(CASES) - set(VALID))


# ── 1/2/5/6/7/8 scoped == former full-table verification ────────────────
@pytest.mark.parametrize("name", sorted(CASES))
def test_scoped_verification_equals_full_table_verification(tmp_path, name):
    scoped, legacy = _both(tmp_path, CASES[name](tmp_path))
    assert scoped == legacy


@pytest.mark.parametrize("name", VALID)
def test_valid_chains_verify_and_file(tmp_path, name):
    scoped, _ = _both(tmp_path, CASES[name](tmp_path))
    assert all(v[0] == "ok" for k, v in scoped.items()
               if k != "bank_rows" and "zz-" not in k)
    assert all(not v[1]["refusals"] for k, v in scoped.items()
               if k.startswith("bank.record_run"))
    assert scoped["bank_rows"]


@pytest.mark.parametrize("name", FAULTS)
def test_requested_chain_faults_still_fail_closed(tmp_path, name):
    scoped, legacy = _both(tmp_path, CASES[name](tmp_path))
    failed = {k: v for k, v in scoped.items()
              if k != "bank_rows" and "zz-" not in k and (v[0] == "raised" or (
                  k.startswith("bank.record_run") and v[1]["refusals"]))}
    assert failed, "a fault in the requested chain must be refused"
    assert failed == {k: legacy[k] for k in failed}
    assert scoped["bank_rows"] == legacy["bank_rows"] == []


def test_exact_refusal_codes_are_preserved(tmp_path):
    cases = {"question_tampered": "reference_invalid:question:",
             "question_missing": "reference_missing:question",
             "result_missing": "reference_missing:result",
             "source_health_tampered": "reference_invalid:question:",
             "source_health_other_spec": "reference_invalid:question:"
                                         "evidence_missing",
             "unattributable_null": "reference_invalid:question:"
                                    "derivation_mismatch:"}
    for name, prefix in cases.items():
        path = tmp_path / name
        path.mkdir()
        scoped, legacy = _both(path, CASES[name](path))
        [key] = [k for k in scoped if k.startswith("run.load:")]
        assert scoped[key][0] == "raised" and scoped[key] == legacy[key]
        assert scoped[key][2].startswith(prefix), (name, scoped[key])
        [bank] = [v for k, v in scoped.items()
                  if k.startswith("bank.record_run")]
        assert bank == [v for k, v in legacy.items()
                        if k.startswith("bank.record_run")][0]
        assert bank[1]["refusals"][0][2].startswith("source_invalid:run:")


# ── 3/4 unrelated rows cannot affect the requested chain ────────────────
def test_unrelated_corrupt_rows_do_not_affect_the_requested_run(tmp_path):
    clean = _ran(tmp_path)
    noisy = _noise(_copy(tmp_path, clean, "noisy.db"), n=10)
    a, b = _paths(clean), _paths(noisy)
    assert {k: v for k, v in b.items() if "zz-" not in k} == a


def test_unrelated_health_rows_are_not_read(tmp_path):
    j = _noise(_ran(tmp_path), n=5)
    spec = "s1"
    rows = j.strategy_health_rows_for_spec(spec)
    assert all(r["kind"] == ho.KIND_SWEEP or r["subject"] == spec
               for r in rows)
    assert not [r for r in rows if r["subject"] == "s9"]
    full = j.strategy_health_rows()
    assert [r for r in full if r["kind"] == ho.KIND_SWEEP
            or r["subject"] in (spec, None, "")] == rows


def test_spec_subset_keeps_every_row_the_contracts_consult(tmp_path):
    j = _ran(tmp_path)
    for i, subject in enumerate((None, "", b"blob")):
        _health(j, ho.KIND_SPEC, subject, "{}", event_id=-i - 1)
    got = j.strategy_health_rows_for_spec("s1")
    assert [r["id"] for r in got] == sorted(r["id"] for r in got)
    assert {-1, -2, -3} <= {r["id"] for r in got}
    assert [r["id"] for r in got] == [
        r["id"] for r in j.strategy_health_rows()]    # all one spec


def test_health_by_id_returns_exactly_the_requested_health_rows(tmp_path):
    j = _noise(_ran(tmp_path))
    full = {r["id"]: r for r in j.strategy_health_rows()}
    ids = sorted(full)[:3] + [10 ** 9]
    assert j.strategy_health_rows_by_id(ids) == [full[i] for i in ids[:3]]
    assert j.strategy_health_rows_by_id([]) == []
    j.log_brain_event("note", "s1", "{}")
    [other] = j.query("SELECT id FROM brain_events WHERE kind='note'")
    assert j.strategy_health_rows_by_id([other["id"]]) == []


# ── no full-table chain reads when the identity is known ────────────────
_FULL = re.compile(r"FROM (research_questions|research_plans|"
                   r"research_evidence|research_results)\s*(ORDER|$)")


def _sql_log(j):
    seen, orig = [], j.query

    def query(sql, params=()):
        seen.append(" ".join(sql.split()))
        return orig(sql, params)
    vars(j)["query"] = query
    return seen


def test_verification_reads_no_full_chain_or_health_table(tmp_path):
    j = _noise(_ran(tmp_path))
    rid = j.research_runs()[0]["run_id"]
    seen = _sql_log(j)
    run_.load(j, run_id=rid)
    rb.record_run(j, rid, now_ms=9)
    rb.load(j, run_id=rid)
    assert seen
    assert not [s for s in seen if _FULL.search(s)]
    assert not [s for s in seen if s.endswith(      # strategy_health_rows()
        "WHERE kind IN ('strategy_health_observed', "
        "'strategy_health_sweep')")]


# ── 9 rows read decrease where expected ─────────────────────────────────
def _rows_read(j, fn):
    with run_._Meter(j) as m:
        fn()
    assert not m.gaps
    return m.rows


def test_verification_rows_read_do_not_grow_with_unrelated_rows(tmp_path):
    base = _ran(tmp_path)
    rid = base.research_runs()[0]["run_id"]
    small = _noise(_copy(tmp_path, base, "small.db"), n=1)
    big = _noise(_copy(tmp_path, base, "big.db"), n=40)

    def verify(j):
        # the bank object is filed outside the meter: its writer is not one
        # of research_run's declared writers
        assert rb.record_run(j, rid, now_ms=9)["refusals"] == []
        return lambda: (run_.load(j, run_id=rid), rb.load(j, run_id=rid))
    s, b = _rows_read(small, verify(small)), _rows_read(big, verify(big))
    assert s == b
    legacy = _legacy(_noise(_copy(tmp_path, base, "legacy.db"), n=40))
    assert _rows_read(legacy, verify(legacy)) > b


def test_run_receipt_is_unchanged_and_result_step_reads_fewer_rows(
        tmp_path):
    seed = _fresh(tmp_path, verdicts=(W, D))
    for i in range(20):                      # another spec's observations
        _health(seed, ho.KIND_SPEC, "s9", "{corrupt")
    a = _copy(tmp_path, seed, "a.db")
    b = _legacy(_copy(tmp_path, seed, "b.db"))
    ra = run_.run(a, "k", 1, clock=_clock())
    rb_ = run_.run(b, "k", 1, clock=_clock())
    assert ra["receipt"] == rb_["receipt"]
    assert ra["run_id"] == rb_["run_id"]
    [row_a], [row_b] = a.research_runs(), b.research_runs()
    assert (row_a["canonical_json"], row_a["canonical_sha256"]) == (
        row_b["canonical_json"], row_b["canonical_sha256"])
    ta, tb = ra["telemetry"]["steps"], rb_["telemetry"]["steps"]
    assert [t["rows_read"]["value"] for t in ta[:3]] == \
        [t["rows_read"]["value"] for t in tb[:3]]   # writer paths unchanged
    assert ta[3]["rows_read"]["value"] < tb[3]["rows_read"]["value"]
    for t in (a, b):
        assert rb.record_run(t, ra["run_id"], now_ms=2)["refusals"] == []
    [oa], [ob] = rb.load(a), rb.load(b)
    [bank_a], [bank_b] = a.research_bank_objects(), b.research_bank_objects()
    # the bank identity binds the run/result IDs and receipt hashes, not
    # telemetry: truthful telemetry differs, the object id does not
    assert oa["bank_object_id"] == ob["bank_object_id"]
    assert bank_a["bank_object_id"] == bank_b["bank_object_id"]
    assert oa["bank_object_id"] == rb.bank_object_id(oa)
    assert oa["links"]["run"]["canonical_sha256"] == \
        row_a["canonical_sha256"] == row_b["canonical_sha256"]
    # the object copies MEASURED telemetry verbatim, so its telemetry copy
    # and digest, and hence its canonical JSON and hash, differ
    assert oa["cost"]["steps"][:3] == ob["cost"]["steps"][:3]
    assert oa["cost"]["steps"][3] != ob["cost"]["steps"][3]
    assert oa["links"]["run"]["telemetry_sha256"] != \
        ob["links"]["run"]["telemetry_sha256"]
    assert bank_a["canonical_json"] != bank_b["canonical_json"]
    assert bank_a["canonical_sha256"] != bank_b["canonical_sha256"]
    # nothing else differs
    same = {k: v for k, v in oa.items() if k != "cost"}
    other = {k: v for k, v in ob.items() if k != "cost"}
    same["links"] = {**oa["links"], "run": {
        k: v for k, v in oa["links"]["run"].items()
        if k != "telemetry_sha256"}}
    other["links"] = {**ob["links"], "run": {
        k: v for k, v in ob["links"]["run"].items()
        if k != "telemetry_sha256"}}
    assert same == other


# ── 10 SELECT-only helpers; nothing live calls them ─────────────────────
def test_keyed_helpers_are_select_only():
    src = Path(Journal.__module__.replace(".", "/") + ".py")
    root = Path(run_.__file__).resolve().parents[2]
    text = (root / src).read_text()
    for name in KEYED:
        body = text.split(f"def {name}(", 1)[1].split("\n    def ", 1)[0]
        assert "SELECT" in body
        assert not re.search(r"INSERT|UPDATE|DELETE|_tx\(|execute\(", body)


def test_nothing_live_calls_the_keyed_helpers():
    root = Path(run_.__file__).resolve().parents[1]
    pat = re.compile("|".join(KEYED))
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if pat.search(p.read_text(errors="ignore")))
    assert callers == ["cognition/research_bank.py",
                       "cognition/research_evidence.py",
                       "cognition/research_plan.py",
                       "cognition/research_run.py", "core/journal.py"]


def test_no_new_table_or_schema_change(tmp_path):
    j = Journal(tmp_path / "s.db")
    with sqlite3.connect(j.db_path) as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(CHAIN) <= tables
    assert not [t for t in tables if "scoped" in t or "verif" in t]
