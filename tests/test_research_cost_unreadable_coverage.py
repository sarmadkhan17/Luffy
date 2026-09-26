"""SDD-STAGE-3-STRATEGY-HEALTH-UNREADABLE-COST-LEDGER-COVERAGE-V1: opt-in
strategy-health-unreadable-run-telemetry.v1 coverage in research-cost-ledger.v1.
The default ledger stays byte-identical to 71cf905. Synthetic; no network,
LLM, Attention, Kernel, Risk or Execution."""
import json
import random
import sqlite3
import subprocess
import types
from pathlib import Path

import pytest

from tests.test_research_cost_ledger import (  # noqa: F401  (fixtures)
    FORBIDDEN, _entries, _family, _group, _keys, isolated, receipts_db)
from tests.test_strategy_health_unreadable_run import _clock, _two
from trader.cognition import research_cost as rc
from trader.cognition import research_run as run_
from trader.cognition import research_unreadable_bank as ub
from trader.cognition import research_unreadable_plan as up
from trader.cognition import research_unreadable_question as uq
from trader.cognition import research_unreadable_run as ru
from trader.core.journal import Journal

ROOT = Path(__file__).resolve().parents[1]
BASE = "71cf905"                         # HEAD this package was cut from
UF = "strategy-health-unreadable-run-telemetry.v1"
# ledger_sha256 of the baseline module's build() and build([], [])
PINNED = {(): "e29ae2273dcd7724f283a716c62d8338796148a6e30b493d7343a11896015f4f",
          ((), ()): "65bf5a3184602398d75f518627df778520465d327f3902867fd5f842c334ad34"}


def _base_module():
    src = subprocess.run(
        ["git", "-C", str(ROOT), "show",
         f"{BASE}:trader/cognition/research_cost.py"],
        capture_output=True, text=True)
    if src.returncode != 0:
        pytest.skip("baseline commit not available")
    m = types.ModuleType("research_cost_base")
    exec(compile(src.stdout, "research_cost_base", "exec"), m.__dict__)
    return m


def _both(tmp_path, n=1, decay=1):
    """One journal holding `decay` strategy-decay runs and `n` unreadable
    runs, all with a deterministic clock."""
    j = _two(tmp_path)
    for i in range(decay):
        run_.run(j, f"d{i}", 1 + i, clock=_clock(7))
    for i in range(n):
        ru.run(j, f"u{i}", 5 + i, clock=_clock(3))
    return j


def _unreadable(ledger):
    return _family(ledger, UF)


# 1, 2 ── default output and hash byte-identical to the baseline ───────────
@pytest.mark.parametrize("args", [(), ((), ())])
def test_default_pure_ledger_hash_is_pinned(args):
    ledger = rc.build(*[list(a) for a in args])
    assert rc.ledger_sha256(ledger) == PINNED[args]
    assert [f["family"] for f in ledger["families"]] == [
        rc.RECEIPT_FAMILY, rc.RUN_FAMILY]


def test_default_ledger_is_byte_identical_to_baseline(tmp_path, receipts_db):
    base = _base_module()
    j = _both(tmp_path, n=2, decay=2)
    receipts, runs = rc.read_receipt_rows(receipts_db), j.research_runs()
    bad = dict(runs[0], telemetry_json="{")
    for args, kw in [((), {}), (([], []), {}), ((receipts, runs), {}),
                     ((receipts, [bad] + runs[1:]), {}),
                     ((None, None), {"receipt_source": ("UNAVAILABLE", "x")})]:
        a, b = base.build(*args, **kw), rc.build(*args, **kw)
        assert rc.canonical(b) == base.canonical(a)
        assert rc.ledger_sha256(b) == base.ledger_sha256(a)
    for kw in ({}, {"journal": j}, {"investigation_path": receipts_db},
               {"journal": j, "investigation_path": receipts_db}):
        a, b = base.from_sources(**kw), rc.from_sources(**kw)
        assert rc.canonical(b) == base.canonical(a)
        assert rc.ledger_sha256(b) == base.ledger_sha256(a)
        assert rc.from_sources(include_unreadable_runs=False, **kw) == b
    # the default path never reads the unreadable table
    assert UF not in rc.canonical(rc.from_sources(journal=j))
    assert base.run_entries(j, runs[0]["run_id"]) == rc.run_entries(
        j, runs[0]["run_id"])


def test_default_never_touches_the_unreadable_table(tmp_path):
    j = _both(tmp_path)
    j.research_unreadable_runs = lambda *a, **k: pytest.fail("read")
    rc.from_sources(journal=j)


def test_unreadable_rows_without_opt_in_are_refused_not_dropped():
    with pytest.raises(ValueError, match="unreadable_coverage_not_requested"):
        rc.build([], [], unreadable_run_rows=[])
    with pytest.raises(ValueError, match="unreadable_coverage_not_requested"):
        rc.build(unreadable_run_source=("UNAVAILABLE", "x"))


# 3, 12 ── opt-in appends exactly one family; existing ones byte-identical ─
def test_opt_in_appends_exactly_one_unreadable_family(tmp_path, receipts_db):
    j = _both(tmp_path, n=2, decay=2)
    default = rc.from_sources(journal=j, investigation_path=receipts_db)
    on = rc.from_sources(journal=j, investigation_path=receipts_db,
                         include_unreadable_runs=True)
    assert [f["family"] for f in on["families"]] == [
        rc.RECEIPT_FAMILY, rc.RUN_FAMILY, UF]
    assert rc.UNREADABLE_FAMILY == ru.TELEMETRY_SCHEMA == UF
    for a, b in zip(default["families"], on["families"][:2]):
        assert rc.canonical(a) == rc.canonical(b)
    assert {k: v for k, v in on.items() if k != "families"} == \
        {k: v for k, v in default.items() if k != "families"}
    fam = _unreadable(on)
    assert fam["authority"] == "measurement_only"
    assert (fam["source_state"], fam["source_reason"]) == ("READ", None)
    assert fam["source_semantics"] == ru.TELEMETRY_SEMANTICS
    assert fam["refused_sources"] == []
    assert set(fam) == set(on["families"][1]) | {"authority"}
    assert "authority" not in on["families"][0] | on["families"][1]


def test_opt_in_ordering_is_deterministic(tmp_path, receipts_db):
    j = _both(tmp_path, n=3, decay=2)
    receipts, runs, urs = (rc.read_receipt_rows(receipts_db),
                           j.research_runs(), j.research_unreadable_runs())
    base = rc.build(receipts, runs, include_unreadable_runs=True,
                    unreadable_run_rows=urs)
    text = rc.canonical(base)
    for seed in range(5):
        a, b, c = list(receipts), list(runs), list(urs)
        for i, x in enumerate((a, b, c)):
            random.Random(seed * 3 + i).shuffle(x)
        again = rc.build(a, b, include_unreadable_runs=True,
                         unreadable_run_rows=c)
        assert rc.canonical(again) == text
    assert rc.from_sources(journal=j, investigation_path=receipts_db,
                           include_unreadable_runs=True) == base
    ms = [e["provenance"]["run_recorded_at_ms"]
          for e in _unreadable(base)["entries"]]
    assert ms == sorted(ms) and len(set(ms)) == 3


# 4, 5 ── measured elapsed_wall_ns and rows_read copied exactly ────────────
def test_measured_unreadable_telemetry_is_copied_exactly(tmp_path):
    j = _both(tmp_path, decay=0)
    [stored] = ru.load(j)
    [row] = j.research_unreadable_runs()
    fam = _unreadable(rc.from_sources(journal=j,
                                      include_unreadable_runs=True))
    tel, rec = stored["telemetry"], stored["receipt"]
    assert len(fam["entries"]) == len(ru.STEPS) * 2
    for t, s in zip(tel["steps"], rec["steps"]):
        for name, unit, definition in rc.UNREADABLE_MEASURES:
            [e] = _entries(fam, measurement=name, step=t["step"])
            assert (e["state"], e["value"], e["unit"], e["reason"]) == (
                t[name]["status"], t[name]["value"], t[name]["unit"],
                t[name]["reason"])
            assert e["unit_definition"] == t[name]["source"] == definition
            assert e["family"] == UF and e["source_id"] == rec["run_id"]
            assert e["context"] == {"step": s["step"],
                                    "step_status": s["status"]}
            assert e["provenance"] == {
                "table": "research_unreadable_runs", "run_id": rec["run_id"],
                "canonical_sha256": row["canonical_sha256"],
                "telemetry_sha256": row["telemetry_sha256"],
                "run_recorded_at_ms": rec["inputs"]["recorded_at_ms"]}
    walls = [e["value"] for e in _entries(fam, measurement="elapsed_wall_ns")]
    assert walls == [3] * len(ru.STEPS)
    rows = [t["rows_read"]["value"] for t in tel["steps"]]
    assert all(type(v) is int and v > 0 for v in rows)
    assert [e["value"] for e in _entries(fam, measurement="rows_read")] == rows
    assert (_group(fam, "elapsed_wall_ns")["state"],
            _group(fam, "elapsed_wall_ns")["value"]) == ("SUMMED", 12)
    assert _group(fam, "rows_read")["value"] == sum(rows)
    assert rc.UNREADABLE_MEASURES == (
        ("elapsed_wall_ns", "ns", ru.CLOCK), ("rows_read", "rows",
                                               ru.ROWS_DEFINITION))


# 6 ── NOT_MEASURED reasons preserved exactly ──────────────────────────────
def test_unmetered_access_reason_is_preserved(tmp_path, monkeypatch):
    j = _two(tmp_path)
    orig = uq.record_from_journal

    def sneaky(journal, now_ms):
        journal._conn().execute("SELECT 1").fetchall()
        return orig(journal, now_ms=now_ms)
    monkeypatch.setattr(uq, "record_from_journal", sneaky)
    ru.run(j, "k", 5)
    fam = _unreadable(rc.from_sources(journal=j,
                                      include_unreadable_runs=True))
    [e] = _entries(fam, measurement="rows_read", step="question")
    assert (e["state"], e["value"], e["reason"]) == (
        "NOT_MEASURED", None,
        "unmetered_journal_access:direct_connection_access")
    g = _group(fam, "rows_read")
    assert (g["state"], g["value"], g["reason"], g["not_measured"]) == (
        "NOT_ESTABLISHED", None, "unmeasured_entries_present", 1)
    assert _group(fam, "elapsed_wall_ns")["state"] == "SUMMED"


def test_not_run_steps_keep_step_not_run(tmp_path, monkeypatch):
    j = _two(tmp_path)

    def boom(journal, now_ms):
        raise RuntimeError("planner unavailable")
    monkeypatch.setattr(up, "record_from_journal", boom)
    ru.run(j, "k", 5, clock=_clock(3))
    fam = _unreadable(rc.from_sources(journal=j,
                                      include_unreadable_runs=True))
    got = [(e["context"]["step"], e["context"]["step_status"],
            e["measurement"], e["state"], e["value"], e["reason"])
           for e in fam["entries"]]
    assert got[2] == ("plan", "FAILED", "elapsed_wall_ns", "MEASURED", 3,
                      None)
    for step in ("evidence", "result"):
        for m in ("elapsed_wall_ns", "rows_read"):
            assert (step, "NOT_RUN", m, "NOT_MEASURED", None,
                    "step_not_run") in got
    for m in ("elapsed_wall_ns", "rows_read"):
        assert _group(fam, m)["state"] == "NOT_ESTABLISHED"
        assert _group(fam, m)["value"] is None


# 7 ── a missing / empty / unreadable source is never zero ─────────────────
def test_missing_unreadable_source_is_not_zero(tmp_path):
    empty = rc.build([], [], include_unreadable_runs=True,
                     unreadable_run_rows=[])
    fam = _unreadable(empty)
    assert fam["source_state"] == "READ" and fam["entries"] == []
    for g in fam["groups"]:
        assert (g["state"], g["value"], g["reason"]) == (
            "NOT_ESTABLISHED", None, "no_entries")
    assert empty["total_research_cost"]["reasons"] == list(rc.TOTAL_REASONS)

    unread = rc.from_sources(include_unreadable_runs=True)
    fam = _unreadable(unread)
    assert (fam["source_state"], fam["source_reason"]) == (
        "NOT_READ", "source_not_supplied")
    assert fam["groups"] == fam["entries"] == []
    assert unread["total_research_cost"]["reasons"][-1] == \
        "source_not_read:" + UF

    j = Journal(tmp_path / "j.db")
    with j._tx() as c:
        c.execute("DROP TABLE research_unreadable_runs")
    broken = rc.from_sources(journal=j, include_unreadable_runs=True)
    fam = _unreadable(broken)
    assert (fam["source_state"], fam["source_reason"]) == (
        "UNAVAILABLE", "source_unreadable:OperationalError")
    assert fam["groups"] == fam["entries"] == []
    assert _family(broken, rc.RUN_FAMILY)["source_state"] == "READ"
    assert broken["total_research_cost"]["value"] is None
    assert broken["total_research_cost"]["reasons"][-1] == \
        "source_unavailable:" + UF


def test_unreadable_not_covered_is_its_own_truthful_list():
    fam = _unreadable(rc.build([], [], include_unreadable_runs=True,
                               unreadable_run_rows=[]))
    names = [n["name"] for n in fam["not_covered"]]
    assert names == [n for n, _ in rc.UNREADABLE_NOT_COVERED]
    assert all(n["state"] == "UNKNOWN" and n["reason"]
               for n in fam["not_covered"])
    # decay-specific declarations are not copied
    decay = {n for n, _ in rc.RUN_NOT_COVERED}
    assert "research_work_outside_offline_runner" not in names
    assert "unstored_run_attempts" not in names
    assert decay & set(names) == {"sqlite_page_and_writer_reads"}
    assert "SQLite page reads" in ru.ROWS_DEFINITION   # the shared exclusion
    reasons = " ".join(n["reason"] for n in fam["not_covered"])
    assert "recall" not in reasons and "prior-research" not in reasons


def test_unstored_conflict_attempt_leaves_no_entry(tmp_path):
    j = _both(tmp_path, decay=0)
    [row] = j.research_unreadable_runs()
    before = _unreadable(rc.from_sources(journal=j,
                                         include_unreadable_runs=True))
    rec = json.loads(row["canonical_json"])
    rec["steps"][0]["outcomes"]["refusals"].append(
        {"spec_id": "x", "reason": "r", "event_id": None, "sweep_id": None})
    status = j.record_research_unreadable_run(
        ru.row_for(rec, json.loads(row["telemetry_json"])),
        recorded_at_ms=5)
    assert status == "conflict"
    after = _unreadable(rc.from_sources(journal=j,
                                        include_unreadable_runs=True))
    assert after == before


# 8 ── malformed / tampered unreadable rows fail closed ────────────────────
def _retel(row, mutate):
    tel = json.loads(row["telemetry_json"])
    mutate(tel)
    text = ru.canonical(tel)
    return dict(row, telemetry_json=text,
                telemetry_sha256=ru._sha(text))


@pytest.mark.parametrize("tamper, reason", [
    (lambda r: dict(r, telemetry_json=ru.canonical(dict(
        json.loads(r["telemetry_json"]), run_id="x"))),
     "telemetry_contract"),
    (lambda r: _retel(r, lambda t: t["steps"][0]["elapsed_wall_ns"]
                      .update(value=None)), "measured_shape"),
    (lambda r: _retel(r, lambda t: t["steps"][0]["rows_read"]
                      .update(value=True)), "measured_shape"),
    (lambda r: _retel(r, lambda t: t["steps"][0]["elapsed_wall_ns"]
                      .update(status="NOT_MEASURED", value=None, source=None,
                              reason="guess")), "not_measured_reason"),
    (lambda r: _retel(r, lambda t: t["steps"][1]["rows_read"]
                      .update(unit="bytes")), "measure_unit"),
    (lambda r: _retel(r, lambda t: t.update(semantics="cost")),
     "telemetry_contract"),
    (lambda r: _retel(r, lambda t: t["steps"].reverse()),
     "telemetry_step_order"),
    (lambda r: dict(r, telemetry_json=r["telemetry_json"] + " "),
     "telemetry_not_canonical"),
    (lambda r: dict(r, telemetry_json="{"), "telemetry_not_json"),
    (lambda r: dict(r, telemetry_json='{"a":1e999}'),
     "malformed_source:ValueError"),
    (lambda r: dict(r, canonical_json="[" * 100_000 + "]" * 100_000),
     "malformed_source:RecursionError"),
    (lambda r: dict(r, telemetry_json=ru.canonical(dict(
        json.loads(r["telemetry_json"]), extra=1))), "telemetry_keys"),
    (lambda r: dict(_retel(r, lambda t: t["steps"][0]["elapsed_wall_ns"]
                           .update(value=1)),
                    telemetry_sha256=r["telemetry_sha256"]),
     "row_projection"),
    (lambda r: dict(r, run_recorded_at_ms=r["run_recorded_at_ms"] + 1),
     "row_projection"),
    (lambda r: dict(r, canonical_json=ru.canonical(dict(
        json.loads(r["canonical_json"]), authority="live"))),
     "receipt_contract"),
    (lambda r: dict(r, telemetry_json=None), "telemetry_not_text"),
])
def test_tampered_unreadable_row_is_refused(tmp_path, tamper, reason):
    j = _both(tmp_path, n=2, decay=0)
    rows = j.research_unreadable_runs()
    bad = tamper(rows[0])
    fam = _unreadable(rc.build(None, None, include_unreadable_runs=True,
                               unreadable_run_rows=[bad, rows[1]]))
    assert fam["refused_sources"] == [
        {"source_id": rows[0]["run_id"],
         "reason": f"unreadable_run_invalid:{reason}"}]
    assert {e["source_id"] for e in fam["entries"]} == {rows[1]["run_id"]}
    for g in fam["groups"]:
        assert (g["state"], g["value"], g["reason"]) == (
            "NOT_ESTABLISHED", None, "refused_source_records_present")


def test_consistent_telemetry_rewrite_row_projection_limit(tmp_path):
    """A rewrite of a MEASURED value that also rewrites the stored
    telemetry_sha256 stays well-formed; like the decay family, telemetry is
    bound only by its stored digest (the row, not an external anchor)."""
    j = _both(tmp_path, decay=0)
    [row] = j.research_unreadable_runs()
    bad = _retel(row, lambda t: t["steps"][0]["elapsed_wall_ns"]
                 .update(value=1))
    assert bad["telemetry_sha256"] != row["telemetry_sha256"]
    fam = _unreadable(rc.build(None, None, include_unreadable_runs=True,
                               unreadable_run_rows=[bad]))
    [e] = _entries(fam, measurement="elapsed_wall_ns", step="question")
    assert e["value"] == 1
    assert e["provenance"]["telemetry_sha256"] == bad["telemetry_sha256"]


def test_duplicate_unreadable_run_ids_are_refused(tmp_path):
    rows = _both(tmp_path, decay=0).research_unreadable_runs()
    fam = _unreadable(rc.build(None, None, include_unreadable_runs=True,
                               unreadable_run_rows=rows + rows))
    assert fam["refused_sources"] == [
        {"source_id": rows[0]["run_id"],
         "reason": "unreadable_run_invalid:duplicate_source_id"}] * 2
    assert fam["entries"] == []


def test_run_rows_are_refused_across_families(tmp_path):
    """A decay run row is not an unreadable row and vice versa: each family
    reads only its own contract, so mixed-in rows are refused, not merged."""
    j = _both(tmp_path)
    [decay], [unr] = j.research_runs(), j.research_unreadable_runs()
    ledger = rc.build(None, [decay, unr], include_unreadable_runs=True,
                      unreadable_run_rows=[unr, decay])
    runs, fam = _family(ledger, rc.RUN_FAMILY), _unreadable(ledger)
    assert [r["source_id"] for r in runs["refused_sources"]] == [
        unr["run_id"]]
    assert [r["source_id"] for r in fam["refused_sources"]] == [
        decay["run_id"]]
    assert runs["refused_sources"][0]["reason"].startswith("run_invalid:")
    assert fam["refused_sources"][0]["reason"].startswith(
        "unreadable_run_invalid:")
    assert {e["source_id"] for e in runs["entries"]} == {decay["run_id"]}
    assert {e["source_id"] for e in fam["entries"]} == {unr["run_id"]}


# 9 ── no cross-family aggregation ─────────────────────────────────────────
def test_no_cross_family_aggregation(tmp_path, receipts_db):
    j = _both(tmp_path, n=2, decay=2)
    ledger = rc.from_sources(journal=j, investigation_path=receipts_db,
                             include_unreadable_runs=True)
    runs, fam = _family(ledger, rc.RUN_FAMILY), _unreadable(ledger)
    for f in ledger["families"]:
        for g in f["groups"]:
            es = [e for e in f["entries"]
                  if e["measurement"] == g["measurement"]]
            assert {e["family"] for e in es} == {g["family"]} == {
                f["family"]}
            assert g["state"] == "SUMMED"
            assert g["value"] == sum(e["value"] for e in es)
    # decay and unreadable runs: same measurement names, never one group
    assert _group(runs, "elapsed_wall_ns")["value"] == 2 * 4 * 7
    assert _group(fam, "elapsed_wall_ns")["value"] == 2 * 4 * 3
    assert _group(runs, "rows_read")["unit_definition"] != _group(
        fam, "rows_read")["unit_definition"]
    assert not {e["source_id"] for e in runs["entries"]} & {
        e["source_id"] for e in fam["entries"]}
    assert all(e["provenance"]["table"] == "research_unreadable_runs"
               for e in fam["entries"])
    pairs = [(g["family"], g["measurement"]) for f in ledger["families"]
             for g in f["groups"]]
    assert len(pairs) == len(set(pairs)) == len(rc.RECEIPT_UNITS) + 2 + 2
    assert set(ledger) == {"schema", "builder_id", "semantics",
                           "total_research_cost", "families"}


# 10 ── no per-result attribution ──────────────────────────────────────────
def test_no_per_result_attribution(tmp_path):
    j = _both(tmp_path, decay=0)
    [stored] = ru.load(j)
    fam = _unreadable(rc.from_sources(journal=j,
                                      include_unreadable_runs=True))
    text = rc.canonical(fam)
    ids = [r["object_id"] for s in stored["receipt"]["steps"]
           for k in ("inserted", "duplicate")
           for r in s["outcomes"][k]] + [
        i for s in stored["receipt"]["steps"]
        for i in s["outcomes"]["conflict"]]
    assert ids and not any(i in text for i in ids)
    for e in fam["entries"]:
        assert set(e) == {"family", "source_id", "measurement", "state",
                          "value", "unit", "unit_definition", "reason",
                          "provenance", "context"}
        assert set(e["context"]) == {"step", "step_status"}
    for word in ("result_id", "question_id", "plan_id", "evidence_id",
                 "bank_object_id", "spec_id", "s1"):
        assert f'"{word}"' not in text


# 11, 14 ── total NOT_ESTABLISHED; no budget / priority / live authority ───
def test_total_stays_not_established_and_no_authority(tmp_path,
                                                      receipts_db):
    ledger = rc.from_sources(journal=_both(tmp_path, n=2),
                             investigation_path=receipts_db,
                             include_unreadable_runs=True)
    assert ledger["total_research_cost"] == {
        "state": "NOT_ESTABLISHED", "value": None,
        "reasons": list(rc.TOTAL_REASONS)}
    assert not [k for k in _keys(ledger) if FORBIDDEN.search(k.lower())]
    fam = _unreadable(ledger)
    assert fam["authority"] == "measurement_only"
    text = rc.canonical(fam).lower()
    for word in ("usd", "usdt", "price", "fee", "monetary", "roi",
                 "priority", "salience", "usefulness", "context_only",
                 "live"):
        assert word not in text.replace(ru.TELEMETRY_SEMANTICS.lower(), "")


# 13 ── unreadable Bank cost blocks are never counted ──────────────────────
def test_bank_cost_block_is_not_double_counted(tmp_path):
    j = _both(tmp_path, decay=0)
    before = rc.from_sources(journal=j, include_unreadable_runs=True)
    res = ub.record_from_journal(j, now_ms=9)
    objs = ub.load(j)
    assert len(res["inserted"]) == len(objs) == 2   # two results, one run
    [tel] = [r["telemetry"] for r in ru.load(j)]
    for obj in objs:                                # each a verbatim copy
        assert obj["cost"]["steps"] == tel["steps"]
    after = rc.from_sources(journal=j, include_unreadable_runs=True)
    assert rc.canonical(after) == rc.canonical(before)
    fam = _unreadable(after)
    assert len(fam["entries"]) == len(ru.STEPS) * 2
    assert {e["source_id"] for e in fam["entries"]} == {tel["run_id"]}
    assert "research_unreadable_bank_objects" not in rc.canonical(after)


def test_opt_in_ledger_is_read_only(tmp_path):
    j = _both(tmp_path)
    tables = [r["name"] for r in j.query(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    before = {t: j.query(f'SELECT * FROM "{t}"') for t in tables}
    rc.from_sources(journal=j, include_unreadable_runs=True)
    assert {t: j.query(f'SELECT * FROM "{t}"') for t in tables} == before
    ro = sqlite3.connect(f"file:{j.db_path}?mode=ro", uri=True)
    assert ro.execute("SELECT count(*) FROM research_unreadable_runs"
                      ).fetchone() == (1,)
    ro.close()


# 8b ── malformed run identities are isolated per row, JSON-safe ───────────
def _assert_isolated(ledger, good, reasons):
    text = rc.canonical(ledger)                  # serializable at all
    assert json.loads(text) == json.loads(rc.canonical(json.loads(text)))
    assert len(rc.ledger_sha256(ledger)) == 64
    fam = _unreadable(ledger)
    assert sorted(r["reason"] for r in fam["refused_sources"]) == sorted(
        f"unreadable_run_invalid:{x}" for x in reasons)
    assert all(r["source_id"] is None or isinstance(r["source_id"], str)
               for r in fam["refused_sources"])
    assert {e["source_id"] for e in fam["entries"]} == {good["run_id"]}
    assert len(fam["entries"]) == len(ru.STEPS) * 2
    for g in fam["groups"]:
        assert (g["state"], g["value"], g["reason"]) == (
            "NOT_ESTABLISHED", None, "refused_source_records_present")


@pytest.mark.parametrize("bad, kind", [
    ([1, 2], "list"), ({"a": 1}, "dict"), (float("inf"), "float"),
    (float("nan"), "float"), (b"bad", "bytes"), (bytearray(b"x"),
                                                  "bytearray"),
    (None, "NoneType"), (7, "int"), (True, "bool"), ("not-hex", "str"),
    ("", "str")])
def test_malformed_run_identity_refuses_only_its_row(tmp_path, bad, kind):
    rows = _both(tmp_path, n=2, decay=0).research_unreadable_runs()
    ledger = rc.build(None, None, include_unreadable_runs=True,
                      unreadable_run_rows=[dict(rows[0], run_id=bad),
                                           rows[1]])
    _assert_isolated(ledger, rows[1], [f"invalid_source_id:{kind}"])
    assert _unreadable(ledger)["refused_sources"][0]["source_id"] is None


def test_uppercase_or_foreign_identity_is_refused(tmp_path):
    rows = _both(tmp_path, n=2, decay=0).research_unreadable_runs()
    upper = dict(rows[0], run_id=rows[0]["run_id"].upper())
    other = dict(rows[0], run_id="0" * 64)       # well-formed, not this row's
    ledger = rc.build(None, None, include_unreadable_runs=True,
                      unreadable_run_rows=[upper, other, rows[1]])
    _assert_isolated(ledger, rows[1],
                     ["invalid_source_id:str", "row_projection"])


def test_repeated_unhashable_identities_do_not_abort(tmp_path):
    rows = _both(tmp_path, n=2, decay=0).research_unreadable_runs()
    bad = [dict(rows[0], run_id=[1]), dict(rows[0], run_id=[1]),
           dict(rows[0], run_id={"x": [1]})]
    ledger = rc.build(None, None, include_unreadable_runs=True,
                      unreadable_run_rows=bad + [rows[1]])
    _assert_isolated(ledger, rows[1], ["invalid_source_id:list"] * 2
                     + ["invalid_source_id:dict"])


@pytest.mark.parametrize("stored, kind", [
    (sqlite3.Binary(b"bad"), "bytes"), (None, "NoneType")])
def test_stored_malformed_identity_is_isolated(tmp_path, stored, kind):
    j = _both(tmp_path, n=2, decay=0)
    rows = j.research_unreadable_runs()
    with j._tx() as c:
        c.execute("UPDATE research_unreadable_runs SET run_id=? "
                  "WHERE run_id=?", (stored, rows[0]["run_id"]))
    got = [r["run_id"] for r in j.research_unreadable_runs()]
    assert type(got[0]) is (bytes if kind == "bytes" else type(None))
    ledger = rc.from_sources(journal=j, include_unreadable_runs=True)
    _assert_isolated(ledger, rows[1], [f"invalid_source_id:{kind}"])
    # the other families and the default ledger are untouched by it
    assert rc.canonical(rc.from_sources(journal=j)["families"]) == \
        rc.canonical(ledger["families"][:2])
