"""Owner frontend M1: Decisions GraphQL hot path, event-loop responsiveness under
slow journal reads, and the read contracts in trader/dashboard/owner_reads.py."""
import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.owner_frontend_fixture import FakeChat, make_app
from trader.api import graphql_schema

H = {"x-luffy-token": "fixture-token"}
DECISIONS = "query($o:Int!){decisions(limit:101,offset:$o){id ts symbol action skip_reason}}"


@pytest.fixture
def live(tmp_path, monkeypatch):
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, journal, gateway = make_app(tmp_path, monkeypatch)
    return TestClient(app), journal, app, tmp_path


def _count_vote_reads(monkeypatch):
    calls = []
    real = graphql_schema.votes_for_cycles

    def counted(j, ids):
        calls.append(list(ids))
        return real(j, ids)
    monkeypatch.setattr(graphql_schema, "votes_for_cycles", counted)
    return calls


def _add_decisions(journal, n):
    with journal._tx() as c:
        for i in range(n):
            c.execute("INSERT INTO cycles(id,ts,symbol) VALUES(?,?,?)",
                      (f"cx{i}", f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}", "ETH/USDT"))
            c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,"
                      "confidence,executed) VALUES(?,?,?,?,'HOLD',0,0.5,0,0)",
                      (f"dx{i}", f"cx{i}", f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}",
                       "ETH/USDT"))


# ── Decisions ─────────────────────────────────────────────────────────────────
def test_decisions_without_votes_reads_no_votes(live, monkeypatch):
    c, journal, *_ = live
    _add_decisions(journal, 120)
    calls = _count_vote_reads(monkeypatch)
    r = c.post("/graphql", headers=H, json={"query": DECISIONS, "variables": {"o": 0}}).json()
    assert "errors" not in r and len(r["data"]["decisions"]) == 101
    assert calls == []


def test_decisions_votes_are_one_batched_read_with_cycle_semantics(live, monkeypatch):
    c, journal, *_ = live
    _add_decisions(journal, 30)
    calls = _count_vote_reads(monkeypatch)
    q = "{decisions(limit:50){id votes{agent side rationale}} recent_vetoes{id}}"
    r = c.post("/graphql", headers=H, json={"query": q}).json()
    assert "errors" not in r
    assert len(calls) == 1 and len(calls[0]) == 31          # 30 + the seeded c1
    by_id = {d["id"]: d["votes"] for d in r["data"]["decisions"]}
    assert by_id["d1"] == [{"agent": "trend", "side": "long", "rationale": "fixture vote"}]
    assert by_id["dx0"] == []


def test_decisions_limit_is_bounded(live):
    c, journal, *_ = live
    _add_decisions(journal, 5)
    r = c.post("/graphql", headers=H,
               json={"query": "{decisions(limit:100000,offset:-5){id}}"}).json()
    assert "errors" not in r and len(r["data"]["decisions"]) == 6
    assert graphql_schema._bound(100000) == graphql_schema.MAX_ROWS


def _concurrent_timings(app, monkeypatch):
    """(graphql, [(response, seconds)] for bootstrap, a chunk and research)
    while one GraphQL decisions read takes 1.5 s in the journal."""
    from trader.core.journal import Journal
    real = Journal.query

    def slow(self, sql, params=()):
        if "FROM decisions" in sql:
            time.sleep(1.5)
        return real(self, sql, params)
    # the app's Journal instance is its own; slow every instance's decisions read
    monkeypatch.setattr(Journal, "query", slow)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            # completion time from the GraphQL request's start: a blocked loop
            # also delays the moment the other requests are sent
            t0 = time.perf_counter()

            async def timed(coro):
                resp = await coro
                return resp, time.perf_counter() - t0
            gql = asyncio.ensure_future(timed(client.post(
                "/graphql", headers=H, json={"query": DECISIONS, "variables": {"o": 0}})))
            await asyncio.sleep(0.1)
            others = await asyncio.gather(
                timed(client.get("/owner-api/v1/bootstrap", headers=H)),
                timed(client.get("/assets/missing-chunk.js", headers=H)),
                timed(client.get("/owner-api/v1/research", headers=H)))
            return await gql, others
    return asyncio.run(run())


def test_slow_decisions_read_does_not_block_other_requests(live, monkeypatch):
    """The regression: a sync resolver ran on the event loop, so a slow journal
    read stalled static chunks and every other route until it finished."""
    (g, g_s), others = _concurrent_timings(live[2], monkeypatch)
    assert g.status_code == 200 and g_s >= 1.5
    for resp, secs in others:
        assert resp.status_code in (200, 404, 503)
        assert secs < 1.0, secs


def test_responsiveness_check_detects_an_inline_resolver(tmp_path, monkeypatch):
    """Negative control: with resolvers back on the event loop (the pre-fix
    behavior) the same measurement sees the other requests stall."""
    monkeypatch.setattr(graphql_schema, "_offloop", lambda fn: fn)
    app, *_ = make_app(tmp_path, monkeypatch)
    (g, _), others = _concurrent_timings(app, monkeypatch)
    assert g.status_code == 200
    assert max(secs for _, secs in others) >= 1.0


# ── read contracts ────────────────────────────────────────────────────────────
def test_new_read_routes_require_auth(live):
    c, *_ = live
    for path in ("research", "operations/activity", "diagnostics", "trades/t-btc/lineage",
                 "strategies/s1", "knowledge/note?id=MOC.md"):
        assert c.get(f"/owner-api/v1/{path}").status_code == 401


def test_trade_lineage_uses_recorded_ids_only(live):
    c, *_ = live
    d = c.get("/owner-api/v1/trades/t-btc/lineage", headers=H).json()
    assert d["decision"]["id"] == "d1" and d["strategy"]["id"] == "s1"
    assert d["strategy"]["spec_sha256"] is None          # s1 has no spec_json
    [receipt] = d["accounting"]["receipts"]
    assert receipt["kind"] == "entry" and receipt["integrity"] == "verified"
    assert receipt["assessment"]["status"] == "unverified"
    assert d["accounting"]["fills_verified"] is False
    missing = {u["field"] for u in d["unavailable"]}
    assert {"venue_fills", "strategy_version_at_entry", "outcome", "opportunity"} <= missing
    # a trade without recorded links is not joined by inference
    e = c.get("/owner-api/v1/trades/t-eth/lineage", headers=H).json()
    assert e["decision"] is None and e["strategy"] is None
    assert {"decision", "strategy", "accounting"} <= {u["field"] for u in e["unavailable"]}
    assert c.get("/owner-api/v1/trades/nope/lineage", headers=H).status_code == 404


def test_trade_lineage_reports_tampered_receipt(live):
    c, journal, *_ = live
    with journal._tx() as conn:
        conn.execute("UPDATE trade_accounting_bookings SET payload=replace(payload, "
                     "'\"entry\"', '\"close\"')")
    [r] = c.get("/owner-api/v1/trades/t-btc/lineage", headers=H).json()["accounting"]["receipts"]
    assert r["integrity"].startswith("failed:")


def test_research_contract(live):
    c, *_ = live
    d = c.get("/owner-api/v1/research", headers=H).json()
    assert d["available"] is True
    assert d["results"][0]["hash"] == "h-fixture" and d["results"][0]["verdict"] == "prune"
    assert d["runs"][0]["n"] == 5 and d["counts"]["registered_tests"] == 0
    assert {u["field"] for u in d["unavailable"]} >= {"plans", "costs"}
    assert "questions" not in {u["field"] for u in d["unavailable"]}
    assert d["questions"] == [] and d["questions_page"]["has_more"] is False


def test_strategies_contract(live):
    c, journal, *_ = live
    with journal._tx() as conn:
        conn.execute("UPDATE strategies SET spec_json=? WHERE id='s1'",
                     ('{"timeframe": "4h", "direction": "both"}',))
        conn.execute("INSERT INTO control_events(ts,event,actor,detail) VALUES "
                     "('2026-01-02T00:00:00+00:00','strategy_promote','analyst',"
                     "'{\"id\": \"s1\"}')")
    [row] = c.get("/owner-api/v1/strategies", headers=H).json()["strategies"]
    assert row["name"] == "Donchian" and len(row["spec_sha256"]) == 64
    assert row["spec"]["timeframe"] == "4h"
    assert row["journal_economics"]["trades"] == 1         # t-btc carries strategy_id s1
    d = c.get("/owner-api/v1/strategies/s1", headers=H).json()
    assert "strategy_promote" in [e["event"] for e in d["lifecycle"]]
    assert {u["field"] for u in d["unavailable"]} >= {"health", "capacity", "allocation"}
    assert c.get("/owner-api/v1/strategies/none", headers=H).status_code == 404


def test_operations_contract(live):
    c, *_ = live
    d = c.get("/owner-api/v1/operations/activity", headers=H).json()
    assert d["window"]["decisions"] == 1 and d["decisions"][0]["id"] == "d1"
    assert {o["id"] for o in d["orders"]} >= {"t-btc", "t-eth"}
    assert d["control_events"][0]["to_state"] == "FROZEN"


def test_knowledge_note_contract_and_path_safety(live):
    c, *_ = live
    d = c.get("/owner-api/v1/knowledge/note",
              params={"id": "10 Theories/Mechanisms/Aggressor Flow.md"}, headers=H).json()
    assert "Aggressor Flow" in d["body"] and d["links"] == ["Absorption Test"]
    assert d["frontmatter"]["status"] == "active"
    assert any(e["event"] == "file_modified" for e in d["chronology"])
    for bad in ("../data/luffy.db", "../../etc/passwd", "MOC.txt", "missing.md"):
        assert c.get("/owner-api/v1/knowledge/note", params={"id": bad},
                     headers=H).status_code == 404


def test_diagnostics_contract(live):
    c, _, _, root = live
    (root / "data" / "watchdog.off").write_text("")
    d = c.get("/owner-api/v1/diagnostics", headers=H).json()
    files = {f["file"]: f for f in d["storage"]["files"]}
    assert files["data/luffy.db"]["bytes"] > 0 and files["data/candles.db"]["bytes"] is None
    assert d["watchdog"]["disabled_flag"] is True and d["watchdog"]["log_tail"] is None
    assert d["incidents"][0]["event"] == "state_change"
    assert all(col["status"] == "unavailable" for col in d["collectors"])
    assert {u["field"] for u in d["unavailable"]} >= {"kernel_resources", "incident_ledger"}


# ── correction pass: exact lifecycle attribution ──────────────────────────────
def test_strategy_lifecycle_uses_exact_recorded_id_only(live):
    c, journal, *_ = live
    events = [
        ("strategy_demote", '{"id": "s1", "reason": "pf below floor"}'),          # s1
        ("strategy_demote", '{"id": "another-strategy", "reason": "s1"}'),        # other
        ("strategy_promote", '{"from": "paper", "to": "active", "kind": "s1"}'),  # no id
        ("strategy_demote", 'not json "s1"'),                                      # malformed
        ("strategy_demote", '{"id": ["s1"]}'),                                     # non-string id
        ("strategy_demote", '{"id": "s10"}'),                                      # prefix only
        ("strategy_demote", '{"id": "S1"}'),                                       # case differs
    ]
    with journal._tx() as conn:
        for i, (event, d) in enumerate(events):
            conn.execute("INSERT INTO control_events(ts,event,actor,detail) VALUES "
                         "(?,?,'operator',?)", (f"2026-01-0{i + 1}T00:00:00+00:00", event, d))
    d = c.get("/owner-api/v1/strategies/s1", headers=H).json()
    attributed = [e for e in d["lifecycle"] if e["source"] == "control_events"]
    assert [e["detail"] for e in attributed] == [{"id": "s1", "reason": "pf below floor"}]
    gaps = {u["field"]: u["reason"] for u in d["unavailable"]}
    assert gaps["unattributed_lifecycle_events"].startswith("3 strategy control event(s)")


# ── correction pass: fill verification truth ──────────────────────────────────
def _fills_evidence():
    fill = {"id": "f1", "order": "o1", "symbol": "BTC/USDT", "side": "sell",
            "timestamp": 1000, "amount": 0.01, "price": 61000.0, "realized_pnl": 10.0,
            "commission": 0.1, "commission_asset": "USDT"}
    return {"basis": "venue_order_fills", "order_id": "o1", "symbol": "BTC/USDT",
            "side": "sell", "quantity": 0.01, "since_ms": 0, "observed_ms": 2000,
            "fills": [fill]}


def _only_receipt(journal, trade_id="t-btc", evidence=None):
    from trader.engine import booking
    with journal._tx() as conn:
        conn.execute("DELETE FROM trade_accounting_bookings")
        booking.persist(conn, trade_id, "close:fill", None, evidence or _fills_evidence())


def _set_payload(journal, payload):
    with journal._tx() as conn:
        conn.execute("UPDATE trade_accounting_bookings SET payload=?", (payload,))


def _payload(journal):
    import json
    return json.loads(journal.query("SELECT payload FROM trade_accounting_bookings")[0]["payload"])


def _lineage(c):
    d = c.get("/owner-api/v1/trades/t-btc/lineage", headers=H).json()
    return d, {u["field"] for u in d["unavailable"]}


def test_fills_verified_only_for_a_replayed_trade_bound_success(live):
    c, journal, *_ = live
    _only_receipt(journal)
    d, gaps = _lineage(c)
    [r] = d["accounting"]["receipts"]
    assert r["integrity"] == "verified"
    assert r["assessment"]["status"] == "verified_leg_fills_only"
    assert d["accounting"]["fills_verified"] is True
    assert "venue_fills" not in gaps and "funding" in gaps


@pytest.mark.parametrize("claimed", ["verified", "verified_leg_fills_only"])
def test_fills_not_verified_for_failed_integrity(live, claimed):
    """Astra's reproduction: a receipt whose own assessment claims success but
    fails booking_integrity must not verify fills."""
    import json
    c, journal, *_ = live
    _only_receipt(journal)
    rec = _payload(journal)
    rec["assessment"]["status"] = claimed
    rec["sha256"] = "0" * 64
    _set_payload(journal, json.dumps(rec))
    d, gaps = _lineage(c)
    [r] = d["accounting"]["receipts"]
    assert r["integrity"] == "failed:booking_integrity" and r["assessment"] is None
    assert d["accounting"]["fills_verified"] is False and "venue_fills" in gaps


def test_fills_not_verified_for_tampered_receipt(live):
    import json
    c, journal, *_ = live
    _only_receipt(journal)
    rec = _payload(journal)
    rec["evidence"]["quantity"] = 5.0            # altered after booking, sha not updated
    _set_payload(journal, json.dumps(rec))
    d, gaps = _lineage(c)
    assert d["accounting"]["receipts"][0]["integrity"].startswith("failed:")
    assert d["accounting"]["receipts"][0]["assessment"] is None
    assert d["accounting"]["fills_verified"] is False and "venue_fills" in gaps


def test_fills_not_verified_for_unsuccessful_replay(live):
    """A self-consistent digest over a claimed assessment the evidence does not
    support: replay recomputes the assessment and refuses it."""
    import json
    from trader.engine.accounting import digest
    c, journal, *_ = live
    _only_receipt(journal, evidence={"basis": "unattributed_journal_booking"})
    rec = _payload(journal)
    rec["assessment"] = dict(rec["assessment"], status="verified_leg_fills_only")
    rec["sha256"] = digest({k: v for k, v in rec.items() if k != "sha256"})
    _set_payload(journal, json.dumps(rec))
    d, gaps = _lineage(c)
    [r] = d["accounting"]["receipts"]
    assert r["integrity"] == "failed:booking_assessment_mismatch" and r["assessment"] is None
    assert d["accounting"]["fills_verified"] is False and "venue_fills" in gaps


def test_fills_not_verified_when_only_some_legs_are(live):
    """Production shape: close leg verified by venue fills, entry leg not."""
    from trader.engine import booking
    c, journal, *_ = live
    _only_receipt(journal)
    with journal._tx() as conn:
        booking.persist(conn, "t-btc", "entry", None,
                        {"basis": "entry_order_confirmation"})
    d, gaps = _lineage(c)
    assert d["accounting"]["verified_legs"] == ["close:fill"]
    assert d["accounting"]["fills_verified"] is False
    reason = {u["field"]: u["reason"] for u in d["unavailable"]}["venue_fills"]
    assert reason.startswith("1 of 2 booked receipt(s)") and "close:fill" in reason


def test_fills_not_verified_when_another_receipt_failed(live):
    import json
    c, journal, *_ = live
    _only_receipt(journal)
    rec = _payload(journal)
    rec["sha256"] = "f" * 64
    with journal._tx() as conn:
        conn.execute("INSERT INTO trade_accounting_bookings(trade_id,payload) VALUES "
                     "('t-btc', ?)", (json.dumps(rec),))
    d, gaps = _lineage(c)
    assert [r["integrity"] for r in d["accounting"]["receipts"]] == [
        "verified", "failed:booking_integrity"]
    assert d["accounting"]["fills_verified"] is False and "venue_fills" in gaps


def test_fills_not_verified_for_wrong_trade(live):
    """A valid, verified receipt booked for t-eth, filed under t-btc."""
    c, journal, *_ = live
    _only_receipt(journal, trade_id="t-eth")
    with journal._tx() as conn:
        conn.execute("UPDATE trade_accounting_bookings SET trade_id='t-btc'")
    d, gaps = _lineage(c)
    [r] = d["accounting"]["receipts"]
    assert r["integrity"] == "failed:booking_trade_mismatch" and r["assessment"] is None
    assert d["accounting"]["fills_verified"] is False and "venue_fills" in gaps


@pytest.mark.parametrize("payload", ["not json", "[1, 2]", "null", "{}",
                                     '{"schema_version": "trade-booking.v1"}'])
def test_fills_not_verified_for_malformed_receipt(live, payload):
    c, journal, *_ = live
    _only_receipt(journal)
    _set_payload(journal, payload)
    d, gaps = _lineage(c)
    [r] = d["accounting"]["receipts"]
    assert r["integrity"].startswith("failed:") and r["assessment"] is None
    assert d["accounting"]["fills_verified"] is False and "venue_fills" in gaps
