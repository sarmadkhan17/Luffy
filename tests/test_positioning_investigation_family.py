"""SDD-STAGE-3-ATTENTION-POSITIONING-INVESTIGATION-FAMILY-V1: a positioning-
dominant Attention candidate opens a deterministic, direction-free investigation
from captured scan evidence only. Synthetic; no network, LLM or trading."""
import ast
import copy
import dataclasses
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trader.cognition import attention as CA, investigation as I
from trader.observability import attention as A, investigation as C, memory as S, positioning as P
from trader.observability.store import Store
from tests.test_attention_telemetry import event, frames
from tests.test_cognition_attention_positioning import NOW, SD, series

SYM = "S1/USDT"
HEALTH = {"health_schema": "attention-collector-health.v2", "instance_id": "1" * 32,
          "updated_ms": NOW, "status": "ok", "worker_alive": True, "errors": 0,
          "capture_errors": 0, "worker_errors": 0, "dropped": 0, "details_lost": 0,
          "process_started_ms": 0, "failure_generation": 0, "fence_seq": 0, "certificate": None,
          "last_error": None, "first_error_ms": None, "last_error_ms": None,
          "last_complete": {"scan_id": "r1", "seq": 1}}
# The frozen v2 catalog id: existing families' registrations are unchanged.
V2_CATALOG_ID = I.CATALOG_ID


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(P, "read", lambda *a, **k: pytest.fail("derivs.db queried"))


def publish(tmp_path, records=None, name="attention.db", spike=False):
    """A persisted scan whose positioning is the captured input only; no derivs.db exists."""
    path = tmp_path / name
    data = frames(6, NOW)
    if spike:                                       # the golden volume-anomaly candidate
        data["S3/USDT"]["4h"].loc[29, "volume"] = 50_000
    ev = event("r1", NOW, data)
    if records is not None:
        ev["input"]["positioning"] = records
    ident = dict(schema="attention-scan-identity.v1", instance_id="1" * 32, seq=1)
    store = Store(path, A.settings())
    with patch("trader.observability.store.time", SimpleNamespace(time=lambda: NOW / 1000)):
        store.write(dict(copy.deepcopy(ev), identity=ident))
        store.write({"kind": "causes", "scan_id": "r1", "as_of_ms": NOW, "identity": ident,
                     "items": [{"symbol": s, "decision_id": "d_" + s} for s in data]})
    store.close()
    path.with_name("attention_health.json").write_text(json.dumps(HEALTH))
    return path


def run(tmp_path, records, ledger="investigation.db", spike=False):
    path = publish(tmp_path, records, spike=spike)
    dest = tmp_path / ledger
    detail = C.step(path, dest, NOW)
    return path, dest, detail


def case(dest, symbol=SYM):
    with sqlite3.connect(dest) as db:
        row = db.execute("SELECT id,payload FROM cases WHERE symbol=?", (symbol,)).fetchone()
        assert row, "no case registered"
        upd = db.execute("SELECT payload FROM updates WHERE case_id=?", (row[0],)).fetchall()
    return I.investigation_from_dict(json.loads(row[1])), [I.update_from_dict(json.loads(u)) for (u,) in upd]


def pos_dims(inv):
    return {d.name: d for d in inv.state.dimensions if d.name.startswith("positioning")}


FUND_UP = series(SYM, "funding", 1.5 + 50 * SD, end=NOW - 1000)
FUND_DN = series(SYM, "funding", 1.5 - 50 * SD, end=NOW - 1000)
LS_UP = series(SYM, "ls_ratio", 1.5 + 30 * SD)


# 1, 2, 4 ── maps to the family, no skip, payload from captured evidence ──

def test_positioning_dominant_candidate_registers_positioning_family(tmp_path):
    path, dest, detail = run(tmp_path, FUND_UP + LS_UP)
    scan = json.loads(sqlite3.connect(path).execute("SELECT payload FROM scans").fetchone()[0])
    row = {r["symbol"]: r for r in scan["rows"]}[SYM]
    assert row["dominant"] == CA.POSITIONING_COMPONENT == I.POSITIONING_FAMILY and row["selected"]
    assert "no_investigation_family" not in detail["skipped"] and detail["registered"] >= 1
    inv, [update] = case(dest)
    assert inv.primary_trigger == I.POSITIONING_FAMILY
    assert inv.question == I.QUESTIONS[I.POSITIONING_FAMILY]
    assert inv.measurement.catalog_id == I.POSITIONING_CATALOG_ID != V2_CATALOG_ID
    assert inv.measurement.sign == 0 and inv.measurement.target_keys == ()
    dims = pos_dims(inv)
    # Signed z and obs identity are exactly the captured scan's observations.
    for s in ("funding", "ls_ratio"):
        [o] = [o for o in scan["observations"] if o["kind"] == "positioning"
               and o["symbol"] == SYM and o["detail"]["series"] == s]
        assert (dims["positioning:" + s].value, dims["positioning:" + s].status,
                dims["positioning:" + s].evidence_id) == (o["value"], "ok", o["obs_id"])
        assert o["obs_id"] in inv.state.evidence_ids
    top = dims[I.POSITIONING_FAMILY]
    assert top.value == row["components"][I.POSITIONING_FAMILY]
    assert top.rule == "max_abs_positioning_z:funding"
    assert top.evidence_id == dims["positioning:funding"].evidence_id
    assert I.positioning_evidence(inv.state) == ({"funding": dims["positioning:funding"].value,
                                                  "ls_ratio": dims["positioning:ls_ratio"].value}, "funding")
    assert update.evidence.status == "assessed" and update.evidence.score is None
    assert dict(update.assessment)[
        "distinct_supported" if any(abs(d.value) >= I.CATALOG["thresholds"][d.name]
            for d in inv.state.dimensions if d.name in I.FAMILIES and d.status == "ok")
        else "distinct_unsupported"] == "compatible"
    with sqlite3.connect(dest) as db:
        assert db.execute("SELECT terminal_ms FROM cases WHERE id=?", (inv.investigation_id,)).fetchone()[0] == NOW


def test_old_skip_reason_is_gone_for_positioning_dominance(tmp_path):
    _, _, detail = run(tmp_path, LS_UP)
    assert detail["skipped"] == {} and detail["registered"] >= 1


# 3 ── existing families map exactly as before ───────────────────────────

def test_existing_families_unchanged(tmp_path):
    assert I.FAMILIES == ("volume_anomaly", "volatility_transition", "relative_return_divergence")
    assert I.CATALOG_ID == I.stable_id("catalog", I.CATALOG) and I.POSITIONING_FAMILY not in I.CATALOG["thresholds"]
    path, dest, _ = run(tmp_path, None, spike=True)
    snap = C.adapt(C.source_snapshot(path), NOW)
    with sqlite3.connect(dest) as db:
        rows = db.execute("SELECT symbol,payload FROM cases").fetchall()
    assert rows
    for symbol, payload in rows:
        inv = I.investigation_from_dict(json.loads(payload))
        fam = snap.result["rows"][symbol]["dominant"]
        assert inv.primary_trigger == fam in I.FAMILIES
        assert not pos_dims(inv) and inv.measurement.catalog_id == V2_CATALOG_ID
        # Default (family-less) state is what existing families were always given.
        assert inv.state == snap.state(symbol) == snap.state(symbol, fam)
        assert inv == I.open_investigation(snap.state(symbol), fam, snap.bars, NOW)


def test_positioning_state_is_added_only_for_the_positioning_family(tmp_path):
    path = publish(tmp_path, FUND_UP)
    snap = C.adapt(C.source_snapshot(path), NOW)
    other = next(s for s in snap.result["cohort"] if s != SYM)
    assert not any(d.name.startswith("positioning") for d in snap.state(other).dimensions)
    assert pos_dims(SimpleNamespace(state=I.make_state(snap.scan, snap.result, SYM, snap.bars, NOW, I.POSITIONING_FAMILY)))


# 5 ── replay without derivs.db ──────────────────────────────────────────

def test_replay_and_archive_export_without_derivs_db(tmp_path):
    path, dest, _ = run(tmp_path, FUND_UP + LS_UP)
    assert not list(tmp_path.glob("derivs*"))
    inv, [update] = case(dest)
    snap = C.adapt(C.source_snapshot(path), NOW)
    assert I.open_investigation(snap.state(SYM, I.POSITIONING_FAMILY), I.POSITIONING_FAMILY, snap.bars, NOW) == inv
    with sqlite3.connect(dest) as db:
        archive = S.export_case(db, inv.investigation_id)
    path.unlink()                                   # source attention store gone too
    body = S.replay_export(json.loads(I.encode(archive)))
    assert body["source"]["investigation"]["primary_trigger"] == I.POSITIONING_FAMILY
    assert I.advance(inv, I.measure(inv, (), NOW, NOW)) == update


# 6, 7 ── single-series evidence ─────────────────────────────────────────

@pytest.mark.parametrize("records,valid,missing", [
    (FUND_UP, "funding", "ls_ratio"), (LS_UP, "ls_ratio", "funding")])
def test_single_series_positioning_is_recorded_truthfully(tmp_path, records, valid, missing):
    _, dest, _ = run(tmp_path, records)
    inv, [update] = case(dest)
    dims = pos_dims(inv)
    assert dims["positioning:" + valid].status == "ok"
    assert dims["positioning:" + missing].status == "missing" and dims["positioning:" + missing].value is None
    assert "positioning:" + missing in inv.state.missing
    assert set(I.positioning_evidence(inv.state)[0]) == {valid}
    assert dims[I.POSITIONING_FAMILY].rule == "max_abs_positioning_z:" + valid
    assert "positioning_series_disagree" not in inv.state.contradictions
    assert update.evidence.status == "assessed"


# 8, 9 ── symmetry and no direction ──────────────────────────────────────

def structure(inv, update):
    return (inv.primary_trigger, inv.question, inv.alternatives and tuple(
                (a.name, a.prediction, a.invalidator, a.status) for a in inv.alternatives),
            dataclasses.replace(inv.measurement, deadline_ms=0, expires_ms=0),
            tuple((d.name, d.status, None if d.value is None else abs(d.value), d.rule)
                  for d in inv.state.dimensions),
            inv.state.contradictions, inv.state.missing,
            update.evidence.status, update.evidence.reason, update.assessment, update.next_action)


def test_positive_and_negative_equal_abs_z_are_symmetric(tmp_path):
    up_dir, dn_dir = tmp_path / "up", tmp_path / "dn"
    up_dir.mkdir(); dn_dir.mkdir()
    _, up_dest, _ = run(up_dir, FUND_UP)
    _, dn_dest, _ = run(dn_dir, FUND_DN)
    up, [uu] = case(up_dest)
    dn, [du] = case(dn_dest)
    assert pos_dims(up)["positioning:funding"].value == -pos_dims(dn)["positioning:funding"].value > 0
    assert structure(up, uu) == structure(dn, du)


def test_no_directional_conclusion_is_embedded(tmp_path):
    _, dest, _ = run(tmp_path, FUND_DN + LS_UP)
    inv, [update] = case(dest)
    text = I.encode([I.POSITIONING_CATALOG, inv.question, dataclasses.asdict(inv.measurement),
                     [dataclasses.asdict(a) for a in inv.alternatives],
                     dataclasses.asdict(update.next_action), update.assessment,
                     update.evidence.reason]).lower()
    for word in ("bull", "bear", "long", "short", "buy", "sell", "revers", "continu", "fade",
                 "crowd", "squeeze", "same_direction", "opposite"):
        assert word not in text, word
    assert inv.measurement.sign == 0 and I.POSITIONING_CATALOG["direction"] is None


# 10 ── malformed / missing evidence fails closed ────────────────────────

def _state(tmp_path):
    snap = C.adapt(C.source_snapshot(publish(tmp_path, FUND_UP + LS_UP)), NOW)
    return snap, snap.state(SYM, I.POSITIONING_FAMILY)


def _with(state, fn):
    return dataclasses.replace(state, dimensions=tuple(fn(d) for d in state.dimensions if fn(d) is not None))


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: None if d.name.startswith("positioning") else d, "positioning_evidence_missing"),
    (lambda d: dataclasses.replace(d, status="stale", value=None) if d.name.startswith("positioning:") else d,
     "positioning_evidence_missing"),
    (lambda d: dataclasses.replace(d, value=None) if d.name == "positioning:funding" else d,
     "positioning_evidence_malformed"),
    (lambda d: dataclasses.replace(d, value=float("nan")) if d.name == "positioning:ls_ratio" else d,
     "positioning_evidence_malformed"),
    (lambda d: dataclasses.replace(d, name="positioning:oi") if d.name == "positioning:ls_ratio" else d,
     "positioning_evidence_malformed"),
    (lambda d: dataclasses.replace(d, value=d.value + 1) if d.name == I.POSITIONING_FAMILY else d,
     "positioning_extreme_inconsistent"),
    (lambda d: None if d.name == I.POSITIONING_FAMILY else d, "positioning_extreme_inconsistent"),
    (lambda d: dataclasses.replace(d, evidence_id="x") if d.name == I.POSITIONING_FAMILY else d,
     "positioning_extreme_inconsistent"),
])
def test_malformed_positioning_evidence_fails_closed(tmp_path, mutate, reason):
    snap, state = _state(tmp_path)
    with pytest.raises(ValueError, match=reason):
        I.open_investigation(_with(state, mutate), I.POSITIONING_FAMILY, snap.bars, NOW)


def test_step_records_explicit_fail_closed_reason_and_no_case(tmp_path, monkeypatch):
    path = publish(tmp_path, FUND_UP)
    real = C.Snapshot.state

    def broken(self, symbol, family=None):
        st = real(self, symbol, family)
        return _with(st, lambda d: None if d.name.startswith("positioning:") else d)
    monkeypatch.setattr(C.Snapshot, "state", broken)
    detail = C.step(path, tmp_path / "investigation.db", NOW)
    assert detail["skipped"] == {"positioning_evidence_missing": 1}
    with sqlite3.connect(tmp_path / "investigation.db") as db:
        assert not db.execute("SELECT 1 FROM cases WHERE symbol=?", (SYM,)).fetchone()


def test_not_distinct_and_missing_concurrent_evidence_are_explicit(tmp_path):
    snap, state = _state(tmp_path)
    inv = I.open_investigation(state, I.POSITIONING_FAMILY, snap.bars, NOW)
    high = dataclasses.replace(inv, measurement=dataclasses.replace(inv.measurement, threshold=1e9))
    assert dict(I.advance(high, I.measure(high, (), NOW, NOW)).assessment)["not_distinct"] == "compatible"
    bare = dataclasses.replace(inv, state=_with(inv.state, lambda d: None if d.name in I.FAMILIES else d))
    ev = I.measure(bare, (), NOW, NOW)
    assert (ev.status, ev.reason) == ("not_testable", "concurrent_evidence_missing")


# 11, 12 ── deterministic registration and allocation idempotence ────────

def test_registration_is_deterministic_across_retry_and_restart(tmp_path):
    path, dest, first = run(tmp_path, FUND_UP)
    inv, updates = case(dest)
    shutil.copy(dest, tmp_path / "copy.db")
    again = C.step(path, dest, NOW)                 # retry: allocation already executed
    assert again["registered"] == 0 and again["allocation"]["reused"]
    assert case(dest) == (inv, updates)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _, dest2, second = run(fresh, FUND_UP)          # restart from nothing, same inputs
    assert case(dest2) == (inv, updates)
    assert first["allocation"]["selected"] == second["allocation"]["selected"]


# 13 ── ranking / top-k / salience untouched ─────────────────────────────

def test_attention_selection_is_unchanged_by_registration(tmp_path):
    path, _, detail = run(tmp_path, FUND_UP)
    scan = json.loads(sqlite3.connect(path).execute("SELECT payload FROM scans").fetchone()[0])
    selected = [r["symbol"] for r in sorted(scan["rows"], key=lambda r: r.get("rank") or 1e9) if r.get("selected")]
    assert SYM in selected and detail["allocation"]["legacy_selected"] == selected


# 14 ── no network / LLM / trading dependency ────────────────────────────

def test_investigation_module_imports_are_unchanged():
    tree = ast.parse(Path(I.__file__).read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert names == {"__future__", "dataclasses", "json", "math", "statistics", "trader.cognition.contracts"}
