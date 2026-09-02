"""One operator view: is the book able to trade, and is it protected?

Answers the four questions that actually matter between trades, in the order
you need them:

  1. PROTECTION — does every open position have exactly one live stop on the
     venue, sized to the position? Binance books stops as ALGO orders that
     fetch_open_orders() cannot see, so "the dashboard shows no orders" is
     not evidence either way. This asks the endpoint that knows.
  2. REACH — is each live strategy actually evaluated, and how far is each of
     its symbols from firing? "No trades" reads identically whether the setup
     has not appeared or the strategy was never reached. An empty regime
     filter once meant the latter for weeks.
  3. HEALTH — is the live record still inside the envelope the strategy was
     admitted on? A 38.85% win rate throws three losses in a row a quarter of
     the time; that is not breakage and must not read as breakage.
  4. GATE — what did the strategy-signal gate refuse, and did those refusals
     cost anything? Reopening the analyst blend must be an evidence decision.

    ./venv/bin/python -m scripts.monitor
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config          # noqa: E402
from trader.core.journal import Journal             # noqa: E402
from trader.data.feed import DataFeed, make_exchange  # noqa: E402
from trader.engine import protective as P           # noqa: E402
from trader.strategy.compile import compile_spec    # noqa: E402
from trader.strategy.health import assess_health    # noqa: E402

DB = "data/luffy.db"


def _rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 72)


# ── 1. protection ────────────────────────────────────────────────────────
def protection(j: Journal, ex) -> list[str]:
    _rule("1. PROTECTION — every position, one correctly sized stop")
    alarms: list[str] = []
    try:
        positions = {P.venue_key(p["symbol"]): p for p in ex.fetch_positions()
                     if float(p.get("contracts") or 0)}
    except Exception as e:
        print(f"  cannot read positions: {e}")
        return [f"positions unreadable: {e}"]
    stops = P.open_stops(ex)
    by_sym: dict[str, list] = {}
    for s in stops:
        by_sym.setdefault(P.venue_key(s["symbol"]), []).append(s)

    if not positions:
        print("  no open positions")
    for key, pos in positions.items():
        held = float(pos["contracts"])
        mine = by_sym.get(key, [])
        if not mine:
            alarms.append(f"{key} is NAKED — {held} held, no stop")
            print(f"  \033[31m✗ {key:10} {held:>12} held — NO STOP\033[0m")
            continue
        for s in mine:
            short = abs(s["amount"] - held) > held * 0.01
            flag = "\033[33m(size mismatch)\033[0m" if short else ""
            print(f"  {'✓' if len(mine) == 1 and not short else '!'} "
                  f"{key:10} {held:>12} held · stop {s['amount']:>12} "
                  f"@ {s['stop_price']:<12} {flag}")
            if short:
                alarms.append(f"{key} stop covers {s['amount']} of {held}")
        if len(mine) > 1:
            alarms.append(f"{key} carries {len(mine)} stops — ratchet residue")

    orphans = [s for k, v in by_sym.items() if k not in positions for s in v]
    for s in orphans:
        alarms.append(f"orphan stop {s['id']} on {s['symbol']}")
        print(f"  \033[31m✗ ORPHAN {s['symbol']:10} {s['side']} "
              f"{s['amount']} @ {s['stop_price']}\033[0m")
    return alarms


# ── 2. reach ─────────────────────────────────────────────────────────────
def reach(j: Journal, feed: DataFeed) -> list[str]:
    _rule("2. REACH — is the book evaluated, and how close is it to firing?")
    alarms: list[str] = []
    specs = [sp for _r, sp in j.list_specs(["paper", "active"])]
    if not specs:
        print("  \033[31mno tradeable strategy in the book\033[0m")
        return ["book is empty — nothing can trade"]
    for spec in specs:
        syms = list((spec.universe or {}).get("include") or [])
        print(f"  {spec.name}  [{spec.id}]  {spec.timeframe}  "
              f"direction={spec.direction}  {len(syms)} symbols")
        ev = compile_spec(spec).to_evaluator()
        rows = []
        for sym in syms:
            try:
                df = feed.cached_ohlcv(sym, spec.timeframe, limit=400)
                if df is None or len(df) < 120:
                    rows.append((sym, None, None, "thin history"))
                    continue
                px = float(df["close"].iloc[-1])
                hi = float(df["high"].rolling(100).max().shift(1).iloc[-1])
                lo = float(df["low"].rolling(100).min().shift(1).iloc[-1])
                up = (hi - px) / px * 100      # % to the upside break
                dn = (px - lo) / px * 100      # % to the downside break
                rows.append((sym, up, dn, ""))
            except Exception as e:
                rows.append((sym, None, None, str(e)[:40]))
        rows.sort(key=lambda r: min([x for x in (r[1], r[2])
                                     if x is not None] or [9e9]))
        for sym, up, dn, note in rows:
            if up is None:
                print(f"     {sym:12} {note}")
                continue
            near = min(up, dn)
            side = "LONG" if up <= dn else "SHORT"
            colour = "\033[32m" if near < 1.0 else ""
            end = "\033[0m" if colour else ""
            print(f"     {colour}{sym:12} {near:6.2f}% from a {side:5} break"
                  f"   (up {up:5.2f}% / down {dn:5.2f}%){end}")
        if ev is None:
            alarms.append(f"{spec.id} does not compile to an evaluator")
    return alarms


# ── 3. health ────────────────────────────────────────────────────────────
def health(j: Journal) -> list[str]:
    _rule("3. HEALTH — is the live record inside the validated envelope?")
    alarms: list[str] = []
    for row, spec in j.list_specs(["paper", "active"]):
        exp = float((spec.provenance or {}).get("expected_winrate") or 0)
        trades = j.query(
            "SELECT realized_pnl FROM trades WHERE strategy_id=? "
            "AND status!='open'", (spec.id,))
        wins = sum(1 for t in trades if float(t["realized_pnl"] or 0) > 0)
        losses = len(trades) - wins
        if not exp:
            print(f"  {spec.name}: no validated win rate recorded")
            continue
        if not trades:
            print(f"  {spec.name}: \033[33mNO LIVE TRADES YET\033[0m — "
                  f"validated at {exp:.2%}; everything known is backtest")
            continue
        h = assess_health(exp, wins, losses)
        colour = {"DIVERGED": "\033[31m", "CONSISTENT": "\033[32m"}.get(
            h.verdict, "\033[33m")
        print(f"  {spec.name}: {colour}{h.summary}\033[0m")
        if h.verdict == "DIVERGED":
            alarms.append(f"{spec.id} has DIVERGED from its envelope")
    return alarms


# ── 4. gate ──────────────────────────────────────────────────────────────
def gate(j: Journal) -> list[str]:
    _rule("4. GATE — what the strategy-signal gate refused, and its cost")
    vetoed = j.query(
        "SELECT COUNT(*) n FROM decisions WHERE skip_reason LIKE '%strategy%'")
    n = int(vetoed[0]["n"]) if vetoed else 0
    print(f"  directional calls refused for want of a strategy: {n}")
    graded = j.query(
        "SELECT o.fwd_ret_4h r FROM outcomes o JOIN decisions d "
        "ON d.id=o.decision_id WHERE d.skip_reason LIKE '%strategy%' "
        "AND o.fwd_ret_4h IS NOT NULL")
    if not graded:
        print("  none graded yet — the 4h horizon has not elapsed")
        return []
    rets = [float(g["r"]) for g in graded]
    mean = sum(rets) / len(rets)
    wins = sum(1 for x in rets if x > 0)
    print(f"  graded at 4h: n={len(rets)}  mean={mean * 100:+.3f}%  "
          f"win={wins / len(rets):.1%}")
    if mean > 0.002 and len(rets) >= 60:
        print("  \033[33mthe gate may be refusing calls that pay — "
              "worth re-measuring\033[0m")
        return ["gate is refusing profitable calls; re-measure"]
    print("  the refusals are not costing anything measurable")
    return []


def main() -> int:
    cfg = load_config()
    j = Journal(DB)
    ex = make_exchange("futures",
                       demo=bool(cfg.get("exchange", {}).get("demo", True)),
                       with_keys=True)
    feed = DataFeed()
    alarms: list[str] = []
    alarms += protection(j, ex)
    alarms += reach(j, feed)
    alarms += health(j)
    alarms += gate(j)

    _rule("VERDICT")
    if not alarms:
        print("  \033[32mnothing needs attention\033[0m")
        return 0
    for a in alarms:
        print(f"  \033[31m• {a}\033[0m")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
