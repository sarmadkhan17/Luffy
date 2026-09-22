"""Legacy genome lifecycle must never govern `kind='spec'` rows.

`promotion.evaluate_population` implements the LEGACY GENOME rules
(kernel.py says so at the call site). `journal.list_strategies()` applies no
kind filter, so it also handed that function every spec row — and on
2026-09-20 the 6-consecutive-loss counter retired the Donchian spec on a
closed-trade-only sample while six winners sat open, writing no retire_reason.

Specs retire through analyst.review_deployed -> rolling.has_decayed, which
scores pooled backtested evidence on the spec's own universe and treats too
few recent trades as idle rather than decay.

See docs/superpowers/reports/2026-09-20-donchian-retirement-attribution-audit.md
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.strategy import promotion


def _mk(j, sid, state, kind="ema_trend", retire_reason="", stats="{}"):
    j.query("INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json,retire_reason,state_changed_at) "
            "VALUES (?,?,?,'{}',?,'d','seed','hyp','inv','[]',"
            "'[\"futures\"]',0,'',?,?,?,?)",
            (sid, sid, kind, state, datetime.now(timezone.utc).isoformat(),
             stats, retire_reason, "2026-09-20T09:04:33+00:00"))


def _losses(j, sid, n):
    """n closed losers — enough to trip DEMOTE_CONSEC_LOSSES."""
    for i in range(n):
        p = Position(id=f"{sid}_t{i}", symbol="X/USDT", side=Side.LONG,
                     amount=1, entry_price=100, notional_usdt=100,
                     strategy_id=sid, market_type="futures")
        j.add_trade(p)
        j.close_trade(f"{sid}_t{i}", 95, -5.0, "sl_fill")


def _row(j, sid):
    return j.query("SELECT * FROM strategies WHERE id=?", (sid,))[0]


def test_genome_rows_still_evaluate_normally(tmp_path):
    """The legacy path must keep working for the rows it owns."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_paper", "paper")
    _losses(j, "g_paper", promotion.DEMOTE_CONSEC_LOSSES)

    actions = promotion.evaluate_population(j)

    assert any(a["id"] == "g_paper" and a["to"] == "retired"
               for a in actions), actions
    assert _row(j, "g_paper")["state"] == "retired"


def test_spec_rows_are_ignored_by_legacy_lifecycle(tmp_path):
    """Identical losing record on a spec row must produce NO transition.

    Same trade history that retires the genome above. The spec is left for
    rolling.has_decayed, which asks a different question of different
    evidence.
    """
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_paper", "paper", kind="spec")
    _losses(j, "s_paper", promotion.DEMOTE_CONSEC_LOSSES)

    actions = promotion.evaluate_population(j)

    assert not [a for a in actions if a["id"] == "s_paper"], actions
    row = _row(j, "s_paper")
    assert row["state"] == "paper"
    assert row["retire_reason"] == ""
    # no statistical_transition event may be attributed to a spec
    events = j.query("SELECT * FROM brain_events WHERE subject=?", ("s_paper",))
    assert not [e for e in events if e["kind"] == "statistical_transition"], events


def test_spec_and_genome_coexist_in_one_sweep(tmp_path):
    """The filter must be per-row, not abort the sweep."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_paper", "paper", kind="spec")
    _mk(j, "g_paper", "paper")
    _losses(j, "s_paper", promotion.DEMOTE_CONSEC_LOSSES)
    _losses(j, "g_paper", promotion.DEMOTE_CONSEC_LOSSES)

    actions = promotion.evaluate_population(j)

    assert {a["id"] for a in actions} == {"g_paper"}, actions
    assert _row(j, "s_paper")["state"] == "paper"
    assert _row(j, "g_paper")["state"] == "retired"


def test_existing_retired_spec_rows_are_not_mutated(tmp_path):
    """A spec already retired keeps its row byte-for-byte.

    Guards the Donchian row itself: its (empty) retire_reason, its recorded
    state_changed_at and its stats blob are historical evidence of HOW it was
    retired, and this fix must not rewrite them.
    """
    j = Journal(tmp_path / "j.db")
    stats = '{"trades": 6, "wins": 0, "pnl_usdt": -69.3361, "pf": 0.0}'
    _mk(j, "auth_donchian_breakout_trail", "retired", kind="spec", stats=stats)
    _losses(j, "auth_donchian_breakout_trail", promotion.DEMOTE_CONSEC_LOSSES)
    before = _row(j, "auth_donchian_breakout_trail")

    promotion.evaluate_population(j)

    assert _row(j, "auth_donchian_breakout_trail") == before


def test_retired_genome_rows_are_also_left_alone(tmp_path):
    """Unchanged pre-existing behaviour: retired is terminal for genomes too."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_dead", "retired", retire_reason="measured: PF 0.41")
    _losses(j, "g_dead", promotion.DEMOTE_CONSEC_LOSSES)
    before = _row(j, "g_dead")

    promotion.evaluate_population(j)

    assert _row(j, "g_dead") == before
