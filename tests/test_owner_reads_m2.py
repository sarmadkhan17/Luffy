"""Owner frontend M2: connected evidence reads in trader/dashboard/owner_reads.py
(trade story, decision detail, research item, strategy workspace, operations
timeline, overview activity, observed flows, note/chat record links, control
event paging, probes). Exact-id joins only; missing and malformed records are
reported, never filled in."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.owner_frontend_fixture import FakeChat, make_app, seed_m2
from trader.dashboard import owner_reads

H = {"x-luffy-token": "fixture-token"}
API = "/owner-api/v1"


@pytest.fixture
def m2(tmp_path, monkeypatch):
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, journal, _ = make_app(tmp_path, monkeypatch)
    seed_m2(tmp_path, journal)
    return TestClient(app), journal, app, tmp_path


def get(c, path, status=200, **params):
    r = c.get(API + path, headers=H, params=params)
    assert r.status_code == status, (path, r.status_code, r.text[:300])
    return r.json()


def fields(d):
    return {u["field"] for u in d["unavailable"]}


# ── auth ──────────────────────────────────────────────────────────────────────
def test_m2_routes_require_auth(m2):
    c, *_ = m2
    for path in ("decisions/dec_fixture01", "research/combos/a1b2c3d4e5f60718",
                 "overview/activity", "system/observed", "diagnostics/events"):
        assert c.get(f"{API}/{path}").status_code == 401


# ── Trades: forensic story ────────────────────────────────────────────────────
def test_trade_story_chain_and_exact_cycle_votes(m2):
    c, *_ = m2
    d = get(c, "/trades/pos_fixture01/lineage")
    steps = {s["step"]: s for s in d["chain"]}
    assert list(steps) == ["opportunity", "market_context", "decision", "signals", "strategy",
                           "execution", "accounting", "outcome"]
    assert steps["opportunity"]["ref"] == {"kind": "scan", "id": "scan_fixture01"}
    assert steps["decision"]["ref"] == {"kind": "decision", "id": "dec_fixture01"}
    assert steps["strategy"]["ref"] == {"kind": "strategy", "id": "spec_fixture_trend"}
    assert steps["outcome"]["status"] == "recorded" and "12.5" in steps["outcome"]["summary"]
    assert steps["accounting"]["status"] == "unavailable"          # no receipts booked
    assert d["cycle"]["regime"] == "TRENDING_UP"
    # the other cycle's vote sits inside the time window but is not this cycle's
    assert sorted(v["agent"] for v in d["votes"]) == ["flow", "trend"]
    assert all(v["side"] != "short" for v in d["votes"])
    assert d["votes_basis"]["join"].startswith("votes.cycle_id")
    assert d["links"]["strategy"]["id"] == "spec_fixture_trend"
    assert {"venue_fills", "strategy_version_at_entry", "funding"} <= fields(d)
    assert d["trade"]["mfe_r"] == 1.8 and d["trade"]["mae_r"] == -0.4


def test_trade_without_links_has_no_inferred_chain(m2):
    c, *_ = m2
    d = get(c, "/trades/t-eth/lineage")
    steps = {s["step"]: s for s in d["chain"]}
    for step in ("opportunity", "market_context", "decision", "strategy"):
        assert steps[step]["status"] == "unavailable" and steps[step]["ref"] is None
    assert d["votes"] == [] and d["cycle"] is None


# ── Decisions ─────────────────────────────────────────────────────────────────
def test_decision_detail_exact_links(m2):
    c, *_ = m2
    d = get(c, "/decisions/dec_fixture01")
    assert d["decision"]["scan_id"] == "scan_fixture01"
    assert [t["id"] for t in d["trades"]] == ["pos_fixture01"]
    assert d["strategies"] == [{"id": "spec_fixture_trend", "in_registry": True,
                                "name": "Fixture Trend Pullback", "kind": "spec",
                                "state": "paper"}]
    assert d["outcome"]["fwd_ret_4h"] == 0.012
    assert len(d["votes"]) == 2


def test_decision_detail_malformed_and_unregistered(m2):
    c, *_ = m2
    d = get(c, "/decisions/dec_fixture02")
    assert d["decision"]["signals"] == [] and "signals" in fields(d)
    assert d["strategies"] == [{"id": "spec_ghost", "in_registry": False, "name": None,
                                "kind": None, "state": None}]
    assert {"outcome", "opportunity"} <= fields(d) and d["trades"] == []
    get(c, "/decisions/nope", status=404)


# ── Research ──────────────────────────────────────────────────────────────────
def test_research_paging_and_seed_links(m2):
    c, *_ = m2
    d = get(c, "/research", limit=1, offset=0)
    assert d["results_page"] == {"offset": 0, "limit": 1, "has_more": True,
                                 "order": "journal insertion order, newest first"}
    [newest] = d["results"]
    assert newest["hash"] == "b1b2c3d4e5f60718" and newest["seed_strategy"] is None
    d2 = get(c, "/research", limit=1, offset=1)
    assert d2["results"][0]["seed_strategy"] == {"id": "spec_fixture_trend",
                                                 "in_registry": True,
                                                 "name": "Fixture Trend Pullback"}
    assert get(c, "/research", limit=100000)["results_page"]["limit"] == 200
    ideas = d["ideas"]
    assert ideas[0]["idea_id"] == "tv_fixture_1" and ideas[0]["spec_in_registry"] is True


def test_research_item_relationships(m2):
    c, *_ = m2
    d = get(c, "/research/combos/a1b2c3d4e5f60718")
    assert d["item"]["seed_strategy"]["id"] == "spec_fixture_trend"
    assert [ch["hash"] for ch in d["children"]] == ["b1b2c3d4e5f60718"]
    assert d["candidate"]["state"] == "queued" and d["registrations"][0]["gate"] == "gate1"
    assert d["item"]["ablation"] == {"trail": -0.1} and d["item"]["result"]["verdict"] == "grow"
    child = get(c, "/research/combos/b1b2c3d4e5f60718")
    assert child["parent"]["hash"] == "a1b2c3d4e5f60718"
    assert {"candidate", "registrations", "strategy"} <= fields(child)
    get(c, "/research/combos/0000000000000000", status=404)


def test_research_item_oversized_result_is_withheld(m2):
    c, journal, *_ = m2
    with journal._tx() as conn:
        conn.execute("UPDATE research_combos SET result=? WHERE hash='a1b2c3d4e5f60718'",
                     (json.dumps({"x": "y" * (owner_reads.RESULT_MAX_BYTES + 10)}),))
    d = get(c, "/research/combos/a1b2c3d4e5f60718")
    assert d["item"]["result"] is None and d["item"]["result_truncated"] is True
    assert "result" in fields(d)


# ── Strategies ────────────────────────────────────────────────────────────────
def test_strategy_workspace_exact_subject(m2):
    c, *_ = m2
    d = get(c, "/strategies/spec_fixture_trend")
    kinds = [e["kind"] for e in d["brain_events"]]
    assert "spec_admitted" in kinds and "keep_verdict" in kinds and "spec_rejected" not in kinds
    broken = next(e for e in d["brain_events"] if e["kind"] == "keep_verdict")
    assert broken["detail"] == {"_unparseable": True}          # malformed: shown, not fixed
    assert "spec_admitted" in [e["event"] for e in d["lifecycle"]]
    idea = d["source_idea"]
    assert idea["idea_id"] == "tv_fixture_1" and idea["consumed"]["outcome"] == "admitted"
    assert idea["harvested"]["title"] == "Fixture pullback idea"
    assert d["postmortem"]["verdict"] == "CONSISTENT" and d["postmortem"]["at"]
    assert [r["hash"] for r in d["research_seeded"]] == ["a1b2c3d4e5f60718"]
    assert [t["id"] for t in d["recent_trades"]] == ["pos_fixture01"]
    assert "postmortem verdict" in next(u["reason"] for u in d["unavailable"]
                                        if u["field"] == "health")


def test_strategy_family_by_recorded_ids(m2):
    c, *_ = m2
    child = get(c, "/strategies/strat_child_fx")["family"]
    assert child["parent"]["id"] == "strat_parent_fx" and child["parent"]["in_registry"]
    parent = get(c, "/strategies/strat_parent_fx")["family"]
    assert [x["id"] for x in parent["children"]] == ["strat_child_fx"]
    assert [x["id"] for x in parent["siblings"]] == ["strat_child_fx"]
    s1 = get(c, "/strategies/s1")
    assert s1["source_idea"] is None and s1["postmortem"] is None
    assert s1["family"]["parent"] is None


# ── Operations ────────────────────────────────────────────────────────────────
def test_operations_timeline_refs(m2):
    c, *_ = m2
    d = get(c, "/operations/activity")
    tl = d["timeline"]
    assert [i["at"] for i in tl] == sorted((i["at"] for i in tl), reverse=True)
    kinds = {i["kind"] for i in tl}
    assert {"decision", "trade_opened", "trade_closed", "control_event",
            "strategy_lifecycle"} <= kinds
    assert not any(i["title"] == "signal_cooldown" for i in tl)
    assert d["signal_cooldowns"]["in_newest_500_control_events"] == 3
    assert all(e["event"] != "signal_cooldown" for e in d["control_events"])
    ghost = next(i for i in tl if i["title"].startswith("spec_rejected"))
    assert ghost["ref"] is None and ghost["strategies"] == [{"id": "spec_ghost",
                                                             "in_registry": False}]
    executed = next(i for i in tl if i["kind"] == "decision" and i["status"] == "executed")
    assert executed["ref"] == {"kind": "decision", "id": "dec_fixture01"}


# ── Overview ──────────────────────────────────────────────────────────────────
def test_overview_activity(m2):
    c, *_ = m2
    d = get(c, "/overview/activity")
    assert [r["id"] for r in d["decisions"]["executed"]] == ["dec_fixture01"]
    assert [r["id"] for r in d["decisions"]["skipped"]] == ["dec_fixture02"]
    assert d["decisions"]["risk_blocks"] == [{"reason": "risk", "n": 1}]
    assert d["risk_state"]["peak_equity"] == 5200.0
    assert d["rent_state"] is None and "rent_state" in fields(d)       # malformed
    assert {"approvals", "news_guard"} <= fields(d)
    assert d["research"]["newest_results"][1]["seed_strategy"]["id"] == "spec_fixture_trend"
    ids = {s["id"] for s in d["strategies"]}
    assert "spec_fixture_trend" in ids and "strat_parent_fx" not in ids


# ── Live System ───────────────────────────────────────────────────────────────
def test_observed_flows_distinguish_declared_edges(m2):
    c, *_ = m2
    d = get(c, "/system/observed")
    by = {(f["source"], f["target"], f["record"]): f for f in d["flows"]}
    assert by[("execution", "journal", "journal trades closed")]["count"] == 2  # t-old + fx
    assert by[("kernel", "journal", "journal equity")]["status"] in ("observed",
                                                                     "none_in_window")
    assert all(f["declared_edge"] in (True, False) for f in d["flows"])
    unobserved = {(e["source"], e["target"]) for e in d["unobserved_declared_edges"]}
    assert ("attention", "orchestrator") in unobserved
    sysd = get(c, "/system")
    assert sysd["events"] == []                       # nothing animated from counts


# ── Knowledge ─────────────────────────────────────────────────────────────────
def test_note_records_exact_ids_only(m2):
    c, *_ = m2
    d = get(c, "/knowledge/note", id="30 Postmortems/Fixture Autopsy.md")
    got = {(r["kind"], r["id"]) for r in d["records"]}
    assert got == {("strategy", "spec_fixture_trend"), ("trade", "pos_fixture01"),
                   ("decision", "dec_fixture01"), ("research", "a1b2c3d4e5f60718")}
    assert all(r["basis"] == "exact_id" for r in d["records"])
    f = get(c, "/knowledge/note", id="20 Strategies/Fixture_Child.md")
    assert f["records"] == []                          # a title is not an id
    assert {s["id"] for s in f["family"]["strategies"]} == {"strat_parent_fx",
                                                            "strat_child_fx"}


# ── LUFFY chat ────────────────────────────────────────────────────────────────
def chat(c, message="what opened?"):
    r = c.post(API + "/chat", headers=H, json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


def test_chat_links_are_exact_id_only_and_non_operational(m2):
    c, *_ = m2
    FakeChat.mode = "mentions"
    r = chat(c)
    assert r["operational"] is False and r["evidence"] == []
    # "Fixture Trend Pullback" is a unique registry name: still never linked
    assert [(x["kind"], x["id"], x["basis"]) for x in r["links"]] == [
        ("trade", "pos_fixture01", "exact_id")]
    assert r["links_error"] is None
    # an id-shaped token that resolves to nothing is reported, not dropped
    assert r["unresolved"] == [{"token": "spec_nonexistent", "kind_hint": "strategy"}]
    assert r["consulted"][0]["tool"] == "get_trades"


def test_chat_successful_zero_result_lookup(m2):
    c, *_ = m2
    r = chat(c, "hello")                       # FakeChat "ok": no id in the reply
    assert r["links"] == [] and r["links_error"] is None and r["unresolved"] == []
    assert r["consulted"] == []


def test_chat_lookup_exception_is_not_empty_success(m2, monkeypatch):
    c, *_ = m2
    FakeChat.mode = "mentions"

    def boom(*a, **k):
        raise RuntimeError("journal gone")
    monkeypatch.setattr(owner_reads, "recognize", boom)
    r = chat(c)
    assert r["links"] is None and r["unresolved"] is None
    assert r["links_error"] == "lookup_failed:RuntimeError"
    assert r["reply"]                           # the reply itself still stands


def test_chat_lookup_timeout_is_unavailable(m2, monkeypatch):
    c, *_ = m2
    FakeChat.mode = "mentions"
    from trader.dashboard import owner_api
    monkeypatch.setattr(owner_api, "CHAT_LINK_TIMEOUT_S", 0.05)
    real = owner_reads.recognize

    def slow(*a, **k):
        time.sleep(0.5)
        return real(*a, **k)
    monkeypatch.setattr(owner_reads, "recognize", slow)
    r = chat(c)
    assert r["links"] is None and r["links_error"] == "lookup_timeout"


def test_recognize_exact_ids_never_names(m2):
    _, journal, *_ = m2
    rec = owner_reads.recognize
    # exact id → linked
    assert [x["id"] for x in rec(journal, "see spec_fixture_trend")] == ["spec_fixture_trend"]
    # name only (unique in the registry) → not linked
    assert rec(journal, "Fixture Trend Pullback did well") == []
    # two identical names → not linked
    with journal._tx() as conn:
        conn.execute("UPDATE strategies SET name='Fixture Trend Pullback' "
                     "WHERE id='strat_parent_fx'")
    assert rec(journal, "Fixture Trend Pullback did well") == []
    # name + an unrelated exact id → only the id resolves
    got = rec(journal, "Fixture Trend Pullback and strat_child_fx")
    assert [(x["kind"], x["id"]) for x in got] == [("strategy", "strat_child_fx")]


def test_recognize_resolves_ids_beyond_any_row_window(m2):
    _, journal, *_ = m2
    with journal._tx() as conn:
        for i in range(650):
            conn.execute("INSERT INTO strategies(id,name,kind,params,state,created_at) "
                         "VALUES(?,?,'spec','{}','retired','2026-01-01')",
                         (f"spec_bulk_{i:04d}", f"Bulk {i}"))
    got = owner_reads.recognize(journal, "only spec_bulk_0649 matters")
    assert [x["id"] for x in got] == ["spec_bulk_0649"]
    assert owner_reads.unresolved_ids("spec_bulk_9999 pos_abcdef1234", got) == [
        {"token": "pos_abcdef1234", "kind_hint": "trade"},
        {"token": "spec_bulk_9999", "kind_hint": "strategy"}]


# ── malformed persisted content ───────────────────────────────────────────────
def test_non_record_signals_are_reported_not_emptied(m2):
    c, journal, *_ = m2
    with journal._tx() as conn:
        conn.execute("UPDATE decisions SET signals_json='[17]' WHERE id='dec_fixture01'")
    d = get(c, "/decisions/dec_fixture01")
    assert d["decision"]["signals"] == []
    assert "1 of 1 signals_json entries are not signal records" in next(
        u["reason"] for u in d["unavailable"] if u["field"] == "signals")
    lin = get(c, "/trades/pos_fixture01/lineage")
    assert "signals" in fields(lin)
    step = next(s for s in lin["chain"] if s["step"] == "signals")
    assert step["status"] == "malformed"
    ops = get(c, "/operations/activity")
    # dec_fixture01 ([17]) and dec_fixture02 (not json)
    assert ops["window"]["malformed_signals"] == 2 and "signals" in fields(ops)


def test_zero_count_flow_does_not_prove_a_declared_edge(m2, monkeypatch):
    c, journal, *_ = m2
    with journal._tx() as conn:
        conn.execute("DELETE FROM equity")
    d = get(c, "/system/observed")
    unobserved = {(e["source"], e["target"]) for e in d["unobserved_declared_edges"]}
    assert len(d["declared_edges"]) == 11
    # no execution→journal declared edge exists; kernel→owner-interface has no flow
    assert ("kernel", "owner-interface") in unobserved
    flows = {(f["source"], f["target"]): f for f in d["flows"]}
    # a declared edge whose only flow has zero records stays unobserved
    from trader.dashboard.owner_api import SYSTEM_EDGES
    for s_, t_ in SYSTEM_EDGES:
        f = flows.get((s_, t_))
        if f is not None and not f["count"]:
            assert (s_, t_) in unobserved


# ── Diagnostics ───────────────────────────────────────────────────────────────
def test_control_event_keyset_paging(m2):
    c, journal, *_ = m2
    with journal._tx() as conn:
        for i in range(7):
            conn.execute("INSERT INTO control_events(ts,event,actor,detail) VALUES(?, "
                         "'reconcile','luffy','{}')", (f"2026-09-01T00:00:0{i}+00:00",))
    p1 = get(c, "/diagnostics/events", limit=4)
    assert len(p1["events"]) == 4 and p1["has_more"] is True
    p2 = get(c, "/diagnostics/events", limit=4, before=p1["next_before"])
    assert max(e["id"] for e in p2["events"]) < min(e["id"] for e in p1["events"])
    allp = get(c, "/diagnostics/events", limit=200, include_cooldown=True)
    assert sum(e["event"] == "signal_cooldown" for e in allp["events"]) == 3
    assert get(c, "/diagnostics/events", limit=10 ** 6)["limit"] == 200


def test_diagnostics_probes(m2):
    c, *_ = m2
    d = get(c, "/diagnostics")
    probes = {p["probe"]: p for p in d["probes"]}
    assert probes["journal_read"]["ok"] is True and probes["journal_read"]["ms"] >= 0
    assert probes["knowledge_vault"]["ok"] is True


# ── empty / absent stores ─────────────────────────────────────────────────────
def test_empty_journal_reads_truthfully(tmp_path, monkeypatch):
    app, journal, _ = make_app(tmp_path, monkeypatch)
    with journal._tx() as conn:
        for t in ("outcomes", "votes", "trade_accounting_bookings", "trades", "decisions",
                  "cycles", "strategies", "brain_events", "equity",
                  "control_events", "research_combos", "research_batches", "state_kv"):
            conn.execute(f"DELETE FROM {t}")
    c = TestClient(app)
    ov = get(c, "/overview/activity")
    assert ov["decisions"] == {"window": 0, "executed": [], "skipped": [], "risk_blocks": []}
    assert ov["strategies"] == [] and ov["risk_state"] is None
    assert {"risk_state", "rent_state"} <= fields(ov)
    ops = get(c, "/operations/activity")
    assert ops["timeline"] == [] and ops["signal_cooldowns"]["in_newest_500_control_events"] == 0
    obs = get(c, "/system/observed")
    assert all(f["status"] in ("none_in_window", "unavailable") for f in obs["flows"])
    assert get(c, "/research")["results"] == []
    assert get(c, "/diagnostics/events")["events"] == []


# ── bounded reads ─────────────────────────────────────────────────────────────
def test_reads_are_bounded_on_large_tables(m2):
    c, journal, *_ = m2
    with journal._tx() as conn:
        for i in range(1200):
            conn.execute("INSERT INTO cycles(id,ts,symbol) VALUES(?,?,'XRP/USDT')",
                         (f"cb{i}", f"2026-09-02T00:{i // 60 % 60:02d}:{i % 60:02d}"))
            conn.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,"
                         "confidence,executed,skip_reason) VALUES(?,?,?,'XRP/USDT','BUY',0.6,"
                         "0.5,0.5,0,'risk: cap')",
                         (f"db{i}", f"cb{i}", f"2026-09-02T00:{i // 60 % 60:02d}:{i % 60:02d}"))
            conn.execute("INSERT INTO control_events(ts,event,actor,detail) VALUES(?,"
                         "'signal_cooldown','luffy','{}')", (f"2026-09-02T01:00:{i % 60:02d}",))
    ops = get(c, "/operations/activity")
    assert ops["window"]["decisions"] == owner_reads.ACTIVITY_WINDOW
    assert len(ops["timeline"]) <= owner_reads.TIMELINE_ROWS
    assert ops["signal_cooldowns"]["in_newest_500_control_events"] == 500
    ov = get(c, "/overview/activity")
    assert ov["decisions"]["window"] == owner_reads.ACTIVITY_WINDOW
    assert len(ov["decisions"]["skipped"]) == 5
    obs = get(c, "/system/observed")
    dec = next(f for f in obs["flows"] if f["record"] == "journal decisions")
    assert dec["count"] <= 500


def test_slow_detail_reads_do_not_block_other_requests(m2, monkeypatch):
    _, _, app, _ = m2
    from trader.core.journal import Journal
    real = Journal.query

    def slow(self, sql, params=()):
        if "FROM decisions" in sql:
            time.sleep(1.2)
        return real(self, sql, params)
    monkeypatch.setattr(Journal, "query", slow)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            t0 = time.perf_counter()

            async def timed(coro):
                resp = await coro
                return resp, time.perf_counter() - t0
            slow_reads = [asyncio.ensure_future(timed(client.get(API + p, headers=H)))
                          for p in ("/decisions/dec_fixture01", "/overview/activity",
                                    "/operations/activity")]
            await asyncio.sleep(0.1)
            fast = await asyncio.gather(
                timed(client.get(API + "/bootstrap", headers=H)),
                timed(client.get(API + "/research/combos/a1b2c3d4e5f60718", headers=H)),
                timed(client.get(API + "/strategies/spec_fixture_trend", headers=H)))
            return await asyncio.gather(*slow_reads), fast
    slow_done, fast = asyncio.run(run())
    assert all(r.status_code == 200 for r, _ in slow_done)
    for resp, secs in fast:
        assert resp.status_code == 200 and secs < 1.0, (resp.url, secs)


# ── existence vs display cap (Astra: 51 ids must not yield a false "not found") ──
def _review_strategies(journal, n):
    with journal._tx() as conn:
        for i in range(n):
            conn.execute("INSERT INTO strategies(id,name,kind,params,state,created_at) "
                         "VALUES(?,?,'spec','{}','retired','2026-01-01')",
                         (f"spec_review_{i:03d}", f"Review {i}"))


def _note(root, rel, text):
    p = root / "knowledge" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _chat_mentions(c, text):
    FakeChat.mode = "echo"
    r = chat(c, text)
    assert r["links_error"] is None
    return (len(r["links"]), r["resolved_count"], r["truncated_count"],
            sorted(u["token"] for u in r["unresolved"]))


def _note_mentions(c, root, text):
    _note(root, "50 Daily/Review.md", "# Review\n\n" + text + "\n")
    d = get(c, "/knowledge/note", id="50 Daily/Review.md")
    assert d["records_error"] is None
    return (len(d["records"]), d["records_resolved_count"], d["records_truncated_count"],
            sorted(u["token"] for u in d["records_unresolved"]))


@pytest.mark.parametrize("surface", ["chat", "knowledge"])
def test_astra_51_valid_ids_are_truncated_not_unresolved(m2, surface):
    c, journal, _, root = m2
    _review_strategies(journal, 51)
    text = " ".join(f"spec_review_{i:03d}" for i in range(51))
    got = (_chat_mentions(c, text) if surface == "chat"
           else _note_mentions(c, root, text))
    assert got == (50, 51, 1, [])


@pytest.mark.parametrize("valid,missing,expect", [
    (49, 0, (49, 49, 0, [])),
    (50, 0, (50, 50, 0, [])),
    (51, 0, (50, 51, 1, [])),
    (60, 0, (50, 60, 10, [])),
    (50, 1, (50, 50, 0, ["spec_review_900"])),
    (60, 2, (50, 60, 10, ["spec_review_900", "spec_review_901"])),
])
@pytest.mark.parametrize("surface", ["chat", "knowledge"])
def test_existence_is_classified_before_the_display_cap(m2, surface, valid, missing, expect):
    c, journal, _, root = m2
    _review_strategies(journal, 60)
    ids = [f"spec_review_{i:03d}" for i in range(valid)]
    ids += [f"spec_review_{900 + i}" for i in range(missing)]
    text = ", ".join(ids)
    got = (_chat_mentions(c, text) if surface == "chat"
           else _note_mentions(c, root, text))
    assert got == expect


def test_duplicate_and_malformed_ids(m2):
    c, journal, *_ = m2
    _review_strategies(journal, 3)
    text = ("spec_review_000 spec_review_000 (spec_review_001). spec_review_002, "
            "SPEC_REVIEW_000 spec_review_00 spec-review-001 spec_review_000x")
    n, resolved, truncated, unresolved = _chat_mentions(c, text)
    # duplicates count once; punctuation around an exact id still resolves
    assert (n, resolved, truncated) == (3, 3, 0)
    # id-shaped but absent tokens are unresolved; case/hyphen variants are not ids
    assert unresolved == ["spec_review_00", "spec_review_000x"]


def test_capped_lookup_failure_is_still_unavailable(m2, monkeypatch):
    c, journal, *_ = m2
    _review_strategies(journal, 51)
    FakeChat.mode = "echo"

    def boom(*a, **k):
        raise RuntimeError("journal gone")
    monkeypatch.setattr(owner_reads, "recognize", boom)
    r = chat(c, " ".join(f"spec_review_{i:03d}" for i in range(51)))
    assert r["links"] is None and r["unresolved"] is None
    assert r["resolved_count"] is None and r["truncated_count"] is None
    assert r["links_error"] == "lookup_failed:RuntimeError"
