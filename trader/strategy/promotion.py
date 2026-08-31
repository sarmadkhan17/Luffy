"""Strategy lifecycle genetics — statistical promotion/demotion.

Deterministic, instant, no LLM required (REQUIREMENTS §6):
  PAPER → ACTIVE   when ≥15 trades, winrate ≥40%, PF ≥1.15
  ACTIVE/DEMOTED → DEMOTED  on 6 consecutive losses or PF<0.85 over ≥20
  DEMOTED → RETIRED after 14 days without recovery
The Strategist (LLM) may overrule upward with written rationale only.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.journal import Journal
from ..core.types import StrategyState

log = logging.getLogger(__name__)

PROMOTE_MIN_TRADES = 15
PROMOTE_MIN_WINRATE = 0.40
PROMOTE_MIN_PF = 1.15
DEMOTE_CONSEC_LOSSES = 6
DEMOTE_MAX_PF = 0.85
DEMOTE_MIN_TRADES = 20
RETIRE_DEMOTED_DAYS = 14


def _stats_for(journal: Journal, strategy_id: str) -> dict:
    trades = journal.trades_for_strategy(strategy_id)
    closed = [t for t in trades if t["status"] == "closed"]
    wins = [t for t in closed if float(t.get("realized_pnl") or 0) > 0]
    losses = [t for t in closed if float(t.get("realized_pnl") or 0) <= 0]
    gross_win = sum(float(t["realized_pnl"]) for t in wins)
    gross_loss = sum(abs(float(t["realized_pnl"])) for t in losses)
    consec = 0
    for t in reversed(closed):
        if float(t.get("realized_pnl") or 0) <= 0:
            consec += 1
        else:
            break
    pf = gross_win / gross_loss if gross_loss > 0 else \
        (99.0 if gross_win > 0 else 0.0)
    return {"trades": len(closed), "wins": len(wins),
            "winrate": len(wins) / len(closed) if closed else 0.0,
            "pf": pf, "consecutive_losses": consec,
            "pnl": sum(float(t.get("realized_pnl") or 0) for t in closed)}


def evaluate_population(journal: Journal, notifier=None) -> list[dict]:
    """Run deterministic lifecycle rules; persist state changes + events."""
    actions = []
    for row in journal.list_strategies():
        sid, state = row["id"], row["state"]
        if state == "retired":
            continue
        st = _stats_for(journal, sid)

        def transition(new_state: str, reason: str):
            if new_state == state:
                return          # no-op: don't spam events/alerts each cycle
            journal.query("UPDATE strategies SET state=?, state_changed_at=? "
                          "WHERE id=?",
                          (new_state, datetime.now(timezone.utc).isoformat(),
                           sid))
            journal.log_brain_event(
                "statistical_transition", sid,
                {"from": state, "to": new_state, "reason": reason,
                 "trades": st["trades"], "pf": round(st["pf"], 2),
                 "winrate": round(st["winrate"], 3)})
            log.warning(f"STRATEGY {row['name']}: {state} → {new_state} ({reason})")
            actions.append({"strategy": row["name"], "id": sid,
                            "from": state, "to": new_state, "reason": reason})

        if state == "paper":
            if st["trades"] >= PROMOTE_MIN_TRADES and \
                    st["winrate"] >= PROMOTE_MIN_WINRATE and \
                    st["pf"] >= PROMOTE_MIN_PF:
                transition("active", f"probation passed: WR {st['winrate']:.0%}, "
                                     f"PF {st['pf']:.2f}")
            # demotion must be reachable at ANY trade count — previously this
            # was nested under `trades >= PROMOTE_MIN_TRADES`, so a paper
            # strategy bleeding 14 straight losses could never be retired.
            elif st["consecutive_losses"] >= DEMOTE_CONSEC_LOSSES or (
                    st["trades"] >= DEMOTE_MIN_TRADES and st["pf"] < DEMOTE_MAX_PF):
                transition("retired", f"failed probation: PF {st['pf']:.2f}, "
                                      f"WR {st['winrate']:.0%}, "
                                      f"{st['trades']} trades")
        elif state in ("active", "demoted"):
            if st["consecutive_losses"] >= DEMOTE_CONSEC_LOSSES:
                transition("demoted", f"{st['consecutive_losses']} consecutive losses")
            elif st["trades"] >= DEMOTE_MIN_TRADES and st["pf"] < DEMOTE_MAX_PF \
                    and state != "demoted":
                transition("demoted", f"PF decayed to {st['pf']:.2f} "
                                      f"over {st['trades']} trades")
            elif state == "demoted":
                # measure from WHEN IT WAS DEMOTED, not from birth — using
                # created_at made a strategy demoted late in its life
                # instantly retire-eligible.
                since = row.get("state_changed_at") or row["created_at"]
                demoted_at = datetime.fromisoformat(since)
                if demoted_at.tzinfo is None:
                    demoted_at = demoted_at.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - demoted_at).days
                if age_days > RETIRE_DEMOTED_DAYS and st["pf"] < PROMOTE_MIN_PF:
                    transition("retired", f"no recovery in {age_days}d "
                                          f"since demotion")
        # refresh persisted stats blob either way
        journal.query("UPDATE strategies SET stats_json=? WHERE id=?",
                      (json.dumps({"trades": st["trades"], "wins": st["wins"],
                                   "pnl_usdt": round(st["pnl"], 4),
                                   "pf": round(min(st["pf"], 99), 3)}), sid))

    if actions and notifier:
        msg = "\n".join(f"{a['strategy']}: {a['from']}→{a['to']} — {a['reason']}"
                        for a in actions)
        notifier.send(f"🧬 Strategy lifecycle:\n{msg}")
    return actions


def population_snapshot(journal: Journal) -> list[dict]:
    out = []
    for row in journal.list_strategies():
        st = _stats_for(journal, row["id"])
        out.append({"id": row["id"], "name": row["name"], "kind": row["kind"],
                    "state": row["state"], **st})
    return out
