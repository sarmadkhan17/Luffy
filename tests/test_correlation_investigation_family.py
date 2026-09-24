"""SDD-STAGE-3-ATTENTION-CORRELATION-CHANGE-V1: a correlation_change-dominant
Attention candidate opens a deterministic, direction-free investigation from
captured scan evidence only. Synthetic; no network, LLM, price query or trading."""
import ast
import dataclasses
import json
import math
import sqlite3
from pathlib import Path

import pytest

from trader.cognition import attention as CA, investigation as I
from trader.observability import attention as A, investigation as C, memory as S
from tests._corr_synth import NOW, SYM, publish, universe

# Catalog ids at the pre-package commit: existing registrations are unchanged.
V2_CATALOG_ID = "catalog_9777745b96669be5"
POSITIONING_CATALOG_ID = "catalog_33057b509ce181d6"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(A, "_history", lambda *a: pytest.fail("price history queried"))


def run(tmp_path, data=None, ledger="investigation.db", mutate=None):
    path = _publish(tmp_path, data, mutate)
    dest = tmp_path / ledger
    return path, dest, C.step(path, dest, NOW)


_REAL_HISTORY = A._history


def _publish(tmp_path, data=None, mutate=None):
    """Capture (the producer) may read frames; everything after it may not."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(A, "_history", _REAL_HISTORY)
        return publish(tmp_path, data, mutate=mutate)


def case(dest, symbol=SYM):
    with sqlite3.connect(dest) as db:
        row = db.execute("SELECT id,payload FROM cases WHERE symbol=?", (symbol,)).fetchone()
        assert row, "no case registered"
        upd = db.execute("SELECT payload FROM updates WHERE case_id=?", (row[0],)).fetchall()
    return I.investigation_from_dict(json.loads(row[1])), [I.update_from_dict(json.loads(u)) for (u,) in upd]


def corr_dims(inv):
    return {d.name: d for d in inv.state.dimensions if d.name.startswith(I.CORRELATION_FAMILY)}


# ── maps to the family; never no_investigation_family ────────────────────

def test_correlation_dominant_candidate_registers_correlation_family(tmp_path):
    path, dest, detail = run(tmp_path)
    scan = json.loads(sqlite3.connect(path).execute("SELECT payload FROM scans").fetchone()[0])
    row = {r["symbol"]: r for r in scan["rows"]}[SYM]
    assert row["dominant"] == CA.CORRELATION_COMPONENT == I.CORRELATION_FAMILY and row["selected"]
    assert "no_investigation_family" not in detail["skipped"] and detail["registered"] >= 1
    inv, [update] = case(dest)
    assert inv.primary_trigger == I.CORRELATION_FAMILY
    assert inv.question == I.QUESTIONS[I.CORRELATION_FAMILY] == (
        "How did this asset's contemporaneous correlation with the same captured peer basket differ "
        "between the baseline and recent windows? What captured evidence distinguishes an asset-specific "
        "change, a shared cohort change, or an unreliable comparison?")
    assert inv.secondary_questions == ()
    assert inv.measurement.catalog_id == I.CORRELATION_CATALOG_ID not in (V2_CATALOG_ID, POSITIONING_CATALOG_ID)
    assert inv.measurement.sign == 0 and inv.measurement.target_keys == ()
    assert inv.measurement.threshold == I.CORRELATION_CATALOG["threshold"] == 2.0
    assert I.CORRELATION_CATALOG["threshold_basis"] == "package-owned Attention/assessment policy"
    assert I.CORRELATION_CATALOG["reference"] == "captured leave-one-out peer basket"
    [o] = [o for o in scan["observations"] if o["kind"] == CA.CORRELATION_COMPONENT and o["symbol"] == SYM]
    dims = corr_dims(inv)
    signed = dims[SIGNED]
    assert (signed.value, signed.status, signed.evidence_id) == (o["value"], "ok", o["obs_id"])
    assert o["obs_id"] in inv.state.evidence_ids
    # Both endpoints are carried into the frozen state from the same observation.
    assert (dims[I.CORR_R_BASELINE].value, dims[I.CORR_R_RECENT].value) == \
           (o["detail"]["r_baseline"], o["detail"]["r_recent"])
    assert {dims[I.CORR_R_BASELINE].evidence_id, dims[I.CORR_R_RECENT].evidence_id} == {o["obs_id"]}
    basket = dims[I.CORR_BASKET]
    assert basket.value == o["detail"]["reference_size"]
    assert basket.rule == "leave_one_out_equal_weight:" + o["detail"]["reference_membership_id"]
    assert sorted(n[len(I.CORR_PEER):] for n in dims if n.startswith(I.CORR_PEER)) == o["detail"]["reference_peers"]
    claim = dims[I.CORRELATION_FAMILY]
    assert claim.value == row["components"][I.CORRELATION_FAMILY] == abs(o["value"])
    ev = I.correlation_evidence(inv.state)
    assert claim.evidence_id == o["obs_id"] and ev["signed"] == o["value"] and ev["change"] == abs(o["value"])
    # Registration-only assessment: closes at once, no forward score.
    assert update.evidence.status == "assessed" and update.evidence.score is None
    # The synthetic candidate tracked its basket, then stopped: a positive
    # relationship weakened without crossing zero.
    assert dict(update.assessment)["relationship_decreased"] == "compatible"
    assert {"correlation_decreased", "baseline_relationship_positive",
            "relationship_magnitude_decreased"} <= set(update.reason_codes)
    # Endpoints, signed change, absolute score, membership and windows are kept.
    case_dims = corr_dims(inv)
    assert case_dims[I.CORR_R_BASELINE].value > case_dims[I.CORR_R_RECENT].value
    assert case_dims[SIGNED].value < 0 and case_dims[I.CORRELATION_FAMILY].value == abs(case_dims[SIGNED].value)
    assert o["detail"]["baseline_return_bar_open_ms"] and o["detail"]["recent_return_bar_open_ms"]
    assert update.next_action.kind == "RESEARCH" and "not prospective validation" in update.next_action.test
    with sqlite3.connect(dest) as db:
        assert db.execute("SELECT terminal_ms FROM cases WHERE id=?", (inv.investigation_id,)).fetchone()[0] == NOW
        catalog = db.execute("SELECT payload FROM protocols WHERE id=?", (I.CORRELATION_CATALOG_ID,)).fetchone()
    assert json.loads(catalog[0]) == I.CORRELATION_CATALOG


def test_no_candidate_is_skipped_for_missing_family(tmp_path):
    _, _, detail = run(tmp_path)
    assert "no_investigation_family" not in detail["skipped"]
    assert I.CORRELATION_FAMILY in I.REGISTRATION_FAMILIES


def test_scan_before_protocol_activation_waits(tmp_path):
    path = _publish(tmp_path)
    dest = tmp_path / "investigation.db"
    db = C.ledger(dest)
    for cid, at, cat in ((I.CATALOG_ID, NOW, I.CATALOG),
                         (I.POSITIONING_CATALOG_ID, NOW, I.POSITIONING_CATALOG),
                         (I.CORRELATION_CATALOG_ID, NOW + 1, I.CORRELATION_CATALOG)):
        db.execute("INSERT INTO protocols VALUES (?,?,?)", (cid, at, I.encode(cat)))
    db.commit(); db.close()
    detail = C.step(path, dest, NOW + 1)
    assert detail["skipped"].get("awaiting_post_activation_scan") == 1, detail


# ── existing families and catalogs unchanged ─────────────────────────────

def test_existing_families_and_catalogs_unchanged(tmp_path):
    assert I.FAMILIES == ("volume_anomaly", "volatility_transition", "relative_return_divergence")
    assert I.CATALOG_ID == V2_CATALOG_ID and I.POSITIONING_CATALOG_ID == POSITIONING_CATALOG_ID
    path = _publish(tmp_path)
    snap = C.adapt(C.source_snapshot(path), NOW)
    # Family-less and other-family states carry no correlation dimensions,
    # so existing families' states and ids are exactly what they were.
    for fam in (None, *I.FAMILIES, I.POSITIONING_FAMILY):
        assert not any(d.name.startswith(I.CORRELATION_FAMILY) for d in snap.state(SYM, fam).dimensions)
    assert snap.state(SYM) == snap.state(SYM, "volume_anomaly")
    assert any(d.name == SIGNED for d in snap.state(SYM, I.CORRELATION_FAMILY).dimensions)


# ── replay / archive from captured evidence only ─────────────────────────

def test_replay_and_archive_export_without_price_source(tmp_path):
    path, dest, _ = run(tmp_path)
    inv, [update] = case(dest)
    snap = C.adapt(C.source_snapshot(path), NOW)          # A._history is fail-patched
    assert I.open_investigation(snap.state(SYM, I.CORRELATION_FAMILY), I.CORRELATION_FAMILY,
                                snap.bars, NOW) == inv
    with sqlite3.connect(dest) as db:
        archive = S.export_case(db, inv.investigation_id)
    path.unlink()
    body = S.replay_export(json.loads(I.encode(archive)))
    assert body["source"]["investigation"]["primary_trigger"] == I.CORRELATION_FAMILY
    assert I.advance(inv, I.measure(inv, (), NOW, NOW)) == update


# ── symmetry and no direction ────────────────────────────────────────────

def structure(inv, update):
    return (inv.primary_trigger, inv.question,
            tuple((a.name, a.prediction, a.invalidator, a.status) for a in inv.alternatives),
            dataclasses.replace(inv.measurement, deadline_ms=0, expires_ms=0),
            corr_dims(inv)[I.CORRELATION_FAMILY].value,
            update.evidence.status, update.evidence.reason, update.next_action)


def test_breakdown_and_convergence_of_equal_magnitude_are_symmetric(tmp_path):
    (tmp_path / "dn").mkdir(); (tmp_path / "up").mkdir()
    _, dn_dest, _ = run(tmp_path / "dn", universe(sign=1))
    _, up_dest, _ = run(tmp_path / "up", universe(sign=-1))
    dn, [du] = case(dn_dest)
    up, [uu] = case(up_dest)
    sd = corr_dims(dn)[SIGNED].value
    su = corr_dims(up)[SIGNED].value
    assert sd == -su < 0
    assert structure(dn, du) == structure(up, uu)
    # Mirrored endpoints give the mirrored description; nothing else differs.
    assert dict(du.assessment)["relationship_decreased"] == "compatible"
    assert dict(uu.assessment)["relationship_increased"] == "compatible"


def test_no_directional_or_significance_claim_is_embedded(tmp_path):
    _, dest, _ = run(tmp_path)
    inv, [update] = case(dest)
    text = I.encode([I.CORRELATION_CATALOG, inv.question, inv.secondary_questions,
                     dataclasses.asdict(inv.measurement),
                     [dataclasses.asdict(a) for a in inv.alternatives],
                     {k: v for k, v in dataclasses.asdict(update.next_action).items() if k != "reason"},
                     update.assessment, update.reason_codes, update.rationale,
                     update.evidence.reason]).lower()
    text = text.replace('"prediction":', '')      # the shared Alternative field name, not a claim
    for word in ("bull", "bear", "long", "short", "buy", "sell", "revers", "continu", "hedge",
                 "pair", "same_direction", "opposite", "p_value", "p-value", "significan", "regime",
                 "confidence", "z-test", "calibrated", "false-positive", "decoupl",
                 "diversif", "market factor", "sector", "btc", "crypto", "supported", "unsupported",
                 "confirm", "predict", "broad_market_move"):
        assert word not in text, word
    assert [a.name for a in inv.alternatives] == I.CORRELATION_CATALOG["alternatives"] == [
        "relationship_increased", "relationship_decreased", "relationship_crossed_zero", "not_distinct"]
    assert inv.measurement.sign == 0 and I.CORRELATION_CATALOG["direction"] is None
    assert I.CORRELATION_CATALOG["probability"] is None
    assert I.CORRELATION_CATALOG["authority"] == "research_only"


def test_broad_cohort_movement_is_context_not_contradiction(tmp_path):
    path = _publish(tmp_path)
    snap = C.adapt(C.source_snapshot(path), NOW)
    broad = dict(snap.result, market=dict(snap.result["market"], broad=True))
    state = I.make_state(snap.scan, broad, SYM, snap.bars, NOW, I.CORRELATION_FAMILY)
    assert state.contradictions == ()
    [d] = [d for d in state.dimensions if d.name == I.CORR_SHARED]
    assert (d.value, d.status) == (1.0, "ok") and d.evidence_id in state.evidence_ids
    inv = I.open_investigation(state, I.CORRELATION_FAMILY, snap.bars, NOW)
    plain = I.open_investigation(snap.state(SYM, I.CORRELATION_FAMILY), I.CORRELATION_FAMILY, snap.bars, NOW)
    assert all(a.contrary == () for a in inv.alternatives)
    upd, base = I.advance(inv, I.measure(inv, (), NOW, NOW)), I.advance(plain, I.measure(plain, (), NOW, NOW))
    # The measured change is unchanged; only the context code differs.
    assert upd.assessment == base.assessment and upd.evidence.status == "assessed"
    assert "shared_cohort_context" in upd.reason_codes and "shared_cohort_context_absent" in base.reason_codes


# ── malformed / missing evidence fails closed ────────────────────────────

def _state(tmp_path):
    snap = C.adapt(C.source_snapshot(_publish(tmp_path)), NOW)
    return snap, snap.state(SYM, I.CORRELATION_FAMILY)


def _with(state, fn):
    return dataclasses.replace(state, dimensions=tuple(fn(d) for d in state.dimensions if fn(d) is not None))


SIGNED = I.CORR_SIGNED


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: None if d.name.startswith(I.CORRELATION_FAMILY) else d, "correlation_evidence_missing"),
    (lambda d: None if d.name == SIGNED else d, "correlation_evidence_missing"),
    (lambda d: dataclasses.replace(d, status="insufficient_cohort", value=None) if d.name == SIGNED else d,
     "correlation_evidence_missing"),
    (lambda d: dataclasses.replace(d, status="gap") if d.name == SIGNED else d, "correlation_evidence_malformed"),
    (lambda d: dataclasses.replace(d, value=None) if d.name == SIGNED else d, "correlation_evidence_malformed"),
    (lambda d: dataclasses.replace(d, value=float("nan")) if d.name == SIGNED else d,
     "correlation_evidence_malformed"),
    (lambda d: dataclasses.replace(d, evidence_id=None) if d.name == SIGNED else d,
     "correlation_evidence_malformed"),
    (lambda d: None if d.name == I.CORRELATION_FAMILY else d, "correlation_evidence_malformed"),
    (lambda d: dataclasses.replace(d, value=d.value + 1) if d.name == I.CORRELATION_FAMILY else d,
     "correlation_change_inconsistent"),
    (lambda d: dataclasses.replace(d, evidence_id="x") if d.name == I.CORRELATION_FAMILY else d,
     "correlation_change_inconsistent"),
    (lambda d: dataclasses.replace(d, status="missing") if d.name == I.CORRELATION_FAMILY else d,
     "correlation_change_inconsistent"),
])
def test_malformed_correlation_evidence_fails_closed(tmp_path, mutate, reason):
    snap, state = _state(tmp_path)
    with pytest.raises(ValueError, match=reason):
        I.open_investigation(_with(state, mutate), I.CORRELATION_FAMILY, snap.bars, NOW)


def test_duplicate_signed_dimension_is_malformed(tmp_path):
    snap, state = _state(tmp_path)
    [d] = [d for d in state.dimensions if d.name == SIGNED]
    dup = dataclasses.replace(state, dimensions=state.dimensions + (d,))
    with pytest.raises(ValueError, match="correlation_evidence_malformed"):
        I.open_investigation(dup, I.CORRELATION_FAMILY, snap.bars, NOW)


def test_step_records_explicit_fail_closed_reason_and_no_case(tmp_path, monkeypatch):
    path = _publish(tmp_path)
    real = C.Snapshot.state

    def broken(self, symbol, family=None):
        st = real(self, symbol, family)
        return _with(st, lambda d: None if d.name == SIGNED else d)
    monkeypatch.setattr(C.Snapshot, "state", broken)
    detail = C.step(path, tmp_path / "investigation.db", NOW)
    assert detail["skipped"] == {"correlation_evidence_missing": 1}
    with sqlite3.connect(tmp_path / "investigation.db") as db:
        assert not db.execute("SELECT 1 FROM cases WHERE symbol=?", (SYM,)).fetchone()


def test_not_distinct_and_missing_concurrent_evidence_are_explicit(tmp_path):
    snap, state = _state(tmp_path)
    inv = I.open_investigation(state, I.CORRELATION_FAMILY, snap.bars, NOW)
    high = dataclasses.replace(inv, measurement=dataclasses.replace(inv.measurement, threshold=1e9))
    assert dict(I.advance(high, I.measure(high, (), NOW, NOW)).assessment)["not_distinct"] == "compatible"
    # Missing concurrent context is recorded, never a reason to refuse the change.
    bare = dataclasses.replace(inv, state=_with(inv.state, lambda d: None if d.name in I.FAMILIES else d))
    ev = I.measure(bare, (), NOW, NOW)
    assert (ev.status, ev.reason) == ("assessed", "registration_evidence_described")
    upd = I.advance(bare, ev)
    assert dict(upd.assessment)["relationship_decreased"] == "compatible"
    assert "concurrent_evidence_unavailable" in upd.reason_codes


# ── deterministic registration ───────────────────────────────────────────

def test_registration_is_deterministic_across_retry_and_restart(tmp_path):
    path, dest, first = run(tmp_path)
    inv, updates = case(dest)
    again = C.step(path, dest, NOW)
    assert again["registered"] == 0 and again["allocation"]["reused"]
    assert case(dest) == (inv, updates)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _, dest2, second = run(fresh)
    assert case(dest2) == (inv, updates)
    assert first["allocation"]["selected"] == second["allocation"]["selected"]


def test_attention_selection_is_unchanged_by_registration(tmp_path):
    path, _, detail = run(tmp_path)
    scan = json.loads(sqlite3.connect(path).execute("SELECT payload FROM scans").fetchone()[0])
    selected = [r["symbol"] for r in sorted(scan["rows"], key=lambda r: r.get("rank") or 1e9) if r.get("selected")]
    assert SYM in selected and detail["allocation"]["legacy_selected"] == selected


def test_investigation_module_imports_are_unchanged():
    tree = ast.parse(Path(I.__file__).read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert names == {"__future__", "dataclasses", "json", "math", "statistics", "trader.cognition.contracts"}


# ── semantics from the targeted review ───────────────────────────────────

def _inv(tmp_path, fn=None):
    snap, state = _state(tmp_path)
    if fn:
        state = _with(state, fn)
    return I.open_investigation(state, I.CORRELATION_FAMILY, snap.bars, NOW)


def _winner(inv):
    upd = I.advance(inv, I.measure(inv, (), NOW, NOW))
    [w] = [n for n, a in upd.assessment if a == "compatible"]
    return w, upd


def test_isolated_change_with_quiet_context_is_a_valid_description(tmp_path):
    quiet = lambda d: dataclasses.replace(d, value=0.0) if d.name in I.FAMILIES and d.status == "ok" else d
    inv = _inv(tmp_path, quiet)
    w, upd = _winner(inv)
    assert w == "relationship_decreased" and upd.evidence.status == "assessed"
    assert "concurrent_unusual_evidence_absent" in upd.reason_codes
    loud = lambda d: dataclasses.replace(d, value=9.0) if d.name == "volume_anomaly" and d.status == "ok" else d
    w2, upd2 = _winner(_inv(tmp_path, loud))
    # Concurrent evidence changes the context codes only, never the result.
    assert w2 == w and "concurrent_unusual:volume_anomaly" in upd2.reason_codes
    assert "concurrent_unusual_evidence_present" in upd2.reason_codes


def test_peer_basket_changes_are_context_only(tmp_path):
    loud = lambda d: dataclasses.replace(d, value=-3.0) if d.name.startswith(I.CORR_PEER) else d
    w, upd = _winner(_inv(tmp_path, loud))
    assert w == "relationship_decreased" and "peer_basket_change_context" in upd.reason_codes
    first = sorted(d.name for d in _state(tmp_path)[1].dimensions if d.name.startswith(I.CORR_PEER))[0]
    gone = lambda d: dataclasses.replace(d, value=None, status="fisher_undefined", evidence_id=None) \
        if d.name == first else d
    w2, upd2 = _winner(_inv(tmp_path, gone))
    assert w2 == w and "peer_change_evidence_unavailable" in upd2.reason_codes


def test_unreadable_frozen_evidence_is_not_testable(tmp_path):
    inv = _inv(tmp_path)
    broken = dataclasses.replace(inv, state=_with(inv.state, lambda d: None if d.name == SIGNED else d))
    ev = I.measure(broken, (), NOW, NOW)
    assert (ev.status, ev.reason) == ("not_testable", "correlation_evidence_missing")
    assert set(dict(I.advance(broken, ev).assessment).values()) == {"not_testable"}


@pytest.mark.parametrize("rb,rr,result,codes", [
    # strengthening positive relationship
    (0.2, 0.8, "relationship_increased", {"correlation_increased", "baseline_relationship_positive",
                                          "recent_relationship_positive", "relationship_magnitude_increased"}),
    # weakening positive relationship
    (0.8, 0.2, "relationship_decreased", {"correlation_decreased", "baseline_relationship_positive",
                                          "recent_relationship_positive", "relationship_magnitude_decreased"}),
    # weakening inverse relationship
    (-0.9, -0.1, "relationship_increased", {"correlation_increased", "baseline_relationship_inverse",
                                            "recent_relationship_inverse", "relationship_magnitude_decreased"}),
    # strengthening inverse relationship
    (-0.1, -0.9, "relationship_decreased", {"correlation_decreased", "baseline_relationship_inverse",
                                            "recent_relationship_inverse", "relationship_magnitude_increased"}),
    # crossing zero keeps its increase/decrease fact
    (0.6, -0.4, "relationship_crossed_zero", {"correlation_crossed_zero", "correlation_decreased",
                                              "baseline_relationship_positive", "recent_relationship_inverse"}),
    (-0.5, 0.5, "relationship_crossed_zero", {"correlation_crossed_zero", "correlation_increased",
                                              "baseline_relationship_inverse", "recent_relationship_positive"}),
])
def test_endpoints_determine_the_description(tmp_path, rb, rr, result, codes):
    z = (math.atanh(rr) - math.atanh(rb)) / I.CORRELATION_SCALE
    assert abs(z) >= 2.0

    def fix(d):
        return {I.CORR_R_BASELINE: dataclasses.replace(d, value=rb),
                I.CORR_R_RECENT: dataclasses.replace(d, value=rr),
                SIGNED: dataclasses.replace(d, value=z),
                I.CORRELATION_FAMILY: dataclasses.replace(d, value=abs(z))}.get(d.name, d)
    w, upd = _winner(_inv(tmp_path, fix))
    assert w == result and codes <= set(upd.reason_codes)
    opposite = {"correlation_increased": "correlation_decreased", "correlation_decreased": "correlation_increased"}
    assert not any(opposite.get(c) in upd.reason_codes for c in codes)


@pytest.mark.parametrize("fn,reason", [
    (lambda d: dataclasses.replace(d, value=1.0) if d.name == I.CORR_R_RECENT else d,
     "correlation_evidence_malformed"),                               # |r| = 1 is never clipped
    (lambda d: dataclasses.replace(d, value=-1.0) if d.name == I.CORR_R_BASELINE else d,
     "correlation_evidence_malformed"),
    (lambda d: None if d.name == I.CORR_R_BASELINE else d, "correlation_evidence_malformed"),
    (lambda d: dataclasses.replace(d, value=d.value + 0.1) if d.name == I.CORR_R_RECENT else d,
     "correlation_change_inconsistent"),                              # endpoints must reproduce the change
    (lambda d: dataclasses.replace(d, value=d.value + 1) if d.name == I.CORR_BASKET else d,
     "correlation_evidence_malformed"),                               # basket size must match its peers
    (lambda d: dataclasses.replace(d, name=I.CORR_PEER + SYM) if d.name.startswith(I.CORR_PEER) else d,
     "correlation_evidence_malformed"),                               # the candidate is never its own peer
])
def test_endpoint_and_basket_evidence_fail_closed(tmp_path, fn, reason):
    snap, state = _state(tmp_path)
    with pytest.raises(ValueError, match=reason):
        I.open_investigation(_with(state, fn), I.CORRELATION_FAMILY, snap.bars, NOW)
