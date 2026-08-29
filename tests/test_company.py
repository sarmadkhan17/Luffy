"""Company cockpit data — build_company() derives live per-employee status.

Uses a stub journal (keyword-routed query results) so no live DB is needed.
"""
from __future__ import annotations

from trader.dashboard.server import build_company


class StubJournal:
    def kv_get(self, key, default=None):
        return {"control_state": "ACTIVE", "market_type": "futures"}.get(key, default)

    def open_trades(self):
        return [{"symbol": "BTC/USDT", "side": "long", "notional": 500.0}]

    def agent_accuracy(self, since_hours=None):
        return [{"agent": "structure", "n": 40, "accuracy": 0.62},
                {"agent": "flow", "n": 12, "accuracy": 0.5}]

    def query(self, sql, params=()):
        s = sql.lower()
        if "count(" in s:
            return [{"n": 3}]
        if "brain_events" in s:
            return [{"ts": "2026-08-29T10:00:00", "kind": "harvest_cycle",
                     "subject": "ideas", "detail": '{"accepted": 2}'}]
        if "from votes" in s:
            return [{"agent": "structure", "side": "LONG", "conviction": 0.34,
                     "ts": "2026-08-29T12:00:00"}]
        if "from trades" in s:
            return [{"ts": "2026-08-29T11:00:00", "symbol": "BTC/USDT",
                     "realized_pnl": 4.2}]
        if "from cycles" in s:
            return [{"ts": "2026-08-29T12:00:00", "id": "c1"}]
        if "from equity" in s:
            return [{"equity": 10234.5}]
        return []


def _company():
    return build_company(StubJournal(), {"risk": {"portfolio_heat_cap_pct": 15.0}})


def test_has_entry_per_employee():
    from trader.org import Org
    c = _company()
    names = {e["name"] for e in c["employees"]}
    for e in Org.load().all():
        assert e.name in names, f"missing {e.name}"


def test_manager_first_and_reports_to():
    c = _company()
    emps = c["employees"]
    assert emps[0]["name"] == "Manager"
    assert emps[0]["reports_to"] is None
    assert all(e["reports_to"] == "Manager"
               for e in emps if e["name"] != "Manager")


def test_every_entry_has_status_and_node():
    c = _company()
    for e in c["employees"]:
        assert e["status"] in {"active", "idle", "stale", "alert", "offline"}
        assert e["node"] == f"00 Company/{e['name']}.md"
        assert "last_output" in e and "metric" in e


def test_analyst_metric_shows_accuracy():
    c = _company()
    struct = next(e for e in c["employees"] if e["name"] == "Structure Analyst")
    assert "62" in struct["metric"]  # 62% accuracy from the stub


def test_trader_reflects_open_positions():
    c = _company()
    trader = next(e for e in c["employees"] if e["name"] == "Trader")
    assert "1" in trader["metric"]  # one open position
    assert trader["status"] in {"active", "idle"}
