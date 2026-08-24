"""Luffy kernel — the runnable process (Phase 0 scope).

Boots in order: journal → state machine → exchange → RECONCILE → universe
→ loop { heartbeat, equity tracking, panic processing, sleep }.

Phase 0 does NOT trade: no entries, no exits. It proves the skeleton:
safe boot, honest books, responsive controls. Orchestrator arrives Phase 1.

Usage:
  python -m trader.kernel                 # run monitor loop
  python -m trader.kernel --status        # print control summary, exit
  python -m trader.kernel --panic         # request flatten (processed by running kernel)
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time

from .core.config import load_config
from .core.journal import Journal
from .core.types import MarketType
from .data.feed import DataFeed, Universe, make_exchange
from .engine.reconcile import flatten_all, reconcile_futures
from .engine.risk import RiskManager
from .engine.state import ControlStateMachine
from .engine.watchdog import Heartbeat, start_stall_monitor
from .notify.telegram import Telegram

log = logging.getLogger("luffy")


def setup_logging(cfg: dict) -> None:
    from logging.handlers import RotatingFileHandler
    from .core.config import ROOT
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs / "luffy.log",
                            maxBytes=int(cfg["logging"].get("max_bytes", 10 << 20)),
                            backupCount=int(cfg["logging"].get("backup_count", 5))),
        logging.StreamHandler(sys.stdout)]
    logging.basicConfig(level=cfg["logging"].get("level", "INFO"),
                        format=fmt, handlers=handlers)


class Kernel:
    def __init__(self, cfg: dict):
        from .core.config import ROOT
        self.cfg = cfg
        db_path = cfg.get("_db_path") or str(ROOT / "data" / "luffy.db")
        self.journal = Journal(db_path)
        self.state_machine = ControlStateMachine(self.journal)
        self.risk = RiskManager(cfg, self.journal)
        self.heartbeat = Heartbeat()
        self.notifier = Telegram()
        self.market_type = MarketType(
            self.journal.kv_get("market_type", cfg["mode"]["market"]))
        self.exchange = make_exchange(self.market_type.value)
        self.feed = DataFeed(self.exchange)
        self.universe = Universe(cfg, self.exchange)
        self._stop = False

        self._ensure_seed_population()

    # ── boot ─────────────────────────────────────────────────────────────
    def _ensure_seed_population(self) -> None:
        if not self.journal.list_strategies():
            for st, _g in __import__(
                    "trader.strategy.library", fromlist=["build_seed_population"]
            ).build_seed_population():
                st.hypothesis, st.invalidation = _g.hypothesis, _g.invalidation
                st.regime_filter, st.markets = set(_g.regime_filter), set(_g.markets)
                st.generation = _g.generation
                self.journal.upsert_strategy(st)
            log.info(f"seed population planted "
                     f"({len(self.journal.list_strategies())} strategies)")

    def boot(self) -> None:
        log.info(f"LUFFY BOOT | market={self.market_type.value} "
                 f"state={self.state_machine.state.value}")
        report = reconcile_futures(self.exchange, self.journal)
        if report.get("adopted") or report.get("ghosts"):
            self.notifier.send(
                f"🔧 Luffy boot reconciliation: adopted={report['adopted']} "
                f"ghosts={report['ghosts']} aligned={report['aligned']}")
        start_stall_monitor(self.heartbeat,
                            stale_after=float(self.cfg["timeframes"]["scan_interval_seconds"]) * 4)
        signal.signal(signal.SIGTERM, self._graceful)
        signal.signal(signal.SIGINT, self._graceful)

    def _graceful(self, signum, _frame) -> None:
        log.warning(f"signal {signum} — shutting down")
        self._stop = True

    # ── cycle ────────────────────────────────────────────────────────────
    def cycle(self) -> dict:
        symbols = self.universe.symbols()
        balance = self._fetch_balance()
        status = self.risk.update_equity(balance)

        # panic requests land via kv flag (CLI/telegram/dashboard all write it)
        if self.journal.kv_get("panic_requested") == "1":
            n = flatten_all(self.exchange, self.journal, self.notifier)
            self.journal.kv_set("panic_requested", "0")
            self.state_machine.set(
                __import__("trader.core.types", fromlist=["ControlState"])
                .ControlState.FROZEN, "operator", f"panic flattened {n}")
            status["panic_closed"] = n

        self.heartbeat.beat({"equity": round(balance, 2),
                             "state": self.state_machine.state.value})
        self.journal.log_equity(status["equity"], balance,
                                len(self.journal.open_trades()))
        return {"symbols": len(symbols), **status}

    def run(self) -> None:
        self.boot()
        interval = float(self.cfg["timeframes"]["scan_interval_seconds"])
        while not self._stop:
            t0 = time.time()
            try:
                info = self.cycle()
                log.info(f"cycle ok | {info}")
            except Exception as e:
                log.exception(f"cycle failed: {e}")
            time.sleep(max(1.0, interval - (time.time() - t0)))
        log.info("kernel stopped cleanly")

    # ── helpers ──────────────────────────────────────────────────────────
    def _fetch_balance(self) -> float:
        try:
            code = "USDT"
            bal = self.exchange.fetch_balance().get(code, {})
            total = float(bal.get("total") or 0)
            return total if total > 0 else self._last_equity_fallback()
        except Exception as e:
            log.warning(f"balance fetch failed: {e}")
            return self._last_equity_fallback()

    def _last_equity_fallback(self) -> float:
        rows = self.journal.query(
            "SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
        return float(rows[0]["equity"]) if rows else 0.0


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
        sm = k.state_machine
        print(json.dumps({
            "control_state": sm.state.value,
            "market": k.market_type.value,
            "heartbeat_age_s": k.heartbeat.age_seconds(),
            "open_trades": len(k.journal.open_trades()),
            "strategies": len(k.journal.list_strategies()),
        }, indent=2))
        return

    if args.panic:
        k.journal.kv_set("panic_requested", "1")
        k.journal.log_control_event("panic", "operator", detail="requested via CLI")
        print("panic requested — running kernel will flatten and FROZEN")
        return

    k.run()


if __name__ == "__main__":
    main()
