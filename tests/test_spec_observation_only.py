"""Specs are OBSERVED by the legacy sweep, never GOVERNED by it.

Excluding `kind='spec'` from the legacy genome lifecycle (see
test_lifecycle_kind_routing.py) initially excluded it from the loop's trailing
stats refresh too, so spec rows' persisted `stats_json` froze — and that blob
is read by trader/api/graphql_schema.py and trader/knowledge/vault.py.

The split this file pins: stats refresh for every declared member, state
changes for genomes only.

See docs/superpowers/reports/2026-09-20-donchian-retirement-attribution-audit.md
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.strategy import promotion


def _mk(j, sid, state, kind="ema_trend", stats="{}"):
    ts = datetime.now(timezone.utc).isoformat()
    j.query("INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json,retire_reason,state_changed_at) "
            "VALUES (?,?,?,'{}',?,'d','seed','hyp','inv','[]',"
            "'[\"futures\"]',0,'',?,?,'',?)", (sid, sid, kind, state, ts,
                                               stats, ts))


def _close(j, sid, n, pnl, tag="t"):
    for i in range(n):
        tid = f"{sid}_{tag}{i}"
        j.add_trade(Position(id=tid, symbol="X/USDT", side=Side.LONG, amount=1,
                             entry_price=100, notional_usdt=100,
                             strategy_id=sid, market_type="futures"))
        j.close_trade(tid, 100 + pnl, float(pnl), "sl_fill")


def _row(j, sid):
    return j.query("SELECT * FROM strategies WHERE id=?", (sid,))[0]


def _stats(j, sid):
    return json.loads(_row(j, sid)["stats_json"])


def test_spec_stats_json_refreshes(tmp_path):
    """The regression this file exists for: a spec's stats must not freeze."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_paper", "paper", kind="spec", stats="{}")
    _close(j, "s_paper", 3, -5, tag="l")
    _close(j, "s_paper", 2, +5, tag="w")

    promotion.evaluate_population(j)

    assert _stats(j, "s_paper") == {"trades": 5, "wins": 2,
                                    "pnl_usdt": -5.0, "pf": 0.667}


def test_spec_stats_keep_tracking_new_trades(tmp_path):
    """Refresh is live, not a one-shot backfill."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_paper", "paper", kind="spec")
    _close(j, "s_paper", 2, -5, tag="a")
    promotion.evaluate_population(j)
    assert _stats(j, "s_paper")["trades"] == 2

    _close(j, "s_paper", 3, +5, tag="b")
    promotion.evaluate_population(j)
    assert _stats(j, "s_paper")["trades"] == 5
    assert _stats(j, "s_paper")["wins"] == 3


def test_spec_state_is_never_changed_by_legacy_lifecycle(tmp_path):
    """Observation must not smuggle control back in.

    A losing record that retires a genome, and a winning record that promotes
    one, must both leave every spec field except stats_json untouched.
    """
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_lose", "paper", kind="spec")
    _close(j, "s_lose", 6, -5)
    _mk(j, "s_win", "paper", kind="spec")
    _close(j, "s_win", 15, +5)
    _mk(j, "s_active", "active", kind="spec")
    _close(j, "s_active", 6, -5)

    before = {s: _row(j, s) for s in ("s_lose", "s_win", "s_active")}
    actions = promotion.evaluate_population(j)

    assert not actions, actions
    for sid, b in before.items():
        a = _row(j, sid)
        assert a["state"] == b["state"]
        assert a["state_changed_at"] == b["state_changed_at"]
        assert a["retire_reason"] == b["retire_reason"] == ""
        # stats_json is the ONLY field a sweep may touch on a spec
        assert {k: v for k, v in a.items() if k != "stats_json"} == \
               {k: v for k, v in b.items() if k != "stats_json"}
        assert not [e for e in j.query(
            "SELECT * FROM brain_events WHERE subject=?", (sid,))
            if e["kind"] == "statistical_transition"]






def test_retired_rows_still_frozen(tmp_path):
    """Retired rows are skipped before stats — historical evidence is inert.

    This is what protects the Donchian row's recorded stats blob.
    """
    j = Journal(tmp_path / "j.db")
    stats = '{"trades": 6, "wins": 0, "pnl_usdt": -69.3361, "pf": 0.0}'
    _mk(j, "auth_donchian_breakout_trail", "retired", kind="spec", stats=stats)
    _close(j, "auth_donchian_breakout_trail", 3, -5)
    before = _row(j, "auth_donchian_breakout_trail")

    promotion.evaluate_population(j)

    assert _row(j, "auth_donchian_breakout_trail") == before


def test_spec_stats_are_committed(tmp_path):
    """A spec-only sweep produces no transition — the write must still land.

    `query()` does not commit, so before this the refresh depended on some
    later _tx() flushing it. Proven by reading through a SEPARATE connection.
    """
    import sqlite3
    db = tmp_path / "j.db"
    j = Journal(db)
    _mk(j, "s_only", "paper", kind="spec")
    _close(j, "s_only", 4, -5)

    assert not promotion.evaluate_population(j)      # no transitions at all

    other = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    blob = other.execute("SELECT stats_json FROM strategies WHERE id='s_only'"
                         ).fetchone()[0]
    assert json.loads(blob)["trades"] == 4, "stats refresh was not committed"
