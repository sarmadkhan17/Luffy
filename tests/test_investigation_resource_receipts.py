"""SDD-STAGE-3-ATTENTION-INVESTIGATION-RESOURCE-RECEIPTS-V1: per-case resource
receipts from work the investigation step already performs. Raw measurement only:
no bound, no compliance other than UNKNOWN. Synthetic; no network, LLM or trading."""
import ast
import json
import math
import re
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from trader.cognition import investigation as I
from trader.observability import investigation as C
from tests import test_correlation_investigation_family as CF
from tests import test_positioning_investigation_family as PF
from tests.test_investigation_state_feedback import ALL, open_symbols, publish, two_active

UNITS = ("wall_ns", "cpu_ns", "evidence_rows_read", "ledger_rows_written", "payload_bytes",
         "llm_calls", "venue_requests")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def paths(tmp_path):
    now = int(time.time()*1000)//I.TF*I.TF+60_000
    return tmp_path/"attention.db", tmp_path/"investigation.db", now


def rows(dest):
    with sqlite3.connect(dest) as db:
        return db.execute("SELECT id,case_id,stage,execution_id,recorded_ms,payload "
                          "FROM resource_receipts ORDER BY rowid").fetchall()


def case_ids(dest):
    with sqlite3.connect(dest) as db:
        return dict(db.execute("SELECT symbol,id FROM cases"))


def assert_measured(r):
    assert r["schema_version"] == C.RECEIPT_SCHEMA
    assert set(r["measurements"]) == set(UNITS)
    assert r["measurement_status"] == "MEASURED" and r["unknown_units"] == {}
    for unit in UNITS:
        v = r["measurements"][unit]
        assert type(v) is int and v >= 0, unit
    assert math.isfinite(r["elapsed_ms"]) and r["elapsed_ms"] >= 0
    assert r["elapsed_ms"] == r["measurements"]["wall_ns"] / 1e6
    assert r["measurements"]["llm_calls"] == 0 and r["measurements"]["venue_requests"] == 0
    assert r["resource_bound"] is None and r["resource_compliance"] == "UNKNOWN"
    assert r["coverage"] == "committed_case_local_investigation_work"
    assert "failed_or_rolled_back_attempt_cost" in r["not_covered"] and "total_research_cost" in r["not_covered"]
    assert re.fullmatch(r"[0-9a-f]{32}", r["execution_id"])


# 1, 2, 5, 8 ── one receipt per registered case, measured separately ─────────

def test_one_registration_pass_one_receipt_per_case(paths):
    source, dest, now = paths
    publish(source, now, "s1", [0, 1])
    detail = C.step(source, dest, now)
    assert detail["registered"] == 2
    got = C.receipts(dest)
    ids = case_ids(dest)
    assert sorted(r["case_id"] for r in got) == sorted(ids.values())
    assert len({r["receipt_id"] for r in got}) == 2 and len({r["execution_id"] for r in got}) == 1
    assert got[0]["execution_id"] == detail["resource_receipts"]["execution_id"]
    for r in got:
        assert_measured(r)
        inv = I.investigation_from_dict(json.loads(sqlite3.connect(dest).execute(
            "SELECT payload FROM cases WHERE id=?", (r["case_id"],)).fetchone()[0]))
        (first_update,) = sqlite3.connect(dest).execute(
            "SELECT id FROM updates WHERE case_id=?", (r["case_id"],)).fetchone()
        assert (r["stage"], r["outcome"]) == ("registration", "registered")
        assert r["work"] == {"source_scan_id": "s1", "previous_update_id": None,
                             "resulting_update_id": first_update}
        assert (r["family"], r["protocol_id"], r["episode_id"], r["symbol"]) == (
            inv.measurement.family, inv.measurement.catalog_id, inv.episode_id, inv.state.symbol)
        assert r["source_scan_id"] == "s1" and r["observed_ms"] == now
        assert r["accounting_scope"] == "investigation_step_case_local"
        assert r["measurements"]["payload_bytes"] > 0 and r["measurements"]["ledger_rows_written"] > 0
        assert r["measurements"]["evidence_rows_read"] > 0
        assert r["receipt_id"] == C.digest({k: r[k] for k in ("schema_version", "execution_id", "case_id", "stage", "work")})
    # Raw process-local clock readings are never persisted.
    for (*_, payload) in rows(dest):
        assert "start" not in json.loads(payload) and "monotonic" not in payload


# 3, 4, 5, 7 ── every real execution is a receipt; only re-persisting one is idempotent

def test_repeated_real_work_distinct_and_retry_idempotent(paths):
    source, dest, _ = paths
    t = two_active(paths)
    C.step(source, dest, t)                      # s2: S0,S1 assessed; S2..S4 registered
    C.step(source, dest, t+1)                    # S2..S4 first assessed on s2
    s0 = case_ids(dest)["S0/USDT"]
    polls = []
    for i in (2, 3):                             # same case, same scan, same prior update
        before = len(rows(dest))
        again = C.step(source, dest, t+i)
        res = again["resource_receipts"]
        new = C.receipts(dest)[before:]
        assert res["written"] == len(new) == 5 and res["duplicates_ignored"] == 0
        # Repeated polling cost is attributed, not moved into the remainder.
        assert res["attributed_wall_ns"] == sum(r["measurements"]["wall_ns"] for r in new)
        assert res["attributed_wall_ns"] + res["unattributed_wall_ns"] == res["pass_wall_ns"]
        assert {r["execution_id"] for r in new} == {res["execution_id"]}
        polls.append(next(r for r in new if r["case_id"] == s0))
    a, b = polls
    assert a["stage"] == b["stage"] == "assessment"
    assert a["outcome"] == b["outcome"] == "no_state_change"
    assert a["work"] == b["work"] and a["work"]["resulting_update_id"] is None
    assert a["work"]["source_scan_id"] == "s2" and a["work"]["previous_update_id"] is not None
    assert a["execution_id"] != b["execution_id"] and a["receipt_id"] != b["receipt_id"]
    history = rows(dest)
    # Persisting the SAME measured execution again creates nothing and rewrites nothing.
    with closing(C.ledger(dest)) as db, db:
        assert C._persist_receipt(db, a) is False and C._persist_receipt(db, b) is False
    assert rows(dest) == history
    # A later new scan is another execution, recorded normally.
    end = max(d["investigation"]["measurement"]["deadline_ms"] for d in C.dossiers(dest))
    publish(source, end+1, "final", [])
    r = C.step(source, dest, end+1)
    assert r["updated"] == 5 and open_symbols(dest) == []
    last = C.receipts(dest, s0)[-1]
    assert (last["stage"], last["outcome"]) == ("update", "update:measured")
    assert last["work"]["source_scan_id"] == "final" and last["work"]["previous_update_id"] == a["work"]["previous_update_id"]
    assert last["work"]["resulting_update_id"] is not None
    assert rows(dest)[:len(history)] == history
    for g in C.receipts(dest):
        assert_measured(g)


# 6 ── failed or invalid timing is UNKNOWN, never 0 ─────────────────────────

@pytest.mark.parametrize("unit,clock,reason", [
    ("wall_ns", lambda: 1/0, "clock_read_failed"),
    ("cpu_ns", lambda: 1.5, "clock_read_failed"),
    ("wall_ns", iter(range(10**12, 0, -1000)).__next__, "non_monotonic_reading"),
])
def test_failed_measurement_is_unknown_not_zero(paths, monkeypatch, unit, clock, reason):
    source, dest, now = paths
    publish(source, now, "s1", [0, 1])
    monkeypatch.setitem(C._CLOCKS, unit, clock)
    detail = C.step(source, dest, now)
    assert detail["registered"] == 2 and detail["status"] == "ok"
    got = C.receipts(dest)
    assert len(got) == 2
    for r in got:
        assert r["measurements"][unit] is None and r["unknown_units"] == {unit: reason}
        assert r["measurement_status"] == "UNKNOWN" and r["resource_compliance"] == "UNKNOWN"
        if unit == "wall_ns":
            assert r["elapsed_ms"] is None
    res = detail["resource_receipts"]
    name = unit.split("_")[0]
    assert res[f"unattributed_{name}_ns"] is None and unit in res["unknown_units"]
    assert res["unknown_receipts"] == 2


def test_egress_inside_a_case_section_makes_llm_and_venue_unknown(paths, monkeypatch):
    source, dest, now = paths
    publish(source, now, "s1", [0])
    real = I.open_investigation
    def with_egress(*a, **k):
        sys.audit("socket.connect", None, None)   # observed only; no connection is made
        return real(*a, **k)
    monkeypatch.setattr(I, "open_investigation", with_egress)
    C.step(source, dest, now)
    (r,) = C.receipts(dest)
    assert r["measurements"]["llm_calls"] is None and r["measurements"]["venue_requests"] is None
    assert r["unknown_units"] == {"llm_calls": "unclassified_egress", "venue_requests": "unclassified_egress"}
    assert r["egress_events"] == {"socket.connect": 1} and r["measurement_status"] == "UNKNOWN"


# 7 ── shared / unattributable work is never charged to a case ──────────────

def test_shared_work_is_reported_unattributed_not_apportioned(paths):
    source, dest, _ = paths
    t = two_active(paths)
    with sqlite3.connect(dest) as db:            # S0's episode closed: its reselection is refused
        db.execute("UPDATE cases SET terminal_ms=? WHERE symbol='S0/USDT'", (t,))
    before = len(rows(dest))
    detail = C.step(source, dest, t)
    assert detail["skipped"] == {"existing_closed_episode": 1} and detail["registered"] == 2
    new = C.receipts(dest)[before:]
    # S1 assessed; S2,S3 registered. The refused S0 candidate charges nothing.
    assert sorted(r["symbol"] for r in new) == ["S1/USDT", "S2/USDT", "S3/USDT"]
    assert {r["symbol"]: r["stage"] for r in new if r["stage"] == "registration"} == {
        "S2/USDT": "registration", "S3/USDT": "registration"}
    res = detail["resource_receipts"]
    assert res["attributed_wall_ns"] == sum(r["measurements"]["wall_ns"] for r in new)
    assert res["attributed_cpu_ns"] == sum(r["measurements"]["cpu_ns"] for r in new)
    assert res["unattributed_wall_ns"] > 0 and res["unattributed_cpu_ns"] >= 0
    assert res["attributed_wall_ns"] + res["unattributed_wall_ns"] == res["pass_wall_ns"]
    assert res["pass_wall_ns"] <= detail["elapsed_ms"] * 1e6
    assert res["resource_compliance"] == "UNKNOWN" and res["unknown_units"] == {}
    assert {r["stage"] for r in C.receipts(dest)} <= {"registration", "update", "assessment"}


# 8, 9 ── compliance stays UNKNOWN; no budget is declared anywhere ───────────

def test_no_bound_budget_or_compliance_verdict_exists():
    src = Path(C.__file__).read_text()
    assert "WITHIN_BOUND" not in src and "EXCEEDED" not in src
    tree = ast.parse(src)
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets
             if isinstance(t, ast.Name)}
    names |= {e.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets
              if isinstance(t, ast.Tuple) for e in t.elts if isinstance(e, ast.Name)}
    assert not [n for n in names if re.search(r"BUDGET|BOUND|RESOURCE|RECEIPT_MAX|COST", n)]
    assert src.count('resource_compliance="UNKNOWN"') == 1
    assert (I.MAX_RUNTIME_MS, I.GUARD_MS) == (20_000, 30_000)
    assert (C.MAX_ACTIVE, C.MAX_UPDATES, C.MAX_CASES, C.MAX_CASE_UPDATES) == (32, 32, 256, 64)


# 10 ── history survives restart; rolled-back passes leave no receipt ───────

def test_receipts_roll_back_with_the_pass(paths, monkeypatch):
    source, dest, now = paths
    publish(source, now, "s1", [0, 1])
    monkeypatch.setattr(I, "MAX_RUNTIME_MS", 0)
    with pytest.raises(TimeoutError):
        C.step(source, dest, now)
    assert rows(dest) == []                      # rolled-back cost: no receipt, never 0
    monkeypatch.setattr(I, "MAX_RUNTIME_MS", 20_000)
    assert C.step(source, dest, now)["resource_receipts"]["written"] == 2


def test_restart_preserves_receipt_history(paths):
    source, dest, now = paths
    publish(source, now, "s1", [0, 1])
    C.step(source, dest, now)
    first = rows(dest)
    publish(source, now+1000, "s2", ALL)
    C.step(source, dest, now+1000)
    C.step(source, dest, now+2000)
    later = rows(dest)
    assert later[:len(first)] == first and len(later) > len(first)
    # Replayable from the ledger alone, in append order.
    assert [json.loads(p) for (*_, p) in later] == C.receipts(dest)


# 11, 12 ── receipts are telemetry: every other artefact is byte-identical ───

def dump(dest):
    with sqlite3.connect(dest) as db:
        tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if t not in ("resource_receipts", "diagnostics")]
        return {t: sorted(map(repr, db.execute(f"SELECT * FROM {t}"))) for t in tables}


def without_receipts(monkeypatch):
    def none(db, meter, *a):
        meter.stop()
        return True, {"wall_ns": 0, "cpu_ns": 0}, False
    monkeypatch.setattr(C, "_receipt", none)


def paired(monkeypatch, source, a, b, now):
    """Same source, two ledgers: one with receipts, one with the writer removed."""
    with_detail = C.step(source, a, now)
    with monkeypatch.context() as m:
        without_receipts(m)
        without_detail = C.step(source, b, now)
    return with_detail, without_detail


def strip(detail):
    return {k: v for k, v in detail.items() if k not in ("elapsed_ms", "resource_receipts")}


def test_cases_allocation_and_replay_identical_with_and_without_receipts(paths, monkeypatch):
    source, a, now = paths
    b = a.with_name("without.db")
    publish(source, now, "s1", [0, 1])
    out = [paired(monkeypatch, source, a, b, now)]
    publish(source, now+1000, "s2", ALL)
    out += [paired(monkeypatch, source, a, b, now+1000), paired(monkeypatch, source, a, b, now+2000)]
    end = max(d["investigation"]["measurement"]["deadline_ms"] for d in C.dossiers(a))
    publish(source, end+1, "final", [])
    out.append(paired(monkeypatch, source, a, b, end+1))
    assert rows(a) and not rows(b)
    assert dump(a) == dump(b)
    for w, wo in out:
        assert strip(w) == strip(wo)
    assert out[1][0]["allocation"]["selected"] == ["S2/USDT", "S3/USDT", "S4/USDT"]
    assert out[-1][0]["updated"] == 5


@pytest.mark.parametrize("family,run", [
    ("positioning", lambda p: PF.run(p, PF.FUND_UP)),
    ("correlation", lambda p: CF.run(p)),
])
def test_positioning_and_correlation_results_unchanged(tmp_path, monkeypatch, family, run):
    source, with_dest, with_detail = run(tmp_path)
    without_dest = tmp_path/"without.db"
    with monkeypatch.context() as m:
        without_receipts(m)
        without_detail = C.step(source, without_dest, PF.NOW if family == "positioning" else CF.NOW)
    assert dump(with_dest) == dump(without_dest) and strip(with_detail) == strip(without_detail)
    inv, _ = (PF if family == "positioning" else CF).case(with_dest)
    got = C.receipts(with_dest, inv.investigation_id)
    assert [(r["stage"], r["family"], r["protocol_id"]) for r in got] == [
        ("registration", inv.measurement.family, inv.measurement.catalog_id)]
    assert inv.measurement.family == (I.POSITIONING_FAMILY if family == "positioning" else I.CORRELATION_FAMILY)
    for r in C.receipts(with_dest):
        assert_measured(r)


# 13 ── no network / LLM / trading dependency ───────────────────────────────

def test_no_llm_venue_or_trading_imports():
    tree = ast.parse(Path(C.__file__).read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    banned = ("trader.brain", "trader.engine", "trader.execution", "trader.kernel", "trader.data",
              "ccxt", "anthropic", "openai", "requests", "httpx", "urllib", "socket", "subprocess")
    assert not [m for m in mods if m.startswith(banned)]
