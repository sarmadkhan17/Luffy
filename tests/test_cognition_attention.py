"""Attention and framing on synthetic fixtures: broad move vs isolated
divergence vs unknown, contradictions retained, bounded stable selection."""
import math

import pytest

from tests.test_cognition_contracts import SYMBOLS, at, make_fixture, no_network  # noqa: F401
from trader.cognition.attention import CognitionConfig
from trader.cognition.replay import run


def broad(sym, i):
    """Every symbol climbs +1%/bar for the last five bars, volume triples on the anchor."""
    return (0.01, 3.0 if i == 44 else 1.0) if 40 <= i <= 44 else (0.0, 1.0)


def isolated(sym, i):
    return (0.02, 1.0) if sym == "CCC" and 40 <= i <= 44 else (0.0, 1.0)


def contested(sym, i):
    if 40 <= i <= 44:
        return 0.01 + (0.03 if sym == "AAA" else 0.0), 1.0
    return 0.0, 1.0


def one_decision(raw, **cfg):
    trace = run(raw, CognitionConfig(**cfg))
    assert len(trace["decisions"]) == 1
    return trace["decisions"][0]


def rows(dec):
    return {r["symbol"]: r for r in dec["universe"]}


def obs(dec):
    return {o["obs_id"]: o for o in dec["observations"]}


def hyps(episode):
    return {h["template"]: h for h in episode["hypotheses"]}


def test_broad_move_is_framed_market_wide():
    dec = one_decision(make_fixture(event=broad))
    assert dec["market_state"]["status"] == "ok"
    assert dec["market_state"]["broad"] is True
    assert dec["market_state"]["breadth_up"] == 1.0
    assert len(dec["episodes"]) == 3                    # six tied-ish candidates, K = 3
    for ep in dec["episodes"]:
        assert ep["framing"] == "market_wide"
        h = hyps(ep)
        assert h["market_continuation"]["supporting_ids"] == [dec["market_state"]["obs_id"]]
        assert dec["market_state"]["obs_id"] in h["asset_divergence"]["contradicting_ids"]
        assert all(x["probability"] is None for x in ep["hypotheses"])


def test_isolated_divergence_is_framed_asset_specific():
    participation = [{"symbol": "CCC", "kind": "open_interest", "event_ms": at(44),
                      "available_ms": at(44), "value": 1000.0},
                     {"symbol": "CCC", "kind": "open_interest", "event_ms": at(45),
                      "available_ms": at(45), "value": 1150.0}]
    dec = one_decision(make_fixture(event=isolated, participation=participation))
    assert dec["market_state"]["broad"] is False
    assert dec["selected"] == ["CCC"]
    r = rows(dec)["CCC"]
    assert r["dominant"] == "relative_return_divergence" and r["salience"] > 5
    (ep,) = dec["episodes"]
    assert ep["framing"] == "asset_specific"
    by_id = obs(dec)
    sup = [by_id[i]["kind"] for i in hyps(ep)["asset_divergence"]["supporting_ids"]]
    assert sup == ["relative_return_divergence", "participation"]
    assert hyps(ep)["asset_divergence"]["contradicting_ids"] == []


def test_contradicting_evidence_is_kept_on_both_sides():
    dec = one_decision(make_fixture(event=contested))
    ep = next(e for e in dec["episodes"] if e["symbol"] == "AAA")
    assert ep["framing"] == "contested" and ep["contradictions_present"]
    h, by_id = hyps(ep), obs(dec)
    assert [by_id[i]["kind"] for i in h["market_continuation"]["supporting_ids"]] == ["market_breadth"]
    assert [by_id[i]["kind"] for i in h["market_continuation"]["contradicting_ids"]] == [
        "relative_return_divergence"]
    assert [by_id[i]["kind"] for i in h["asset_divergence"]["supporting_ids"]] == [
        "relative_return_divergence"]
    assert [by_id[i]["kind"] for i in h["asset_divergence"]["contradicting_ids"]] == ["market_breadth"]


def test_stale_cohort_makes_the_episode_unknown_not_zero():
    spike = lambda s, i: (0.0, 3.0) if s == "AAA" and i == 44 else (0.0, 1.0)
    dec = one_decision(make_fixture(event=spike, drop={(s, 44) for s in ("DDD", "EEE", "FFF")}))
    r = rows(dec)
    assert [r[s]["status"] for s in ("DDD", "EEE", "FFF")] == ["stale"] * 3
    assert all(r[s]["eligible"] is False for s in ("DDD", "EEE", "FFF"))
    assert dec["market_state"]["status"] == "insufficient_cohort"
    assert dec["market_state"]["market_z"] is None
    assert r["AAA"]["components"]["relative_return_divergence"] is None
    (ep,) = dec["episodes"]
    assert ep["symbol"] == "AAA"
    assert ep["framing"] == "unknown"
    assert ep["framing_reason"] == "insufficient_required_evidence"
    missing = [obs(dec)[i]["status"] for i in hyps(ep)["unknown"]["supporting_ids"]]
    assert missing == ["insufficient_cohort", "insufficient_cohort"]
    assert hyps(ep)["market_continuation"]["prediction"]["cohort_median_forward_sign"] is None


def test_warmup_and_gap_are_not_eligible():
    drop = {("AAA", i) for i in range(0, 30)} | {("BBB", 30)}
    dec = one_decision(make_fixture(event=broad, drop=drop))
    r = rows(dec)
    assert r["AAA"]["status"] == "warmup" and r["BBB"]["status"] == "gap"
    assert "AAA" not in dec["cohort"] and "BBB" not in dec["cohort"]


def test_selection_is_bounded_with_stable_ties():
    dec = one_decision(make_fixture(event=broad), k=2)
    ranked = sorted((r for r in dec["universe"] if r.get("rank")), key=lambda r: r["rank"])
    keys = [(-r["salience"], r["symbol"]) for r in ranked]
    assert keys == sorted(keys)
    assert dec["selected"] == [r["symbol"] for r in ranked[:2]]
    assert len({r["salience"] for r in ranked}) < len(ranked)      # ties exist
    assert {r["reason"] for r in ranked[2:]} == {"beyond_top_k"}


def test_salience_is_direction_independent():
    down = lambda s, i: (-broad(s, i)[0], broad(s, i)[1])
    up_rows, down_rows = rows(one_decision(make_fixture(event=broad))), rows(
        one_decision(make_fixture(event=down)))
    for sym in SYMBOLS:
        assert up_rows[sym]["salience"] == pytest.approx(down_rows[sym]["salience"], rel=0.05)
    assert [s for s in sorted(up_rows, key=lambda s: (-up_rows[s]["salience"], s))][:3] == \
        [s for s in sorted(down_rows, key=lambda s: (-down_rows[s]["salience"], s))][:3]


@pytest.mark.parametrize("field,value", [
    ("min_salience", float("nan")), ("min_salience", float("inf")), ("min_salience", -1.0),
    ("broad_z", float("nan")), ("broad_breadth", 1.5), ("broad_breadth", 0.2),
    ("divergence_z", 0.0), ("persist_z", float("-inf")), ("participation_change", "0.1"),
    ("k", True), ("k", 2.0), ("k", 0), ("window", "20"), ("short", 1),
    ("horizon", 0), ("min_cohort", 1), ("participation_stale_bars", -1),
    ("max_symbols", 0), ("max_decisions", False), ("divergence_z", True),
])
def test_config_rejects_non_finite_mistyped_and_out_of_bounds(field, value):
    """Regression: min_salience=NaN bypassed the threshold (NaN < x is False)."""
    with pytest.raises(ValueError, match=field):
        CognitionConfig(**{field: value})


def test_participation_series_are_never_mixed():
    """Regression: two sources of the same kind were diffed against each other."""
    participation = [
        {"symbol": "CCC", "kind": "open_interest", "source": "venA", "event_ms": at(43),
         "available_ms": at(43), "value": 1000.0},
        {"symbol": "CCC", "kind": "open_interest", "source": "venA", "event_ms": at(44),
         "available_ms": at(44), "value": 1010.0},
        {"symbol": "CCC", "kind": "open_interest", "source": "venA", "event_ms": at(44),
         "available_ms": at(45), "value": 1200.0},                 # revision, known by 45
        {"symbol": "CCC", "kind": "open_interest", "source": "venB", "event_ms": at(45),
         "available_ms": at(45), "value": 5000.0},                 # other venue, other scale
    ]
    dec = one_decision(make_fixture(event=isolated, participation=participation))
    parts = [o for o in dec["observations"]
             if o["kind"] == "participation" and o["symbol"] == "CCC"]
    by_src = {o["source"]: o for o in parts}
    assert set(by_src) == {"venA", "venB"} and len({o["obs_id"] for o in parts}) == 2
    a, b = by_src["venA"], by_src["venB"]
    assert a["status"] == "ok"
    assert a["detail"]["current"]["value"] == 1200.0 and a["detail"]["previous"]["value"] == 1000.0
    assert a["value"] == pytest.approx(math.log(1.2))
    assert b["status"] == "warmup" and b["value"] is None       # no cross-series diff
    (ep,) = dec["episodes"]
    assert hyps(ep)["asset_divergence"]["supporting_ids"] == [
        rows(dec)["CCC"]["evidence"][3], a["obs_id"]]


def test_derived_participation_is_available_only_when_both_endpoints_are():
    """Regression: a newer point can land before the prior one; the change must
    carry both endpoints and the later availability."""
    participation = [
        {"symbol": "CCC", "kind": "open_interest", "source": "venA", "event_ms": at(43),
         "available_ms": at(46), "value": 1000.0},                 # prior value lands late
        {"symbol": "CCC", "kind": "open_interest", "source": "venA", "event_ms": at(44),
         "available_ms": at(44), "value": 1100.0},
    ]
    trace = run(make_fixture(event=isolated, decisions=(45, 47), participation=participation),
                CognitionConfig(participation_stale_bars=5))
    first, second = [
        next(o for o in d["observations"] if o["kind"] == "participation" and o["symbol"] == "CCC")
        for d in trace["decisions"]]
    assert first["status"] == "warmup" and first["detail"]["previous"] is None
    assert second["status"] == "ok"
    assert second["value"] == pytest.approx(math.log(1.1))
    assert second["event_ms"] == at(44)
    assert second["available_ms"] == at(46)
    assert second["detail"]["previous"]["available_ms"] == at(46)
    assert second["detail"]["current"]["available_ms"] == at(44)
    assert second["detail"]["previous"]["record_id"] != second["detail"]["current"]["record_id"]
    for d in trace["decisions"]:
        for o in d["observations"]:
            assert o["available_ms"] is None or o["available_ms"] <= o["as_of_ms"]


def test_open_episode_is_not_duplicated_on_the_next_decision():
    raw = make_fixture(event=isolated, decisions=(45, 46))
    first, second = run(raw)["decisions"]
    assert first["selected"] == ["CCC"]
    assert second["selected"] == []
    r = rows(second)["CCC"]
    assert r["reason"] == "open_episode"
    assert r["open_episode_id"] == first["episodes"][0]["episode_id"]
    assert all(math.isfinite(x["salience"]) for x in second["universe"] if x.get("rank"))
