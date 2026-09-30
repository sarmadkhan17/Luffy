"""LUFFY-CURRENT-TRUTH-CONTRACT-R1 final-review integration gaps (A–C).

A  legacy /api/summary, websocket, company cockpit, _today_stats and GraphQL
   heartbeat use the shared account/valuation truth; no partial aggregates or
   numeric defaults.
B  the journal fallback's source is the exact row captured in the same read;
   an older observation is never matched to a new cycle's equity row.
C  a pre-migration journal (no equity_provenance, no partial indexes), opened
   strictly read-only, still serves history as unknown provenance and exact
   reads as UNAVAILABLE — never a 500, never an implicit migration.

Temp journals and fakes only; no network, no production data.
"""
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from trader.core.journal import Journal
from trader.dashboard import current_truth, owner_api, owner_reads
from tests.owner_frontend_fixture import TOKEN, account_observation, make_app
from tests.test_current_truth_contract import _gql, _kernel, _kv, _obs

H = {"x-luffy-token": TOKEN}


def _now():
    return datetime.now(timezone.utc)


def _quote(price, age=1.0):
    return {"symbol": "?", "price": price, "field": "last", "source_ms": None,
            "received_at": time.time() - age, "error": None}


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    from trader.dashboard import server
    app, journal, _ = make_app(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_account_snapshot", lambda: {})
    monkeypatch.setattr(server, "_universe_prices", lambda: {})
    return TestClient(app), journal, server, monkeypatch


# ── A. legacy summary ──────────────────────────────────────────────────────
def test_summary_partial_valuation_has_no_total(legacy):
    c, journal, server, mp = legacy            # fixture quotes BTC only; ETH has none
    d = c.get("/api/summary", headers=H).json()
    assert d["total_upnl"] is None and d["valuation"]["status"] == "UNAVAILABLE"
    pos = {p["symbol"]: p for p in d["open_positions"]}
    assert pos["BTC/USDT"]["valuation_status"] == "OK" and pos["BTC/USDT"]["upnl"] == 10.0
    assert pos["BTC/USDT"]["quote_observed_at"]
    eth = pos["ETH/USDT"]
    assert eth["valuation_status"] == "UNAVAILABLE" and "mark" not in eth and "upnl" not in eth
    assert "quote_missing" in eth["valuation_reasons"]
    assert d["valuation"]["fees_funding"] == "EXCLUDED_UNPROVEN"


def test_summary_complete_valuation_and_account_basis(legacy):
    c, journal, server, mp = legacy
    mp.setattr(server, "_position_quotes", lambda j: (j.open_trades(), {
        "BTC/USDT": _quote(61000.0), "ETH/USDT": _quote(2900.0)}))
    d = c.get("/api/summary", headers=H).json()
    assert d["valuation"]["status"] == "COMPLETE" and d["total_upnl"] == 60.0
    assert d["equity"] == 5164.5 and d["account"]["status"] == "FRESH"
    assert d["account"]["basis"] == "venue_total_margin_balance"
    assert d["account"]["observed_at"]
    assert d["long_exposure"] == 600.0 and d["short_exposure"] == 1500.0
    assert d["exposure_basis"].startswith("journal entry notional")


@pytest.mark.parametrize("quotes", [
    {"BTC/USDT": _quote(61000.0), "ETH/USDT": _quote(2900.0, age=600)},          # stale
    {"BTC/USDT": _quote(61000.0), "ETH/USDT": dict(_quote(0), price="abc")},    # malformed
    {"BTC/USDT": _quote(61000.0), "ETH/USDT": dict(_quote(1.0), received_at=None)},
])
def test_summary_stale_or_malformed_quote_nulls_the_total(legacy, quotes):
    c, journal, server, mp = legacy
    mp.setattr(server, "_position_quotes", lambda j: (j.open_trades(), quotes))
    d = c.get("/api/summary", headers=H).json()
    assert d["total_upnl"] is None and d["valuation"]["status"] == "UNAVAILABLE"


def test_summary_valuation_failure_is_unavailable_not_zero(legacy):
    c, journal, server, mp = legacy
    mp.setattr(server, "_position_quotes",
               lambda j: (_ for _ in ()).throw(TimeoutError("venue")))
    d = c.get("/api/summary", headers=H).json()
    assert d["total_upnl"] is None and d["valuation"]["status"] == "UNAVAILABLE"
    assert all(p["valuation_status"] == "UNAVAILABLE" for p in d["open_positions"])


def test_summary_position_set_mismatch_is_not_complete(legacy):
    c, journal, server, mp = legacy
    mp.setattr(server, "_position_quotes", lambda j: (j.open_trades()[:1], {
        "BTC/USDT": _quote(61000.0), "ETH/USDT": _quote(2900.0)}))
    d = c.get("/api/summary", headers=H).json()
    assert d["total_upnl"] is None and "position_set_changed" in d["valuation"]["reasons"]


@pytest.mark.parametrize("snap,total", [
    ({}, None),                                                        # no snapshot
    ({"assets": {"USDT": 10.0, "ETH": "0.500000"}, "assets_total": 10.0}, None),  # partial
    ({"assets": {"USDT": 10.0, "USDC": 5.0}, "assets_total": 15.0}, 15.0),
])
def test_summary_assets_total_is_never_a_default_or_partial(legacy, snap, total):
    c, journal, server, mp = legacy
    mp.setattr(server, "_account_snapshot", lambda: snap)
    assert c.get("/api/summary", headers=H).json()["assets_total"] == total


def test_summary_without_account_observation_says_so(legacy):
    c, journal, *_ = legacy
    with journal._tx() as conn:
        conn.execute("DELETE FROM state_kv WHERE key='account_observation'")
    d = c.get("/api/summary", headers=H).json()
    assert d["account"]["status"] == "UNAVAILABLE"
    assert d["account"]["source_status"] == "PROVENANCE_UNRECORDED"
    assert d["account"]["freshness"] == "unavailable"
    assert d["equity_prev_provenance"]["row_kind"] == "unknown"


def test_websocket_equity_carries_its_source(legacy):
    c, journal, *_ = legacy
    with c.websocket_connect("/ws/live", headers=H) as ws:
        p = json.loads(ws.receive_text())
    assert p["equity"] == [{"equity": 5164.5, "status": "FRESH",
                            "basis": "venue_total_margin_balance",
                            "observed_at": p["equity"][0]["observed_at"],
                            "freshness": "fresh"}]
    journal.kv_set("account_observation", "{broken")
    with c.websocket_connect("/ws/live", headers=H) as ws:
        p = json.loads(ws.receive_text())
    assert p["equity"] == [] and p["account_error"] == "account_observation_malformed"


def test_today_and_company_day_pnl_missing_is_not_zero(tmp_path):
    from trader.dashboard import server
    j = Journal(tmp_path / "j.db")
    assert server._today_stats(j)["realized_pnl_today"] == 0.0         # no closes: real 0
    with j._tx() as c:
        c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,opened_at,closed_at,"
                  "realized_pnl,status) VALUES('x','X/USDT','long',1,1,?,?,NULL,'closed')",
                  (_now().isoformat(), _now().isoformat()))
    assert server._today_stats(j)["realized_pnl_today"] is None
    company = json.dumps(server.build_company(j, {"risk": {}}), ensure_ascii=False)
    assert '["Day P&L", "—"]' in company
    assert '["Equity", "—"]' in company                     # no observation: not $0
    assert '["Risk", "UNAVAILABLE"]' in company


def test_company_equity_comes_from_the_account_observation(tmp_path):
    from trader.dashboard import server
    j = Journal(tmp_path / "j.db")
    j.log_equity(9999.0, 9999.0, 0)                          # a row with no provenance
    _kv(j, "account_observation", account_observation(_now(), value=1234.0))
    company = json.dumps(server.build_company(j, {"risk": {}}), ensure_ascii=False)
    assert '["Equity", "$1,234"]' in company and "9,999" not in company


@pytest.mark.parametrize("stamp,expect", [
    (lambda: time.time() + 0.5, "invalid"), (lambda: "garbage", "invalid"),
    (lambda: time.time() - 42, "42s")])
def test_graphql_heartbeat_future_or_malformed_is_invalid(tmp_path, monkeypatch, stamp, expect):
    from trader.api import graphql_schema
    hb = tmp_path / "hb.json"
    hb.write_text(json.dumps({"timestamp": stamp()}))
    monkeypatch.setattr(graphql_schema, "HEARTBEAT_PATH", str(hb))
    d = _gql(Journal(tmp_path / "j.db"), "{ status { heartbeat_age_s } }")
    assert d["data"]["status"]["heartbeat_age_s"] in (expect, "41s", "43s")
    if expect == "invalid":
        assert d["data"]["status"]["heartbeat_age_s"] == "invalid"


# ── B. exact fallback row, bound observation ───────────────────────────────
def test_fallback_uses_the_row_it_read_not_a_later_one(tmp_path, monkeypatch):
    j = Journal(tmp_path / "j.db")
    k = _kernel(j, monkeypatch, [(1000.0, "venue_total_margin_balance", []),
                                 (None, None, ["venue_account_failed:X"])])
    k._risk_step()
    j.log_equity(1000.0, 1000.0, 0, provenance=k._equity_provenance(1000.0))
    first = _obs(j)
    captured = j.query("SELECT ts FROM equity")[0]["ts"]
    real = k._last_equity_fallback

    def read_then_swap():
        value = real()
        later = (datetime.fromisoformat(captured) + timedelta(seconds=5)).isoformat(
            timespec="seconds")
        with j._tx() as c:   # a concurrent writer lands a same-value row between reads
            c.execute("INSERT INTO equity VALUES (?,?,?,?)", (later, value, value, 0))
            c.execute("INSERT INTO equity_provenance(ts, provenance) VALUES (?,?)",
                      (later, json.dumps({"value": value, "observed_at": "2026-01-01T00:00:00"
                                          "+00:00", "basis": "intruder",
                                          "row_kind": "venue_observation"})))
        return value
    monkeypatch.setattr(k, "_last_equity_fallback", read_then_swap)
    balance, _ = k._risk_step()
    o = _obs(j)
    assert balance == 1000.0                                  # trading value unchanged
    assert o["fallback"]["row_written_at"] == captured        # the row actually reused
    assert o["observed_at"] == first["observed_at"]           # its own provenance
    assert o["fallback"]["value_origin_basis"] == "venue_total_margin_balance"


def test_fallback_row_capture_is_one_read(tmp_path, monkeypatch):
    j = Journal(tmp_path / "j.db")
    k = _kernel(j, monkeypatch, [(None, None, [])])
    j.log_equity(5.0, 5.0, 0)
    calls = []
    real_query = j.query

    def counting(sql, params=()):
        calls.append(sql)
        return real_query(sql, params)
    monkeypatch.setattr(j, "query", counting)
    k._risk_step()
    equity_reads = [s for s in calls if "FROM equity" in s]
    assert len(equity_reads) == 1 and "equity_provenance" in equity_reads[0]


class _Explodes:
    def get(self, *a, **k):
        raise RuntimeError("boom")


def test_failed_observation_is_never_reused_for_the_next_row(tmp_path, monkeypatch):
    j = Journal(tmp_path / "j.db")
    k = _kernel(j, monkeypatch, [(1000.0, "venue_total_margin_balance", [])])
    k._risk_step()
    assert k._equity_provenance(1000.0) is not None           # this cycle: bound
    # next cycle: same value, but building the observation fails
    def read():
        k._balance_read = _Explodes()
        return 1000.0
    monkeypatch.setattr(k, "_fetch_balance_fresh", read, raising=False)
    balance, status = k._risk_step()
    assert balance == 1000.0 and status["risk_state"] == "ok"  # behaviour preserved
    assert k._account_obs is None
    assert k._equity_provenance(1000.0) is None               # unknown, not last cycle's


def test_previous_cycle_observation_with_equal_value_is_not_bound(tmp_path, monkeypatch):
    j = Journal(tmp_path / "j.db")
    k = _kernel(j, monkeypatch, [(1000.0, "venue_total_margin_balance", [])])
    k._risk_step()
    k._risk_attempt_at = _now().isoformat()                  # a newer attempt
    assert k._equity_provenance(1000.0) is None


# ── C. pre-migration journal, strictly read-only ───────────────────────────
OLD_SCHEMA = """
CREATE TABLE equity (ts TEXT PRIMARY KEY, equity REAL NOT NULL, balance REAL NOT NULL,
                     open_positions INTEGER);
CREATE TABLE state_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE decisions (id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, ts TEXT NOT NULL,
    symbol TEXT NOT NULL, action TEXT NOT NULL, score REAL NOT NULL, threshold REAL NOT NULL,
    confidence REAL NOT NULL, executed INTEGER NOT NULL DEFAULT 0, skip_reason TEXT,
    size_usdt REAL DEFAULT 0, entry_price REAL, strategy_ids TEXT);
CREATE INDEX idx_decisions_exec ON decisions(executed);
CREATE TABLE trades (id TEXT PRIMARY KEY, decision_id TEXT, symbol TEXT NOT NULL,
    side TEXT NOT NULL, amount REAL NOT NULL, entry_price REAL NOT NULL, exit_price REAL,
    notional_usdt REAL, leverage INTEGER DEFAULT 1, stop_loss REAL, take_profit REAL,
    sl_order_id TEXT DEFAULT '', strategy_id TEXT, strategy_name TEXT, market_type TEXT,
    exec_mode TEXT, opened_at TEXT NOT NULL, closed_at TEXT, realized_pnl REAL DEFAULT 0,
    close_reason TEXT, status TEXT NOT NULL DEFAULT 'open');
"""


class ReadOnlyJournal:
    """sqlite mode=ro: can never create a table or index (no Journal init)."""

    def __init__(self, path):
        self.c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.c.row_factory = sqlite3.Row

    def query(self, sql, params=()):
        return [dict(r) for r in self.c.execute(sql, params).fetchall()]

    def kv_get(self, key, default=None):
        r = self.c.execute("SELECT value FROM state_kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else default


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    now = _now()
    for i in range(3):
        c.execute("INSERT INTO equity VALUES (?,?,?,?)",
                  ((now - timedelta(hours=3 - i)).isoformat(timespec="seconds"),
                   1000.0 + i, 1000.0, 0))
    c.execute("INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,"
              "executed,skip_reason) VALUES('d1','c',?,'X/USDT','BUY',0,0.3,0,1,NULL)",
              (now.isoformat(),))
    c.commit()
    c.close()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def test_old_journal_history_reads_as_unknown_provenance(old_db):
    path, digest = old_db
    ro = ReadOnlyJournal(path)
    series, err = owner_api.read_equity_series(ro, _now())
    assert err is None and len(series["points"]) == 3
    assert {p["row_kind"] for p in series["points"]} == {"unknown"}
    assert all(p["source_observed_at"] is None for p in series["points"])
    pts = _gql(ro, "{ equity_curve { ts equity row_kind source_observed_at } }")
    assert "errors" not in pts
    assert [p["row_kind"] for p in pts["data"]["equity_curve"]] == ["unknown"] * 3
    acc, err = current_truth.read_account(ro, _now())
    assert err is None and acc["status"] == "UNAVAILABLE" and acc["equity"] == 1002.0
    assert acc["source_status"] == "PROVENANCE_UNRECORDED"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest   # never migrated


def test_old_journal_exact_reads_are_unavailable_not_500(old_db):
    path, digest = old_db
    ro = ReadOnlyJournal(path)
    lat = owner_reads.latest_activity(ro)
    assert lat["latest_decision"]["record"]["id"] == "d1"   # plain rowid read still works
    for k in ("latest_executed", "latest_requested", "latest_rejection"):
        assert lat[k]["status"] == "UNAVAILABLE" and lat[k]["found"] is None
        assert lat[k]["reason"].startswith("exact_index_missing:")
    with pytest.raises(owner_reads.ExactIndexMissing):
        owner_reads.latest_decisions(ro, owner_reads.EXECUTED)
    names = {r["name"] for r in ro.query("SELECT name FROM sqlite_master")}
    assert "equity_provenance" not in names and "idx_decisions_executed_ts" not in names
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_owner_route_on_unmigrated_journal_is_not_500(old_db, tmp_path):
    from fastapi import FastAPI
    path, _ = old_db
    app = FastAPI()
    owner_reads.install(app, journal=ReadOnlyJournal(path), root=tmp_path, vault=tmp_path)
    r = TestClient(app).get("/owner-api/v1/activity/latest")
    assert r.status_code == 200
    assert r.json()["latest_executed"]["status"] == "UNAVAILABLE"
