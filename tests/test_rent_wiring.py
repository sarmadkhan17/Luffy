"""The rent check reaches Sarmad: kernel thread, /rent, GraphQL, Manager card."""
import inspect
import json

from trader.core.config import load_config
from trader.core.journal import Journal


def _seed(j):
    j.kv_set("rent_state", json.dumps({
        "week_start": "2026-09-14", "net": 12.5, "bar": 50.0,
        "days_left": 4.6, "status": "IN_PROGRESS",
        "updated_at": "2026-09-16T09:00:00+00:00"}))
    j.log_brain_event("rent_verdict", "2026-09-07",
                      {"verdict": "FAIL", "net": -3.0})


def test_config_carries_the_bar_and_the_first_week():
    r = load_config()["rent"]
    assert r["weekly_usdt"] == 50 and str(r["first_week_start"]) == "2026-09-14"


def test_graphql_rent_reads_the_keeper_state(tmp_path):
    from trader.api.graphql_schema import make_graphql_router
    j = Journal(tmp_path / "j.db")
    _seed(j)
    res = make_graphql_router(j).schema.execute_sync(
        "{ rent { week_start net bar status history { week_start verdict net } } }")
    assert res.errors is None, res.errors
    r = res.data["rent"]
    assert r["week_start"] == "2026-09-14" and r["net"] == 12.5 and r["bar"] == 50.0
    assert r["history"] == [{"week_start": "2026-09-07", "verdict": "FAIL", "net": -3.0}]


def test_the_manager_card_shows_the_rent(tmp_path):
    from trader.dashboard.server import build_company
    j = Journal(tmp_path / "j.db")
    _seed(j)
    blob = json.dumps(build_company(j, load_config()))
    assert '["Rent", "+12 / 50, 4.6d left"]' in blob
    assert '["Weeks", "F"]' in blob          # last verdicts, oldest first


def test_the_kernel_runs_the_rent_check_and_answers_rent():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert 'name="rent-check"' in src
    assert 'msg.startswith("/rent")' in src
    assert "RentKeeper(" in src
