"""Obsidian knowledge vault — Luffy's narrative memory.

~/trader/knowledge/ IS an Obsidian vault (markdown + YAML frontmatter +
wikilinks). Writers here regenerate notes from the journal so the vault
always mirrors reality; the human reads it in Obsidian, the brain distills
it into doctrine.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import ROOT
from ..org import Org

VAULT = ROOT / "knowledge"

#: note type -> vault folder, for building an employee's "Files here" backlinks
_AUTHOR_FOLDERS = {
    "strategy": "20 Strategies",
    "postmortem": "30 Postmortems",
    "daily-review": "50 Daily",
    "theory": "10 Theories",
}

THEORY_NOTES = {
    "Auction Market Theory.md": """---
type: theory
family: structure
status: core-belief
---
# Auction Market Theory / Wyckoff

Price is a continuous two-way auction seeking liquidity.

## Mechanisms we trade
- **Liquidity sweeps** — stops resting beyond obvious swings are harvested
  before real moves ([[Liquidity Sweep Reversal]] trades these).
- **BOS/CHoCH** — break of structure = auction accepting new prices;
  failure to extend = change of character.
- **Effort vs result** — high volume with no progress = absorption;
  position against the exhausted side.

## Evidence in our book
See [[Agent Ledger]] for measured accuracy of the structure analyst.
""",
    "Market Microstructure.md": """---
type: theory
family: flow
status: core-belief
---
# Market Microstructure

Price moves when aggressive orders consume passive liquidity.

## What we measure
- Order-book imbalance within ±2% of mid.
- Taker buy ratio (aggressor share) on 15m klines.
- Funding crowding: ≥0.06%/interval = crowded longs → fade fuel.
- Absorption: heavy volume, zero progress.

## Open questions
- [ ] Does demo-platform order-book depth mirror production? (verify before trusting flow on demo)
""",
    "Behavioral Momentum.md": """---
type: theory
family: momentum
status: core-belief
---
# Behavioral Momentum

Under-reaction then over-reaction (anchoring, herding) makes trends persist
short-term and overshoot long-term.

## Rules derived
- Trade WITH full EMA stacks only when ADX confirms quality.
- RSI extremes *inside* a trend warn of exhaustion — discount continuation.
""",
    "Statistical Mean Reversion.md": """---
type: theory
family: meanrev
status: core-belief
---
# Statistical Mean Reversion

Absent a trend regime, extensions beyond ~2σ from anchored VWAP revert:
liquidity providers earn the panic premium of overreacting traders
(De Bondt–Thaler short-horizon overreaction).

## Guards
- Never fade inside TRENDING regimes — that's paying to catch knives.
- Require z ≥ 2.5 (see [[VWAP Extreme Fade]]).
""",
    "Cross-Asset Rotation.md": """---
type: theory
family: rotation
status: core-belief
---
# Cross-Asset Rotation

Crypto capital cascades BTC → ETH → large caps → small caps. Alts positive
but lagging a fresh BTC impulse get chased by late rotators within hours.

## Measured driver
BTC 1h return ≥ +0.8% is our cascade trigger ([[BTC Rotation Momentum]]).
""",
}

REGIME_PLAYBOOK = """---
type: regime-playbook
updated: {updated}
---
# Regime Playbook

| Regime | Paid mechanisms | Muted mechanisms |
|---|---|---|
| TRENDING_UP/DOWN | momentum .20×1.25, structure BOS | value fades |
| RANGING | vwap_fade, sweep_reversal | momentum pullbacks |
| VOLATILE | sweep_reversal only | breakout entries |

Fit multiplier 0.55 applies when an analyst votes outside its affinity.
"""


class Vault:
    def __init__(self, journal, org: Org | None = None):
        self.journal = journal
        self.org = org or Org.load()
        VAULT.mkdir(exist_ok=True)
        for d in ("00 Company", "10 Theories", "20 Strategies",
                  "30 Postmortems", "40 Regimes", "50 Daily"):
            (VAULT / d).mkdir(exist_ok=True)

    def _write(self, rel: str, text: str) -> Path:
        p = VAULT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    # ── one-time-ish seeds ──────────────────────────────────────────────
    def seed_theories(self) -> int:
        n = 0
        for name, body in THEORY_NOTES.items():
            self._write(f"10 Theories/{name}", body)
            n += 1
        self._write("40 Regimes/Regime Playbook.md",
                    REGIME_PLAYBOOK.format(
                        updated=datetime.now(timezone.utc).date().isoformat()))
        return n

    def seed_doctrine(self) -> dict:
        doc_path = ROOT / "data" / "doctrine.json"
        if doc_path.exists():
            return json.loads(doc_path.read_text())
        doctrine = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "beliefs": [
                {"id": "risk_first",
                 "belief": "Capital survival outranks opportunity; heat ≤15%, "
                           "never average into losers.",
                 "evidence": "spec §10"},
                {"id": "regime_gating",
                 "belief": "Mechanisms are paid conditionally: momentum in "
                           "trends, fades in ranges; fit multiplier 0.55 outside.",
                 "evidence": "regime layer design"},
                {"id": "hypothesis_or_death",
                 "belief": "Every strategy must state its inefficiency and its "
                           "invalidation; vague strategies are rejected at birth.",
                 "evidence": "genome validator"},
                {"id": "no_veto_analysts",
                 "belief": "Analysts measure, they never veto; only risk limits "
                           "are hard gates.",
                 "evidence": "orchestrator design"},
                {"id": "proving_period",
                 "belief": "First 30 live trades run half-size regardless of "
                           "conviction.",
                 "evidence": "spec §2"},
            ],
        }
        doc_path.write_text(json.dumps(doctrine, indent=2))
        self.journal.log_brain_event("doctrine_created", "doctrine",
                                     f"v{doctrine['version']}")
        return doctrine

    # ── company / org chart ─────────────────────────────────────────────
    def seed_company(self, org: Org | None = None) -> int:
        """Write one node per employee + the org chart into `00 Company/`.

        The org chart and the knowledge graph become one browsable structure:
        every node links up to its manager and down to the notes it authors.
        """
        org = org or self.org
        for e in org.all():
            files = []
            for note_type in e.authors:
                folder = _AUTHOR_FOLDERS.get(note_type)
                if folder:
                    for p in sorted((VAULT / folder).glob("*.md")):
                        files.append(f"- [[{p.stem}]]")
                elif note_type == "agent-ledger":
                    files.append("- [[Agent Ledger]]")
            wraps = "\n".join(f"- `{w}`" for w in e.wraps) or "- —"
            body = [f"---\ntype: role\ntitle: {e.title}",
                    f"reports_to: {e.reports_to or '—'}\n---",
                    f"# {e.name}", "", f"> {e.desc}", ""]
            if e.reports_to:
                body.append(f"Reports to [[{e.reports_to}]].\n")
            body += ["## Wraps", wraps, "", "## Files here"]
            body += files or ["- _nothing filed yet_"]
            body.append("\nRelated: [[Company]], [[MOC]]")
            self._write(f"00 Company/{e.name}.md", "\n".join(body) + "\n")

        # the org chart itself
        rows = ["| Employee | Title | Job |", "|---|---|---|"]
        for e in org.employees:
            rows.append(f"| [[{e.name}]] | {e.title} | {e.desc} |")
        chart = [f"---\ntype: org-chart\nupdated: "
                 f"{datetime.now(timezone.utc).date().isoformat()}\n---",
                 "# Company", "",
                 f"**[[{org.manager.name}]]** — {org.manager.desc}", "",
                 "## Team", *rows, "", "Related: [[MOC]]"]
        self._write("00 Company/Company.md", "\n".join(chart) + "\n")
        return len(org.all())

    # ── regenerated from journal ────────────────────────────────────────
    def refresh_strategy_notes(self) -> int:
        n = 0
        for r in self.journal.list_strategies():
            stats = json.loads(r.get("stats_json") or "{}")
            closed = self.journal.trades_for_strategy(r["id"])
            wins = sum(1 for t in closed if float(t.get("realized_pnl") or 0) > 0)
            pnl = sum(float(t.get("realized_pnl") or 0) for t in closed)
            author = ("Researcher"
                      if str(r["origin"] or "").lower().startswith("harvest")
                      else self.org.author_for("strategy") or "Strategist")
            note = f"""---
type: strategy
state: {r['state']}
family: {r['kind']}
origin: {r['origin']}
author: {author}
---
# {r['name']}

> **Hypothesis.** {r['hypothesis'] or '—'}
>
> **Invalidation.** {r['invalidation'] or '—'}

## Genes
```json
{r['params']}
```

## Live record
- closed trades: {len(closed)} · wins: {wins}
- realized P&L: {pnl:+.2f} USDT
- state: **{r['state']}**

Related: [[Regime Playbook]], [[MOC]]

Filed by [[{author}]]
"""
            self._write(f"20 Strategies/{r['name'].replace(' ', '_')}.md", note)
            n += 1
        return n

    def daily_review(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        taken = self.journal.query(
            "SELECT COUNT(*) n FROM decisions WHERE executed=1 AND ts LIKE ?",
            (f"{day}%",))[0]["n"]
        skipped = self.journal.query(
            "SELECT COUNT(*) n FROM decisions WHERE executed=0 AND action!='HOLD' "
            "AND ts LIKE ?", (f"{day}%",))[0]["n"]
        holds = self.journal.query(
            "SELECT COUNT(*) n FROM decisions WHERE action='HOLD' AND ts LIKE ?",
            (f"{day}%",))[0]["n"]
        pnl_rows = self.journal.query(
            "SELECT COALESCE(SUM(realized_pnl),0) s FROM trades WHERE closed_at LIKE ?",
            (f"{day}%",))
        eq = self.journal.query("SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
        author = self.org.author_for("daily-review") or "Manager"
        body = f"""---
type: daily-review
day: {day}
author: {author}
---
# Daily Review {day}

- decisions: {taken} taken · {skipped} skipped · {holds} holds
- realized P&L: {pnl_rows[0]['s'] if pnl_rows else 0:+.2f} USDT
- latest equity: {eq[0]['equity'] if eq else '—'}

## Notes
Auto-generated by theorist-lite. Brain autopsies arrive Phase 2.

Filed by [[{author}]]
"""
        return self._write(f"50 Daily/{day}.md", body)

    def write_moc(self) -> None:
        lines = ["---\ntype: moc\n---", "# Map of Content",
                 "", "[[Company]] · [[Regime Playbook]] · [[Agent Ledger]]", "",
                 "## Company"]
        for e in self.org.all():
            lines.append(f"- [[{e.name}]] — {e.title}")
        lines.append("\n## Theories")
        for name in sorted(THEORY_NOTES):
            stem = name[:-3]
            lines.append(f"- [[{stem}]]")
        lines.append("\n## Strategies")
        for s in sorted((VAULT / "20 Strategies").glob("*.md")):
            lines.append(f"- [[{s.stem}]]")
        lines.append("\n## Daily")
        for d in sorted((VAULT / "50 Daily").glob("*.md"), reverse=True)[:14]:
            lines.append(f"- [[{d.stem}]]")
        self._write("MOC.md", "\n".join(lines) + "\n")

    def agent_ledger(self) -> None:
        rows = self.journal.agent_accuracy()
        body = ["---\ntype: ledger\n---", "# Agent Ledger",
                "", "| Agent | Samples | Accuracy(4h) |", "|---|---|---|"]
        if rows:
            for r in rows:
                acc = (r["accuracy"] or 0) * 100
                node = self.org.analyst_node(r["agent"])
                who = f"[[{node}]]" if node else r["agent"]
                body.append(f"| {who} | {r['n']} | {acc:.1f}% |")
        else:
            body.append("| _no resolved outcomes yet_ | | |")
        self._write("40 Regimes/Agent Ledger.md", "\n".join(body) + "\n")

    def incident_note(self, title: str, body: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        author = self.org.author_for("postmortem") or "Theorist"
        self._write(f"30 Postmortems/{ts} {title}.md",
                    f"---\ntype: postmortem\nauthor: {author}\n---\n"
                    f"# {title}\n\n{body}\n\nFiled by [[{author}]]\n")

    def run_full_refresh(self) -> dict:
        out = {
            "theories": self.seed_theories(),
            "strategies": self.refresh_strategy_notes(),
            "daily": str(self.daily_review().relative_to(VAULT)),
            "company": self.seed_company(),
            "ledger": True,
            "moc": True,
        }
        self.seed_doctrine()
        self.write_moc()
        self.agent_ledger()
        return out
