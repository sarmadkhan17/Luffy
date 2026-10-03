"""Owner frontend live binding: /owner-api/v1 contract, controls and session."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.owner_frontend_fixture import FakeChat, TOKEN, make_app, seed
from trader.dashboard import owner_api

ORIGIN = {"origin": "http://testserver"}
H = {"x-luffy-token": TOKEN}


@pytest.fixture
def live(tmp_path, monkeypatch):
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, journal, gateway = make_app(tmp_path, monkeypatch)
    return TestClient(app), journal, gateway, tmp_path


def signed_in(client):
    assert client.post("/auth/login", json={"password": TOKEN}, headers=ORIGIN).status_code == 200
    return client


def test_every_owner_route_requires_auth(live):
    c, *_ = live
    for path in ("bootstrap", "overview", "knowledge", "system", "trades", "strategies",
                 "logs", "owner-interface", "enrichment/marks", "protection"):
        r = c.get(f"/owner-api/v1/{path}")
        assert r.status_code == 401 and r.json() == {"error": "authentication required"}
    assert c.post("/owner-api/v1/chat", json={"message": "hi"}, headers=ORIGIN).status_code == 401
    # the app shell at / answers with the login page, never the app or data
    r = c.get("/")
    assert r.status_code == 401 and "Luffy owner login" in r.text
    assert "history.replaceState(null,'',location.pathname + location.hash)" in r.text


def test_bootstrap_contract(live):
    c, *_ = live
    signed_in(c)
    b = c.get("/owner-api/v1/bootstrap").json()
    assert b["mode"] == "LIVE" and b["principal"] == "owner"
    assert b["session"]["method"] == "cookie"
    expires = datetime.fromisoformat(b["session"]["expires_at"])
    assert timedelta(hours=11) < expires - datetime.now(timezone.utc) <= timedelta(hours=12)
    assert b["owner_interface"]["configured"] is True
    assert b["providers"]["openclaw"] == "NOT_CONFIGURED"
    assert b["providers"]["whatsapp"] == "NOT_CONFIGURED"
    assert b["capabilities"]["voice"] is False
    assert b["backend"]["source_time"]
    assert c.get("/owner-api/v1/bootstrap", headers=H).json()["session"]["method"] == "header"


def test_overview_normal_is_local_and_labelled(live):
    c, *_ = live
    o = c.get("/owner-api/v1/overview", headers=H).json()
    assert o["errors"] == {}
    assert o["control"]["state"] == "FROZEN"
    assert o["account"]["freshness"] == "fresh" and o["account"]["currency"] == "USDT"
    assert o["heartbeat"]["freshness"] == "fresh"
    assert len(o["equity_series"]["points"]) >= 40
    assert [p["protection"]["status"] for p in o["positions"]] == ["VERIFIED", "VERIFIED"]
    assert o["protection"]["status"] == "VERIFIED"
    assert all(p["journal_stop"]["source"].startswith("journal") for p in o["positions"])
    assert o["exposure"]["pct_of_equity"] > 0
    assert o["realized_today"]["value"] == 2.0


def test_stale_protection_is_never_verified(live):
    c, journal, _, root = live
    seed(root, journal, "stale")
    o = c.get("/owner-api/v1/overview", headers=H).json()
    for p in o["positions"]:
        assert p["protection"]["status"] == "STALE"
        assert p["protection"]["last_reported"] == "VERIFIED"
        assert "verification_stale" in p["protection"]["reasons"]
    assert o["protection"]["status"] == "STALE"
    assert o["heartbeat"]["freshness"] == "stale"
    assert o["account"]["freshness"] == "stale"
    assert o["needs_you"]["needs_owner"] is True


def test_missing_is_missing_not_zero(live):
    c, journal, _, root = live
    seed(root, journal, "missing")
    o = c.get("/owner-api/v1/overview", headers=H).json()
    assert o["account"] is None and o["control"] is None and o["heartbeat"] is None
    assert o["exposure"] is None and o["needs_you"] is None
    assert o["errors"]["account"] == "no_equity_records"
    assert o["errors"]["control"] == "control_state_missing"
    assert o["errors"]["supervisor"] == "supervisor_status_missing"
    assert o["errors"]["protection_snapshot"] == "protection_snapshot_missing"
    assert o["equity_series"]["points"] == []


def _snap(**over):
    now = datetime.now(timezone.utc)
    rec = {"symbol": "BTC/USDT", "stop_present": True, "verified": True, "reasons": []}
    snap = {"status": "VERIFIED", "reasons": [], "observed_at": now.isoformat(),
            "freshness": "fresh", "venue_positions": True, "reconciliation": True,
            "observation_consistent": True, "precision_known": True,
            "venue_protection": True, "complete_listing": True, "symbols": [rec]}
    snap.update(over)
    return snap


def test_protection_rules():
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    btc = {"symbol": "BTC/USDT", "opened_at": old}
    eth = {"symbol": "ETH/USDT", "opened_at": old}
    assert owner_api.protection_for(btc, _snap())["status"] == "VERIFIED"
    naked = {"symbol": "ETH/USDT", "stop_present": False, "verified": False,
             "reasons": ["position_unprotected:ETH/USDT"]}
    partial = _snap(status="PARTIAL", venue_protection=False,
                    reasons=["position_unprotected:ETH/USDT"],
                    symbols=_snap()["symbols"] + [naked])
    assert owner_api.protection_for(eth, partial)["status"] == "UNPROTECTED"
    # a verified position in a PARTIAL book is not VERIFIED, and says why
    p = owner_api.protection_for(btc, partial)
    assert p["status"] == "PARTIAL" and "position_unprotected:ETH/USDT" in p["reasons"]
    later = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    p = owner_api.protection_for({"symbol": "BTC/USDT", "opened_at": later}, _snap())
    assert p["status"] == "UNVERIFIED" and "opened_after_last_verification" in p["reasons"]
    assert owner_api.protection_for(eth, _snap())["status"] == "UNVERIFIED"
    unread = _snap(status="UNREADABLE", venue_positions=False, reasons=["venue_timeout"],
                   symbols=[])
    p = owner_api.protection_for(btc, unread)
    assert p["status"] == "UNREADABLE" and "venue_timeout" in p["reasons"]
    p = owner_api.protection_for(btc, _snap(freshness="stale"))
    assert p["status"] == "STALE" and p["last_reported"] == "VERIFIED"
    assert owner_api.protection_for({"symbol": "X"}, None)["status"] == "UNAVAILABLE"
    # R2: an inconsistent observation or unknown precision is never VERIFIED
    for over in ({"observation_consistent": False}, {"precision_known": False}):
        assert owner_api.protection_for(btc, _snap(**over))["status"] == "PARTIAL"
        assert owner_api.book_protection(_snap(**over), []) == "PARTIAL"


def test_r2_snapshot_reader_contract(live):
    """Freshness is the stored snapshot's own checked_at; malformed rows are explicit."""
    from tests.owner_frontend_fixture import protection_snapshot, store_protection_snapshot
    c, journal, _, root = live
    now = datetime.now(timezone.utc)
    assert owner_api.SNAPSHOT_STALE_S == 120.0
    from trader.engine import protection_snapshot as ps
    assert ps.STALE_AFTER_S == owner_api.SNAPSHOT_STALE_S
    with journal._tx() as conn:
        store_protection_snapshot(conn, protection_snapshot(now - timedelta(seconds=119)))
    snap, err = owner_api.read_protection_snapshot(journal, now)
    assert err is None and snap["freshness"] == "fresh" and snap["generation"] == {"boot": 1, "seq": 1}
    snap, _ = owner_api.read_protection_snapshot(journal, now + timedelta(seconds=2))
    assert snap["freshness"] == "stale"            # ages by its own checked_at alone
    with journal._tx() as conn:                    # identity disagrees with its columns
        conn.execute("UPDATE protection_evidence SET seq = 7")
    assert owner_api.read_protection_snapshot(journal, now) == (None, "protection_snapshot_unreadable")
    with journal._tx() as conn:                    # schema-1 document is not accepted
        store_protection_snapshot(conn, {**protection_snapshot(now), "schema": 1})
    assert owner_api.read_protection_snapshot(journal, now) == (None, "protection_snapshot_unreadable")
    with journal._tx() as conn:
        conn.execute("DROP TABLE protection_evidence")
    assert owner_api.read_protection_snapshot(journal, now) == (None, "protection_snapshot_missing")


def test_chat_is_conversation_only(live):
    c, journal, gateway, _ = live
    signed_in(c)
    before = journal.kv_get("control_state")
    for text in ("freeze now", "/panic", "resume trading", "unhalt"):
        r = c.post("/owner-api/v1/chat", json={"message": text, "history": []}, headers=ORIGIN)
        assert r.status_code == 200
        body = r.json()
        assert body["operational"] is False and body["evidence"] == []
        assert body["request_id"]
    assert gateway.calls == []                          # never reached the Owner Interface
    assert journal.kv_get("control_state") == before
    assert FakeChat.calls == ["freeze now", "/panic", "resume trading", "unhalt"]


def test_chat_errors_are_structured(live):
    c, *_ = live
    signed_in(c)
    post = lambda body: c.post("/owner-api/v1/chat", json=body, headers=ORIGIN)
    assert post({"message": " "}).json()["error"] == "empty_message"
    assert post({"message": "x" * 2001}).status_code == 413
    assert post({"message": "hi", "history": [{"who": "system", "text": "x"}]}).status_code == 400
    FakeChat.mode = "fail"
    assert post({"message": "hi"}).status_code == 502
    FakeChat.mode = "fallback"
    r = post({"message": "hi"})
    assert r.status_code == 503 and r.json()["error"] == "llm_unavailable"
    # cross-origin chat is refused before any engine call
    FakeChat.calls = []
    assert c.post("/owner-api/v1/chat", json={"message": "hi"},
                  headers={"origin": "http://evil"}).status_code == 403
    assert FakeChat.calls == []


CONTROL = ("mutation($o:String!,$r:String!,$t:Float!){res:owner_control(operation:$o,"
           "request_id:$r,issued_at_ms:$t){status disposition control_state_after reasons "
           "replayed request_id}}")


def gql(c, op, rid, t):
    return c.post("/graphql", headers=ORIGIN, json={
        "query": CONTROL, "variables": {"o": op, "r": rid, "t": t}}).json()["data"]["res"]


def test_controls_go_through_owner_interface_idempotently(live):
    c, journal, gateway, _ = live
    signed_in(c)
    import time
    t = time.time() * 1000
    r = gql(c, "halt", "rid-halt-000000000001", t)
    assert r["status"] == "ACCEPTED" and r["control_state_after"] == "HALTED"
    again = gql(c, "halt", "rid-halt-000000000001", t)
    assert again["replayed"] is True and again["request_id"] == "dashboard-rid-halt-000000000001"
    r = gql(c, "resume", "rid-resume-0000000001", t)
    assert r["status"] == "CONTAINED" and journal.kv_get("control_state") == "HALTED"
    ops = [x["operation"] for x in gateway.calls]
    assert ops == ["halt", "halt", "resume"]
    # the owner mutation refuses anything outside containment/recovery
    r = gql(c, "close_trade", "rid-close-00000000001", t)
    assert r["status"] == "REFUSED" and "unsupported_operation" in r["reasons"]


def test_owner_interface_health_fails_closed(live):
    c, _, gateway, _ = live
    h = c.get("/owner-api/v1/owner-interface", headers=H).json()
    assert h["availability"] == "AVAILABLE" and h["control_state"] == "FROZEN"
    gateway.mode = "down"
    h = c.get("/owner-api/v1/owner-interface", headers=H).json()
    assert h["availability"] == "UNAVAILABLE" and h["control_state"] is None
    assert "kernel_unavailable" in h["reasons"]


def test_session_expiry_logout_and_forged_cookie(live, monkeypatch):
    c, *_ = live
    signed_in(c)
    assert c.get("/owner-api/v1/overview").status_code == 200
    import time as _time
    real = _time.time
    monkeypatch.setattr(_time, "time", lambda: real() + 13 * 3600)
    assert c.get("/owner-api/v1/overview").status_code == 401
    monkeypatch.setattr(_time, "time", real)
    assert c.get("/owner-api/v1/overview").status_code == 200
    assert c.post("/auth/logout", headers=ORIGIN).status_code == 200
    assert c.get("/owner-api/v1/overview").status_code == 401
    c.cookies.set("luffy_session", "9999999999.abc.deadbeef")
    assert c.get("/owner-api/v1/bootstrap").status_code == 401


def test_knowledge_binding_real_relations_and_unresolved(live):
    c, *_ = live
    k = c.get("/owner-api/v1/knowledge", headers=H).json()
    ids = {n["id"] for n in k["nodes"]}
    assert len([n for n in k['nodes'] if 'Knowledge' in n['lenses']]) == 4 and not k["truncated"]
    typed = [e for e in k["edges"] if e["kind"] == "typed"]
    assert {e["relation"] for e in typed} >= {"supports", "contradicts", "works_in", "fails_in"}
    unresolved = [e for e in k["edges"] if not e["resolved"]]
    assert {e["target"] for e in unresolved} == {"unresolved:Missing Concept",
                                                 "unresolved:Nowhere Note"}
    assert k["unresolved_edges"] == 2
    assert all(e["target"] in ids for e in k["edges"] if e["resolved"])
    assert k["lenses"]["Evidence"]["available"] is True
    assert k["lenses"]["Code"]["available"] is True
    node = next(n for n in k["nodes"] if n["label"] == "Aggressor Flow")
    assert node["provenance"]["time_basis"] == "file_modified"


def test_system_distinguishes_architecture_and_telemetry(live):
    c, journal, _, root = live
    s = c.get("/owner-api/v1/system", headers=H).json()
    assert s["events"] == []
    tele = {n["id"]: n["telemetry"] for n in s["nodes"]}
    assert tele["kernel"]["freshness"] == "fresh" and tele["kernel"]["health"] == "active"
    assert tele["risk"]["freshness"] == "unavailable" and tele["risk"]["health"] is None
    assert tele["orchestrator"]["health"] == "unknown"
    assert all(e["kind"] == "architecture" for e in s["edges"])
    seed(root, journal, "stale")
    tele = {n["id"]: n["telemetry"] for n in c.get("/owner-api/v1/system", headers=H).json()["nodes"]}
    assert tele["supervisor"]["freshness"] == "stale" and tele["kernel"]["freshness"] == "stale"


def test_other_routes_and_marks_enrichment(live):
    c, *_ = live
    assert c.get("/owner-api/v1/trades?limit=500", headers=H).json()["limit"] == 200
    assert c.get("/owner-api/v1/strategies", headers=H).json()["strategies"][0]["name"] == "Donchian"
    logs = c.get("/owner-api/v1/logs", headers=H).json()
    assert logs["tail"] == ["line one", "line two"]
    m = c.get("/owner-api/v1/enrichment/marks", headers=H).json()
    assert m["marks"]["BTC/USDT"]["mark"] == 61000.0 and m["observed_at"]


def test_frontend_served_at_root(live, tmp_path):
    c, *_ = live
    signed_in(c)
    r = c.get("/", follow_redirects=False)
    if r.status_code == 503:
        assert r.json()["error"] == "frontend_build_missing"
    else:
        assert r.status_code == 200 and r.headers["cache-control"].startswith("no-store")
        assert '<div id="root">' in r.text
    assert c.get("/assets/../config.yaml").status_code in (404, 503)
    assert c.get("/assets/nope.js").status_code in (404, 503)
    for gone in ("/owner-preview/", "/legacy/", "/attention.js", "/investigation.js"):
        assert c.get(gone).status_code == 404


def test_knowledge_endpoint_cache_errors_do_not_serve_previous_snapshot(live, monkeypatch):
    c, _, _, root = live
    first = c.get('/owner-api/v1/knowledge', headers=H)
    second = c.get('/owner-api/v1/knowledge', headers=H)
    assert first.content == second.content
    assert second.headers['cache-control'] == 'no-store'
    def fail(self):
        raise OSError('source disappeared')
    monkeypatch.setattr(owner_api.KnowledgeReadCache, 'fingerprint', fail)
    failed = c.get('/owner-api/v1/knowledge', headers=H)
    assert failed.status_code == 503
    assert failed.json()['error'] == 'knowledge_source_unavailable'
    assert 'nodes' not in failed.json()


def test_decision_offset_contract_is_available(live):
    c, *_ = live
    query = 'query($offset:Int!){decisions(limit:101,offset:$offset){id ts symbol action}}'
    first = c.post('/graphql', headers=H, json={'query': query, 'variables': {'offset': 0}}).json()
    after = c.post('/graphql', headers=H, json={'query': query, 'variables': {'offset': 1}}).json()
    assert 'errors' not in first and 'errors' not in after
    assert len(first['data']['decisions']) == 1
    assert after['data']['decisions'] == []
