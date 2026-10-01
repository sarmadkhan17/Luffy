"""Legacy lifecycle statistics remain descriptive without replay authority."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.strategy import promotion


def _mk(j, sid, state, created=None):
    created = created or datetime.now(timezone.utc).isoformat()
    j.query("INSERT INTO strategies (id,name,kind,params,state,description,"
            "origin,hypothesis,invalidation,regime_filter,markets,generation,"
            "parent_id,created_at,stats_json) VALUES (?,?,'ema_trend',"
            "'{}',?,'d','seed','hyp','inv','[]','[\"futures\"]',0,'',?,'{}')",
            (sid, sid, state, created))


def _losses(j, sid, n, prefix="l"):
    for i in range(n):
        p = Position(id=f"{prefix}{sid}{i}", symbol="X/USDT", side=Side.LONG,
                     amount=1, entry_price=100, notional_usdt=100,
                     strategy_id=sid, market_type="futures")
        j.add_trade(p)
        j.close_trade(f"{prefix}{sid}{i}", 95, -5.0, "sl_fill")


def test_paper_losses_do_not_authorize_retirement(tmp_path):
    """Legacy loss counts cannot bypass replay evidence and proposal gates."""
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_paper", "paper")
    _losses(j, "s_paper", 6)
    actions = promotion.evaluate_population(j)
    assert actions == []
    assert j.query("SELECT state FROM strategies WHERE id='s_paper'")[0]["state"] == "paper"


def test_no_self_transition_spam(tmp_path):
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_dem", "demoted")
    _losses(j, "s_dem", 8)
    first = promotion.evaluate_population(j)
    second = promotion.evaluate_population(j)
    third = promotion.evaluate_population(j)
    # already demoted and staying demoted -> nothing to announce, ever
    assert not [a for a in first + second + third
                if a["id"] == "s_dem" and a["from"] == a["to"]]
    events = j.query("SELECT COUNT(*) n FROM brain_events "
                     "WHERE kind='statistical_transition' AND subject='s_dem'")
    assert events[0]["n"] == 0


def test_retirement_clock_runs_from_demotion_not_birth(tmp_path):
    """A strategy born 60d ago but demoted just now must NOT retire."""
    j = Journal(tmp_path / "j.db")
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    _mk(j, "s_old", "demoted", created=old)
    j.query("UPDATE strategies SET state_changed_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), "s_old"))
    _losses(j, "s_old", 3)
    actions = promotion.evaluate_population(j)
    assert not [a for a in actions if a["to"] == "retired"], actions

    # Even an old demotion cannot authorize retirement without replay evidence.
    j.query("UPDATE strategies SET state_changed_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
             "s_old"))
    actions = promotion.evaluate_population(j)
    assert actions == []
    assert j.query("SELECT state FROM strategies WHERE id='s_old'")[0]["state"] == "demoted"


def test_refused_transition_preserves_state_clock(tmp_path):
    j = Journal(tmp_path / "j.db")
    _mk(j, "s_stamp", "active")
    _losses(j, "s_stamp", 6)
    promotion.evaluate_population(j)
    row = j.query("SELECT state, state_changed_at FROM strategies "
                  "WHERE id='s_stamp'")[0]
    assert row["state"] == "active"
    assert row["state_changed_at"] == ""
