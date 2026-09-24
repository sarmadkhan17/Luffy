"""SDD-STAGE-3-ATTENTION-POSITIONING-EXTREMES-V1: synthetic, no network, LLM
or trading. Positioning (funding, ls_ratio) z-scores compete in Attention
salience; captured values are frozen into the scan input and replayed."""
import ast
import copy
import json
import sqlite3
import statistics
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trader.cognition import attention as CA
from trader.cognition.contracts import load_input
from trader.observability import attention as A, investigation as C, positioning as P
from trader.observability.collector import Collector
from trader.observability.store import Store, export_scan, read_latest
from tests.test_attention_telemetry import event, frames

TF = 14_400_000
NOW = 1_790_000_000_000 + 60_000          # one minute after a 4h close
LS = 900_000
FUND = 28_800_000
REF = [1.0, 2.0] * 10                       # mean 1.5, stdev sqrt(5/19)
SD = statistics.stdev(REF)
# evaluate_snapshot digest of the no-positioning golden event, computed with
# the pre-package code at commit f8e1fa4 (capture_ms pinned to 0.0).
GOLDEN_INPUT = "240900b2f1e69ae6939310a67910e99d7c8098c413b291d284622d52bfa533a0"
GOLDEN_OUTPUT = "ca32ed74502760fc25a3b5597b331182acbae1c271738182c0bc14ecb9974e04"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def golden():
    data = frames(6, NOW)
    data["S3/USDT"]["4h"].loc[29, "volume"] = 50_000
    ev = event("golden", NOW, data)
    ev["capture_ms"] = 0.0
    return ev


def series(sym, name, anchor, *, end=NOW, step=None, ref=REF):
    step = step or (LS if name == "ls_ratio" else FUND)
    values = list(ref) + [anchor]
    n = len(values)
    return [{"symbol": sym, "series": name, "ts": end - (n - 1 - i) * step, "value": v}
            for i, v in enumerate(values)]


def with_positioning(ev, records):
    ev = copy.deepcopy(ev)
    ev["input"]["positioning"] = records
    return ev


def rows(out):
    return {r["symbol"]: r for r in out["rows"]}


def pos_obs(out, sym, name):
    [o] = [o for o in out["observations"] if o["kind"] == "positioning"
           and o["symbol"] == sym and o["detail"]["series"] == name]
    return o


def z_of(anchor):
    return CA.rnd((anchor - statistics.fmean(REF)) / SD)


# 1 ── absence preserves behavior exactly ─────────────────────────────────

def test_no_positioning_input_is_byte_identical_to_pre_package_code(tmp_path):
    ev = golden()
    out = A.evaluate_snapshot(ev)
    assert A.digest(ev["input"]) == GOLDEN_INPUT
    assert A.digest(out) == GOLDEN_OUTPUT
    assert not any(o["kind"] == "positioning" for o in out["observations"])
    assert all("positioning" not in r and CA.POSITIONING_COMPONENT not in r.get("components", {})
               for r in out["rows"])
    store = Store(tmp_path / "attention.db", A.settings())
    store.write(dict(ev))
    payload = json.loads(sqlite3.connect(store.path).execute(
        "SELECT payload FROM scans").fetchone()[0])
    assert "positioning_input" not in payload and "positioning_capture" not in payload
    assert payload["input_hash"] == GOLDEN_INPUT
    store.close()


def test_empty_positioning_is_explicitly_missing_not_zero():
    out = A.evaluate_snapshot(with_positioning(golden(), []))
    base = A.evaluate_snapshot(golden())
    for sym, row in rows(out).items():
        if row["status"] != "ok":
            continue
        assert row["components"][CA.POSITIONING_COMPONENT] is None
        assert {v["status"] for v in row["positioning"].values()} == {"missing"}
        assert row["salience"] == rows(base)[sym]["salience"]
        assert row["rank"] == rows(base)[sym]["rank"]


# 2-5 ── z values, direction independence, competing component ───────────

def test_funding_extreme_produces_expected_z():
    anchor = 1.5 + 7 * SD
    out = A.evaluate_snapshot(with_positioning(golden(), series("S1/USDT", "funding", anchor,
                                                                  end=NOW - 1000)))
    o = pos_obs(out, "S1/USDT", "funding")
    assert o["status"] == "ok" and o["value"] == z_of(anchor)
    assert o["detail"]["anchor_ts"] == NOW - 1000 and o["detail"]["step_ms"] == FUND
    r = rows(out)["S1/USDT"]
    assert r["positioning"]["funding"]["z"] == z_of(anchor)
    assert r["positioning"]["ls_ratio"]["status"] == "missing"
    assert r["components"][CA.POSITIONING_COMPONENT] == abs(z_of(anchor))


def test_ls_ratio_extreme_produces_expected_z():
    anchor = 1.5 - 9 * SD
    out = A.evaluate_snapshot(with_positioning(golden(), series("S2/USDT", "ls_ratio", anchor)))
    o = pos_obs(out, "S2/USDT", "ls_ratio")
    assert o["status"] == "ok" and o["value"] == z_of(anchor) < 0
    assert rows(out)["S2/USDT"]["components"][CA.POSITIONING_COMPONENT] == abs(z_of(anchor))


def test_plus_and_minus_z_give_equal_positioning_salience():
    up = A.evaluate_snapshot(with_positioning(golden(), series("S1/USDT", "ls_ratio", 1.5 + 50 * SD)))
    down = A.evaluate_snapshot(with_positioning(golden(), series("S1/USDT", "ls_ratio", 1.5 - 50 * SD)))
    a, b = rows(up)["S1/USDT"], rows(down)["S1/USDT"]
    assert a["positioning"]["ls_ratio"]["z"] == -b["positioning"]["ls_ratio"]["z"]
    assert a["components"][CA.POSITIONING_COMPONENT] == b["components"][CA.POSITIONING_COMPONENT]
    assert a["salience"] == b["salience"] and a["rank"] == b["rank"]


def test_max_over_series_and_competition_with_existing_components():
    base = rows(A.evaluate_snapshot(golden()))
    big = 1.5 + 5000 * SD
    recs = series("S1/USDT", "funding", 1.5 + 3 * SD) + series("S1/USDT", "ls_ratio", big)
    out = rows(A.evaluate_snapshot(with_positioning(golden(), recs)))
    r = out["S1/USDT"]
    assert r["components"][CA.POSITIONING_COMPONENT] == abs(z_of(big))
    assert r["dominant"] == CA.POSITIONING_COMPONENT and r["salience"] == abs(z_of(big))
    assert r["rank"] == 1 and r["selected"]
    # A small positioning z does not displace the existing dominant component.
    small = rows(A.evaluate_snapshot(with_positioning(golden(), series("S3/USDT", "funding", 1.5 + SD / 10))))
    assert small["S3/USDT"]["salience"] == base["S3/USDT"]["salience"]
    assert small["S3/USDT"]["dominant"] == base["S3/USDT"]["dominant"]
    # Existing components are untouched.
    for sym, row in out.items():
        if row["status"] == "ok":
            for k in CA.COMPONENTS:
                assert row["components"][k] == base[sym]["components"][k]


def test_ties_rank_by_symbol():
    big = 1.5 + 5000 * SD
    recs = series("S4/USDT", "funding", big) + series("S2/USDT", "funding", big)
    out = rows(A.evaluate_snapshot(with_positioning(golden(), recs)))
    assert out["S2/USDT"]["salience"] == out["S4/USDT"]["salience"]
    assert (out["S2/USDT"]["rank"], out["S4/USDT"]["rank"]) == (1, 2)


# 6-8 ── eligibility ──────────────────────────────────────────────────────

def test_future_timestamps_are_excluded():
    recs = series("S1/USDT", "ls_ratio", 1.5 + 4 * SD)
    recs.append({"symbol": "S1/USDT", "series": "ls_ratio", "ts": NOW + LS, "value": 1e9})
    out = A.evaluate_snapshot(with_positioning(golden(), recs))
    o = pos_obs(out, "S1/USDT", "ls_ratio")
    assert o["status"] == "ok" and o["value"] == z_of(1.5 + 4 * SD)
    assert o["detail"]["anchor_ts"] == NOW


def test_ratio_timestamp_is_period_end_with_no_extra_delay():
    # anchor ts == as_of exactly is eligible; no +15m is required.
    out = A.evaluate_snapshot(with_positioning(golden(), series("S1/USDT", "ls_ratio", 1.5 + 4 * SD, end=NOW)))
    o = pos_obs(out, "S1/USDT", "ls_ratio")
    assert o["status"] == "ok" and o["event_ms"] == NOW and o["detail"]["age_ms"] == 0


def test_freshness_and_continuity_rules_are_explicit():
    def status(recs, sym="S1/USDT", name="ls_ratio"):
        return pos_obs(A.evaluate_snapshot(with_positioning(golden(), recs)), sym, name)["status"]
    assert status(series("S1/USDT", "ls_ratio", 3.0, end=NOW - CA.LS_RATIO_MAX_AGE_MS)) == "ok"
    assert status(series("S1/USDT", "ls_ratio", 3.0, end=NOW - CA.LS_RATIO_MAX_AGE_MS - 1)) == "stale"
    gap = series("S1/USDT", "ls_ratio", 3.0)
    del gap[5]
    gap.insert(0, dict(gap[0], ts=gap[0]["ts"] - LS))
    assert status(gap) == "gap"
    age = FUND + CA.FUNDING_AGE_SLACK_MS
    assert status(series("S1/USDT", "funding", 3.0, end=NOW - age), name="funding") == "ok"
    assert status(series("S1/USDT", "funding", 3.0, end=NOW - age - 1), name="funding") == "stale"
    # Binance settlement jitter (a few ms) rounds to the same whole minute.
    jitter = series("S1/USDT", "funding", 3.0)
    jitter[3]["ts"] += 5
    assert status(jitter, name="funding") == "ok"
    # A 4h symbol is fine; an interval change inside the window is a gap.
    assert status(series("S1/USDT", "funding", 3.0, step=TF), name="funding") == "ok"
    changed = series("S1/USDT", "funding", 3.0)
    pivot = changed[10]["ts"]                 # 4h settlements before, 8h after
    for i in range(10):
        changed[i]["ts"] = pivot - (10 - i) * TF
    assert status(changed, name="funding") == "gap"


# 9-12 ── fail closed per series ──────────────────────────────────────────

def test_insufficient_history_is_explicit_none():
    recs = series("S1/USDT", "funding", 3.0, ref=REF[:19])
    out = A.evaluate_snapshot(with_positioning(golden(), recs))
    o = pos_obs(out, "S1/USDT", "funding")
    assert o["status"] == "insufficient_history" and o["value"] is None
    assert o["detail"]["samples"] == 20 and o["detail"]["need"] == 21
    assert rows(out)["S1/USDT"]["components"][CA.POSITIONING_COMPONENT] is None


def test_zero_variance_is_explicit_none():
    out = A.evaluate_snapshot(with_positioning(golden(), series("S1/USDT", "funding", 5.0, ref=[1.0] * 20)))
    o = pos_obs(out, "S1/USDT", "funding")
    assert o["status"] == "zero_variance" and o["value"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "1.0", True])
def test_non_finite_values_are_rejected_and_fail_the_series(bad):
    recs = series("S1/USDT", "funding", 3.0)
    recs[4]["value"] = bad
    recs += series("S1/USDT", "ls_ratio", 1.5 + 6 * SD)
    ds = load_input(with_positioning(golden(), recs)["input"])
    assert {"section": "positioning", "index": 4, "reason": "non_finite"} in ds.rejected
    out = A.evaluate_snapshot(with_positioning(golden(), recs))
    r = rows(out)["S1/USDT"]
    assert r["positioning"]["funding"] == {"status": "invalid", "z": None,
                                           "obs_id": r["positioning"]["funding"]["obs_id"]}
    assert pos_obs(out, "S1/USDT", "funding")["detail"]["reason"] == "non_finite"
    # the other series is still used
    assert r["components"][CA.POSITIONING_COMPONENT] == abs(z_of(1.5 + 6 * SD))


@pytest.mark.parametrize("field,value,reason", [
    ("ts", -1, "bad_timestamp"), ("ts", 1.5, "bad_timestamp"), ("ts", None, "bad_timestamp")])
def test_invalid_timestamps_are_rejected_and_fail_the_series(field, value, reason):
    recs = series("S1/USDT", "ls_ratio", 3.0)
    recs[2][field] = value
    out = A.evaluate_snapshot(with_positioning(golden(), recs))
    assert {"section": "positioning", "index": 2, "reason": reason} in out["rejected_inputs"]
    assert pos_obs(out, "S1/USDT", "ls_ratio")["status"] == "invalid"


def test_conflicting_revision_rejected_identical_duplicate_harmless():
    recs = series("S1/USDT", "ls_ratio", 3.0)
    dup = recs + [dict(recs[7])]
    out = A.evaluate_snapshot(with_positioning(golden(), dup))
    assert {"section": "positioning", "index": 21, "reason": "duplicate"} in out["rejected_inputs"]
    assert pos_obs(out, "S1/USDT", "ls_ratio")["status"] == "ok"
    clash = recs + [dict(recs[7], value=recs[7]["value"] + 1)]
    out = A.evaluate_snapshot(with_positioning(golden(), clash))
    assert {"section": "positioning", "index": 21, "reason": "conflicting_revision"} in out["rejected_inputs"]
    o = pos_obs(out, "S1/USDT", "ls_ratio")
    assert o["status"] == "invalid" and o["detail"]["reason"] == "conflicting_revision"


def test_malformed_records_and_structure():
    recs = [{"symbol": "S1/USDT", "series": "oi", "ts": NOW, "value": 1.0},
            {"symbol": "S1/USDT", "series": "funding", "ts": NOW}]
    ds = load_input(with_positioning(golden(), recs)["input"])
    assert [r["reason"] for r in ds.rejected] == ["unknown_series", "missing_field"]
    assert ds.positioning_invalid == {("S1/USDT", "funding"): "missing_field"}
    with pytest.raises(ValueError, match="positioning must be a list"):
        load_input(with_positioning(golden(), {"S1/USDT": []})["input"])


# 13-15 ── capture: faults, bounds, no network ───────────────────────────

def derivs_db(path, rows_per_series=60, symbols=("S0/USDT", "S1/USDT")):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE derivs (symbol TEXT NOT NULL, series TEXT NOT NULL, "
               "ts INTEGER NOT NULL, value REAL, PRIMARY KEY (symbol, series, ts))")
    for sym in symbols:
        for name, step in (("funding", FUND), ("ls_ratio", LS), ("oi", LS), ("taker_ratio", LS)):
            for i in range(rows_per_series):
                db.execute("INSERT INTO derivs VALUES (?,?,?,?)",
                           (sym, name, NOW - i * step, 1.0 + (i % 2)))
            # a future row that must never be read
            db.execute("INSERT INTO derivs VALUES (?,?,?,?)", (sym, name, NOW + step, 99.0))
    db.commit()
    db.close()
    return path


def test_read_is_bounded_per_symbol_and_series_and_excludes_future(tmp_path):
    path = derivs_db(tmp_path / "derivs.db")
    records, prov = P.read(path, ["S1/USDT", "S0/USDT", "S1/USDT", "ZZ/USDT"], NOW)
    assert prov["status"] == "ok" and prov["limit_per_series"] == 21 == P.LIMIT
    by = {}
    for r in records:
        by.setdefault((r["symbol"], r["series"]), []).append(r["ts"])
    assert set(by) == {(s, n) for s in ("S0/USDT", "S1/USDT") for n in ("funding", "ls_ratio")}
    assert all(len(v) == 21 and v == sorted(v) and max(v) <= NOW for v in by.values())
    assert prov["symbols"] == ["S0/USDT", "S1/USDT", "ZZ/USDT"]
    assert prov["eligibility"] == "ts <= as_of_ms" and "no claim" in prov["pit"]
    many, prov = P.read(path, [f"X{i}" for i in range(500)], NOW)
    assert many == [] and len(prov["symbols"]) == P.MAX_SYMBOLS


@pytest.mark.parametrize("make", ["missing", "corrupt", "no_table", "timeout"])
def test_db_fault_records_positioning_unavailable_and_scan_continues(tmp_path, make):
    path = tmp_path / "derivs.db"
    kw = {}
    if make == "corrupt":
        path.write_bytes(b"not a sqlite database" * 100)
    elif make == "no_table":
        sqlite3.connect(path).close()
    elif make == "timeout":
        derivs_db(path)
        kw["deadline_seconds"] = -1
    ev = P.attach(golden(), path, **kw)
    assert "positioning" not in ev["input"]
    assert ev["positioning_capture"]["status"] == "positioning_unavailable"
    assert {"symbol": "*", "reason": "positioning_unavailable",
            "detail": ev["positioning_capture"]["reason"]} in ev["issues"]
    out = A.evaluate_snapshot(ev)
    assert [r for r in out["rows"] if r.get("selected")]
    base = A.evaluate_snapshot(golden())
    assert out["rows"] == base["rows"] and out["observations"] == base["observations"]


def test_worker_child_attaches_positioning_and_survives_faults(tmp_path):
    derivs_db(tmp_path / "derivs.db", symbols=[f"S{j}/USDT" for j in range(6)])
    ok = Collector(tmp_path / "a", start=False, positioning_path=tmp_path / "derivs.db")
    ok._run(event("w1", NOW, frames(6, NOW)))
    scan = read_latest(ok.path, now_ms=NOW)["scan"]
    assert scan["positioning_capture"]["status"] == "ok"
    assert len(scan["positioning_input"]) == 6 * 2 * 21
    assert any(o["kind"] == "positioning" for o in scan["observations"])
    bad = Collector(tmp_path / "b", start=False, positioning_path=tmp_path / "absent.db")
    bad._run(event("w2", NOW, frames(6, NOW)))
    scan = read_latest(bad.path, now_ms=NOW)["scan"]
    assert scan["positioning_capture"]["reason"] == "FileNotFoundError"
    assert "positioning_input" not in scan and scan["rows"]
    off = Collector(tmp_path / "c", start=False)
    off._run(event("w3", NOW, frames(6, NOW)))
    scan = read_latest(off.path, now_ms=NOW)["scan"]
    assert "positioning_capture" not in scan and "positioning_input" not in scan


def test_producer_never_reads_derivs(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "read", lambda *a, **k: pytest.fail("producer read derivs"))
    c = Collector(tmp_path, start=False, positioning_path=tmp_path / "derivs.db")
    assert c.begin(frames(6, NOW), list(frames(6, NOW)), NOW)
    ev = c.queue.get_nowait()
    assert "positioning" not in ev["input"]


def test_capture_module_has_no_network_or_recorder_imports():
    tree = ast.parse(Path(P.__file__).read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert names <= {"__future__", "math", "sqlite3", "time", "pathlib",
                     "trader.cognition.attention", "trader.cognition.contracts"}


# 16 ── replay consumes the captured input only ──────────────────────────

def publish(tmp_path, derivs):
    path = tmp_path / "attention.db"
    data = frames(6, NOW)
    ev = P.attach(event("r1", NOW, data), derivs)
    ident = dict(schema="attention-scan-identity.v1", instance_id="1" * 32, seq=1)
    store = Store(path, A.settings())
    with patch("trader.observability.store.time", SimpleNamespace(time=lambda: NOW / 1000)):
        store.write(dict(copy.deepcopy(ev), identity=ident))
        store.write({"kind": "causes", "scan_id": "r1", "as_of_ms": NOW, "identity": ident,
                     "items": [{"symbol": s, "decision_id": "d_" + s} for s in data]})
    store.close()
    return path, ev


def test_replay_uses_captured_positioning_not_live_db(tmp_path, monkeypatch):
    db = tmp_path / "derivs.db"
    derivs_db(db, symbols=[f"S{j}/USDT" for j in range(6)])
    with sqlite3.connect(db) as c:          # make S1 ls_ratio extreme at its anchor
        c.execute("UPDATE derivs SET value=1e6 WHERE symbol='S1/USDT' AND series='ls_ratio' AND ts=?", (NOW,))
    path, ev = publish(tmp_path, db)
    # Live source changes or disappears; replay must not notice.
    db.unlink()
    monkeypatch.setattr(P, "read", lambda *a, **k: pytest.fail("replay re-queried derivs"))
    export_scan(path, "r1", tmp_path / "export.json")
    out = json.loads((tmp_path / "export.json").read_text())
    scan = out["scan"]
    assert scan["positioning_input"] == ev["input"]["positioning"]
    assert A.digest(ev["input"]) == scan["input_hash"]
    replayed = A.evaluate_snapshot(ev)
    assert replayed["rows"] == scan["rows"] and replayed["observations"] == scan["observations"]
    assert rows(replayed)["S1/USDT"]["dominant"] == CA.POSITIONING_COMPONENT
    # The investigation adapter recomputes from persisted versions + captured positioning.
    snap = C.adapt(C.source_snapshot(path), NOW)
    assert snap.result["universe"] == scan["rows"]


def test_positioning_dominant_selection_gets_explicit_investigation_skip(tmp_path):
    db = tmp_path / "derivs.db"
    derivs_db(db, symbols=[f"S{j}/USDT" for j in range(6)])
    with sqlite3.connect(db) as c:
        c.execute("UPDATE derivs SET value=1e6 WHERE symbol='S1/USDT' AND series='ls_ratio' AND ts=?", (NOW,))
    path, _ = publish(tmp_path, db)
    path.with_name("attention_health.json").write_text(json.dumps({
        "health_schema": "attention-collector-health.v2", "instance_id": "1" * 32,
        "updated_ms": NOW, "status": "ok", "worker_alive": True, "errors": 0,
        "capture_errors": 0, "worker_errors": 0, "dropped": 0, "details_lost": 0,
        "process_started_ms": 0, "failure_generation": 0, "fence_seq": 0, "certificate": None,
        "last_error": None, "first_error_ms": None, "last_error_ms": None,
        "last_complete": {"scan_id": "r1", "seq": 1}}))
    detail = C.step(path, tmp_path / "investigation.db", NOW)
    assert detail["skipped"].get("no_investigation_family") == 1
    with sqlite3.connect(tmp_path / "investigation.db") as c:
        assert not c.execute("SELECT 1 FROM cases WHERE symbol='S1/USDT'").fetchone()
