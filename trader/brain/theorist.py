"""Theorist — Luffy's research arm (Phase 3).

Periodically mines the decision journal into an evidence report, asks
DeepSeek-R1 for an honest autopsy, then:
  1. writes the autopsy as an Obsidian vault note
  2. proposes doctrine updates (versioned, evidence-cited)
  3. logs everything as brain_events

The theorist never touches live risk numbers directly — it evolves beliefs;
the strategist translates beliefs into strategy actions.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.config import ROOT
from ..core.journal import Journal
from ..knowledge.vault import Vault
from .llm import BrainLLM

log = logging.getLogger(__name__)


class Theorist:
    def __init__(self, journal: Journal, cfg: dict):
        self.journal = journal
        self.llm = BrainLLM(cfg)
        self.vault = Vault(journal)

    # ── evidence gathering ──────────────────────────────────────────────
    def _evidence_report(self) -> dict:
        j = self.journal
        pop = []
        for r in j.list_strategies():
            trades = [t for t in j.trades_for_strategy(r["id"])
                      if t["status"] == "closed"]
            pnl = sum(float(t.get("realized_pnl") or 0) for t in trades)
            wins = sum(1 for t in trades if float(t.get("realized_pnl") or 0) > 0)
            pop.append({"name": r["name"], "state": r["state"],
                        "family": r["kind"], "trades": len(trades),
                        "wins": wins, "pnl": round(pnl, 2)})

        agents = j.agent_accuracy()
        regimes = j.query("""
            SELECT c.regime, d.action, COUNT(*) n FROM decisions d
            JOIN cycles c ON c.id=d.cycle_id
            WHERE d.action!='HOLD' GROUP BY c.regime, d.action""")

        outcomes = j.query("""
            SELECT action,
                   SUM(correct_4h=1) right4h, SUM(correct_4h=0) wrong4h
            FROM outcomes WHERE correct_4h IS NOT NULL GROUP BY action""")
        recent_trades = j.query("""
            SELECT symbol, side, strategy_name, realized_pnl, close_reason,
                   opened_at FROM trades WHERE status='closed'
                   ORDER BY closed_at DESC LIMIT 12""")
        skips = j.query("""
            SELECT skip_reason, COUNT(*) n FROM decisions
            WHERE executed=0 AND action!='HOLD' AND skip_reason!=''
            GROUP BY skip_reason ORDER BY n DESC LIMIT 5""")
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "population": pop,
            "agent_accuracy_4h": [
                {"agent": a["agent"], "n": a["n"],
                 "accuracy": round(a["accuracy"] or 0, 3)} for a in agents],
            "signals_by_regime": regimes,
            "outcome_direction_score": outcomes,
            "recent_closed_trades": recent_trades,
            "top_skip_reasons": skips,
        }

    # ── autopsy ─────────────────────────────────────────────────────────
    def run_autopsy(self, deep: bool = True) -> dict:
        evidence = self._evidence_report()
        current_doctrine = self._load_doctrine()

        prompt = (
            "You are the theorist of an autonomous crypto trading system. "
            "Below is EVIDENCE from its decision journal and its current "
            "operating doctrine.\n\n"
            "Produce an honest autopsy:\n"
            "1. findings — what the numbers actually say (cite counts)\n"
            "2. doctrine_updates — at most TWO concrete belief edits, each "
            "with the evidence that justifies it. Only touch existing ids.\n"
            "3. hypotheses_to_test — up to three falsifiable experiments for "
            "the coming week\n\n"
            "Be skeptical: if sample sizes are small, say so. Never invent "
            "numbers.\n\n"
            f'Output JSON: {{"summary":"3 sentences","findings":["..."],'
            '"doctrine_updates":[{"id","belief","evidence"}],'
            '"hypotheses_to_test":["..."]}}\n\n'
            f"EVIDENCE:\n{json.dumps(evidence, indent=1)}\n\n"
            f"CURRENT DOCTRINE:\n{json.dumps(current_doctrine['beliefs'], indent=1)}")

        result = self.llm.chat_json(prompt, deep=True)
        if not result:
            log.warning("autopsy skipped: no LLM/budget")
            return {"ran": False, "reason": "llm unavailable or out of budget"}

        applied = self._apply_doctrine_updates(
            current_doctrine, result.get("doctrine_updates") or [])
        note_path = self._write_autopsy_note(result, evidence)
        self.journal.log_brain_event("autopsy", "theorist", {
            "summary": (result.get("summary") or "")[:400],
            "findings": len(result.get("findings") or []),
            "doctrine_updates_applied": applied,
            "note": str(note_path),
        })
        self.vault.write_moc()
        return {"ran": True, "note": str(note_path),
                "doctrine_version": current_doctrine["version"],
                "applied_updates": applied}

    # ── helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _load_doctrine() -> dict:
        p = ROOT / "data" / "doctrine.json"
        if p.exists():
            return json.loads(p.read_text())
        return {"version": 0, "updated_at": "", "beliefs": []}

    def _apply_doctrine_updates(self, doctrine: dict,
                                updates: list[dict]) -> list[str]:
        """Only existing belief ids may be edited; each edit must cite
        non-empty evidence. Version bumps once per autopsy."""
        applied = []
        by_id = {b["id"]: b for b in doctrine["beliefs"]}
        for u in updates or []:
            bid = (u.get("id") or "").strip()
            belief = (u.get("belief") or "").strip()
            ev = (u.get("evidence") or "").strip()
            if bid not in by_id or len(belief) < 20 or not ev:
                continue
            by_id[bid]["belief"] = belief
            by_id[bid]["evidence"] = ev
            applied.append(bid)
        if applied:
            doctrine["version"] += 1
            doctrine["updated_at"] = datetime.now(timezone.utc).isoformat()
            (ROOT / "data" / "doctrine.json").write_text(
                json.dumps(doctrine, indent=2))
            self.journal.log_brain_event("doctrine_updated", "theorist",
                                         {"version": doctrine["version"],
                                          "ids": applied})
        return applied

    def _write_autopsy_note(self, result: dict, evidence: dict) -> object:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        body = f"""---
type: autopsy
ts: {ts}
---
# Autopsy — {ts}

**Summary.** {result.get('summary', '')}

## Findings
""" + "\n".join(f"- {f}" for f in result.get("findings", [])) + """

## Doctrine updates applied
""" + ("\n".join(f"- `{u.get('id')}` — {u.get('belief')} *(evidence: {u.get('evidence')})*"
                 for u in result.get("doctrine_updates", []))
      or "- none this cycle") + """

## Hypotheses to test next
""" + "\n".join(f"- [ ] {h}" for h in result.get("hypotheses_to_test", [])) + f"""

## Evidence snapshot
```json
{json.dumps(evidence)[:1800]}
```

Related: [[Agent Ledger]], [[Regime Playbook]], [[MOC]]
"""
        safe_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        return self.vault._write(f"30 Postmortems/{safe_ts} Autopsy.md", body)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="theorist")
    ap.add_argument("--now", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    from ..core.config import load_config
    t = Theorist(Journal(str(ROOT / "data" / "luffy.db")), load_config())
    print(json.dumps(t.run_autopsy(), indent=2, default=str))


if __name__ == "__main__":
    main()
