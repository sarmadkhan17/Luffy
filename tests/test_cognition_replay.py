"""Point-in-time replay: prefix invariance under future data and late
arrivals, deterministic IDs, duplicate inputs, outcomes for selected and
unselected assets with deadlines honoured, and the explicit-path CLI."""
import json
import subprocess
import sys

import pytest

from tests.test_cognition_contracts import (ROOT, TF, T0, at, make_fixture,  # noqa: F401
                                            no_network)
from trader.cognition.attention import CognitionConfig
from trader.cognition.replay import dump, main, run


def isolated(sym, i):
    return (0.02, 1.0) if sym == "CCC" and 40 <= i <= 44 else (0.0, 1.0)


def persistent(sym, i):
    return (0.02, 1.0) if sym == "CCC" and 40 <= i <= 49 else (0.0, 1.0)


def _set_available(raw, sym, bar, available_ms):
    for c in raw["candles"]:
        if c["symbol"] == sym and c["open_ms"] == T0 + bar * TF:
            c["available_ms"] = available_ms


def _outcomes(trace, kind=None):
    return [o for o in trace["outcomes"] if kind is None or o["subject_kind"] == kind]


def test_future_data_and_late_arrivals_do_not_rewrite_earlier_decisions():
    full = make_fixture(event=isolated, decisions=(45, 52, 58), n_bars=75)
    _set_available(full, "DDD", 51, at(57))                    # lands late
    revision = dict(next(c for c in full["candles"]
                         if c["symbol"] == "AAA" and c["open_ms"] == T0 + 44 * TF))
    revision.update(close=revision["close"] * 1.01, high=revision["high"] * 1.02,
                    available_ms=at(56))                        # revises an old bar later
    full["candles"].append(revision)
    full["candles"].append({"symbol": "AAA", "open_ms": T0 + 75 * TF, "open": 1.0,
                            "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
                            "closed": False})                  # forming bar

    cut = at(52)
    prefix = {**full, "decision_times": [t for t in full["decision_times"] if t <= cut],
              "candles": [c for c in full["candles"] if c.get("closed", True)
                          and c.get("available_ms", c["open_ms"] + TF) <= cut]}
    long_trace, short_trace = run(full), run(prefix)

    assert long_trace["decisions"][:2] == short_trace["decisions"]
    assert {"section": "candles", "index": len(full["candles"]) - 1,
            "reason": "incomplete_bar"} in long_trace["rejected_inputs"]
    dec52 = {r["symbol"]: r for r in short_trace["decisions"][1]["universe"]}
    assert dec52["DDD"]["status"] == "stale"                   # not yet arrived at 52
    dec58 = {r["symbol"]: r for r in long_trace["decisions"][2]["universe"]}
    assert dec58["DDD"]["status"] == "ok"

    def resolved_by(trace, t):
        return sorted((o for o in trace["outcomes"]
                       if o["resolved_at_ms"] is not None and o["resolved_at_ms"] <= t),
                      key=lambda o: o["outcome_id"])
    assert resolved_by(long_trace, cut) == resolved_by(short_trace, cut)
    assert resolved_by(short_trace, cut)                       # not vacuous


def test_duplicate_inputs_do_not_duplicate_episodes():
    raw = make_fixture(event=isolated, decisions=(45, 50))
    dup = {**raw, "decision_times": raw["decision_times"] * 2,
           "candles": raw["candles"] + [dict(c) for c in raw["candles"]],
           "membership": raw["membership"] * 2}
    assert dump(run(dup, end_ms=at(59))) == dump(run(raw, end_ms=at(59)))


def test_trace_is_deterministic_with_unique_ids():
    raw = make_fixture(event=isolated, decisions=(45, 50, 55))
    a, b = dump(run(raw, end_ms=at(60))), dump(run(raw, end_ms=at(60)))
    assert a == b
    t = json.loads(a)
    ids = [o["obs_id"] for d in t["decisions"] for o in d["observations"]]
    ids += [h["hyp_id"] for d in t["decisions"] for e in d["episodes"] for h in e["hypotheses"]]
    ids += [s["sample_id"] for d in t["decisions"] for s in d["baseline_samples"]]
    ids += [o["outcome_id"] for o in t["outcomes"]]
    ids += [d["decision_id"] for d in t["decisions"]]
    assert len(ids) == len(set(ids))


def test_outcomes_cover_selected_and_unselected_after_deadline():
    trace = run(make_fixture(event=persistent, decisions=(45,)), end_ms=at(60))
    (dec,) = trace["decisions"]
    assert dec["deadline_ms"] == at(50)
    for kind in ("baseline_raw", "baseline_relative"):
        base = _outcomes(trace, kind)
        assert len(base) == len(dec["baseline_samples"]) == 6
        assert sum(o["measurement"]["selected"] for o in base) == 1
    for o in trace["outcomes"]:
        assert o["status"] != "unresolved"
        assert o["resolved_at_ms"] >= o["deadline_ms"]
        assert o["measurement"]["bar_close_ms"] == o["deadline_ms"]
        assert o["measurement"]["tolerance_ms"] == 0
    status = {o["subject_id"]: o["status"] for o in _outcomes(trace, "hypothesis")}
    by_template = {h["template"]: status[h["hyp_id"]] for h in dec["episodes"][0]["hypotheses"]}
    # Divergence persisted (|rel_forward_z| >= persist_z), which falsifies
    # market_continuation whatever the sign of the cohort median.
    assert by_template == {"asset_divergence": "confirmed",
                           "market_continuation": "falsified", "unknown": "falsified"}


def test_market_continuation_resolves_on_a_broad_move():
    broad = lambda s, i: (0.01, 3.0 if i == 44 else 1.0) if 40 <= i <= 49 else (0.0, 1.0)
    trace = run(make_fixture(event=broad, decisions=(45,)), end_ms=at(60))
    status = {o["subject_id"]: o["status"] for o in _outcomes(trace, "hypothesis")}
    for ep in trace["decisions"][0]["episodes"]:
        assert ep["framing"] == "market_wide"
        got = {h["template"]: status[h["hyp_id"]] for h in ep["hypotheses"]}
        assert got == {"market_continuation": "confirmed",
                       "asset_divergence": "falsified", "unknown": "falsified"}


def test_divergence_that_fades_is_falsified():
    trace = run(make_fixture(event=isolated, decisions=(45,)), end_ms=at(60))
    status = {o["subject_id"]: o["status"] for o in _outcomes(trace, "hypothesis")}
    (ep,) = trace["decisions"][0]["episodes"]
    got = {h["template"]: status[h["hyp_id"]] for h in ep["hypotheses"]}
    assert got["asset_divergence"] == "falsified"
    assert (got["unknown"] == "confirmed") == ("confirmed" not in
                                               (got["asset_divergence"], got["market_continuation"]))


def test_before_deadline_everything_is_unresolved():
    trace = run(make_fixture(event=isolated, decisions=(45,), n_bars=48), end_ms=at(48))
    assert trace["outcomes"]
    assert {o["status"] for o in trace["outcomes"]} == {"unresolved"}
    assert {o["measurement"]["reason"] for o in trace["outcomes"]} == {"deadline_not_reached"}


def test_missing_deadline_bar_stays_unresolved_and_no_later_bar_is_substituted():
    trace = run(make_fixture(event=isolated, decisions=(45,), drop={("CCC", 49)}),
                end_ms=at(60))
    ccc = [o for o in trace["outcomes"] if o["measurement"]["symbol"] == "CCC"]
    assert len(ccc) == 5                                        # 3 hypotheses + raw + relative
    assert {o["status"] for o in ccc} == {"unresolved"}
    assert {o["measurement"]["reason"] for o in ccc} == {"deadline_bar_unavailable"}
    raw = [o for o in _outcomes(trace, "baseline_raw") if o["measurement"]["symbol"] != "CCC"]
    assert len(raw) == 5 and all(o["status"] == "measured" for o in raw)
    rel = [o for o in _outcomes(trace, "baseline_relative")
           if o["measurement"]["symbol"] != "CCC"]
    assert len(rel) == 5 and all(o["status"] == "unresolved" for o in rel)
    assert all(o["measurement"]["missing_cohort"] == ["CCC"] for o in rel)


def test_relative_outcomes_wait_for_the_whole_frozen_cohort():
    """Regression: the median was taken over whichever min_cohort subset had
    arrived, grading the hypothesis against a different cohort."""
    raw = make_fixture(event=persistent, decisions=(45,))
    _set_available(raw, "FFF", 49, at(55))                     # unselected member lands late
    early = run(raw, end_ms=at(52))
    hyp = _outcomes(early, "hypothesis")
    assert len(hyp) == 3 and {o["status"] for o in hyp} == {"unresolved"}
    assert {o["measurement"]["reason"] for o in hyp} == {"cohort_incomplete"}
    assert all(o["measurement"]["missing_cohort"] == ["FFF"] for o in hyp)
    assert {o["status"] for o in _outcomes(early, "baseline_relative")} == {"unresolved"}
    raw_status = {o["measurement"]["symbol"]: o["status"] for o in _outcomes(early, "baseline_raw")}
    assert raw_status.pop("FFF") == "unresolved"
    assert set(raw_status.values()) == {"measured"}           # own bar is enough

    late = run(raw, end_ms=at(56))
    assert all(o["status"] != "unresolved" for o in late["outcomes"])
    for o in _outcomes(late, "hypothesis") + _outcomes(late, "baseline_relative"):
        assert sorted(o["measurement"]["cohort_bars"]) == ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
        assert o["resolved_at_ms"] == at(56)


def test_micro_returns_resolve_without_zero_scales():
    """Regression: sigma was rounded to 0 before division -> ZeroDivisionError."""
    micro = lambda s, i: (5e-12, 1.0) if s == "CCC" and 40 <= i <= 44 else (0.0, 1.0)
    trace = run(make_fixture(event=micro, e=1e-12, decisions=(45,)), end_ms=at(60))
    json.loads(dump(trace))                                    # finite, serialisable
    (dec,) = trace["decisions"]
    assert len(dec["cohort"]) == 6
    assert all(0 < c["sigma"] < 1e-9 for c in dec["cohort"].values())
    assert dec["selected"] == ["CCC"]
    assert dec["episodes"][0]["framing"] == "asset_specific"
    assert all(o["status"] != "unresolved" for o in trace["outcomes"])
    for o in _outcomes(trace, "baseline_raw"):
        assert o["measurement"]["scale_status"] == "ok"
        assert o["measurement"]["abs_forward_z"] not in (None, 0.0)
    for o in _outcomes(trace, "baseline_relative"):
        assert o["measurement"]["rel_forward_z"] is not None
    assert {o["status"] for o in _outcomes(trace, "hypothesis")} <= {"confirmed", "falsified",
                                                                     "not_testable"}


def test_unusable_scale_is_explicit_not_a_crash():
    from trader.cognition.hypotheses import resolve_episode
    decision = {"deadline_ms": 1, "cohort_ok": True,
                "cohort": {s: {"anchor_close": 1.0, "sigma": 0.0} for s in "ABCD"}}
    fwd = {"per": {s: {"fwd": 0.1, "bar_close_ms": 1, "bar_available_ms": 1} for s in "ABCD"},
           "median": 0.1, "missing": [], "complete": True}
    hyps = [{"hyp_id": f"h{t}", "template": t, "deadline_ms": 1,
             "prediction": {"cohort_median_forward_sign": 1}}
            for t in ("market_continuation", "asset_divergence", "unknown")]
    out = resolve_episode({"symbol": "A"}, hyps, decision, fwd, 1, CognitionConfig())
    assert {o.status for o in out} == {"not_testable"}
    assert {o.measurement["reason"] for o in out} == {"unusable_scale"}


def test_membership_removal_does_not_rewrite_the_earlier_decision():
    raw = make_fixture(event=isolated, decisions=(45, 50))
    closed = dict(next(m for m in raw["membership"] if m["symbol"] == "FFF"),
                  to_ms=at(48), available_ms=at(49))
    revised = {**raw, "membership": raw["membership"] + [closed]}
    before, after = run(raw), run(revised)
    assert after["decisions"][0] == before["decisions"][0]
    assert "FFF" in after["decisions"][0]["members"]
    assert "FFF" not in after["decisions"][1]["members"]
    assert "FFF" in before["decisions"][1]["members"]


@pytest.mark.parametrize("bad", [-1, 1.5, True, "123"])
def test_end_ms_must_be_a_timestamp(bad):
    with pytest.raises(ValueError, match="end_ms"):
        run(make_fixture(), end_ms=bad)


def test_cli_rejects_bad_end_ms_and_nan_threshold(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(make_fixture()))
    out = str(tmp_path / "out.json")
    with pytest.raises(SystemExit):
        main(["--input", str(src), "--output", out, "--end-ms", "-5"])
    with pytest.raises(SystemExit):
        main(["--input", str(src), "--output", out, "--min-salience", "nan"])
    assert not (tmp_path / "out.json").exists()


def test_late_deadline_bar_resolves_only_once_it_has_arrived():
    raw = make_fixture(event=isolated, decisions=(45,))
    _set_available(raw, "CCC", 49, at(53))
    early = run(raw, end_ms=at(52))
    assert {o["status"] for o in early["outcomes"]
            if o["measurement"]["symbol"] == "CCC"} == {"unresolved"}
    late = run(raw, end_ms=at(54))
    ccc = [o for o in late["outcomes"] if o["measurement"]["symbol"] == "CCC"]
    assert all(o["resolved_at_ms"] == at(54) for o in ccc)
    assert all(o["measurement"]["bar_available_ms"] == at(53) for o in ccc)
    assert early["decisions"] == late["decisions"]


def test_cli_requires_explicit_paths(tmp_path):
    with pytest.raises(SystemExit):
        main([])
    path = tmp_path / "in.json"
    path.write_text(json.dumps(make_fixture()))
    with pytest.raises(SystemExit):
        main(["--input", str(path), "--output", str(path)])


def test_cli_writes_trace_to_temp_path(tmp_path, capsys):
    src, out = tmp_path / "fixture.json", tmp_path / "trace.json"
    src.write_text(json.dumps(make_fixture(event=isolated, decisions=(45, 50))))
    assert main(["--input", str(src), "--output", str(out), "--end-ms", str(at(60))]) == 0
    trace = json.loads(out.read_text())
    assert trace["schema_version"] == "cognition.v1"
    assert trace["decisions"][0]["episodes"][0]["framing"] == "asset_specific"
    assert any("profitability" in x for x in trace["limitations"])
    assert "decisions=2" in capsys.readouterr().out


def test_cli_module_runs_offline_in_a_subprocess(tmp_path):
    src, out = tmp_path / "fixture.json", tmp_path / "trace.json"
    src.write_text(json.dumps(make_fixture(event=isolated)))
    code = ("import socket, runpy, sys\n"
            "def boom(*a, **k): raise RuntimeError('network access attempted')\n"
            "socket.socket.connect = boom\n"
            "socket.create_connection = boom\n"
            f"sys.argv = ['replay', '--input', {str(src)!r}, '--output', {str(out)!r}]\n"
            "runpy.run_module('trader.cognition.replay', run_name='__main__')\n")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True,
                   capture_output=True, text=True)
    assert json.loads(out.read_text())["decisions"]
