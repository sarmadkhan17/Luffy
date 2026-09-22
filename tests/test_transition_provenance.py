"""Every state transition must persist the reason it already announces.

`transition()` wrote `state` and `state_changed_at` but not the reason, so a
row could carry a changed state with no recorded justification. That is how
`auth_donchian_breakout_trail` came to be retired on 2026-09-20 with an empty
`retire_reason` — the only spec retirement in the journal with no evidence on
the row — and had to be traced by elimination against the other writers.

See docs/superpowers/reports/2026-09-20-donchian-retirement-attribution-audit.md
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.strategy import promotion


def _mk(j, sid, state, kind="ema_trend", created=None, changed=None,
        retire_reason=""):
    created = created or datetime.now(timezone.utc).isoformat()
    j.query("INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json,retire_reason,state_changed_at) "
            "VALUES (?,?,?,'{}',?,'d','seed','hyp','inv','[]',"
            "'[\"futures\"]',0,'',?,'{}',?,?)",
            (sid, sid, kind, state, created, retire_reason, changed or created))


def _close(j, sid, n, pnl, tag="t"):
    for i in range(n):
        tid = f"{sid}_{tag}{i}"
        p = Position(id=tid, symbol="X/USDT", side=Side.LONG, amount=1,
                     entry_price=100, notional_usdt=100, strategy_id=sid,
                     market_type="futures")
        j.add_trade(p)
        j.close_trade(tid, 100 + pnl, float(pnl), "sl_fill")


def _row(j, sid):
    return j.query("SELECT * FROM strategies WHERE id=?", (sid,))[0]


def _events(j, sid):
    return [e for e in j.query("SELECT * FROM brain_events WHERE subject=?",
                               (sid,))
            if e["kind"] == "statistical_transition"]


def test_state_changes_exactly_as_before(tmp_path):
    """Thresholds and destination states are untouched by the fix."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_retire", "paper")                 # 6 consecutive losses -> retired
    _close(j, "g_retire", promotion.DEMOTE_CONSEC_LOSSES, -5)
    _mk(j, "g_promote", "paper")                # 15 wins -> active
    _close(j, "g_promote", promotion.PROMOTE_MIN_TRADES, +5)
    _mk(j, "g_demote", "active")                # 6 consecutive losses -> demoted
    _close(j, "g_demote", promotion.DEMOTE_CONSEC_LOSSES, -5)

    promotion.evaluate_population(j)

    assert _row(j, "g_retire")["state"] == "retired"
    assert _row(j, "g_promote")["state"] == "active"
    assert _row(j, "g_demote")["state"] == "demoted"


def test_reason_is_stored_on_the_row(tmp_path):
    """The defect itself: a changed state with no recorded justification."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_paper", "paper")
    _close(j, "g_paper", promotion.DEMOTE_CONSEC_LOSSES, -5)

    promotion.evaluate_population(j)

    row = _row(j, "g_paper")
    assert row["state"] == "retired"
    assert row["retire_reason"], "state changed with empty provenance"
    assert "failed probation" in row["retire_reason"]


def test_every_transition_kind_stores_provenance(tmp_path):
    """Promotion and demotion record a reason too, not just retirement."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_promote", "paper")
    _close(j, "g_promote", promotion.PROMOTE_MIN_TRADES, +5)
    _mk(j, "g_demote", "active")
    _close(j, "g_demote", promotion.DEMOTE_CONSEC_LOSSES, -5)

    promotion.evaluate_population(j)

    assert "probation passed" in _row(j, "g_promote")["retire_reason"]
    assert "consecutive losses" in _row(j, "g_demote")["retire_reason"]


def test_brain_event_and_stored_reason_agree(tmp_path):
    """One reason string, two destinations — they must not drift."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_paper", "paper")
    _close(j, "g_paper", promotion.DEMOTE_CONSEC_LOSSES, -5)

    actions = promotion.evaluate_population(j)

    events = _events(j, "g_paper")
    assert len(events) == 1
    detail = json.loads(events[0]["detail"])
    stored = _row(j, "g_paper")["retire_reason"]
    assert detail["reason"] == stored
    assert [a for a in actions if a["id"] == "g_paper"][0]["reason"] == stored


def test_noop_transition_does_not_overwrite_provenance(tmp_path):
    """demoted -> demoted is suppressed; the original reason must survive.

    Guards the self-transition suppression at the top of transition(): a row
    that stays put keeps the provenance of the change that put it there.
    """
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_dem", "active")
    _close(j, "g_dem", promotion.DEMOTE_CONSEC_LOSSES, -5)

    promotion.evaluate_population(j)             # active -> demoted
    first = _row(j, "g_dem")
    assert first["state"] == "demoted" and first["retire_reason"]

    promotion.evaluate_population(j)             # demoted -> demoted: no-op
    promotion.evaluate_population(j)
    after = _row(j, "g_dem")

    assert after["retire_reason"] == first["retire_reason"]
    assert after["state_changed_at"] == first["state_changed_at"]
    assert len(_events(j, "g_dem")) == 1


def test_skipped_transition_writes_nothing(tmp_path):
    """A stale expected-state must not clobber the row or emit an event.

    Simulates a concurrent writer: the row is moved out from under the sweep
    after its state was read. The guarded UPDATE matches zero rows, so no
    provenance is overwritten and no event is announced.
    """
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_race", "paper")
    _close(j, "g_race", promotion.DEMOTE_CONSEC_LOSSES, -5)

    real_list = j.list_strategies

    def racing_list(*a, **kw):
        rows = real_list(*a, **kw)
        # another writer retires it, with its own provenance, after the read
        with j._tx() as c:
            c.execute("UPDATE strategies SET state='retired', "
                      "retire_reason='retired by another writer' WHERE id=?",
                      ("g_race",))
        return rows

    j.list_strategies = racing_list
    actions = promotion.evaluate_population(j)
    j.list_strategies = real_list

    row = _row(j, "g_race")
    assert row["retire_reason"] == "retired by another writer"
    assert not [a for a in actions if a["id"] == "g_race"], actions
    assert not _events(j, "g_race")


def test_retired_rows_are_never_revisited(tmp_path):
    """No historical rewrite: a retired row keeps its row byte-for-byte."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "g_dead", "retired", retire_reason="measured 2026-09-02: PF 0.41")
    _close(j, "g_dead", promotion.DEMOTE_CONSEC_LOSSES, -5)
    before = _row(j, "g_dead")

    promotion.evaluate_population(j)

    assert _row(j, "g_dead") == before
