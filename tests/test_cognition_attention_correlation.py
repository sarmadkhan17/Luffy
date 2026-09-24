"""SDD-STAGE-3-ATTENTION-CORRELATION-CHANGE-V1: synthetic, no network, LLM or
trading. An asset's correlation with its exact-grid leave-one-out cohort
peer basket, recent 30 vs baseline 120 returns of 151 captured closed closes,
competes in Attention as |signed Fisher-scaled change|; every failure is explicit."""
import ast
import copy
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from trader.cognition import attention as CA
from trader.cognition.contracts import (CORRELATION_BASELINE, CORRELATION_CAPTURE_CLOSES,
                                        CORRELATION_RECENT, load_input)
from trader.observability import attention as A, investigation as C
from tests._corr_synth import (ANCHOR, NOW, SYM, TF, candidate_steps, closes, frame,
                               peer_steps, publish, steps, universe)
from tests.test_cognition_attention_positioning import GOLDEN_OUTPUT, golden


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket

    def denied(*a, **kw):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def scan(data, sid="c1"):
    ev = A.capture(data, list(data), sid, A.settings(), NOW)
    ev["capture_ms"] = 0.0
    return ev


def rows(out):
    return {r["symbol"]: r for r in out["rows"]}


def obs(out, sym=SYM):
    [o] = [o for o in out["observations"] if o["kind"] == CA.CORRELATION_COMPONENT and o["symbol"] == sym]
    return o


def history(ev, sym=SYM):
    [h] = [h for h in ev["input"]["correlation_history"] if h["symbol"] == sym]
    return h


def log_returns(c):
    return [math.log(c[i] / c[i - 1]) for i in range(1, len(c))]


# ── absence preserves behavior exactly ───────────────────────────────────

def test_absent_input_is_byte_identical_to_pre_package_output():
    ev = golden()                                  # correlation_history removed
    assert "correlation_history" not in ev["input"]
    out = A.evaluate_snapshot(ev)
    assert A.digest(out) == GOLDEN_OUTPUT
    assert load_input(ev["input"]).correlation is None
    assert not any(o["kind"] == CA.CORRELATION_COMPONENT for o in out["observations"])
    assert all("correlation" not in r and CA.CORRELATION_COMPONENT not in r.get("components", {})
               for r in out["rows"])


def test_other_components_and_existing_outputs_are_unchanged_by_the_input():
    ev = scan(universe())
    bare = copy.deepcopy(ev)
    del bare["input"]["correlation_history"]
    with_c, without = rows(A.evaluate_snapshot(ev)), rows(A.evaluate_snapshot(bare))
    for sym, r in without.items():
        comps = dict(with_c[sym]["components"])
        assert comps.pop(CA.CORRELATION_COMPONENT, "absent") != "absent"
        assert comps == r["components"]


# ── capture: exact 151 closes, 120/30 split, no forming bar ──────────────

def test_capture_is_exactly_151_closed_closes_on_the_exact_grid():
    data = universe()
    data[SYM] = frame(closes(steps(5, n=199)))     # 200 closed bars available
    h = history(scan(data))
    assert CORRELATION_CAPTURE_CLOSES == 151 and (CORRELATION_BASELINE, CORRELATION_RECENT) == (120, 30)
    assert h["status"] == "ok" and len(h["closes"]) == 151
    assert h["last_open_ms"] == ANCHOR - TF and h["first_open_ms"] == ANCHOR - 151 * TF
    assert h["closes"] == list(data[SYM]["4h"]["close"])[-151:]


def test_a_still_forming_bar_never_enters_the_history():
    data = universe()
    c = closes(candidate_steps()) + [123456.0]      # forming bar opened at ANCHOR
    data[SYM] = frame(c, end_open=ANCHOR)
    assert NOW < ANCHOR + TF
    h = history(scan(data))
    assert h["status"] == "ok" and h["last_open_ms"] == ANCHOR - TF
    assert 123456.0 not in h["closes"] and h["closes"] == c[-152:-1]


def test_150_returns_split_into_non_overlapping_120_baseline_and_30_recent():
    base = [math.sin(i) for i in range(150)]
    ref = [math.cos(i * 1.3) + 0.5 * math.sin(i) for i in range(150)]
    _, d0, z0 = CA.correlation_change(base, ref)
    last_baseline = list(base); last_baseline[119] += 3.0
    _, d1, _ = CA.correlation_change(last_baseline, ref)
    assert d1["r_baseline"] != d0["r_baseline"] and d1["r_recent"] == d0["r_recent"]
    first_recent = list(base); first_recent[120] += 3.0
    _, d2, _ = CA.correlation_change(first_recent, ref)
    assert d2["r_recent"] != d0["r_recent"] and d2["r_baseline"] == d0["r_baseline"]
    x, y = np.array(base), np.array(ref)
    assert d0["r_baseline"] == CA.rnd(np.corrcoef(x[:120], y[:120])[0, 1])
    assert d0["r_recent"] == CA.rnd(np.corrcoef(x[120:], y[120:])[0, 1])
    assert CA.correlation_change(base[:149], ref[:149])[0] == "malformed"


def test_fisher_difference_is_divided_by_the_fixed_scale():
    ev = scan(universe())
    o = obs(A.evaluate_snapshot(ev))
    d = o["detail"]
    se = math.sqrt(1 / (120 - 3) + 1 / (30 - 3))
    assert CA.CORRELATION_SCALE == se and d["fisher_scale"] == CA.rnd(se)
    assert d["f_baseline"] == CA.rnd(math.atanh(d["r_baseline"]))
    assert d["f_recent"] == CA.rnd(math.atanh(d["r_recent"]))
    assert math.isclose(d["fisher_difference"], d["f_recent"] - d["f_baseline"], rel_tol=1e-10)
    assert math.isclose(o["value"], d["fisher_difference"] / se, rel_tol=1e-10)
    assert o["value"] == d["signed_fisher_scaled_change"] and d["correlation_change"] == abs(o["value"])


def test_salience_is_abs_change_never_the_raw_fisher_difference():
    out = A.evaluate_snapshot(scan(universe()))
    o, r = obs(out), rows(out)[SYM]
    comp = r["components"][CA.CORRELATION_COMPONENT]
    assert comp == abs(o["value"]) == r["salience"] and r["dominant"] == CA.CORRELATION_COMPONENT
    assert comp != abs(o["detail"]["fisher_difference"])
    assert r["correlation"]["signed_fisher_scaled_change"] == o["value"] < 0     # evidence keeps the sign


# ── leave-one-out, exact grid ─────────────────────────────────────────────

def test_leave_one_out_reference_excludes_the_candidate():
    data = universe()
    out = A.evaluate_snapshot(scan(data))
    d = obs(out)["detail"]
    peers = sorted(s for s in data if s != SYM)
    assert d["reference_peers"] == peers and SYM not in d["reference_peers"]
    rets = {s: log_returns(list(data[s]["4h"]["close"])) for s in data}
    ref = [math.fsum(rets[p][t] for p in peers) / len(peers) for t in range(150)]
    status, expect, z = CA.correlation_change(rets[SYM], ref)
    assert status == "ok" and obs(out)["value"] == CA.rnd(z)
    assert (d["r_baseline"], d["r_recent"]) == (expect["r_baseline"], expect["r_recent"])
    # Including the candidate would change the reference and the answer.
    inclusive = [math.fsum(rets[p][t] for p in data) / len(data) for t in range(150)]
    assert CA.correlation_change(rets[SYM], inclusive)[2] != z
    # Every peer's reference likewise excludes that peer.
    for p in peers:
        assert p not in obs(out, p)["detail"]["reference_peers"]


def test_timestamp_grids_are_exact_no_fill_or_nearest_bar():
    data = universe(n_peers=6)
    off = "S0/USDT"
    data[off] = frame(closes(peer_steps(0)), end_open=ANCHOR - 2 * TF)   # ends one bar early
    ev = scan(data)
    out = A.evaluate_snapshot(ev)
    assert history(ev, off)["status"] == "ok"      # a valid history, on another grid
    assert rows(out)[off]["status"] == "stale"
    d = obs(out)["detail"]
    assert off not in d["reference_peers"] and d["reference_size"] == 5


def test_history_on_a_shifted_grid_is_never_realigned():
    def shift(ev):
        h = history(ev)
        h.update(first_open_ms=h["first_open_ms"] - TF, last_open_ms=h["last_open_ms"] - TF)
    status, o, _ = _status(universe(), mutate=shift)
    assert status == "stale" and o["detail"]["first_open_ms"] == ANCHOR - 152 * TF
    def ahead(ev):
        h = history(ev)
        h.update(first_open_ms=h["first_open_ms"] + TF, last_open_ms=h["last_open_ms"] + TF)
    assert _status(universe(), mutate=ahead)[0] == "malformed"


def test_capture_gap_is_explicit_and_carries_no_closes():
    data = universe()
    df = frame(closes(peer_steps(2)[:20] + peer_steps(2)))["4h"]      # 171 closed bars
    data["S2/USDT"] = {"4h": df.drop(index=100).reset_index(drop=True)}
    ev = scan(data)
    h = history(ev, "S2/USDT")
    assert h == {"symbol": "S2/USDT", "status": "gap", "first_open_ms": None,
                 "last_open_ms": None, "closes": []}
    out = A.evaluate_snapshot(ev)
    assert obs(out, "S2/USDT")["status"] == "gap" and obs(out, "S2/USDT")["value"] is None
    assert "S2/USDT" not in obs(out)["detail"]["reference_peers"]


# ── ranking and symmetry ─────────────────────────────────────────────────

def test_decoupling_asset_ranks_on_correlation_change():
    out = A.evaluate_snapshot(scan(universe()))
    r = rows(out)[SYM]
    assert r["dominant"] == CA.CORRELATION_COMPONENT and r["rank"] == 1 and r["selected"]
    assert r["correlation"]["status"] == "ok" and r["correlation"]["signed_fisher_scaled_change"] < -2


def test_convergence_and_breakdown_of_equal_magnitude_rank_equally():
    down = A.evaluate_snapshot(scan(universe(sign=1)))
    up = A.evaluate_snapshot(scan(universe(sign=-1)))
    zd, zu = obs(down)["value"], obs(up)["value"]
    assert zd == -zu and zd < 0 < zu                 # breakdown vs convergence, exact mirror
    rd, ru = rows(down)[SYM], rows(up)[SYM]
    assert rd["components"][CA.CORRELATION_COMPONENT] == ru["components"][CA.CORRELATION_COMPONENT]
    assert (rd["salience"], rd["rank"], rd["dominant"], rd["reason"]) == \
           (ru["salience"], ru["rank"], ru["dominant"], ru["reason"])


def test_pure_feature_is_exactly_antisymmetric():
    x = [math.sin(i * 0.7) for i in range(150)]
    y = [math.sin(i * 0.7) + math.cos(i * 2.1) for i in range(120)] + [math.cos(i) for i in range(30)]
    _, _, z = CA.correlation_change(x, y)
    _, _, zm = CA.correlation_change([-v for v in x], y)
    assert z == -zm and abs(z) > 0


# ── fail closed: explicit status, never 0 ────────────────────────────────

def _status(data, sym=SYM, mutate=None):
    ev = scan(data)
    if mutate:
        mutate(ev)
    out = A.evaluate_snapshot(ev)
    r = rows(out)[sym]
    if r["status"] != "ok":
        return r["status"], None, r
    o = obs(out, sym)
    assert o["value"] is None and r["components"][CA.CORRELATION_COMPONENT] is None
    assert r["correlation"]["signed_fisher_scaled_change"] is None
    assert r["correlation"]["correlation_change"] is None
    return o["status"], o, r


def _set(sym, **kw):
    def mutate(ev):
        history(ev, sym).update(kw)
    return mutate


def test_fewer_than_151_closed_bars_is_warmup():
    data = universe()
    data[SYM] = frame(closes(candidate_steps()[:100]))
    assert history(scan(data))["status"] == "warmup"
    assert _status(data)[0] == "warmup"


def test_non_finite_close_is_explicit():
    data = universe()
    data[SYM]["4h"].loc[10, "close"] = float("nan")        # outside the candle window
    assert history(scan(data))["status"] == "non_finite"
    assert _status(data)[0] == "non_finite"


def test_zero_variance_candidate_returns():
    data = universe()
    data[SYM] = frame(closes([0] * 120 + steps(3, n=30)))
    status, o, _ = _status(data)
    assert status == "zero_variance_candidate" and o["detail"]["window"] == "baseline"


def test_zero_variance_leave_one_out_reference():
    data = universe()
    for j in (0, 2, 3, 4, 5):
        data[f"S{j}/USDT"] = frame(closes([0] * 120 + steps(50 + j, n=30)), extra=j)
    status, o, _ = _status(data)
    assert status == "zero_variance_reference" and o["detail"]["window"] == "baseline"


def test_insufficient_valid_leave_one_out_cohort():
    status, o, _ = _status(universe(n_peers=3))
    assert status == "insufficient_cohort" and o["detail"]["reference_size"] == 3
    assert o["detail"]["min_cohort"] == CA.CognitionConfig().min_cohort == 4


def test_non_finite_correlation_is_explicit(monkeypatch):
    monkeypatch.setattr(CA, "_pearson", lambda *a: None)
    status, o, _ = _status(universe())
    assert status == "non_finite_correlation"


def test_abs_r_of_one_makes_fisher_undefined():
    x = [math.sin(i * 0.3) for i in range(150)]
    assert CA.correlation_change(x, list(x))[0] == "fisher_undefined"
    assert CA.correlation_change(x, [-v for v in x])[0] == "fisher_undefined"
    assert CA.correlation_change(x, [2 * v + 1 for v in x])[0] == "fisher_undefined"


def test_pearson_never_silently_overflows_to_zero():
    x = [1e150 * math.sin(i) for i in range(150)]
    y = [1e150 * math.sin(i) + 1e150 * math.cos(i * 3) for i in range(150)]
    status, d, _ = CA.correlation_change(x, y)
    assert status == "ok" and d["r_baseline"] > 0.3


@pytest.mark.parametrize("mutate,expect", [
    (_set(SYM, closes=[1.0] * 150), "malformed"),                    # short
    (_set(SYM, last_open_ms=ANCHOR), "malformed"),                   # inconsistent grid
    (_set(SYM, first_open_ms=ANCHOR - 151 * TF + 1), "malformed"),   # off the bar grid
    (_set(SYM, status="bogus"), "malformed"),
    (_set(SYM, status="warmup"), "malformed"),                       # non-ok with closes
    (_set(SYM, extra=1), "malformed"),
    (lambda ev: history(ev).__setitem__("closes", history(ev)["closes"][:-1] + [None]), "non_finite"),
    (lambda ev: history(ev).__setitem__("closes", history(ev)["closes"][:-1] + [-1.0]), "non_finite"),
    (lambda ev: ev["input"]["correlation_history"].remove(history(ev)), "missing"),
    (lambda ev: ev["input"]["correlation_history"].append(
        dict(history(ev), closes=[c * 1.5 for c in history(ev)["closes"]])), "malformed"),  # conflicting
])
def test_malformed_captured_history_fails_closed(mutate, expect):
    status, o, r = _status(universe(), mutate=mutate)
    assert status == expect
    base = A.evaluate_snapshot(scan(universe()))
    assert r["salience"] != 0 and CA.CORRELATION_COMPONENT != r["dominant"]
    assert r["salience"] <= rows(base)[SYM]["salience"]


def test_history_disagreeing_with_captured_candles_is_malformed():
    def mutate(ev):
        h = history(ev)
        h["closes"] = h["closes"][:-1] + [h["closes"][-1] * 1.01]
    status, o, _ = _status(universe(), mutate=mutate)
    assert status == "malformed" and o["detail"]["reason"] == "candle_mismatch"


def test_identical_duplicate_record_is_not_a_failure():
    def mutate(ev):
        ev["input"]["correlation_history"].append(copy.deepcopy(history(ev)))
    ev = scan(universe())
    mutate(ev)
    out = A.evaluate_snapshot(ev)
    assert obs(out)["status"] == "ok"
    assert {"section": "correlation_history", "reason": "duplicate"}.items() <= out["rejected_inputs"][0].items()


def test_non_list_input_is_structural_error():
    ev = scan(universe())
    ev["input"]["correlation_history"] = {}
    with pytest.raises(ValueError):
        load_input(ev["input"])


# ── no significance claim; replay from captured input only ──────────────

def test_no_p_value_or_significance_claim_enters_the_payload():
    out = A.evaluate_snapshot(scan(universe()))
    o = obs(out)
    assert set(o["detail"]) == {
        "baseline_returns", "recent_returns", "fisher_scale", "r_baseline", "r_recent", "f_baseline",
        "f_recent", "fisher_difference", "signed_fisher_scaled_change", "correlation_change",
        "reference", "reference_policy", "reference_peers", "reference_size", "reference_membership_id",
        "min_cohort", "capture_closes", "baseline_return_bar_open_ms", "recent_return_bar_open_ms",
        "windows", "method", "interpretation"}
    assert o["detail"]["interpretation"] == "deterministic Fisher-scaled correlation-change feature"
    assert o["detail"]["reference"] == "captured leave-one-out peer basket"
    text = json.dumps([o, rows(out)[SYM], out["limitations"]]).lower()
    for word in ("p_value", "p-value", "pvalue", "significan", "confidence", "probability", "z-test",
                 "standardized", "bull", "bear", "long", "short", "buy", "sell", "market factor",
                 "sector", "btc proxy", "decoupl"):
        assert word not in text, word


def test_replay_recomputes_from_captured_input_only(tmp_path, monkeypatch):
    path = publish(tmp_path)
    payload = json.loads(sqlite3.connect(path).execute("SELECT payload FROM scans").fetchone()[0])
    assert payload["correlation_input"] == scan(universe())["input"]["correlation_history"]
    monkeypatch.setattr(A, "_history", lambda *a: pytest.fail("price history re-read"))
    monkeypatch.setattr(A, "capture", lambda *a, **k: pytest.fail("re-captured"))
    snap = C.adapt(C.source_snapshot(path), NOW)        # raises unless recompute == persisted
    assert snap.result["rows"][SYM]["dominant"] == CA.CORRELATION_COMPONENT


def test_cognition_module_imports_stay_stdlib():
    tree = ast.parse(Path(CA.__file__).read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert names == {"__future__", "math", "statistics", "sys", "dataclasses", "hashlib",
                     "trader.cognition.contracts"}


# ── frozen peer basket (targeted review) ─────────────────────────────────

def test_one_frozen_basket_for_both_windows_with_provenance():
    data = universe()
    out = A.evaluate_snapshot(scan(data))
    o, row = obs(out), rows(out)[SYM]
    d = o["detail"]
    peers = sorted(s for s in data if s != SYM)
    assert d["reference_peers"] == peers and d["reference_size"] == len(peers) and SYM not in peers
    assert d["reference_policy"] == "leave_one_out_equal_weight"
    assert d["reference_membership_id"] == CA.stable_id("peer_basket", "leave_one_out_equal_weight", peers)
    first = ANCHOR - 151 * TF
    assert d["baseline_return_bar_open_ms"] == [first + TF, first + 120 * TF]
    assert d["recent_return_bar_open_ms"] == [first + 121 * TF, ANCHOR - TF]
    assert "5 days" in d["windows"] and "20 days" in d["windows"]
    assert row["correlation"]["reference_membership_id"] == d["reference_membership_id"]
    # A single reference series from the same peers and equal weights at every
    # one of the 150 bars: recomputing that way reproduces both endpoints.
    rets = {s: log_returns(list(data[s]["4h"]["close"])) for s in data}
    ref = CA.basket_reference(rets, peers)
    assert ref == [math.fsum(rets[p][t] for p in peers) * (1 / len(peers)) for t in range(150)]
    _, expect, z = CA.correlation_change(rets[SYM], ref)
    assert (d["r_baseline"], d["r_recent"], o["value"]) == (expect["r_baseline"], expect["r_recent"], CA.rnd(z))


def test_basket_reference_refuses_a_peer_that_changes_per_bar():
    rets = {"A": [0.1] * 150, "B": [0.2] * 150, "C": [0.3] * 30}
    with pytest.raises(ValueError, match="peer_basket_incomplete"):
        CA.basket_reference(rets, ["A", "B", "C"])
    assert CA.peer_basket(dict(rets, X=[0.0] * 150), "X") == ["A", "B"]


def test_incomplete_peer_is_excluded_entirely_not_intermittently():
    data = universe(n_peers=6)
    short = "S7/USDT" if "S7/USDT" in data else sorted(s for s in data if s != SYM)[-1]
    # Only the most recent 60 bars exist: enough for the candle window,
    # not for the 151-close history. It must not join the recent window alone.
    data[short] = frame(closes(peer_steps(7)[-59:]), extra=7)
    ev = scan(data)
    assert history(ev, short)["status"] == "warmup"
    out = A.evaluate_snapshot(ev)
    d = obs(out)["detail"]
    assert short not in d["reference_peers"] and d["reference_size"] == 5
    assert rows(out)[short]["status"] == "ok"             # it is in the Attention cohort
    full = sorted(s for s in data if s not in (SYM, short))
    rets = {s: log_returns(list(data[s]["4h"]["close"])) for s in data}
    fixed = CA.correlation_change(rets[SYM], CA.basket_reference(rets, full))[1]
    assert (d["r_baseline"], d["r_recent"]) == (fixed["r_baseline"], fixed["r_recent"])
    # Including it for only the bars it has would change the recent endpoint.
    part = {150 - len(rets[short]) + i: v for i, v in enumerate(rets[short])}
    per_bar = [(math.fsum(rets[p][t] for p in full) + part.get(t, 0.0)) / (len(full) + (t in part))
               for t in range(150)]
    assert CA.correlation_change(rets[SYM], per_bar)[1]["r_recent"] != d["r_recent"]


def test_endpoints_persist_and_replay(tmp_path):
    path = publish(tmp_path)
    payload = json.loads(sqlite3.connect(path).execute("SELECT payload FROM scans").fetchone()[0])
    [o] = [o for o in payload["observations"] if o["kind"] == CA.CORRELATION_COMPONENT and o["symbol"] == SYM]
    row = {r["symbol"]: r for r in payload["rows"]}[SYM]["correlation"]
    d = o["detail"]
    assert None not in (d["r_baseline"], d["r_recent"], d["signed_fisher_scaled_change"], d["correlation_change"])
    assert (row["r_baseline"], row["r_recent"], row["signed_fisher_scaled_change"], row["correlation_change"]) == \
           (d["r_baseline"], d["r_recent"], o["value"], abs(o["value"]))
    snap = C.adapt(C.source_snapshot(path), NOW)          # recompute must equal persisted
    [r] = [x for x in snap.result["observations"] if x.kind == CA.CORRELATION_COMPONENT and x.symbol == SYM]
    assert (r.detail["r_baseline"], r.detail["r_recent"], r.value) == (d["r_baseline"], d["r_recent"], o["value"])


@pytest.mark.parametrize("r_value", [1.0, -1.0, 1.0000000000000002, -1.0000000000000002])
def test_saturated_correlation_fails_closed_without_clipping(monkeypatch, r_value):
    monkeypatch.setattr(CA, "_pearson", lambda *a: r_value)
    x = [math.sin(i) for i in range(150)]
    status, d, z = CA.correlation_change(x, [math.cos(i) for i in range(150)])
    assert (status, z, d["window"]) == ("fisher_undefined", None, "baseline")
    assert d["r_baseline"] == CA.rnd(r_value)            # the computed r, never pulled inside ±1
    assert "f_baseline" not in d and "signed_fisher_scaled_change" not in d
    status, _, row = _status(universe())
    assert status == "fisher_undefined" and row["components"][CA.CORRELATION_COMPONENT] is None


def test_quiet_context_does_not_invalidate_a_valid_change():
    # Correlation change is its own component: with volume and volatility
    # held quiet it is still ok, salient and dominant — never "unsupported".
    data = universe()
    for s in data:
        data[s]["4h"]["volume"] = 100.0
    out = A.evaluate_snapshot(scan(data))
    r = rows(out)[SYM]
    assert obs(out)["status"] == "ok" and r["dominant"] == CA.CORRELATION_COMPONENT and r["selected"]
    assert "unsupported" not in json.dumps(r)
