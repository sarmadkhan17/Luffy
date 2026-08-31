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

from .agents.calibration import maybe_refit as maybe_refit_calibration
from .agents.flow import FlowAnalyst
from .agents.momentum import MomentumAnalyst, RotationAnalyst, ValueAnalyst
from .agents.macro_guard import MacroGuard
from .agents.news_guard import NewsGuard
from .agents.orderbook_depth import DepthScout
from .agents.positioning import PositioningAnalyst
from .agents.regime import btc_context
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
          "rotation": RotationAnalyst, "positioning": PositioningAnalyst,
          "depth": DepthScout}


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
        self.positioning_agent = PositioningAnalyst(self.exchange)
        self.depth_agent = DepthScout()
        order = ("structure", "momentum", "flow", "value", "rotation",
                 "positioning", "depth")
        self.analysts = [self._make_agent(k) for k in order]
        from .engine.orchestrator import Orchestrator
        self.news_guard = NewsGuard(cfg, self.journal)
        self.macro_guard = MacroGuard(cfg, self.journal)
        self.orchestrator = Orchestrator(self.analysts, self.journal,
                                         news_guard=self.news_guard, cfg=cfg)
        self.executor = Executor(self.exchange, self.journal, cfg,
                                 self.market_type)
        from .engine.exits import ExitEngine

        self.population = self._load_population()
        self._pop_sig = [(st.id, st.state, st.params)
                         for st, _g in self.population]
        from .engine.exits import ExitEngine
        self.exits = ExitEngine(self.exchange, self.journal, self.executor,
                                cfg, genomes={st.id: st.params
                                              for st, _g in self.population})
        self._stop = False
        self._book_cache: dict[str, tuple[float, dict]] = {}
        self._funding_cache: dict[str, float] | None = None
        self._oi_cache: tuple[float, dict[str, dict]] = (0.0, {})
        self._btc_ctx: dict = {}

    def _make_agent(self, key: str):
        if key == "positioning":
            return self.positioning_agent
        if key == "depth":
            return self.depth_agent
        return AGENTS[key]()

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
        threading.Thread(target=self._derivatives_recorder, daemon=True,
                         name="derivs-recorder").start()
        threading.Thread(target=self._maybe_validate_agents, daemon=True,
                         name="agent-validator").start()
        if self.cfg.get("harvester", {}).get("enabled", False):
            threading.Thread(target=self._harvest_loop, daemon=True,
                             name="harvester").start()
        if self.cfg.get("crawler", {}).get("enabled", False):
            threading.Thread(target=self._crawl_loop, daemon=True,
                             name="crawler").start()
        if self.cfg.get("brain", {}).get("judge_interval_minutes"):
            threading.Thread(target=self._brain_judge_loop, daemon=True,
                             name="brain-judge").start()

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

    def _derivatives_recorder(self) -> None:
        """Record funding / open interest / taker ratio / long-short every N
        minutes.

        Runs from day one even though nothing reads it yet. The OI, taker and
        long-short endpoints are hard-limited to ~30 days of retention, so
        history not recorded now is permanently unavailable to any future
        backtest. A coarse backfill at start makes those ~30 days usable
        immediately instead of after two months of forward recording.
        """
        import time as _t
        dcfg = self.cfg.get("derivatives", {}) or {}
        if not dcfg.get("enabled", True):
            log.info("derivatives recorder disabled")
            return
        from .data.derivatives import DerivFeed
        feed = DerivFeed()
        symbols = dcfg.get("symbols") or self.cfg["universe"]["majors"]
        interval = float(dcfg.get("record_interval_minutes", 15)) * 60
        delay = float(dcfg.get("request_delay_s", 0.3))
        _t.sleep(30)                        # let boot settle
        if dcfg.get("backfill_on_start", True):
            try:
                counts = feed.backfill(symbols, delay=delay)
                log.info(f"derivatives backfill: {counts}")
            except Exception as e:
                log.warning(f"derivatives backfill failed: {e}")
        while not self._stop:
            try:
                counts = feed.record_all(symbols, delay=delay)
                if counts:
                    log.info(f"derivatives recorded: {counts}")
            except Exception as e:
                log.warning(f"derivatives recorder: {e}")
            # sleep in slices so shutdown is not delayed a full interval
            for _ in range(int(interval)):
                if self._stop:
                    return
                _t.sleep(1)

    def _harvest_loop(self) -> None:
        """Continuous strategy discovery from the internet."""
        import time as _t
        interval = float(self.cfg["harvester"].get("interval_minutes", 240)) * 60
        _t.sleep(180)                       # let boot settle
        while not self._stop:
            try:
                from .brain.harvester import Harvester
                h = Harvester(self.journal, self.cfg, self.feed,
                              self.notifier)
                stats = h.harvest_once()
            except Exception as e:
                log.warning(f"harvest cycle failed: {e}")
            _t.sleep(interval)

    def _crawl_loop(self) -> None:
        """Deep-reading crawler: walks finance knowledge sites, mines
        tradeable rules from full-text material."""
        import time as _t
        c = self.cfg.get("crawler", {})
        interval = float(c.get("interval_minutes", 360)) * 60
        _t.sleep(600)                    # let boot + first harvest settle
        while not self._stop:
            try:
                from .brain.crawler import DeepCrawler
                DeepCrawler(self.journal, self.cfg, self.feed,
                            self.notifier).crawl_once()
            except Exception as e:
                log.warning(f"crawl cycle failed: {e}")
            _t.sleep(interval)

    def _brain_judge_loop(self) -> None:
        """Brain-Judge: LLM reviews the strategy book and decides —
        promote/demote/hold, with journaled reasoning. Weekly deep
        meta-review answers 'are we going in the right direction?'"""
        import time as _t
        b = self.cfg.get("brain", {})
        interval = float(b.get("judge_interval_minutes", 360)) * 60
        meta_every_s = float(b.get("judge_meta_every_hours", 168)) * 3600
        last_meta = 0.0
        _t.sleep(900)                    # let harvest/crawler settle first
        while not self._stop:
            try:
                from .brain.judge import BrainJudge
                do_meta = (time.time() - last_meta) > meta_every_s
                rep = BrainJudge(self.journal, self.cfg,
                                 self.notifier).review(meta_review=do_meta)
                if do_meta:
                    last_meta = time.time()
                if rep.get("reviewed") and rep.get("applied"):
                    log.info(f"brain-judge applied {rep['applied']} changes")
            except Exception as e:
                log.warning(f"brain-judge review failed: {e}")
            _t.sleep(interval)

    def _maybe_validate_agents(self) -> None:
        """Re-run analyst validation weekly (or at boot if stale/missing)."""
        import json as _json
        from .agents.validate import run as validate_run
        from .agents.structure import StructureAnalyst
        from .agents.flow import FlowAnalyst
        from .agents.momentum import (MomentumAnalyst, ValueAnalyst,
                                      RotationAnalyst)
        from .core.config import ROOT
        wpath = ROOT / "data" / "agent_weights.json"
        age_days = 999.0
        if wpath.exists():
            age_days = (time.time() - wpath.stat().st_mtime) / 86400
        if age_days < 7:
            log.info(f"agent weights fresh ({age_days:.1f}d) — skip validation")
            return
        try:
            time.sleep(90)                     # let boot settle
            analysts = {"structure": StructureAnalyst(),
                        "flow": FlowAnalyst(),
                        "momentum": MomentumAnalyst(),
                        "value": ValueAnalyst(),
                        "rotation": RotationAnalyst()}
            rep = validate_run(analysts, self.universe.symbols()[:8],
                               self.feed, days=20)
            self.journal.log_brain_event(
                "agent_validation", "theorist",
                {a: {"acc": i["overall_acc_1h"], "n": i["samples"]}
                 for a, i in rep["agents"].items()})
            try:
                from .knowledge.vault import Vault
                Vault(self.journal).agent_ledger()
            except Exception:
                pass
            log.info(f"agent validation complete: "
                     f"{ {a: round(i['overall_acc_1h'],3) for a,i in rep['agents'].items()} }")
            self.notifier.send("🔬 Analyst validation refreshed — weights "
                               "updated from measured accuracy.")
        except Exception as e:
            log.warning(f"agent validation failed: {e}")

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
                        market_type=self.market_type.value,
                        btc_ctx=self._btc_ctx)

    def _refresh_btc_context(self) -> None:
        """Leader context computed once per cycle, shared by all scouts."""
        b15 = self.feed.fetch_ohlcv("BTC/USDT", "15m")
        b1h = self.feed.fetch_ohlcv("BTC/USDT", "1h")
        self._btc_ctx = btc_context(b15, b1h)

    def _order_book(self, symbol: str) -> dict | None:
        hit = self._book_cache.get(symbol)
        now = time.time()
        if hit and now - hit[0] < 45:
            return hit[1]
        try:
            ob = self.exchange.fetch_order_book(symbol, limit=20)
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

    def _oi_map(self, ttl: float = 900.0) -> dict[str, dict]:
        """Open interest now + 24h change per symbol (TTL-cached).

        OI history lives on production fapiData — the demo venue has no
        route for it, so a public no-key instance serves the reads.
        """
        now = time.time()
        if now - self._oi_cache[0] < ttl:
            return self._oi_cache[1]
        out: dict[str, dict] = {}
        try:
            if getattr(self, "_oi_ex", None) is None:
                self._oi_ex = make_exchange("futures", demo=False,
                                            with_keys=False)
            for sym in self.universe.symbols():
                try:
                    hist = self._oi_ex.fetch_open_interest_history(
                        sym, timeframe="1h", limit=25) or []
                    points = [float(p.get("openInterestAmount") or
                                    p.get("openInterestValue") or 0)
                              for p in hist if isinstance(p, dict)]
                    points = [p for p in points if p > 0]
                    if len(points) >= 6:
                        chg = (points[-1] - points[0]) / points[0]
                        out[sym] = {"now": points[-1], "chg_24h": round(chg, 4)}
                except Exception:
                    continue
        except Exception as e:
            log.warning(f"OI map failed: {e}")
        self._oi_cache = (now, out)
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

        # MacroGuard: hard-freeze during scheduled high-impact US events.
        # Only auto-resumes if MacroGuard owns the current freeze, not operator.
        macro = self.macro_guard.check()
        macro_owns_freeze = self.journal.kv_get("macro_guard_froze", "0") == "1"
        _cur = self.state_machine.state
        if macro.get("active") and _cur == ControlState.ACTIVE:
            self.state_machine.set(ControlState.FROZEN, "macro_guard",
                                   macro.get("event", "macro event"))
            self.journal.kv_set("macro_guard_froze", "1")
            self.notifier.send(
                f"🔒 MacroGuard FREEZE: {macro.get('event', 'macro event')} "
                f"until {macro.get('until', '?')}")
        elif (not macro.get("active") and macro_owns_freeze
              and _cur == ControlState.FROZEN):
            self.state_machine.set(ControlState.ACTIVE, "macro_guard",
                                   "macro event cleared")
            self.journal.kv_set("macro_guard_froze", "0")
            self.notifier.send("✅ MacroGuard: event cleared — resuming ACTIVE")
        self.journal.kv_set("macro_guard_state", json.dumps({
            "active": bool(macro.get("active")),
            "event": macro.get("event", ""),
            "until": macro.get("until"),
            "ts": dt.datetime.now(dt.timezone.utc).isoformat()}))

        state = self.state_machine.state
        entry_allowed = state == ControlState.ACTIVE
        blocked = "" if entry_allowed else f"state={state.value}"
        if entry_allowed and status.get("daily_pnl_pct", 0) <= \
                -self.risk.daily_loss_block * 100:
            entry_allowed = False
            blocked = f"daily breaker {status['daily_pnl_pct']:.1f}%"

        funding = self._funding_map() if self.market_type == MarketType.FUTURES else {}
        oi = self._oi_map() if self.market_type == MarketType.FUTURES else {}
        self._refresh_btc_context()
        news = self.news_guard.check()
        self.journal.kv_set("news_guard_state", json.dumps(
            {"active": bool(news.get("active")), "why": news.get("why", ""),
             "ts": dt.datetime.now(dt.timezone.utc).isoformat()}))
        if news.get("active"):
            log.info(f"news guard: {news['why']} — thresholds raised")
        closed_count = int(self.journal.query(
            "SELECT COUNT(*) AS n FROM trades WHERE status='closed'")[0]["n"])

        for symbol in self.universe.symbols():
            snap = self._snapshot_for(symbol)
            if snap is None:
                continue
            stats["scanned"] += 1
            self.positioning_agent.set_context(symbol, funding.get(symbol),
                                               oi.get(symbol))
            self.depth_agent.set_context(symbol, self._order_book(symbol))
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
            for t in self.journal.open_trades():
                if t["symbol"] != symbol:
                    continue
                from .agents.indicators import atr as _atr
                a = _atr(snap.df(self.cfg["timeframes"]["execution"])) \
                    if snap.df(self.cfg["timeframes"]["execution"]) is not None else 0
                if a <= 0:
                    continue
                score = d.score if d.symbol == symbol else None
                try:
                    reason = self.exits.manage(t, snap.price, a, score)
                    if reason:
                        stats["exit_action"] = reason
                        self.notifier.send(
                            f"↪ {symbol} exit: {reason} @ {snap.price:.4g}")
                except Exception as e:
                    log.warning(f"exit manage {symbol}: {e}")

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
                         * float(t["amount"]))
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
        if Kernel._outcome_tick % 360 == 5:     # ~every 6h: theorist autopsy
            try:
                from .brain.theorist import Theorist
                rep = Theorist(self.journal, self.cfg).run_autopsy()
                if rep.get("ran"):
                    log.info(f"theorist autopsy → {rep['note']} "
                             f"(doctrine v{rep.get('doctrine_version')})")
            except Exception as e:
                log.warning(f"theorist failed: {e}")
        if Kernel._outcome_tick % 60 == 1:      # ~hourly brain/vault tick
            try:
                maybe_refit_calibration(
                    self.journal,
                    min_samples=int((self.cfg.get("scouts", {})
                                     .get("calibration", {})
                                     .get("min_samples", 60))),
                    refit_hours=float((self.cfg.get("scouts", {})
                                       .get("calibration", {})
                                       .get("refit_hours", 6))))
            except Exception as e:
                log.warning(f"calibration tick failed: {e}")
            try:
                from .brain.strategist import Strategist
                from .strategy.promotion import evaluate_population
                evaluate_population(self.journal, self.notifier)
                report = Strategist(self.journal, self.cfg,
                                    self.notifier).review()
                if report.get("reviewed"):
                    log.info(f"strategist review: {report['why']} → "
                             f"{len(report.get('actions', []))} actions "
                             f"(llm={report.get('used_llm')})")
            except Exception as e:
                log.warning(f"brain tick failed: {e}")
            try:
                # hot reload: promote/demote/mutate/harvest writes take
                # effect within one brain tick — no restart required
                fresh = self._load_population()
                sig = [(st.id, st.state, st.params) for st, _g in fresh]
                if sig != self._pop_sig:
                    self.population = fresh
                    self._pop_sig = sig
                    self.exits.genomes = {st.id: st.params
                                          for st, _g in fresh}
                    self.orchestrator.reload_weights()
                    elig = sum(1 for s in sig
                               if s[1] in ("paper", "active"))
                    log.info(f"population hot-reloaded: {len(fresh)} "
                             f"strategies ({elig} trade-eligible)")
            except Exception as e:
                log.warning(f"population hot-reload failed: {e}")
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
            total = 0.0
            # demo-fapi.binance.com intermittently answers 5xx
            # ("upstream request failed") for seconds at a time — retry
            # once before falling back to the last known equity
            for attempt in range(2):
                try:
                    total = float(self.exchange.fetch_balance()
                                  .get("USDT", {}).get("total") or 0)
                    if total > 0:
                        break
                except Exception as inner:
                    if attempt == 1:
                        raise
                    log.debug(f"balance retry after: {inner}")
                    time.sleep(3)
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
        elif msg.startswith("/news"):
            st = self.news_guard.check()
            emoji = "📰🚨" if st.get("active") else "📰✅"
            reply(f"{emoji} news guard: "
                  f"{'ARMED — entering suppressed' if st.get('active') else 'quiet — trading normal'}"
                  f"\n{st.get('why', '')}")
        elif msg.startswith("/scouts"):
            try:
                rows = self.journal.agent_accuracy(since_hours=336)
                acc = {r["agent"]: r["accuracy"] for r in rows}
                from .agents import calibration
                fits = {k for k in calibration.load()
                        if not k.startswith("_")}
                w = self.orchestrator.base_weights
                lines = []
                for a in self.orchestrator.analysts:
                    acc_s = f"{acc[a]:.0%}" if a in acc and \
                        acc[a] is not None else "—"
                    cal = " ·cal" if a in fits else ""
                    lines.append(f"{a:12s} w={w.get(a, 0):.2f} "
                                 f"acc={acc_s}{cal}")
                reply("🔭 scouts:\n" + "\n".join(lines))
            except Exception as e:
                reply(f"/scouts failed: {e}")
        elif msg.startswith("/judge"):
            try:
                r = self.journal.query(
                    "SELECT ts, detail FROM brain_events WHERE "
                    "kind='brain_judgement' ORDER BY ts DESC LIMIT 1")
                if not r:
                    reply("🧠 no brain judgement yet — first review fires "
                          "~15min after boot, then every judge interval")
                else:
                    d = json.loads(r[0]["detail"])
                    applied = d.get("decisions_applied") or []
                    body = "\n".join(
                        f"· {a['id'][:14]} {a['from']}→{a['to']}: "
                        f"{str(a.get('rationale', ''))[:80]}"
                        for a in applied) or "held the book"
                    reply(f"🧠 judgement {r[0]['ts'][:16]}\n{body}\n"
                          f"direction: {d.get('direction', '')[:200]}")
            except Exception as e:
                reply(f"/judge failed: {e}")
        elif msg.startswith("/tv"):
            try:
                from .brain.tv_harness import TVHarness
                h = TVHarness(self.journal, self.cfg)
                st = h.health()
                emoji = {"healthy": "✅", "degraded": "⚠️",
                         "down": "❌"}.get(st["state"], "❔")
                login_hint = "" if (ROOT / "data" / "tv_profile").exists() \
                    else "\n(never logged in — run: ./venv/bin/python -m trader.brain.tv_harness --login)"
                reply(f"{emoji} TV harness: {st['state']} · "
                      f"{st['runs_today']}/{st['budget']} runs today"
                      f"{login_hint}")
            except Exception as e:
                reply(f"/tv failed: {e}")

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
