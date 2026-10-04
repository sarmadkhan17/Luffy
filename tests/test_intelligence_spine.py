"""LUFFY-INTELLIGENCE-SPINE-LIVE-INTEGRATION-R1.

observation -> WorldModel -> Attention -> investigation -> (research) with
exact stored ids, in shadow/read-only mode. Synthetic; no network, LLM or
trading.
"""
import json
import math
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trader.cognition import investigation as I
from trader.cognition import opportunity_context as oc
from trader.cognition import research_families as rf
from trader.cognition.contracts import Candle, load_input
from trader.observability import attention as A, investigation as C
from trader.observability import intelligence_trace as T
from trader.observability import world_producer as W
from trader.observability.store import Store
from trader.world import Quality, WorldModelRecord
from tests.test_attention_telemetry import frames
from tests.test_investigation_state_feedback import paths  # noqa: F401  (fixture)

WORLD = A.settings({"world_model": True})
LEGACY = A.settings()


def publish(path, now, sid="s1", spikes=(0,), cfg=WORLD, data=None, prepared_event=None):
    """Persist one scan through the real Store (what the worker child runs)."""
    data = frames(6, now) if data is None else data
    for j in spikes:
        data[f"S{j}/USDT"]["4h"].loc[29, "volume"] = 100_000 // (j + 1)
    store = Store(path, cfg)
    with patch("trader.observability.store.time", SimpleNamespace(time=lambda: now / 1000)):
        ident = dict(schema="attention-scan-identity.v1", instance_id="1" * 32, seq=now)
        from copy import deepcopy
        event = A.capture(data, list(data), sid, cfg, now) if prepared_event is None else deepcopy(prepared_event)
        event['capture_settings'] = dict(cfg)
        store.write(dict(event, identity=ident))
        store.write({"kind": "causes", "scan_id": sid, "as_of_ms": now, "identity": ident,
                     "items": [{"symbol": s, "decision_id": "d_" + s} for s in data]})
    store.close()
    path.with_name("attention_health.json").write_text(json.dumps({
        "health_schema": "attention-collector-health.v2", "instance_id": "1" * 32,
        "updated_ms": now, "status": "ok", "worker_alive": True, "errors": 0,
        "capture_errors": 0, "worker_errors": 0, "dropped": 0, "details_lost": 0,
        "process_started_ms": 0, "failure_generation": 0, "fence_seq": 0, "certificate": None,
        "last_error": None, "first_error_ms": None, "last_error_ms": None,
        "last_complete": {"scan_id": sid, "seq": now}}))
    return event


def scan_of(path, sid="s1"):
    with sqlite3.connect(path) as db:
        return json.loads(db.execute("SELECT payload FROM scans WHERE scan_id=?", (sid,)).fetchone()[0])


def table(path, sql, *args):
    with sqlite3.connect(path) as db:
        return db.execute(sql, args).fetchall()


def strip_world(rows):
    """Scan rows without the WorldModel attribution fields."""
    out = []
    for r in rows:
        r = {k: v for k, v in r.items() if k != "world_model"}
        if "components" in r:
            r["components"] = {k: v for k, v in r["components"].items()
                               if k != "world_volume_anomaly"}
        out.append(r)
    return out


# ── 1. WorldModel live producer ─────────────────────────────────────────────

def test_settings_flag_is_opt_in_and_legacy_settings_are_unchanged():
    assert "world_model" not in A.settings() and "world_model" not in A.settings({"world_model": False})
    assert A.settings({"world_model": True})["world_model"] is True
    with pytest.raises(ValueError):
        A.settings({"world_model": "yes"})


def test_producer_builds_exact_point_in_time_model_with_provenance(paths):  # noqa: F811
    _src, _dest, now = paths
    event = A.capture(frames(6, now), [f"S{j}/USDT" for j in range(6)], "s1", WORLD, now)
    model, rec = W.produce(event)
    assert rec["status"] == W.OK and rec["model_id"] == model.model_id
    assert model.as_of_ms == event["as_of_ms"]
    assert WorldModelRecord.from_json(rec["record_json"]).reconstruct().model_id == model.model_id
    anchor = now // I.TF * I.TF
    for state in model.states:
        (win,) = state.get_observations(W.WINDOW_KIND)
        (z,) = state.get_observations(W.OUTPUT_KIND)
        assert win.quality is z.quality is Quality.VALID
        assert win.timestamp_ms == z.timestamp_ms == anchor <= now          # closed anchor bar
        assert win.available_at_ms <= now and z.available_at_ms == now      # no future leakage
        assert win.source_ref.startswith("attention-scan:s1:closed_bars:")
        assert z.source == "perception:" + W.PERCEPTION_NAME and win.observation_id in z.source_ref
        assert z.unit == "z_score" and z.horizon == "intraday" and len(win.value) == 21
    # Deterministic: the same captured input rebuilds the identical model.
    assert W.produce(event)[0].model_id == model.model_id


def test_world_component_is_the_existing_volume_component_and_changes_no_ranking(paths):  # noqa: F811
    src, _dest, now = paths
    captured = publish(src, now, spikes=(0, 1, 2), cfg=WORLD)
    world = scan_of(src)
    legacy_src = src.with_name("legacy.db")
    publish(legacy_src, now, spikes=(0, 1, 2), cfg=LEGACY, prepared_event=captured)
    legacy = scan_of(legacy_src)
    assert "world_model" not in legacy
    for r in world["rows"]:
        if r.get("eligible"):
            assert r["world_model"]["status"] == "ok"
            assert r["components"]["world_volume_anomaly"] == r["components"]["volume_anomaly"]
    # Rank, selection, reason, salience and dominant family are unchanged.
    assert strip_world(world["rows"]) == legacy["rows"]
    assert world["market"] == legacy["market"] and world["observations"] == legacy["observations"]


def _stub(bars):
    tf = I.TF
    return SimpleNamespace(tf_ms=tf, timeframe="4h",
                           bar_asof=lambda s, t, a: bars.get(t))


def _bar(open_ms, volume):
    return Candle("X/USDT", open_ms, 1.0, 1.0, 1.0, 1.0, volume, open_ms + I.TF, "t", open_ms + I.TF)


@pytest.mark.parametrize("case,quality,reason", [
    ("anchor_missing", Quality.STALE, "anchor_bar_unavailable"),
    ("hole", Quality.MISSING, "volume_window_incomplete"),
    ("negative", Quality.INVALID, "malformed_volume"),
    ("nan", Quality.INVALID, "malformed_volume"),
    ("flat", Quality.INVALID, "undefined_zero_or_nonfinite_variance"),
])
def test_unusable_inputs_are_explicit_states_never_filled(case, quality, reason):
    as_of = 100 * I.TF + 60_000
    last_open = 99 * I.TF
    opens = [last_open - (20 - j) * I.TF for j in range(21)]
    bars = {t: _bar(t, 100.0 + (t // I.TF) % 7) for t in opens}
    if case == "anchor_missing":
        del bars[last_open]
    elif case == "hole":
        del bars[opens[5]]
    elif case == "negative":
        bars[last_open] = _bar(last_open, -5.0)
    elif case == "nan":
        bars[last_open] = _bar(last_open, math.nan)
    elif case == "flat":
        bars = {t: _bar(t, 100.0) for t in opens}
    spec = W.perception_spec("4h", I.TF)
    _state, obs = W._member_state(_stub(bars), "X/USDT", as_of, "s1", spec, 20)
    assert obs.quality is quality and obs.value is None
    assert obs.uncertainty["reason"] == reason


def test_producer_failure_degrades_to_legacy_attention(paths):  # noqa: F811
    _src, _dest, now = paths
    event = A.capture(frames(6, now), [f"S{j}/USDT" for j in range(6)], "s1", WORLD, now)
    with patch.object(W, "build", side_effect=RuntimeError("boom")):
        payload = W.evaluate(event)
    assert payload["world_model"]["status"] == W.UNAVAILABLE
    assert payload["world_model"]["reason"] == "producer_failed:RuntimeError"
    assert payload["world_model"]["record_json"] is None
    assert payload["rows"] == A.evaluate_snapshot(event)["rows"]


def test_stale_world_model_is_refused_and_attention_stays_legacy(paths):  # noqa: F811
    _src, _dest, now = paths
    event = A.capture(frames(6, now), [f"S{j}/USDT" for j in range(6)], "s1", WORLD, now)
    stale = W.build(load_input(event["input"]), now - 60_000, "s1")
    payload = W.evaluate(event, model=stale)
    assert payload["world_model"]["status"] == W.REFUSED
    assert payload["world_model"]["reason"] == "world_model_cut_mismatch"
    assert payload["rows"] == A.evaluate_snapshot(event)["rows"]


def test_attention_world_evaluation_failure_keeps_the_legacy_scan(paths):  # noqa: F811
    _src, _dest, now = paths
    event = A.capture(frames(6, now), [f"S{j}/USDT" for j in range(6)], "s1", WORLD, now)
    real = A.evaluate_snapshot

    def broken(ev, world_model=None, learning_journal=None):
        if world_model is not None:
            raise KeyError("boom")
        return real(ev, learning_journal=learning_journal)
    with patch.object(A, "evaluate_snapshot", broken):
        payload = W.evaluate(event)
    assert payload["world_model"]["status"] == W.REFUSED
    assert payload["world_model"]["reason"] == "attention_world_evaluation_failed:KeyError"
    assert payload["rows"] == real(event)["rows"]


# ── 2/3. Attention consumes the persisted model; investigation replays it ───

def test_investigation_replays_the_exact_model(paths):  # noqa: F811
    src, _dest, now = paths
    publish(src, now)
    snap = C.adapt(C.H.bound_snapshot(src, now_ms=now)[0], now)
    assert snap.world_model.model_id == scan_of(src)["world_model"]["model_id"]


@pytest.mark.parametrize("tamper,reason", [
    (lambda w: w.update(record_json=w["record_json"][:-2] + "]}"), "world_model_record_corrupt"),
    (lambda w: w.update(model_id="world.model.v1:" + "0" * 64), "world_model_receipt_mismatch"),
    (lambda w: w.update(schema="other"), "world_model_receipt_invalid"),
])
def test_tampered_world_receipt_refuses_the_scan_and_registers_nothing(paths, tamper, reason):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    scan = scan_of(src)
    tamper(scan["world_model"])
    with sqlite3.connect(src) as db:
        db.execute("UPDATE scans SET payload=? WHERE scan_id='s1'", (json.dumps(scan),))
    r = C.step(src, dest, now)
    assert r["status"] == "degraded" and r["reason"] == reason
    assert r["registered"] == 0 and table(dest, "SELECT * FROM intelligence_traces") == []


def test_fabricated_but_self_consistent_model_fails_rederivation(paths):  # noqa: F811
    """A verifying record that the stored candles do not produce is refused."""
    src, dest, now = paths
    publish(src, now)
    scan = scan_of(src)
    other = frames(6, now)
    other["S3/USDT"]["4h"].loc[29, "volume"] = 77_777
    fake = W.build(load_input(A.capture(other, list(other), "s1", WORLD, now)["input"]), now, "s1")
    rec = WorldModelRecord.from_model(fake)
    scan["world_model"].update(model_id=rec.model_id, record_id=rec.record_id,
                               record_json=rec.to_json())
    with sqlite3.connect(src) as db:
        db.execute("UPDATE scans SET payload=? WHERE scan_id='s1'", (json.dumps(scan),))
    r = C.step(src, dest, now)
    assert r["reason"] == "world_model_rederivation_mismatch" and r["registered"] == 0


def test_no_world_model_keeps_legacy_registration_and_trace_says_so(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now, cfg=LEGACY)
    r = C.step(src, dest, now)
    assert r["registered"] == 1 and r["intelligence_trace"]["written"] == 1
    (iid,), = table(dest, "SELECT id FROM cases")
    t = T.view(dest, iid)
    assert t["links"]["world_model"] == {"status": "UNKNOWN", "reason": "world_model_not_supplied"}
    assert C.context_for(dest, iid).to_dict()["world_model"]["status"] == "UNKNOWN"


def test_attention_no_selection_registers_and_traces_nothing(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now, spikes=())
    assert not [r for r in scan_of(src)["rows"] if r.get("selected")]
    r = C.step(src, dest, now)
    assert r["status"] == "ok" and r["registered"] == 0
    assert table(dest, "SELECT * FROM cases") == table(dest, "SELECT * FROM intelligence_traces") == []


def test_registration_failure_leaves_no_case_or_trace(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    with patch.object(I, "open_investigation", side_effect=ValueError("forced_refusal")):
        r = C.step(src, dest, now)
    assert r["registered"] == 0 and r["skipped"] == {"forced_refusal": 1}
    assert table(dest, "SELECT * FROM cases") == table(dest, "SELECT * FROM intelligence_traces") == []


def test_trace_refusal_never_undoes_registration_or_context(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    with patch.object(T, "build", side_effect=T.TraceRefused("forced")):
        r = C.step(src, dest, now)
    assert r["registered"] == 1 and r["opportunity_context"]["written"] == 1
    assert r["intelligence_trace"] == {"written": 0, "duplicate": 0, "refused": {"forced": 1}}
    assert table(dest, "SELECT * FROM intelligence_traces") == []


# ── 9. acceptance: one deterministic end-to-end chain with exact ids ────────

def test_end_to_end_trace_links_actual_stored_ids(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    r = C.step(src, dest, now)
    assert r["status"] == "ok" and r["registered"] == 1
    assert r["opportunity_context"]["written"] == r["intelligence_trace"]["written"] == 1

    scan = scan_of(src)
    (iid, case_payload), = table(dest, "SELECT id,payload FROM cases")
    case = I.investigation_from_dict(json.loads(case_payload))
    (update_id,), = table(dest, "SELECT id FROM updates WHERE case_id=?", iid)
    (decision_id, alloc_payload), = table(dest, "SELECT id,payload FROM allocation_decisions")
    (ctx_id,), = table(dest, "SELECT id FROM opportunity_contexts WHERE investigation_id=?", iid)
    (trace_id, trace_payload), = table(dest, "SELECT id,payload FROM intelligence_traces")
    t = T.view(dest, iid)
    L = t["links"]
    assert t["trace_id"] == trace_id and T.verify(trace_payload)["trace_id"] == trace_id
    assert t["stages"] == list(T.STAGES) and t["authority"] == "NONE"

    # source observation -> WorldModel: the ids exist in the persisted record.
    model = WorldModelRecord.from_json(scan["world_model"]["record_json"]).reconstruct()
    state = next(s for s in model.states if s.instrument == case.state.symbol)
    (win,) = state.get_observations(W.WINDOW_KIND)
    (z,) = state.get_observations(W.OUTPUT_KIND)
    assert L["source_observation"]["observation_id"] == win.observation_id
    assert set(L["source_observation"]["input_version_ids"]) == {
        v["version_id"] for v in scan["input_versions"] if v["symbol"] == case.state.symbol}
    assert L["world_model"]["model_id"] == model.model_id == scan["world_model"]["model_id"]
    assert L["world_model"]["record_id"] == scan["world_model"]["record_id"]
    assert L["world_model"]["observation_id"] == z.observation_id

    # WorldModel -> Attention: the scan row names the same model and observation.
    row = next(x for x in scan["rows"] if x["symbol"] == case.state.symbol)
    assert row["world_model"]["model_id"] == model.model_id
    assert row["world_model"]["observation_id"] == z.observation_id
    assert L["world_model"]["attention_contribution"]["status"] == "ok"
    assert (L["attention"]["scan_id"], L["attention"]["rank"], L["attention"]["selected"]) == \
        (scan["scan_id"], row["rank"], True)
    assert L["attention"]["scan_sha256"] == oc._sha(scan)

    # Attention -> allocation -> investigation -> context.
    assert L["allocation"]["decision_id"] == decision_id == json.loads(alloc_payload)["decision_id"]
    assert case.state.symbol in json.loads(alloc_payload)["selected"]
    assert (L["investigation"]["investigation_id"], L["investigation"]["episode_id"]) == \
        (iid, case.episode_id)
    assert L["registration"]["initial_update_id"] == update_id
    assert L["registration"]["opportunity_context_id"] == ctx_id
    assert L["investigation"]["family"] == case.measurement.family == row["dominant"]
    ctx = C.context_for(dest, iid).to_dict()
    assert ctx["world_model"] == {"status": "AVAILABLE", "reason": None, "as_of_ms": now,
                                  "model_id": model.model_id,
                                  "record_id": scan["world_model"]["record_id"]}

    # Research: the volume_anomaly family is registered but has not run yet;
    # nothing is fabricated downstream (tests/test_investigation_research_family.py).
    for stage in ("research_question", *T.RESEARCH_STAGES):
        assert L[stage]["status"] == "PENDING" and L[stage]["reason"] == T.AWAITING_RUN

    # Evidence incomplete: the investigation is still open; no result is claimed.
    out = t["investigation_outcome"]
    assert out["terminal"] is False and out["latest_evidence_status"] == "unresolved"
    assert [u["event_id"] for u in out["updates"]] == [update_id]


def test_retry_restart_and_replay_are_idempotent(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    C.step(src, dest, now)
    before = table(dest, "SELECT * FROM intelligence_traces")
    (iid,), = table(dest, "SELECT id FROM cases")
    # Retry of the same scan: allocation execution closed; no duplicate trace.
    again = C.step(src, dest, now + 1000)
    assert again["registered"] == 0 and table(dest, "SELECT * FROM intelligence_traces") == before
    # Restart/replay: the context and the trace rebuild byte-for-byte.
    replayed = C.replay_context(dest, iid, src)
    assert replayed.context_id == C.context_for(dest, iid).context_id
    with sqlite3.connect(dest) as db:
        (initial,), = db.execute("SELECT payload FROM updates WHERE case_id=?", (iid,)).fetchall()
        case = I.investigation_from_dict(json.loads(
            db.execute("SELECT payload FROM cases WHERE id=?", (iid,)).fetchone()[0]))
        alloc = json.loads(db.execute("SELECT payload FROM allocation_decisions").fetchone()[0])
    rebuilt = T.build(scan_of(src), alloc, case, I.update_from_dict(json.loads(initial)), replayed)
    assert (rebuilt["trace_id"], T.canonical(rebuilt)) == before[0][:1] + before[0][2:]
    with C.ledger(dest) as db:
        assert T.persist(db, rebuilt) is False
        forged = dict(rebuilt, symbol="OTHER")
        with pytest.raises(T.TraceRefused):
            T.persist(db, forged)


def test_corrupt_stored_trace_is_refused_on_read(paths):  # noqa: F811
    src, dest, now = paths
    publish(src, now)
    C.step(src, dest, now)
    (iid,), = table(dest, "SELECT id FROM cases")
    with sqlite3.connect(dest) as db:
        db.execute("UPDATE intelligence_traces SET payload=replace(payload,'selected','chosen')")
    with pytest.raises(T.TraceRefused):
        T.view(dest, iid)


# ── 7. the Kernel's collector path: producer -> queue -> worker child ───────

def test_kernel_collector_worker_child_maintains_the_world_model(tmp_path, paths):  # noqa: F811
    from trader.observability.collector import Collector
    from trader.observability.store import read_latest
    _src, _dest, now = paths
    data = frames(6, now)
    data["S0/USDT"]["4h"].loc[29, "volume"] = 100_000
    on = Collector(tmp_path / "on", {"world_model": True}, start=False)
    assert on.begin(data, list(data), now)                 # the Kernel's call
    event = on.queue.get_nowait()
    assert event["capture_settings"]["world_model"] is True
    on._run(event)                                          # disposable worker child
    scan = read_latest(on.path, now_ms=now)["scan"]
    assert scan["world_model"]["status"] == W.OK and scan["world_model"]["authority"] == "NONE"
    assert scan["world_model"]["observation_counts"] == {"VALID": 6}
    assert scan["rows"][0]["world_model"]["model_id"] == scan["world_model"]["model_id"]
    off = Collector(tmp_path / "off", start=False)
    assert off.begin(frames(6, now), list(data), now)
    off._run(off.queue.get_nowait())
    assert "world_model" not in read_latest(off.path, now_ms=now)["scan"]


# ── shadow boundary: TRADING_BEHAVIOR_CHANGED = NO ──────────────────────────

def test_spine_modules_import_no_trading_or_llm_path():
    code = ("import sys; import trader.observability.world_producer, "
            "trader.observability.intelligence_trace, trader.observability.investigation; "
            "print('\\n'.join(sorted(sys.modules)))")
    loaded = set(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                check=True, cwd=Path(__file__).resolve().parents[1]).stdout.split())
    forbidden = {"trader.kernel", "trader.engine.executor", "trader.engine.risk",
                 "trader.engine.orchestrator", "trader.engine.exits", "trader.brain",
                 "trader.brain.llm", "trader.data.feed", "ccxt", "openai", "anthropic",
                 "requests", "httpx"}
    assert not loaded & forbidden


def test_trading_paths_do_not_reference_the_spine():
    root = Path(__file__).resolve().parents[1] / "trader"
    for name in ("kernel.py", "engine/orchestrator.py", "engine/risk.py", "engine/executor.py",
                 "engine/exits.py"):
        text = (root / name).read_text()
        assert "world_producer" not in text and "intelligence_trace" not in text, name
