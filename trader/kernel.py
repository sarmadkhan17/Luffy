"""Luffy kernel — full Phase 1 trading loop.

cycle():
  universe → snapshots (+BTC context) → analyst votes → strategy signals
  → orchestrate → journal EVERYTHING → risk-gate → execute w/ native stops
  → detect exchange-side exits (SL/TP fills) → resolve outcomes

Controls honored every cycle: ACTIVE/FROZEN/HALTED, panic flag, proving sizing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import signal
import sys
import threading
import time

from .agents.flow import FlowAnalyst
from .agents.momentum import MomentumAnalyst, RotationAnalyst, ValueAnalyst
from .agents.structure import StructureAnalyst
from .core.config import ROOT, load_config
from .core.journal import Journal
from .core.types import (Action, ControlState, MarketType, Snapshot,
                         norm_symbol)
from .data.feed import DataFeed, Universe, make_exchange
from .engine.executor import Executor
from .engine.outcomes import resolve_pending
from .engine.reconcile import flatten_all, reconcile_futures
from .engine.risk import RiskManager
from .engine.state import ControlStateMachine
from .engine.watchdog import Heartbeat, start_stall_monitor
from .notify.telegram import Telegram
from .strategy.genome import Genome

log = logging.getLogger("luffy")

AGENTS = {"structure": StructureAnalyst, "flow": FlowAnalyst,
          "momentum": MomentumAnalyst, "value": ValueAnalyst,
          "rotation": RotationAnalyst}


def setup_logging(cfg: dict) -> None:
    from logging.handlers import RotatingFileHandler
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=cfg["logging"].get("level", "INFO"), format=fmt,
        handlers=[RotatingFileHandler(
            logs / "luffy.log",
            maxBytes=int(cfg["logging"].get("max_bytes", 10 << 20)),
            backupCount=int(cfg["logging"].get("backup_count", 5))),
        logging.StreamHandler(sys.stdout)])


class Kernel:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.journal = Journal(str(ROOT / "data" / "luffy.db"))
        self.state_machine = ControlStateMachine(self.journal)
        self.risk = RiskManager(cfg, self.journal)
        self.heartbeat = Heartbeat()
        self.notifier = Telegram()
        self.market_type = MarketType(
            self.journal.kv_get("market_type", cfg["mode"]["market"]))
        self.exchange = make_exchange(self.market_type.value)
        self.feed = DataFeed(self.exchange)
        self.universe = Universe(cfg, self.exchange)
        self.flow_agent = AGENTS["flow"](self.exchange)
        self.analysts = [AGENTS[k]() if k != "flow" else self.flow_agent
                         for k in ("structure", "flow", "momentum",
                                   "value", "rotation")]
        from .engine.orchestrator import Orchestrator
        self.orchestrator = Orchestrator(self.analysts, self.journal)
        self.executor = Executor(self.exchange, self.journal, cfg,
                                 self.market_type)
        self.population = self._load_population()
        self._stop = False
        self._book_cache: dict[str, tuple[float, dict]] = {}
        self._funding_cache: dict[str, float] | None = None

    # ── boot ─────────────────────────────────────────────────────────────
    def _load_population(self) -> list[tuple]:
        pop = []
        for row in self.journal.list_strategies(["paper", "active", "demoted"]):
            st = type("S", (), {})()          # light strategy shim over row
            st.id, st.name, st.state = row["id"], row["name"], row["state"]
            st.params = json.loads(row["params"])
            g = Genome(strategy_id=row["id"], family=row["kind"],
                       hypothesis=row["hypothesis"] or "seed",
                       invalidation=row["invalidation"] or "",
                       regime_filter=frozenset(json.loads(row["regime_filter"] or "[]")),
                       markets=frozenset(json.loads(row["markets"] or '["futures"]')),
                       params=json.loads(row["params"]),
                       generation=row["generation"] or 0)
            st.is_trade_eligible = row["state"] in ("paper", "active")
            pop.append((st, g))
        return pop

    def boot(self) -> None:
        log.info(f"LUFFY BOOT | market={self.market_type.value} "
                 f"state={self.state_machine.state.value} "
                 f"population={len(self.population)}")
        self._filter_universe_to_venue()
        report = reconcile_futures(self.exchange, self.journal)
        if any(report.get(k) for k in ("adopted", "ghosts")):
            self.notifier.send(f"🔧 boot reconciliation: {report}")
        start_stall_monitor(
            self.heartbeat,
            stale_after=float(self.cfg["timeframes"]["scan_interval_seconds"]) * 4)
        signal.signal(signal.SIGTERM, self._graceful)
        signal.signal(signal.SIGINT, self._graceful)
        threading.Thread(target=self._telegram_listener, daemon=True,
                         name="tg-listener").start()

    def _filter_universe_to_venue(self) -> None:
        """Universe comes from production data; drop symbols the trading
        venue (demo) cannot actually trade."""
        try:
            self.exchange.load_markets()
            tradable = set()
            for m in self.exchange.markets.values():
                if not m.get("active", True) or m.get("spot"):
                    continue
                base = (m.get("base") or "")
                quote = (m.get("quote") or "")
                if quote == "USDT":
                    tradable.add(f"{base}/USDT")
            before = self.universe.symbols()
            kept = [s for s in before if s in tradable]
            dropped = set(before) - set(kept)
            self.universe._alts = [s for s in self.universe._alts
                                   if s in kept]
            self.universe.majors = [s for s in self.universe.majors
                                    if s in kept]
            if dropped:
                log.warning(f"universe trimmed to venue: dropped {sorted(dropped)}")
        except Exception as e:
            log.warning(f"universe venue-filter failed: {e}")

    def _graceful(self, signum, _frame) -> None:
        log.warning(f"signal {signum} — shutting down")
        self._stop = True

    # ── per-symbol pipeline ───────────────────────────────────────────────
    def _snapshot_for(self, symbol: str) -> Snapshot | None:
        dfs = self.feed.fetch_multi(
            symbol, self.cfg["timeframes"]["context"] + [self.cfg["timeframes"]["execution"]])
        exec_tf = self.cfg["timeframes"]["execution"]
        if exec_tf not in dfs:
            return None
        btc = self.feed.fetch_ohlcv("BTC/USDT", "1h")
        if btc is not None:
            dfs["BTC_1h"] = btc
        price = float(dfs[exec_tf]["close"].iloc[-1])
        return Snapshot(symbol=symbol,
                        ts=dt.datetime.now(dt.timezone.utc).isoformat(),
                        price=price, dfs=dfs,
                        market_type=self.market_type.value)

    def _order_book(self, symbol: str) -> dict | None:
        hit = self._book_cache.get(symbol)
        now = time.time()
        if hit and now - hit[0] < 45:
            return hit[1]
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=25)
            self._book_cache[symbol] = (now, ob)
            return ob
        except Exception:
            return hit[1] if hit else None

    def _funding_map(self) -> dict[str, float]:
        if self._funding_cache is not None:
            return self._funding_cache
        out: dict[str, float] = {}
        if self.market_type == MarketType.FUTURES:
            for sym in self.universe.symbols():
                try:
                    fr = self.exchange.fetch_funding_rate(sym)
                    out[sym] = float(fr.get("fundingRate") or 0)
                except Exception:
                    continue
        self._funding_cache = out
        return out

    # ── main loop ─────────────────────────────────────────────────────────
    def cycle(self) -> dict:
        stats = {"scanned": 0, "decisions": 0, "entries": 0,
                 "skips": 0, "exits_detected": 0}
        balance = self._fetch_balance()
        status = self.risk.update_equity(balance)

        panic_requested = self.journal.kv_get("panic_requested") == "1"
        if panic_requested:
            n = flatten_all(self.exchange, self.journal, self.notifier)
            self.journal.kv_set("panic_requested", "0")
            self.state_machine.set(ControlState.FROZEN, "operator",
                                   f"panic flattened {n}")
            stats["panic_closed"] = n

        state = self.state_machine.state
        entry_allowed = state == ControlState.ACTIVE
        blocked = "" if entry_allowed else f"state={state.value}"
        if entry_allowed and status.get("daily_pnl_pct", 0) <= \
                -self.risk.daily_loss_block * 100:
            entry_allowed = False
            blocked = f"daily breaker {status['daily_pnl_pct']:.1f}%"

        funding = self._funding_map() if self.market_type == MarketType.FUTURES else {}
        closed_count = int(self.journal.query(
            "SELECT COUNT(*) AS n FROM trades WHERE status='closed'")[0]["n"])

        for symbol in self.universe.symbols():
            snap = self._snapshot_for(symbol)
            if snap is None:
                continue
            stats["scanned"] += 1
            self.flow_agent.set_context(symbol, self._order_book(symbol),
                                        funding.get(symbol))
            d = self.orchestrator.decide(snap, self.population,
                                         entry_allowed=entry_allowed,
                                         blocked_reason=blocked)
            self.orchestrator.journalize(snap, d, self.market_type.value,
                                         mode="live")
            stats["decisions"] += 1

            if d.action != Action.HOLD:
                if d.skip_reason:
                    stats["skips"] += 1
                    log.info(f"SKIP {symbol} {d.action} score={d.score:+.3f} "
                             f"| {d.skip_reason}")
                elif entry_allowed:
                    ok = self._try_enter(d, snap, balance, closed_count)
                    self.journal.update_decision_outcome(
                        d.id, d.executed, d.size_usdt, d.skip_reason)
                    if ok:
                        stats["entries"] += 1
                        self.notifier.send(
                            f"🎯 <b>{d.action}</b> {symbol} @ {snap.price:.4g} "
                            f"score {d.score:+.2f} conf {d.confidence:.0%}")

            stats["exits_detected"] += self._detect_exchange_exits(symbol)

        self._maybe_resolve_outcomes()
        self.heartbeat.beat({"equity": round(balance, 2),
                             "state": state.value, **stats})
        self.journal.log_equity(status["equity"], balance,
                                len(self.journal.open_trades()))
        return {**stats, "equity": status["equity"],
                "dd_pct": status["drawdown_pct"]}

    def _try_enter(self, d, snap, equity, closed_count) -> bool:
        df = snap.df(self.cfg["timeframes"]["execution"])
        from .agents.indicators import atr as _atr
        a = _atr(df)
        side = "long" if d.action == Action.BUY else "short"
        sl, tp = self.risk.protection_levels(snap.price, a, side,
                                             self.executor.tp_atr_mult)
        stop_frac = abs(snap.price - sl) / snap.price
        sizing = self.risk.check_entry(
            self.state_machine.state, d.symbol, snap.price, a, stop_frac,
            open_positions=[self._as_position(t) for t in
                            self.journal.open_trades()],
            equity=equity, closed_trades_count=closed_count,
            market_type=self.market_type.value)
        if not sizing.ok:
            d.skip_reason = f"risk: {sizing.reason}"
            log.info(f"RISK DENY {d.symbol}: {sizing.reason}")
            return False
        top_strategy = ""
        if d.strategy_signals:
            best = max(d.strategy_signals, key=lambda s: s.get("confidence", 0))
            top_strategy = best.get("strategy_id", "")
        pos = self.executor.open(
            d, sizing.amount, a, sl, tp,
            strategy_id=top_strategy or "orchestrator",
            strategy_name=top_strategy and next(
                (s.get("strategy_name", "") for s in d.strategy_signals
                 if s.get("strategy_id") == top_strategy), "consensus"),
            exec_mode="live")
        return pos is not None

    @staticmethod
    def _as_position(t: dict):
        from .core.types import Position, Side
        return Position(id=t["id"], symbol=t["symbol"],
                        side=Side(t["side"]), amount=float(t["amount"]),
                        entry_price=float(t["entry_price"]),
                        notional_usdt=float(t["notional_usdt"] or 0),
                        leverage=int(t.get("leverage") or 1),
                        stop_loss=float(t.get("stop_loss") or 0))

    def _detect_exchange_exits(self, symbol: str) -> int:
        """Position vanished on exchange while journal says open → SL/TP fired."""
        try:
            ex_syms = {norm_symbol(p["symbol"])
                       for p in self.exchange.fetch_positions()
                       if float(p.get("contracts") or 0) > 0}
        except Exception:
            return 0
        n = 0
        for t in self.journal.open_trades():
            if t["symbol"] != symbol or t["market_type"] != "futures":
                continue
            if t["symbol"] not in ex_syms:
                px = self.feed.price(symbol) or float(t["entry_price"])
                sl, tp = float(t.get("stop_loss") or 0), float(t.get("take_profit") or 0)
                direction = 1.0 if t["side"] == "long" else -1.0
                gross = ((px - float(t["entry_price"])) * direction
                         * float(t["amount"]) * int(t.get("leverage") or 1))
                fees = self.executor.taker_fee * (
                    float(t["notional_usdt"]) + float(t["amount"]) * px)
                reason = "tp_fill" if tp and abs(px - tp) <= abs(px - sl) else "sl_fill"
                self.journal.close_trade(t["id"], px,
                                         round(gross - fees, 8), reason)
                self.notifier.send(
                    f"{'✅' if gross > 0 else '🛑'} {symbol} closed "
                    f"({reason}) pnl {(gross - fees):+.2f} USDT")
                log.info(f"EXCHANGE EXIT {symbol}: {reason} @{px} pnl={gross-fees:+.2f}")
                n += 1
        return n

    _outcome_tick = 0

    def _maybe_resolve_outcomes(self):
        Kernel._outcome_tick += 1
        if Kernel._outcome_tick % 12 == 1:      # ~every 12 cycles
            try:
                resolve_pending(self.journal, self.feed)
            except Exception as e:
                log.warning(f"outcome resolution failed: {e}")
        if Kernel._outcome_tick % 60 == 1:      # ~hourly vault refresh
            try:
                from .knowledge.vault import Vault
                v = Vault(self.journal)
                v.refresh_strategy_notes()
                v.daily_review()
                v.agent_ledger()
                v.write_moc()
            except Exception as e:
                log.warning(f"vault refresh failed: {e}")

    # ── infra ────────────────────────────────────────────────────────────
    def account_snapshot(self) -> dict:
        """{margin_equity, assets_total, assets:{}} — display truth vs risk truth."""
        try:
            import hashlib, hmac as _hmac
            import requests
            from .core.config import Env
            key, secret = Env.binance_keys()
            q = f"timestamp={int(time.time()*1000)}&recvWindow=10000"
            sig = _hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            base = self.exchange.urls.get("api", {}).get(
                "fapiPrivate", "").rsplit("/", 1)[0]
            r = requests.get(f"{base}/v3/account",
                             params=q + f"&signature={sig}",
                             headers={"X-MBX-APIKEY": key}, timeout=10)
            a = r.json()
            assets = {x["asset"]: float(x["walletBalance"])
                      for x in a.get("assets", [])
                      if abs(float(x.get("walletBalance") or 0)) > 1e-9}
            px_btc = self.feed.price("BTC/USDT") or 0
            usd = {k: (v if k in ("USDT", "USDC", "BUSD") else
                       v * px_btc if k == "BTC" else None) for k, v in assets.items()}
            return {"margin_equity": float(a.get("totalMarginBalance") or 0),
                    "assets_total": round(sum(v for v in usd.values() if v), 2),
                    "assets": {k: round(v, 4) if v else f"{assets[k]:.6f}"
                               for k, v in usd.items()}}
        except Exception as e:
            log.debug(f"account snapshot failed: {e}")
            return {}

    def _fetch_balance(self) -> float:
        """Collateral margin equity — the number risk sizing is allowed to use."""
        try:
            import hashlib, hmac as _hmac
            import requests
            from .core.config import Env
            key, secret = Env.binance_keys()
            q = f"timestamp={int(time.time()*1000)}&recvWindow=10000"
            sig = _hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            base = self.exchange.urls.get("api", {}).get("fapiPrivate",
                   "https://demo-fapi.binance.com/fapi/v1").rsplit("/", 1)[0]
            r = requests.get(
                f"{base}/fapi/v3/account" if "/v1" in base
                else f"{base}/v3/account",
                params=q + f"&signature={sig}",
                headers={"X-MBX-APIKEY": key}, timeout=10)
            tmb = float(r.json().get("totalMarginBalance") or 0)
            if tmb > 0:
                return tmb
        except Exception as e:
            log.debug(f"v3 account fetch failed ({e}); falling back")
        try:
            total = float(self.exchange.fetch_balance().get("USDT", {})
                          .get("total") or 0)
            return total if total > 0 else self._last_equity_fallback()
        except Exception as e:
            log.warning(f"balance fetch failed: {e}")
            return self._last_equity_fallback()

    def _last_equity_fallback(self) -> float:
        rows = self.journal.query(
            "SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
        return float(rows[0]["equity"]) if rows else 0.0

    # ── telegram minimal control (/panic, /status, /freeze, /resume) ─────
    def _telegram_listener(self) -> None:
        if not self.notifier.configured:
            log.info("telegram unconfigured — listener off")
            return
        offset = 0
        base = f"https://api.telegram.org/bot{self.notifier.token}"
        while not self._stop:
            time.sleep(4)
            try:
                r = __import__("requests").get(
                    f"{base}/getUpdates", params={"timeout": 3, "offset": offset},
                    timeout=8)
                for u in (r.json().get("result") or []):
                    offset = u["update_id"] + 1
                    msg = (u.get("message") or {}).get("text", "").strip().lower()
                    chat = (u.get("message") or {}).get("chat", {}).get("id")
                    if chat != int(self.notifier.chat_id):
                        continue
                    self._handle_tg_command(msg, base)
            except Exception as e:
                log.debug(f"tg poll error: {e}")

    def _handle_tg_command(self, msg: str, base: str) -> None:
        def reply(text):
            try:
                __import__("requests").post(
                    f"{base}/sendMessage",
                    json={"chat_id": int(self.notifier.chat_id), "text": text},
                    timeout=8)
            except Exception:
                pass
        if msg.startswith("/panic"):
            self.journal.kv_set("panic_requested", "1")
            reply("🚨 PANIC queued — flattening on next cycle.")
        elif msg.startswith("/freeze"):
            self.state_machine.set(ControlState.FROZEN, "operator", "telegram")
            reply("🥶 FROZEN — no new entries; managing existing to close.")
        elif msg.startswith("/resume"):
            self.state_machine.set(ControlState.ACTIVE, "operator", "telegram")
            reply("🙂 ACTIVE — full autonomy restored.")
        elif msg.startswith("/halt"):
            self.state_machine.set(ControlState.HALTED, "operator", "telegram")
            reply("😴 HALTED.")
        elif msg.startswith("/status"):
            hb_age = self.heartbeat.age_seconds()
            reply(f"state={self.state_machine.state.value} "
                  f"open={len(self.journal.open_trades())} "
                  f"heartbeat={hb_age and f'{hb_age:.0f}s'}")

    # ── run ──────────────────────────────────────────────────────────────
    def run(self) -> None:
        self.boot()
        interval = float(self.cfg["timeframes"]["scan_interval_seconds"])
        n = 0
        while not self._stop:
            n += 1
            t0 = time.time()
            try:
                info = self.cycle()
                dur = time.time() - t0
                bits = [f"cycle #{n}",
                        f"{info['scanned']} symbols",
                        f"{info['decisions']} decisions"]
                if info.get("entries"):
                    bits.append(f"{info['entries']} ENTRIES")
                if info.get("exits_detected"):
                    bits.append(f"{info['exits_detected']} exits")
                if info.get("panic_closed"):
                    bits.append(f"panic x{info['panic_closed']}")
                bits.append(f"eq ${info['equity']:,.0f}")
                if info.get("dd_pct"):
                    bits.append(f"dd {info['dd_pct']}%")
                bits.append(f"{dur:.1f}s")
                log.info(" · ".join(bits))
            except Exception as e:
                log.exception(f"cycle #{n} failed: {e}")
            self._funding_cache = None                 # refresh funding hourly-ish
            time.sleep(max(1.0, interval - (time.time() - t0)))
        log.info("kernel stopped cleanly")


def main() -> None:
    ap = argparse.ArgumentParser(prog="luffy")
    ap.add_argument("--config", default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--panic", action="store_true")
    args = ap.parse_args()
    cfg = load_config(args.config)
    setup_logging(cfg)
    k = Kernel(cfg)
    if args.status:
        print(json.dumps({
            "control_state": k.state_machine.state.value,
            "market": k.market_type.value,
            "heartbeat_age_s": k.heartbeat.age_seconds(),
            "open_trades": len(k.journal.open_trades()),
            "strategies": len(k.population),
        }, indent=2))
        return
    if args.panic:
        k.journal.kv_set("panic_requested", "1")
        print("panic requested — running kernel will flatten and FROZEN")
        return
    k.run()


if __name__ == "__main__":
    main()
