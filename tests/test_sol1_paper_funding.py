"""Funding-crossing paper trades: priced from published evidence, or the whole
probation is INCOMPLETE_COST_EVIDENCE. Never excluded, never guessed.

No network: every request goes through an injected, recording fetch."""
import json
import sqlite3
import types

import pandas as pd
import pytest

from trader.engine import paper_cost_evidence as C
from trader.observability import funding_events as FE
from trader.strategy import factory_handoff as F
from tests.test_sol1_paper_cost import (  # noqa: F401  (fixtures + helpers)
    NOW, Run, cfg, run_outcomes)


def ms(value):
    return int(pd.Timestamp(value).timestamp() * 1000)


def rows_fetch(rows_of, calls=None):
    """TEST-ONLY public endpoint: rows_of(start, end) -> list of row dicts."""
    def fetch(url, timeout_s=None, max_bytes=None):
        if calls is not None:
            calls.append(url)
        q = dict(p.split("=") for p in url.split("?")[1].split("&"))
        rows = rows_of(int(q["startTime"]), int(q["endTime"]))
        return types.SimpleNamespace(status=200, body=json.dumps(rows).encode())
    return fetch


def mid_event(rate="0.0001", mark="110.0", extra=()):
    def rows_of(start, end):
        return [dict(symbol="BTCUSDT", fundingTime=(start + end) // 2 if i == 0 else t,
                     fundingRate=rate, markPrice=mark)
                for i, t in enumerate((None, *extra))][:1 + len(extra)]
    return rows_of


def priced(r, outcome, rate="0.0001", mark="110.0", **kw):
    tid = r.trade(outcome)
    assert r.complete(fetch=rows_fetch(mid_event(rate, mark)), **kw) == [tid]
    return tid


def test_a_winning_crossing_trade_is_priced_from_the_published_rate_and_mark(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = priced(r, "win", rate="0.0003", mark="111.5")
    rec, row = r.receipt(tid), r.row(tid)
    f = rec["dimensions"]["funding"]
    assert f["status"] == "ESTABLISHED" and f["reason"] == "ACTUAL_FUNDING_EVENTS"
    q = row["amount"]
    assert f["amount"] == pytest.approx(0.0003 * 111.5 * q, rel=1e-12)       # long pays
    assert len(f["events"]) == 1 and f["events"][0]["quantity"] == q
    # no flat 8h fallback anywhere, and funding enters the net exactly once
    assert f["amount"] != pytest.approx(0.0001 * row["exit_price"] * q)
    assert rec["net_pnl_status"] == "ESTABLISHED"
    d = rec["dimensions"]
    assert rec["known_costs"] == pytest.approx(
        d["commission"]["amount"] + d["slippage"]["amount"] + f["amount"])
    assert rec["net_pnl"] == pytest.approx(rec["gross_pnl"] - rec["known_costs"])
    src = json.loads(r.j.query(f"SELECT canonical_json FROM {C.SOURCES} WHERE source_id=?",
                               (rec["source_ids"]["funding"],))[0]["canonical_json"])
    assert src["method"] == FE.CROSSING_METHOD
    assert src["provenance"]["label"] == "ESTIMATED_FROM_PUBLISHED_RATE_AND_MARK_NOT_VENUE_ACTUAL"
    assert len(src["provenance"]["assumptions"]) == 4
    C.verify(r.j, row, r.v, F.verify_install(r.j, r.v, current=False))


def test_a_losing_crossing_trade_and_a_negative_rate_credit(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    lose = priced(r, "loss", rate="0.0002", mark="100.0")
    credit = priced(r, "win", rate="-0.0004", mark="100.0")
    for tid, sign in ((lose, 1), (credit, -1)):
        rec, row = r.receipt(tid), r.row(tid)
        assert rec["dimensions"]["funding"]["amount"] == pytest.approx(
            sign * abs(float(0.0002 if sign > 0 else 0.0004)) * 100.0 * row["amount"])
        assert rec["net_pnl_status"] == "ESTABLISHED"
    loss = r.receipt(lose)
    assert loss["net_pnl"] < loss["gross_pnl"] < 0                      # a loss only gets worse
    win = r.receipt(credit)
    assert win["known_costs"] < (win["dimensions"]["commission"]["amount"]
                                 + win["dimensions"]["slippage"]["amount"])  # the credit reduces cost


def test_two_events_are_both_priced_and_not_double_counted(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = r.trade("win")
    row = r.row(tid)
    a, b = ms(row["opened_at"]) + 1000, ms(row["closed_at"]) - 1000

    def rows_of(start, end):
        return [dict(symbol="BTCUSDT", fundingTime=a, fundingRate="0.0001", markPrice="100.0"),
                dict(symbol="BTCUSDT", fundingTime=b, fundingRate="0.0002", markPrice="120.0")]
    assert r.complete(fetch=rows_fetch(rows_of)) == [tid]
    f = r.receipt(tid)["dimensions"]["funding"]
    assert f["amount"] == pytest.approx((0.0001 * 100.0 + 0.0002 * 120.0) * row["amount"])


def test_ten_crossing_trades_pass_ten_trade_qualification(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    for o in ["win"] * 7 + ["loss"] * 3:
        r.trade(o)
    assert len(r.complete(fetch=rows_fetch(mid_event()), limit=10)) == 10
    a = r.assess()
    assert a["status"] == F.P_SATISFIED and a["request_id"]
    p = json.loads(r.j.query("SELECT canonical_json FROM strategy_probation_receipts")[0]["canonical_json"])
    assert p["stats"]["trades"] == 10 and p["excluded_trades"] == []
    assert all(t["cost_evidence"]["dimensions"]["funding"]["status"] == "ESTABLISHED" for t in p["trades"])
    assert p["stats"]["pnl"] == pytest.approx(
        sum(t["realized_pnl"] - t["cost_evidence"]["known_costs"] for t in p["trades"]))


def test_funding_can_turn_a_gross_pass_into_a_negative_net_failure(tmp_path, cfg):
    """Gross and no-funding net clear the profit-factor gate; the published
    funding the trades really paid takes the net below it."""
    base = Run(tmp_path / "a", cfg)
    run_outcomes(base, ["win"] * 5 + ["loss"] * 5)
    assert base.assess()["status"] == F.P_SATISFIED
    r = Run(tmp_path / "b", cfg)
    for o in ["win"] * 5 + ["loss"] * 5:
        r.trade(o)
    assert len(r.complete(fetch=rows_fetch(mid_event(rate="0.05", mark="110.0")), limit=10)) == 10
    a = r.assess()
    p = json.loads(r.j.query("SELECT canonical_json FROM strategy_probation_receipts")[0]["canonical_json"])
    assert p["gross_stats"]["profit_factor"] >= 1.15
    assert p["stats"]["profit_factor"] < 1.0 and p["stats"]["pnl"] < 0   # negative net PF
    assert a["status"] == F.P_NOT_SATISFIED and a["request_id"] is None


def refused_run(tmp_path, cfg, rows_of):
    """Nine fully evidenced trades and a tenth whose funding cannot be priced."""
    r = Run(tmp_path, cfg)
    run_outcomes(r, ["win"] * 7 + ["loss"] * 2)
    tid = r.trade("win")
    assert r.complete(fetch=rows_fetch(rows_of)) == [tid]
    return r, tid


@pytest.mark.parametrize("name", ["boundary", "missing_mark", "zero_mark", "non_numeric_mark"])
def test_unpriceable_funding_is_incomplete_evidence_never_a_selective_exclusion(tmp_path, cfg, name):
    holder = {}

    def rows_of(start, end):
        t = start if name == "boundary" else (start + end) // 2
        row = dict(symbol="BTCUSDT", fundingTime=t, fundingRate="0.0001", markPrice="110.0")
        if name == "missing_mark":
            del row["markPrice"]
        elif name == "zero_mark":
            row["markPrice"] = "0"
        elif name == "non_numeric_mark":
            row["markPrice"] = "n/a"
        return [row]
    r, tid = refused_run(tmp_path, cfg, rows_of)
    rec = r.receipt(tid)
    assert rec["net_pnl_status"] == "UNAVAILABLE" and rec["net_pnl"] is None
    assert rec["dimensions"]["funding"]["status"] == "UNAVAILABLE" and "funding" not in rec["source_ids"]
    a = r.assess()
    assert a["status"] == F.P_COST_INCOMPLETE and a["request_id"] is None
    assert F.state_of(r.j, r.vid) == F.SHADOW
    p = json.loads(r.j.query("SELECT canonical_json FROM strategy_probation_receipts")[0]["canonical_json"])
    assert [m["trade_id"] for m in p["missing_cost_evidence"]] == [tid]
    assert "funding" in p["missing_cost_evidence"][0]["dimensions"]
    assert p["excluded_trades"] == [] and p["stats"] is None
    # nine good trades do not pass in its place, and eleven trades cannot hide it
    r.trade("loss")
    r.complete()
    assert r.assess(day=31)["status"] == F.P_COST_INCOMPLETE


def test_pending_then_retry_and_a_failed_fetch_blocks_rather_than_skips(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    t1, t2 = r.trade("win"), r.trade("loss")

    def down(url, timeout_s=None, max_bytes=None):
        raise OSError("offline")

    def bad_status(url, timeout_s=None, max_bytes=None):
        return types.SimpleNamespace(status=500, body=b"[]")
    assert r.complete(fetch=down) == [] and r.complete(fetch=bad_status) == []
    assert r.receipt(t1) is None and r.receipt(t2) is None
    assert r.assess()["status"] == F.P_COST_INCOMPLETE                   # pending blocks
    assert r.complete(fetch=rows_fetch(mid_event()), limit=2) == [t1, t2]
    assert r.receipt(t1)["net_pnl_status"] == "ESTABLISHED"
    assert r.complete(fetch=down) == []                                  # finalized: no refetch


def test_a_stored_crossing_receipt_replays_identically_and_resists_tampering(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    tid = priced(r, "win", rate="0.0003", mark="111.5")
    body = r.receipt(tid)
    ids = body["source_ids"]
    fresh = sqlite3.connect(":memory:")
    fresh.row_factory = sqlite3.Row
    C.ensure(fresh)
    stored = {}
    for kind, sid in ids.items():
        stored[kind] = json.loads(r.j.query(
            f"SELECT canonical_json FROM {C.SOURCES} WHERE source_id=?", (sid,))[0]["canonical_json"])
        C.freeze_source(fresh, stored[kind])
    assert C.build(fresh, body["binding"], ids) == body
    forged = json.loads(json.dumps(stored["funding"]))
    for mutate in (lambda s: s["events"][0].__setitem__("rate", 0.0),
                   lambda s: s["events"][0].__setitem__("position_basis_price", 1.0),
                   lambda s: s["events"].clear(),
                   lambda s: s["provenance"].__setitem__("environment", "demo")):
        bad = json.loads(json.dumps(forged))
        mutate(bad)
        with pytest.raises(ValueError):
            C.freeze_source(fresh, bad)
    # published evidence in the frozen receipt cannot be edited either
    with pytest.raises(Exception):
        with r.j._tx() as db:
            db.execute(f"UPDATE {C.SOURCES} SET canonical_json='x'")


def test_the_crossing_source_does_not_apply_to_other_trades_or_environments(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    t1, t2 = priced(r, "win"), priced(r, "loss")
    src = json.loads(r.j.query(f"SELECT canonical_json FROM {C.SOURCES} WHERE source_id=?",
                               (r.receipt(t1)["source_ids"]["funding"],))[0]["canonical_json"])
    other = r.receipt(t2)["binding"]
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    C.ensure(db)
    C.freeze_source(db, src)
    with pytest.raises(ValueError, match="cost_source_binding_differs"):
        C.load_source(db, src["source_id"], other, "funding")


def test_funding_requests_respect_the_existing_controls(tmp_path, cfg, monkeypatch):
    """Only the public fundingRate GET, only when admitted and production-proven;
    no LLM provider is reachable even with brain disabled or enabled."""
    assert cfg["brain"]["enabled"] is False and cfg["research"]["referee"] is False
    r = Run(tmp_path, cfg)
    tid = r.trade("win")
    calls = []
    # owner switch off: no request at all, the trade stays pending, probation blocks
    r.paper.cfg = {**cfg, "paper_cost": {"funding_history_lookup": False}}
    assert r.complete(fetch=rows_fetch(mid_event(), calls)) == [] and calls == []
    assert r.receipt(tid) is None and r.assess()["status"] == F.P_COST_INCOMPLETE
    r.paper.cfg = cfg
    # admitted: exactly one bounded GET to the documented public endpoint, nothing else
    from trader.brain import llm
    monkeypatch.setattr(llm.Brain if hasattr(llm, "Brain") else llm, "__init__",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM provider reached")),
                        raising=False)
    assert r.complete(fetch=rows_fetch(mid_event(), calls)) == [tid]
    assert len(calls) == 1 and calls[0].startswith(FE.ENDPOINT + "?symbol=BTCUSDT&startTime=")
    assert "limit=1000" in calls[0]
    import inspect
    from trader.engine import paper
    src = inspect.getsource(paper) + inspect.getsource(FE)
    for forbidden in ("trader.brain", "llm", "Env.", "api_key", "apiKey", "signature"):
        assert forbidden not in src.split("class PaperExecutor")[0] + inspect.getsource(FE)
    assert FE.lookup.__kwdefaults__["fetch"].__name__ == "urllib_fetch"


def test_a_non_production_trade_makes_no_request_and_blocks_probation(tmp_path, cfg):
    r = Run(tmp_path, cfg)
    r.paper.venue_environment = "demo"
    tid = r.trade("win")
    calls = []
    assert r.complete(fetch=rows_fetch(mid_event(), calls)) == [tid] and calls == []
    rec = r.receipt(tid)
    assert rec["net_pnl_status"] == "UNAVAILABLE" and rec["dimensions"]["funding"]["status"] == "UNAVAILABLE"
    assert r.assess()["status"] == F.P_COST_INCOMPLETE


def test_kernel_loop_honours_the_funding_lookup_switch(tmp_path, cfg, monkeypatch):
    from trader.kernel import Kernel
    r = Run(tmp_path, cfg)
    tid = r.trade("win")
    calls = []
    import functools
    monkeypatch.setattr(FE, "lookup", functools.partial(
        FE.lookup, fetch=rows_fetch(mid_event(), calls), clock=lambda: NOW))
    k = Kernel.__new__(Kernel)
    k.journal, k.cfg, k._paper = r.j, cfg, r.paper
    from tests.test_sol1_paper_cost import snapshot, iso, T0, DAY
    snap = snapshot(r.frame())
    snap.ts = iso(T0 + 30 * DAY)
    r.paper.cfg = {**cfg, "paper_cost": {"funding_history_lookup": False}}
    k._paper_cost_at = -1e9
    assert k._manage_paper(snap) == [] and calls == []
    r.paper.cfg = cfg
    k._paper_cost_at = -1e9
    assert k._manage_paper(snap) == [tid] and len(calls) == 1
