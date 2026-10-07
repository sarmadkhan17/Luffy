"""Overview desk reads: cumulative realized curve, newest scan, research counts."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tests.owner_frontend_fixture import FakeChat, make_app
from tests.test_trade_history import add, H
from trader.core.journal import Journal
from trader.dashboard.overview_desk import latest_scan, realized_curve, research_counts

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def decision(j, did, scan, ts, symbol, score, executed=0):
    with j._tx() as c:
        c.execute("INSERT OR IGNORE INTO cycles (id, ts, symbol) VALUES (?,?,?)", ("c" + did, ts, symbol))
        c.execute("INSERT INTO decisions (id, cycle_id, ts, symbol, action, score, threshold, "
                  "confidence, executed, scan_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (did, "c" + did, ts, symbol, "HOLD", score, 0.28, 0.3, executed, scan))


def test_realized_curve_is_cumulative_and_skips_unknown(tmp_path):
    j = Journal(tmp_path / "luffy.db")
    add(j, "a", T0, pnl=10.0)
    add(j, "b", T0 + timedelta(days=1), pnl=-4.0)
    add(j, "c", T0 + timedelta(days=2), pnl=None)
    add(j, "o", T0 + timedelta(days=3), status="open")
    r = realized_curve(j)
    assert r["closed_trades"] == 3 and r["unknown_pnl_trades"] == 1
    assert [p["cumulative"] for p in r["points"]] == [10.0, 6.0]
    assert r["total"] == 6.0 and [p["pnl"] for p in r["last_closes"]] == [10.0, -4.0]


def test_latest_scan_uses_time_not_id_order(tmp_path):
    j = Journal(tmp_path / "luffy.db")
    decision(j, "zzz", "old", "2026-09-01T00:00:00+00:00", "BTC/USDT", 0.1)
    decision(j, "aaa", "new", "2026-10-01T00:00:00+00:00", "ETH/USDT", -0.2)
    decision(j, "bbb", "new", "2026-10-01T00:00:01+00:00", "BTC/USDT", 0.1)
    s = latest_scan(j)
    assert s["scan_id"] == "new" and [c["symbol"] for c in s["coins"]] == ["BTC/USDT", "ETH/USDT"]
    assert s["traded"] == 0 and latest_scan(Journal(tmp_path / "empty.db")) is None


def test_research_counts_distinguish_empty_from_absent_ledger(tmp_path):
    j = Journal(tmp_path / "luffy.db")
    r = research_counts(j, {"research": {"referee": False}})
    assert r["counts"]["candidates"] is None and r["referee_enabled"] is False  # no table: unknown
    with j._tx() as c:
        c.execute("CREATE TABLE research_candidates (hash TEXT, state TEXT)")
    assert research_counts(j, None)["counts"]["candidates"] == 0


def test_http_desk(tmp_path, monkeypatch):
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, journal, _ = make_app(tmp_path, monkeypatch)
    before = realized_curve(journal)["total"] or 0.0
    add(journal, "x", T0 + timedelta(days=400), pnl=2.0)
    body = TestClient(app).get("/owner-api/v1/desk", headers=H).json()
    assert body["errors"] == {} and body["scan"] is None
    assert body["realized"]["total"] == before + 2.0 and body["realized"]["points"][-1]["trade_id"] == "x"


def test_research_board_is_discovery_only_and_tolerates_missing_tables(tmp_path):
    from trader.dashboard.overview_desk import research_board
    b = research_board(Journal(tmp_path / "luffy.db"), {"research": {"referee": False, "handoff": False}})
    assert b["referee_enabled"] is False and b["handoff_enabled"] is False
    assert "discovery evidence only" in b["source"]


def test_http_research_board_route(tmp_path, monkeypatch):
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, _, _ = make_app(tmp_path, monkeypatch)
    r = TestClient(app).get("/owner-api/v1/research/board", headers=H)
    assert r.status_code == 200 and "generated_at" in r.json()


def test_strategy_board_aggregates_per_strategy_and_skips_unknown(tmp_path):
    from trader.dashboard.overview_desk import strategy_board
    j = Journal(tmp_path / "luffy.db")
    with j._tx() as c:
        c.execute("INSERT INTO strategies (id, name, kind, state, params, created_at, state_changed_at) VALUES "
                  "('spec:donchian','Donchian','spec','paper','{}','2026-09-01T00:00:00+00:00','2026-09-02T00:00:00+00:00')")
    add(j, "a", T0, pnl=10.0); add(j, "b", T0 + timedelta(days=1), pnl=-4.0)
    add(j, "u", T0 + timedelta(days=2), pnl=None); add(j, "o", T0 + timedelta(days=3), status="open")
    row = strategy_board(j, (T0 + timedelta(days=5)).timestamp())["strategies"][0]
    assert row["closed_trades"] == 2 and row["unknown_pnl_trades"] == 1 and row["open_trades"] == 1
    assert row["pnl_total"] == 6.0 and row["profit_factor"] == 2.5 and row["curve"] == [10.0, 6.0]
    assert "Legacy" in row["description"]


def test_operations_log_rolls_up_scans_per_hour_and_lists_orders(tmp_path):
    from trader.dashboard.overview_desk import operations_log
    j = Journal(tmp_path / "luffy.db")
    decision(j, "d1", "s1", "2026-10-01T10:05:00+00:00", "BTC/USDT", 0.1)
    decision(j, "d2", "s2", "2026-10-01T10:35:00+00:00", "BTC/USDT", 0.2)
    decision(j, "d3", "s3", "2026-10-01T11:05:00+00:00", "BTC/USDT", 0.3)
    add(j, "t1", datetime(2026, 10, 1, 9, tzinfo=timezone.utc), pnl=2.0)
    log = operations_log(j, datetime(2026, 10, 2, tzinfo=timezone.utc).timestamp())
    scans = [e for e in log["events"] if e["type"] == "Scan"]
    assert sorted(e["n"] for e in scans) == [1, 2]                     # two passes in 10h, one in 11h
    assert {e["id"] for e in log["events"] if e["type"] == "Order"} == {"open:t1", "close:t1"}
    assert log["events"] == sorted(log["events"], key=lambda e: e["at"], reverse=True)


def test_http_operations_log_route(tmp_path, monkeypatch):
    FakeChat.calls, FakeChat.mode = [], "ok"
    app, _, _ = make_app(tmp_path, monkeypatch)
    r = TestClient(app).get("/owner-api/v1/operations/log", headers=H)
    assert r.status_code == 200 and "events" in r.json()
