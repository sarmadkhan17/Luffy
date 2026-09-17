"""Synthetic semantic demonstrations. These are not predictive validation."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import math

import pytest

from trader.cognition import investigation as I
from trader.cognition.contracts import Candle
from trader.observability import investigation as C
from trader.observability.learning import source_snapshot
from tests.test_attention_learning import publish


@pytest.fixture
def prefix(tmp_path):
    import time
    now = int(time.time()*1000)//I.TF*I.TF + 60_000
    path = tmp_path / "attention.db"
    publish(path, now)
    source = source_snapshot(path)
    snapshot = C.adapt(source, now)
    symbol = snapshot.result["selected"][0]
    inv = I.open_investigation(snapshot.state(symbol), "volume_anomaly", snapshot.bars, now)
    return source, snapshot, inv


def targets(inv, *, volume=None, returns=None, observed=None):
    m = inv.measurement
    volume = math.expm1(m.baseline_mean + m.sign * 4*m.baseline_scale) if volume is None and m.baseline_mean is not None else (volume or 100)
    result = []
    for i, (symbol, opened) in enumerate(m.target_keys):
        ret = returns[i % len(returns)] if returns else .01
        close = 100 * math.exp(ret)
        c = Candle(symbol, opened, 100, max(100, close), min(100, close), close,
                   volume, observed or opened+I.TF, "synthetic", opened+I.TF)
        result.append(I.InputBar(f"v_{symbol}_{opened}_{volume}_{ret}_{observed}", c))
    return tuple(result)


def test_prefix_round_trip_and_no_source_mutation(prefix):
    source, snapshot, inv = prefix
    original = deepcopy(source)
    assert C.adapt(source, snapshot.observed_ms).state(inv.state.symbol) == inv.state
    assert I.investigation_from_dict(json.loads(I.encode(asdict(inv)))) == inv
    assert source == original
    assert set(inv.state.input_versions) == {r["version_id"] for r in source[0]["input_versions"]}
    assert inv.measurement.target_keys[0][1] > inv.registered_ms + I.GUARD_MS
    assert all(a.probability is None for a in inv.alternatives)


@pytest.mark.parametrize("change,reason", [
    ("future", "unsupported_or_future_scan"), ("missing", "missing_version_join"),
    ("revision", "invalid_version_join"), ("malformed", "malformed_inputs"),
    ("available", "unavailable_input"), ("fabricated", "source_evaluator_mismatch")])
def test_adapter_refuses_bad_or_unavailable_inputs(prefix, change, reason):
    source, snapshot, _ = prefix
    source = deepcopy(source)
    b = next(iter(source[1].values()))[0]
    if change == "future": source[0]["as_of_ms"] += 1
    if change == "missing": next(iter(source[1].values())).pop()
    if change == "revision": b["open_ms"] += I.TF
    if change == "malformed": b["volume"] = -1
    if change == "available":
        b["available_ms"] = snapshot.observed_ms+1
        next(r for r in source[0]["input_versions"] if r["version_id"] == b["version_id"])["first_seen_ms"] = b["available_ms"]
    if change == "fabricated": source[0]["rows"][0]["components"]["volume_anomaly"] = 999
    with pytest.raises(ValueError, match=reason):
        C.adapt(source, snapshot.observed_ms)


def test_trigger_relevance_missing_cause_and_zero_scale(prefix):
    _, snap, inv = prefix
    assert "volume" in inv.question
    for family in I.FAMILIES:
        state = replace(inv.state, dimensions=tuple(replace(d, value=3., status="ok") if d.name == family else d for d in inv.state.dimensions))
        case = I.open_investigation(state, family, snap.bars, inv.registered_ms)
        assert case.question == I.QUESTIONS[family] and case.measurement.family == family
        assert "participation" in case.causal_unknowns
        assert next(d for d in state.dimensions if d.name == "participation").value is None
        assert len(case.alternatives) == 3
    flat = tuple(replace(b, candle=replace(b.candle, volume=100)) for b in snap.bars)
    untestable = I.open_investigation(inv.state, "volume_anomaly", flat, inv.registered_ms)
    update = I.advance(untestable, I.measure(untestable, (), inv.registered_ms, inv.registered_ms))
    assert update.next_action.kind == "UNASSESSABLE"
    assert update.evidence.reason == "unusable_baseline_scale"


def test_paired_identical_prefixes_diverge_only_after_relevant_evidence(prefix):
    _, _, inv = prefix
    initial = I.advance(inv, I.measure(inv, (), inv.registered_ms, inv.registered_ms))
    assert initial.next_action.kind == "WAIT"
    frozen = I.encode(asdict(initial))
    all_bars = targets(inv)
    partial = I.advance(inv, I.measure(inv, all_bars[:2], all_bars[1].candle.close_ms, all_bars[1].candle.close_ms), initial)
    assert partial.assessment == initial.assessment and partial.evidence.score is None
    assert I.advance(inv, replace(partial.evidence, observed_ms=partial.observed_ms+1), partial) is None
    end = inv.measurement.deadline_ms
    persistent = I.advance(inv, I.measure(inv, all_bars, end, end+1), partial)
    neutral = targets(inv, volume=math.expm1(inv.measurement.baseline_mean))
    normal = I.advance(inv, I.measure(inv, neutral, end, end+1), partial)
    assert dict(persistent.assessment)["same_direction"] == "compatible"
    assert dict(normal.assessment)["normalization"] == "compatible"
    assert persistent.previous_event_id == normal.previous_event_id == partial.event_id
    assert I.encode(asdict(initial)) == frozen
    assert I.update_from_dict(json.loads(I.encode(asdict(persistent)))) == persistent
    assert persistent.next_action.kind == "ACQUIRE"
    assert I.advance(inv, normal.evidence, persistent) is None  # terminal records cannot be revised
    irrelevant = replace(inv, question="irrelevant prose")
    assert I.advance(irrelevant, persistent.evidence, partial) == persistent


def test_full_window_exact_keys_and_late_arrival(prefix):
    _, _, inv = prefix
    end = inv.measurement.deadline_ms
    bars = targets(inv)
    late = replace(bars[0], candle=replace(bars[0].candle, available_ms=end+1000))
    ev = I.measure(inv, (late, *bars[1:]), end, end)
    assert ev.status == "unresolved" and ev.missing_keys == (inv.measurement.target_keys[0],)
    resolved = I.measure(inv, (late, *bars[1:]), end+1000, end+1001)
    assert resolved.status == "measured" and resolved.observed_ms == end+1001
    assert I.measure(inv, bars[1:], end+I.TF, end+I.TF).score is None
    assert I.measure(inv, bars, inv.measurement.expires_ms+1, inv.measurement.expires_ms+1).status == "not_testable"


def test_relative_reverse_never_means_persistence_and_full_cohort_required(prefix):
    _, snap, volume = prefix
    state = replace(volume.state, dimensions=tuple(replace(d, value=3., status="ok") if d.name == "relative_return_divergence" else d for d in volume.state.dimensions))
    inv = I.open_investigation(state, "relative_return_divergence", snap.bars, volume.registered_ms)
    bars = targets(inv, returns=[0.001, .002])
    bars = tuple(replace(b, candle=replace(b.candle, close=50., low=50.)) if b.candle.symbol == state.symbol else b for b in bars)
    end = inv.measurement.deadline_ms
    assert I.measure(inv, bars[:-1], end, end).status == "unresolved"
    ev = I.measure(inv, bars, end, end)
    update = I.advance(inv, ev)
    assert update.next_action.kind == "RESEARCH"
    assert dict(update.assessment)["opposite_direction"] == "compatible"
    assert dict(update.assessment)["same_direction"] == "contradicted"


def test_revision_is_later_evidence_not_new_independent_case(prefix):
    _, _, inv = prefix
    bars = targets(inv)
    t = bars[1].candle.close_ms
    first = I.advance(inv, I.measure(inv, bars[:2], t, t))
    revision = replace(bars[0], version_id="revised", candle=replace(bars[0].candle, volume=5, available_ms=t+1))
    later = I.advance(inv, I.measure(inv, (*bars[:2], revision), t+1, t+1), first)
    assert "input_revision" in later.reason_codes
    assert first.assessment == later.assessment
    assert "revised" not in first.input_ids and "revised" in later.input_ids


def test_publication_guard(prefix):
    _, snap, inv = prefix
    with pytest.raises(ValueError, match="registration_boundary_guard"):
        I.open_investigation(inv.state, inv.primary_trigger, snap.bars, (inv.registered_ms//I.TF+1)*I.TF-1000)


def test_volatility_uses_complete_future_intrabar_returns(prefix):
    _, snap, volume = prefix
    state = replace(volume.state, dimensions=tuple(replace(d,value=3.,status='ok') if d.name=='volatility_transition' else d for d in volume.state.dimensions))
    inv=I.open_investigation(state,'volatility_transition',snap.bars,volume.registered_ms)
    bars=targets(inv,returns=[.12,-.12,.12,-.12,.12])
    end=inv.measurement.deadline_ms
    assert I.measure(inv,bars[:-1],end,end).status=='unresolved'
    e=I.measure(inv,bars,end,end)
    assert e.status=='measured' and e.score>inv.measurement.threshold
    update=I.advance(inv,e)
    assert dict(update.assessment)['same_direction']=='compatible'
    assert I.measure(inv,targets(inv,returns=[.01]),end,end).status=='not_testable'


def test_zero_initial_measurement_is_unassessable(prefix):
    _, snap, inv=prefix
    state=replace(inv.state,dimensions=tuple(replace(d,value=0.) if d.name=='volume_anomaly' else d for d in inv.state.dimensions))
    inv=I.open_investigation(state,'volume_anomaly',snap.bars,inv.registered_ms)
    u=I.advance(inv,I.measure(inv,(),inv.registered_ms,inv.registered_ms))
    assert u.next_action.kind=='UNASSESSABLE' and u.evidence.reason=='zero_or_missing_initial_component'
